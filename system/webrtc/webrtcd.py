#!/usr/bin/env python3

import argparse
import asyncio
import json
import time
import uuid
import logging
from dataclasses import dataclass, field
from collections.abc import Callable
from typing import Any, TYPE_CHECKING

# aiortc and its dependencies have lots of internal warnings :(
import warnings
warnings.filterwarnings("ignore", category=DeprecationWarning)
warnings.filterwarnings("ignore", category=RuntimeWarning) # TODO: remove this when google-crc32c publish a python3.12 wheel

import capnp
from aiohttp import web
if TYPE_CHECKING:
  from aiortc.rtcdatachannel import RTCDataChannel

from openpilot.system.webrtc.schema import generate_field
from cereal import messaging, log

from openpilot.common.params import Params

# Must match Connect ``purpose`` in ``/stream`` and Athena ``startPhotoboothStream`` body.
STREAM_PURPOSE_PHOTOBOOTH = "photobooth"
PHOTBOOTH_COUNTDOWN_EVENT_TYPE = "photoboothCountdownStart"
DEFAULT_PHOTOBOOTH_COUNTDOWN_SEC = 3
PHOTOBOOTH_COUNTDOWN_SOUND = "countdown"
PHOTOBOOTH_COUNTDOWN_SOUND_REQUEST = "photoboothCountdownStart"

CORS_ALLOW_ORIGIN = "*"
CORS_ALLOW_METHODS = "GET, POST, OPTIONS"
CORS_ALLOW_HEADERS = "Content-Type, Authorization"


def _apply_cors_headers(resp: web.StreamResponse, request: 'web.Request') -> web.StreamResponse:
  resp.headers["Access-Control-Allow-Origin"] = CORS_ALLOW_ORIGIN
  resp.headers["Access-Control-Allow-Methods"] = CORS_ALLOW_METHODS
  resp.headers["Access-Control-Allow-Headers"] = CORS_ALLOW_HEADERS
  resp.headers["Access-Control-Max-Age"] = "600"
  # Required by modern browsers when a public origin accesses local/private addresses.
  if request.headers.get("Access-Control-Request-Private-Network") == "true":
    resp.headers["Access-Control-Allow-Private-Network"] = "true"
  return resp


@web.middleware
async def cors_middleware(request: 'web.Request', handler):
  if request.method == "OPTIONS":
    return _apply_cors_headers(web.Response(status=204), request)

  try:
    resp = await handler(request)
  except web.HTTPException as e:
    _apply_cors_headers(e, request)
    raise

  return _apply_cors_headers(resp, request)


class CerealOutgoingMessageProxy:
  def __init__(self, sm: messaging.SubMaster):
    self.sm = sm
    self.channels: list[RTCDataChannel] = []

  def add_channel(self, channel: 'RTCDataChannel'):
    self.channels.append(channel)

  def to_json(self, msg_content: Any):
    if isinstance(msg_content, capnp._DynamicStructReader):
      msg_dict = msg_content.to_dict()
    elif isinstance(msg_content, capnp._DynamicListReader):
      msg_dict = [self.to_json(msg) for msg in msg_content]
    elif isinstance(msg_content, bytes):
      msg_dict = msg_content.decode()
    else:
      msg_dict = msg_content

    return msg_dict

  def update(self):
    # this is blocking in async context...
    self.sm.update(0)
    for service, updated in self.sm.updated.items():
      if not updated:
        continue
      msg_dict = self.to_json(self.sm[service])
      mono_time, valid = self.sm.logMonoTime[service], self.sm.valid[service]
      outgoing_msg = {"type": service, "logMonoTime": mono_time, "valid": valid, "data": msg_dict}
      encoded_msg = json.dumps(outgoing_msg).encode()
      for channel in self.channels:
        channel.send(encoded_msg)


class CerealIncomingMessageProxy:
  def __init__(self, pm: messaging.PubMaster):
    self.pm = pm

  def send(self, message: bytes):
    msg_json = json.loads(message)
    msg_type, msg_data = msg_json["type"], msg_json["data"]
    size = None
    if not isinstance(msg_data, dict):
      size = len(msg_data)

    msg = messaging.new_message(msg_type, size=size)
    setattr(msg, msg_type, msg_data)
    self.pm.send(msg_type, msg)


class CerealProxyRunner:
  def __init__(self, proxy: CerealOutgoingMessageProxy):
    self.proxy = proxy
    self.is_running = False
    self.task = None
    self.logger = logging.getLogger("webrtcd")

  def start(self):
    assert self.task is None
    self.task = asyncio.create_task(self.run())

  def stop(self):
    if self.task is None or self.task.done():
      return
    self.task.cancel()
    self.task = None

  async def run(self):
    from aiortc.exceptions import InvalidStateError

    while True:
      try:
        self.proxy.update()
      except InvalidStateError:
        self.logger.warning("Cereal outgoing proxy invalid state (connection closed)")
        break
      except Exception:
        self.logger.exception("Cereal outgoing proxy failure")
      await asyncio.sleep(0.01)


class DynamicPubMaster(messaging.PubMaster):
  def __init__(self, *args, **kwargs):
    super().__init__(*args, **kwargs)
    self.lock = asyncio.Lock()

  async def add_services_if_needed(self, services):
    async with self.lock:
      for service in services:
        if service not in self.sock:
          self.sock[service] = messaging.pub_sock(service)


class StreamSession:
  shared_pub_master = DynamicPubMaster([])

  def __init__(self, sdp: str, cameras: list[str], incoming_services: list[str], outgoing_services: list[str], debug_mode: bool = False,
               photobooth_session: bool = False, on_end: Callable[[str], None] | None = None):
    from aiortc.mediastreams import VideoStreamTrack
    from openpilot.system.webrtc.device.video import LiveStreamVideoStreamTrack
    from teleoprtc import WebRTCAnswerBuilder
    from teleoprtc.info import parse_info_from_offer

    config = parse_info_from_offer(sdp)
    builder = WebRTCAnswerBuilder(sdp)

    assert len(cameras) == config.n_expected_camera_tracks, "Incoming stream has misconfigured number of video tracks"
    for cam in cameras:
      builder.add_video_stream(cam, LiveStreamVideoStreamTrack(cam) if not debug_mode else VideoStreamTrack())

    self.stream = builder.stream()
    self.identifier = str(uuid.uuid4())

    self.incoming_bridge: CerealIncomingMessageProxy | None = None
    self.incoming_bridge_services = incoming_services
    self.outgoing_bridge: CerealOutgoingMessageProxy | None = None
    self.outgoing_bridge_runner: CerealProxyRunner | None = None
    if len(incoming_services) > 0:
      self.incoming_bridge = CerealIncomingMessageProxy(self.shared_pub_master)
    if len(outgoing_services) > 0:
      self.outgoing_bridge = CerealOutgoingMessageProxy(messaging.SubMaster(outgoing_services))
      self.outgoing_bridge_runner = CerealProxyRunner(self.outgoing_bridge)

    self.run_task: asyncio.Task | None = None
    self._photobooth_session = photobooth_session
    self._on_end = on_end
    self.logger = logging.getLogger("webrtcd")
    self.logger.info("New stream session (%s), cameras %s, incoming services %s, outgoing services %s, photobooth_session=%s",
                      self.identifier, cameras, incoming_services, outgoing_services, photobooth_session)

  def start(self):
    self.run_task = asyncio.create_task(self.run())

  def stop(self):
    if self.run_task is None or self.run_task.done():
      return
    self.run_task.cancel()
    self.run_task = None
    asyncio.run(self.post_run_cleanup())

  async def get_answer(self):
    return await self.stream.start()

  async def message_handler(self, message: bytes):
    assert self.incoming_bridge is not None
    try:
      if self._handle_photobooth_countdown_message(message):
        return
      if await self._forward_photobooth_sound_request_with_reset(message):
        return
      self.incoming_bridge.send(message)
    except Exception:
      self.logger.exception("Cereal incoming proxy failure")

  async def _forward_photobooth_sound_request_with_reset(self, message: bytes) -> bool:
    if not self._photobooth_session:
      return False
    try:
      payload = json.loads(message)
    except Exception:
      return False
    if not isinstance(payload, dict) or payload.get("type") != "soundRequest":
      return False
    data = payload.get("data")
    if not isinstance(data, dict):
      return False

    sound_name = str(data.get("sound", "")).strip()
    if not sound_name:
      return False

    # Some sound consumers only react when the value changes. Force an edge by
    # publishing a brief "none" reset before replaying the requested sound.
    self.incoming_bridge.send(json.dumps({"type": "soundRequest", "data": {"sound": "none"}}).encode())
    if sound_name.lower() != "none":
      await asyncio.sleep(0.06)
      self.incoming_bridge.send(message)
    return True

  def _handle_photobooth_countdown_message(self, message: bytes) -> bool:
    if not self._photobooth_session:
      return False
    try:
      payload = json.loads(message)
    except Exception:
      return False
    if not isinstance(payload, dict):
      return False
    msg_type = payload.get("type")
    data = payload.get("data")
    if not isinstance(data, dict):
      data = {}

    should_start_countdown = msg_type == PHOTBOOTH_COUNTDOWN_EVENT_TYPE
    if msg_type == "soundRequest":
      # Backward-compatible path: existing Connect UI buttons may send soundRequest.
      # Only start countdown for the exact explicit countdown sound token.
      sound_name = str(data.get("sound", "")).lower()
      if sound_name in (PHOTOBOOTH_COUNTDOWN_SOUND, PHOTOBOOTH_COUNTDOWN_SOUND_REQUEST.lower()):
        should_start_countdown = True

    if not should_start_countdown:
      return False

    try:
      duration_sec = int(data.get("seconds", DEFAULT_PHOTOBOOTH_COUNTDOWN_SEC))
    except (TypeError, ValueError):
      duration_sec = DEFAULT_PHOTOBOOTH_COUNTDOWN_SEC
    duration_sec = min(max(duration_sec, 1), 10)

    # Monotonic is comparable across processes on the device (CLOCK_MONOTONIC).
    now_ms = int(time.monotonic() * 1000)
    params = Params()
    # INT-typed keys require Python int; str raises TypeError in Params.python2cpp and aborts this handler.
    params.put("PhotoboothCountdownStartMs", now_ms)
    params.put("PhotoboothCountdownDurationSec", duration_sec)
    self.logger.info("Photobooth countdown started for %ss", duration_sec)
    return msg_type == PHOTBOOTH_COUNTDOWN_EVENT_TYPE

  async def run(self):
    photobooth_session_param_armed = False
    try:
      await self.stream.wait_for_connection()
      if self._photobooth_session:
        Params().put_bool("PhotoboothSessionActive", True)
        photobooth_session_param_armed = True
      if self.stream.has_messaging_channel():
        if self.incoming_bridge is not None:
          await self.shared_pub_master.add_services_if_needed(self.incoming_bridge_services)
          self.stream.set_message_handler(self.message_handler)
        if self.outgoing_bridge_runner is not None:
          channel = self.stream.get_messaging_channel()
          self.outgoing_bridge_runner.proxy.add_channel(channel)
          self.outgoing_bridge_runner.start()
      self.logger.info("Stream session (%s) connected", self.identifier)

      await self.stream.wait_for_disconnection()
      await self.post_run_cleanup()

      self.logger.info("Stream session (%s) ended", self.identifier)
    except asyncio.CancelledError:
      raise
    except Exception:
      self.logger.exception("Stream session failure")
    finally:
      if photobooth_session_param_armed:
        Params().put_bool("PhotoboothSessionActive", False)
      if self._on_end is not None:
        self._on_end(self.identifier)

  async def post_run_cleanup(self):
    await self.stream.stop()
    if self.outgoing_bridge is not None:
      self.outgoing_bridge_runner.stop()


@dataclass
class StreamRequestBody:
  sdp: str
  cameras: list[str]
  bridge_services_in: list[str] = field(default_factory=list)
  bridge_services_out: list[str] = field(default_factory=list)
  purpose: str = ""


async def get_stream(request: 'web.Request'):
  logger = logging.getLogger("webrtcd")
  try:
    stream_dict, debug_mode = request.app['streams'], request.app['debug']
    raw_body = await request.json()
    if not isinstance(raw_body, dict):
      raise web.HTTPBadRequest(text="expected JSON object")
    try:
      sdp, cameras = raw_body["sdp"], raw_body["cameras"]
    except KeyError as e:
      raise web.HTTPBadRequest(text=f"missing required field: {e.args[0]}") from e
    body = StreamRequestBody(
      sdp=sdp,
      cameras=cameras,
      bridge_services_in=list(raw_body.get("bridge_services_in", [])),
      bridge_services_out=list(raw_body.get("bridge_services_out", [])),
      purpose=str(raw_body.get("purpose", "")),
    )
    photobooth_session = body.purpose == STREAM_PURPOSE_PHOTOBOOTH

    session = StreamSession(
      body.sdp,
      body.cameras,
      body.bridge_services_in,
      body.bridge_services_out,
      debug_mode,
      photobooth_session=photobooth_session,
      on_end=lambda identifier: stream_dict.pop(identifier, None),
    )
    answer = await session.get_answer()
    session.start()

    stream_dict[session.identifier] = session
    return web.json_response({"sdp": answer.sdp, "type": answer.type})
  except web.HTTPException:
    raise
  except Exception:
    logger.exception("Error in /stream handler")
    raise


async def get_schema(request: 'web.Request'):
  services = request.query["services"].split(",")
  services = [s for s in services if s]
  assert all(s in log.Event.schema.fields and not s.endswith("DEPRECATED") for s in services), "Invalid service name"
  schema_dict = {s: generate_field(log.Event.schema.fields[s]) for s in services}
  return web.json_response(schema_dict)

async def post_notify(request: 'web.Request'):
  try:
    payload = await request.json()
  except Exception as e:
    raise web.HTTPBadRequest(text="Invalid JSON") from e

  for session in list(request.app.get('streams', {}).values()):
    try:
      ch = session.stream.get_messaging_channel()
      ch.send(json.dumps(payload))
    except Exception:
      continue

  return web.Response(status=200, text="OK")


async def options_ok(_request: 'web.Request'):
  # Preflight is handled in middleware; this keeps route matching explicit.
  return web.Response(status=204)

async def on_shutdown(app: 'web.Application'):
  for session in list(app['streams'].values()):
    session.stop()
  del app['streams']


def webrtcd_thread(host: str, port: int, debug: bool):
  logging.basicConfig(level=logging.CRITICAL, handlers=[logging.StreamHandler()])
  logging_level = logging.DEBUG if debug else logging.INFO
  logging.getLogger("WebRTCStream").setLevel(logging_level)
  logging.getLogger("webrtcd").setLevel(logging_level)

  app = web.Application(middlewares=[cors_middleware])

  app['streams'] = dict()
  app['debug'] = debug
  app.on_shutdown.append(on_shutdown)
  app.router.add_post("/stream", get_stream)
  app.router.add_options("/stream", options_ok)
  app.router.add_post("/notify", post_notify)
  app.router.add_options("/notify", options_ok)
  app.router.add_get("/schema", get_schema)
  app.router.add_options("/schema", options_ok)

  web.run_app(app, host=host, port=port)


def main():
  parser = argparse.ArgumentParser(description="WebRTC daemon")
  parser.add_argument("--host", type=str, default="0.0.0.0", help="Host to listen on")
  parser.add_argument("--port", type=int, default=5001, help="Port to listen on")
  parser.add_argument("--debug", action="store_true", help="Enable debug mode")
  args = parser.parse_args()

  webrtcd_thread(args.host, args.port, args.debug)


if __name__=="__main__":
  main()

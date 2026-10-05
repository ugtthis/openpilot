"""Record a take: RGB preview for the on-device player, HEVC for export.

Lifecycle state is guarded by ``_lock``:

* IDLE has no warm capture services and no take.
* WARMING keeps the mic and encoder pre-roll ready for a take.
* RECORDING lets the preview and HEVC workers own and write their tracks.
* FINALIZING starts only after both workers have been asked to stop; the
  teardown thread owns the writers after they join.

``_warm`` is the requested post-take policy and is guarded by the same lock.
A take may remain RECORDING after the page cools; in that case teardown ends
in IDLE. ``_stop`` and ``_preview_ready`` remain thread signals, not lifecycle
state. Shutter teardown joins before releasing leases, ignition teardown
releases before joining, and stale pre-start teardown aborts instead of
publishing tracks.
"""

import threading
import time
from collections.abc import Callable
from enum import StrEnum

from openpilot.cereal.visionipc import VisionStreamType
from openpilot.common.swaglog import cloudlog
from openpilot.system.camcorder.cameras import camera_for_stream
from openpilot.system.camcorder.capture_status import CaptureFailure
from openpilot.system.camcorder.mic import CamcorderMic
from openpilot.system.camcorder.preroll import HevcPacket, PreRoll
from openpilot.system.camcorder.clip_storage import (
  Clip, ClipWriter, extract_clip_rgb, preview_size,
)
from openpilot.system.camcorder.hevc_writer import HevcWriter
from openpilot.system.camcorder.storage import StorageFullError, StorageMonitor
from openpilot.system.camcorder.timing import boot_time_ns
from openpilot.system.loggerd.encoder_lease import acquire_encoder, release_encoder

_VIDEO_TAIL_TIMEOUT_S = 0.5


class RecorderState(StrEnum):
  IDLE = "idle"
  WARMING = "warming"
  RECORDING = "recording"
  FINALIZING = "finalizing"


class TeardownReason(StrEnum):
  SHUTTER = "shutter"
  IGNITION = "ignition"
  STALE = "stale"


class ClipRecorder:
  def __init__(self, capture_allowed: Callable[[], bool] = lambda: True, mic: CamcorderMic | None = None,
               storage: StorageMonitor | None = None):
    self._capture_allowed = capture_allowed
    self._mic = mic or CamcorderMic()
    self._storage = storage or StorageMonitor()
    self._preview_thread: threading.Thread | None = None
    self._hevc_thread: threading.Thread | None = None
    self._stop = threading.Event()
    self._preview_ready = threading.Event()
    self._started_mono = 0.0
    self._stop_mono_ns = 0
    self._stream_type = VisionStreamType.VISION_STREAM_WIDE_ROAD
    self._preview: ClipWriter | None = None
    self._hevc: HevcWriter | None = None
    self._lock = threading.Lock()
    self._warm = False
    self._state = RecorderState.IDLE
    self._preroll = PreRoll()
    self._preroll_video: list[HevcPacket] = []
    # Newest pre-roll timestamp written; the take's own sockets repeat earlier packets.
    self._hevc_after_ns = 0
    self._capture_error = ""
    self._capture_failure = CaptureFailure.NONE

  @property
  def recording(self) -> bool:
    with self._lock:
      return self._state in (RecorderState.RECORDING, RecorderState.FINALIZING)

  @property
  def state(self) -> RecorderState:
    with self._lock:
      return self._state

  @property
  def capture_error(self) -> str:
    with self._lock:
      return self._capture_error

  @property
  def capture_failure(self) -> CaptureFailure:
    with self._lock:
      return self._capture_failure

  @property
  def elapsed_s(self) -> float:
    if not self.recording:
      return 0.0
    return max(0.0, boot_time_ns() / 1e9 - self._started_mono)

  @property
  def mic_name(self) -> str:
    return self._mic.device_name

  @property
  def mic_sample_rate(self) -> int:
    return self._mic.sample_rate

  @property
  def mic_channels(self) -> int:
    return self._mic.channels

  @property
  def mic_error(self) -> str:
    return self._mic.error

  @property
  def ready(self) -> bool:
    return self._preroll.ready and self._mic.ready

  def poll(self) -> None:
    with self._lock:
      if self._state != RecorderState.RECORDING:
        return
    if self._mic.write_error:
      self._set_capture_error(self._mic.write_error, CaptureFailure.AUDIO)
    elif not self._storage.available():
      self._set_capture_error("storage full", CaptureFailure.STORAGE)

  def set_warm(self, warm: bool, stream_type: VisionStreamType) -> None:
    """Keep encoderd and direct mic capture warm while the camcorder is on screen.

    Spawning capture on the shutter press cost every take its first ~0.7 s.
    """
    warm = warm and self._capture_allowed()
    with self._lock:
      changed = warm != self._warm
      self._warm = warm
      active = self._state in (RecorderState.RECORDING, RecorderState.FINALIZING)
      if not active:
        self._state = RecorderState.WARMING if warm else RecorderState.IDLE
    if changed:
      if warm:
        try:
          acquire_encoder()
        except (OSError, RuntimeError):
          with self._lock:
            self._warm = False
            if self._state == RecorderState.WARMING:
              self._state = RecorderState.IDLE
          self._release_leases()
          cloudlog.exception("camcorder could not warm up recording services")
      elif not active:
        self._release_leases()
    with self._lock:
      warm = self._warm
      active = self._state in (RecorderState.RECORDING, RecorderState.FINALIZING)
    if not warm:
      self._preroll.stop()
    elif not active:
      self._mic.start()
      self._preroll.start(camera_for_stream(stream_type).encode_service)

  def start(self, stream_type: VisionStreamType, recording_start_mono_ns: int | None = None) -> bool:
    # Recorder-level backstop: never acquire offroad capture processes based
    # only on the UI page being visible.
    with self._lock:
      active = self._state in (RecorderState.RECORDING, RecorderState.FINALIZING)
    if not self._capture_allowed() or active or not self._discard_stale():
      return False
    self._stop.clear()
    self._preview_ready.clear()
    self._stop_mono_ns = 0
    self._preroll_video = []
    self._hevc_after_ns = 0
    with self._lock:
      self._capture_error = ""
      self._capture_failure = CaptureFailure.NONE
    self._stream_type = stream_type
    self._started_mono = (recording_start_mono_ns or boot_time_ns()) / 1e9
    try:
      self._storage.start()
      acquire_encoder()
      self._mic.start()
    except StorageFullError as exc:
      self._set_capture_error(str(exc), CaptureFailure.STORAGE)
      self._release_leases()
      cloudlog.exception("camcorder could not request recording services")
      return False
    except (OSError, RuntimeError) as exc:
      self._set_capture_error(str(exc))
      self._release_leases()
      cloudlog.exception("camcorder could not request recording services")
      return False
    self._preview_thread = threading.Thread(target=self._capture_preview, name="camcorder-preview", daemon=True)
    self._hevc_thread = threading.Thread(target=self._capture_hevc, name="camcorder-hevc", daemon=True)
    with self._lock:
      self._state = RecorderState.RECORDING
    try:
      self._preview_thread.start()
      self._hevc_thread.start()
    except RuntimeError as exc:
      self._set_capture_error(f"capture worker could not start: {exc}")
      self._stop.set()
      stopped = self._join_threads()
      self._release_leases()
      with self._lock:
        if stopped:
          self._state = self._idle_state_locked()
      return False
    return True

  def stop(self, stop_mono_ns: int | None = None) -> Clip | None:
    with self._lock:
      if self._state != RecorderState.RECORDING:
        return None
      self._state = RecorderState.FINALIZING
      self._stop_mono_ns = stop_mono_ns or boot_time_ns()
    self._stop.set()
    _stopped, clip = self._teardown(TeardownReason.SHUTTER)
    return clip

  def stop_async(self) -> None:
    """Save a take during ignition teardown without blocking the caller."""
    with self._lock:
      if self._state == RecorderState.FINALIZING:
        return
      self._stop_mono_ns = boot_time_ns()
      self._warm = False
      self._state = RecorderState.FINALIZING
    self._stop.set()
    self._preroll.stop()
    self._release_leases()
    try:
      threading.Thread(target=self._finish_stop_async, name="camcorder-stop", daemon=True).start()
    except RuntimeError:
      # Resources are already released and capture workers have been asked to
      # stop. Leave stale-file cleanup to the next offroad start.
      with self._lock:
        self._state = RecorderState.IDLE
      cloudlog.exception("camcorder stop finalizer could not start")

  def _finish_stop_async(self) -> None:
    self._teardown(TeardownReason.IGNITION)

  def _teardown(self, reason: TeardownReason) -> tuple[bool, Clip | None]:
    """Join workers, then either abort stale tracks or publish a completed take."""
    timeout = 1.0 if reason == TeardownReason.STALE else 2.0
    stopped = self._join_threads(timeout)
    if reason in (TeardownReason.SHUTTER, TeardownReason.STALE):
      # Shutter capture drains before leases go away. Ignition has already
      # released them so process shutdown cannot wait on capture threads.
      self._release_leases()
    if not stopped:
      if reason != TeardownReason.STALE:
        self._set_capture_error("capture workers did not stop")
      self._finish_teardown_state(preserve_live_preview=True)
      return False, None

    if reason == TeardownReason.STALE:
      with self._lock:
        if self._hevc is not None:
          self._hevc.abort()
          self._hevc = None
        if self._preview is not None:
          self._preview.abort()
          self._preview = None
        self._mic.abort()
      self._finish_teardown_state()
      return True, None

    with self._lock:
      preview, hevc = self._preview, self._hevc
      self._preview = None
      self._hevc = None
    master = None
    audio_info = None
    try:
      if hevc is not None:
        master = hevc.finalize(self._stop_mono_ns)
    except Exception as exc:
      self._set_capture_error(f"encoded video finalization failed: {exc}")
      cloudlog.exception("camcorder encoded video finalization failed")
    # Workers keep writing until they notice the stop, so every track is cut to
    # one end: the press, extended to finish the last video frame shown across it.
    end_ns = max(self._stop_mono_ns, master.end_ns if master is not None else 0)
    try:
      audio_info = self._mic.finish(end_ns)
    except Exception as exc:
      self._set_capture_error(f"audio finalization failed: {exc}", CaptureFailure.AUDIO)
      cloudlog.exception("camcorder audio finalization failed")
    try:
      clip = preview.finalize(master, audio_info, end_ns) if preview is not None else None
      return True, clip
    except Exception as exc:
      self._set_capture_error(f"clip finalization failed: {exc}")
      cloudlog.exception("camcorder clip finalization failed")
      return True, None
    finally:
      self._finish_teardown_state()

  def _discard_stale(self) -> bool:
    self._stop.set()
    stopped, _clip = self._teardown(TeardownReason.STALE)
    if not stopped:
      cloudlog.error("stale camcorder threads did not stop")
    return stopped

  def _release_leases(self) -> None:
    with self._lock:
      if self._warm:
        return
    release_encoder()
    self._mic.stop()

  def _idle_state_locked(self) -> RecorderState:
    return RecorderState.WARMING if self._warm else RecorderState.IDLE

  def _finish_teardown_state(self, preserve_live_preview: bool = False) -> None:
    preview_alive = self._preview_thread is not None and self._preview_thread.is_alive()
    with self._lock:
      if preserve_live_preview and preview_alive:
        self._state = RecorderState.RECORDING
      else:
        self._state = self._idle_state_locked()

  def _join_threads(self, timeout: float = 2.0) -> bool:
    deadline = time.monotonic() + timeout
    threads = (self._preview_thread, self._hevc_thread)
    for thread in threads:
      if thread is not None:
        thread.join(timeout=max(0.0, deadline - time.monotonic()))
    stopped = all(thread is None or not thread.is_alive() for thread in threads)
    if stopped:
      self._preview_thread = None
      self._hevc_thread = None
    return stopped

  def _capture_preview(self):
    from msgq.visionipc import VisionIpcClient

    client = VisionIpcClient("camerad", self._stream_type, conflate=True)
    camera = camera_for_stream(self._stream_type)
    try:
      while not self._stop.is_set() and not (client.is_connected() and client.num_buffers):
        client.connect(False)
        if not (client.is_connected() and client.num_buffers):
          self._stop.wait(0.2)

      while not self._stop.is_set():
        buf = client.recv(timeout_ms=100)
        if buf is None:
          continue
        with self._lock:
          if self._preview is None:
            press_ns = int(self._started_mono * 1e9)
            # Overlaps the take's own subscriptions, which began at the press.
            self._preroll_video = self._preroll.take(press_ns)
            width, height = preview_size(buf.width, buf.height)
            self._preview = ClipWriter(camera,
                                       width, height, preview_contains_full_frame=True,
                                       recording_start_mono_ns=press_ns)
            video_start_ns = self._preroll_video[0].timestamp_ns if self._preroll_video else press_ns
            self._mic.attach(self._preview.path, min(video_start_ns, press_ns))
            self._preview_ready.set()
          preview = self._preview
        rgb = extract_clip_rgb(buf.data, buf.width, buf.height, buf.stride, buf.uv_offset,
                               out_w=preview.width, out_h=preview.height,
                               flip_h=camera.flip_h, enhance=camera.enhance, crop_aspect=None)
        t_ms = int((time.monotonic() - self._started_mono) * 1000)
        with self._lock:
          preview.add_frame(rgb, t_ms)
    except Exception as exc:
      self._set_capture_error(f"preview capture failed: {exc}")
      cloudlog.exception("camcorder preview capture failed")
    finally:
      del client

  def _capture_hevc(self):
    from openpilot.cereal import messaging

    service = camera_for_stream(self._stream_type).encode_service
    sock = messaging.sub_sock(service, conflate=False)
    try:
      while not self._stop.is_set():
        if not self._preview_ready.wait(0.05):
          continue
        messages = messaging.drain_sock(sock, wait_for_one=False)
        if not messages:
          self._stop.wait(0.01)
          continue
        self._write_hevc([getattr(event, service) for event in messages])
      if self._preview_ready.is_set():
        self._drain_hevc_tail(sock, service)
    except Exception as exc:
      self._set_capture_error(f"encoded video capture failed: {exc}")
      cloudlog.exception("camcorder encoded video capture failed")

  def _write_hevc(self, encoded_frames) -> int:
    with self._lock:
      if self._preview is None:
        return 0
      if self._hevc is None:
        self._hevc = HevcWriter(self._preview.path)
        for packet in self._preroll_video:
          self._hevc.add_packet(packet.header, packet.data, packet.keyframe,
                                packet.width, packet.height, packet.timestamp_ns)
          self._hevc_after_ns = packet.timestamp_ns
      hevc = self._hevc
    for encoded in encoded_frames:
      if encoded.idx.timestampEof > self._hevc_after_ns:
        hevc.add_encoded(encoded)
    return int(encoded_frames[-1].idx.timestampEof) if encoded_frames else 0

  def _set_capture_error(self, error: str, failure: CaptureFailure = CaptureFailure.RECORDING) -> None:
    with self._lock:
      if not self._capture_error:
        self._capture_error = error
        self._capture_failure = failure
    self._stop.set()

  def _drain_hevc_tail(self, sock, service: str) -> None:
    from openpilot.cereal import messaging

    deadline = time.monotonic() + _VIDEO_TAIL_TIMEOUT_S
    while self._stop_mono_ns and time.monotonic() < deadline:
      events = messaging.drain_sock(sock, wait_for_one=False)
      # Only a frame after the press proves every frame up to it has arrived.
      if events and self._write_hevc([getattr(event, service) for event in events]) > self._stop_mono_ns:
        return
      time.sleep(0.005)

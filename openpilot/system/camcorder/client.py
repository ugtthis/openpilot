"""Non-blocking UI client for the managed camcorder recorder."""

import time

from openpilot.cereal import messaging
from openpilot.cereal.visionipc import VisionStreamType
from openpilot.system.camcorder.clip_storage import Clip, clips_root, load_clip
from openpilot.system.camcorder_lease import acquire_camcorder, release_camcorder

_STREAM_NAMES = {
  VisionStreamType.VISION_STREAM_WIDE_ROAD: "wideRoad",
  VisionStreamType.VISION_STREAM_CABIN: "cabin",
}


class CamcorderClient:
  def __init__(self, pm=None, sm=None):
    self._pm = pm or messaging.PubMaster(["camcorderControl"])
    self._sm = sm or messaging.SubMaster(["camcorderState"])
    self._sequence = 0
    self._pending = None
    self._phase = "idle"
    self._elapsed_s = 0.0
    self._warm = False
    self._lease_held = False
    self._stream_type = VisionStreamType.VISION_STREAM_WIDE_ROAD
    self._requested_recording = False
    self._completed_clip: Clip | None = None

  @property
  def recording(self) -> bool:
    return self._requested_recording or self._phase in ("recording", "finalizing")

  @property
  def elapsed_s(self) -> float:
    return self._elapsed_s

  def set_warm(self, warm: bool, stream_type: VisionStreamType) -> None:
    stream_changed = stream_type != self._stream_type
    self._stream_type = stream_type
    self._warm = warm
    if warm and not self._lease_held:
      acquire_camcorder()
      self._lease_held = True
    if self._lease_held and warm and stream_changed and not self.recording:
      self._send("idle", stream_type, time.monotonic_ns())
    if not warm and self._lease_held and not self.recording:
      release_camcorder()
      self._lease_held = False

  def start(self, stream_type: VisionStreamType, press_mono_ns: int | None = None) -> bool:
    if self.recording:
      return False
    self.set_warm(True, stream_type)
    self._requested_recording = True
    self._completed_clip = None
    self._send("start", stream_type, press_mono_ns or time.monotonic_ns())
    return True

  def stop(self, stop_mono_ns: int | None = None) -> None:
    if not self.recording:
      return
    self._send("stop", self._stream_type, stop_mono_ns or time.monotonic_ns())

  def close(self) -> None:
    self.stop()
    if self._lease_held:
      release_camcorder()
      self._lease_held = False
    self._warm = False

  def update(self) -> Clip | None:
    self._sm.update(0)
    if self._sm.updated["camcorderState"]:
      state = self._sm["camcorderState"]
      self._phase = str(state.phase)
      self._elapsed_s = float(state.elapsedS)
      if self._pending is not None and int(state.sequence) >= self._sequence:
        self._pending = None
      if self._requested_recording and self._phase == "warming" and state.clipId:
        self._requested_recording = False
        self._completed_clip = load_clip(clips_root() / str(state.clipId))
        if not self._warm and self._lease_held:
          release_camcorder()
          self._lease_held = False
    if self._pending is not None:
      self._pm.send("camcorderControl", self._pending)
    clip, self._completed_clip = self._completed_clip, None
    return clip

  def _send(self, action: str, stream_type: VisionStreamType, request_mono_ns: int) -> None:
    self._sequence += 1
    msg = messaging.new_message("camcorderControl", valid=True)
    msg.camcorderControl.sequence = self._sequence
    msg.camcorderControl.action = action
    msg.camcorderControl.stream = _STREAM_NAMES[stream_type]
    msg.camcorderControl.requestMonoTime = request_mono_ns
    self._pending = msg
    self._pm.send("camcorderControl", msg)

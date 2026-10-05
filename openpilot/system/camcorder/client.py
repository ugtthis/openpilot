"""Non-blocking UI client for the managed camcorder recorder."""

import time

from openpilot.cereal import messaging
from openpilot.cereal.visionipc import VisionStreamType
from openpilot.system.camcorder.cameras import camera_for_stream
from openpilot.system.camcorder.clip_storage import Clip, clips_root, load_clip
from openpilot.system.camcorder.mic import mic_label
from openpilot.system.camcorder.timing import boot_time_ns
from openpilot.system.camcorder_lease import acquire_camcorder, release_camcorder

_NOTICE_TEXT = {
  "storageFullSaved": "Storage full — clip saved",
  "storageFull": "Storage full — delete clips to record",
  "audioErrorSaved": "Audio error — clip saved",
  "recordingErrorSaved": "Camera error — clip saved",
  "recordingFailed": "Recording failed — try again",
  "micDisconnected": "Mic disconnected — recording silence",
  "micUnavailable": "Mic unavailable — reconnect it",
  "recordingRecovered": "Recorder restarted — clip recovered",
  "timelineGapSaved": "Recording gap detected — clip saved",
}
_UNKNOWN_NOTICE_TEXT = "Recording error — try again"
# A restarted camcorderd announces a new session well before this. The timeout
# only unlatches the UI when the recorder never comes back.
_STATE_TIMEOUT_S = 10.0
_TAKE_PHASES = ("recording", "finalizing")


def notice_text(notice: str) -> str:
  if notice == "none":
    return ""
  return _NOTICE_TEXT.get(notice, _UNKNOWN_NOTICE_TEXT)


class CamcorderClient:
  def __init__(self, pm=None, sm=None):
    self._pm = pm or messaging.PubMaster(["camcorderControl"])
    self._sm = sm or messaging.SubMaster(["camcorderState"])
    self._sequence = 0
    self._pending = None
    self._phase = "warming"
    self._elapsed_s = 0.0
    self._remaining_s: float | None = None
    self._mic_name = ""
    self._warm = False
    self._lease_held = False
    self._stream_type = VisionStreamType.VISION_STREAM_WIDE_ROAD
    self._requested_recording = False
    self._take_sequence = 0
    self._requested_photo = False
    self._completed_clip: Clip | None = None
    self._error = ""
    self._notice_code = "none"
    self._dismissed_notice = ""
    self._local_error = ""
    self._session_id = 0
    self._last_state_update = time.monotonic()

  @property
  def recording(self) -> bool:
    return self._requested_recording or self._phase in _TAKE_PHASES

  @property
  def elapsed_s(self) -> float:
    return self._elapsed_s

  @property
  def remaining_s(self) -> float | None:
    """Recording time left before storage stops a take, once camcorderd has reported it."""
    return self._remaining_s

  @property
  def mic_label(self) -> str:
    """Which mic the current or next take records from, e.g. "USB mic"; empty when none is working."""
    return mic_label(self._mic_name)

  @property
  def error(self) -> str:
    return self._error

  def dismiss_error(self) -> None:
    self._dismissed_notice = self._notice_code
    self._local_error = ""
    self._error = ""

  def set_warm(self, warm: bool, stream_type: VisionStreamType) -> None:
    stream_changed = stream_type != self._stream_type
    self._stream_type = stream_type
    self._warm = warm
    if warm and not self._lease_held:
      acquire_camcorder()
      self._lease_held = True
    if self._lease_held and warm and stream_changed and not self.recording:
      self._phase = "warming"
      self._send("idle", stream_type, boot_time_ns())
    if not warm and self._lease_held and not self.recording:
      self._release_lease()

  def start(self, stream_type: VisionStreamType, press_mono_ns: int | None = None) -> bool:
    """Request a take; the command repeats until camcorderd, which may still be launching, acknowledges it."""
    if self.recording:
      return False
    self.set_warm(True, stream_type)
    self._requested_recording = True
    self._completed_clip = None
    self._dismissed_notice = self._notice_code
    self._local_error = ""
    self._error = ""
    self._last_state_update = time.monotonic()
    self._send("start", stream_type, press_mono_ns or boot_time_ns())
    self._take_sequence = self._sequence
    return True

  def stop(self, stop_mono_ns: int | None = None) -> None:
    if not self.recording:
      return
    self._send("stop", self._stream_type, stop_mono_ns or boot_time_ns())

  def take_photo(self, stream_type: VisionStreamType) -> bool:
    if self.recording:
      return False
    self.set_warm(True, stream_type)
    self._requested_photo = True
    self._dismissed_notice = self._notice_code
    self._local_error = ""
    self._error = ""
    self._send("photo", stream_type, boot_time_ns())
    return True

  def close(self) -> None:
    self.stop()
    if self._lease_held:
      self._release_lease()
    self._warm = False
    self._requested_photo = False

  def update(self) -> Clip | None:
    self._sm.update(0)
    if self._sm.updated["camcorderState"]:
      state = self._sm["camcorderState"]
      self._last_state_update = time.monotonic()
      if self._session_id and state.sessionId != self._session_id:
        self._reconnect(recovered=bool(state.clipId))
      self._session_id = state.sessionId
      self._phase = str(state.phase)
      self._elapsed_s = float(state.elapsedS)
      self._remaining_s = float(state.remainingS)
      self._mic_name = str(state.micName)
      self._update_notice(str(state.notice))
      if self._pending is not None and int(state.sequence) >= self._sequence:
        self._pending = None
        self._requested_photo = False
      # Until camcorderd acknowledges the start, its phase and clipId describe the previous take.
      take_acknowledged = int(state.sequence) >= self._take_sequence
      if self._requested_recording and take_acknowledged and self._phase not in _TAKE_PHASES:
        self._end_take()
        if state.clipId:
          self._completed_clip = load_clip(clips_root() / str(state.clipId))
    elif self._requested_recording and time.monotonic() - self._last_state_update > _STATE_TIMEOUT_S:
      self._phase = "failed"
      self._abandon_take("Recorder unavailable — reopen camera")
    if self._pending is not None:
      self._publish_pending()
    clip, self._completed_clip = self._completed_clip, None
    return clip

  def _reconnect(self, recovered: bool) -> None:
    """camcorderd restarted: the old take and any command sent to it are gone."""
    self._pending = None
    self._requested_photo = False
    # The new session restarts its sequence, and its first state is the take's outcome.
    self._take_sequence = 0
    if self._requested_recording and not recovered:
      self._abandon_take("Recorder restarted — no clip recovered")
    if self._warm:
      self._send("idle", self._stream_type, boot_time_ns())

  def _end_take(self) -> None:
    self._requested_recording = False
    if not self._warm and self._lease_held:
      self._release_lease()

  def _release_lease(self) -> None:
    release_camcorder()
    self._lease_held = False
    # Manager may stop camcorderd now, so its next session is a fresh start, not a crash.
    self._session_id = 0

  def _abandon_take(self, message: str) -> None:
    self._end_take()
    self._pending = None
    self._local_error = message
    self._error = message

  def _update_notice(self, notice: str) -> None:
    self._notice_code = notice
    if notice == "none":
      self._dismissed_notice = ""
      self._error = self._local_error
    elif notice != self._dismissed_notice:
      self._local_error = ""
      self._error = notice_text(notice)

  def _send(self, action: str, stream_type: VisionStreamType, request_mono_ns: int) -> None:
    self._sequence += 1
    msg = messaging.new_message("camcorderControl", valid=True)
    msg.camcorderControl.sequence = self._sequence
    msg.camcorderControl.action = action
    msg.camcorderControl.stream = camera_for_stream(stream_type).control_name
    msg.camcorderControl.requestMonoTime = request_mono_ns
    self._pending = msg
    self._publish_pending()

  def _publish_pending(self) -> None:
    assert self._pending is not None
    self._pm.send("camcorderControl", self._pending)
    self._pending.clear_write_flag()

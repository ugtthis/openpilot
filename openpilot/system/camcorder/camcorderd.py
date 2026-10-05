#!/usr/bin/env python3
"""Offroad camcorder recorder process.

The UI owns only the page policy and sends shutter commands. Capture and disk
writing live here at normal process priority so UI rendering cannot starve them.
"""

from openpilot.cereal import messaging
from openpilot.cereal.visionipc import VisionStreamType
from openpilot.common.realtime import Ratekeeper
from openpilot.common.swaglog import cloudlog
from openpilot.system.camcorder.cameras import camera_for_control_name
from openpilot.system.camcorder.clip_storage import Clip, recover_interrupted_clips
from openpilot.system.camcorder.recorder import ClipRecorder
from openpilot.system.camcorder.storage import StorageMonitor
from openpilot.system.camcorder.timing import boot_time_ns


def failure_notice(failure: str, clip_saved: bool) -> str:
  if failure == "storage":
    return "storageFullSaved" if clip_saved else "storageFull"
  if failure == "audio" and clip_saved:
    return "audioErrorSaved"
  if clip_saved:
    return "recordingErrorSaved"
  return "recordingFailed"


class CamcorderDaemon:
  def __init__(self, recorder: ClipRecorder | None = None, recovered_clip: Clip | None = None):
    self.recorder = recorder or ClipRecorder()
    self.storage = StorageMonitor()
    self.session_id = boot_time_ns()
    self.stream_type = VisionStreamType.VISION_STREAM_WIDE_ROAD
    self.sequence = 0
    self.phase = "warming"
    self.clip_id = recovered_clip.clip_id if recovered_clip is not None else ""
    self.error = ""
    self.notice = "recordingRecovered" if recovered_clip is not None else "none"
    self.audio_gap_count = 0
    self.audio_gap_frame_count = 0

  def apply_control(self, control) -> None:
    sequence = int(control.sequence)
    if sequence <= self.sequence:
      return
    self.sequence = sequence
    camera = camera_for_control_name(str(control.stream))
    if camera is not None:
      self.stream_type = camera.stream_type
    action = str(control.action)
    try:
      if action == "idle" and not self.recorder.recording:
        self.recorder.set_warm(True, self.stream_type)
      elif action == "start":
        self.notice = "none"
        self.recorder.set_warm(True, self.stream_type)
        if not self.recorder.ready:
          self.phase = "warming"
          self.error = "recorder is still warming up"
        elif self.recorder.start(self.stream_type, int(control.requestMonoTime)):
          self.phase = "recording"
          self.clip_id = ""
          self.error = ""
          self.notice = "none"
          self.audio_gap_count = 0
          self.audio_gap_frame_count = 0
        else:
          self.phase = "failed"
          self.error = self.recorder.capture_error or "recorder could not start"
          self.notice = failure_notice(self.recorder.capture_failure, False)
      elif action == "stop" and self.recorder.recording:
        self._finish_recording(int(control.requestMonoTime))
    except Exception as exc:
      self.phase = "failed"
      self.error = str(exc)
      self.notice = "recordingFailed"
      cloudlog.exception("camcorder command failed")

  def update(self) -> None:
    if self.phase == "recording":
      self.recorder.poll()
    if self.phase == "recording" and self.recorder.capture_error:
      self._finish_recording()
      return
    if not self.recorder.recording and self.phase not in ("failed", "finalizing"):
      self.recorder.set_warm(True, self.stream_type)
      self.phase = "idle" if self.recorder.ready else "warming"

  def _finish_recording(self, stop_mono_ns: int | None = None) -> None:
    self.phase = "finalizing"
    clip = self.recorder.stop(stop_mono_ns)
    self.clip_id = clip.clip_id if clip is not None else ""
    self.audio_gap_count = clip.audio_gap_count if clip is not None else 0
    self.audio_gap_frame_count = clip.audio_gap_frame_count if clip is not None else 0
    self.error = self.recorder.capture_error
    if clip is None and not self.error:
      self.error = "recording stopped without a usable clip"
    if self.error:
      self.notice = failure_notice(self.recorder.capture_failure, clip is not None)
    else:
      self.notice = "timelineGapSaved" if clip is not None and clip.has_timeline_gap else "none"
    self.phase = "warming"

  def state_message(self):
    msg = messaging.new_message("camcorderState", valid=True)
    state = msg.camcorderState
    state.sessionId = self.session_id
    state.sequence = self.sequence
    state.phase = self.phase
    state.clipId = self.clip_id
    state.error = self.error
    notice = self.notice
    state.elapsedS = self.recorder.elapsed_s
    state.remainingS = self.storage.remaining_s()
    state.audioSampleRate = self.recorder.mic_sample_rate
    state.audioChannels = self.recorder.mic_channels
    state.audioGapCount = self.audio_gap_count
    state.audioGapFrameCount = self.audio_gap_frame_count
    # Name only a working input; on failure the error banner explains instead.
    state.micName = "" if self.recorder.mic_error else self.recorder.mic_name
    if self.recorder.mic_error and not state.error:
      state.error = self.recorder.mic_error
      notice = "micDisconnected" if self.phase == "recording" else "micUnavailable"
    state.notice = notice
    return msg

  def run(self) -> None:
    sm = messaging.SubMaster(["camcorderControl"])
    pm = messaging.PubMaster(["camcorderState"])
    rk = Ratekeeper(10)
    self.recorder.set_warm(True, self.stream_type)
    try:
      while True:
        sm.update(0)
        if sm.updated["camcorderControl"]:
          self.apply_control(sm["camcorderControl"])
        self.update()
        pm.send("camcorderState", self.state_message())
        rk.keep_time()
    finally:
      self.recorder.stop()
      self.recorder.set_warm(False, self.stream_type)


def main() -> None:
  recovered = recover_interrupted_clips()
  for clip in recovered:
    cloudlog.warning(f"recovered interrupted camcorder clip: {clip.clip_id}")
  latest = max(recovered, key=lambda clip: clip.started_at, default=None)
  CamcorderDaemon(recovered_clip=latest).run()


if __name__ == "__main__":
  main()

#!/usr/bin/env python3
"""Offroad camcorder recorder process.

The UI owns only the page policy and sends shutter commands. Capture and disk
writing live here at normal process priority so UI rendering cannot starve them.
"""

from openpilot.cereal import messaging
from openpilot.cereal.visionipc import VisionStreamType
from openpilot.common.realtime import Ratekeeper
from openpilot.common.swaglog import cloudlog
from openpilot.system.camcorder.recorder import ClipRecorder

_STREAMS = {
  "wideRoad": VisionStreamType.VISION_STREAM_WIDE_ROAD,
  "cabin": VisionStreamType.VISION_STREAM_CABIN,
}


class CamcorderDaemon:
  def __init__(self, recorder: ClipRecorder | None = None):
    self.recorder = recorder or ClipRecorder()
    self.stream_type = VisionStreamType.VISION_STREAM_WIDE_ROAD
    self.sequence = 0
    self.phase = "warming"
    self.clip_id = ""
    self.error = ""

  def apply_control(self, control) -> None:
    sequence = int(control.sequence)
    if sequence <= self.sequence:
      return
    self.sequence = sequence
    self.stream_type = _STREAMS.get(str(control.stream), self.stream_type)
    action = str(control.action)
    try:
      if action == "idle" and not self.recorder.recording:
        self.recorder.set_warm(True, self.stream_type)
      elif action == "start":
        self.recorder.set_warm(True, self.stream_type)
        if self.recorder.start(self.stream_type, int(control.requestMonoTime)):
          self.phase = "recording"
          self.clip_id = ""
          self.error = ""
        else:
          self.phase = "failed"
          self.error = "recorder could not start"
      elif action == "stop" and self.recorder.recording:
        self.phase = "finalizing"
        clip = self.recorder.stop(int(control.requestMonoTime))
        self.clip_id = clip.clip_id if clip is not None else ""
        self.phase = "warming"
    except Exception as exc:
      self.phase = "failed"
      self.error = str(exc)
      cloudlog.exception("camcorder command failed")

  def state_message(self):
    msg = messaging.new_message("camcorderState", valid=True)
    state = msg.camcorderState
    state.sequence = self.sequence
    state.phase = self.phase
    state.clipId = self.clip_id
    state.error = self.error
    state.elapsedS = self.recorder.elapsed_s
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
        if not self.recorder.recording and self.phase not in ("failed", "finalizing"):
          self.phase = "warming"
          self.recorder.set_warm(True, self.stream_type)
        pm.send("camcorderState", self.state_message())
        rk.keep_time()
    finally:
      self.recorder.stop()
      self.recorder.set_warm(False, self.stream_type)


def main() -> None:
  CamcorderDaemon().run()


if __name__ == "__main__":
  main()

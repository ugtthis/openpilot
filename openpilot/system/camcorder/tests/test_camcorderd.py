from types import SimpleNamespace

from openpilot.cereal.visionipc import VisionStreamType
from openpilot.system.camcorder.camcorderd import CamcorderDaemon


class FakeRecorder:
  def __init__(self):
    self.recording = False
    self.elapsed_s = 0.0
    self.starts = []
    self.stops = 0

  def start(self, stream_type):
    self.starts.append(stream_type)
    self.recording = True
    return True

  def stop(self):
    self.stops += 1
    self.recording = False
    return SimpleNamespace(clip_id="saved-clip")


def control(sequence, action, stream="wideRoad"):
  return SimpleNamespace(sequence=sequence, action=action, stream=stream)


def test_start_and_stop_commands_publish_the_saved_clip():
  recorder = FakeRecorder()
  daemon = CamcorderDaemon(recorder)

  daemon.apply_control(control(1, "start", "cabin"))
  assert recorder.starts == [VisionStreamType.VISION_STREAM_CABIN]
  assert daemon.phase == "recording"

  daemon.apply_control(control(2, "stop", "cabin"))
  assert recorder.stops == 1
  assert daemon.phase == "warming"
  assert daemon.clip_id == "saved-clip"

  state = daemon.state_message().camcorderState
  assert state.sequence == 2
  assert state.phase == "warming"
  assert state.clipId == "saved-clip"


def test_duplicate_or_old_commands_are_ignored():
  recorder = FakeRecorder()
  daemon = CamcorderDaemon(recorder)

  daemon.apply_control(control(4, "start"))
  daemon.apply_control(control(4, "stop"))
  daemon.apply_control(control(3, "stop"))

  assert len(recorder.starts) == 1
  assert recorder.stops == 0

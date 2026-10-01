from types import SimpleNamespace

from openpilot.cereal.visionipc import VisionStreamType
from openpilot.system.camcorder.camcorderd import CamcorderDaemon
from openpilot.system.camcorder.recorder import ClipRecorder


class FakeRecorder:
  def __init__(self):
    self.recording = False
    self.elapsed_s = 0.0
    self.mic_name = "test mic"
    self.mic_sample_rate = 48000
    self.mic_channels = 2
    self.mic_error = ""
    self.capture_error = ""
    self.ready = True
    self.starts = []
    self.stops = 0

  def set_warm(self, warm, stream_type):
    pass

  def start(self, stream_type, request_mono_ns):
    self.starts.append((stream_type, request_mono_ns))
    self.recording = True
    return True

  def stop(self, request_mono_ns=None):
    self.stops += 1
    self.recording = False
    return SimpleNamespace(clip_id="saved-clip", audio_gap_count=0, audio_gap_frame_count=0)


def control(sequence, action, stream="wideRoad", request_mono_time=123):
  return SimpleNamespace(sequence=sequence, action=action, stream=stream, requestMonoTime=request_mono_time)


def test_start_and_stop_commands_publish_the_saved_clip():
  recorder = FakeRecorder()
  daemon = CamcorderDaemon(recorder)

  daemon.apply_control(control(1, "start", "cabin"))
  assert recorder.starts == [(VisionStreamType.VISION_STREAM_CABIN, 123)]
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


def test_capture_failure_stops_and_publishes_the_salvaged_clip():
  recorder = FakeRecorder()
  daemon = CamcorderDaemon(recorder)
  daemon.apply_control(control(1, "start"))
  recorder.capture_error = "preview capture failed: disk write failed"

  daemon.update()

  assert recorder.stops == 1
  assert daemon.phase == "warming"
  assert daemon.clip_id == "saved-clip"
  assert daemon.error == recorder.capture_error


def test_finalization_salvages_other_tracks_when_one_writer_fails():
  class BrokenHevc:
    def finalize(self):
      raise OSError("video write failed")

  class Preview:
    def finalize(self, master, audio):
      assert master is None
      assert audio == "audio"
      return "clip"

  mic = SimpleNamespace(finish=lambda stop_ns: "audio")
  recorder = ClipRecorder(mic=mic)
  recorder._warm = True
  recorder._recording.set()
  recorder._preview = Preview()
  recorder._hevc = BrokenHevc()

  assert recorder.stop(123) == "clip"
  assert recorder.capture_error == "encoded video finalization failed: video write failed"
  assert not recorder.recording

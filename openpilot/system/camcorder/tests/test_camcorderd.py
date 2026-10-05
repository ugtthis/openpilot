import ast
import inspect
import textwrap
from types import SimpleNamespace

from openpilot.cereal import messaging
from openpilot.cereal.visionipc import VisionStreamType
from openpilot.system.camcorder.camcorderd import CamcorderDaemon
from openpilot.system.camcorder.capture_status import CaptureFailure, CaptureStatus
from openpilot.system.camcorder.hevc_writer import MasterInfo
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
    self.capture_failure = "none"
    self.ready = True
    self.starts = []
    self.stops = 0
    self.timeline_gap = False

  def set_warm(self, warm, stream_type):
    pass

  def poll(self):
    pass

  def start(self, stream_type, request_mono_ns):
    self.starts.append((stream_type, request_mono_ns))
    self.recording = True
    return True

  def stop(self, request_mono_ns=None):
    self.stops += 1
    self.recording = False
    return SimpleNamespace(clip_id="saved-clip", audio_gap_count=0, audio_gap_frame_count=0,
                           has_timeline_gap=self.timeline_gap)


def test_fake_recorder_covers_the_state_message_interface():
  tree = ast.parse(textwrap.dedent(inspect.getsource(CamcorderDaemon.state_message)))
  recorder_attributes = {
    node.attr for node in ast.walk(tree)
    if isinstance(node, ast.Attribute)
    and isinstance(node.value, ast.Attribute)
    and isinstance(node.value.value, ast.Name)
    and node.value.value.id == "self"
    and node.value.attr == "recorder"
  }
  assert recorder_attributes <= set(dir(FakeRecorder()))


def control(sequence, action, stream="wideRoad", request_mono_time=123):
  return SimpleNamespace(sequence=sequence, action=action, stream=stream, requestMonoTime=request_mono_time)


def test_every_capture_failure_maps_to_a_valid_notice():
  for failure in CaptureFailure:
    for clip_saved in (False, True):
      status = CaptureStatus.from_failure(failure, clip_saved, "detail")
      msg = messaging.new_message("camcorderState")
      msg.camcorderState.notice = status.notice
      assert str(msg.camcorderState.notice) == status.notice
  assert CaptureStatus.from_failure(CaptureFailure.AUDIO, False, "detail").notice == "recordingFailed"


def test_restarted_daemon_publishes_the_recovered_clip():
  daemon = CamcorderDaemon(FakeRecorder(), recovered_clip=SimpleNamespace(clip_id="recovered"))

  state = daemon.state_message().camcorderState

  assert state.clipId == "recovered"
  assert str(state.notice) == "recordingRecovered"
  assert state.sessionId == daemon.session_id > 0


def test_only_a_working_mic_is_named():
  recorder = FakeRecorder()
  daemon = CamcorderDaemon(recorder)
  assert daemon.state_message().camcorderState.micName == "test mic"

  recorder.mic_error = "Microphone disconnected"
  state = daemon.state_message().camcorderState
  assert state.micName == ""
  assert str(state.notice) == "micUnavailable"
  assert state.error == recorder.mic_error


def test_mic_disconnect_is_published_live_without_changing_latched_status():
  recorder = FakeRecorder()
  daemon = CamcorderDaemon(recorder)
  daemon.apply_control(control(1, "start"))
  recorder.mic_error = "Microphone disconnected"

  state = daemon.state_message().camcorderState

  assert str(state.notice) == "micDisconnected"
  assert state.error == recorder.mic_error
  assert daemon.notice == "none"


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


def test_saved_clip_with_a_timeline_gap_publishes_a_warning():
  recorder = FakeRecorder()
  recorder.timeline_gap = True
  daemon = CamcorderDaemon(recorder)

  daemon.apply_control(control(1, "start"))
  daemon.apply_control(control(2, "stop"))

  assert daemon.error == ""
  assert daemon.notice == "timelineGapSaved"


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
  recorder.capture_failure = "recording"

  daemon.update()

  assert recorder.stops == 1
  assert daemon.phase == "warming"
  assert daemon.clip_id == "saved-clip"
  assert daemon.error == recorder.capture_error
  assert daemon.notice == "recordingErrorSaved"

  recorder.mic_error = "Microphone disconnected"
  state = daemon.state_message().camcorderState
  assert str(state.notice) == "recordingErrorSaved"
  assert state.error == recorder.capture_error


def test_warming_error_blocks_the_mic_banner():
  recorder = FakeRecorder()
  recorder.ready = False
  daemon = CamcorderDaemon(recorder)
  daemon.apply_control(control(1, "start"))
  recorder.mic_error = "Microphone disconnected"

  state = daemon.state_message().camcorderState

  assert str(state.notice) == "none"
  assert state.error == "recorder is still warming up"


def _track_ends(stop_ns: int, last_video_ns: int) -> dict[str, int]:
  ends = {}

  class Hevc:
    def finalize(self, end_ns):
      ends["video"] = end_ns
      return MasterInfo("video.hevc", 1344, 760, 3, 1_000_000_000, last_timestamp_ns=last_video_ns)

  class Preview:
    def finalize(self, master, audio, end_ns):
      ends["preview"] = end_ns
      return "clip"

  def finish(end_ns):
    ends["audio"] = end_ns
    return "audio"

  recorder = ClipRecorder(mic=SimpleNamespace(finish=finish))
  recorder._warm = True
  recorder._recording.set()
  recorder._preview = Preview()
  recorder._hevc = Hevc()
  assert recorder.stop(stop_ns) == "clip"
  return ends


def test_every_track_ends_where_the_last_video_frame_does():
  ends = _track_ends(stop_ns=1_120_000_000, last_video_ns=1_100_000_000)
  assert ends == {"video": 1_120_000_000, "audio": 1_150_000_000, "preview": 1_150_000_000}


def test_a_stalled_encoder_does_not_cut_audio_before_the_press():
  ends = _track_ends(stop_ns=1_120_000_000, last_video_ns=900_000_000)
  assert ends == {"video": 1_120_000_000, "audio": 1_120_000_000, "preview": 1_120_000_000}


def test_finalization_salvages_other_tracks_when_one_writer_fails():
  class BrokenHevc:
    def finalize(self, end_ns):
      raise OSError("video write failed")

  class Preview:
    def finalize(self, master, audio, end_ns):
      assert master is None
      assert audio == "audio"
      assert end_ns == 123
      return "clip"

  mic = SimpleNamespace(finish=lambda end_ns: "audio")
  recorder = ClipRecorder(mic=mic)
  recorder._warm = True
  recorder._recording.set()
  recorder._preview = Preview()
  recorder._hevc = BrokenHevc()

  assert recorder.stop(123) == "clip"
  assert recorder.capture_error == "encoded video finalization failed: video write failed"
  assert not recorder.recording


def test_recorder_poll_promotes_audio_write_failures_without_a_cross_thread_callback():
  mic = SimpleNamespace(write_error="audio write failed: disk full")
  recorder = ClipRecorder(mic=mic)
  recorder._recording.set()

  recorder.poll()

  assert recorder.capture_error == mic.write_error
  assert recorder.capture_failure == "audio"

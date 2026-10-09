import ast
import inspect
import textwrap
from types import SimpleNamespace
from unittest.mock import patch

from openpilot.cereal import messaging
from openpilot.cereal.visionipc import VisionStreamType
from openpilot.system.camcorder.camcorderd import CamcorderDaemon
from openpilot.system.camcorder.capture_status import CaptureFailure, CaptureStatus
from openpilot.system.camcorder.hevc_writer import HevcWriter, MasterInfo
from openpilot.system.camcorder.recorder import ClipRecorder, RecorderState


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
    self.starts = []
    self.stops = 0
    self.timeline_gap = False
    self.video_is_ready = True

  def set_warm(self, warm):
    pass

  def update_video_health(self, stream_type):
    pass

  def video_ready(self, stream_type):
    return self.video_is_ready

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
  assert daemon.phase == "idle"
  assert daemon.clip_id == "saved-clip"

  state = daemon.state_message().camcorderState
  assert state.sequence == 2
  assert state.phase == "idle"
  assert state.clipId == "saved-clip"


def test_recorder_reports_warming_until_the_camera_video_is_arriving():
  recorder = FakeRecorder()
  recorder.video_is_ready = False
  daemon = CamcorderDaemon(recorder)

  daemon.update()
  assert str(daemon.state_message().camcorderState.phase) == "warming"

  recorder.video_is_ready = True
  daemon.update()
  assert str(daemon.state_message().camcorderState.phase) == "idle"


def test_start_without_video_is_refused_instead_of_recording_a_preview_only_take():
  recorder = FakeRecorder()
  recorder.video_is_ready = False
  daemon = CamcorderDaemon(recorder)

  daemon.apply_control(control(1, "start"))
  daemon.update()

  assert recorder.starts == []
  state = daemon.state_message().camcorderState
  assert state.sequence == 1
  assert str(state.phase) == "warming"
  assert str(state.notice) == "videoNotReady"


def test_photo_command_publishes_the_clip_without_entering_recording():
  recorder = FakeRecorder()
  daemon = CamcorderDaemon(recorder)

  with patch("openpilot.system.camcorder.camcorderd.take_photo",
             return_value=SimpleNamespace(clip_id="photo")) as take_photo:
    daemon.apply_control(control(1, "photo", "cabin"))

  take_photo.assert_called_once_with(VisionStreamType.VISION_STREAM_CABIN)
  assert daemon.clip_id == "photo"
  assert daemon.phase == "idle"
  assert daemon.notice == "none"
  assert not recorder.recording


def test_photo_is_ignored_during_a_video_take():
  recorder = FakeRecorder()
  recorder.recording = True
  daemon = CamcorderDaemon(recorder)

  with patch("openpilot.system.camcorder.camcorderd.take_photo") as take_photo:
    daemon.apply_control(control(1, "photo"))

  take_photo.assert_not_called()
  assert recorder.recording


def test_failed_photo_does_not_block_the_next_video_take():
  recorder = FakeRecorder()
  daemon = CamcorderDaemon(recorder)

  with (
    patch("openpilot.system.camcorder.camcorderd.take_photo", return_value=None),
    patch("openpilot.system.camcorder.camcorderd.cloudlog.exception"),
  ):
    daemon.apply_control(control(1, "photo"))

  assert daemon.phase == "idle"
  assert daemon.notice == "recordingFailed"

  daemon.apply_control(control(2, "start"))
  assert daemon.phase == "recording"
  assert recorder.recording


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
  assert daemon.phase == "idle"
  assert daemon.clip_id == "saved-clip"
  assert daemon.error == recorder.capture_error
  assert daemon.notice == "recordingErrorSaved"

  recorder.mic_error = "Microphone disconnected"
  state = daemon.state_message().camcorderState
  assert str(state.notice) == "recordingErrorSaved"
  assert state.error == recorder.capture_error


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
  recorder._state = RecorderState.RECORDING
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


def test_failed_video_finalization_discards_the_take_instead_of_saving_a_preview():
  class BrokenHevc:
    def finalize(self, end_ns):
      raise OSError("video write failed")

  class Preview:
    aborted = False

    def finalize(self, master, audio, end_ns):
      raise AssertionError("a take without video must not be published")

    def abort(self):
      self.aborted = True

  mic = SimpleNamespace(aborted=False)
  mic.abort = lambda: setattr(mic, "aborted", True)
  recorder = ClipRecorder(mic=mic)
  recorder._warm = True
  recorder._state = RecorderState.RECORDING
  preview = Preview()
  recorder._preview = preview
  recorder._hevc = BrokenHevc()

  with patch("openpilot.system.camcorder.recorder.cloudlog.exception"):
    assert recorder.stop(123) is None
  assert recorder.capture_error == "encoded video finalization failed: video write failed"
  assert preview.aborted and mic.aborted
  assert not recorder.recording


def test_take_whose_video_never_started_is_discarded():
  class Preview:
    aborted = False

    def abort(self):
      self.aborted = True

  mic = SimpleNamespace(aborted=False)
  mic.abort = lambda: setattr(mic, "aborted", True)
  recorder = ClipRecorder(mic=mic)
  recorder._warm = True
  recorder._state = RecorderState.RECORDING
  preview = Preview()
  recorder._preview = preview

  assert recorder.stop(123) is None
  assert recorder.capture_error == "no encoded video was recorded"
  assert preview.aborted and mic.aborted


def test_take_asks_its_encoder_for_a_keyframe_until_video_starts(tmp_path):
  sent = []
  recorder = ClipRecorder(mic=SimpleNamespace())
  recorder._keyframe_requests = SimpleNamespace(send=sent.append)

  recorder._request_keyframe("cabinEncodeData")
  assert messaging.log_from_bytes(sent[0]).encoderKeyframeRequest.encodeService == "cabinEncodeData"

  assert not recorder._video_started()
  recorder._hevc = HevcWriter(tmp_path)
  assert not recorder._video_started()
  recorder._hevc.add_packet(b"header", b"frame", keyframe=True, width=8, height=8)
  assert recorder._video_started()
  recorder._hevc.abort()


def test_recorder_poll_promotes_audio_write_failures_without_a_cross_thread_callback():
  mic = SimpleNamespace(write_error="audio write failed: disk full")
  recorder = ClipRecorder(mic=mic)
  recorder._state = RecorderState.RECORDING

  recorder.poll()

  assert recorder.capture_error == mic.write_error
  assert recorder.capture_failure == "audio"

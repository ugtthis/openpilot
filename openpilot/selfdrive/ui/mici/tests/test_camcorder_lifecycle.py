import time
from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import patch

from openpilot.cereal import log
from openpilot.cereal.visionipc import VisionStreamType
from openpilot.selfdrive.ui.mici.layouts.camcorder_style import PressTracker
from openpilot.selfdrive.ui.mici.layouts.camcorder_view import CamcorderView, format_remaining
from openpilot.selfdrive.ui.mici.layouts.main import MiciMainLayout, SwipeLeftPage, camcorder_available
from openpilot.selfdrive.ui.ui_state import device, ui_state
from openpilot.system.camcorder.recorder import ClipRecorder, RecorderState
from openpilot.system.ui.widgets import Widget


def test_remaining_time_reads_like_a_camera_counter():
  assert format_remaining(0) == "<1m left"
  assert format_remaining(59.9) == "<1m left"
  assert format_remaining(23 * 60 + 59) == "23m left"
  assert format_remaining(83 * 60) == "1h 23m left"
  assert format_remaining(10 * 3600 + 5 * 60) == "10h 05m left"


def test_camcorder_available_only_when_parked_and_state_is_known():
  known = log.PandaState.PandaType.tres
  unknown = log.PandaState.PandaType.unknown
  assert camcorder_available(is_body=False, ignition=False, panda_type=known)
  assert not camcorder_available(is_body=False, ignition=True, panda_type=known)
  assert not camcorder_available(is_body=False, ignition=False, panda_type=unknown)
  assert not camcorder_available(is_body=True, ignition=False, panda_type=known)


def test_detached_battery_pack_mode_uses_known_internal_panda():
  # Harness attachment is intentionally not part of the policy. The comma
  # four's internal Panda remains known when the device is detached from a car.
  assert camcorder_available(
    is_body=False,
    ignition=False,
    panda_type=log.PandaState.PandaType.tres,
  )


def test_swipe_left_page_selects_body_camcorder_or_onroad_without_changing_page():
  page = cast(Any, object.__new__(SwipeLeftPage))
  page._body = object()
  page._camcorder = object()
  page._onroad = object()
  known = log.PandaState.PandaType.tres

  with patch.object(ui_state, "is_body", True):
    assert page.active_view is page._body
  with (
    patch.object(ui_state, "is_body", False),
    patch.object(ui_state, "ignition", True),
    patch.object(ui_state, "panda_type", known),
  ):
    assert page.active_view is page._onroad
  with (
    patch.object(ui_state, "is_body", False),
    patch.object(ui_state, "ignition", False),
    patch.object(ui_state, "panda_type", known),
  ):
    assert page.active_view is page._camcorder


def test_swipe_left_page_forwards_scroller_input_state_to_children():
  class Child(Widget):
    def _render(self, _):
      pass

  children = [Child(), Child(), Child()]
  page = SwipeLeftPage(*cast(Any, children))

  page.set_enabled(False)
  assert all(not child.enabled for child in children)

  page.set_enabled(True)
  page.set_touch_valid_callback(lambda: False)
  assert all(not child._touch_valid() for child in children)


def test_ignition_navigation_targets_stable_swipe_left_page():
  layout = cast(Any, object.__new__(MiciMainLayout))
  layout._onboarding_window = object()
  layout._swipe_left_page = object()
  layout._camcorder_view = SimpleNamespace(on_ignition_transition=lambda: None)

  with (
    patch.object(ui_state, "ignition", True),
    patch("openpilot.selfdrive.ui.mici.layouts.main.gui_app.widget_in_stack", return_value=False),
    patch("openpilot.selfdrive.ui.mici.layouts.main.gui_app.pop_widgets_to",
          side_effect=lambda _widget, callback: callback()),
    patch.object(layout, "_scroll_to") as scroll_to,
  ):
    layout._on_ignition_changed()

  scroll_to.assert_called_once_with(layout._swipe_left_page)


def test_bookmark_callback_publishes_both_openpilot_bookmarks():
  sent = []
  layout = cast(Any, object.__new__(MiciMainLayout))
  layout._pm = SimpleNamespace(send=lambda service, msg: sent.append((service, msg)))

  with patch("openpilot.selfdrive.ui.mici.layouts.main.messaging.new_message",
             side_effect=lambda service, valid: (service, valid)):
    layout._on_bookmark_clicked()

  assert sent == [
    ("bookmarkButton", ("bookmarkButton", True)),
    ("userBookmark", ("userBookmark", True)),
  ]


def test_started_transition_keeps_delayed_navigation_fallback():
  class SubMaster:
    def __getitem__(self, service):
      assert service == "carState"
      return SimpleNamespace(standstill=True)

  layout = cast(Any, object.__new__(MiciMainLayout))
  layout._onboarding_window = object()
  layout._prev_onroad = False
  layout._prev_standstill = True
  layout._onroad_time_delay = None

  with (
    patch.object(ui_state, "started", True),
    patch.object(ui_state, "sm", SubMaster()),
    patch("openpilot.selfdrive.ui.mici.layouts.main.gui_app.widget_in_stack", return_value=False),
    patch("openpilot.selfdrive.ui.mici.layouts.main.rl.get_time", return_value=42.0),
  ):
    layout._handle_transitions()

  assert layout._onroad_time_delay == 42.0


class _FakeMic:
  def __init__(self, audio=None):
    self.audio = audio
    self.started = False
    self.stopped = False
    self.aborted = False
    self.device_name = "test mic"
    self.sample_rate = 48000
    self.channels = 2
    self.error = ""
    self.write_error = ""

  def start(self):
    self.started = True

  def stop(self):
    self.stopped = True

  def finish(self, end_ns):
    return self.audio

  def abort(self):
    self.aborted = True

  def attach(self, path, start_ns):
    pass


def test_recorder_refuses_onroad_start_before_acquiring_leases():
  mic = _FakeMic()
  recorder = ClipRecorder(lambda: not ui_state.ignition, cast(Any, mic))
  with (
    patch.object(ui_state, "ignition", True),
    patch.object(ui_state, "started", False),
    patch("openpilot.system.camcorder.recorder.acquire_encoder") as acquire_encoder,
  ):
    assert not recorder.start(VisionStreamType.VISION_STREAM_WIDE_ROAD)

  acquire_encoder.assert_not_called()
  assert not mic.started


def test_recorder_ignition_stop_releases_leases_before_waiting_for_threads():
  events = []

  class CaptureThread:
    def join(self, timeout=None):
      events.append("join")

    def is_alive(self):
      return False

  recorder = ClipRecorder()
  recorder._preview_thread = cast(Any, CaptureThread())

  with (
    patch("openpilot.system.camcorder.recorder.release_encoder",
          side_effect=lambda: events.append("encoder")),
  ):
    recorder.stop_async()
    deadline = time.monotonic() + 2.0
    while recorder.state == RecorderState.FINALIZING and time.monotonic() < deadline:
      time.sleep(0.01)

  assert recorder.state == RecorderState.IDLE
  assert events == ["encoder", "join"]


def test_recorder_normal_stop_releases_leases_after_waiting_for_threads():
  joined = False

  class CaptureThread:
    def join(self, timeout=None):
      nonlocal joined
      joined = True

    def is_alive(self):
      return False

  recorder = ClipRecorder()
  recorder._preview_thread = cast(Any, CaptureThread())
  recorder._state = RecorderState.RECORDING

  with (
    patch("openpilot.system.camcorder.recorder.release_encoder",
          side_effect=lambda: assert_joined()),
  ):
    def assert_joined():
      assert joined

    assert recorder.stop() is None


def test_ignition_stop_still_saves_the_take():
  class Writer:
    def __init__(self, result):
      self.result = result
      self.finalized_with = None

    def finalize(self, *args):
      self.finalized_with = args
      return self.result

  recorder = ClipRecorder(mic=cast(Any, _FakeMic("audio")))
  recorder._preview = cast(Any, Writer("clip"))
  recorder._hevc = cast(Any, Writer(None))
  preview = recorder._preview

  with patch("openpilot.system.camcorder.recorder.release_encoder"):
    recorder.stop_async()
    deadline = time.monotonic() + 2.0
    while recorder.state == RecorderState.FINALIZING and time.monotonic() < deadline:
      time.sleep(0.01)

  assert recorder.state == RecorderState.IDLE
  assert preview.finalized_with == (None, "audio", recorder._stop_mono_ns)


def test_async_stop_thread_failure_remains_fail_safe():
  recorder = ClipRecorder()
  with (
    patch("openpilot.system.camcorder.recorder.release_encoder") as release_encoder,
    patch("openpilot.system.camcorder.recorder.threading.Thread.start",
          side_effect=RuntimeError("thread unavailable")),
    patch("openpilot.system.camcorder.recorder.cloudlog.exception") as log_exception,
  ):
    recorder.stop_async()

  assert recorder._stop.is_set()
  assert recorder.state == RecorderState.IDLE
  release_encoder.assert_called_once()
  log_exception.assert_called_once()


class _TakeThread:
  """Looks like a running capture thread until the recorder joins it."""

  def __init__(self):
    self.alive = True

  def join(self, timeout=None):
    self.alive = False

  def is_alive(self):
    return self.alive


@contextmanager
def _recorded_leases(ignition: bool = False):
  events: list[str] = []
  recorder_module = "openpilot.system.camcorder.recorder"
  with (
    patch.object(ui_state, "ignition", ignition),
    patch(f"{recorder_module}.acquire_encoder", side_effect=lambda: events.append("acquire")),
    patch(f"{recorder_module}.release_encoder", side_effect=lambda: events.append("release")),
  ):
    yield events


def _warmable_recorder() -> ClipRecorder:
  return ClipRecorder(lambda: not ui_state.ignition, cast(Any, _FakeMic()))


WIDE = VisionStreamType.VISION_STREAM_WIDE_ROAD


def test_stop_during_warming_keeps_the_recorder_warm():
  recorder = _warmable_recorder()
  with _recorded_leases() as events:
    recorder.set_warm(True)

    assert recorder.state == RecorderState.WARMING
    assert recorder.stop() is None
    assert recorder.state == RecorderState.WARMING

  assert events == ["acquire"]


def test_double_stop_finalizes_writers_once():
  class Preview:
    calls = 0

    def finalize(self, master, audio, end_ns):
      self.calls += 1
      return "clip"

  mic = _FakeMic("audio")
  preview = Preview()
  recorder = ClipRecorder(mic=cast(Any, mic))
  recorder._preview = cast(Any, preview)
  recorder._state = RecorderState.RECORDING

  with patch("openpilot.system.camcorder.recorder.release_encoder"):
    assert recorder.stop(123) == "clip"
    assert recorder.stop(456) is None

  assert preview.calls == 1
  assert recorder.state == RecorderState.IDLE


def test_failed_join_keeps_a_live_preview_worker_in_recording_state():
  class StuckThread:
    def join(self, timeout=None):
      pass

    def is_alive(self):
      return True

  recorder = ClipRecorder(mic=cast(Any, _FakeMic()))
  recorder._preview_thread = cast(Any, StuckThread())
  recorder._state = RecorderState.RECORDING

  with patch("openpilot.system.camcorder.recorder.release_encoder"):
    assert recorder.stop(123) is None

  assert recorder.state == RecorderState.RECORDING
  assert recorder.capture_error == "capture workers did not stop"


def test_stale_teardown_aborts_instead_of_publishing_tracks():
  class Writer:
    aborted = False

    def abort(self):
      self.aborted = True

  mic = _FakeMic()
  preview, hevc = Writer(), Writer()
  recorder = ClipRecorder(mic=cast(Any, mic))
  recorder._warm = True
  recorder._state = RecorderState.WARMING
  recorder._preview = cast(Any, preview)
  recorder._hevc = cast(Any, hevc)

  with patch("openpilot.system.camcorder.recorder.release_encoder") as release:
    assert recorder._discard_stale()

  assert preview.aborted and hevc.aborted and mic.aborted
  assert recorder.state == RecorderState.WARMING
  release.assert_not_called()


def test_double_async_stop_is_single_flight():
  recorder = ClipRecorder()
  with (
    patch("openpilot.system.camcorder.recorder.release_encoder"),
    patch("openpilot.system.camcorder.recorder.threading.Thread") as thread,
  ):
    recorder.stop_async()
    recorder.stop_async()

  thread.assert_called_once()
  thread.return_value.start.assert_called_once()
  assert recorder.state == RecorderState.FINALIZING


def test_warm_camcorder_holds_leases_across_a_take():
  recorder = _warmable_recorder()
  with _recorded_leases() as events:
    recorder.set_warm(True)
    recorder.set_warm(True)
    recorder._preview_thread = cast(Any, _TakeThread())
    recorder._state = RecorderState.RECORDING
    recorder.stop()
    assert events == ["acquire"]

    recorder.set_warm(False)
  assert events == ["acquire", "release"]


def test_leaving_the_camcorder_mid_take_keeps_leases_until_stop():
  recorder = _warmable_recorder()
  with _recorded_leases() as events:
    recorder.set_warm(True)
    recorder._preview_thread = cast(Any, _TakeThread())
    recorder._state = RecorderState.RECORDING
    recorder.set_warm(False)
    assert events == ["acquire"]

    recorder.stop()
  assert events == ["acquire", "release"]


def test_camcorder_does_not_warm_up_with_ignition_on():
  recorder = _warmable_recorder()
  with _recorded_leases(ignition=True) as events:
    recorder.set_warm(True)
  assert events == []


def test_ignition_stop_releases_warm_leases():
  recorder = _warmable_recorder()
  with _recorded_leases() as events:
    recorder.set_warm(True)
    recorder.stop_async()
    deadline = time.monotonic() + 2.0
    while recorder.state == RecorderState.FINALIZING and time.monotonic() < deadline:
      time.sleep(0.01)
  assert events == ["acquire", "release"]


def _encoded(timestamp_ms: int, keyframe: bool = False):
  return SimpleNamespace(header=b"H" if keyframe else b"", data=b"%d" % timestamp_ms, width=1344, height=760,
                         idx=SimpleNamespace(flags=8 if keyframe else 0, timestampEof=timestamp_ms * 1_000_000))


def test_take_video_starts_at_its_first_keyframe():
  with TemporaryDirectory() as directory:
    recorder = ClipRecorder(mic=cast(Any, _FakeMic()))
    recorder._preview = cast(Any, SimpleNamespace(path=Path(directory)))

    recorder._write_hevc([_encoded(1000), _encoded(1050, keyframe=True), _encoded(1100)])
    assert recorder._hevc is not None
    master = recorder._hevc.finalize()

    assert master is not None
    assert (master.first_timestamp_ns, master.frame_count) == (1_050_000_000, 2)
    assert (Path(directory) / "video.hevc").read_bytes() == b"H1050" + b"1100"


def test_stop_drains_encoded_video_until_a_frame_after_the_press():
  service = "wideRoadEncodeData"
  with TemporaryDirectory() as directory:
    recorder = ClipRecorder(mic=cast(Any, _FakeMic()))
    recorder._preview = cast(Any, SimpleNamespace(path=Path(directory)))
    recorder._write_hevc([_encoded(1000, keyframe=True)])
    recorder._stop_mono_ns = 1_050_000_000
    batches = [[SimpleNamespace(wideRoadEncodeData=_encoded(1050))],
               [SimpleNamespace(wideRoadEncodeData=_encoded(1100))]]

    with patch("openpilot.cereal.messaging.drain_sock", side_effect=lambda *_args, **_kwargs: batches.pop(0)):
      recorder._drain_hevc_tail(object(), service)

    assert batches == []
    assert recorder._hevc is not None
    master = recorder._hevc.finalize(recorder._stop_mono_ns)
    assert master is not None and master.frame_count == 2


def test_camcorder_warms_up_only_while_settled_on_screen():
  warm = []
  layout = cast(Any, object.__new__(MiciMainLayout))
  layout._setup = True
  layout._rect = SimpleNamespace(x=0.0, width=536.0)
  layout._swipe_left_page = SimpleNamespace(showing_camcorder=True, rect=SimpleNamespace(x=0.0))
  layout._camcorder_view = SimpleNamespace(set_warm=warm.append)

  def tick(active=layout, awake=True):
    with (
      patch("openpilot.selfdrive.ui.mici.layouts.main.gui_app.get_active_widget", return_value=active),
      patch.object(type(device), "awake", property(lambda _self: awake)),
    ):
      layout._update_camcorder_warmup()

  tick()
  layout._swipe_left_page.rect.x = 400.0
  tick()
  layout._swipe_left_page.rect.x = 0.0
  tick(active=object())
  tick(awake=False)
  layout._swipe_left_page.showing_camcorder = False
  tick()
  assert warm == [True, False, False, False, False]


def test_ignition_transition_cancels_and_stops_capture():
  class Countdown:
    cancelled = False

    def cancel(self):
      self.cancelled = True

  class Recorder:
    recording = True
    stopped = False

    def stop(self):
      self.stopped = True

    def set_warm(self, warm, stream_type):
      assert not warm

  press = PressTracker()
  press._name = "record"
  view = SimpleNamespace(
    _snapshot_countdown=Countdown(),
    _recorder=Recorder(),
    _press=press,
    stream_type=WIDE,
  )

  with (
    patch.object(ui_state, "ignition", True),
    patch.object(device, "set_override_interactive_timeout") as clear_timeout,
  ):
    CamcorderView.on_ignition_transition(cast(Any, view))

  assert view._snapshot_countdown.cancelled
  assert view._recorder.stopped
  assert not view._press.is_down("record")
  clear_timeout.assert_called_once_with(None)

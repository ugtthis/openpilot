import time
from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import patch

from openpilot.cereal import log
from openpilot.cereal.visionipc import VisionStreamType
from openpilot.selfdrive.ui.mici.layouts.camcorder_view import CamcorderView
from openpilot.selfdrive.ui.mici.layouts.main import MiciMainLayout, SwipeLeftPage, camcorder_available
from openpilot.selfdrive.ui.ui_state import device, ui_state
from openpilot.system.camcorder.preroll import _Run
from openpilot.system.camcorder.recorder import ClipRecorder
from openpilot.system.ui.widgets import Widget


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


def test_recorder_refuses_onroad_start_before_acquiring_leases():
  recorder = ClipRecorder(lambda: not ui_state.ignition)
  with (
    patch.object(ui_state, "ignition", True),
    patch.object(ui_state, "started", False),
    patch("openpilot.system.camcorder.recorder.acquire_encoder") as acquire_encoder,
    patch("openpilot.system.camcorder.recorder.acquire_mic") as acquire_mic,
  ):
    assert not recorder.start(VisionStreamType.VISION_STREAM_WIDE_ROAD)

  acquire_encoder.assert_not_called()
  acquire_mic.assert_not_called()


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
    patch("openpilot.system.camcorder.recorder.release_mic",
          side_effect=lambda: events.append("mic")),
  ):
    recorder.stop_async()
    deadline = time.monotonic() + 2.0
    while recorder._finalizing.is_set() and time.monotonic() < deadline:
      time.sleep(0.01)

  assert not recorder._finalizing.is_set()
  assert events == ["encoder", "mic", "join"]


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

  with (
    patch("openpilot.system.camcorder.recorder.release_encoder",
          side_effect=lambda: assert_joined()),
    patch("openpilot.system.camcorder.recorder.release_mic",
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

  recorder = ClipRecorder()
  recorder._preview = cast(Any, Writer("clip"))
  recorder._hevc = cast(Any, Writer("master"))
  recorder._audio = cast(Any, Writer("audio"))
  preview = recorder._preview

  with (
    patch("openpilot.system.camcorder.recorder.release_encoder"),
    patch("openpilot.system.camcorder.recorder.release_mic"),
  ):
    recorder.stop_async()
    deadline = time.monotonic() + 2.0
    while recorder._finalizing.is_set() and time.monotonic() < deadline:
      time.sleep(0.01)

  assert not recorder._finalizing.is_set()
  assert preview.finalized_with == ("master", "audio")


def test_async_stop_thread_failure_remains_fail_safe():
  recorder = ClipRecorder()
  with (
    patch("openpilot.system.camcorder.recorder.release_encoder") as release_encoder,
    patch("openpilot.system.camcorder.recorder.release_mic") as release_mic,
    patch("openpilot.system.camcorder.recorder.threading.Thread.start",
          side_effect=RuntimeError("thread unavailable")),
    patch("openpilot.system.camcorder.recorder.cloudlog.exception") as log_exception,
  ):
    recorder.stop_async()

  assert recorder._stop.is_set()
  assert not recorder._finalizing.is_set()
  release_encoder.assert_called_once()
  release_mic.assert_called_once()
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
    patch(f"{recorder_module}.acquire_mic", side_effect=lambda: events.append("acquire mic")),
    patch(f"{recorder_module}.release_encoder", side_effect=lambda: events.append("release")),
    patch(f"{recorder_module}.release_mic", side_effect=lambda: events.append("release mic")),
  ):
    yield events


class _FakePreRoll:
  def __init__(self):
    self.service: str | None = None

  def start(self, service):
    self.service = service

  def stop(self):
    self.service = None


def _warmable_recorder() -> ClipRecorder:
  recorder = ClipRecorder(lambda: not ui_state.ignition)
  recorder._preroll = cast(Any, _FakePreRoll())
  return recorder


WIDE = VisionStreamType.VISION_STREAM_WIDE_ROAD
CABIN = VisionStreamType.VISION_STREAM_CABIN


def test_warm_camcorder_holds_leases_across_a_take():
  recorder = _warmable_recorder()
  with _recorded_leases() as events:
    recorder.set_warm(True, WIDE)
    recorder.set_warm(True, WIDE)
    recorder._preview_thread = cast(Any, _TakeThread())
    recorder.stop()
    assert events == ["acquire", "acquire mic"]

    recorder.set_warm(False, WIDE)
  assert events == ["acquire", "acquire mic", "release", "release mic"]


def test_leaving_the_camcorder_mid_take_keeps_leases_until_stop():
  recorder = _warmable_recorder()
  with _recorded_leases() as events:
    recorder.set_warm(True, WIDE)
    recorder._preview_thread = cast(Any, _TakeThread())
    recorder.set_warm(False, WIDE)
    assert events == ["acquire", "acquire mic"]

    recorder.stop()
  assert events == ["acquire", "acquire mic", "release", "release mic"]


def test_camcorder_does_not_warm_up_with_ignition_on():
  recorder = _warmable_recorder()
  with _recorded_leases(ignition=True) as events:
    recorder.set_warm(True, WIDE)
  assert events == []
  assert recorder._preroll.service is None


def test_preroll_follows_the_camera_and_pauses_for_takes():
  recorder = _warmable_recorder()
  preroll = recorder._preroll
  with _recorded_leases():
    recorder.set_warm(True, WIDE)
    assert preroll.service == "wideRoadEncodeData"
    recorder.set_warm(True, CABIN)
    assert preroll.service == "cabinEncodeData"

    preroll.stop()  # a take hands the pre-roll over at its first preview frame
    recorder._preview_thread = cast(Any, _TakeThread())
    recorder.set_warm(True, CABIN)
    assert preroll.service is None

    recorder.stop()
    recorder.set_warm(True, CABIN)
    assert preroll.service == "cabinEncodeData"
    recorder.set_warm(False, CABIN)
  assert preroll.service is None


def test_ignition_stop_releases_warm_leases():
  recorder = _warmable_recorder()
  with _recorded_leases() as events:
    recorder.set_warm(True, WIDE)
    recorder.stop_async()
    deadline = time.monotonic() + 2.0
    while recorder._finalizing.is_set() and time.monotonic() < deadline:
      time.sleep(0.01)
  assert events == ["acquire", "acquire mic", "release", "release mic"]
  assert recorder._preroll.service is None


def _encoded(timestamp_ms: int, keyframe: bool = False):
  return SimpleNamespace(header=b"H" if keyframe else b"", data=b"%d" % timestamp_ms, width=1344, height=760,
                         idx=SimpleNamespace(flags=8 if keyframe else 0, timestampEof=timestamp_ms * 1_000_000))


def _audio_message(stamp_ms: int):
  return SimpleNamespace(rawAudioData=SimpleNamespace(data=b"\1\0" * 5, sampleRate=100), logMonoTime=stamp_ms * 1_000_000)


def test_preroll_starts_at_the_keyframe_before_the_press():
  run = _Run("wideRoadEncodeData")
  for timestamp_ms in range(0, 3000, 50):
    run.add_video(_encoded(timestamp_ms, keyframe=timestamp_ms % 1000 == 0))
    run.add_audio(_audio_message(timestamp_ms + 50))

  video, audio = run.take(press_ns=2_400_000_000)

  assert video[0].keyframe and video[0].timestamp_ns == 2_000_000_000
  assert video[-1].timestamp_ns == 2_950_000_000
  assert audio[0].log_mono_ns == 2_050_000_000


def test_preroll_is_bounded():
  run = _Run("wideRoadEncodeData")
  for timestamp_ms in range(0, 10_000, 50):
    run.add_video(_encoded(timestamp_ms, keyframe=timestamp_ms % 1000 == 0))
    run.add_audio(_audio_message(timestamp_ms))

  assert len(run.gops) == 3
  assert run.gops[0][0].timestamp_ns == 7_000_000_000
  assert run.audio[0].log_mono_ns >= 4_950_000_000


def test_preroll_without_an_earlier_keyframe_starts_at_the_first_one():
  run = _Run("wideRoadEncodeData")
  run.add_video(_encoded(900))  # mid-GOP frame before any keyframe is dropped
  run.add_video(_encoded(1000, keyframe=True))

  video, _ = run.take(press_ns=500_000_000)

  assert [packet.timestamp_ns for packet in video] == [1_000_000_000]


def test_take_writes_preroll_then_skips_what_its_own_sockets_repeat():
  run = _Run("wideRoadEncodeData")
  for timestamp_ms in (1000, 1050, 1100):
    run.add_video(_encoded(timestamp_ms, keyframe=timestamp_ms == 1000))
    run.add_audio(_audio_message(timestamp_ms))

  with TemporaryDirectory() as directory:
    recorder = ClipRecorder()
    recorder._preview = cast(Any, SimpleNamespace(path=Path(directory)))
    recorder._preroll_video, recorder._preroll_audio = run.take(press_ns=1_060_000_000)

    recorder._write_hevc([_encoded(1050), _encoded(1100), _encoded(1150)])
    recorder._write_audio([_audio_message(1100), _audio_message(1150)])
    assert recorder._hevc is not None and recorder._audio is not None
    master = recorder._hevc.finalize()
    audio = recorder._audio.finalize()

    assert master is not None and audio is not None
    assert (master.first_timestamp_ns, master.frame_count) == (1_000_000_000, 4)
    assert (Path(directory) / "video.hevc").read_bytes() == b"H1000" + b"1050" + b"1100" + b"1150"
    # The packet stamped 1000 ends where the video starts, so audio begins with the next one.
    assert (audio.first_log_mono_ns, audio.frame_count) == (1_050_000_000, 15)


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


class _AudioSink:
  def __init__(self):
    self.stamps = []

  def add_packet(self, _data, _sample_rate, log_mono_ns):
    self.stamps.append(log_mono_ns)


def _audio_event(log_mono_ns):
  return SimpleNamespace(rawAudioData=SimpleNamespace(data=b"\0\0", sampleRate=10), logMonoTime=log_mono_ns)


def test_stop_keeps_audio_up_to_the_stop_press():
  recorder = ClipRecorder()
  sink = _AudioSink()
  recorder._preview = cast(Any, SimpleNamespace(path=None))
  recorder._audio = cast(Any, sink)
  recorder._stop_mono_ns = 1_000
  batches = [[_audio_event(800), _audio_event(900)], [], [_audio_event(1_050)], [_audio_event(1_100)]]

  with patch("openpilot.cereal.messaging.drain_sock", side_effect=lambda *_args, **_kwargs: batches.pop(0)):
    recorder._drain_audio_tail(object())

  assert sink.stamps == [800, 900, 1_050]


def test_audio_tail_drain_gives_up_when_micd_is_gone():
  recorder = ClipRecorder()
  recorder._preview = cast(Any, SimpleNamespace(path=None))
  recorder._audio = cast(Any, _AudioSink())
  recorder._stop_mono_ns = time.monotonic_ns()

  started = time.monotonic()
  with patch("openpilot.cereal.messaging.drain_sock", return_value=[]):
    recorder._drain_audio_tail(object())

  assert time.monotonic() - started < 0.5


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

  view = SimpleNamespace(
    _snapshot_countdown=Countdown(),
    _recorder=Recorder(),
    _pressed="record",
    stream_type=WIDE,
  )

  with (
    patch.object(ui_state, "ignition", True),
    patch.object(device, "set_override_interactive_timeout") as clear_timeout,
  ):
    CamcorderView.on_ignition_transition(cast(Any, view))

  assert view._snapshot_countdown.cancelled
  assert view._recorder.stopped
  assert view._pressed is None
  clear_timeout.assert_called_once_with(None)

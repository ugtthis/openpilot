import time
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import patch

from openpilot.cereal import log
from openpilot.cereal.visionipc import VisionStreamType
from openpilot.selfdrive.ui.mici.layouts.camcorder_recorder import ClipRecorder
from openpilot.selfdrive.ui.mici.layouts.camcorder_view import CamcorderView
from openpilot.selfdrive.ui.mici.layouts.main import MiciMainLayout, SwipeLeftPage, camcorder_available
from openpilot.selfdrive.ui.ui_state import device, ui_state
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
  recorder = ClipRecorder()
  with (
    patch.object(ui_state, "ignition", True),
    patch.object(ui_state, "started", False),
    patch("openpilot.selfdrive.ui.mici.layouts.camcorder_recorder.acquire_encoder") as acquire_encoder,
    patch("openpilot.selfdrive.ui.mici.layouts.camcorder_recorder.acquire_mic") as acquire_mic,
  ):
    assert not recorder.start(VisionStreamType.VISION_STREAM_WIDE_ROAD)

  acquire_encoder.assert_not_called()
  acquire_mic.assert_not_called()


def test_recorder_ignition_abort_releases_leases_before_waiting_for_threads():
  released = {"encoder": False, "mic": False}

  class CaptureThread:
    def join(self, timeout=None):
      assert released == {"encoder": True, "mic": True}

    def is_alive(self):
      return False

  recorder = ClipRecorder()
  recorder._preview_thread = cast(Any, CaptureThread())

  with (
    patch("openpilot.selfdrive.ui.mici.layouts.camcorder_recorder.release_encoder",
          side_effect=lambda: released.__setitem__("encoder", True)),
    patch("openpilot.selfdrive.ui.mici.layouts.camcorder_recorder.release_mic",
          side_effect=lambda: released.__setitem__("mic", True)),
  ):
    assert recorder.stop(release_first=True) is None

  assert released == {"encoder": True, "mic": True}


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
    patch("openpilot.selfdrive.ui.mici.layouts.camcorder_recorder.release_encoder",
          side_effect=lambda: assert_joined()),
    patch("openpilot.selfdrive.ui.mici.layouts.camcorder_recorder.release_mic",
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
    patch("openpilot.selfdrive.ui.mici.layouts.camcorder_recorder.release_encoder"),
    patch("openpilot.selfdrive.ui.mici.layouts.camcorder_recorder.release_mic"),
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
    patch("openpilot.selfdrive.ui.mici.layouts.camcorder_recorder.release_encoder") as release_encoder,
    patch("openpilot.selfdrive.ui.mici.layouts.camcorder_recorder.release_mic") as release_mic,
    patch("openpilot.selfdrive.ui.mici.layouts.camcorder_recorder.threading.Thread.start",
          side_effect=RuntimeError("thread unavailable")),
    patch("openpilot.selfdrive.ui.mici.layouts.camcorder_recorder.cloudlog.exception") as log_exception,
  ):
    recorder.stop_async()

  assert recorder._stop.is_set()
  assert not recorder._finalizing.is_set()
  release_encoder.assert_called_once()
  release_mic.assert_called_once()
  log_exception.assert_called_once()


def test_ignition_transition_cancels_and_stops_capture():
  class Countdown:
    cancelled = False

    def cancel(self):
      self.cancelled = True

  class Recorder:
    recording = True
    stopped = False

    def stop_async(self):
      self.stopped = True

  view = SimpleNamespace(
    _snapshot_countdown=Countdown(),
    _recorder=Recorder(),
    _pressed="record",
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

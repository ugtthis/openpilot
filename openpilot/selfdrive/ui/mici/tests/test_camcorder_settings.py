from typing import Any, cast
from unittest.mock import patch

import pyray as rl
import pytest

from openpilot.common.test import OpenpilotTestCase
from openpilot.selfdrive.ui.mici.layouts.camcorder_settings import (
  PULL_BLOCK_PX, PULL_COMMIT_PX, PULL_LOCK_PX, SettingsSheet, VerticalPull,
)
from openpilot.selfdrive.ui.mici.layouts.camcorder_style import PressTracker
from openpilot.selfdrive.ui.mici.layouts.camcorder_view import CamcorderView, ModePullGesture
from openpilot.system.camcorder.settings import CamcorderSettings, Quality
from openpilot.system.ui.lib.application import MouseEvent, MousePos, gui_app

SCREEN = rl.Rectangle(0, 0, 536, 240)
SLOW_STEP_S = 0.5  # slow enough that only distance decides
FAST_STEP_S = 0.01


@pytest.fixture(autouse=True)
def _no_fonts():
  with patch.object(gui_app, "font"):
    yield


def _touch(press, move, release, points: list[tuple[float, float]], step_s: float) -> bool:
  """Press at the first point, move through the rest one step apart, and lift one step after the last."""
  press(MousePos(*points[0]), 0.0)
  for i, (x, y) in enumerate(points[1:], start=1):
    move(MousePos(x, y), i * step_s)
  return release(MousePos(*points[-1]), len(points) * step_s)


def _pull(pull: VerticalPull, points: list[tuple[float, float]], step_s: float = SLOW_STEP_S) -> bool:
  return _touch(pull.press, pull.move, pull.release, points, step_s)


def _drag(sheet: SettingsSheet, points: list[tuple[float, float]], can_open: bool = True,
          step_s: float = SLOW_STEP_S) -> bool:
  return _touch(lambda pos, t: sheet.press(pos, t, can_open), sheet.move, sheet.release, points, step_s)


def _settle(sheet: SettingsSheet) -> None:
  for _ in range(100):
    sheet.update(SCREEN)


def _segment(sheet: SettingsSheet, name: str) -> MousePos:
  rect = dict(sheet._controls())[name]
  return MousePos(rect.x + rect.width / 2, rect.y + rect.height / 2)


def _open_sheet() -> SettingsSheet:
  sheet = SettingsSheet()
  sheet.update(SCREEN)
  assert _drag(sheet, [(268, 20), (268, 200)])
  _settle(sheet)
  assert sheet.is_open
  return sheet


class TestVerticalPull(OpenpilotTestCase):
  def test_small_jitter_is_still_a_tap(self):
    assert not _pull(VerticalPull(1), [(100, 100), (103, 100 + PULL_LOCK_PX - 1)])

  def test_sideways_swipe_is_left_to_the_page_scroller(self):
    pull = VerticalPull(1)
    pull.press(MousePos(100, 100), 0.0)
    pull.move(MousePos(100 + PULL_BLOCK_PX + 1, 110), 0.1)
    pull.move(MousePos(100 + PULL_BLOCK_PX + 1, 300), 0.2)
    assert not pull.claimed

  def test_sideways_drift_after_locking_in_is_ignored(self):
    assert _pull(VerticalPull(1), [(100, 0), (100, PULL_LOCK_PX + 4), (250, PULL_COMMIT_PX + 10)])

  def test_only_claims_its_own_direction(self):
    pull = VerticalPull(-1)
    pull.press(MousePos(100, 100), 0.0)
    pull.move(MousePos(100, 200), 0.1)
    assert not pull.claimed
    pull.move(MousePos(100, 100 - PULL_LOCK_PX), 0.2)
    assert pull.claimed

  def test_slow_release_commits_only_past_the_distance(self):
    assert not _pull(VerticalPull(1), [(100, 0), (100, PULL_COMMIT_PX - 10)])
    assert _pull(VerticalPull(1), [(100, 0), (100, PULL_COMMIT_PX)])

  def test_short_flick_commits(self):
    assert _pull(VerticalPull(-1), [(100, 200), (100, 180), (100, 170), (100, 160), (100, 150)], FAST_STEP_S)

  def test_pulling_back_before_release_cancels(self):
    pull = VerticalPull(1)
    pull.press(MousePos(100, 0), 0.0)
    pull.move(MousePos(100, 100), 1.0)
    for i, y in enumerate((90, 80, 70)):
      pull.move(MousePos(100, y), 1.0 + (i + 1) * FAST_STEP_S)
    assert not pull.release(MousePos(100, 70), 1.0 + 4 * FAST_STEP_S)


class TestSettingsSheet(OpenpilotTestCase):
  def test_pull_down_opens_and_consumes_the_touch(self):
    _open_sheet()

  def test_short_slow_pull_springs_back_closed(self):
    sheet = SettingsSheet()
    sheet.update(SCREEN)
    assert _drag(sheet, [(268, 20), (268, 50)])
    assert not sheet.is_open
    _settle(sheet)
    assert not sheet.visible

  def test_tap_is_not_consumed_while_closed(self):
    sheet = SettingsSheet()
    sheet.update(SCREEN)
    assert not _drag(sheet, [(268, 120)])
    assert not sheet.owns_touch

  def test_cannot_open_when_disallowed(self):
    sheet = SettingsSheet()
    sheet.update(SCREEN)
    assert not _drag(sheet, [(268, 20), (268, 200)], can_open=False)
    assert not sheet.is_open

  def test_tapping_an_option_saves_it(self):
    sheet = _open_sheet()
    assert _drag(sheet, [_segment(sheet, "quality=max")])
    assert _drag(sheet, [_segment(sheet, "frame_rate=30")])
    assert sheet.is_open
    assert CamcorderSettings.load() == CamcorderSettings(Quality.MAX, 30)

  def test_dragging_off_an_option_does_not_select_it(self):
    sheet = _open_sheet()
    start = _segment(sheet, "quality=max")
    assert _drag(sheet, [start, (start.x + 200, start.y)])
    assert sheet.settings.quality == Quality.STOCK

  def test_swipe_up_closes_without_selecting(self):
    sheet = _open_sheet()
    start = _segment(sheet, "quality=max")
    assert _drag(sheet, [start, (start.x, start.y - PULL_COMMIT_PX)])
    assert not sheet.is_open
    assert sheet.settings.quality == Quality.STOCK

  def test_flick_up_closes(self):
    sheet = _open_sheet()
    assert _drag(sheet, [(268, 200), (268, 185), (268, 170)], step_s=FAST_STEP_S)
    assert not sheet.is_open

  def test_abandoned_pull_does_not_leave_the_sheet_hanging(self):
    sheet = SettingsSheet()
    sheet.update(SCREEN)
    sheet.press(MousePos(268, 20), 0.0, can_open=True)
    sheet.move(MousePos(268, 150), 0.5)
    assert sheet.visible
    sheet.cancel_touch()
    _settle(sheet)
    assert not sheet.visible


class FakeRecorder:
  recording = False
  error = None


class FakeCountdown:
  def active(self, now):
    return False


class _InputOnlyCamcorderView(CamcorderView):
  def close(self):
    pass


def _camcorder_view(recording: bool = False) -> Any:
  view = cast(Any, object.__new__(_InputOnlyCamcorderView))
  view._recorder = FakeRecorder()
  view._recorder.recording = recording
  view._snapshot_countdown = FakeCountdown()
  view._mode_pull = ModePullGesture()
  view._press = PressTracker()
  view._settings = SettingsSheet()
  view._settings.update(SCREEN)
  view._feed = rl.Rectangle(0, 0, 400, 240)
  view._record_slot = rl.Rectangle(400, 120, 136, 120)
  view._playback_slot = rl.Rectangle(400, 0, 136, 120)
  view._error_dismiss = rl.Rectangle()
  view.switched = 0
  view._switch_camera = lambda: setattr(view, "switched", view.switched + 1)
  return view


def _event(x, y, t, pressed=False, released=False):
  return MouseEvent(MousePos(x, y), 0, pressed, released, not released, t)


def _touch_view(view, points: list[tuple[float, float]]):
  CamcorderView._handle_mouse_press(view, MousePos(*points[0]))
  CamcorderView._handle_mouse_event(view, _event(*points[0], 0.0, pressed=True))
  for i, (x, y) in enumerate(points[1:], start=1):
    CamcorderView._handle_mouse_event(view, _event(x, y, i * SLOW_STEP_S))
  CamcorderView._handle_mouse_event(view, _event(*points[-1], len(points) * SLOW_STEP_S, released=True))
  CamcorderView._handle_mouse_release(view, MousePos(*points[-1]))


class TestCamcorderViewSettingsPull(OpenpilotTestCase):
  def test_tapping_the_feed_still_flips_the_camera(self):
    view = _camcorder_view()
    _touch_view(view, [(200, 120)])
    assert view.switched == 1
    assert not view._settings.is_open

  def test_pulling_down_on_the_feed_opens_settings_without_flipping(self):
    view = _camcorder_view()
    _touch_view(view, [(200, 20), (200, 60), (200, 200)])
    assert view._settings.is_open
    assert view.switched == 0

  def test_page_scroller_stands_down_once_a_pull_locks_in(self):
    view = _camcorder_view()
    CamcorderView._handle_mouse_press(view, MousePos(200, 20))
    CamcorderView._handle_mouse_event(view, _event(200, 20, 0.0, pressed=True))
    assert not view.settings_active
    CamcorderView._handle_mouse_event(view, _event(200, 20 + PULL_LOCK_PX, 0.1))
    assert view.settings_active

  def test_cannot_open_settings_while_recording(self):
    view = _camcorder_view(recording=True)
    _touch_view(view, [(200, 20), (200, 60), (200, 200)])
    assert not view._settings.is_open

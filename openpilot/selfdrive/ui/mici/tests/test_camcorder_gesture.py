from unittest.mock import patch

import pyray as rl

from openpilot.common.test import OpenpilotTestCase
from openpilot.selfdrive.ui.mici.layouts.camcorder_view import (
  MODE_PULL_COMMIT_PX, MODE_PULL_START_PX, SNAPSHOT_COUNTDOWN_S,
  ModePullGesture, SnapshotCountdown, _SnapshotCountdownRing,
)


class TestModePullGesture(OpenpilotTestCase):
  def test_release_commits_and_holds_completed_indicator(self):
    gesture = ModePullGesture()

    assert not gesture.update(MODE_PULL_COMMIT_PX, dragging=True, now=1.0)
    assert gesture.progress == 1.0
    assert gesture.bounce_started == 1.0

    assert gesture.update(MODE_PULL_COMMIT_PX - 10, dragging=False, now=1.1)
    assert gesture.progress == 1.0
    assert not gesture.update(MODE_PULL_START_PX, dragging=False, now=1.2)
    assert gesture.progress == 0.0

  def test_backing_off_disarms_mode_change(self):
    gesture = ModePullGesture()

    assert not gesture.update(MODE_PULL_COMMIT_PX, dragging=True, now=1.0)
    assert not gesture.update(MODE_PULL_START_PX + 50, dragging=True, now=1.1)
    assert not gesture.update(MODE_PULL_START_PX + 50, dragging=False, now=1.2)


class TestSnapshotCountdown(OpenpilotTestCase):
  def test_counts_down_then_fires(self):
    timer = SnapshotCountdown()
    timer.start(0.0)

    assert timer.active(0.0)
    assert timer.digit(0.0) == 3
    assert timer.digit(0.99) == 3
    assert timer.digit(1.0) == 2
    assert timer.digit(2.0) == 1
    assert timer.digit(2.99) == 1
    assert not timer.tick(2.99)
    assert timer.tick(SNAPSHOT_COUNTDOWN_S)
    assert not timer.active(SNAPSHOT_COUNTDOWN_S)
    assert timer.digit(SNAPSHOT_COUNTDOWN_S) is None
    assert not timer.tick(SNAPSHOT_COUNTDOWN_S + 1)

  def test_cancel_prevents_fire(self):
    timer = SnapshotCountdown()
    timer.start(0.0)
    timer.cancel()
    assert not timer.active(1.0)
    assert timer.digit(1.0) is None
    assert not timer.tick(SNAPSHOT_COUNTDOWN_S)


class TestSnapshotCountdownRing(OpenpilotTestCase):
  def test_shader_failure_is_nonfatal(self):
    ring = _SnapshotCountdownRing()
    with (
      patch.object(rl, "load_shader_from_memory", side_effect=TypeError) as load_shader,
      patch("openpilot.selfdrive.ui.mici.layouts.camcorder_view.cloudlog.exception"),
    ):
      ring.draw(rl.Vector2(), 48, 9, 1.0)
      ring.draw(rl.Vector2(), 48, 9, 1.0)
    load_shader.assert_called_once()

from openpilot.common.test import OpenpilotTestCase
from openpilot.selfdrive.ui.mici.layouts.camcorder_view import (
  MODE_PULL_COMMIT_PX, MODE_PULL_START_PX, ModePullGesture,
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

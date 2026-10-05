import pyray as rl

from openpilot.selfdrive.ui.mici.layouts.camcorder_style import PressTracker

A = ("a", rl.Rectangle(0, 0, 20, 20))
B = ("b", rl.Rectangle(30, 0, 20, 20))


def point(x: float, y: float = 10) -> rl.Vector2:
  return rl.Vector2(x, y)


def test_release_dispatches_only_the_control_pressed():
  press = PressTracker()
  press.press(point(10), [A, B])
  assert press.is_down("a")
  assert press.release(point(40), [A, B]) is None
  assert not press.is_down("a")

  press.press(point(10), [A, B])
  assert press.release(point(10), [A, B]) == "a"


def test_misses_and_clear_never_dispatch():
  press = PressTracker()
  press.press(point(60), [A])
  assert press.release(point(10), [A]) is None

  press.press(point(10), [A])
  assert press.release(point(60), [A]) is None

  press.press(point(10), [A])
  press.clear()
  assert not press.is_down("a")
  assert press.release(point(10), [A]) is None


def test_first_overlapping_control_wins():
  press = PressTracker()
  controls = [
    ("delete", rl.Rectangle(10, 10, 20, 20)),
    ("open", rl.Rectangle(0, 0, 50, 50)),
  ]

  press.press(point(15, 15), controls)

  assert press.is_down("delete")
  assert press.release(point(15, 15), controls) == "delete"


def test_second_press_replaces_the_first():
  press = PressTracker()
  press.press(point(10), [A, B])
  press.press(point(40), [A, B])

  assert not press.is_down("a")
  assert press.is_down("b")
  assert press.release(point(40), [A, B]) == "b"


def test_empty_controls_never_track_a_press():
  press = PressTracker()
  press.press(point(10), [])

  assert not press.is_down("a")
  assert press.release(point(10), [A]) is None

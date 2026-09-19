import pyray as rl

BODY_COLOR = rl.Color(24, 26, 27, 255)
VIEWFINDER_PANEL_COLOR = rl.Color(36, 35, 33, 255)
VIEWFINDER_OUTER_BEZEL_COLOR = rl.Color(52, 47, 39, 255)
VIEWFINDER_UPPER_EDGE_COLOR = rl.Color(18, 16, 13, 255)
VIEWFINDER_LOWER_EDGE_COLOR = rl.Color(82, 74, 58, 255)
VIEWFINDER_INNER_BEZEL_COLOR = rl.Color(9, 8, 7, 255)
DIVIDER_COLOR = rl.Color(8, 8, 7, 255)
TEXT_COLOR = rl.Color(207, 202, 187, 255)
OSD_COLOR = rl.Color(198, 194, 181, 255)
OSD_BACKGROUND = rl.Color(21, 22, 21, 235)
RECORD_COLOR = rl.Color(160, 43, 38, 255)

BUTTON_FACE_COLOR = rl.Color(48, 49, 47, 255)
BUTTON_FACE_PRESSED_COLOR = rl.Color(27, 28, 27, 255)


def inset(rec: rl.Rectangle, amount: float) -> rl.Rectangle:
  return rl.Rectangle(rec.x + amount, rec.y + amount,
                      rec.width - amount * 2, rec.height - amount * 2)


def expand(rec: rl.Rectangle, amount: float) -> rl.Rectangle:
  return inset(rec, -amount)


def offset(rec: rl.Rectangle, x: float, y: float) -> rl.Rectangle:
  return rl.Rectangle(rec.x + x, rec.y + y, rec.width, rec.height)


def draw_physical_button(slot: rl.Rectangle, pressed: bool) -> rl.Rectangle:
  """Draw a fixed, upper-left-lit button and return its icon/label face."""
  size = min(slot.width, slot.height)
  margin = max(4, size * 0.10)
  rim_inset = max(2, size * 0.025)
  face_inset = max(3, size * (0.067 if pressed else 0.05))

  well = inset(slot, margin)
  rl.draw_rectangle_rounded(well, 0.16, 8, rl.Color(7, 7, 7, 255))

  rim = inset(well, rim_inset)
  if pressed:
    lower_right_color = rl.Color(71, 70, 65, 255)
    upper_left_color = rl.Color(12, 12, 11, 255)
  else:
    lower_right_color = rl.Color(18, 18, 17, 255)
    upper_left_color = rl.Color(82, 82, 77, 255)
  rl.draw_rectangle_rounded(offset(rim, 1, 1), 0.15, 8, lower_right_color)
  rl.draw_rectangle_rounded(offset(rim, -1, -1), 0.15, 8, upper_left_color)

  face = inset(well, face_inset)
  face_color = BUTTON_FACE_PRESSED_COLOR if pressed else BUTTON_FACE_COLOR
  outline_color = rl.Color(11, 11, 10, 255) if pressed else rl.Color(38, 39, 37, 255)
  rl.draw_rectangle_rounded(face, 0.14, 8, face_color)
  rl.draw_rectangle_rounded_lines_ex(face, 0.14, 8, 1, outline_color)
  return face

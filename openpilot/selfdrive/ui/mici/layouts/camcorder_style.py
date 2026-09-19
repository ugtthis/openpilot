import pyray as rl

BODY_COLOR = rl.Color(24, 24, 24, 255)
VIEWFINDER_PANEL_COLOR = rl.Color(36, 36, 36, 255)
VIEWFINDER_OUTER_BEZEL_COLOR = rl.Color(45, 45, 45, 255)
VIEWFINDER_UPPER_EDGE_COLOR = rl.Color(16, 16, 16, 255)
VIEWFINDER_INNER_BEZEL_COLOR = rl.Color(8, 8, 8, 255)
VIEWFINDER_SCREEN_WELL_COLOR = rl.Color(3, 3, 3, 255)
DIVIDER_COLOR = rl.Color(8, 8, 8, 255)

VIEWFINDER_EDGE_LIGHT_COLOR = rl.Color(81, 81, 81, 255)
BUTTON_EDGE_LIGHT_COLOR = rl.Color(69, 69, 69, 255)
BEVEL_WIDTH = 2
TEXT_COLOR = rl.Color(207, 202, 187, 255)
OSD_COLOR = rl.Color(198, 194, 181, 255)
OSD_BACKGROUND = rl.Color(21, 22, 21, 235)
RECORD_COLOR = rl.Color(160, 43, 38, 255)

BUTTON_WELL_COLOR = rl.Color(16, 16, 16, 255)
BUTTON_FACE_COLOR = rl.Color(36, 36, 36, 255)
BUTTON_FACE_PRESSED_COLOR = rl.Color(28, 28, 28, 255)
BUTTON_OUTLINE_COLOR = rl.Color(32, 32, 32, 255)
BUTTON_OUTLINE_PRESSED_COLOR = rl.Color(20, 20, 20, 255)


def inset(rec: rl.Rectangle, amount: float) -> rl.Rectangle:
  return rl.Rectangle(rec.x + amount, rec.y + amount,
                      rec.width - amount * 2, rec.height - amount * 2)


def expand(rec: rl.Rectangle, amount: float) -> rl.Rectangle:
  return inset(rec, -amount)


def offset(rec: rl.Rectangle, x: float, y: float) -> rl.Rectangle:
  return rl.Rectangle(rec.x + x, rec.y + y, rec.width, rec.height)


def _draw_directional_bevel(rim: rl.Rectangle, fill_color: rl.Color, pressed: bool):
  if pressed:
    fill = rl.Rectangle(rim.x, rim.y,
                        rim.width - BEVEL_WIDTH, rim.height - BEVEL_WIDTH)
  else:
    fill = rl.Rectangle(rim.x + BEVEL_WIDTH, rim.y + BEVEL_WIDTH,
                        rim.width - BEVEL_WIDTH, rim.height - BEVEL_WIDTH)

  rl.draw_rectangle_rounded(rim, 0.15, 8, BUTTON_EDGE_LIGHT_COLOR)
  rl.draw_rectangle_rounded(fill, 0.15, 8, fill_color)


def draw_physical_button(slot: rl.Rectangle, pressed: bool) -> rl.Rectangle:
  """Draw a raised (upper-left lit) or recessed (lower-right lit) button, returning its icon face."""
  size = min(slot.width, slot.height)
  margin = max(4, round(size * 0.10))
  rim_inset = max(BEVEL_WIDTH, round(size * 0.025))
  face_inset = max(3, round(size * (0.067 if pressed else 0.05)))

  well = inset(slot, margin)
  rl.draw_rectangle_rounded(well, 0.16, 8, BUTTON_WELL_COLOR)

  rim = inset(well, rim_inset)
  rim_color = BUTTON_WELL_COLOR if pressed else BUTTON_FACE_COLOR
  _draw_directional_bevel(rim, rim_color, pressed)

  face = inset(well, face_inset)
  face_color = BUTTON_FACE_PRESSED_COLOR if pressed else BUTTON_FACE_COLOR
  outline_color = BUTTON_OUTLINE_PRESSED_COLOR if pressed else BUTTON_OUTLINE_COLOR
  rl.draw_rectangle_rounded(face, 0.14, 8, face_color)
  rl.draw_rectangle_rounded_lines_ex(face, 0.14, 8, 1, outline_color)
  return face

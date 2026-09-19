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

FEED_ASPECT = 4 / 3
VIEWFINDER_MARGIN = 18
VIEWFINDER_OUTER_BEZEL_WIDTH = 12
VIEWFINDER_INNER_BEZEL_WIDTH = 7
VIEWFINDER_SCREEN_LIP = 2

TRASH_ICON = "icons_mici/settings/network/new/trash.png"


def _inset(rec: rl.Rectangle, amount: float) -> rl.Rectangle:
  return rl.Rectangle(rec.x + amount, rec.y + amount,
                      rec.width - amount * 2, rec.height - amount * 2)


def _expand(rec: rl.Rectangle, amount: float) -> rl.Rectangle:
  return _inset(rec, -amount)


def _offset(rec: rl.Rectangle, x: float, y: float) -> rl.Rectangle:
  return rl.Rectangle(rec.x + x, rec.y + y, rec.width, rec.height)


def _draw_directional_bevel(rim: rl.Rectangle, fill_color: rl.Color, pressed: bool):
  if pressed:
    fill = rl.Rectangle(rim.x, rim.y, rim.width - BEVEL_WIDTH, rim.height - BEVEL_WIDTH)
  else:
    fill = rl.Rectangle(rim.x + BEVEL_WIDTH, rim.y + BEVEL_WIDTH,
                        rim.width - BEVEL_WIDTH, rim.height - BEVEL_WIDTH)
  rl.draw_rectangle_rounded(rim, 0.15, 8, BUTTON_EDGE_LIGHT_COLOR)
  rl.draw_rectangle_rounded(fill, 0.15, 8, fill_color)


def fit_inside(rect: rl.Rectangle, aspect: float) -> rl.Rectangle:
  if rect.width / rect.height > aspect:
    width, height = rect.height * aspect, rect.height
  else:
    width, height = rect.width, rect.width / aspect
  return rl.Rectangle(rect.x + (rect.width - width) / 2,
                      rect.y + (rect.height - height) / 2, width, height)


def camera_body(rect: rl.Rectangle, aspect: float = FEED_ASPECT) -> tuple[rl.Rectangle, rl.Rectangle, rl.Rectangle]:
  pane_w = min(rect.width, rect.height * aspect)
  pane = rl.Rectangle(rect.x + rect.width - pane_w, rect.y, pane_w, rect.height)
  rail = rl.Rectangle(rect.x, rect.y, pane.x - rect.x, rect.height)
  feed = fit_inside(_inset(pane, VIEWFINDER_MARGIN), aspect)
  return rail, pane, feed


def split_rail(rail: rl.Rectangle, count: int) -> list[rl.Rectangle]:
  h = rail.height / count
  return [rl.Rectangle(rail.x, rail.y + i * h, rail.width, h) for i in range(count)]


def draw_rail(rail: rl.Rectangle, slots: list[rl.Rectangle]):
  rl.draw_rectangle_rec(rail, BODY_COLOR)
  for slot in slots[1:]:
    rl.draw_line_ex(rl.Vector2(rail.x, slot.y), rl.Vector2(rail.x + rail.width, slot.y), 2, DIVIDER_COLOR)


def draw_recessed_viewfinder(pane: rl.Rectangle, feed: rl.Rectangle):
  rl.draw_rectangle_rec(pane, VIEWFINDER_PANEL_COLOR)
  outer_bezel = _expand(feed, VIEWFINDER_OUTER_BEZEL_WIDTH)
  inner_bezel = _expand(feed, VIEWFINDER_INNER_BEZEL_WIDTH)
  rl.draw_rectangle_rounded(_offset(outer_bezel, BEVEL_WIDTH, BEVEL_WIDTH),
                           0.04, 6, VIEWFINDER_EDGE_LIGHT_COLOR)
  rl.draw_rectangle_rounded(_offset(outer_bezel, -BEVEL_WIDTH, -BEVEL_WIDTH),
                           0.04, 6, VIEWFINDER_UPPER_EDGE_COLOR)
  rl.draw_rectangle_rounded(outer_bezel, 0.04, 6, VIEWFINDER_OUTER_BEZEL_COLOR)
  rl.draw_rectangle_rounded(inner_bezel, 0.04, 6, VIEWFINDER_INNER_BEZEL_COLOR)
  rl.draw_rectangle_rounded(_expand(feed, VIEWFINDER_SCREEN_LIP), 0.02, 6, VIEWFINDER_SCREEN_WELL_COLOR)


def draw_physical_button(slot: rl.Rectangle, pressed: bool) -> rl.Rectangle:
  size = min(slot.width, slot.height)
  well = _inset(slot, max(4, round(size * 0.10)))
  rl.draw_rectangle_rounded(well, 0.16, 8, BUTTON_WELL_COLOR)
  _draw_directional_bevel(_inset(well, max(BEVEL_WIDTH, round(size * 0.025))),
                          BUTTON_WELL_COLOR if pressed else BUTTON_FACE_COLOR, pressed)
  face = _inset(well, max(3, round(size * (0.067 if pressed else 0.05))))
  rl.draw_rectangle_rounded(face, 0.14, 8, BUTTON_FACE_PRESSED_COLOR if pressed else BUTTON_FACE_COLOR)
  rl.draw_rectangle_rounded_lines_ex(face, 0.14, 8, 1,
                                    BUTTON_OUTLINE_PRESSED_COLOR if pressed else BUTTON_OUTLINE_COLOR)
  return face


def draw_centered_texture(rec: rl.Rectangle, tex: rl.Texture, color: rl.Color = rl.WHITE):
  x = round(rec.x + (rec.width - tex.width) / 2)
  y = round(rec.y + (rec.height - tex.height) / 2)
  rl.draw_texture_ex(tex, (x, y), 0, 1.0, color)


def hit_name(pos, named: list[tuple[str, rl.Rectangle]]) -> str | None:
  for name, rec in named:
    if rl.check_collision_point_rec(pos, rec):
      return name
  return None

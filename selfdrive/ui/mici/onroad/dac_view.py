import pyray as rl

from openpilot.selfdrive.ui.ui_state import ui_state, UIStatus
from openpilot.system.ui.widgets import Widget


# DAC view baseline: solid background plus a tici-style status border.
#
# Keep this file intentionally tiny while we rebuild the DAC UI from scratch.

_DAC_BG_COLOR = rl.BLACK
_PLACEHOLDER_TILE_COLOR = rl.Color(210, 210, 210, 255)

_BORDER_SIZE = 10
_BORDER_ROUNDNESS = 0.05
_BORDER_SEGMENTS = 24

_CONTENT_INSET = 8
_TILE_GAP = 16
_TILE_ROUNDNESS = 0.06
_TILE_SEGMENTS = 12

_BORDER_COLORS = {
  UIStatus.DISENGAGED: rl.Color(0x12, 0x28, 0x39, 0xFF),
  UIStatus.OVERRIDE: rl.Color(0x89, 0x92, 0x8D, 0xFF),
  UIStatus.ENGAGED: rl.Color(0x16, 0x7F, 0x40, 0xFF),
}


class DACView(Widget):
  def _render(self, rect: rl.Rectangle) -> None:
    rl.draw_rectangle_rec(rect, _DAC_BG_COLOR)
    self._draw_placeholder_tiles(self._content_rect(rect))

  def _content_rect(self, rect: rl.Rectangle) -> rl.Rectangle:
    inset = _BORDER_SIZE + _CONTENT_INSET
    return rl.Rectangle(
      rect.x + inset,
      rect.y + inset,
      rect.width - 2 * inset,
      rect.height - 2 * inset,
    )

  def _draw_placeholder_tiles(self, rect: rl.Rectangle) -> None:
    gap = _TILE_GAP

    left_group_w = rect.width * 0.42
    right_group_w = rect.width - left_group_w - gap

    tall_tile_w = (left_group_w - 2 * gap) / 3
    tall_tile_h = rect.height

    top_right_h = rect.height * 0.34
    bottom_right_h = rect.height - top_right_h - gap
    bottom_tile_w = (right_group_w - gap) / 2

    tile_rects = (
      rl.Rectangle(rect.x, rect.y, tall_tile_w, tall_tile_h),
      rl.Rectangle(rect.x + tall_tile_w + gap, rect.y, tall_tile_w, tall_tile_h),
      rl.Rectangle(rect.x + 2 * (tall_tile_w + gap), rect.y, tall_tile_w, tall_tile_h),
      rl.Rectangle(rect.x + left_group_w + gap, rect.y, right_group_w, top_right_h),
      rl.Rectangle(rect.x + left_group_w + gap, rect.y + top_right_h + gap, bottom_tile_w, bottom_right_h),
      rl.Rectangle(rect.x + left_group_w + gap + bottom_tile_w + gap, rect.y + top_right_h + gap,
                   bottom_tile_w, bottom_right_h),
    )

    for tile_rect in tile_rects:
      rl.draw_rectangle_rounded(tile_rect, _TILE_ROUNDNESS, _TILE_SEGMENTS, _PLACEHOLDER_TILE_COLOR)

  def draw_status_border(self, rect: rl.Rectangle) -> None:
    inset = _BORDER_SIZE / 2
    border_rect = rl.Rectangle(
      rect.x + inset,
      rect.y + inset,
      rect.width - 2 * inset,
      rect.height - 2 * inset,
    )
    border_color = _BORDER_COLORS.get(ui_state.status, _BORDER_COLORS[UIStatus.DISENGAGED])
    rl.draw_rectangle_rounded_lines_ex(
      border_rect,
      _BORDER_ROUNDNESS,
      _BORDER_SEGMENTS,
      _BORDER_SIZE,
      border_color,
    )

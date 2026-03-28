import pyray as rl

from openpilot.selfdrive.ui.ui_state import ui_state, UIStatus
from openpilot.system.ui.widgets import Widget


# DAC view baseline: solid background plus a tici-style status border.
#
# Keep this file intentionally tiny while we rebuild the DAC UI from scratch.

_DAC_BG_COLOR = rl.BLACK

_BORDER_SIZE = 10
_BORDER_ROUNDNESS = 0.05
_BORDER_SEGMENTS = 24

_BORDER_COLORS = {
  UIStatus.DISENGAGED: rl.Color(0x12, 0x28, 0x39, 0xFF),
  UIStatus.OVERRIDE: rl.Color(0x89, 0x92, 0x8D, 0xFF),
  UIStatus.ENGAGED: rl.Color(0x16, 0x7F, 0x40, 0xFF),
}


class DACView(Widget):
  def _render(self, rect: rl.Rectangle) -> None:
    rl.draw_rectangle_rec(rect, _DAC_BG_COLOR)

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

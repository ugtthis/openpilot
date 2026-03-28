import pyray as rl

from openpilot.common.constants import CV
from openpilot.common.filter_simple import FirstOrderFilter
from openpilot.selfdrive.ui.mici.onroad.driver_state import DriverStateRenderer
from openpilot.selfdrive.ui.ui_state import ui_state
from openpilot.system.ui.lib.application import FontWeight, gui_app
from openpilot.system.ui.lib.text_measure import measure_text_cached
from openpilot.system.ui.widgets import Widget

_BG_COLOR = rl.Color(14, 14, 14, 255)
_SEPARATOR_COLOR = rl.Color(40, 40, 40, 255)
_SEPARATOR_THICKNESS = 1
_CORNER_ROUNDNESS = 0.2
_CORNER_SEGMENTS = 10
_SPEED_FONT_SIZE = 130
_SPEED_UNIT_FONT_SIZE = 36
_SPEED_CELL_FRACTION = 0.60
_DMOJI_SIZE = 100
_CELL_PAD = 8


class _SpeedCell(Widget):
  def __init__(self):
    super().__init__()
    self._font_bold = gui_app.font(FontWeight.BOLD)
    self._font_medium = gui_app.font(FontWeight.MEDIUM)
    self._speed_filter = FirstOrderFilter(0.0, 0.15, 1 / gui_app.target_fps)
    self._speed = 0.0
    self._unit = "km/h"

  def _update_state(self) -> None:
    car_state = ui_state.sm["carState"]
    v_ego = car_state.vEgoCluster or car_state.vEgo
    factor = CV.MS_TO_KPH if ui_state.is_metric else CV.MS_TO_MPH
    self._unit = "km/h" if ui_state.is_metric else "mph"
    self._speed = self._speed_filter.update(max(0.0, v_ego * factor))

  def _render(self, rect: rl.Rectangle) -> None:
    speed_text = str(round(self._speed))
    speed_size = measure_text_cached(self._font_bold, speed_text, _SPEED_FONT_SIZE)
    unit_size = measure_text_cached(self._font_medium, self._unit, _SPEED_UNIT_FONT_SIZE)
    group_top = rect.y + (rect.height - (speed_size.y + 4 + unit_size.y)) / 2
    speed_x = rect.x + (rect.width - speed_size.x) / 2
    unit_x = rect.x + (rect.width - unit_size.x) / 2

    rl.draw_text_ex(self._font_bold, speed_text, rl.Vector2(speed_x, group_top), _SPEED_FONT_SIZE, 0, rl.WHITE)
    rl.draw_text_ex(
      self._font_medium,
      self._unit,
      rl.Vector2(unit_x, group_top + speed_size.y + 4),
      _SPEED_UNIT_FONT_SIZE,
      0,
      rl.Color(200, 200, 200, 180),
    )


class _DMojiCell(Widget):
  def __init__(self):
    super().__init__()
    self._dmoji = DriverStateRenderer(lines=True)
    self._dmoji.set_should_draw(True)
    self._dmoji.set_force_active(True)

  def _update_state(self) -> None:
    self._dmoji.set_should_draw(ui_state.is_onroad())

  def _render(self, rect: rl.Rectangle) -> None:
    x = rect.x + (rect.width - _DMOJI_SIZE) / 2
    y = rect.y + (rect.height - _DMOJI_SIZE) / 2
    dmoji_rect = rl.Rectangle(x, y, _DMOJI_SIZE, _DMOJI_SIZE)
    self._dmoji.set_rect(dmoji_rect)
    self._dmoji.render(dmoji_rect)


class DACView(Widget):
  def __init__(self):
    super().__init__()
    self._speed_cell = self._child(_SpeedCell())
    self._dmoji_cell = self._child(_DMojiCell())

  def _layout(self) -> None:
    r = self.rect
    pad = _CELL_PAD
    speed_w = r.width * _SPEED_CELL_FRACTION
    dmoji_w = r.width - speed_w

    self._speed_cell.set_rect(rl.Rectangle(r.x + pad, r.y + pad, speed_w - pad * 2, r.height - pad * 2))
    self._dmoji_cell.set_rect(rl.Rectangle(r.x + speed_w + pad, r.y + pad, dmoji_w - pad * 2, r.height - pad * 2))

  def _render(self, rect: rl.Rectangle) -> None:
    rl.draw_rectangle_rounded(rect, _CORNER_ROUNDNESS, _CORNER_SEGMENTS, _BG_COLOR)
    sep_x = int(rect.x + rect.width * _SPEED_CELL_FRACTION)
    rl.draw_rectangle(sep_x, int(rect.y), _SEPARATOR_THICKNESS, int(rect.height), _SEPARATOR_COLOR)
    self._speed_cell.render(self._speed_cell.rect)
    self._dmoji_cell.render(self._dmoji_cell.rect)

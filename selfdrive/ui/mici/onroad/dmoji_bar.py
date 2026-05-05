from __future__ import annotations

from dataclasses import dataclass
import pyray as rl
from cereal import log

from openpilot.common.filter_simple import FirstOrderFilter
from openpilot.system.ui.lib.application import gui_app
from openpilot.system.ui.widgets import Widget
from openpilot.selfdrive.ui.ui_state import ui_state

# Geometry
_N_PAIRS = 3
_N_CELLS = _N_PAIRS * 2  # 6 cells

# Higher reference inner width -> smaller scaled gaps vs inner width -> chunkier LED blocks
_REF_INNER_W = 200.0
_CELL_GAP_REF = 5
_PAIR_EXTRA_REF = 7

# A pair merges only after both cells in that pair are fully lit.
_PAIR_MERGE_FILL = 1.0
_COLLAPSE_STARTS_AT_PAIR = 1

_CELL_ROUNDNESS = 0.88
_PAIR_JOIN_CELL_ROUNDNESS = 0.46
_MULTI_CELL_ROUNDNESS = 0.34
_SEG_ROUND_SEGS = 6

_BAR_ROUNDNESS = 0.42
_BAR_ROUND_SEGMENTS = 10

# +y nudges dmoji bar down (frees dmoji face; bar may extend below cell bottom)
_DMOJI_BAR_SHIFT_DOWN_PX = 34.5
# Allow the dmoji bar to extend beyond the dmoji circle width.
_DMOJI_BAR_WIDTH_SCALE = 1.14
_DMOJI_BAR_HEIGHT_SCALE = 0.58

# Colors and alpha
_BAR_BG_RGB = (18, 18, 22)
_DMOJI_BAR_IDLE_ALPHA = 120
_DMOJI_BAR_FULL_ALPHA = 248
# TODO: Decide whether the dmoji bar and dmoji should share one peak opacity constant.
_DMOJI_IDLE_ALPHA = 120
_DMOJI_FULL_ALPHA = 248

_CELL_OFF_COLOR = rl.Color(38, 38, 44, 255)
_CELL_DIM_GREY = rl.Color(152, 152, 152, 255)
_CELL_BRIGHT_GREY = rl.Color(232, 232, 232, 255)
_CELL_YELLOW = rl.Color(255, 215, 0, 255)

_PAIR_COLORS: tuple[rl.Color, ...] = (
  _CELL_DIM_GREY,
  _CELL_BRIGHT_GREY,
  _CELL_YELLOW,
)

# Awareness / policy anchors (matches monitoring policy time ratios where noted)
_VISION_ALERT_1_AWARENESS = 1.0 - 3.0 / 11.0
_WHEELTOUCH_ALERT_1_AWARENESS = 1.0 - 15.0 / 30.0
_FULL_BAR_AT_ALERT_1_RISK_FRACTION = 0.68

_YELLOW_PAIR_START_N_LIT = 4.0


@dataclass(frozen=True)
class BarLayout:
  """Bar geometry; owns the rectangle math for cells, pairs, and prefixes."""

  cell_y: float
  cell_h: float
  cell_w: float
  cell_area_left: float
  cell_gap: float
  pair_extra: float

  def cell_left(self, cell_idx: int) -> float:
    pair_idx = cell_idx // 2
    return (
      self.cell_area_left
      + cell_idx * self.cell_w
      + cell_idx * self.cell_gap
      + pair_idx * self.pair_extra
    )

  def cell_rect(self, cell_idx: int) -> rl.Rectangle:
    return rl.Rectangle(self.cell_left(cell_idx), self.cell_y, self.cell_w, self.cell_h)

  def pair_rect(self, pair: int) -> rl.Rectangle:
    left_cell_idx = pair * 2
    return rl.Rectangle(
      self.cell_left(left_cell_idx),
      self.cell_y,
      2 * self.cell_w + self.cell_gap,
      self.cell_h,
    )

  def prefix_rect_through_cell(self, last_cell_idx: int) -> rl.Rectangle:
    w = self.cell_left(last_cell_idx) + self.cell_w - self.cell_area_left
    return rl.Rectangle(
      self.cell_area_left,
      self.cell_y,
      w,
      self.cell_h,
    )


# Awareness mapping

def _alert_1_awareness(is_vision_policy: bool) -> float:
  return _VISION_ALERT_1_AWARENESS if is_vision_policy else _WHEELTOUCH_ALERT_1_AWARENESS


def _full_bar_awareness(is_vision_policy: bool) -> float:
  """Awareness at which the strip reaches full fill (start of the full-yellow plateau)."""
  pre = 1.0 - _alert_1_awareness(is_vision_policy)
  return 1.0 - pre * _FULL_BAR_AT_ALERT_1_RISK_FRACTION


def dm_display_level(awareness: float, is_vision_policy: bool) -> float:
  """Map awareness [0,1] to bar fill [0,1], reaching 1.0 before AlertLevel.one."""
  awareness_f = max(0.0, min(1.0, float(awareness)))
  risk = max(0.0, min(1.0, 1.0 - awareness_f))
  ramp_risk = 1.0 - _full_bar_awareness(is_vision_policy)
  if ramp_risk <= 1e-6:
    return 1.0
  return max(0.0, min(1.0, risk / ramp_risk))


def dm_n_cells_lit_from_display_level(level: float) -> float:
  """Visual fill [0,1] -> equivalent lit cell units (fractional OK)."""
  return max(0.0, min(1.0, float(level))) * _N_CELLS


def _first_cell_fill(level: float) -> float:
  """Progress through the first cell: 0 at idle, 1 once any cell is fully lit."""
  return min(1.0, dm_n_cells_lit_from_display_level(level))


def _idle_alpha(base_alpha: int, level: float, idle_alpha: int, full_alpha: int) -> int:
  alpha_scale = idle_alpha / full_alpha + _first_cell_fill(level) * (1.0 - idle_alpha / full_alpha)
  a = float(base_alpha) * alpha_scale
  return int(max(0, min(255, round(a))))


def dmoji_idle_alpha(base_alpha: int, level: float) -> int:
  """Scale dmoji texture alpha with the same idle-to-active curve as the bar."""
  return _idle_alpha(base_alpha, level, _DMOJI_IDLE_ALPHA, _DMOJI_FULL_ALPHA)


def dm_should_show_ring(level: float) -> bool:
  """Return whether the yellow dmoji ring should be visible."""
  n = dm_n_cells_lit_from_display_level(level)
  return n > _YELLOW_PAIR_START_N_LIT + 1e-9


# Layout

def _layout_gaps(inner_w: float) -> tuple[float, float]:
  s = min(1.0, max(0.22, inner_w / _REF_INNER_W))
  cell_gap = max(0.0, _CELL_GAP_REF * s)
  pair_extra = max(0.0, _PAIR_EXTRA_REF * s)
  total_gap = (_N_CELLS - 1) * cell_gap + (_N_PAIRS - 1) * pair_extra
  if total_gap >= inner_w * 0.55:
    shrink = (inner_w * 0.48) / max(total_gap, 1e-6)
    cell_gap *= shrink
    pair_extra *= shrink
  return cell_gap, pair_extra


def _bar_layout(rect: rl.Rectangle) -> BarLayout:
  rw = max(1.0, rect.width)
  rh = max(1.0, rect.height)
  compact = rw < 112.0 or rh < 22.0
  pad_h = max(4.0, min(14.0, rw * 0.072))
  pad_v = max(3.0, min(9.0, rh * 0.14))
  if compact:
    pad_h = max(2.0, pad_h * 0.88)
    pad_v = max(2.0, pad_v * 0.88)

  cell_y = rect.y + pad_v
  cell_area_h = rh - 2 * pad_v
  cell_area_left = rect.x + pad_h
  cell_w_total = rw - 2 * pad_h
  cell_gap, pair_extra = _layout_gaps(cell_w_total)
  total_gap_w = (_N_CELLS - 1) * cell_gap + (_N_PAIRS - 1) * pair_extra
  cell_w = max(1.5, (cell_w_total - total_gap_w) / _N_CELLS)
  cell_h = max(2.0, cell_area_h)
  return BarLayout(
    cell_y=cell_y,
    cell_h=cell_h,
    cell_w=cell_w,
    cell_area_left=cell_area_left,
    cell_gap=cell_gap,
    pair_extra=pair_extra,
  )


# Drawing

def _blend_cell(on: rl.Color, fill: float) -> rl.Color:
  off = _CELL_OFF_COLOR
  return rl.Color(
    int(off.r + fill * (on.r - off.r)),
    int(off.g + fill * (on.g - off.g)),
    int(off.b + fill * (on.b - off.b)),
    255,
  )


def _pair_color(pair: int, cell_color: rl.Color | None) -> rl.Color:
  if cell_color is not None:
    return cell_color
  return _PAIR_COLORS[pair]


def _color_with_idle_dim(c: rl.Color, level: float, fade_alpha: int) -> rl.Color:
  a = min(c.a, fade_alpha)
  return rl.Color(c.r, c.g, c.b, _idle_alpha(a, level, _DMOJI_BAR_IDLE_ALPHA, _DMOJI_BAR_FULL_ALPHA))


def _draw_segment(rect: rl.Rectangle, roundness: float, color: rl.Color, level: float, fade_alpha: int) -> None:
  rl.draw_rectangle_rounded(
    rect,
    roundness,
    _SEG_ROUND_SEGS,
    _color_with_idle_dim(color, level, fade_alpha),
  )


def _draw_pair(layout: BarLayout, pair: int, n_lit: float, color: rl.Color, level: float, fade_alpha: int) -> None:
  """Draw a pair as two fractional cells until the right cell is filled enough to merge."""
  i_left = pair * 2
  i_right = i_left + 1
  left_fill = min(max(n_lit - i_left, 0.0), 1.0)
  right_fill = min(max(n_lit - i_right, 0.0), 1.0)

  if right_fill >= _PAIR_MERGE_FILL:
    _draw_segment(layout.pair_rect(pair), _PAIR_JOIN_CELL_ROUNDNESS, color, level, fade_alpha)
    return

  _draw_segment(layout.cell_rect(i_left), _CELL_ROUNDNESS, _blend_cell(color, left_fill), level, fade_alpha)
  _draw_segment(layout.cell_rect(i_right), _CELL_ROUNDNESS, _blend_cell(color, right_fill), level, fade_alpha)


def _draw_cells(
  n_lit: float,
  layout: BarLayout,
  level: float,
  fade_alpha: int,
  cell_color: rl.Color | None = None,
) -> None:
  """
  Draw lit cells left-to-right.

  Rules (unchanged behavior):
  - All cells lit -> one full-width yellow block.
  - Else optional collapsed prefix for completed pairs from pair >= _COLLAPSE_STARTS_AT_PAIR,
    then fractional / merged drawing for remaining pairs.
  """
  # All cells on: single full-width block (after second yellow cell completes per prior behavior).
  if n_lit + 1e-3 >= _N_CELLS:
    yc = cell_color if cell_color is not None else _CELL_YELLOW
    _draw_segment(layout.prefix_rect_through_cell(_N_CELLS - 1), _MULTI_CELL_ROUNDNESS, yc, level, fade_alpha)
    return

  # Highest completed pair index in [_COLLAPSE_STARTS_AT_PAIR, _N_PAIRS), or -1.
  last_collapsed_pair = min(_N_PAIRS - 1, int(n_lit // 2) - 1)
  if last_collapsed_pair < _COLLAPSE_STARTS_AT_PAIR:
    last_collapsed_pair = -1

  first_discrete_pair = last_collapsed_pair + 1 if last_collapsed_pair >= 0 else 0

  if last_collapsed_pair >= 0:
    top_cell = 2 * (last_collapsed_pair + 1) - 1
    col = _pair_color(top_cell // 2, cell_color)
    _draw_segment(layout.prefix_rect_through_cell(top_cell), _MULTI_CELL_ROUNDNESS, col, level, fade_alpha)

  for pair in range(first_discrete_pair, _N_PAIRS):
    _draw_pair(layout, pair, n_lit, _pair_color(pair, cell_color), level, fade_alpha)


def _draw_panel(rect: rl.Rectangle, level: float, fade_alpha: int) -> None:
  bar_a = _idle_alpha(fade_alpha, level, _DMOJI_BAR_IDLE_ALPHA, _DMOJI_BAR_FULL_ALPHA)
  bar_bg = rl.Color(_BAR_BG_RGB[0], _BAR_BG_RGB[1], _BAR_BG_RGB[2], bar_a)
  rl.draw_rectangle_rounded(rect, _BAR_ROUNDNESS, _BAR_ROUND_SEGMENTS, bar_bg)


def _draw_horizontal_bar(
  rect: rl.Rectangle,
  level: float,
  fade_alpha: int = _DMOJI_BAR_FULL_ALPHA,
  cell_color: rl.Color | None = None,
) -> None:
  """Draw cell strip + panel. level in [0, 1]."""
  fade_alpha = int(max(0, min(_DMOJI_BAR_FULL_ALPHA, fade_alpha)))
  _draw_panel(rect, level, fade_alpha)
  layout = _bar_layout(rect)
  n_lit = dm_n_cells_lit_from_display_level(level)
  _draw_cells(n_lit, layout, level, fade_alpha, cell_color)


def dmoji_bar_rect(dmoji_rect: rl.Rectangle) -> rl.Rectangle:
  """Place the awareness strip along the bottom of the dmoji rect; +y = down."""
  w = max(48.0, float(dmoji_rect.width) * _DMOJI_BAR_WIDTH_SCALE)
  h_bar = max(4.0, float(dmoji_rect.height) * _DMOJI_BAR_HEIGHT_SCALE)
  cx = dmoji_rect.x + dmoji_rect.width / 2
  bottom = dmoji_rect.y + dmoji_rect.height
  margin_bottom = max(0.0, min(2.0, float(dmoji_rect.height) * 0.02))
  return rl.Rectangle(
    cx - w / 2, bottom - margin_bottom - h_bar + _DMOJI_BAR_SHIFT_DOWN_PX, w, h_bar,
  )


class DmojiBar(Widget):
  """Smoothed horizontal awareness strip; reads `driverMonitoringState` (DM v2) each frame."""

  def __init__(self) -> None:
    super().__init__()
    self._level_filter = FirstOrderFilter(0.0, 0.1, 1 / gui_app.target_fps)
    self._dmoji_fade_alpha = _DMOJI_BAR_FULL_ALPHA

  def set_dmoji_fade_alpha(self, fade_alpha: int) -> None:
    self._dmoji_fade_alpha = fade_alpha

  def _update_state(self) -> None:
    dm = ui_state.sm["driverMonitoringState"]
    is_vision_policy = dm.activePolicy == log.DriverMonitoringState.MonitoringPolicy.vision
    pct = dm.visionPolicyState.awarenessPercent if is_vision_policy else dm.wheeltouchPolicyState.awarenessPercent
    awareness = max(0.0, min(1.0, float(pct) / 100.0))
    self._level_filter.update(dm_display_level(awareness, is_vision_policy))

  def _render(self, rect: rl.Rectangle) -> None:
    fade = int(max(0, min(_DMOJI_BAR_FULL_ALPHA, self._dmoji_fade_alpha)))
    if fade <= 0 or rect.width <= 0 or rect.height <= 0:
      return
    _draw_horizontal_bar(rect, self._level_filter.x, fade)

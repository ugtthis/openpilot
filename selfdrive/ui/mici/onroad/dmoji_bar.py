from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import numpy as np
import pyray as rl
from cereal import log

from openpilot.common.filter_simple import FirstOrderFilter
from openpilot.system.ui.lib.application import gui_app
from openpilot.system.ui.widgets import Widget
from openpilot.selfdrive.ui.ui_state import ui_state

# --- Dmoji bar geometry ---
_N_PAIRS = 3
_N_CELLS = _N_PAIRS * 2  # 6 cells
# Higher reference -> smaller scaled gaps vs inner width -> chunkier LED blocks (paired with taller bar rects)
_REF_INNER_W = 200.0
_CELL_GAP_REF = 5
_PAIR_EXTRA_REF = 7
_MERGE_THRESHOLD = 0.5
_COLLAPSE_STARTS_AT_PAIR = 1
_CELL_ROUNDNESS = 0.88
_PAIR_JOIN_CELL_ROUNDNESS = 0.46
_MULTI_CELL_ROUNDNESS = 0.34
_SEG_ROUND_SEGS = 6

_BAR_ROUNDNESS = 0.42
_BAR_ROUND_SEGMENTS = 10

# +y nudges strip down (frees dmoji face; bar may extend below cell bottom)
_DMOJI_BAR_SHIFT_DOWN_PX = 34.5
# Allow the dmoji bar to extend beyond the dmoji circle width.
_DMOJI_BAR_WIDTH_SCALE = 1.14
# Keep bar thickness independent from width so widening does not make it taller.
_DMOJI_BAR_HEIGHT_SCALE = 0.58

# --- Colors and alpha (RGB fixed; bar alpha lerp'd in _draw_horizontal_bar) ---
_BAR_BG_RGB = (18, 18, 22)
# Dmoji bar panel + cell opacity.
_DMOJI_BAR_IDLE_ALPHA = 130
_DMOJI_BAR_FULL_ALPHA = 248
# TODO: Decide whether the dmoji bar and dmoji should share one peak opacity constant.
# Dmoji background/person texture opacity.
_DMOJI_IDLE_ALPHA = 120
_DMOJI_FULL_ALPHA = 248
_CELL_OFF_COLOR = rl.Color(38, 38, 44, 255)
_CELL_DIM_GREY = rl.Color(152, 152, 152, 255)
_CELL_BRIGHT_GREY = rl.Color(232, 232, 232, 255)
_CELL_YELLOW = rl.Color(255, 215, 0, 255)

_CELL_ON: tuple[rl.Color, ...] = (
  _CELL_DIM_GREY,
  _CELL_DIM_GREY,
  _CELL_BRIGHT_GREY,
  _CELL_BRIGHT_GREY,
  _CELL_YELLOW,
  _CELL_YELLOW,
)

# --- Awareness display (AlertLevel.one; mirrors monitoring policy time ratios) ---
# AlertLevel.one anchors: 1 - t1 / t3 for the active policy (see monitoring policy).
# Vision intentionally uses the exact 1 - 3/11 ratio, not the old rounded 0.727 literal
# (tiny difference in the bar ramp; policy alignment is preferred here).
_VISION_ALERT_1_AWARENESS = 1.0 - 3.0 / 11.0
# Wheeltouch: 1 - 15/30
_WHEELTOUCH_ALERT_1_AWARENESS = 1.0 - 15.0 / 30.0
# In risk (1 - awareness), fill the bar by this fraction of the window down to alert one; then
# hold full until alert one. Order: second yellow cell -> full bar -> first alert.
_FULL_BAR_AT_ALERT_1_RISK_FRACTION = 0.68
# n_lit = level * _N_CELLS. Ring turns on when the yellow pair (last pair) gets any fill (n_lit > 4).
_YELLOW_PAIR_START_N_LIT = 4.0
# All cells lit: collapse to one full-width yellow block.
_FULL_BAR_N_LIT = float(_N_CELLS)


@dataclass(frozen=True)
class BarStripLayout:
  """Padded cell strip: geometry shared by all cells, before fill-aware drawing."""
  cell_y: float
  cell_h: float
  cell_w: float
  cell_area_left: float
  cell_gap: float
  pair_extra: float


@dataclass(frozen=True)
class CellLayout:
  index: int
  pair: int
  rect: rl.Rectangle  # unscaled cell hit rect (x, y, w, h)


@dataclass(frozen=True)
class CellDraw:
  rect: rl.Rectangle
  roundness: float
  color: rl.Color


def _layout_gaps(inner_w: float) -> tuple[float, float]:
  """Scale cell and pair gaps so all blocks fit without crowding on narrow strips."""
  s = min(1.0, max(0.22, inner_w / _REF_INNER_W))
  cell_gap = max(0.0, _CELL_GAP_REF * s)
  pair_extra = max(0.0, _PAIR_EXTRA_REF * s)
  total_gap = (_N_CELLS - 1) * cell_gap + (_N_PAIRS - 1) * pair_extra
  if total_gap >= inner_w * 0.55:
    shrink = (inner_w * 0.48) / max(total_gap, 1e-6)
    cell_gap *= shrink
    pair_extra *= shrink
  return cell_gap, pair_extra


def _cell_left_x(
  cell_idx: int,
  cell_w: float,
  cell_area_left: float,
  cell_gap: float,
  pair_extra: float,
) -> float:
  pair_idx = cell_idx // 2
  return (
    cell_area_left
    + cell_idx * cell_w
    + cell_idx * cell_gap
    + pair_idx * pair_extra
  )


def _bar_strip_layout(rect: rl.Rectangle) -> BarStripLayout:
  """Panel padding, gap scaling, and cell W/H for the cell strip; same math as _draw_horizontal_bar body."""
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
  return BarStripLayout(
    cell_y=cell_y, cell_h=cell_h, cell_w=cell_w, cell_area_left=cell_area_left, cell_gap=cell_gap, pair_extra=pair_extra,
  )


def _cell_layouts(strip: BarStripLayout) -> list[CellLayout]:
  """One layout entry per cell index (0.._N_CELLS-1)."""
  out: list[CellLayout] = []
  for i in range(_N_CELLS):
    left = _cell_left_x(i, strip.cell_w, strip.cell_area_left, strip.cell_gap, strip.pair_extra)
    r = rl.Rectangle(left, strip.cell_y, strip.cell_w, strip.cell_h)
    out.append(CellLayout(index=i, pair=i // 2, rect=r))
  return out


def _lit_cell_count(level: float) -> float:
  """How many fully-equivalent cell units are lit; alias for public helper used by this module."""
  return dm_n_cells_lit_from_display_level(level)


def _blend_cell(on: rl.Color, fill: float) -> rl.Color:
  off = _CELL_OFF_COLOR
  return rl.Color(
    int(off.r + fill * (on.r - off.r)),
    int(off.g + fill * (on.g - off.g)),
    int(off.b + fill * (on.b - off.b)),
    255,
  )


def _alert_1_awareness(is_vision_policy: bool) -> float:
  return _VISION_ALERT_1_AWARENESS if is_vision_policy else _WHEELTOUCH_ALERT_1_AWARENESS


def _full_bar_awareness(is_vision_policy: bool) -> float:
  """Awareness at which the strip reaches full fill (start of the full-yellow plateau)."""
  pre = 1.0 - _alert_1_awareness(is_vision_policy)
  return 1.0 - pre * _FULL_BAR_AT_ALERT_1_RISK_FRACTION


def dm_display_level(awareness: float, is_vision_policy: bool) -> float:
  """Map awareness [0,1] to bar fill [0,1], reaching 1.0 before AlertLevel.one."""
  awareness = float(np.clip(awareness, 0.0, 1.0))
  risk = float(np.clip(1.0 - awareness, 0.0, 1.0))
  ramp_risk = 1.0 - _full_bar_awareness(is_vision_policy)
  if ramp_risk <= 1e-6:
    return 1.0
  return float(np.clip(risk / ramp_risk, 0.0, 1.0))


def dm_n_cells_lit_from_display_level(level: float) -> float:
  """Convert visual fill to the cell count used by _draw_horizontal_bar."""
  v = float(np.clip(level, 0.0, 1.0)) * _N_CELLS
  return float(v)


def _first_cell_fill(level: float) -> float:
  """Progress through the first cell: 0 at idle, 1 once any cell is fully lit."""
  return min(1.0, dm_n_cells_lit_from_display_level(level))


def _idle_alpha(base_alpha: int, level: float, idle_alpha: int, full_alpha: int) -> int:
  """Scale a draw alpha by an idle-to-full reference alpha curve."""
  alpha_scale = np.interp(_first_cell_fill(level), [0.0, 1.0], [idle_alpha / full_alpha, 1.0])
  a = float(base_alpha) * alpha_scale
  return int(max(0, min(255, round(a))))


def dmoji_idle_alpha(base_alpha: int, level: float) -> int:
  """Scale dmoji texture alpha with the same idle-to-active curve as the bar."""
  return _idle_alpha(base_alpha, level, _DMOJI_IDLE_ALPHA, _DMOJI_FULL_ALPHA)


def _color_with_idle_dim(c: rl.Color, level: float, fade_alpha: int) -> rl.Color:
  """Apply the dmoji bar idle curve to cell colors."""
  a = min(c.a, fade_alpha)
  return rl.Color(c.r, c.g, c.b, _idle_alpha(a, level, _DMOJI_BAR_IDLE_ALPHA, _DMOJI_BAR_FULL_ALPHA))


def dm_display_ring_band(level: float) -> Literal["none", "yellow", "orange"]:
  """Return the dmoji ring band for the current visual fill level."""
  # TODO: drop "orange" from return type + callers once orange ring is removed
  n = dm_n_cells_lit_from_display_level(level)
  if n <= _YELLOW_PAIR_START_N_LIT + 1e-9:
    return "none"  # dim + bright grey pairs; last pair not started
  return "yellow"  # last pair (yellow cells) has started


def _cell_on_colors(pair: int, cell_color: rl.Color | None) -> tuple[rl.Color, rl.Color]:
  i_left = pair * 2
  i_right = pair * 2 + 1
  if cell_color is not None:
    on_l = on_r = cell_color
  else:
    on_l = _CELL_ON[i_left]
    on_r = _CELL_ON[i_right]
  return on_l, on_r


def _cells_for_pair(cells: list[CellLayout], pair: int) -> tuple[CellLayout, CellLayout]:
  pair_cells = [cell for cell in cells if cell.pair == pair]
  assert len(pair_cells) == 2
  return pair_cells[0], pair_cells[1]


def _draw_panel(rect: rl.Rectangle, level: float, fade_alpha: int) -> None:
  bar_a = _idle_alpha(fade_alpha, level, _DMOJI_BAR_IDLE_ALPHA, _DMOJI_BAR_FULL_ALPHA)
  bar_bg = rl.Color(_BAR_BG_RGB[0], _BAR_BG_RGB[1], _BAR_BG_RGB[2], bar_a)
  rl.draw_rectangle_rounded(rect, _BAR_ROUNDNESS, _BAR_ROUND_SEGMENTS, bar_bg)


def _draw_cells(draws: list[CellDraw], level: float, fade_alpha: int) -> None:
  for d in draws:
    rl.draw_rectangle_rounded(
      d.rect, d.roundness, _SEG_ROUND_SEGS, _color_with_idle_dim(d.color, level, fade_alpha),
    )


def _pair_cell_draws(
  pair: int,
  n_lit: float,
  strip: BarStripLayout,
  cells: list[CellLayout],
  cell_color: rl.Color | None,
) -> list[CellDraw]:
  on_l, on_r = _cell_on_colors(pair, cell_color)
  color = on_l
  i_left = pair * 2
  i_right = pair * 2 + 1
  left_fill = min(max(n_lit - i_left, 0.0), 1.0)
  right_fill = min(max(n_lit - i_right, 0.0), 1.0)
  left_cell, right_cell = _cells_for_pair(cells, pair)
  left = left_cell.rect
  right = right_cell.rect
  left_x, y = left.x, left.y
  w, h = left.width, left.height
  right_x = right.x

  if right_fill >= _MERGE_THRESHOLD:
    return [
      CellDraw(
        rect=rl.Rectangle(left_x, y, 2 * w + strip.cell_gap, h),
        roundness=_PAIR_JOIN_CELL_ROUNDNESS,
        color=color,
      ),
    ]
  return [
    CellDraw(
      rect=rl.Rectangle(left_x, y, w, h), roundness=_CELL_ROUNDNESS, color=_blend_cell(on_l, left_fill)
    ),
    CellDraw(
      rect=rl.Rectangle(right_x, y, w, h), roundness=_CELL_ROUNDNESS, color=_blend_cell(on_r, right_fill)
    ),
  ]


def _peak_full_bar_draw(n_lit: float, strip: BarStripLayout, cell_color: rl.Color | None) -> CellDraw | None:
  """All cells on: one full-width yellow block (let second yellow cell finish before this)."""
  if n_lit + 1e-3 < _FULL_BAR_N_LIT:
    return None
  last_i = _N_CELLS - 1
  full_w = (
    _cell_left_x(last_i, strip.cell_w, strip.cell_area_left, strip.cell_gap, strip.pair_extra) + strip.cell_w
    - strip.cell_area_left
  )
  yc = cell_color if cell_color is not None else _CELL_YELLOW
  return CellDraw(
    rect=rl.Rectangle(strip.cell_area_left, strip.cell_y, full_w, strip.cell_h),
    roundness=_MULTI_CELL_ROUNDNESS,
    color=yc,
  )


def _last_collapsed_pair(n_lit: float) -> int:
  """
  Highest pair index p in [_COLLAPSE_STARTS_AT_PAIR, _N_PAIRS) where the pair p is fully covered
  (n_lit >= 2 * (p + 1)). -1 if no collapsed prefix.
  """
  last = -1
  for p in range(_COLLAPSE_STARTS_AT_PAIR, _N_PAIRS):
    if n_lit >= 2 * (p + 1):
      last = p
  return last


def _collapsed_prefix_draw(
  last_collapsed_pair: int,
  strip: BarStripLayout,
  cell_color: rl.Color | None,
) -> CellDraw | None:
  if last_collapsed_pair < 0:
    return None
  # Rightmost cell index of the collapsed run (inclusive)
  top_cell = 2 * (last_collapsed_pair + 1) - 1
  c_x = strip.cell_area_left
  c_w = (
    _cell_left_x(top_cell, strip.cell_w, strip.cell_area_left, strip.cell_gap, strip.pair_extra) + strip.cell_w
    - strip.cell_area_left
  )
  col = cell_color if cell_color is not None else _CELL_ON[top_cell]
  return CellDraw(
    rect=rl.Rectangle(c_x, strip.cell_y, c_w, strip.cell_h),
    roundness=_MULTI_CELL_ROUNDNESS,
    color=col,
  )


def _first_discrete_pair(last_collapsed_pair: int) -> int:
  return last_collapsed_pair + 1 if last_collapsed_pair >= 0 else 0


def _cell_draws_for_level(
  lit_cells: float,
  strip: BarStripLayout,
  cells: list[CellLayout],
  cell_color: rl.Color | None,
) -> list[CellDraw]:
  peak_full_bar = _peak_full_bar_draw(lit_cells, strip, cell_color)
  if peak_full_bar is not None:
    return [peak_full_bar]

  last_collapsed_pair = _last_collapsed_pair(lit_cells)
  first_discrete_pair = _first_discrete_pair(last_collapsed_pair)

  draws: list[CellDraw] = []
  collapsed_prefix = _collapsed_prefix_draw(last_collapsed_pair, strip, cell_color)
  if collapsed_prefix is not None:
    draws.append(collapsed_prefix)

  for pair in range(first_discrete_pair, _N_PAIRS):
    draws.extend(_pair_cell_draws(pair, lit_cells, strip, cells, cell_color))

  return draws


def _draw_horizontal_bar(rect: rl.Rectangle, level: float, fade_alpha: int = _DMOJI_BAR_FULL_ALPHA,
                         cell_color: rl.Color | None = None) -> None:
  """Draw only the cell strip + panel (no DM label/icons). Level in [0, 1]."""
  fade_alpha = int(np.clip(fade_alpha, 0, _DMOJI_BAR_FULL_ALPHA))
  _draw_panel(rect, level, fade_alpha)

  strip = _bar_strip_layout(rect)
  cells = _cell_layouts(strip)
  lit_cells = _lit_cell_count(level)
  draws = _cell_draws_for_level(lit_cells, strip, cells, cell_color)
  _draw_cells(draws, level, fade_alpha)


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
    _draw_horizontal_bar(rect, self._level_filter.x, self._dmoji_fade_alpha)

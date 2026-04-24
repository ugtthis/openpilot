from __future__ import annotations

from typing import Literal

import numpy as np
import pyray as rl
from cereal import log

from openpilot.common.filter_simple import FirstOrderFilter
from openpilot.system.ui.lib.application import gui_app
from openpilot.system.ui.widgets import Widget
from openpilot.selfdrive.ui.ui_state import ui_state

# --- Segmented bar geometry (DAC reference; gaps scale down for narrow bars) ---
_N_PAIRS = 3
_N_SEGS = _N_PAIRS * 2  # 6 bars
# Higher reference → smaller scaled gaps vs inner width → chunkier LED blocks (paired with taller bar rects)
_REF_INNER_W = 200.0
_SEG_GAP_REF = 5
_PAIR_EXTRA_REF = 7
_MERGE_THRESHOLD = 0.5
_COLLAPSE_STARTS_AT_PAIR = 1
_SEG_ROUND_SEGS = 6
_PEAK_YELLOW_START_N_LIT = 5.0

_TILE_ROUNDNESS = 0.14
_TILE_SEGMENTS = 10

# Softer panel than raw DAC tile (reads better on small dmoji and over camera)
_BAR_BG_COLOR = rl.Color(18, 18, 22, 248)
_BAR_FRAME_COLOR = rl.Color(88, 88, 96, 90)
_SEG_OFF_COLOR = rl.Color(38, 38, 44, 255)

# DM awareness anchors (same as DAC)
_DM_AWARENESS_PRE_ALERT = 0.727
_DM_AWARENESS_PRE_ALERT_PASSIVE = 0.5
# The bar reaches full display before the pre-alert "pay attention" line, so the
# peak state has time to settle. (Smoothed display may still lag; tune 0.85-0.95.)
_DM_VISUAL_FULL_AT_PRE_RISK_FRACTION = 0.88

_SEG_DIM_GREY = rl.Color(152, 152, 152, 255)
_SEG_BRIGHT_GREY = rl.Color(232, 232, 232, 255)
_SEG_YELLOW = rl.Color(255, 215, 0, 255)

_SEG_ON: tuple[rl.Color, ...] = (
  _SEG_DIM_GREY,
  _SEG_DIM_GREY,
  _SEG_BRIGHT_GREY,
  _SEG_BRIGHT_GREY,
  _SEG_YELLOW,
  _SEG_YELLOW,
)

# +y nudges strip down (frees dmoji face; bar may extend below cell bottom)
_DM_BAR_SHIFT_DOWN_PX = 31.0
# Allow the DM strip to extend beyond the dmoji circle width.
_DM_BAR_WIDTH_SCALE = 1.16
# Keep bar thickness independent from width so widening does not make it taller.
_DM_BAR_HEIGHT_SCALE = 0.53


def _layout_gaps(inner_w: float) -> tuple[float, float]:
  """Scale segment and pair gaps so all blocks fit without crowding on narrow strips."""
  s = min(1.0, max(0.22, inner_w / _REF_INNER_W))
  seg_gap = max(0.0, _SEG_GAP_REF * s)
  pair_extra = max(0.0, _PAIR_EXTRA_REF * s)
  total_gap = (_N_SEGS - 1) * seg_gap + (_N_PAIRS - 1) * pair_extra
  if total_gap >= inner_w * 0.55:
    shrink = (inner_w * 0.48) / max(total_gap, 1e-6)
    seg_gap *= shrink
    pair_extra *= shrink
  return seg_gap, pair_extra


def _block_left_x(
  block_idx: int,
  seg_w: float,
  seg_area_left: float,
  seg_gap: float,
  pair_extra: float,
) -> float:
  pair_idx = block_idx // 2
  return (
    seg_area_left
    + block_idx * seg_w
    + block_idx * seg_gap
    + pair_idx * pair_extra
  )


def _seg_roundness(seg_w: float, seg_h: float) -> float:
  m = min(seg_w, seg_h)
  return min(0.32, max(0.1, m / 20.0))


def _blend_seg(on: rl.Color, fill: float) -> rl.Color:
  off = _SEG_OFF_COLOR
  return rl.Color(
    int(off.r + fill * (on.r - off.r)),
    int(off.g + fill * (on.g - off.g)),
    int(off.b + fill * (on.b - off.b)),
    255,
  )


def _pre_threshold(is_active_mode: bool) -> float:
  return _DM_AWARENESS_PRE_ALERT if is_active_mode else _DM_AWARENESS_PRE_ALERT_PASSIVE


def dm_display_level(awareness: float, is_active_mode: bool) -> float:
  """Map awareness [0,1] to bar fill [0,1], reaching full before the first alert."""
  awareness = float(np.clip(awareness, 0.0, 1.0))
  risk = float(np.clip(1.0 - awareness, 0.0, 1.0))
  pre_risk = float(np.clip(1.0 - _pre_threshold(is_active_mode), 0.0, 1.0))
  ramp_risk = pre_risk * _DM_VISUAL_FULL_AT_PRE_RISK_FRACTION
  if ramp_risk <= 1e-6:
    return 1.0
  return float(np.clip(risk / ramp_risk, 0.0, 1.0))


# Last pair (yellow) starts at n_lit = 4; ring turns on when that pair first gets
# any fill. `n_lit = level * _N_SEGS` in _draw_horizontal_bar
_RING_TURNS_ON_AFTER_N_LIT = 4.0


def dm_n_lit_from_display_level(level: float) -> float:
  """Match the bar: `n_lit` used in _draw_horizontal_bar (same as level * 6, clamped to [0,6])."""
  v = float(np.clip(level, 0.0, 1.0)) * _N_SEGS
  return float(v)


def dm_display_ring_band(level: float) -> Literal["none", "yellow", "orange"]:
  """Single ring: off until the last (yellow) pair gets any fill; “orange” unused here."""
  # TODO: drop "orange" from return type + callers once orange ring is removed
  n = dm_n_lit_from_display_level(level)
  if n <= _RING_TURNS_ON_AFTER_N_LIT + 1e-9:
    return "none"  # dim + bright grey pairs; last pair not started
  return "yellow"  # last pair (yellow segments) has started


def _draw_pair_horizontal(
  pair: int,
  n_lit: float,
  seg_h: float,
  seg_y: float,
  seg_w: float,
  seg_area_left: float,
  seg_gap: float,
  pair_extra: float,
  seg_round: float,
  segment_color: rl.Color | None,
) -> None:
  i_left = pair * 2
  i_right = pair * 2 + 1
  if segment_color is not None:
    on_l = on_r = segment_color
  else:
    on_l = _SEG_ON[i_left]
    on_r = _SEG_ON[i_right]
  color = on_l

  left_fill = min(max(n_lit - i_left, 0.0), 1.0)
  right_fill = min(max(n_lit - i_right, 0.0), 1.0)

  left_x = _block_left_x(i_left, seg_w, seg_area_left, seg_gap, pair_extra)
  right_x = _block_left_x(i_right, seg_w, seg_area_left, seg_gap, pair_extra)

  if right_fill >= _MERGE_THRESHOLD:
    rl.draw_rectangle_rounded(
      rl.Rectangle(left_x, seg_y, 2 * seg_w + seg_gap, seg_h),
      seg_round,
      _SEG_ROUND_SEGS,
      color,
    )
  else:
    rl.draw_rectangle_rounded(
      rl.Rectangle(left_x, seg_y, seg_w, seg_h),
      seg_round,
      _SEG_ROUND_SEGS,
      _blend_seg(on_l, left_fill),
    )
    rl.draw_rectangle_rounded(
      rl.Rectangle(right_x, seg_y, seg_w, seg_h),
      seg_round,
      _SEG_ROUND_SEGS,
      _blend_seg(on_r, right_fill),
    )


def _draw_horizontal_bar(rect: rl.Rectangle, level: float, segment_color: rl.Color | None = None) -> None:
  """Draw only the segmented strip + panel (no DM label/icons). Level in [0, 1]."""
  rw = max(1.0, rect.width)
  rh = max(1.0, rect.height)
  compact = rw < 112.0 or rh < 22.0
  # Increase panel inset so segments sit farther from all edges.
  pad_h = max(4.0, min(14.0, rw * 0.065))
  pad_v = max(3.0, min(9.0, rh * 0.14))
  if compact:
    pad_h = max(2.0, pad_h * 0.88)
    pad_v = max(2.0, pad_v * 0.88)

  tile_r = min(0.22, rh / max(rw, 1.0) * 0.9)
  rl.draw_rectangle_rounded(rect, tile_r, _TILE_SEGMENTS, _BAR_BG_COLOR)
  rl.draw_rectangle_rounded_lines_ex(rect, tile_r, _TILE_SEGMENTS, 1.0, _BAR_FRAME_COLOR)

  seg_y = rect.y + pad_v
  seg_area_h = rh - 2 * pad_v
  seg_area_left = rect.x + pad_h
  seg_w_total = rw - 2 * pad_h

  seg_gap, pair_extra = _layout_gaps(seg_w_total)
  total_gap_w = (_N_SEGS - 1) * seg_gap + (_N_PAIRS - 1) * pair_extra
  seg_w = max(1.5, (seg_w_total - total_gap_w) / _N_SEGS)
  seg_h = max(2.0, seg_area_h)
  seg_round = _seg_roundness(seg_w, seg_h)

  n_lit = level * _N_SEGS

  if n_lit + 1e-3 >= _PEAK_YELLOW_START_N_LIT:
    full_w = (
      _block_left_x(_N_SEGS - 1, seg_w, seg_area_left, seg_gap, pair_extra)
      + seg_w
      - seg_area_left
    )
    rl.draw_rectangle_rounded(
      rl.Rectangle(seg_area_left, seg_y, full_w, seg_h),
      seg_round,
      _SEG_ROUND_SEGS,
      segment_color if segment_color is not None else _SEG_YELLOW,
    )
    return

  collapse_until = -1
  for p in range(_COLLAPSE_STARTS_AT_PAIR, _N_PAIRS):
    if n_lit >= 2 * (p + 1):
      collapse_until = p

  if collapse_until >= 0:
    top_block_of_collapse = 2 * (collapse_until + 1) - 1
    c_x = seg_area_left
    c_w = (
      _block_left_x(top_block_of_collapse, seg_w, seg_area_left, seg_gap, pair_extra)
      + seg_w
      - seg_area_left
    )
    rl.draw_rectangle_rounded(
      rl.Rectangle(c_x, seg_y, c_w, seg_h),
      seg_round,
      _SEG_ROUND_SEGS,
      segment_color if segment_color is not None else _SEG_ON[top_block_of_collapse],
    )
    first_normal_pair = collapse_until + 1
  else:
    first_normal_pair = 0

  for pair in range(first_normal_pair, _N_PAIRS):
    _draw_pair_horizontal(
      pair,
      n_lit,
      seg_h,
      seg_y,
      seg_w,
      seg_area_left,
      seg_gap,
      pair_extra,
      seg_round,
      segment_color,
    )


def dm_segment_bar_rect(dmoji_rect: rl.Rectangle) -> rl.Rectangle:
  """Place the LED strip along the bottom of the dmoji rect (body band); +y = down."""
  w = max(48.0, float(dmoji_rect.width) * _DM_BAR_WIDTH_SCALE)
  h_bar = max(4.0, float(dmoji_rect.height) * _DM_BAR_HEIGHT_SCALE)
  cx = dmoji_rect.x + dmoji_rect.width / 2
  bottom = dmoji_rect.y + dmoji_rect.height
  margin_bottom = max(0.0, min(2.0, float(dmoji_rect.height) * 0.02))
  return rl.Rectangle(
    cx - w / 2, bottom - margin_bottom - h_bar + _DM_BAR_SHIFT_DOWN_PX, w, h_bar,
  )


class DmSegmentBar(Widget):
  """Smoothed horizontal awareness strip; reads `driverMonitoringState` (DM v2) each frame."""

  def __init__(self) -> None:
    super().__init__()
    self._level_filter = FirstOrderFilter(0.0, 0.1, 1 / gui_app.target_fps)

  def _update_state(self) -> None:
    dm = ui_state.sm["driverMonitoringState"]
    is_active = dm.activePolicy == log.DriverMonitoringState.MonitoringPolicy.vision
    pct = dm.visionPolicyState.awarenessPercent if is_active else dm.wheeltouchPolicyState.awarenessPercent
    awareness = max(0.0, min(1.0, float(pct) / 100.0))
    self._level_filter.update(dm_display_level(awareness, is_active))

  def _render(self, rect: rl.Rectangle) -> None:
    _draw_horizontal_bar(rect, self._level_filter.x)

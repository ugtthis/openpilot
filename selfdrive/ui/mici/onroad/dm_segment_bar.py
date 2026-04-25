from __future__ import annotations

from typing import Literal

import numpy as np
import pyray as rl
from cereal import log

from openpilot.common.filter_simple import FirstOrderFilter
from openpilot.system.ui.lib.application import gui_app
from openpilot.system.ui.widgets import Widget
from openpilot.selfdrive.ui.ui_state import ui_state

# --- Segmented bar geometry ---
_N_PAIRS = 3
_N_SEGS = _N_PAIRS * 2  # 6 bars
# Higher reference → smaller scaled gaps vs inner width → chunkier LED blocks (paired with taller bar rects)
_REF_INNER_W = 200.0
_SEG_GAP_REF = 5
_PAIR_EXTRA_REF = 7
_MERGE_THRESHOLD = 0.5
_COLLAPSE_STARTS_AT_PAIR = 1
_SEG_ROUNDNESS = 0.88
_PAIR_JOIN_SEG_ROUNDNESS = 0.46
_MULTI_SEG_ROUNDNESS = 0.34
_SEG_ROUND_SEGS = 6
_PEAK_YELLOW_START_N_LIT = float(_N_SEGS)

_BAR_ROUNDNESS = 0.42
_BAR_ROUND_SEGMENTS = 10

# RGB fixed; alpha is lerp’d in _draw_horizontal_bar: idle (no segments lit) → full.
_BAR_BG_RGB = (18, 18, 22)
# DM bar panel + segment opacity.
_DM_BAR_IDLE_ALPHA = 210
_DM_BAR_FULL_ALPHA = 248 # Will this always match the dmoji full alpha?
# Dmoji background/person texture opacity.
_DMOJI_IDLE_ALPHA = 120
_DMOJI_FULL_ALPHA = 248
_SEG_OFF_COLOR = rl.Color(38, 38, 44, 255)

# DM awareness anchors (same as DAC)
_DM_AWARENESS_PRE_ALERT = 0.727
_DM_AWARENESS_PRE_ALERT_PASSIVE = 0.5
# Visual ramp progress through the pre-alert risk window. The remaining window
# shows the peak state, keeping the order clear:
# second yellow segment -> full yellow bar -> alert.
_DM_FULL_YELLOW_AT_PRE_ALERT_RISK_FRACTION = 0.70

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
_DM_BAR_WIDTH_SCALE = 1.14
# Keep bar thickness independent from width so widening does not make it taller.
_DM_BAR_HEIGHT_SCALE = 0.54


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
  ramp_risk = pre_risk * _DM_FULL_YELLOW_AT_PRE_ALERT_RISK_FRACTION
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


def _first_segment_fill(level: float) -> float:
  """Progress through the first segment: 0 at idle, 1 once any segment is fully lit."""
  return min(1.0, dm_n_lit_from_display_level(level))


def _idle_alpha(base_alpha: int, level: float, idle_alpha: int, full_alpha: int) -> int:
  """Scale a draw alpha by an idle-to-full reference alpha curve."""
  alpha_scale = np.interp(_first_segment_fill(level), [0.0, 1.0], [idle_alpha / full_alpha, 1.0])
  a = float(base_alpha) * alpha_scale
  return int(max(0, min(255, round(a))))


def dmoji_idle_alpha(base_alpha: int, level: float) -> int:
  """Idle-aware alpha scaler for dmoji background/person textures."""
  return _idle_alpha(base_alpha, level, _DMOJI_IDLE_ALPHA, _DMOJI_FULL_ALPHA)


def _color_with_idle_dim(c: rl.Color, level: float, fade_alpha: int) -> rl.Color:
  """Apply the DM bar idle curve to segment colors."""
  a = min(c.a, fade_alpha)
  return rl.Color(c.r, c.g, c.b, _idle_alpha(a, level, _DM_BAR_IDLE_ALPHA, _DM_BAR_FULL_ALPHA))


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
  level: float,
  fade_alpha: int,
  seg_h: float,
  seg_y: float,
  seg_w: float,
  seg_area_left: float,
  seg_gap: float,
  pair_extra: float,
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
      _PAIR_JOIN_SEG_ROUNDNESS,
      _SEG_ROUND_SEGS,
      _color_with_idle_dim(color, level, fade_alpha),
    )
  else:
    rl.draw_rectangle_rounded(
      rl.Rectangle(left_x, seg_y, seg_w, seg_h),
      _SEG_ROUNDNESS,
      _SEG_ROUND_SEGS,
      _color_with_idle_dim(_blend_seg(on_l, left_fill), level, fade_alpha),
    )
    rl.draw_rectangle_rounded(
      rl.Rectangle(right_x, seg_y, seg_w, seg_h),
      _SEG_ROUNDNESS,
      _SEG_ROUND_SEGS,
      _color_with_idle_dim(_blend_seg(on_r, right_fill), level, fade_alpha),
    )


def _draw_horizontal_bar(rect: rl.Rectangle, level: float, fade_alpha: int = _DM_BAR_FULL_ALPHA,
                         segment_color: rl.Color | None = None) -> None:
  """Draw only the segmented strip + panel (no DM label/icons). Level in [0, 1]."""
  rw = max(1.0, rect.width)
  rh = max(1.0, rect.height)
  fade_alpha = int(np.clip(fade_alpha, 0, _DM_BAR_FULL_ALPHA))
  compact = rw < 112.0 or rh < 22.0
  # Increase panel inset so segments sit farther from all edges.
  pad_h = max(4.0, min(14.0, rw * 0.072))
  pad_v = max(3.0, min(9.0, rh * 0.14))
  if compact:
    pad_h = max(2.0, pad_h * 0.88)
    pad_v = max(2.0, pad_v * 0.88)

  n_lit = dm_n_lit_from_display_level(level)
  # DM bar opacity: panel and segments share the same idle curve.
  bar_a = _idle_alpha(fade_alpha, level, _DM_BAR_IDLE_ALPHA, _DM_BAR_FULL_ALPHA)
  bar_bg = rl.Color(_BAR_BG_RGB[0], _BAR_BG_RGB[1], _BAR_BG_RGB[2], bar_a)
  rl.draw_rectangle_rounded(rect, _BAR_ROUNDNESS, _BAR_ROUND_SEGMENTS, bar_bg)

  seg_y = rect.y + pad_v
  seg_area_h = rh - 2 * pad_v
  seg_area_left = rect.x + pad_h
  seg_w_total = rw - 2 * pad_h

  seg_gap, pair_extra = _layout_gaps(seg_w_total)
  total_gap_w = (_N_SEGS - 1) * seg_gap + (_N_PAIRS - 1) * pair_extra
  seg_w = max(1.5, (seg_w_total - total_gap_w) / _N_SEGS)
  seg_h = max(2.0, seg_area_h)

  # Let the second yellow segment fill normally before collapsing into the
  # peak full-yellow state.
  if n_lit + 1e-3 >= _PEAK_YELLOW_START_N_LIT:
    full_w = (
      _block_left_x(_N_SEGS - 1, seg_w, seg_area_left, seg_gap, pair_extra)
      + seg_w
      - seg_area_left
    )
    yc = segment_color if segment_color is not None else _SEG_YELLOW
    rl.draw_rectangle_rounded(
      rl.Rectangle(seg_area_left, seg_y, full_w, seg_h),
      _MULTI_SEG_ROUNDNESS,
      _SEG_ROUND_SEGS,
      _color_with_idle_dim(yc, level, fade_alpha),
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
    col = segment_color if segment_color is not None else _SEG_ON[top_block_of_collapse]
    rl.draw_rectangle_rounded(
      rl.Rectangle(c_x, seg_y, c_w, seg_h),
      _MULTI_SEG_ROUNDNESS,
      _SEG_ROUND_SEGS,
      _color_with_idle_dim(col, level, fade_alpha),
    )
    first_normal_pair = collapse_until + 1
  else:
    first_normal_pair = 0

  for pair in range(first_normal_pair, _N_PAIRS):
    _draw_pair_horizontal(
      pair,
      n_lit,
      level,
      fade_alpha,
      seg_h,
      seg_y,
      seg_w,
      seg_area_left,
      seg_gap,
      pair_extra,
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
    self._dmoji_fade_alpha = _DM_BAR_FULL_ALPHA

  def set_dmoji_fade_alpha(self, fade_alpha: int) -> None:
    self._dmoji_fade_alpha = fade_alpha

  def _update_state(self) -> None:
    dm = ui_state.sm["driverMonitoringState"]
    is_active = dm.activePolicy == log.DriverMonitoringState.MonitoringPolicy.vision
    pct = dm.visionPolicyState.awarenessPercent if is_active else dm.wheeltouchPolicyState.awarenessPercent
    awareness = max(0.0, min(1.0, float(pct) / 100.0))
    self._level_filter.update(dm_display_level(awareness, is_active))

  def _render(self, rect: rl.Rectangle) -> None:
    _draw_horizontal_bar(rect, self._level_filter.x, self._dmoji_fade_alpha)

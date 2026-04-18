"""Horizontal DM awareness strip (DAC-style LEDs), used under the MICI dmoji only.

TICI/OG (`selfdrive/ui/onroad/driver_state.py`) does not draw this — MICI
(`selfdrive/ui/mici/onroad/driver_state.py`) imports it. Kept separate from
`dac_view` so layouts can diverge."""

from __future__ import annotations

import numpy as np
import pyray as rl

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

_TILE_ROUNDNESS = 0.14
_TILE_SEGMENTS = 10

# Softer panel than raw DAC tile (reads better on small dmoji and over camera)
_BAR_BG_COLOR = rl.Color(18, 18, 22, 248)
_BAR_FRAME_COLOR = rl.Color(88, 88, 96, 90)
_SEG_OFF_COLOR = rl.Color(38, 38, 44, 255)

# DM awareness anchors (same as DAC)
_DM_AWARENESS_PRE_ALERT = 0.727
_DM_AWARENESS_PROMPT = 0.545
_DM_AWARENESS_PRE_ALERT_PASSIVE = 0.5
_DM_AWARENESS_PROMPT_PASSIVE = 0.2
# With 6 bars, keep pre-alert anchor slightly above 4/6 so neutral+yellow have
# already collapsed into one yellow block before preDriverDistracted appears.
_DM_VISUAL_PRE_ANCHOR = 0.71
_DM_VISUAL_PROMPT_ANCHOR = 0.80

_SEG_ON: tuple[rl.Color, ...] = (
  rl.Color(166, 166, 166, 255),
  rl.Color(166, 166, 166, 255),
  rl.Color(255, 215, 0, 255),
  rl.Color(255, 215, 0, 255),
  rl.Color(255, 85, 0, 255),
  rl.Color(255, 85, 0, 255),
)


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


def og_dm_pre_threshold(is_active_mode: bool) -> float:
  return _DM_AWARENESS_PRE_ALERT if is_active_mode else _DM_AWARENESS_PRE_ALERT_PASSIVE


def og_dm_prompt_threshold(is_active_mode: bool) -> float:
  return _DM_AWARENESS_PROMPT if is_active_mode else _DM_AWARENESS_PROMPT_PASSIVE


def og_dm_display_level(awareness: float, is_active_mode: bool) -> float:
  """Remap DM fill so pre/prompt thresholds land on fixed visual anchors (DAC parity)."""
  awareness = float(np.clip(awareness, 0.0, 1.0))
  risk = float(np.clip(1.0 - awareness, 0.0, 1.0))
  pre_risk = float(np.clip(1.0 - og_dm_pre_threshold(is_active_mode), 0.0, 1.0))
  prompt_risk = float(np.clip(1.0 - og_dm_prompt_threshold(is_active_mode), 0.0, 1.0))

  if prompt_risk <= pre_risk + 1e-6:
    return risk

  pre_visual = _DM_VISUAL_PRE_ANCHOR
  prompt_visual = _DM_VISUAL_PROMPT_ANCHOR

  if risk <= pre_risk:
    if pre_risk <= 1e-6:
      return pre_visual
    return float(np.clip((risk / pre_risk) * pre_visual, 0.0, pre_visual))
  if risk <= prompt_risk:
    t = (risk - pre_risk) / (prompt_risk - pre_risk)
    return float(np.clip(pre_visual + t * (prompt_visual - pre_visual), pre_visual, prompt_visual))

  t = (risk - prompt_risk) / max(1e-6, 1.0 - prompt_risk)
  return float(np.clip(prompt_visual + t * (1.0 - prompt_visual), prompt_visual, 1.0))


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
  color = segment_color if segment_color is not None else _SEG_ON[i_left]

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
      _blend_seg(color, left_fill),
    )
    rl.draw_rectangle_rounded(
      rl.Rectangle(right_x, seg_y, seg_w, seg_h),
      seg_round,
      _SEG_ROUND_SEGS,
      _blend_seg(color, right_fill),
    )


def draw_og_horizontal_dm_bar(rect: rl.Rectangle, level: float, segment_color: rl.Color | None = None) -> None:
  """Draw only the segmented strip + panel (no DM label/icons). Level in [0, 1]."""
  rw = max(1.0, rect.width)
  rh = max(1.0, rect.height)
  compact = rw < 112.0 or rh < 22.0
  # Slightly thinner bezel so more of the strip is lit segments (proportions preserved)
  pad_h = max(2.0, min(8.0, rw * 0.034))
  pad_v = max(2.0, min(7.0, rh * 0.095))
  if compact:
    pad_h = max(1.5, pad_h * 0.82)
    pad_v = max(1.5, pad_v * 0.82)

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

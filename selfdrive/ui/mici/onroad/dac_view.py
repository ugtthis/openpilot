import numpy as np
import pyray as rl

from opendbc.car import ACCELERATION_DUE_TO_GRAVITY
from openpilot.common.filter_simple import FirstOrderFilter
from openpilot.selfdrive.ui.ui_state import ui_state, UIStatus
from openpilot.system.ui.lib.application import FontWeight, gui_app
from openpilot.system.ui.lib.text_measure import measure_text_cached
from openpilot.system.ui.widgets import Widget


# DAC view: solid background + tici-style status border.
#
# The 3 tall left tiles are live LED-style segmented signal bars:
#   STR — steering utilization  (higher = working harder, closer to saturation)
#   BRK — braking intensity     (higher = harder decel)
#   DM  — driver distraction    (higher = less aware, closer to takeover event)
#
# All three bars read the same way: height = disengage risk contribution.
# The remaining 3 right tiles are placeholders.

_DAC_BG_COLOR = rl.BLACK
_PLACEHOLDER_TILE_COLOR = rl.Color(210, 210, 210, 255)

_BORDER_SIZE = 10
_BORDER_ROUNDNESS = 0.05
_BORDER_SEGMENTS = 24

_CONTENT_INSET = 8
_TILE_GAP = 16
_TILE_ROUNDNESS = 0.06
_TILE_SEGMENTS = 12

# Signal tuning
_MAX_DECEL = 3.5              # m/s² — brake bar saturates here
_DEFAULT_MAX_LAT_ACCEL = 3.0  # m/s² — steering ceiling without CP.maxLateralAccel

# Segmented bar geometry
_N_PAIRS = 5          # color zones (green / lime / yellow / orange / red)
_N_SEGS = _N_PAIRS * 2  # 10 discrete LED blocks per bar (2 blocks per zone)
_SEG_GAP = 4          # px gap between the two blocks within a pair
_PAIR_EXTRA_GAP = 5   # additional px gap between pairs (makes zone boundaries legible)
_BAR_H_PAD = 7        # px horizontal inset inside bar tile
_BAR_V_PAD_TOP = 10   # px top padding above first block
_LABEL_AREA_H = 40    # px reserved at the bottom for the label
_LABEL_FONT_SIZE = 24
_SEG_ROUNDNESS = 0.25
_SEG_ROUND_SEGS = 6
# When the top block of a pair reaches this fill fraction, both blocks snap together
# into one merged rectangle (absorbing the within-pair gap). 0.5 = halfway filled.
_MERGE_THRESHOLD = 0.5
# Cross-zone collapse: when a pair is fully complete, all lower pairs merge into
# one tall block. Starts at yellow (pair index 2) — green/lime do only within-pair
# merge until yellow finishes.
_COLLAPSE_STARTS_AT_PAIR = 2

# Bar panel styling
_BAR_BG_COLOR = rl.Color(20, 20, 20, 255)
_BAR_FRAME_COLOR = rl.Color(62, 62, 62, 130)   # subtle bezel outline
_SEG_OFF_COLOR = rl.Color(42, 42, 42, 255)      # dim "unlit" slot

# LED segment colors: 5 pairs × 2 blocks, index 0 = bottom, 9 = top.
# Each pair shares one flat color. The hue jump between pairs is deliberately
# large so zone transitions are immediately legible — not a smooth gradient.
#
#  Pair 0  blocks 0-1   green   0–20%   comfortable, low risk
#  Pair 1  blocks 2-3   lime    20–40%  elevated, worth a look
#  Pair 2  blocks 4-5   yellow  40–60%  significant activity
#  Pair 3  blocks 6-7   orange  60–80%  high, near limit
#  Pair 4  blocks 8-9   red     80–100% critical
_SEG_ON: tuple[rl.Color, ...] = (
  rl.Color(  0, 228,  82, 255),  # 0  green  \
  rl.Color(  0, 228,  82, 255),  # 1  green  / pair 0
  rl.Color(162, 228,   0, 255),  # 2  lime   \
  rl.Color(162, 228,   0, 255),  # 3  lime   / pair 1
  rl.Color(255, 215,   0, 255),  # 4  yellow \
  rl.Color(255, 215,   0, 255),  # 5  yellow / pair 2
  rl.Color(255,  85,   0, 255),  # 6  orange \
  rl.Color(255,  85,   0, 255),  # 7  orange / pair 3
  rl.Color(232,   0,  52, 255),  # 8  red    \
  rl.Color(232,   0,  52, 255),  # 9  red    / pair 4
)

_BORDER_COLORS = {
  UIStatus.DISENGAGED: rl.Color(0x12, 0x28, 0x39, 0xFF),
  UIStatus.OVERRIDE:   rl.Color(0x89, 0x92, 0x8D, 0xFF),
  UIStatus.ENGAGED:    rl.Color(0x16, 0x7F, 0x40, 0xFF),
}


def _block_top_y(block_idx: int, seg_h: float, seg_area_bottom: float) -> float:
  """Top screen-y of a block. Block 0 is at the bottom; block 9 is at the top.

  Each block is separated from its neighbor by _SEG_GAP. At every pair boundary
  (every 2 blocks) an additional _PAIR_EXTRA_GAP is added. This is the single
  source of truth for all block positioning — used both for individual blocks and
  for computing collapsed-zone geometry.
  """
  pair_idx = block_idx // 2
  return (seg_area_bottom
          - (block_idx + 1) * seg_h
          - block_idx * _SEG_GAP
          - pair_idx * _PAIR_EXTRA_GAP)


def _blend_seg(on: rl.Color, fill: float) -> rl.Color:
  """Linearly interpolate a segment between off-color (fill=0) and on-color (fill=1)."""
  off = _SEG_OFF_COLOR
  return rl.Color(
    int(off.r + fill * (on.r - off.r)),
    int(off.g + fill * (on.g - off.g)),
    int(off.b + fill * (on.b - off.b)),
    255,
  )


class DACView(Widget):
  def __init__(self):
    super().__init__()
    dt = 1.0 / gui_app.target_fps
    self._steer_filter = FirstOrderFilter(0.0, 0.1, dt)   # RC from research notes
    self._brake_filter = FirstOrderFilter(0.0, 0.15, dt)
    # DM stores *distraction risk* (1 - awarenessStatus), so 0.0 = safe, 1.0 = terminal
    self._dm_filter = FirstOrderFilter(0.0, 0.5, dt)
    self._font = gui_app.font(FontWeight.BOLD)

  def _update_state(self) -> None:
    sm = ui_state.sm

    # --- Steering bar: mirrors torque_bar.py angle vs torque branching ---
    if sm['controlsState'].lateralControlState.which() == 'angleState':
      controls_state = sm['controlsState']
      car_state = sm['carState']
      live_params = sm['liveParameters']
      car_control = sm['carControl']

      actual_lat_accel = controls_state.curvature * car_state.vEgo ** 2
      desired_lat_accel = controls_state.desiredCurvature * car_state.vEgo ** 2
      accel_diff = desired_lat_accel - actual_lat_accel

      # Roll compensation ramps in between 5–15 m/s (noisy near standstill)
      roll_comp = (live_params.roll * ACCELERATION_DUE_TO_GRAVITY *
                   float(np.interp(car_state.vEgo, [5, 15], [0.0, 1.0])))
      lateral_accel = actual_lat_accel - roll_comp
      max_lat_accel = ui_state.CP.maxLateralAccel if ui_state.CP else _DEFAULT_MAX_LAT_ACCEL

      if not car_control.latActive:
        self._steer_filter.update(0.0)
      else:
        self._steer_filter.update(
          float(np.clip(abs(lateral_accel + accel_diff) / max_lat_accel, 0.0, 1.0))
        )
    else:
      # Torque-based cars: normalized EPS command, strip direction with abs()
      self._steer_filter.update(abs(sm['carOutput'].actuatorsOutput.torque))

    # --- Brake bar: present-tense deceleration via aEgo ---
    a_ego = sm['carState'].aEgo
    self._brake_filter.update(min(max(-a_ego, 0.0) / _MAX_DECEL, 1.0))

    # --- DM bar: awarenessStatus 1.0=attentive → risk 0.0, 0.0=terminal → risk 1.0 ---
    awareness = sm['driverMonitoringState'].awarenessStatus
    self._dm_filter.update(1.0 - max(min(awareness, 1.0), 0.0))

  def _render(self, rect: rl.Rectangle) -> None:
    rl.draw_rectangle_rec(rect, _DAC_BG_COLOR)
    self._draw_tiles(self._content_rect(rect))

  def _content_rect(self, rect: rl.Rectangle) -> rl.Rectangle:
    inset = _BORDER_SIZE + _CONTENT_INSET
    return rl.Rectangle(
      rect.x + inset,
      rect.y + inset,
      rect.width - 2 * inset,
      rect.height - 2 * inset,
    )

  def _draw_tiles(self, rect: rl.Rectangle) -> None:
    gap = _TILE_GAP

    left_group_w = rect.width * 0.42
    right_group_w = rect.width - left_group_w - gap

    tall_tile_w = (left_group_w - 2 * gap) / 3
    tall_tile_h = rect.height

    top_right_h = rect.height * 0.34
    bottom_right_h = rect.height - top_right_h - gap
    bottom_tile_w = (right_group_w - gap) / 2

    bar_rects = (
      rl.Rectangle(rect.x, rect.y, tall_tile_w, tall_tile_h),
      rl.Rectangle(rect.x + tall_tile_w + gap, rect.y, tall_tile_w, tall_tile_h),
      rl.Rectangle(rect.x + 2 * (tall_tile_w + gap), rect.y, tall_tile_w, tall_tile_h),
    )

    placeholder_rects = (
      rl.Rectangle(rect.x + left_group_w + gap, rect.y, right_group_w, top_right_h),
      rl.Rectangle(rect.x + left_group_w + gap, rect.y + top_right_h + gap, bottom_tile_w, bottom_right_h),
      rl.Rectangle(rect.x + left_group_w + gap + bottom_tile_w + gap, rect.y + top_right_h + gap,
                   bottom_tile_w, bottom_right_h),
    )

    bar_configs = (
      (self._steer_filter.x, "S"),
      (self._brake_filter.x, "B"),
      (self._dm_filter.x,    "DM"),
    )

    for tile_rect, (level, label) in zip(bar_rects, bar_configs, strict=True):
      self._draw_segment_bar(tile_rect, level, label)

    for tile_rect in placeholder_rects:
      rl.draw_rectangle_rounded(tile_rect, _TILE_ROUNDNESS, _TILE_SEGMENTS, _PLACEHOLDER_TILE_COLOR)

  def _draw_segment_bar(self, rect: rl.Rectangle, level: float, label: str) -> None:
    rl.draw_rectangle_rounded(rect, _TILE_ROUNDNESS, _TILE_SEGMENTS, _BAR_BG_COLOR)
    rl.draw_rectangle_rounded_lines_ex(rect, _TILE_ROUNDNESS, _TILE_SEGMENTS, 1.5, _BAR_FRAME_COLOR)

    seg_x = rect.x + _BAR_H_PAD
    seg_w = rect.width - 2 * _BAR_H_PAD

    # The block stack occupies the space between the top padding and the label area.
    # seg_area_h is the height available for blocks + gaps.
    # seg_area_bottom is the y-coordinate of the bottom edge of block 0 (the baseline).
    seg_area_h = rect.height - _BAR_V_PAD_TOP - _LABEL_AREA_H
    seg_area_bottom = rect.y + _BAR_V_PAD_TOP + seg_area_h

    # Divide the available height evenly: 10 blocks + 9 within-pair gaps + 4 between-pair gaps
    total_gap_h = (_N_SEGS - 1) * _SEG_GAP + (_N_PAIRS - 1) * _PAIR_EXTRA_GAP
    seg_h = max(4.0, (seg_area_h - total_gap_h) / _N_SEGS)

    n_lit = level * _N_SEGS  # float block count (0.0 – 10.0)

    # --- Cross-zone collapse ---
    # When a pair is fully complete (n_lit >= 2*(pair+1)), all pairs from 0 up to
    # that pair collapse into one tall block using the topmost completed pair's color.
    # Collapse only starts at _COLLAPSE_STARTS_AT_PAIR (yellow, pair 2).
    #
    #  n_lit ≥ 6   yellow done  → pairs 0+1+2 → one yellow block
    #  n_lit ≥ 8   orange done  → pairs 0+1+2+3 → one orange block
    #  n_lit ≥ 10  red done     → all 5 pairs → one red block
    collapse_until = -1
    for p in range(_COLLAPSE_STARTS_AT_PAIR, _N_PAIRS):
      if n_lit >= 2 * (p + 1):
        collapse_until = p

    if collapse_until >= 0:
      # The collapsed block's top is the top of its highest block; its bottom is
      # the bottom of block 0 — which is exactly seg_area_bottom by definition.
      top_block_of_collapse = 2 * (collapse_until + 1) - 1  # = highest block index
      c_y = _block_top_y(top_block_of_collapse, seg_h, seg_area_bottom)
      c_h = seg_area_bottom - c_y                            # extends to the bottom of the stack
      rl.draw_rectangle_rounded(
        rl.Rectangle(seg_x, c_y, seg_w, c_h),
        _SEG_ROUNDNESS, _SEG_ROUND_SEGS,
        _SEG_ON[top_block_of_collapse],
      )
      first_normal_pair = collapse_until + 1
    else:
      first_normal_pair = 0

    for pair in range(first_normal_pair, _N_PAIRS):
      self._draw_pair(pair, n_lit, seg_h, seg_x, seg_w, seg_area_bottom)

    # Label centered in the reserved area below the segments
    text_size = measure_text_cached(self._font, label, _LABEL_FONT_SIZE)
    label_cx = rect.x + rect.width / 2
    label_cy = rect.y + rect.height - _LABEL_AREA_H / 2
    rl.draw_text_ex(
      self._font, label,
      rl.Vector2(int(label_cx - text_size.x / 2), int(label_cy - text_size.y / 2)),
      _LABEL_FONT_SIZE, 0,
      rl.Color(155, 155, 155, 255),
    )

  def _draw_pair(self, pair: int, n_lit: float, seg_h: float,
                 seg_x: float, seg_w: float, seg_area_bottom: float) -> None:
    """Draw one pair of blocks. Within-pair snap occurs when top block crosses _MERGE_THRESHOLD."""
    i_bot = pair * 2
    i_top = pair * 2 + 1
    color = _SEG_ON[i_bot]  # both blocks in a pair share one color

    bot_fill = min(max(n_lit - i_bot, 0.0), 1.0)
    top_fill = min(max(n_lit - i_top, 0.0), 1.0)

    bot_y = _block_top_y(i_bot, seg_h, seg_area_bottom)
    top_y = _block_top_y(i_top, seg_h, seg_area_bottom)

    if top_fill >= _MERGE_THRESHOLD:
      # Snap both blocks into one rect, absorbing the within-pair gap
      rl.draw_rectangle_rounded(
        rl.Rectangle(seg_x, top_y, seg_w, 2 * seg_h + _SEG_GAP),
        _SEG_ROUNDNESS, _SEG_ROUND_SEGS, color,
      )
    else:
      rl.draw_rectangle_rounded(
        rl.Rectangle(seg_x, bot_y, seg_w, seg_h),
        _SEG_ROUNDNESS, _SEG_ROUND_SEGS, _blend_seg(color, bot_fill),
      )
      rl.draw_rectangle_rounded(
        rl.Rectangle(seg_x, top_y, seg_w, seg_h),
        _SEG_ROUNDNESS, _SEG_ROUND_SEGS, _blend_seg(color, top_fill),
      )

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

import time
from collections.abc import Callable

import numpy as np
import pyray as rl

from opendbc.car import ACCELERATION_DUE_TO_GRAVITY
from openpilot.common.constants import CV
from openpilot.common.filter_simple import BounceFilter, FirstOrderFilter
from openpilot.common.params import Params
from openpilot.selfdrive.ui.ui_state import ui_state, UIStatus
from openpilot.system.ui.lib.application import FontWeight, gui_app
from openpilot.system.ui.lib.multilang import tr
from openpilot.system.ui.lib.text_measure import measure_text_cached
from openpilot.system.ui.widgets import DialogResult, Widget
from openpilot.system.ui.widgets.confirm_dialog import ConfirmDialog


# DAC view: solid background + tici-style status border.
#
# The 3 tall left tiles are live LED-style segmented signal bars:
#   STR — steering utilization  (higher = working harder, closer to saturation)
#   BRK — braking intensity     (higher = harder decel)
#   DM  — driver distraction    (higher = less aware, closer to takeover event)
#
# All three bars read the same way: height = disengage risk contribution.
# The top-right tile is split into bookmark button + retro speedometer.
# The lower-left tile toggles chill/experimental mode; the lower-right remains a placeholder.

_DAC_BG_COLOR = rl.BLACK
_PLACEHOLDER_TILE_COLOR = rl.Color(210, 210, 210, 255)

_BORDER_SIZE = 7
_BORDER_ROUNDNESS = 0.11
_BORDER_SEGMENTS = 24

_CONTENT_INSET = 8
_TILE_GAP = 16
_TILE_ROUNDNESS = 0.06
_TILE_SEGMENTS = 12
_LEFT_GROUP_WIDTH_SHRINK = 5
_RIGHT_TOP_HEIGHT_RATIO = 0.34
_RIGHT_TOP_HEIGHT_BOOST = 59
_SPEED_TEXT_BASELINE_OFFSET = -6
_TOP_ROW_NUMBER_CENTER_Y_RATIO = 0.62
_SPEEDO_VALUE_Y_OFFSET = 9

# Signal tuning
_MAX_DECEL = 3.5              # m/s² — brake bar saturates here
_DEFAULT_MAX_LAT_ACCEL = 3.0  # m/s² — steering ceiling without CP.maxLateralAccel
_STEER_PROB_SENSITIVITY_CEILING = 0.5  # S bar saturates at 50% raw steerOverrideProb
_STEER_BLEND_DOMINANT_WEIGHT = 0.65    # weighted blend: emphasize earliest/strongest source
_STEER_BLEND_AGREEMENT_WEIGHT = 0.35   # weighted blend: add confidence when both sources agree

# Right-top layout
_RIGHT_TOP_SPLIT_GAP = 13
_RIGHT_ROW_GAP = _RIGHT_TOP_SPLIT_GAP
_BOOKMARK_WIDTH_RATIO = 0.2
_BOOKMARK_WIDTH_BOOST = 14
_BOTTOM_ROW_GAP = 13

# Bookmark tile layout
_BOOKMARK_ICON_PAD = 18
_BOOKMARK_ICON_SIZE_BOOST = 8
_BOOKMARK_FILLED_LINGER_S = 0.48
_BOOKMARK_ICON_TINT = rl.Color(205, 205, 205, 255) # TODO: use dimmer png

# Experimental tile layout
_EXP_TILE_ICON_PAD = 10
_EXP_TILE_STATE_HOLD_S = 2.0
_EXP_TILE_ACTIVE_SCALE_BOOST = 0.03

# DM bar: awareness zone thresholds and label colors
_DM_AWARENESS_PRE_ALERT = 0.727   # green pre-alert starts here (8s remaining)
_DM_AWARENESS_PROMPT = 0.545      # orange prompt starts here (6s remaining)
_DM_AWARENESS_PRE_ALERT_PASSIVE = 0.5
_DM_AWARENESS_PROMPT_PASSIVE = 0.2
_DM_LABEL_DEFAULT_COLOR = rl.Color(155, 155, 155, 255)
_DM_LABEL_GREEN = rl.Color(0, 228, 82, 255)
_DM_LABEL_YELLOW = rl.Color(255, 215, 0, 255)
_DM_LABEL_ORANGE = rl.Color(255, 85, 0, 255)
_DM_LABEL_RED = rl.Color(232, 0, 52, 255)
_DM_ICON_SIZE = 28                # px — fits within _LABEL_AREA_H (40px)
_DM_ICON_SWITCH_DELAY_S = 0.18    # require a brief stable classification before swapping icons

# Lead tile layout
_LEAD_TILE_ICON_LEFT_PAD = 11
_LEAD_TILE_ICON_RIGHT_PAD = 10
_LEAD_TILE_ICON_PAD_Y = 10
_LEAD_TILE_DOT_RADIUS = 6
_LEAD_TILE_DOT_RIGHT_PAD = 26
_LEAD_TILE_DOT_STROKE = 2

# Speedometer layout
_SPEEDO_PANEL_PAD_X = 14
_SPEEDO_PANEL_PAD_Y = 8
_SPEEDO_SEGMENTS = 42
_SPEEDO_SEG_GAP = 2
_SPEEDO_RED_ZONE_START_RATIO = 0.8
_SPEEDO_SWEEP_TOP_INSET = 1
_SPEEDO_SWEEP_HEIGHT_RATIO = 0.15
_SPEEDO_VALUE_MIN_SIZE = 80
_SPEEDO_READOUT_X_OFFSET = -15
_SPEEDO_UNIT_X_GAP = -19

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
_RETRO_PANEL_BG = rl.Color(24, 24, 24, 255)
_SPEEDO_PANEL_BG = rl.BLACK
_RETRO_PANEL_GLOW = rl.Color(230, 230, 230, 255)
_RETRO_PANEL_GLOW_DIM = rl.Color(120, 88, 32, 255)
_RETRO_TEXT_DIM = rl.Color(185, 150, 90, 255)

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


def _top_row_number_center_y(rect: rl.Rectangle) -> float:
  """Shared vertical anchor for the set-speed and speedometer values."""
  return rect.y + rect.height * _TOP_ROW_NUMBER_CENTER_Y_RATIO


def _rounded_corner_radius_px(rect: rl.Rectangle, roundness: float) -> float:
  return max(0.0, min(rect.width, rect.height) * roundness * 0.5)


def _roundness_for_radius(rect: rl.Rectangle, radius_px: float) -> float:
  min_dim = min(rect.width, rect.height)
  if min_dim <= 0.0:
    return 0.0
  return max(0.0, min(1.0, (2.0 * radius_px) / min_dim))


def _experimental_mode_dialog_content() -> str:
  return (
    f"<h1>{tr('Experimental Mode')}</h1><br>"
    + tr(
      "openpilot defaults to driving in chill mode. Experimental mode enables alpha-level features that aren't "
      + "ready for chill mode. Experimental features are listed below:"
    )
    + "<br>"
    + f"<h4>{tr('End-to-End Longitudinal Control')}</h4><br>"
    + tr(
      "Let the driving model control the gas and brakes. openpilot will drive as it thinks a human would, "
      + "including stopping for red lights and stop signs. Since the driving model decides the speed to drive, "
      + "the set speed will only act as an upper bound. This is an alpha quality feature; mistakes should be expected."
    )
    + "<br>"
    + f"<h4>{tr('New Driving Visualization')}</h4><br>"
    + tr(
      "The driving visualization will transition to the road-facing wide-angle camera at low speeds to better "
      + "show some turns. The Experimental mode logo will also be shown in the top right corner."
    )
  )


class BookmarkTileButton(Widget):
  def __init__(self, click_callback: Callable[[], None] | None):
    super().__init__()
    self._click_delay = 0.075
    self.set_click_callback(click_callback)
    self._outline_texture = gui_app.texture("icons_dac/bookmark-1.png", 48, 48)
    self._filled_texture = gui_app.texture("icons_dac/bookmark-filled-1.png", 48, 48)
    dt = 1.0 / gui_app.target_fps
    self._scale_filter = BounceFilter(1.0, 0.1, dt)
    self._filled_alpha_filter = FirstOrderFilter(0.0, 0.06, dt)
    self._filled_linger_until = 0.0

  def activate(self) -> None:
    self._filled_linger_until = rl.get_time() + _BOOKMARK_FILLED_LINGER_S
    if self._click_delay is not None:
      self._click_release_time = rl.get_time() + self._click_delay
    if self._click_callback:
      self._click_callback()

  def _handle_mouse_release(self, mouse_pos) -> None:
    self.activate()

  def _render(self, rect: rl.Rectangle) -> None:
    bg = rl.Color(34, 34, 34, 255) if self.is_pressed else _RETRO_PANEL_BG
    rl.draw_rectangle_rounded(rect, _TILE_ROUNDNESS, _TILE_SEGMENTS, bg)
    rl.draw_rectangle_rounded_lines_ex(rect, _TILE_ROUNDNESS, _TILE_SEGMENTS, 1.5, _BAR_FRAME_COLOR)

    scale_anim = self._scale_filter.update(0.94 if self.is_pressed else 1.0)
    filled_active = self.is_pressed or rl.get_time() < self._filled_linger_until
    filled_alpha = self._filled_alpha_filter.update(1.0 if filled_active else 0.0)

    icon_w = max(1.0, rect.width - 2 * _BOOKMARK_ICON_PAD + _BOOKMARK_ICON_SIZE_BOOST)
    icon_h = max(1.0, rect.height - 2 * _BOOKMARK_ICON_PAD + _BOOKMARK_ICON_SIZE_BOOST)
    base_scale = min(icon_w / self._outline_texture.width, icon_h / self._outline_texture.height)
    scale = base_scale * scale_anim
    draw_w = self._outline_texture.width * scale
    draw_h = self._outline_texture.height * scale
    draw_x = rect.x + (rect.width - draw_w) / 2
    draw_y = rect.y + (rect.height - draw_h) / 2

    outline_alpha = int(255 * (1.0 - filled_alpha))
    filled_alpha_int = int(255 * filled_alpha)
    if outline_alpha > 0:
      rl.draw_texture_ex(
        self._outline_texture, rl.Vector2(draw_x, draw_y), 0.0, scale,
        rl.Color(_BOOKMARK_ICON_TINT.r, _BOOKMARK_ICON_TINT.g, _BOOKMARK_ICON_TINT.b, outline_alpha)
      )
    if filled_alpha_int > 0:
      rl.draw_texture_ex(
        self._filled_texture, rl.Vector2(draw_x, draw_y), 0.0, scale,
        rl.Color(_BOOKMARK_ICON_TINT.r, _BOOKMARK_ICON_TINT.g, _BOOKMARK_ICON_TINT.b, filled_alpha_int)
      )


class ExperimentalModeTileButton(Widget):
  def __init__(self):
    super().__init__()
    self._click_delay = 0.075
    self._params = Params()
    self._actual_mode = False
    self._requested_mode = False
    self._held_mode: bool | None = None
    self._hold_end_time: float | None = None
    self._experimental_texture = gui_app.texture("icons/experimental.png", 144, 144)
    dt = 1.0 / gui_app.target_fps
    self._scale_filter = BounceFilter(1.0, 0.1, dt)
    self._enabled_filter = FirstOrderFilter(0.0, 0.08, dt)

    # Match current system gating: experimental mode only exists when OP longitudinal is available.
    self.set_enabled(lambda: ui_state.has_longitudinal_control)

  def _update_state(self) -> None:
    self._actual_mode = ui_state.sm["selfdriveState"].experimentalMode
    self._requested_mode = self._params.get_bool("ExperimentalMode")

  def _handle_mouse_release(self, mouse_pos) -> None:
    super()._handle_mouse_release(mouse_pos)

    if self._visual_mode():
      self._apply_mode(False)
      return

    if self._params.get_bool("ExperimentalModeConfirmed"):
      self._apply_mode(True)
      return

    self._show_confirm_dialog()

  def _apply_mode(self, enabled: bool) -> None:
    self._params.put_bool("ExperimentalMode", enabled)
    self._held_mode = enabled
    self._hold_end_time = time.monotonic() + _EXP_TILE_STATE_HOLD_S

  def _visual_mode(self) -> bool:
    # Prefer the temporary held state right after a click, then fall back to the
    # requested param while the backend catches up, and finally settle on actual state.
    now = time.monotonic()
    if self._hold_end_time is not None:
      if now < self._hold_end_time and self._held_mode is not None:
        return self._held_mode
      self._hold_end_time = None
      self._held_mode = None
    if self._requested_mode != self._actual_mode:
      return self._requested_mode
    return self._actual_mode

  def _show_confirm_dialog(self) -> None:
    def confirm_callback(result: DialogResult) -> None:
      if result == DialogResult.CONFIRM:
        self._params.put_bool("ExperimentalModeConfirmed", True)
        self._apply_mode(True)

    gui_app.push_widget(ConfirmDialog(_experimental_mode_dialog_content(), tr("Enable"), rich=True, callback=confirm_callback))

  def _render(self, rect: rl.Rectangle) -> None:
    enabled_visual = self._enabled_filter.update(1.0 if self._visual_mode() else 0.0)
    scale_anim = self._scale_filter.update(0.94 if self.is_pressed else 1.0)

    bg = rl.Color(30, 30, 30, 255) if self.is_pressed else _RETRO_PANEL_BG
    if enabled_visual > 0.5:
      bg = rl.Color(30, 20, 16, 255) if self.is_pressed else rl.Color(24, 18, 16, 255)

    rl.draw_rectangle_rounded(rect, _TILE_ROUNDNESS, _TILE_SEGMENTS, bg)
    if enabled_visual > 0.0:
      glow = rl.Color(255, 112, 36, int(45 * enabled_visual))
      rl.draw_rectangle_rounded(rect, _TILE_ROUNDNESS, _TILE_SEGMENTS, glow)

    border_color = rl.Color(
      int(_BAR_FRAME_COLOR.r + enabled_visual * (255 - _BAR_FRAME_COLOR.r)),
      int(_BAR_FRAME_COLOR.g + enabled_visual * (122 - _BAR_FRAME_COLOR.g)),
      int(_BAR_FRAME_COLOR.b + enabled_visual * (62 - _BAR_FRAME_COLOR.b)),
      int(_BAR_FRAME_COLOR.a + enabled_visual * (235 - _BAR_FRAME_COLOR.a)),
    )
    rl.draw_rectangle_rounded_lines_ex(rect, _TILE_ROUNDNESS, _TILE_SEGMENTS, 1.5 + enabled_visual, border_color)

    content_alpha = 255 if self.enabled else 115
    self._draw_experimental_icon(rect, content_alpha, enabled_visual, scale_anim)

  def _draw_experimental_icon(self, rect: rl.Rectangle, alpha: int, enabled_visual: float, scale_anim: float) -> None:
    icon_w = max(1.0, rect.width - 2 * _EXP_TILE_ICON_PAD)
    icon_h = max(1.0, rect.height - 2 * _EXP_TILE_ICON_PAD)
    base_scale = min(icon_w / self._experimental_texture.width, icon_h / self._experimental_texture.height)
    scale = base_scale * scale_anim * (1.0 + enabled_visual * _EXP_TILE_ACTIVE_SCALE_BOOST)
    draw_w = self._experimental_texture.width * scale
    draw_h = self._experimental_texture.height * scale
    draw_x = rect.x + (rect.width - draw_w) / 2
    draw_y = rect.y + (rect.height - draw_h) / 2

    off_tint = rl.Color(132, 132, 132, int(alpha * (0.74 - 0.32 * enabled_visual)))
    on_tint = rl.Color(255, 255, 255, int(alpha * enabled_visual))

    rl.draw_texture_ex(
      self._experimental_texture,
      rl.Vector2(draw_x, draw_y),
      0.0,
      scale,
      off_tint,
    )
    if on_tint.a > 0:
      rl.draw_texture_ex(
        self._experimental_texture,
        rl.Vector2(draw_x, draw_y),
        0.0,
        scale,
        on_tint,
      )


class LeadCarTile(Widget):
  def __init__(self):
    super().__init__()
    self._lead_detected = False
    dt = 1.0 / gui_app.target_fps
    self._lead_filter = FirstOrderFilter(0.0, 0.08, dt)
    self._lead_texture = gui_app.texture("icons_dac/lead-car.png", 96, 96)

  def _update_state(self) -> None:
    radar_state = ui_state.sm['radarState'] if ui_state.sm.valid['radarState'] else None
    leads = (radar_state.leadOne, radar_state.leadTwo) if radar_state is not None else ()
    self._lead_detected = any(lead.status for lead in leads)

  def _render(self, rect: rl.Rectangle) -> None:
    self._draw_panel(rect)
    lead_visual = self._lead_filter.update(1.0 if self._lead_detected else 0.0)
    self._draw_icon_and_indicator(rect, lead_visual)

  def _draw_panel(self, rect: rl.Rectangle) -> None:
    bg = rl.Color(22, 22, 22, 255)
    rl.draw_rectangle_rounded(rect, _TILE_ROUNDNESS, _TILE_SEGMENTS, bg)
    rl.draw_rectangle_rounded_lines_ex(rect, _TILE_ROUNDNESS, _TILE_SEGMENTS, 1.5, _BAR_FRAME_COLOR)

  def _draw_icon_and_indicator(self, rect: rl.Rectangle, lead_visual: float) -> None:
    icon_w = max(1.0, rect.width - _LEAD_TILE_ICON_LEFT_PAD - _LEAD_TILE_ICON_RIGHT_PAD)
    icon_h = max(1.0, rect.height - 2 * _LEAD_TILE_ICON_PAD_Y)
    base_scale = min(icon_w / self._lead_texture.width, icon_h / self._lead_texture.height)
    icon_x = rect.x + _LEAD_TILE_ICON_LEFT_PAD
    icon_y = rect.y + (rect.height - self._lead_texture.height * base_scale) / 2

    off_tint = rl.Color(118, 118, 118, 220)
    on_tint = rl.Color(255, 255, 255, int(255 * lead_visual))
    rl.draw_texture_ex(self._lead_texture, rl.Vector2(icon_x, icon_y), 0.0, base_scale, off_tint)
    if on_tint.a > 0:
      rl.draw_texture_ex(self._lead_texture, rl.Vector2(icon_x, icon_y), 0.0, base_scale, on_tint)

    dot_cx = rect.x + rect.width - _LEAD_TILE_DOT_RIGHT_PAD - _LEAD_TILE_DOT_RADIUS
    dot_cy = rect.y + rect.height / 2
    dot_off = rl.Color(118, 118, 118, 255)
    dot_on = rl.Color(255, 255, 255, int(255 * lead_visual))
    rl.draw_ring(
      rl.Vector2(int(dot_cx), int(dot_cy)),
      _LEAD_TILE_DOT_RADIUS - _LEAD_TILE_DOT_STROKE,
      _LEAD_TILE_DOT_RADIUS,
      0,
      360,
      24,
      dot_off,
    )
    if dot_on.a > 0:
      rl.draw_circle(int(dot_cx), int(dot_cy), _LEAD_TILE_DOT_RADIUS + 7 * lead_visual, rl.Color(255, 255, 255, int(42 * lead_visual)))
      rl.draw_circle(int(dot_cx), int(dot_cy), _LEAD_TILE_DOT_RADIUS, dot_on)


class DACView(Widget):
  def __init__(self, bookmark_callback: Callable[[], None] | None = None):
    super().__init__()
    dt = 1.0 / gui_app.target_fps
    self._steer_prediction_filter = FirstOrderFilter(0.0, 0.5, dt)  # S: model steerOverrideProb
    self._steer_actuator_filter = FirstOrderFilter(0.0, 0.1, dt)    # SA: steering utilization
    self._brake_filter = FirstOrderFilter(0.0, 0.15, dt)
    # DM stores *distraction risk* (1 - awarenessStatus), so 0.0 = safe, 1.0 = terminal
    self._dm_filter = FirstOrderFilter(0.0, 0.5, dt)
    self._speed = 0.0
    self._speed_display_filter = FirstOrderFilter(0.0, 0.12, dt)
    self._v_ego_cluster_seen = False
    self._bookmark_button = self._child(BookmarkTileButton(bookmark_callback))
    self._experimental_button = self._child(ExperimentalModeTileButton())
    self._lead_tile = self._child(LeadCarTile())
    self._bookmark_hit_rect = rl.Rectangle()

    self._dm_awareness = 1.0
    self._dm_distracted_type = 0
    self._dm_is_active_mode = True
    self._dm_step_change = 0.0
    self._dm_event_name = ""
    self._dm_standstill = False
    self._dm_visual_icon_bit = 0
    self._dm_pending_icon_bit = 0
    self._dm_icon_switch_at: float | None = None
    self._dm_icons = {
      4: gui_app.texture("icons_dac/phone.png", _DM_ICON_SIZE, _DM_ICON_SIZE),
      2: gui_app.texture("icons/eye_closed.png", _DM_ICON_SIZE, _DM_ICON_SIZE),
      1: gui_app.texture("icons_mici/onroad/driver_monitoring/dm_person.png", _DM_ICON_SIZE, _DM_ICON_SIZE),
    }

    self._font = gui_app.font(FontWeight.BOLD)
    self._font_medium = gui_app.font(FontWeight.MEDIUM)
    self._font_display = gui_app.font(FontWeight.DISPLAY)

  def _update_state(self) -> None:
    sm = ui_state.sm
    controls_state = sm['controlsState']
    car_state = sm['carState']

    self._update_steering_warning_state(sm, controls_state, car_state)

    # --- Brake bar: present-tense deceleration via aEgo ---
    a_ego = car_state.aEgo
    self._brake_filter.update(min(max(-a_ego, 0.0) / _MAX_DECEL, 1.0))

    # --- DM bar: awarenessStatus 1.0=attentive → risk 0.0, 0.0=terminal → risk 1.0 ---
    dm_state = sm['driverMonitoringState']
    awareness = dm_state.awarenessStatus
    self._dm_filter.update(1.0 - max(min(awareness, 1.0), 0.0))
    self._dm_awareness = max(min(awareness, 1.0), 0.0)
    self._dm_distracted_type = dm_state.distractedType
    self._dm_is_active_mode = dm_state.isActiveMode
    self._dm_step_change = dm_state.stepChange
    self._dm_event_name = str(dm_state.events[0].name).split('.')[-1] if len(dm_state.events) else ""
    self._dm_standstill = car_state.standstill
    self._update_dm_visual_icon()

    self._update_speed_state(car_state)

  def _render(self, rect: rl.Rectangle) -> None:
    self.draw_status_border(rect)
    self._draw_tiles(self._content_rect(rect))

  def _handle_mouse_release(self, mouse_pos) -> None:
    super()._handle_mouse_release(mouse_pos)
    if rl.check_collision_point_rec(mouse_pos, self._bookmark_hit_rect):
      self._bookmark_button.activate()

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

    left_group_w = rect.width * 0.42 - _LEFT_GROUP_WIDTH_SHRINK
    right_group_w = rect.width - left_group_w - gap

    tall_tile_w = (left_group_w - 2 * gap) / 3
    tall_tile_h = rect.height

    top_right_h = rect.height * _RIGHT_TOP_HEIGHT_RATIO + _RIGHT_TOP_HEIGHT_BOOST
    bottom_right_h = rect.height - top_right_h - _RIGHT_ROW_GAP
    bottom_tile_w = (right_group_w - _BOTTOM_ROW_GAP) / 2

    bar_rects = (
      rl.Rectangle(rect.x, rect.y, tall_tile_w, tall_tile_h),
      rl.Rectangle(rect.x + tall_tile_w + gap, rect.y, tall_tile_w, tall_tile_h),
      rl.Rectangle(rect.x + 2 * (tall_tile_w + gap), rect.y, tall_tile_w, tall_tile_h),
    )

    top_right_rect = rl.Rectangle(rect.x + left_group_w + gap, rect.y, right_group_w, top_right_h)
    bottom_row_y = rect.y + top_right_h + _RIGHT_ROW_GAP
    bottom_row_x = rect.x + left_group_w + gap
    bottom_rects = (
      rl.Rectangle(bottom_row_x, bottom_row_y, bottom_tile_w, bottom_right_h),
      rl.Rectangle(bottom_row_x + bottom_tile_w + _BOTTOM_ROW_GAP, bottom_row_y, bottom_tile_w, bottom_right_h),
    )

    dm_label, dm_label_color, dm_icon = self._dm_bar_label_info()

    bar_configs = (
      (self._combined_steering_warning(), "S",       None, None, None),
      (self._brake_filter.x, "B",       None, None, None),
      (self._dm_filter.x,    dm_label,  dm_label_color, dm_icon, None),
    )

    for tile_rect, (level, label, label_color, icon, segment_color) in zip(bar_rects, bar_configs, strict=True):
      self._draw_segment_bar(tile_rect, level, label, label_color, icon, segment_color)

    bookmark_rect, speedo_rect = self._split_top_right_rect(top_right_rect)
    self._bookmark_hit_rect = top_right_rect

    self._bookmark_button.render(bookmark_rect)
    self._draw_speedometer_tile(speedo_rect)

    self._experimental_button.render(bottom_rects[0])
    self._lead_tile.render(bottom_rects[1])

  def _update_steering_warning_state(self, sm, controls_state, car_state) -> None:
    """Combine model-predicted steer takeover risk (S) and actuator effort (SA).

    S uses the same stock signal as the classic bar: max steerOverrideProbs, which is
    effectively the 10-second cumulative steer override probability.
    SA is the current steering actuator utilization: torque on torque-control cars,
    or normalized lateral-accel effort on angle-control cars.
    """
    self._steer_prediction_filter.update(self._raw_steer_override_probability(sm))
    self._steer_actuator_filter.update(self._steer_actuator_utilization(sm, controls_state, car_state))

  def _raw_steer_override_probability(self, sm) -> float:
    steer_probs = sm['modelV2'].meta.disengagePredictions.steerOverrideProbs or [0.0]
    return float(max(steer_probs))

  def _normalized_steer_prediction(self) -> float:
    return float(np.clip(self._steer_prediction_filter.x / _STEER_PROB_SENSITIVITY_CEILING, 0.0, 1.0))

  def _steer_actuator_utilization(self, sm, controls_state, car_state) -> float:
    if controls_state.lateralControlState.which() == 'angleState':
      live_params = sm['liveParameters']
      car_control = sm['carControl']

      if not car_control.latActive:
        return 0.0

      actual_lat_accel = controls_state.curvature * car_state.vEgo ** 2
      desired_lat_accel = controls_state.desiredCurvature * car_state.vEgo ** 2
      accel_diff = desired_lat_accel - actual_lat_accel

      # Roll compensation ramps in between 5–15 m/s because low-speed roll is noisy.
      roll_comp = (live_params.roll * ACCELERATION_DUE_TO_GRAVITY *
                   float(np.interp(car_state.vEgo, [5, 15], [0.0, 1.0])))
      lateral_accel = actual_lat_accel - roll_comp
      max_lat_accel = ui_state.CP.maxLateralAccel if ui_state.CP else _DEFAULT_MAX_LAT_ACCEL
      return float(np.clip(abs(lateral_accel + accel_diff) / max_lat_accel, 0.0, 1.0))

    # Torque-control cars publish normalized steering effort directly in [-1, 1].
    return float(abs(sm['carOutput'].actuatorsOutput.torque))

  def _combined_steering_warning(self) -> float:
    """Single lateral warning bar using a weighted blend of dominance and agreement.

    We want something calmer than pure max(S, SA), but more responsive than
    product-only overlap logic. So we blend:

      dominant = max(S, SA)
      agreement = S * SA
      combined = 0.65 * dominant + 0.35 * agreement

    This keeps single-source warnings visible while still rewarding convergence
    when both the model and the steering system are elevated.
    """
    steer_prediction = self._normalized_steer_prediction()
    steer_actuator = self._steer_actuator_filter.x
    dominant = max(steer_prediction, steer_actuator)
    agreement = steer_prediction * steer_actuator
    return (
      _STEER_BLEND_DOMINANT_WEIGHT * dominant
      + _STEER_BLEND_AGREEMENT_WEIGHT * agreement
    )

  def _dm_prompt_threshold(self) -> float:
    return _DM_AWARENESS_PROMPT if self._dm_is_active_mode else _DM_AWARENESS_PROMPT_PASSIVE

  def _dm_visual_color(self) -> rl.Color:
    event_name = self._dm_event_name

    if event_name in ("driverDistracted", "driverUnresponsive"):
      return _DM_LABEL_RED
    if event_name in ("promptDriverDistracted", "promptDriverUnresponsive"):
      return _DM_LABEL_ORANGE

    # Match DM policy's "reaching audible" concept: one step before prompt.
    prompt_threshold = self._dm_prompt_threshold()
    about_to_prompt = (
      not self._dm_standstill
      and self._dm_step_change > 0.0
      and self._dm_awareness > prompt_threshold
      and self._dm_awareness - self._dm_step_change <= prompt_threshold
    )
    if about_to_prompt:
      return _DM_LABEL_YELLOW

    if event_name in ("preDriverDistracted", "preDriverUnresponsive"):
      return _DM_LABEL_GREEN

    return _DM_LABEL_DEFAULT_COLOR

  def _dm_target_icon_bit(self) -> int:
    dtype = self._dm_distracted_type
    for bit in (4, 2, 1):
      if dtype & bit:
        return bit
    return 0

  def _update_dm_visual_icon(self) -> None:
    now = time.monotonic()
    target_bit = self._dm_target_icon_bit()

    if target_bit != self._dm_pending_icon_bit:
      self._dm_pending_icon_bit = target_bit
      self._dm_icon_switch_at = now + _DM_ICON_SWITCH_DELAY_S

    if self._dm_icon_switch_at is not None and now >= self._dm_icon_switch_at:
      self._dm_visual_icon_bit = self._dm_pending_icon_bit
      self._dm_icon_switch_at = None

  def _dm_bar_label_info(self):
    """Return (label_text, label_color, icon_texture_or_None) for the DM bar."""
    color = self._dm_visual_color()

    # Priority: PHONE > BLINK > POSE; icon replaces the text label when active
    if self._dm_visual_icon_bit:
      return ("", color, self._dm_icons[self._dm_visual_icon_bit])

    return ("DM", color, None)

  def _draw_segment_bar(self, rect: rl.Rectangle, level: float, label: str,
                        label_color=None, label_icon=None, segment_color=None) -> None:
    rl.draw_rectangle_rounded(rect, _TILE_ROUNDNESS, _TILE_SEGMENTS, _BAR_BG_COLOR)
    rl.draw_rectangle_rounded_lines_ex(rect, _TILE_ROUNDNESS, _TILE_SEGMENTS, 1.5, _BAR_FRAME_COLOR)

    seg_x = rect.x + _BAR_H_PAD
    seg_w = rect.width - 2 * _BAR_H_PAD

    seg_area_h = rect.height - _BAR_V_PAD_TOP - _LABEL_AREA_H
    seg_area_bottom = rect.y + _BAR_V_PAD_TOP + seg_area_h

    total_gap_h = (_N_SEGS - 1) * _SEG_GAP + (_N_PAIRS - 1) * _PAIR_EXTRA_GAP
    seg_h = max(4.0, (seg_area_h - total_gap_h) / _N_SEGS)

    n_lit = level * _N_SEGS

    collapse_until = -1
    for p in range(_COLLAPSE_STARTS_AT_PAIR, _N_PAIRS):
      if n_lit >= 2 * (p + 1):
        collapse_until = p

    if collapse_until >= 0:
      top_block_of_collapse = 2 * (collapse_until + 1) - 1
      c_y = _block_top_y(top_block_of_collapse, seg_h, seg_area_bottom)
      c_h = seg_area_bottom - c_y
      rl.draw_rectangle_rounded(
        rl.Rectangle(seg_x, c_y, seg_w, c_h),
        _SEG_ROUNDNESS, _SEG_ROUND_SEGS,
        segment_color if segment_color is not None else _SEG_ON[top_block_of_collapse],
      )
      first_normal_pair = collapse_until + 1
    else:
      first_normal_pair = 0

    for pair in range(first_normal_pair, _N_PAIRS):
      self._draw_pair(pair, n_lit, seg_h, seg_x, seg_w, seg_area_bottom, segment_color)

    label_cx = rect.x + rect.width / 2
    label_cy = rect.y + rect.height - _LABEL_AREA_H / 2
    color = label_color if label_color is not None else _DM_LABEL_DEFAULT_COLOR

    if label_icon is not None:
      scale = min(_DM_ICON_SIZE / label_icon.width, _DM_ICON_SIZE / label_icon.height)
      draw_w = label_icon.width * scale
      draw_h = label_icon.height * scale
      rl.draw_texture_ex(
        label_icon,
        rl.Vector2(int(label_cx - draw_w / 2), int(label_cy - draw_h / 2)),
        0.0, scale, color,
      )
    elif label:
      text_size = measure_text_cached(self._font, label, _LABEL_FONT_SIZE)
      rl.draw_text_ex(
        self._font, label,
        rl.Vector2(int(label_cx - text_size.x / 2), int(label_cy - text_size.y / 2)),
        _LABEL_FONT_SIZE, 0, color,
      )

  def _draw_pair(self, pair: int, n_lit: float, seg_h: float,
                 seg_x: float, seg_w: float, seg_area_bottom: float,
                 segment_color=None) -> None:
    """Draw one pair of blocks. Within-pair snap occurs when top block crosses _MERGE_THRESHOLD."""
    i_bot = pair * 2
    i_top = pair * 2 + 1
    color = segment_color if segment_color is not None else _SEG_ON[i_bot]

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

  def _update_speed_state(self, car_state) -> None:
    """Mirror hud_renderer's displayed cluster-speed behavior."""
    v_ego_cluster = car_state.vEgoCluster
    self._v_ego_cluster_seen = self._v_ego_cluster_seen or v_ego_cluster != 0.0
    v_ego = v_ego_cluster if self._v_ego_cluster_seen else car_state.vEgo
    speed_conversion = CV.MS_TO_KPH if ui_state.is_metric else CV.MS_TO_MPH
    self._speed = max(0.0, v_ego * speed_conversion)
    self._speed_display_filter.update(self._speed)

  def _split_top_right_rect(self, rect: rl.Rectangle) -> tuple[rl.Rectangle, rl.Rectangle]:
    bookmark_w = (rect.width - _RIGHT_TOP_SPLIT_GAP) * _BOOKMARK_WIDTH_RATIO + _BOOKMARK_WIDTH_BOOST
    speedo_w = rect.width - _RIGHT_TOP_SPLIT_GAP - bookmark_w
    return (
      rl.Rectangle(rect.x, rect.y, bookmark_w, rect.height),
      rl.Rectangle(rect.x + bookmark_w + _RIGHT_TOP_SPLIT_GAP, rect.y, speedo_w, rect.height),
    )

  def _draw_panel(self, rect: rl.Rectangle, bg_color: rl.Color) -> None:
    rl.draw_rectangle_rounded(rect, _TILE_ROUNDNESS, _TILE_SEGMENTS, bg_color)
    rl.draw_rectangle_rounded_lines_ex(rect, _TILE_ROUNDNESS, _TILE_SEGMENTS, 1.5, _BAR_FRAME_COLOR)

  def _draw_placeholder_tile(self, rect: rl.Rectangle) -> None:
    rl.draw_rectangle_rounded(rect, _TILE_ROUNDNESS, _TILE_SEGMENTS, _PLACEHOLDER_TILE_COLOR)

  def _draw_speedometer_tile(self, rect: rl.Rectangle) -> None:
    self._draw_panel(rect, _SPEEDO_PANEL_BG)

    max_speed = 160.0 if ui_state.is_metric else 100.0
    speed_frac = min(max(self._speed / max_speed, 0.0), 1.0)
    panel_rect = rl.Rectangle(
      rect.x + _SPEEDO_PANEL_PAD_X,
      rect.y + _SPEEDO_PANEL_PAD_Y,
      rect.width - 2 * _SPEEDO_PANEL_PAD_X,
      rect.height - 2 * _SPEEDO_PANEL_PAD_Y,
    )

    self._draw_speedometer_sweep(panel_rect, speed_frac)
    self._draw_speedometer_readout(rect, panel_rect)

  def _draw_speedometer_sweep(self, panel_rect: rl.Rectangle, speed_frac: float) -> None:
    seg_w = max(2.0, (panel_rect.width - (_SPEEDO_SEGMENTS - 1) * _SPEEDO_SEG_GAP) / _SPEEDO_SEGMENTS)
    lit_segments = speed_frac * _SPEEDO_SEGMENTS
    red_zone_start = int(_SPEEDO_SEGMENTS * _SPEEDO_RED_ZONE_START_RATIO)
    sweep_y = panel_rect.y + _SPEEDO_SWEEP_TOP_INSET
    sweep_h = max(10.0, panel_rect.height * _SPEEDO_SWEEP_HEIGHT_RATIO)

    for idx in range(_SPEEDO_SEGMENTS):
      x = panel_rect.x + idx * (seg_w + _SPEEDO_SEG_GAP)
      seg_rect = rl.Rectangle(x, sweep_y, seg_w, sweep_h)

      if idx < lit_segments:
        color = _RETRO_PANEL_GLOW if idx < red_zone_start else rl.Color(210, 32, 24, 255)
      else:
        color = rl.Color(52, 52, 52, 255) if idx < red_zone_start else rl.Color(62, 24, 24, 255)

      rl.draw_rectangle_rounded(seg_rect, 0.15, 4, color)

  def _draw_speedometer_readout(self, rect: rl.Rectangle, panel_rect: rl.Rectangle) -> None:
    unit_text = "km/h" if ui_state.is_metric else "mph"
    unit_size = 27
    unit_text_size = measure_text_cached(self._font_medium, unit_text, unit_size)

    speed_text = str(round(self._speed_display_filter.x))
    speed_size = max(_SPEEDO_VALUE_MIN_SIZE, int(rect.height * 0.50))
    speed_text_size = measure_text_cached(self._font_display, speed_text, speed_size)
    speed_slot_size = measure_text_cached(self._font_display, "888", speed_size)
    speed_center_y = _top_row_number_center_y(rect)
    readout_group_w = speed_slot_size.x + _SPEEDO_UNIT_X_GAP + unit_text_size.x
    speed_slot_x = panel_rect.x + (panel_rect.width - readout_group_w) / 2 + _SPEEDO_READOUT_X_OFFSET
    speed_pos = rl.Vector2(speed_slot_x + (speed_slot_size.x - speed_text_size.x) / 2,
                           speed_center_y - speed_text_size.y / 2 - _SPEED_TEXT_BASELINE_OFFSET - _SPEEDO_VALUE_Y_OFFSET)
    rl.draw_text_ex(self._font_display, speed_text, speed_pos, speed_size, 0, rl.WHITE)

    unit_pos = rl.Vector2(
      speed_slot_x + speed_slot_size.x + _SPEEDO_UNIT_X_GAP,
      speed_pos.y + (speed_text_size.y - unit_text_size.y) / 2,
    )
    rl.draw_text_ex(self._font_medium, unit_text, unit_pos, unit_size, 0, rl.Color(235, 235, 235, 230))

  def draw_status_border(self, rect: rl.Rectangle) -> None:
    border_rect = rl.Rectangle(rect.x, rect.y, rect.width, rect.height)
    border_color = _BORDER_COLORS.get(ui_state.status, _BORDER_COLORS[UIStatus.DISENGAGED])
    rl.draw_rectangle_rounded(border_rect, _BORDER_ROUNDNESS, _BORDER_SEGMENTS, border_color)

    inner_rect = rl.Rectangle(
      rect.x + _BORDER_SIZE,
      rect.y + _BORDER_SIZE,
      rect.width - 2 * _BORDER_SIZE,
      rect.height - 2 * _BORDER_SIZE,
    )
    outer_radius_px = _rounded_corner_radius_px(border_rect, _BORDER_ROUNDNESS)
    inner_radius_px = max(0.0, outer_radius_px - _BORDER_SIZE)
    inner_roundness = _roundness_for_radius(inner_rect, inner_radius_px)
    rl.draw_rectangle_rounded(inner_rect, inner_roundness, _BORDER_SEGMENTS, _DAC_BG_COLOR)

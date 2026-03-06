import numpy as np
import pyray as rl
from opendbc.car import ACCELERATION_DUE_TO_GRAVITY
from openpilot.selfdrive.ui import UI_BORDER_SIZE
from openpilot.selfdrive.ui.ui_state import ui_state, UIStatus
from openpilot.common.filter_simple import FirstOrderFilter
from openpilot.system.ui.lib.application import gui_app, FontWeight
from openpilot.system.ui.lib.text_measure import measure_text_cached
from openpilot.system.ui.widgets import Widget

# Widget dimensions -- wide enough for 5 bars
WIDTH = 570
HEIGHT = 480
PADDING = 24
BAR_GAP = 12
LABEL_HEIGHT = 44
BLOCK_COUNT = 5
BLOCK_GAP = 6
CORNER_RADIUS = 0.12
BLOCK_ROUNDNESS = 0.35
BLOCK_SEGMENTS = 8

# B / S bars: saturates at this raw probability (lower = more sensitive)
PROB_SENSITIVITY_CEILING = 0.4

# BI bar: actual measured vehicle deceleration (m/s²) that fills the bar completely.
# carState.aEgo is negative when decelerating; we map [-MAX_DECEL, 0] → [1, 0].
# Works regardless of whether openpilot has longitudinal control.
MAX_DECEL = 3.5  # m/s² -- firm-but-not-emergency braking saturates the bar

# SA bar: default max lateral acceleration for angle-controlled cars without CP data.
DEFAULT_MAX_LAT_ACCEL = 3.0  # m/s²

# Per-level colors, bottom (1) to top (5): green → yellow-green → yellow → orange → red
BLOCK_COLORS_LIT = [
  rl.Color(26,  210,  80, 240),   # level 1 – green
  rl.Color(130, 220,   0, 240),   # level 2 – yellow-green
  rl.Color(255, 200,   0, 240),   # level 3 – yellow
  rl.Color(255, 130,   0, 240),   # level 4 – orange
  rl.Color(255,  40,  40, 240),   # level 5 – red
]

# Dimmed/unlit version of each block (same hue, much darker)
BLOCK_COLORS_DIM = [
  rl.Color( 10,  50,  25, 100),
  rl.Color( 30,  55,   0, 100),
  rl.Color( 55,  45,   0, 100),
  rl.Color( 55,  30,   0, 100),
  rl.Color( 55,  10,  10, 100),
]

# SA bar has more blocks for finer torque-limit resolution.
# Top block (red) is the saturation indicator; blocks 1-9 show continuous utilization.
SA_BLOCK_COUNT = 10
SA_BLOCK_COLORS_LIT = [
  rl.Color( 26, 210,  80, 240),   # 1  – green
  rl.Color( 70, 220,  40, 240),   # 2  – lime-green
  rl.Color(120, 225,   0, 240),   # 3  – lime
  rl.Color(175, 220,   0, 240),   # 4  – yellow-lime
  rl.Color(225, 210,   0, 240),   # 5  – yellow
  rl.Color(255, 175,   0, 240),   # 6  – amber
  rl.Color(255, 130,   0, 240),   # 7  – orange
  rl.Color(255,  90,   0, 240),   # 8  – deep orange
  rl.Color(255,  55,  10, 240),   # 9  – orange-red
  rl.Color(255,  40,  40, 240),   # 10 – red (saturation only)
]
SA_BLOCK_COLORS_DIM = [
  rl.Color( 10,  50,  25, 100),
  rl.Color( 18,  55,  10, 100),
  rl.Color( 28,  55,   0, 100),
  rl.Color( 40,  55,   0, 100),
  rl.Color( 52,  50,   0, 100),
  rl.Color( 55,  40,   0, 100),
  rl.Color( 55,  30,   0, 100),
  rl.Color( 55,  20,   0, 100),
  rl.Color( 55,  14,   4, 100),
  rl.Color( 55,  10,  10, 100),
]


def _lit_levels(scaled_0_to_1: float, block_count: int) -> int:
  """Map a pre-scaled value in [0, 1] to a discrete block count [0, block_count]."""
  return round(min(max(scaled_0_to_1, 0.0), 1.0) * block_count)


def _draw_bar(bar_x: float, bar_area_y: float, bar_w: float, bar_area_h: float,
              lit: int, override: bool, disengaged: bool,
              block_count: int = BLOCK_COUNT,
              colors_lit: list = BLOCK_COLORS_LIT,
              colors_dim: list = BLOCK_COLORS_DIM) -> None:
  """Draw a single segmented bar with `lit` blocks illuminated from the bottom."""
  block_h = (bar_area_h - (block_count - 1) * BLOCK_GAP) / block_count

  for level in range(block_count):
    # level 0 = bottom block (green), top block = red
    block_y = bar_area_y + bar_area_h - (level + 1) * block_h - level * BLOCK_GAP
    block_rect = rl.Rectangle(bar_x, block_y, bar_w, block_h)

    if disengaged:
      color = rl.Color(35, 35, 35, 120)
    elif override:
      color = rl.Color(160, 160, 160, 180) if level < lit else rl.Color(40, 40, 40, 100)
    elif level < lit:
      color = colors_lit[level]
    else:
      color = colors_dim[level]

    rl.draw_rectangle_rounded(block_rect, BLOCK_ROUNDNESS, BLOCK_SEGMENTS, color)


class DisengageBars(Widget):
  """
  Five segmented LED-style bars in the tici onroad view:

    B  – brake-disengage probability (modelV2, 10s horizon, driver-override signal)
    BI – braking intensity (carState.aEgo, actual measured vehicle deceleration)
    S  – steer-override probability (modelV2, 10s horizon, driver-override signal)
    SA – steering arc / torque utilization (abs torque, direction-agnostic)
         shows how close the steering system is to its torque limit
    DM – driver distraction level (1 - driverMonitoringState.awarenessStatus)
         label changes to P (pose) / E (eyes/blink) / Ph (phone) when distracted

  B and S share the same data source as the mici ConfidenceBall.
  BI fills upward as the car brakes -- directly reacts to measured deceleration.
  SA mirrors the mici TorqueBar signal but shows absolute utilization (0=none, 1=limit).
  DM fills upward as the driver becomes more distracted; empty = fully attentive.
  """

  def __init__(self):
    super().__init__()
    # B / S: slow filter (0.5s RC) -- probability signals don't need instant response
    self._brake_filter = FirstOrderFilter(0.0, 0.5, 1 / gui_app.target_fps)
    self._steer_filter = FirstOrderFilter(0.0, 0.5, 1 / gui_app.target_fps)
    # BI: faster filter (0.15s RC) -- physical decel should feel immediate
    self._accel_filter = FirstOrderFilter(0.0, 0.15, 1 / gui_app.target_fps)
    # SA: fast filter matching BI -- torque utilization is a physical signal
    self._torque_utilization_filter = FirstOrderFilter(0.0, 0.15, 1 / gui_app.target_fps)
    # DM: match B/S smoothness -- awarenessStatus is already time-integrated
    self._dm_filter = FirstOrderFilter(0.0, 0.5, 1 / gui_app.target_fps)
    self._dm_distracted_type = 0
    self._is_rhd = False
    self._font = gui_app.font(FontWeight.MEDIUM)

    self.set_visible(lambda: (
      ui_state.started and
      ui_state.sm.recv_frame['modelV2'] > ui_state.started_frame
    ))

  def _update_state(self):
    if not self.is_visible:
      return

    sm = ui_state.sm

    if sm.updated['driverMonitoringState']:
      self._is_rhd = sm['driverMonitoringState'].isRHD
      self._dm_distracted_type = sm['driverMonitoringState'].distractedType

    if ui_state.status == UIStatus.DISENGAGED:
      self._brake_filter.update(0.0)
      self._steer_filter.update(0.0)
      self._accel_filter.update(0.0)
      self._torque_utilization_filter.update(0.0)
      self._dm_filter.update(0.0)
    else:
      # B / S: driver-override probabilities from the neural net meta head
      predictions = sm['modelV2'].meta.disengagePredictions
      self._brake_filter.update(max(predictions.brakeDisengageProbs or [0.0]))
      self._steer_filter.update(max(predictions.steerOverrideProbs or [0.0]))

      # BI: actual measured vehicle deceleration from carState.aEgo.
      # aEgo is negative when decelerating. We flip the sign so braking → positive,
      # then clamp so acceleration (positive aEgo) reads as zero on the bar.
      # Works on all cars regardless of whether openpilot has longitudinal control.
      self._accel_filter.update(max(-sm['carState'].aEgo, 0.0))

      # SA: torque utilization -- how close we are to the steering limit (direction-agnostic).
      # Mirrors the mici TorqueBar signal but takes abs() so left/right both fill upward.
      lat_state = sm['controlsState'].lateralControlState
      lat_which = lat_state.which()
      lat_controller = getattr(lat_state, lat_which, None)
      lat_saturated = getattr(lat_controller, 'saturated', False)

      if lat_which == 'angleState':
        controls_state = sm['controlsState']
        car_state = sm['carState']
        live_parameters = sm['liveParameters']
        actual_lateral_accel = controls_state.curvature * car_state.vEgo ** 2
        desired_lateral_accel = controls_state.desiredCurvature * car_state.vEgo ** 2
        accel_diff = desired_lateral_accel - actual_lateral_accel
        roll_compensation = live_parameters.roll * ACCELERATION_DUE_TO_GRAVITY * np.interp(car_state.vEgo, [5, 15], [0.0, 1.0])
        lateral_acceleration = actual_lateral_accel - roll_compensation
        max_lat_accel = ui_state.CP.maxLateralAccel if ui_state.CP else DEFAULT_MAX_LAT_ACCEL
        if not sm['carControl'].latActive:
          torque_util = 0.0
        else:
          torque_util = float(np.clip(abs(lateral_acceleration + accel_diff) / max_lat_accel, 0.0, 1.0))
      else:
        torque_util = abs(sm['carOutput'].actuatorsOutput.torque)

      # Top block (red) is reserved exclusively for actual controller saturation.
      # Non-saturated values are capped at (SA_BLOCK_COUNT-1)/SA_BLOCK_COUNT so the
      # top block only lights when the lateral controller reports saturated=True.
      if lat_saturated:
        self._torque_utilization_filter.update(1.0)
      else:
        self._torque_utilization_filter.update(min(torque_util, (SA_BLOCK_COUNT - 1) / SA_BLOCK_COUNT))

      # DM: invert awarenessStatus so bar fills UP as distraction increases.
      # awareness=1.0 (attentive) → 0.0 on bar. awareness=0.0 (terminal) → 1.0 on bar.
      awareness = sm['driverMonitoringState'].awarenessStatus
      self._dm_filter.update(1.0 - max(min(awareness, 1.0), 0.0))

  def _dm_label(self) -> str:
    """Return distraction type label when active, else 'DM'."""
    dt = self._dm_distracted_type
    if dt & 4: return "Ph"
    if dt & 2: return "E"
    if dt & 1: return "P"
    return "DM"

  def _render(self, rect: rl.Rectangle) -> None:
    disengaged = ui_state.status == UIStatus.DISENGAGED
    override = ui_state.status == UIStatus.OVERRIDE

    # Mirror DMoji position anchor: bottom-left (LHD) or bottom-right (RHD).
    # Anchor on the horizontal center of the widget so both edges stay on-screen.
    h_offset = UI_BORDER_SIZE + WIDTH // 2
    v_offset = UI_BORDER_SIZE + HEIGHT // 2
    cx = rect.x + (rect.width - h_offset if self._is_rhd else h_offset)
    cy = rect.y + rect.height - v_offset

    container_x = cx - WIDTH // 2
    container_y = cy - HEIGHT // 2
    container = rl.Rectangle(container_x, container_y, WIDTH, HEIGHT)

    # Semi-transparent dark background card
    rl.draw_rectangle_rounded(container, CORNER_RADIUS, 10, rl.Color(0, 0, 0, 110))

    # Bar area geometry -- 5 bars, 4 gaps
    bar_area_x = container_x + PADDING
    bar_area_y = container_y + PADDING
    bar_area_w = WIDTH - 2 * PADDING
    bar_area_h = HEIGHT - 2 * PADDING - LABEL_HEIGHT
    bar_w = (bar_area_w - 4 * BAR_GAP) // 5

    # Pre-scale each signal to [0, 1] before level mapping.
    # Each entry: (scaled_value, label, block_count, colors_lit, colors_dim)
    bars = [
      (min(self._brake_filter.x / PROB_SENSITIVITY_CEILING, 1.0), "B",          BLOCK_COUNT,    BLOCK_COLORS_LIT,    BLOCK_COLORS_DIM),
      (min(self._accel_filter.x / MAX_DECEL, 1.0),                "BI",         BLOCK_COUNT,    BLOCK_COLORS_LIT,    BLOCK_COLORS_DIM),
      (min(self._steer_filter.x / PROB_SENSITIVITY_CEILING, 1.0), "S",          BLOCK_COUNT,    BLOCK_COLORS_LIT,    BLOCK_COLORS_DIM),
      (min(self._torque_utilization_filter.x, 1.0),               "SA",         SA_BLOCK_COUNT, SA_BLOCK_COLORS_LIT, SA_BLOCK_COLORS_DIM),
      (min(self._dm_filter.x, 1.0),                               self._dm_label(), BLOCK_COUNT, BLOCK_COLORS_LIT,   BLOCK_COLORS_DIM),
    ]

    for i, (scaled, label, n_blocks, c_lit, c_dim) in enumerate(bars):
      bar_x = bar_area_x + i * (bar_w + BAR_GAP)

      # Faint background track behind all blocks
      track_rect = rl.Rectangle(bar_x, bar_area_y, bar_w, bar_area_h)
      rl.draw_rectangle_rounded(track_rect, 0.15, 6, rl.Color(20, 20, 20, 160))

      lit = _lit_levels(scaled, n_blocks)
      _draw_bar(bar_x, bar_area_y, bar_w, bar_area_h, lit, override, disengaged, n_blocks, c_lit, c_dim)

      # Centered label below bar
      font_size = 22
      label_size = measure_text_cached(self._font, label, font_size)
      label_pos = rl.Vector2(bar_x + (bar_w - label_size.x) / 2,
                             bar_area_y + bar_area_h + 10)
      rl.draw_text_ex(self._font, label, label_pos, font_size, 0, rl.Color(170, 170, 170, 210))

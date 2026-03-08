import math
import numpy as np
import pyray as rl
from cereal import log
from opendbc.car import ACCELERATION_DUE_TO_GRAVITY
from openpilot.selfdrive.ui import UI_BORDER_SIZE
from openpilot.selfdrive.ui.ui_state import ui_state, UIStatus
from openpilot.common.filter_simple import FirstOrderFilter
from openpilot.system.ui.lib.application import gui_app, FontWeight
from openpilot.system.ui.lib.text_measure import measure_text_cached
from openpilot.system.ui.widgets import Widget

ConfidenceClass = log.ModelDataV2.ConfidenceClass

# Confidence ball column (left of bars) -- matches mici ConfidenceBall radius and column width
BALL_RADIUS = 24       # px, identical to mici status_dot_radius
BALL_COLUMN_W = 60     # px, matches mici SIDE_PANEL_WIDTH; ball column left of the bars
BALL_GAP = 16          # px, gap between ball column and first bar

# Two stacked horizontal bars at the top of the widget showing predicted braking intensity.
# B3 (top row):    brake3MetersPerSecondSquaredProbs -- moderate-hard braking (~0.3g).
#                  Active in traffic, exits, city driving. Ceiling ~0.60 (FCW uses 0.70).
# B4 (bottom row): brake4MetersPerSecondSquaredProbs -- hard braking (~0.4g).
#                  Fires less often; escalates visibly relative to B3 when danger grows.
# Reading together: B3 lights first → B4 follows = escalating braking demand ahead.
H_BAR_HEIGHT = 34         # px, height of each horizontal bar row
H_BAR_INNER_GAP = 8      # px, gap between B3 and B4 rows
H_BAR_GAP = 22            # px, gap between B4 row and the top of the vertical bars
H_BAR_B3_CEILING = 0.60  # B3 prob at which a block renders fully red
H_BAR_B4_CEILING = 0.30  # B4 prob at which a block renders fully red
H_BAR_LABEL_FONT_SIZE = 30

# Widget dimensions -- ball column + 7 bars + two stacked H-bars at top
WIDTH = 646  # bars shrink slightly to fit 7; increase WIDTH if more space is needed
HEIGHT = 480 + 2 * H_BAR_HEIGHT + H_BAR_INNER_GAP + H_BAR_GAP
PADDING = 24
BAR_GAP = 12
GROUP_GAP = 28  # extra space between prediction group (B,G,S) and reactive group (BI,SA,DM)
LABEL_HEIGHT = 44
BLOCK_COUNT = 5
BLOCK_GAP = 6
CORNER_RADIUS = 0.12
BLOCK_ROUNDNESS = 0.35
BLOCK_SEGMENTS = 8

# B / G / S bars: raw probability at which the bar is fully lit.
# 0.5 means each of the 5 blocks represents a ~10% probability band (50% total range).
PROB_SENSITIVITY_CEILING = 0.5

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

# C bar: modelV2.confidence — 3 blocks matching the 3-state RYG enum.
# Blocks bottom-to-top: red (always lit when any confidence signal), yellow, green.
# green → 3/3 lit, yellow → 2/3 lit, red → 1/3 lit.
# Fewer blocks lit means the model is less confident about staying engaged.
CONF_BLOCK_COUNT = 3
CONF_BLOCK_COLORS_LIT = [
  rl.Color(255,  40,  40, 240),  # block 0 (bottom) – red   (lit in all states)
  rl.Color(255, 200,   0, 240),  # block 1 (middle) – yellow (lit when yellow or green)
  rl.Color( 26, 210,  80, 240),  # block 2 (top)    – green  (lit only when green)
]
CONF_BLOCK_COLORS_DIM = [
  rl.Color( 55,  10,  10, 100),
  rl.Color( 55,  45,   0, 100),
  rl.Color( 10,  50,  25, 100),
]


def _lit_levels(scaled_0_to_1: float, block_count: int) -> int:
  """Map a pre-scaled value in [0, 1] to a discrete block count [0, block_count].

  Uses round() so thresholds sit at midpoints of each band (5%, 15%, 25%, ...).
  Switching to int() would place thresholds at band edges (10%, 20%, 30%, ...)
  but makes the top block harder to reach — tradeoff worth revisiting with real drive data.
  """
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


def _draw_h_bar(x: float, y: float, w: float, h: float,
                probs: list, override: bool, disengaged: bool,
                ceiling: float = 0.30) -> None:
  """Draw a horizontal segmented bar where each of the 5 blocks shows one time-horizon's probability.

  Unlike the vertical bars (which stack blocks to show a single scalar), each block here
  is independently colored by its own probability value, creating a temporal risk gradient:
  left block = 2 s ahead, right block = 10 s ahead. Color maps green → red as prob rises,
  with brightness proportional to scaled probability so low-prob blocks stay visibly dim.
  """
  n = BLOCK_COUNT
  block_w = (w - (n - 1) * BLOCK_GAP) / n

  for i, prob in enumerate(probs[:n]):
    block_x = x + i * (block_w + BLOCK_GAP)
    block_rect = rl.Rectangle(block_x, y, block_w, h)

    if disengaged:
      color = rl.Color(35, 35, 35, 120)
    elif override:
      scaled = min(prob / ceiling, 1.0)
      color = rl.Color(160, 160, 160, 180) if scaled > 0.1 else rl.Color(40, 40, 40, 100)
    else:
      scaled = min(prob / ceiling, 1.0)
      level = min(int(scaled * n), n - 1)
      if scaled < 0.04:
        color = BLOCK_COLORS_DIM[0]
      else:
        base = BLOCK_COLORS_LIT[level]
        alpha = int(80 + scaled * 160)
        color = rl.Color(base.r, base.g, base.b, alpha)

    rl.draw_rectangle_rounded(block_rect, BLOCK_ROUNDNESS, BLOCK_SEGMENTS, color)


def _draw_confidence_ball(cx: float, cy: float, radius: int,
                          top: rl.Color, bottom: rl.Color) -> None:
  """Draw a gradient circle using the same technique as mici ConfidenceBall.

  Paints a gradient rectangle then masks the corners with a ring, matching
  mici's draw_circle_gradient exactly.
  """
  rl.draw_rectangle_gradient_v(int(cx - radius), int(cy - radius),
                               radius * 2, radius * 2, top, bottom)
  outer_radius = math.ceil(radius * math.sqrt(2)) + 1
  rl.draw_ring(rl.Vector2(int(cx), int(cy)), radius, outer_radius,
               0.0, 360.0, 20, rl.BLACK)


class DisengageBars(Widget):
  """
  Segmented LED-style bars plus the mici confidence ball in the tici onroad view.

  Left column (confidence ball):
    ●  – combined model confidence: (1-brakeProb)*(1-steerProb), same formula as mici.
         Color: green > 0.5, yellow > 0.2, red ≤ 0.2. Override = white. Disengaged = dark.
         Vertical position: high = confident, low = nervous.

  Bars, left-to-right (two groups separated by a gap):
    Predictions (neural net, 10 s horizon):
      C  – modelV2.confidence: rolling RYG classifier (brake+gas+steer combined, ~2s update).
           3 blocks: bottom=red lit when any state, mid=yellow, top=green.
           green→3 lit, yellow→2 lit, red→1 lit. Fewer blocks = model less confident.
      B  – brake-disengage probability
      G  – gas-override probability (driver pressing gas to override current plan)
      S  – steer-override probability

    Reactive (physical signals):
      BI – braking intensity (carState.aEgo, actual measured vehicle deceleration)
      SA – steering arc / torque utilization (abs torque, direction-agnostic)
           top block reserved for lateral controller saturation
      DM – driver distraction level (1 - driverMonitoringState.awarenessStatus)
           label changes to P (pose) / E (eyes/blink) / Ph (phone) when distracted

  C uses modelV2.confidence (fill_model_msg.py), which is distinct from the continuous ball.
  B, G, S, and the confidence ball share the same NN meta head source.
  BI fills upward as the car brakes -- directly reacts to measured deceleration.
  SA mirrors the mici TorqueBar signal but shows absolute utilization (0=none, 1=limit).
  DM fills upward as the driver becomes more distracted; empty = fully attentive.
  """

  def __init__(self):
    super().__init__()
    # Confidence ball: matches mici ConfidenceBall exactly -- starts at -0.5 to animate in from below
    self._confidence_filter = FirstOrderFilter(-0.5, 0.5, 1 / gui_app.target_fps)
    # C bar: modelV2.confidence enum mapped to a 0/1/2/3-of-3 fill; no filter needed,
    # the signal is already a rolling-history classifier updated every ~2s in modeld.
    self._model_confidence_scaled = 0.0
    # B / S / G: slow filter (0.5s RC) -- probability signals don't need instant response
    self._brake_filter = FirstOrderFilter(0.0, 0.5, 1 / gui_app.target_fps)
    self._steer_filter = FirstOrderFilter(0.0, 0.5, 1 / gui_app.target_fps)
    self._gas_filter = FirstOrderFilter(0.0, 0.5, 1 / gui_app.target_fps)
    # BI: faster filter (0.15s RC) -- physical decel should feel immediate
    self._accel_filter = FirstOrderFilter(0.0, 0.15, 1 / gui_app.target_fps)
    # SA: matches mici TorqueBar RC -- torque utilization is a physical signal
    self._torque_utilization_filter = FirstOrderFilter(0.0, 0.1, 1 / gui_app.target_fps)
    # DM: match B/S smoothness -- awarenessStatus is already time-integrated
    self._dm_filter = FirstOrderFilter(0.0, 0.5, 1 / gui_app.target_fps)
    self._dm_distracted_type = 0
    # H-bars: one filter per time step (t=[2,4,6,8,10]s) for each braking threshold.
    # Same RC as B/G/S -- these are NN probability signals.
    self._b3_filters = [FirstOrderFilter(0.0, 0.5, 1 / gui_app.target_fps) for _ in range(BLOCK_COUNT)]
    self._b4_filters = [FirstOrderFilter(0.0, 0.5, 1 / gui_app.target_fps) for _ in range(BLOCK_COUNT)]
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
      self._confidence_filter.update(-0.5)
      self._model_confidence_scaled = 0.0
      self._brake_filter.update(0.0)
      self._steer_filter.update(0.0)
      self._gas_filter.update(0.0)
      self._accel_filter.update(0.0)
      self._torque_utilization_filter.update(0.0)
      self._dm_filter.update(0.0)
      for f in self._b3_filters:
        f.update(0.0)
      for f in self._b4_filters:
        f.update(0.0)
    else:
      # C bar: modelV2.confidence is a 3-state RYG enum computed in fill_model_msg.py.
      # Map to a scaled value so _lit_levels() produces 1, 2, or 3 out of CONF_BLOCK_COUNT=3.
      conf = sm['modelV2'].confidence
      if conf == ConfidenceClass.green:
        self._model_confidence_scaled = 1.0        # 3/3 blocks lit
      elif conf == ConfidenceClass.yellow:
        self._model_confidence_scaled = 2 / 3      # 2/3 blocks lit
      else:
        self._model_confidence_scaled = 1 / 3      # 1/3 blocks lit (red or unknown)

      # B / S / G: driver-override probabilities from the neural net meta head
      predictions = sm['modelV2'].meta.disengagePredictions
      self._brake_filter.update(max(predictions.brakeDisengageProbs or [0.0]))
      self._steer_filter.update(max(predictions.steerOverrideProbs or [0.0]))
      self._gas_filter.update(max(predictions.gasDisengageProbs or [0.0]))
      # H-bars: per-horizon braking probabilities for B3 and B4 (t=[2,4,6,8,10]s).
      b3_raw = list(predictions.brake3MetersPerSecondSquaredProbs or [])
      b4_raw = list(predictions.brake4MetersPerSecondSquaredProbs or [])
      for i, (f3, f4) in enumerate(zip(self._b3_filters, self._b4_filters, strict=True)):
        f3.update(b3_raw[i] if i < len(b3_raw) else 0.0)
        f4.update(b4_raw[i] if i < len(b4_raw) else 0.0)
      # Confidence ball: identical formula to mici ConfidenceBall
      self._confidence_filter.update(
        (1 - max(predictions.brakeDisengageProbs or [1.0])) *
        (1 - max(predictions.steerOverrideProbs or [1.0]))
      )

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

    # Bar area geometry -- ball column occupies the far-left, then bars follow.
    # Two stacked H-bars (B3, B4) sit above the vertical bars.
    bar_area_x = container_x + PADDING + BALL_COLUMN_W + BALL_GAP
    bar_area_w = WIDTH - 2 * PADDING - BALL_COLUMN_W - BALL_GAP
    b3_bar_y = container_y + PADDING
    b4_bar_y = b3_bar_y + H_BAR_HEIGHT + H_BAR_INNER_GAP
    bar_area_y = b4_bar_y + H_BAR_HEIGHT + H_BAR_GAP
    bar_area_h = HEIGHT - 2 * PADDING - LABEL_HEIGHT - 2 * H_BAR_HEIGHT - H_BAR_INNER_GAP - H_BAR_GAP

    # --- Confidence ball (left column) ---
    # Vertical position: maps confidence [0, 1] → top to bottom of bar area.
    # Identical formula to mici ConfidenceBall._render — no clamp.
    # When disengaged (filter = -0.5) the ball sits below the bar area; the scissor
    # region clips it to the card so it slides in from the bottom on engage, just like mici.
    confidence = self._confidence_filter.x
    ball_cx = container_x + PADDING + BALL_COLUMN_W // 2
    ball_cy = bar_area_y + (1 - confidence) * (bar_area_h - 2 * BALL_RADIUS) + BALL_RADIUS

    # Color zones — identical to mici (order matches: ENGAGED → green/yellow/red, OVERRIDE → white/gray, else → dark)
    if ui_state.status == UIStatus.ENGAGED:
      if confidence > 0.5:
        ball_top = rl.Color(0, 255, 204, 255)
        ball_bot = rl.Color(0, 255, 38, 255)
      elif confidence > 0.2:
        ball_top = rl.Color(255, 200, 0, 255)
        ball_bot = rl.Color(255, 115, 0, 255)
      else:
        ball_top = rl.Color(255, 0, 21, 255)
        ball_bot = rl.Color(255, 0, 89, 255)
    elif override:
      ball_top = rl.Color(255, 255, 255, 255)
      ball_bot = rl.Color(82, 82, 82, 255)
    else:
      ball_top = rl.Color(50, 50, 50, 255)
      ball_bot = rl.Color(13, 13, 13, 255)

    # Scissor to card bounds so the ball slides in from the card bottom on engage (mici behavior)
    rl.begin_scissor_mode(int(container_x), int(container_y), WIDTH, HEIGHT)
    _draw_confidence_ball(ball_cx, ball_cy, BALL_RADIUS, ball_top, ball_bot)
    rl.end_scissor_mode()

    # --- Stacked horizontal bars (B3 top, B4 bottom) ---
    label_color = rl.Color(170, 170, 170, 210)
    for bar_y, probs, label, ceiling in (
      (b3_bar_y, [f.x for f in self._b3_filters], "B3", H_BAR_B3_CEILING),
      (b4_bar_y, [f.x for f in self._b4_filters], "B4", H_BAR_B4_CEILING),
    ):
      rl.draw_rectangle_rounded(
        rl.Rectangle(bar_area_x, bar_y, bar_area_w, H_BAR_HEIGHT),
        0.4, 6, rl.Color(20, 20, 20, 160))
      _draw_h_bar(bar_area_x, bar_y, bar_area_w, H_BAR_HEIGHT, probs, override, disengaged, ceiling)
      lbl_sz = measure_text_cached(self._font, label, H_BAR_LABEL_FONT_SIZE)
      lbl_pos = rl.Vector2(
        container_x + PADDING + (BALL_COLUMN_W - lbl_sz.x) / 2,
        bar_y + (H_BAR_HEIGHT - lbl_sz.y) / 2)
      rl.draw_text_ex(self._font, label, lbl_pos, H_BAR_LABEL_FONT_SIZE, 0, label_color)

    # Two semantic groups:
    #   Predictions (left):  C, B, G, S  — neural net forecasts of driver intervention
    #   Reactive   (right):  BI, SA, DM — physical actuation and driver monitoring
    # Each entry: (scaled_value, label, block_count, colors_lit, colors_dim)
    predict_bars = [
      (self._model_confidence_scaled,                              "C",          CONF_BLOCK_COUNT, CONF_BLOCK_COLORS_LIT, CONF_BLOCK_COLORS_DIM),
      (min(self._brake_filter.x / PROB_SENSITIVITY_CEILING, 1.0), "B",          BLOCK_COUNT,    BLOCK_COLORS_LIT,    BLOCK_COLORS_DIM),
      (min(self._gas_filter.x / PROB_SENSITIVITY_CEILING, 1.0),   "G",          BLOCK_COUNT,    BLOCK_COLORS_LIT,    BLOCK_COLORS_DIM),
      (min(self._steer_filter.x / PROB_SENSITIVITY_CEILING, 1.0), "S",          BLOCK_COUNT,    BLOCK_COLORS_LIT,    BLOCK_COLORS_DIM),
    ]
    reactive_bars = [
      (min(self._accel_filter.x / MAX_DECEL, 1.0),                "BI",         BLOCK_COUNT,    BLOCK_COLORS_LIT,    BLOCK_COLORS_DIM),
      (min(self._torque_utilization_filter.x, 1.0),               "SA",         SA_BLOCK_COUNT, SA_BLOCK_COLORS_LIT, SA_BLOCK_COLORS_DIM),
      (min(self._dm_filter.x, 1.0),                               self._dm_label(), BLOCK_COUNT, BLOCK_COLORS_LIT,   BLOCK_COLORS_DIM),
    ]
    bars = predict_bars + reactive_bars

    n_bars = len(bars)
    bar_w = (bar_area_w - (n_bars - 1) * BAR_GAP - GROUP_GAP) // n_bars

    for i, (scaled, label, n_blocks, c_lit, c_dim) in enumerate(bars):
      group_offset = GROUP_GAP if i >= len(predict_bars) else 0
      bar_x = bar_area_x + i * (bar_w + BAR_GAP) + group_offset

      # Faint background track behind all blocks
      track_rect = rl.Rectangle(bar_x, bar_area_y, bar_w, bar_area_h)
      rl.draw_rectangle_rounded(track_rect, 0.15, 6, rl.Color(20, 20, 20, 160))

      lit = _lit_levels(scaled, n_blocks)
      _draw_bar(bar_x, bar_area_y, bar_w, bar_area_h, lit, override, disengaged, n_blocks, c_lit, c_dim)

      # Centered label below bar
      font_size = 42
      label_size = measure_text_cached(self._font, label, font_size)
      label_pos = rl.Vector2(bar_x + (bar_w - label_size.x) / 2,
                             bar_area_y + bar_area_h + 10)
      rl.draw_text_ex(self._font, label, label_pos, font_size, 0, rl.Color(170, 170, 170, 210))

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
H_BAR_LABEL_FONT_SIZE = 26

# Widget dimensions -- ball column + 7 bars + two stacked H-bars at top
WIDTH = 700  # 8 bars total (5 prediction + 3 reactive); increased from 646 to fit S2
TOP_HEADER_SPACE = 26
SECTION_HEADER_SPACE = 24
LABEL_BOTTOM_GAP = 14
HEIGHT = 480 + 2 * H_BAR_HEIGHT + H_BAR_INNER_GAP + H_BAR_GAP + TOP_HEADER_SPACE + SECTION_HEADER_SPACE
PADDING = 24
BAR_GAP = 12
GROUP_GAP = 34  # extra space between prediction group (B,G,S) and reactive group (BI,SA,DM)
PREDICT_SECTION_RATIO = 0.54
LABEL_HEIGHT = 56
SECTION_CARD_TOP_OVERHANG = 28
SECTION_CARD_BOTTOM_PADDING = 10
BLOCK_COUNT = 5
BLOCK_GAP = 6
CORNER_RADIUS = 0.12
BLOCK_ROUNDNESS = 0.35
BLOCK_SEGMENTS = 8
LABEL_FONT_SIZE = 32
SECTION_LABEL_FONT_SIZE = 16
TRACK_ROUNDNESS = 0.15
FRAME_INSET = 4
BLOCK_FRAME_INSET = 2

# B / G bars: raw probability at which the bar is fully lit.
# 0.5 means each of the 5 blocks represents a ~10% probability band (50% total range).
PROB_SENSITIVITY_CEILING = 0.5

# S bar: keep it as an upward-filling risk bar, but align its key thresholds to the
# confidence ball when brake disengage is zero. That means:
# - yellow starts around steerOverrideProb = 0.5
# - red starts around steerOverrideProb = 0.8
# The first two thresholds fill in lower-risk steer activity without the old 0.5 amplification.
S_BLOCK_THRESHOLDS = [0.10, 0.25, 0.50, 0.65, 0.80]

# BI bar: actual measured vehicle deceleration (m/s²) that fills the bar completely.
# carState.aEgo is negative when decelerating; we map [-MAX_DECEL, 0] → [1, 0].
# Works regardless of whether openpilot has longitudinal control.
MAX_DECEL = 3.5  # m/s² -- firm-but-not-emergency braking saturates the bar

# SA bar: default max lateral acceleration for angle-controlled cars without CP data.
DEFAULT_MAX_LAT_ACCEL = 3.0  # m/s²

# Per-level colors, bottom (1) to top (5): green → yellow-green → yellow → orange → red
BLOCK_COLORS_LIT = [
  rl.Color(70, 178, 102, 235),    # level 1 – muted green
  rl.Color(178, 188, 82, 235),    # level 2 – olive-gold
  rl.Color(225, 190, 86, 238),    # level 3 – sand
  rl.Color(222, 124, 62, 240),    # level 4 – amber
  rl.Color(214, 58, 44, 242),     # level 5 – red
]

# Dimmed/unlit version of each block (same hue, much darker)
BLOCK_COLORS_DIM = [
  rl.Color(22, 36, 24, 112),
  rl.Color(40, 38, 18, 112),
  rl.Color(56, 42, 18, 116),
  rl.Color(60, 32, 18, 118),
  rl.Color(58, 18, 18, 118),
]

# SA bar has more blocks for finer torque-limit resolution.
# Top block (red) is the saturation indicator; blocks 1-9 show continuous utilization.
SA_BLOCK_COUNT = 10
SA_BLOCK_COLORS_LIT = [
  rl.Color( 70, 178, 102, 235),   # 1
  rl.Color(104, 184,  92, 235),   # 2
  rl.Color(142, 186,  88, 235),   # 3
  rl.Color(176, 186,  84, 235),   # 4
  rl.Color(206, 184,  80, 238),   # 5
  rl.Color(220, 166,  74, 238),   # 6
  rl.Color(224, 142,  68, 239),   # 7
  rl.Color(222, 116,  60, 240),   # 8
  rl.Color(218,  86,  48, 241),   # 9
  rl.Color(214,  58,  44, 242),   # 10
]
SA_BLOCK_COLORS_DIM = [
  rl.Color(22, 36, 24, 112),
  rl.Color(26, 36, 22, 112),
  rl.Color(32, 36, 18, 112),
  rl.Color(38, 36, 18, 112),
  rl.Color(46, 38, 18, 114),
  rl.Color(52, 36, 18, 116),
  rl.Color(58, 32, 18, 118),
  rl.Color(60, 28, 18, 118),
  rl.Color(58, 22, 18, 118),
  rl.Color(58, 18, 18, 118),
]

# C bar: modelV2.confidence — 3 blocks matching the 3-state RYG enum.
# Blocks bottom-to-top: red (always lit when any confidence signal), yellow, green.
# green → 3/3 lit, yellow → 2/3 lit, red → 1/3 lit.
# Fewer blocks lit means the model is less confident about staying engaged.
CONF_BLOCK_COUNT = 3
CONF_BLOCK_COLORS_LIT = [
  rl.Color(214,  58,  44, 242),  # block 0 (bottom) – red
  rl.Color(225, 190,  86, 238),  # block 1 (middle) – sand/yellow
  rl.Color( 70, 178, 102, 235),  # block 2 (top)    – muted green
]
CONF_BLOCK_COLORS_DIM = [
  rl.Color(58, 18, 18, 118),
  rl.Color(56, 42, 18, 116),
  rl.Color(22, 36, 24, 112),
]

# S2 bar: max(S, SA) -- combines behavioral prediction with physical actuation.
# Blue-to-amber palette distinguishes S2 from S (green-red) and SA (green-red gradient).
# Top block (red) indicates the same maximum urgency as other bars.
S2_BLOCK_COUNT = 5
S2_BLOCK_COLORS_LIT = [
  rl.Color( 60, 148, 200, 235),   # level 1 – steel blue
  rl.Color( 76, 162, 168, 235),   # level 2 – teal
  rl.Color(118, 166, 118, 236),   # level 3 – transition (teal-green)
  rl.Color(210, 148,  58, 239),   # level 4 – amber
  rl.Color(214,  58,  44, 242),   # level 5 – red (matches top of other bars)
]
S2_BLOCK_COLORS_DIM = [
  rl.Color(16,  36,  52, 112),
  rl.Color(18,  38,  44, 112),
  rl.Color(28,  38,  28, 112),
  rl.Color(56,  38,  16, 116),
  rl.Color(58,  18,  18, 118),
]

PANEL_OUTER = rl.Color(12, 12, 14, 214)
PANEL_INNER = rl.Color(20, 21, 24, 244)
SECTION_CARD = rl.Color(34, 34, 37, 220)
SECTION_CARD_INNER = rl.Color(20, 20, 22, 242)
TRACK_OUTER = rl.Color(84, 79, 69, 88)
TRACK_INNER = rl.Color(18, 18, 20, 238)
LABEL_COLOR = rl.Color(245, 238, 224, 230)
MUTED_LABEL_COLOR = rl.Color(171, 163, 151, 210)
SECTION_LABEL_COLOR = rl.Color(207, 198, 183, 205)
TICK_COLOR = rl.Color(173, 164, 150, 72)
BEZEL_HIGHLIGHT = rl.Color(255, 250, 242, 18)


def _with_alpha(color: rl.Color, alpha: int) -> rl.Color:
  return rl.Color(color.r, color.g, color.b, alpha)


def _inset_rect(rect: rl.Rectangle, inset_x: float, inset_y: float | None = None) -> rl.Rectangle:
  inset_y = inset_x if inset_y is None else inset_y
  return rl.Rectangle(rect.x + inset_x, rect.y + inset_y, rect.width - 2 * inset_x, rect.height - 2 * inset_y)


def _state_shell_colors(override: bool, disengaged: bool) -> tuple[rl.Color, rl.Color]:
  if disengaged:
    return (
      rl.Color(118, 124, 132, 66),
      rl.Color(134, 140, 148, 48),
    )
  if override:
    return (
      rl.Color(196, 198, 202, 74),
      rl.Color(180, 184, 188, 54),
    )
  return (
    rl.Color(86, 132, 102, 78),
    rl.Color(168, 174, 158, 54),
  )


def _draw_shell(rect: rl.Rectangle, frame_color: rl.Color) -> None:
  rl.draw_rectangle_rounded(rect, CORNER_RADIUS, 12, PANEL_OUTER)
  rl.draw_rectangle_rounded(_inset_rect(rect, 1), CORNER_RADIUS, 12, frame_color)
  inner = _inset_rect(rect, FRAME_INSET)
  rl.draw_rectangle_rounded(inner, CORNER_RADIUS, 12, PANEL_INNER)


def _draw_track_shell(rect: rl.Rectangle, frame_color: rl.Color) -> None:
  rl.draw_rectangle_rounded(rect, TRACK_ROUNDNESS, 8, _with_alpha(frame_color, max(44, frame_color.a)))
  rl.draw_rectangle_rounded(_inset_rect(rect, 1), TRACK_ROUNDNESS, 8, BEZEL_HIGHLIGHT)
  inner = _inset_rect(rect, 2)
  rl.draw_rectangle_rounded(inner, TRACK_ROUNDNESS, 8, TRACK_INNER)


def _draw_section_card(rect: rl.Rectangle, frame_color: rl.Color) -> None:
  rl.draw_rectangle_rounded(rect, 0.1, 8, _with_alpha(frame_color, max(28, frame_color.a - 10)))
  inner = _inset_rect(rect, 1)
  rl.draw_rectangle_rounded(inner, 0.1, 8, SECTION_CARD)
  rl.draw_rectangle_rounded(_inset_rect(inner, 3), 0.1, 8, SECTION_CARD_INNER)


def _draw_track_ticks(rect: rl.Rectangle, count: int, horizontal: bool = False) -> None:
  if horizontal:
    step = rect.width / count
    for i in range(count + 1):
      x = rect.x + i * step
      tick_h = rect.height * (0.34 if i in (0, count) else 0.24)
      tick_rect = rl.Rectangle(x - 1, rect.y + rect.height - tick_h - 3, 2, tick_h)
      rl.draw_rectangle_rounded(tick_rect, 1.0, 4, TICK_COLOR)
  else:
    step = rect.height / count
    for i in range(count + 1):
      y = rect.y + i * step
      tick_w = rect.width * (0.54 if i in (0, count) else 0.4)
      tick_rect = rl.Rectangle(rect.x + rect.width - tick_w - 4, y - 1, tick_w, 2)
      rl.draw_rectangle_rounded(tick_rect, 1.0, 4, TICK_COLOR)


def _draw_gauge_ring(cx: float, cy: float, radius: float, tick_color: rl.Color) -> None:
  center = rl.Vector2(int(cx), int(cy))
  rl.draw_ring(center, radius + 6, radius + 10, 0.0, 360.0, 32, _with_alpha(tick_color, 44))
  for angle_deg in range(-120, 121, 24):
    angle = math.radians(angle_deg)
    outer = rl.Vector2(cx + math.cos(angle) * (radius + 10), cy + math.sin(angle) * (radius + 10))
    inner = rl.Vector2(cx + math.cos(angle) * (radius + (4 if angle_deg % 48 else 1)),
                       cy + math.sin(angle) * (radius + (4 if angle_deg % 48 else 1)))
    rl.draw_line_ex(inner, outer, 1.5, _with_alpha(tick_color, 70 if angle_deg % 48 == 0 else 42))


def _draw_segment_block(block_rect: rl.Rectangle, fill_color: rl.Color) -> None:
  rl.draw_rectangle_rounded(block_rect, BLOCK_ROUNDNESS, BLOCK_SEGMENTS, rl.Color(16, 16, 18, 225))
  rl.draw_rectangle_rounded(_inset_rect(block_rect, BLOCK_FRAME_INSET), BLOCK_ROUNDNESS, BLOCK_SEGMENTS, fill_color)


def _lit_levels(scaled_0_to_1: float, block_count: int) -> int:
  """Map a pre-scaled value in [0, 1] to a discrete block count [0, block_count].

  Uses round() so thresholds sit at midpoints of each band (5%, 15%, 25%, ...).
  Switching to int() would place thresholds at band edges (10%, 20%, 30%, ...)
  but makes the top block harder to reach — tradeoff worth revisiting with real drive data.
  """
  return round(min(max(scaled_0_to_1, 0.0), 1.0) * block_count)


def _steer_lit_levels(raw_steer_prob: float) -> int:
  """Map raw steerOverrideProb to lit blocks using S_BLOCK_THRESHOLDS.

  S is intentionally not scaled by PROB_SENSITIVITY_CEILING. Instead each
  threshold directly mirrors when the confidence ball would move to that color
  band assuming brake disengage probability is zero, i.e.:
    confidence ≈ (1 - steerProb) → yellow when steerProb ≥ 0.5, red at ≥ 0.8.
  """
  raw_steer_prob = min(max(raw_steer_prob, 0.0), 1.0)
  return sum(raw_steer_prob >= t for t in S_BLOCK_THRESHOLDS)


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

    _draw_segment_block(block_rect, color)


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
    scaled = 0.0

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

    _draw_segment_block(block_rect, color)


def _draw_confidence_ball(cx: float, cy: float, radius: int,
                          top: rl.Color, bottom: rl.Color) -> None:
  """Draw a gradient circle using the same technique as mici ConfidenceBall.

  Paints a gradient rectangle then masks the corners with a ring, matching
  mici's draw_circle_gradient exactly.
  """
  gauge_color = rl.Color(196, 188, 174, 80)
  _draw_gauge_ring(cx, cy, radius, gauge_color)
  glow = rl.Color(top.r, top.g, top.b, 14)
  rl.draw_ring(rl.Vector2(int(cx), int(cy)), radius + 3, radius + 8, 0.0, 360.0, 24, glow)
  rl.draw_rectangle_gradient_v(int(cx - radius), int(cy - radius),
                               radius * 2, radius * 2, top, bottom)
  outer_radius = math.ceil(radius * math.sqrt(2)) + 1
  rl.draw_ring(rl.Vector2(int(cx), int(cy)), radius, outer_radius,
               0.0, 360.0, 20, rl.BLACK)
  rl.draw_ring(rl.Vector2(int(cx), int(cy)), radius - 2, radius, 0.0, 360.0, 20, rl.Color(255, 246, 232, 16))


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
    # S2: max(S, SA) combined lateral warning -- RC between S (0.5s) and SA (0.1s)
    self._s2_filter = FirstOrderFilter(0.0, 0.2, 1 / gui_app.target_fps)
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
      self._s2_filter.update(0.0)
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

      # S2: max(S_norm, SA_norm) -- earliest lateral warning from either source.
      # S fires when the camera predicts a driver steer override (behavioral).
      # SA fires when the car is physically working hard to steer (actuation).
      # max() means the bar responds to whichever sees trouble first.
      s2_s_norm = min(self._steer_filter.x / PROB_SENSITIVITY_CEILING, 1.0)
      s2_sa_norm = min(self._torque_utilization_filter.x, 1.0)
      self._s2_filter.update(max(s2_s_norm, s2_sa_norm))

      # DM: invert awarenessStatus so bar fills UP as distraction increases.
      # awareness=1.0 (attentive) → 0.0 on bar. awareness=0.0 (terminal) → 1.0 on bar.
      awareness = sm['driverMonitoringState'].awarenessStatus
      self._dm_filter.update(1.0 - max(min(awareness, 1.0), 0.0))

  def _dm_label(self) -> str:
    """Return distraction type label when active, else 'DM'."""
    dt = self._dm_distracted_type
    if dt & 4:
      return "Ph"
    if dt & 2:
      return "E"
    if dt & 1:
      return "P"
    return "DM"

  def _render(self, rect: rl.Rectangle) -> None:
    disengaged = ui_state.status == UIStatus.DISENGAGED
    override = ui_state.status == UIStatus.OVERRIDE
    frame_color, rule_color = _state_shell_colors(override, disengaged)

    # Mirror DMoji position anchor: bottom-left (LHD) or bottom-right (RHD).
    # Anchor on the horizontal center of the widget so both edges stay on-screen.
    h_offset = UI_BORDER_SIZE + WIDTH // 2
    v_offset = UI_BORDER_SIZE + HEIGHT // 2
    cx = rect.x + (rect.width - h_offset if self._is_rhd else h_offset)
    cy = rect.y + rect.height - v_offset

    container_x = cx - WIDTH // 2
    container_y = cy - HEIGHT // 2
    container = rl.Rectangle(container_x, container_y, WIDTH, HEIGHT)

    # Bar area geometry -- ball column occupies the far-left, then bars follow.
    # Two stacked H-bars (B3, B4) sit above the vertical bars.
    bar_area_x = container_x + PADDING + BALL_COLUMN_W + BALL_GAP
    bar_area_w = WIDTH - 2 * PADDING - BALL_COLUMN_W - BALL_GAP
    b3_bar_y = container_y + PADDING + TOP_HEADER_SPACE
    b4_bar_y = b3_bar_y + H_BAR_HEIGHT + H_BAR_INNER_GAP
    bar_area_y = b4_bar_y + H_BAR_HEIGHT + H_BAR_GAP + SECTION_HEADER_SPACE
    bar_area_h = HEIGHT - 2 * PADDING - LABEL_HEIGHT - 2 * H_BAR_HEIGHT - H_BAR_INNER_GAP - H_BAR_GAP - TOP_HEADER_SPACE - SECTION_HEADER_SPACE
    _draw_shell(container, frame_color)

    # S uses an explicit threshold function aligned to the confidence ball; pre-compute
    # its lit count here so the generic bar loop stays a simple (scaled, ...) shape.
    steer_lit = _steer_lit_levels(self._steer_filter.x)

    # Each entry: (scaled_0_to_1, label, block_count, colors_lit, colors_dim)
    # For S we pass steer_lit / BLOCK_COUNT as a pre-quantised scaled value so that
    # _lit_levels() recovers the same integer without re-mapping.
    predict_bars = [
      (self._model_confidence_scaled,                              "C",  CONF_BLOCK_COUNT, CONF_BLOCK_COLORS_LIT, CONF_BLOCK_COLORS_DIM),
      (min(self._brake_filter.x / PROB_SENSITIVITY_CEILING, 1.0), "B",  BLOCK_COUNT,    BLOCK_COLORS_LIT,    BLOCK_COLORS_DIM),
      (min(self._gas_filter.x / PROB_SENSITIVITY_CEILING, 1.0),   "G",  BLOCK_COUNT,    BLOCK_COLORS_LIT,    BLOCK_COLORS_DIM),
      (steer_lit / BLOCK_COUNT,                                    "S",  BLOCK_COUNT,    BLOCK_COLORS_LIT,    BLOCK_COLORS_DIM),
      (min(self._s2_filter.x, 1.0),                               "S2", S2_BLOCK_COUNT, S2_BLOCK_COLORS_LIT, S2_BLOCK_COLORS_DIM),
    ]
    reactive_bars = [
      (min(self._accel_filter.x / MAX_DECEL, 1.0),                "BI", BLOCK_COUNT,    BLOCK_COLORS_LIT,    BLOCK_COLORS_DIM),
      (min(self._torque_utilization_filter.x, 1.0),               "SA", SA_BLOCK_COUNT, SA_BLOCK_COLORS_LIT, SA_BLOCK_COLORS_DIM),
      (min(self._dm_filter.x, 1.0),                               self._dm_label(), BLOCK_COUNT, BLOCK_COLORS_LIT, BLOCK_COLORS_DIM),
    ]
    sections_total_w = bar_area_w - GROUP_GAP
    predict_section_w = int(sections_total_w * PREDICT_SECTION_RATIO)
    reactive_section_w = sections_total_w - predict_section_w
    predict_section_x = bar_area_x
    reactive_section_x = predict_section_x + predict_section_w + GROUP_GAP

    predict_bar_w = (predict_section_w - (len(predict_bars) - 1) * BAR_GAP) // len(predict_bars)
    reactive_bar_w = (reactive_section_w - (len(reactive_bars) - 1) * BAR_GAP) // len(reactive_bars)
    predict_content_w = len(predict_bars) * predict_bar_w + (len(predict_bars) - 1) * BAR_GAP
    reactive_content_w = len(reactive_bars) * reactive_bar_w + (len(reactive_bars) - 1) * BAR_GAP
    predict_content_x = predict_section_x + (predict_section_w - predict_content_w) / 2
    reactive_content_x = reactive_section_x + (reactive_section_w - reactive_content_w) / 2

    horizontal_group_rect = rl.Rectangle(bar_area_x - 8, b3_bar_y - 8, bar_area_w + 16, H_BAR_HEIGHT * 2 + H_BAR_INNER_GAP + 16)

    # Keep the inner section cards above the footer labels so they never intrude
    # into the shell's rounded bottom corners.
    section_card_y = bar_area_y - SECTION_CARD_TOP_OVERHANG
    section_card_h = bar_area_h + SECTION_CARD_TOP_OVERHANG + SECTION_CARD_BOTTOM_PADDING
    predictive_group_rect = rl.Rectangle(predict_section_x - 10, section_card_y, predict_section_w + 20, section_card_h)
    reactive_group_rect = rl.Rectangle(reactive_section_x - 10, section_card_y, reactive_section_w + 20, section_card_h)
    _draw_section_card(horizontal_group_rect, frame_color)
    _draw_section_card(predictive_group_rect, frame_color)
    _draw_section_card(reactive_group_rect, frame_color)

    ball_cx = container_x + PADDING + BALL_COLUMN_W // 2
    ball_track_rect = rl.Rectangle(ball_cx - 14, bar_area_y, 28, bar_area_h)
    _draw_track_shell(ball_track_rect, frame_color)
    _draw_track_ticks(ball_track_rect, BLOCK_COUNT)

    # --- Confidence ball (left column) ---
    # Vertical position: maps confidence [0, 1] → top to bottom of bar area.
    # Identical formula to mici ConfidenceBall._render — no clamp.
    # When disengaged (filter = -0.5) the ball sits below the bar area; the scissor
    # region clips it to the card so it slides in from the bottom on engage, just like mici.
    confidence = self._confidence_filter.x
    ball_cy = bar_area_y + (1 - confidence) * (bar_area_h - 2 * BALL_RADIUS) + BALL_RADIUS

    # Color zones — identical to mici (order matches: ENGAGED → green/yellow/red, OVERRIDE → white/gray, else → dark)
    if ui_state.status == UIStatus.ENGAGED:
      if confidence > 0.5:
        ball_top = rl.Color(120, 178, 124, 255)
        ball_bot = rl.Color(72, 126, 78, 255)
      elif confidence > 0.2:
        ball_top = rl.Color(221, 182, 98, 255)
        ball_bot = rl.Color(179, 108, 56, 255)
      else:
        ball_top = rl.Color(196, 72, 58, 255)
        ball_bot = rl.Color(126, 36, 34, 255)
    elif override:
      ball_top = rl.Color(228, 224, 216, 255)
      ball_bot = rl.Color(104, 100, 96, 255)
    else:
      ball_top = rl.Color(68, 68, 70, 255)
      ball_bot = rl.Color(24, 24, 26, 255)

    # Scissor to card bounds so the ball slides in from the card bottom on engage (mici behavior)
    rl.begin_scissor_mode(int(container_x), int(container_y), WIDTH, HEIGHT)
    _draw_confidence_ball(ball_cx, ball_cy, BALL_RADIUS, ball_top, ball_bot)
    rl.end_scissor_mode()

    # --- Stacked horizontal bars (B3 top, B4 bottom) ---
    label_color = MUTED_LABEL_COLOR if disengaged else LABEL_COLOR
    top_header = "BRAKE HORIZON"
    top_header_size = measure_text_cached(self._font, top_header, SECTION_LABEL_FONT_SIZE)
    top_header_pos = rl.Vector2(
      bar_area_x + (bar_area_w - top_header_size.x) / 2,
      container_y + PADDING + 2,
    )
    rl.draw_text_ex(self._font, top_header, top_header_pos, SECTION_LABEL_FONT_SIZE, 1, SECTION_LABEL_COLOR)
    for bar_y, probs, label, ceiling in (
      (b3_bar_y, [f.x for f in self._b3_filters], "B3", H_BAR_B3_CEILING),
      (b4_bar_y, [f.x for f in self._b4_filters], "B4", H_BAR_B4_CEILING),
    ):
      h_track_rect = rl.Rectangle(bar_area_x, bar_y, bar_area_w, H_BAR_HEIGHT)
      _draw_track_shell(h_track_rect, frame_color)
      _draw_track_ticks(h_track_rect, BLOCK_COUNT, horizontal=True)
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
    predict_label_pos = rl.Vector2(predict_section_x + 12, bar_area_y - 24)
    react_label_pos = rl.Vector2(reactive_section_x + 12, bar_area_y - 24)
    rl.draw_text_ex(self._font, "FORECAST", predict_label_pos, SECTION_LABEL_FONT_SIZE, 1, SECTION_LABEL_COLOR)
    rl.draw_text_ex(self._font, "VEHICLE", react_label_pos, SECTION_LABEL_FONT_SIZE, 1, SECTION_LABEL_COLOR)

    for section_x, bar_w, section_bars in (
      (predict_content_x, predict_bar_w, predict_bars),
      (reactive_content_x, reactive_bar_w, reactive_bars),
    ):
      for i, (scaled, label, n_blocks, c_lit, c_dim) in enumerate(section_bars):
        bar_x = section_x + i * (bar_w + BAR_GAP)

        track_rect = rl.Rectangle(bar_x, bar_area_y, bar_w, bar_area_h)
        _draw_track_shell(track_rect, frame_color)
        _draw_track_ticks(track_rect, n_blocks)

        lit = _lit_levels(scaled, n_blocks)
        _draw_bar(bar_x, bar_area_y, bar_w, bar_area_h, lit, override, disengaged, n_blocks, c_lit, c_dim)

        label_size = measure_text_cached(self._font, label, LABEL_FONT_SIZE)
        label_pos = rl.Vector2(bar_x + (bar_w - label_size.x) / 2,
                               bar_area_y + bar_area_h + LABEL_BOTTOM_GAP)
        rl.draw_text_ex(self._font, label, label_pos, LABEL_FONT_SIZE, 1, label_color)

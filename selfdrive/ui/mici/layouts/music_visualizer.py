"""
Music beat analysis and visual primitives for the MICI settings screen.

AudioAnalysis: decodes the MP3 via ffmpeg, computes beat timestamps and per-frame
spectral energy using numpy FFT.

EyebrowBilly: full-screen dot-matrix robot face with waveform eyebrows, a letter-
explosion intro animation, and an outro wink — all driven by AudioAnalysis data.
"""

import math
import os
import random
import subprocess

import numpy as np
import pyray as rl

from openpilot.common.basedir import BASEDIR

MUSIC_PATH = os.path.join(BASEDIR, "music", "better-now-audio.mp3")

SAMPLE_RATE = 44100
HOP_SIZE = 512
WIN_SIZE = 1024
N_BANDS = 16
MIN_BEAT_GAP = 0.18
ONSET_THRESHOLD_MULT = 1.4
ONSET_WINDOW_FRAMES = 43


# ---------------------------------------------------------------------------
# Color helper
# ---------------------------------------------------------------------------

def hsv_to_color(h: float, s: float, v: float, a: int = 255) -> rl.Color:
  """Convert HSV (h in 0-360, s/v in 0-1) to rl.Color."""
  h = h % 360
  c = v * s
  x = c * (1 - abs((h / 60) % 2 - 1))
  m = v - c
  if h < 60:
    r, g, b = c, x, 0
  elif h < 120:
    r, g, b = x, c, 0
  elif h < 180:
    r, g, b = 0, c, x
  elif h < 240:
    r, g, b = 0, x, c
  elif h < 300:
    r, g, b = x, 0, c
  else:
    r, g, b = c, 0, x
  return rl.Color(int((r + m) * 255), int((g + m) * 255), int((b + m) * 255), a)


# ---------------------------------------------------------------------------
# Beat analysis (runs in background thread)
# ---------------------------------------------------------------------------

class AudioAnalysis:
  """Decodes the MP3 and computes beat timestamps + per-frame spectral bands."""

  def __init__(self, path: str):
    self.beats: list[float] = []
    self.band_frames: np.ndarray | None = None  # shape (n_frames, N_BANDS)
    self.rms_frames: np.ndarray | None = None   # shape (n_frames,)
    self.n_frames: int = 0
    self.beat_drop_time: float = 0.0            # time of the main energy surge
    self.done = False
    self._path = path

  def run(self) -> None:
    samples = self._decode_mp3()
    if samples is None or len(samples) == 0:
      self.done = True
      return

    odf, bands, rms = self._compute_features(samples)
    self.beats = self._pick_beats(odf)
    self.band_frames = bands
    self.rms_frames = rms
    self.n_frames = len(odf)
    self.beat_drop_time = self._find_beat_drop(rms)
    print(f"[AudioAnalysis] beat_drop_time={self.beat_drop_time:.1f}s  beats={len(self.beats)}")
    self.done = True

  def _find_beat_drop(self, rms: np.ndarray) -> float:
    """Return the timestamp (seconds) of the main energy surge / beat drop.

    Uses 0.5-second windows so the quiet breakdown section before the drop is
    isolated clearly, making the post-quiet energy surge easy to detect.

    Strategy:
      1. Skip the first 2 s of audio (avoids false positives on attack transients).
      2. Find the first 0.5 s window where energy is ≥35% of peak AND ≥1.3× the
         1.5-second average immediately before it — the post-quiet surge pattern.
      3. Fallback: earliest window crossing 35% of peak (minimum 1.0 s returned).

    Always returns > 0 so that `drop_t > 0` in settings.py is reliably True.
    """
    fps    = SAMPLE_RATE // HOP_SIZE   # frames per second ≈ 86
    win    = max(1, fps // 2)          # 0.5-second windows for finer resolution
    n_wins = len(rms) // win

    if n_wins < 6:
      return 1.0  # clip too short — treat the whole thing as the drop

    half_e = np.array([float(np.mean(rms[i * win:(i + 1) * win])) for i in range(n_wins)])
    max_e  = float(np.max(half_e)) if np.max(half_e) > 0 else 1.0

    # Step 1 — find the loud kick/surge (energy ≥35% of peak AND ≥1.3× recent avg).
    # Skip the first 2 s to avoid false positives on attack transients.
    surge_i = None
    for i in range(4, n_wins):
      prev_avg = float(np.mean(half_e[max(0, i - 3):i]))
      if half_e[i] >= 0.35 * max_e and (prev_avg < 1e-4 or half_e[i] >= 1.3 * prev_avg):
        surge_i = i
        break

    if surge_i is not None:
      # Step 2 — walk backward from the surge to find where the singing/energy
      # first fell away (the start of the quiet breakdown). That moment is when the
      # "drop section" begins — before the kick but after the vocals end.
      threshold = half_e[surge_i] * 0.65
      for j in range(surge_i - 1, 3, -1):
        if half_e[j] >= threshold:
          # j is still loud — the quiet started at j+1
          return max(1.0, float(j + 1) * 0.5)
      return max(1.0, float(surge_i) * 0.5)

    # Fallback: first window crossing 35% of peak. min 1.0 keeps drop_t > 0.
    for i in range(n_wins):
      if half_e[i] >= 0.35 * max_e:
        return max(1.0, float(i) * 0.5)

    return max(1.0, float(n_wins) * 0.5 / 2)

  def _decode_mp3(self) -> np.ndarray | None:
    cmd = [
      'ffmpeg', '-i', self._path,
      '-f', 'f32le', '-ac', '1', '-ar', str(SAMPLE_RATE),
      '-loglevel', 'error',
      'pipe:1',
    ]
    try:
      result = subprocess.run(cmd, capture_output=True, timeout=60)
      if result.returncode != 0:
        return None
      return np.frombuffer(result.stdout, dtype=np.float32)
    except Exception:
      return None

  def _compute_features(self, samples: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    n_frames = (len(samples) - WIN_SIZE) // HOP_SIZE
    odf = np.zeros(n_frames, dtype=np.float32)
    rms = np.zeros(n_frames, dtype=np.float32)
    bands = np.zeros((n_frames, N_BANDS), dtype=np.float32)

    window = np.hanning(WIN_SIZE).astype(np.float32)
    n_bins = WIN_SIZE // 2 + 1

    low_hz, high_hz = 40.0, SAMPLE_RATE / 2.0
    edges = np.logspace(np.log10(low_hz), np.log10(high_hz), N_BANDS + 1)
    bin_hz = SAMPLE_RATE / WIN_SIZE
    band_edges = np.clip((edges / bin_hz).astype(int), 0, n_bins - 1)

    prev_mag = np.zeros(n_bins, dtype=np.float32)

    for i in range(n_frames):
      start = i * HOP_SIZE
      frame = samples[start:start + WIN_SIZE] * window
      mag = np.abs(np.fft.rfft(frame)).astype(np.float32)
      diff = mag - prev_mag
      odf[i] = float(np.sum(np.maximum(diff, 0)))
      prev_mag = mag
      rms[i] = float(np.sqrt(np.mean(frame ** 2)))
      for b in range(N_BANDS):
        lo, hi = band_edges[b], band_edges[b + 1]
        if hi > lo:
          bands[i, b] = float(np.mean(mag[lo:hi + 1]))

    max_rms = float(np.max(rms)) if np.max(rms) > 0 else 1.0
    rms /= max_rms
    for b in range(N_BANDS):
      col_max = float(np.max(bands[:, b])) if np.max(bands[:, b]) > 0 else 1.0
      bands[:, b] /= col_max

    return odf, bands, rms

  def _pick_beats(self, odf: np.ndarray) -> list[float]:
    n = len(odf)
    beats: list[float] = []
    last_beat = -MIN_BEAT_GAP

    for i in range(n):
      lo = max(0, i - ONSET_WINDOW_FRAMES)
      hi = min(n, i + ONSET_WINDOW_FRAMES + 1)
      threshold = float(np.mean(odf[lo:hi])) * ONSET_THRESHOLD_MULT
      if odf[i] > threshold:
        t = (i * HOP_SIZE) / SAMPLE_RATE
        if t - last_beat >= MIN_BEAT_GAP:
          beats.append(t)
          last_beat = t

    return beats

  def frame_at(self, t: float) -> int:
    return min(int(t * SAMPLE_RATE / HOP_SIZE), self.n_frames - 1)


# ---------------------------------------------------------------------------
# Dancing stick figure
# ---------------------------------------------------------------------------

_DANCE_SPEED   = 4.5
_SWAY_SPEED    = 2.2
_PARTICLE_LIFE = 0.55   # seconds a burst particle lives


class DancingFigure:
  """
  Procedural stick figure that scales to fill any button rect.

  Features:
  - Morphing transition: button rect collapses into skinny body shape.
  - Proper alternating limb gait (left arm ↔ right leg in phase).
  - Squash & stretch on beat impact.
  - Particle bursts on each beat.
  - Tap-to-boost via trigger_boost().
  """

  def __init__(self, hue_offset: float = 0.0):
    self.hue_offset = hue_offset
    self._phase = hue_offset * 0.6
    self._boost_time: float = -999.0
    self._particles: list[dict] = []    # [{x0,y0,vx,vy,born,hue}, ...]
    self._prev_beat_flash: float = 0.0

  def trigger_boost(self) -> None:
    """Tap the dancing figure to trigger a 0.5 s spin/amp burst."""
    self._boost_time = rl.get_time()

  # ------------------------------------------------------------------
  def draw(self, rect: rl.Rectangle, t: float, base_hue: float,
           beat_flash: float, energy: float, transition: float = 1.0,
           hype: float = 1.0) -> None:
    """
    Draw the dancing figure.

    transition: 0→1  button→figure morph (independent of hype).
    hype:       0→1  animation energy + color vibrancy; 0.05 = barely
                     moving/gray, 1.0 = full party.
    """
    h   = rect.height
    w   = rect.width
    now = rl.get_time()

    # Ease-out cubic — snappy shrink then smooth settle
    ease = 1.0 - (1.0 - min(transition, 1.0)) ** 3

    # ---- Base anatomical sizes (tuned to fit within the button rect) ----
    # Vertical budget (rect.y → rect.y+h): hat + head + gap + body + legs ≈ 0.87 h
    body_h    = h * 0.31
    body_w    = min(w * 0.14, body_h * 0.35)
    head_r    = body_w * 0.85   # slightly smaller so hat fits above
    arm_len   = w * 0.27
    leg_len   = h * 0.23
    arm_thick = max(3.0, body_w * 0.28)
    leg_thick = max(4.0, body_w * 0.34)
    hat_cw    = body_w * 1.8
    hat_ch    = body_w * 0.73   # shorter crown to leave room at top
    hat_bw    = body_w * 2.6
    hat_bh    = body_w * 0.26

    hue = (base_hue + self.hue_offset) % 360

    # ---- Squash & stretch on beat ----
    squash = beat_flash * 0.28 * hype
    eff_bw = body_w * (1.0 + squash)          # wider on impact
    eff_bh = body_h * (1.0 - squash * 0.5)   # shorter on impact
    eff_hr = head_r * (1.0 + squash * 0.12)  # head puffs out

    # ---- Tap-to-boost ----
    boost = max(0.0, 1.0 - (now - self._boost_time) / 0.5)

    # ---- Animation amplitude ----
    amp    = ease * hype * (1.0 + beat_flash * 2.0 + energy * 0.6) + boost * 3.0
    sway   = math.sin(t * _SWAY_SPEED + self._phase) * body_w * 0.5 * amp
    if boost > 0.01:
      # Rapid side-to-side spin during boost
      sway += boost * math.sin(now * 40) * body_w * 1.5
    bounce = abs(math.sin(t * _DANCE_SPEED + self._phase)) * h * 0.025 * amp

    # ---- Body anchor ----
    # body_top is positioned so hat+head fit above and legs fit below within rect.
    # With the current anatomy: hat+head height ≈ 0.43 h, body ≈ 0.31 h, legs ≈ 0.23 h
    # → body_top at 0.43 h leaves ~9 px pad at top and ~5 px at bottom.
    cx       = rect.x + w * 0.5 + sway
    body_top = rect.y + h * 0.43 - bounce
    body_bot = body_top + eff_bh

    # ---- Colors: near-gray before drop → vivid after ----
    sat        = 0.15 + 0.85 * hype
    body_color = hsv_to_color(hue,               sat,       1.0,  245)
    limb_color = hsv_to_color((hue + 30)  % 360, sat * 0.9, 1.0,  230)
    hat_color  = hsv_to_color((hue + 180) % 360, sat,       0.95, 255)
    skin_color = hsv_to_color((hue + 60)  % 360, sat * 0.5, 1.0,  255)
    eye_color  = rl.Color(10, 10, 10, 255)

    # ---- Morphing body rect: full button → skinny torso ----
    morph_x = rect.x + (cx - eff_bw / 2 - rect.x) * ease
    morph_y = rect.y + (body_top          - rect.y) * ease
    morph_w = rect.width  + (eff_bw - rect.width)   * ease
    morph_h = rect.height + (eff_bh - rect.height)  * ease
    morph_r = 0.2 + (0.45 - 0.2) * ease

    BG_R, BG_G, BG_B = 10, 10, 30
    mc_r = int(BG_R + (body_color.r - BG_R) * ease)
    mc_g = int(BG_G + (body_color.g - BG_G) * ease)
    mc_b = int(BG_B + (body_color.b - BG_B) * ease)
    rl.draw_rectangle_rounded(rl.Rectangle(morph_x, morph_y, morph_w, morph_h),
                               morph_r, 8, rl.Color(mc_r, mc_g, mc_b, 255))

    # ---- Limbs/head/hat fade in during second half of transition ----
    limb_fade = max(0.0, ease * 2.0 - 1.0)
    if limb_fade < 0.01:
      self._prev_beat_flash = beat_flash
      return

    def _fade(color: rl.Color) -> rl.Color:
      return rl.Color(color.r, color.g, color.b, int(color.a * limb_fade))

    # ---- Proper alternating gait ----
    # Left arm forward  ↔  right leg forward (same phase)
    # Right arm forward ↔  left leg forward  (opposite phase)
    gait  = t * _DANCE_SPEED + self._phase
    arm_l = math.sin(gait)           * 50 * amp
    arm_r = math.sin(gait + math.pi) * 50 * amp
    leg_l = math.sin(gait + math.pi) * 40 * amp   # opposite to left arm
    leg_r = math.sin(gait)           * 40 * amp   # same as left arm

    hat_pop    = beat_flash * eff_hr * 0.6 * limb_fade
    shoulder_y = body_top + eff_bh * 0.15
    head_cy    = body_top - eff_hr - h * 0.03
    hip_y      = body_bot

    # Legs (behind body)
    self._draw_leg(cx, hip_y, leg_l, left=True,  len_=leg_len, thick=leg_thick, color=_fade(limb_color))
    self._draw_leg(cx, hip_y, leg_r, left=False, len_=leg_len, thick=leg_thick, color=_fade(limb_color))

    # Arms (behind body)
    self._draw_arm(cx, shoulder_y, arm_l, left=True,  len_=arm_len, thick=arm_thick, color=_fade(limb_color))
    self._draw_arm(cx, shoulder_y, arm_r, left=False, len_=arm_len, thick=arm_thick, color=_fade(limb_color))

    # Head
    rl.draw_circle(int(cx), int(head_cy), eff_hr, _fade(skin_color))

    # Eyes
    eye_off = eff_hr * 0.30
    rl.draw_circle(int(cx - eye_off), int(head_cy - eff_hr * 0.1), max(1, int(eff_hr * 0.18)), _fade(eye_color))
    rl.draw_circle(int(cx + eye_off), int(head_cy - eff_hr * 0.1), max(1, int(eff_hr * 0.18)), _fade(eye_color))

    # Smile
    for di in range(-4, 5):
      rl.draw_circle(int(cx + di * eff_hr * 0.11),
                     int(head_cy + eff_hr * 0.35 + abs(di) * eff_hr * 0.04),
                     max(1, int(eff_hr * 0.09)), _fade(eye_color))

    # Hat
    hat_y = head_cy - eff_hr - hat_ch - hat_pop
    rl.draw_rectangle_rounded(rl.Rectangle(cx - hat_cw / 2, hat_y, hat_cw, hat_ch), 0.15, 6, _fade(hat_color))
    rl.draw_rectangle_rounded(rl.Rectangle(cx - hat_bw / 2, hat_y + hat_ch - 1, hat_bw, hat_bh), 0.5, 6, _fade(hat_color))

    # ---- Particle bursts on beat ----
    body_mid_y = body_top + eff_bh * 0.5
    if beat_flash > 0.85 and self._prev_beat_flash <= 0.85 and hype > 0.3:
      for _ in range(8):
        angle = random.uniform(0, 2 * math.pi)
        speed = random.uniform(50, 160) * hype
        self._particles.append({
          'x0': cx, 'y0': body_mid_y,
          'vx': math.cos(angle) * speed,
          'vy': math.sin(angle) * speed - 25,   # slight upward bias
          'born': now,
          'hue': (hue + random.uniform(-50, 50)) % 360,
        })
    self._prev_beat_flash = beat_flash

    # Update & draw particles (analytical position from birth time — no dt needed)
    alive = []
    for p in self._particles:
      age = now - p['born']
      if age > _PARTICLE_LIFE:
        continue
      frac   = age / _PARTICLE_LIFE
      px     = p['x0'] + p['vx'] * age
      py     = p['y0'] + p['vy'] * age + 90 * age * age   # gravity
      alpha  = int(255 * (1 - frac) * limb_fade)
      radius = max(1, int(5 * (1 - frac) * hype))
      rl.draw_circle(int(px), int(py), radius, hsv_to_color(p['hue'], 1.0, 1.0, alpha))
      alive.append(p)
    self._particles = alive

  # ------------------------------------------------------------------
  def _draw_arm(self, sx: float, sy: float, angle_deg: float, left: bool,
                len_: float, thick: float, color: rl.Color) -> None:
    base  = 180.0 if left else 0.0
    total = math.radians(base + angle_deg * 0.75)
    ex = sx + math.cos(total) * len_
    ey = sy + math.sin(total) * len_
    rl.draw_line_ex(rl.Vector2(sx, sy), rl.Vector2(ex, ey), thick, color)

  def _draw_leg(self, hx: float, hy: float, angle_deg: float, left: bool,
                len_: float, thick: float, color: rl.Color) -> None:
    side  = -1 if left else 1
    base  = 90.0 + side * -20.0
    total = math.radians(base + angle_deg * 0.55)
    ex = hx + math.cos(total) * len_
    ey = hy + math.sin(total) * len_
    rl.draw_line_ex(rl.Vector2(hx, hy), rl.Vector2(ex, ey), thick, color)


# ---------------------------------------------------------------------------
# Eyebrow Billy — full-screen dot-matrix robot face with waveform eyebrows
# ---------------------------------------------------------------------------

# Dot-matrix eye shape: (col, row) in half-unit grid, relative to eye center.
# Produces a 16-dot rounded-rectangle blob (3 + 5 + 5 + 3).
_EYE_DOTS: list[tuple[float, float]] = [
  (-1, -1.5), ( 0, -1.5), ( 1, -1.5),
  (-2, -0.5), (-1, -0.5), ( 0, -0.5), ( 1, -0.5), ( 2, -0.5),
  (-2,  0.5), (-1,  0.5), ( 0,  0.5), ( 1,  0.5), ( 2,  0.5),
  (-1,  1.5), ( 0,  1.5), ( 1,  1.5),
]

# Mouth: 4 small dots in a gentle V
_MOUTH_DOTS: list[tuple[float, float]] = [
  (-1.0, 0.0), (-0.5, 0.7), (0.5, 0.7), (1.0, 0.0),
]

_N_WAVE_PTS      = 48   # polyline sample points per eyebrow — more = smoother curve
_BROW_SWAY_SPD   = 2.6  # fallback sine speed when analysis not ready
_BILLY_PTCL_LIFE = 0.6


def _ramp(x: float, lo: float, hi: float) -> float:
  """Linearly maps x from [lo, hi] → [0.0, 1.0], clamped at both ends."""
  return max(0.0, min(1.0, (x - lo) / (hi - lo)))


class EyebrowBilly:
  """
  Full-screen dot-matrix robot face with a letter-explosion intro.

  Intro (intro_frac 0 → 1, driven by song time / beat-drop time):
    Phase 1  0.00 → 0.25  35 face-dots blast outward from the "eyebrow"
                           button, grouped by letter (e-y-e-b-r-o-w).
    Phase 2  0.25 → 0.70  dots drift/float chaotically.
    Phase 3  0.70 → 1.00  dots smoothly snap to their face positions.

  After drop (intro_frac ≥ 1): normal face + waveform eyebrows.
  """

  def __init__(self, origin_rect: "rl.Rectangle | None" = None) -> None:
    self._prev_beat_flash: float = 0.0
    self._particles: list[dict] = []

    # Button origin — where the explosion starts from
    if origin_rect is not None:
      self._btn_lx = float(origin_rect.x)
      self._btn_cx = float(origin_rect.x + origin_rect.width  * 0.5)
      self._btn_cy = float(origin_rect.y + origin_rect.height * 0.5)
      self._btn_w  = float(origin_rect.width)
    else:
      self._btn_lx = 0.0
      self._btn_cx = 0.0
      self._btn_cy = 0.0
      self._btn_w  = 402.0

    # Per-dot scatter params — fixed seed so the explosion is repeatable
    n_total = len(_EYE_DOTS) * 2 + len(_MOUTH_DOTS)
    rng = random.Random(1337)
    self._scat_angle  = [rng.uniform(0.0, 2 * math.pi) for _ in range(n_total)]
    self._scat_speed  = [rng.uniform(0.55, 1.40)        for _ in range(n_total)]
    self._scat_dfreq  = [rng.uniform(0.8,  2.8)         for _ in range(n_total)]
    self._scat_dphase = [rng.uniform(0.0, 2 * math.pi)  for _ in range(n_total)]
    # Per-dot oval scale: each circle orbits a differently-sized ellipse so
    # they spread at different depths across the frame — no synchronized ring.
    self._scat_rx     = [rng.uniform(0.15, 0.42)        for _ in range(n_total)]
    self._scat_ry     = [rng.uniform(0.12, 0.38)        for _ in range(n_total)]
    # ---- Firework sparks — shoot out on explosion, arc with gravity, fade away ----
    _N_SPARKS = 220
    srng = random.Random(99)
    # Each spark: angle, speed (0-1 scale), hue, launch delay (fraction of intro),
    # gravity factor (positive = pulled down, negative = floats up briefly)
    self._spark_angle   = [srng.uniform(0.0, 2 * math.pi) for _ in range(_N_SPARKS)]
    self._spark_speed   = [srng.uniform(0.30, 1.00)        for _ in range(_N_SPARKS)]
    self._spark_delay   = [srng.uniform(0.0, 0.12)         for _ in range(_N_SPARKS)]  # staggered waves
    self._spark_gravity = [srng.uniform(0.2, 1.0)          for _ in range(_N_SPARKS)]  # arc strength
    self._spark_size    = [srng.uniform(2.0, 7.0)          for _ in range(_N_SPARKS)]

    # ---- Shrapnel layer — fast, tight, erratic burst on top of main firework ----
    # Travels 2x faster, lives half as long, nearly no gravity — feels like hot debris.
    _N_SHRAP = 300
    xrng = random.Random(777)
    self._shrap_angle   = [xrng.uniform(0.0, 2 * math.pi) for _ in range(_N_SHRAP)]
    self._shrap_speed   = [xrng.uniform(0.60, 1.50)        for _ in range(_N_SHRAP)]  # faster than sparks
    self._shrap_delay   = [xrng.uniform(0.00, 0.08)         for _ in range(_N_SHRAP)]  # tighter wave
    self._shrap_gravity = [xrng.uniform(-0.1, 0.25)         for _ in range(_N_SHRAP)]  # mostly straight
    self._shrap_size    = [xrng.uniform(1.0, 3.5)           for _ in range(_N_SHRAP)]  # tiny shards

    # ---- Warp-speed beams — animated streaks shooting outward during intro ----
    # Each beam is a short moving segment that travels from center → edge at its own
    # speed and phase, so the whole field looks like it's constantly rushing outward.
    _N_WARP = 500
    wrng = random.Random(555)
    # Evenly spaced angles + tiny jitter for full 360° density
    self._warp_angle  = [(wi * 2 * math.pi / _N_WARP + wrng.uniform(-0.010, 0.010))
                         for wi in range(_N_WARP)]
    self._warp_speed  = [wrng.uniform(0.18, 0.80)   for _ in range(_N_WARP)]
    self._warp_phase  = [wrng.random()               for _ in range(_N_WARP)]
    # Wider length range — short slivers to long streaks all co-existing
    self._warp_blen   = [wrng.uniform(0.08, 0.45)   for _ in range(_N_WARP)]
    self._warp_width  = [wrng.uniform(0.4,  2.8)    for _ in range(_N_WARP)]
    self._warp_bright = [wrng.uniform(0.50, 1.00)   for _ in range(_N_WARP)]
    # Palette: 50% green, 30% cyan, 15% blue, 5% near-white
    _warp_buckets = ([(100, 150)] * 250 + [(150, 205)] * 150 +
                     [(205, 255)] * 75  + [(155, 175)] * 25)
    self._warp_hue = [wrng.uniform(*_warp_buckets[wi % len(_warp_buckets)])
                      for wi in range(_N_WARP)]

    # ---- Ephemeral chaos circles — purely decorative, wander and fade ----
    _N_CHAOS = 40
    crng = random.Random(42)
    self._chaos_angle  = [crng.uniform(0.0, 2 * math.pi) for _ in range(_N_CHAOS)]
    self._chaos_rx     = [crng.uniform(0.10, 0.65)        for _ in range(_N_CHAOS)]  # wide: some off-frame
    self._chaos_ry     = [crng.uniform(0.08, 0.55)        for _ in range(_N_CHAOS)]
    self._chaos_dfreq  = [crng.uniform(0.5,  3.5)         for _ in range(_N_CHAOS)]
    self._chaos_dphase = [crng.uniform(0.0, 2 * math.pi)  for _ in range(_N_CHAOS)]
    self._chaos_fphase = [crng.uniform(0.0, 2 * math.pi)  for _ in range(_N_CHAOS)]
    self._chaos_size   = [crng.uniform(3.0, 14.0)          for _ in range(_N_CHAOS)]

  # ------------------------------------------------------------------
  def draw(self, rect: rl.Rectangle, t: float, base_hue: float,
           beat_flash: float, energy: float,
           bands: np.ndarray | None = None,
           intro_frac: float = 1.0, outro_frac: float = 0.0,
           transition: float = 1.0, hype: float = 1.0,
           is_in_drop: bool = False) -> None:
    w, h = rect.width, rect.height
    now  = rl.get_time()

    ease = 1.0 - (1.0 - min(transition, 1.0)) ** 3
    a    = int(255 * ease)

    # Remap the rotating hue to a concert-light palette — green → cyan → blue → purple.
    # This avoids muddy warm yellows/reds and keeps the feel of the reference image.
    hue  = 120.0 + (base_hue % 360) / 360.0 * 200.0   # sweeps 120°(green)→320°(magenta)
    sat      = 0.15 + 0.85 * hype
    brow_col = hsv_to_color(hue, sat, 1.0, a)

    doing_intro = intro_frac < 0.99

    # ---- Outro calming + wink ----
    # Beat flash and eyebrows fade to zero early so the wink lands on a calm face.
    eff_beat  = beat_flash * (1.0 - _ramp(outro_frac, 0.00, 0.25))
    brow_calm =               1.0 - _ramp(outro_frac, 0.28, 0.39)

    # Wink right eye (idx 1).  Timeline inside the 3-s outro window:
    #   0.00–0.40  open  →  0.40–0.47  close (0.21 s)  →  hold  →  0.65–0.75  open (0.3 s)
    wink_scale = 1.0 - _ramp(outro_frac, 0.40, 0.50)   # starts closing at 40 %
    if outro_frac >= 0.65:
      wink_scale = _ramp(outro_frac, 0.65, 0.78)        # overrides: eye opens back up

    # ---- Background light show ----
    # Starts when the face fully forms; fades to black as the outro begins so
    # the wink happens on a clean black bg with only the eyebrows lit.
    bg_fade = 1.0 - _ramp(outro_frac, 0.28, 0.39)
    if intro_frac >= 1.0 and bg_fade > 0.01:
      # Steady hue tint — always visible between beats
      rl.draw_rectangle(int(rect.x), int(rect.y), int(rect.width), int(rect.height),
                        hsv_to_color(hue, 1.0, 1.0, int(hype * 45 * bg_fade)))
      # Punchy color swell on strong beats — filters hi-hats, keeps kick hits
      if beat_flash > 0.45:
        rl.draw_rectangle(int(rect.x), int(rect.y), int(rect.width), int(rect.height),
                          hsv_to_color(hue, 0.7, 1.0, int(beat_flash * hype * 85 * bg_fade)))

    # Dots appear instantly at explosion start then stay fully opaque
    dot_a = int(255 * min(1.0, intro_frac / 0.08)) if doing_intro else a

    # Intro dots are white — matches the sparks and chaos circles
    if doing_intro:
      face_col = rl.Color(255, 255, 255, dot_a)
    else:
      face_col = hsv_to_color(hue, hype * eff_beat * 0.75, 1.0, a)

    # ---- Face layout (target positions) ----
    bounce  = 0.0 if doing_intro else abs(math.sin(t * 1.9)) * h * 0.009 * hype
    eye_y   = rect.y + h * 0.44 - bounce
    eye_lx  = rect.x + w * 0.32
    eye_rx  = rect.x + w * 0.68
    dot_r   = max(8,  int(h * 0.028))
    dot_gap = max(18, int(h * 0.062))

    # During explosion, dots are oversized and shrink to normal — gives impact
    if doing_intro and intro_frac < 0.28:
      explode_scale = 1.0 + 2.5 * (1.0 - _ramp(intro_frac, 0.0, 0.28))
      pulse_r = max(dot_r, int(dot_r * explode_scale))
    else:
      pulse_r = max(dot_r, int(dot_r * (1.0 + eff_beat * 0.40)))

    # ---- Intro position resolver ----
    # Horizontal oval sized to the screen — fills the rectangular frame evenly.
    # rx/ry scale with actual screen dimensions so coverage matches aspect ratio.
    r_explode  = max(w, h) * 0.78   # burst radius — circles fly off screen
    screen_cx  = rect.x + w * 0.5
    screen_cy  = rect.y + h * 0.5

    def _intro_pos(idx: int, tx: float, ty: float) -> tuple[float, float]:
      angle  = self._scat_angle[idx]
      speed  = self._scat_speed[idx]
      dfreq  = self._scat_dfreq[idx]
      dphase = self._scat_dphase[idx]
      # Each circle has its own orbit size — no two trace the same ellipse
      crx    = w * self._scat_rx[idx]
      cry    = h * self._scat_ry[idx]

      # All dots explode from screen center so the burst is perfectly centered
      ox = screen_cx
      oy = screen_cy

      # Wide angular drift — each circle meanders independently like a firefly
      drift = math.sin(intro_frac * math.pi * 2.0 * dfreq + dphase) * 0.80
      eff_a = angle + drift

      if intro_frac < 0.25:
        # Explosion from center — cubic ease-out, can go off screen
        r_t    = intro_frac / 0.25
        r_ease = 1.0 - (1.0 - r_t) ** 3
        sx = ox + math.cos(eff_a) * r_explode * speed * r_ease
        sy = oy + math.sin(eff_a) * r_explode * speed * r_ease * 0.55
      elif intro_frac < 0.45:
        # Pull-back: migrate from explosion to each circle's own orbit
        pull_t = ((intro_frac - 0.25) / 0.20) ** 2   # ease-in
        fx = screen_cx + math.cos(eff_a) * crx
        fy = screen_cy + math.sin(eff_a) * cry
        bx = ox + math.cos(eff_a) * r_explode * speed
        by = oy + math.sin(eff_a) * r_explode * speed * 0.55
        sx, sy = bx + (fx - bx) * pull_t, by + (fy - by) * pull_t
      else:
        # Firefly wander — every circle at its own depth, covering the full frame
        sx = screen_cx + math.cos(eff_a) * crx
        sy = screen_cy + math.sin(eff_a) * cry

      # Phase 3: smooth coalesce toward target face position
      if intro_frac >= 0.70:
        t_c    = (intro_frac - 0.70) / 0.30
        ease_c = t_c * t_c * (3.0 - 2.0 * t_c)   # smooth-step
        sx += (tx - sx) * ease_c
        sy += (ty - sy) * ease_c

      return sx, sy

    # ---- Warp-speed streaks (deepest layer, full intro duration) ----
    if doing_intro:
      warp_in   = _ramp(intro_frac, 0.03, 0.15)
      # Black-hole collapse: an outer boundary shrinks inward, cutting beams off
      # from the outside in — like a closing iris consuming the light.
      # Beams stay full-length; only their visible extent is clamped.
      collapse  = _ramp(intro_frac, 0.50, 1.1)
      # Global outer boundary — very aggressive power curve so it barely moves
      # early then suddenly yanks everything inward.
      outer_cap = 1.0 - collapse ** 0.22
      warp_out  = max(0.0, 1.0 - collapse * 1.8)  # alpha cuts fast at the end
      warp_env  = warp_in * warp_out
      white_t   = _ramp(intro_frac, 0.38, 0.52)
      if warp_env > 0.01 or (doing_intro and collapse > 0 and outer_cap > 0.01):
        max_reach = max(w, h) * 1.20

        for wi in range(len(self._warp_angle)):
          sa = self._warp_angle[wi]

          pos  = (t * self._warp_speed[wi] + self._warp_phase[wi]) % 1.0
          # Large per-beam pull delay — beams retract at wildly different times.
          # _warp_bright (0.55–1.0) and _warp_speed (0.20–0.75) both contribute
          # so adjacent beams behave completely independently.
          beam_delay    = (self._warp_bright[wi] - 0.775) * 0.55   # -0.30 … +0.30
          beam_delay   += (self._warp_speed[wi]  - 0.475) * 0.35   # extra spread from speed
          beam_collapse = max(0.0, min(1.0, collapse + beam_delay))
          # Very low exponent = dramatic snap: boundary barely moves then lurches violently
          beam_cap     = 1.0 - beam_collapse ** 0.15
          # Beam length collapses hard — some become tiny slivers, some stay long
          blen = self._warp_blen[wi] * (1.0 - beam_collapse ** 0.5 * 0.90)

          # Clamp to this beam's personal boundary
          p_head = min(pos,                  beam_cap)
          p_tail = min(max(0.0, pos - blen), beam_cap)
          if p_head <= p_tail:
            continue

          # Alpha envelope: fade in near center, soft fade at edge
          fade_in  = min(1.0, p_head / 0.15)
          fade_out = 1.0 - max(0.0, (p_head - 0.75) / 0.25)
          alpha = int(warp_env * self._warp_bright[wi] * fade_in * fade_out * 230)
          if alpha < 5:
            continue

          sa_cos = math.cos(sa)
          sa_sin = math.sin(sa)
          sx = screen_cx + sa_cos * p_tail * max_reach
          sy = screen_cy + sa_sin * p_tail * max_reach
          ex = screen_cx + sa_cos * p_head * max_reach
          ey = screen_cy + sa_sin * p_head * max_reach

          h_warp  = self._warp_hue[wi]
          lw      = self._warp_width[wi]
          # Saturation fades to 0 (white) as the warp bleaches out before vanishing
          sat_col = 0.85 * (1.0 - white_t)
          sat_glo = 0.40 * (1.0 - white_t)

          # Soft glow aura around the streak
          rl.draw_line_ex(rl.Vector2(sx, sy), rl.Vector2(ex, ey),
                          lw * 5.0, hsv_to_color(h_warp, sat_glo, 1.0, int(alpha * 0.12)))
          # Main streak — bleaches toward white
          rl.draw_line_ex(rl.Vector2(sx, sy), rl.Vector2(ex, ey),
                          lw, hsv_to_color(h_warp, sat_col, 1.0, alpha))
          # Bright white leading tip — the "head" of the shooting streak
          tip_sx = screen_cx + sa_cos * max(0.0, p_head - blen * 0.2) * max_reach
          tip_sy = screen_cy + sa_sin * max(0.0, p_head - blen * 0.2) * max_reach
          rl.draw_line_ex(rl.Vector2(tip_sx, tip_sy), rl.Vector2(ex, ey),
                          max(0.6, lw * 0.5), rl.Color(255, 255, 255, int(alpha * 0.65)))

    # ---- Firework explosion (drawn first so sparks sit behind wandering dots) ----
    if doing_intro and intro_frac < 0.45:
      bx, by     = screen_cx, screen_cy
      burst_dist = max(w, h) * 0.90   # how far sparks can travel

      # Instant white-hot core flash at the button origin
      if intro_frac < 0.15:
        ft     = intro_frac / 0.15
        core_r = int(h * 0.40 * (1.0 - ft ** 0.20))
        core_a = int(255 * (1.0 - ft))
        if core_r > 0:
          rl.draw_circle(int(bx), int(by), core_r, rl.Color(255, 255, 255, core_a))

      # 120 individual sparks — each is a glowing dot + short tail line
      for si in range(len(self._spark_angle)):
        age = intro_frac - self._spark_delay[si]
        if age <= 0 or age > 0.35:
          continue
        life = age / 0.35               # 0 → 1 over spark lifetime
        alpha = int(255 * (1.0 - life) ** 1.8)
        if alpha < 6:
          continue

        # Travel outward; gravity curves the arc downward
        dist  = burst_dist * self._spark_speed[si] * life
        grav  = burst_dist * self._spark_gravity[si] * life * life * 0.35
        sa    = self._spark_angle[si]
        px    = bx + math.cos(sa) * dist
        py    = by + math.sin(sa) * dist + grav   # gravity pulls down

        # Tail: short line segment behind the spark head
        tail_dist = max(4.0, dist * 0.12)
        tx = px - math.cos(sa) * tail_dist
        ty = py - math.sin(sa) * tail_dist - grav * 0.12

        sr = int(self._spark_size[si])
        rl.draw_line_ex(rl.Vector2(tx, ty), rl.Vector2(px, py), 1.5,
                        rl.Color(255, 255, 255, alpha // 4))
        rl.draw_circle(int(px), int(py), sr,
                       rl.Color(255, 255, 255, int(alpha * 0.55)))
        rl.draw_circle_lines(int(px), int(py), sr,
                             rl.Color(200, 200, 200, int(alpha * 0.85)))
        rl.draw_circle_lines(int(px), int(py), sr + 3,
                             rl.Color(255, 255, 255, int(alpha * 0.18)))

      # Shrapnel — tiny fast shards firing over the main burst, sharper falloff
      for xi in range(len(self._shrap_angle)):
        age = intro_frac - self._shrap_delay[xi]
        if age <= 0 or age > 0.18:   # lives only half as long as main sparks
          continue
        life  = age / 0.18
        alpha = int(255 * (1.0 - life) ** 2.5)   # sharper fade = more explosive pop
        if alpha < 8:
          continue

        dist = burst_dist * self._shrap_speed[xi] * life * 1.8   # travels further faster
        grav = burst_dist * self._shrap_gravity[xi] * life * life * 0.15
        sa   = self._shrap_angle[xi]
        px   = bx + math.cos(sa) * dist
        py   = by + math.sin(sa) * dist + grav

        # Long bright streak — shrapnel looks like a shooting shard, not a blob
        streak_len = max(6.0, dist * 0.22)
        tx = px - math.cos(sa) * streak_len
        ty = py - math.sin(sa) * streak_len

        sr = max(1, int(self._shrap_size[xi]))
        rl.draw_line_ex(rl.Vector2(tx, ty), rl.Vector2(px, py), 1.0,
                        rl.Color(255, 255, 255, int(alpha * 0.65)))
        rl.draw_circle(int(px), int(py), sr,
                       rl.Color(255, 255, 255, int(alpha * 0.60)))
        rl.draw_circle_lines(int(px), int(py), sr + 2,
                             rl.Color(220, 220, 220, int(alpha * 0.20)))

    # Hi-hat / clap energy from the two highest spectral bands — drives circle flash.
    if bands is not None and len(bands) >= 16:
      hihat = float(bands[6] + bands[7] + bands[14] + bands[15]) / 4.0
    else:
      hihat = beat_flash * 0.6
    hihat_flash = max(beat_flash * 0.6, hihat)

    # ---- Ephemeral chaos circles (intro only, wildfire colors) ----
    if doing_intro and intro_frac > 0.28:
      chaos_life = _ramp(intro_frac, 0.28, 0.46)
      chaos_die  = 1.0 - _ramp(intro_frac, 0.52, 0.68)
      chaos_env  = chaos_life * chaos_die
      for ci in range(len(self._chaos_angle)):
        drift  = math.sin(intro_frac * math.pi * 2.0 * self._chaos_dfreq[ci] + self._chaos_dphase[ci]) * 1.2
        eff_a  = self._chaos_angle[ci] + drift
        cx_pos = screen_cx + math.cos(eff_a) * (w * self._chaos_rx[ci])
        cy_pos = screen_cy + math.sin(eff_a) * (h * self._chaos_ry[ci])
        breath      = 0.80 + 0.20 * math.sin(t * self._chaos_dfreq[ci] * 2.5 + self._chaos_fphase[ci])
        flash_boost = 1.0 + hihat_flash * 2.0
        alpha = int(min(255, chaos_env * breath * 255 * flash_boost))
        if alpha > 5:
          cr = int(self._chaos_size[ci])
          rl.draw_circle(int(cx_pos), int(cy_pos), cr,      rl.Color(255, 255, 255, int(alpha * 0.80)))
          rl.draw_circle_lines(int(cx_pos), int(cy_pos), cr,      rl.Color(200, 200, 200, int(alpha * 0.95)))
          rl.draw_circle_lines(int(cx_pos), int(cy_pos), cr + 2,  rl.Color(255, 255, 255, int(alpha * 0.40)))
          rl.draw_circle_lines(int(cx_pos), int(cy_pos), cr + 4,  rl.Color(255, 255, 255, int(alpha * 0.18)))
          if hihat_flash > 0.30:
            rl.draw_circle_lines(int(cx_pos), int(cy_pos), cr + int(14 * hihat_flash),
                                 rl.Color(255, 255, 255, int(alpha * hihat_flash * 0.22)))

    # ---- Dot-matrix eyes ----
    # Right eye (index 1) winks: its row offsets are scaled by wink_scale so
    # all dots converge to a single horizontal line when wink_scale → 0.
    dot_idx = 0
    for ei, ecx in enumerate((eye_lx, eye_rx)):
      for (dc, dr) in _EYE_DOTS:
        effective_dr = dr * wink_scale if ei == 1 else dr
        tx, ty = ecx + dc * dot_gap, eye_y + effective_dr * dot_gap
        px, py = _intro_pos(dot_idx, tx, ty) if doing_intro else (tx, ty)
        if doing_intro and intro_frac > 0.10:
          dot_a = int(min(255, a * 0.85 * (1.0 + hihat_flash * 1.8)))
          rl.draw_circle(int(px), int(py), pulse_r,     rl.Color(255, 255, 255, dot_a))
          rl.draw_circle_lines(int(px), int(py), pulse_r,     rl.Color(200, 200, 200, int(min(255, a * 0.95))))
          rl.draw_circle_lines(int(px), int(py), pulse_r + 2, rl.Color(255, 255, 255, int(a * (0.38 + hihat_flash * 0.30))))
          rl.draw_circle_lines(int(px), int(py), pulse_r + 4, rl.Color(255, 255, 255, int(a * (0.15 + hihat_flash * 0.15))))
        else:
          rl.draw_circle(int(px), int(py), pulse_r, face_col)
        dot_idx += 1

    # ---- Small V mouth ----

    mouth_gap   = max(12, int(dot_gap * 0.75))
    mouth_dot_r = max(5,  int(dot_r  * 0.65))
    mouth_cx    = rect.x + w * 0.50
    mouth_y     = rect.y + h * 0.63
    spread      = 1.0 if doing_intro else 1.0 + eff_beat * 0.12 * hype
    for (dc, dr) in _MOUTH_DOTS:
      tx, ty = mouth_cx + dc * mouth_gap * spread, mouth_y + dr * mouth_gap
      px, py = _intro_pos(dot_idx, tx, ty) if doing_intro else (tx, ty)
      if doing_intro and intro_frac > 0.10:
        dot_a = int(min(255, a * 0.85 * (1.0 + hihat_flash * 1.8)))
        rl.draw_circle(int(px), int(py), mouth_dot_r,     rl.Color(255, 255, 255, dot_a))
        rl.draw_circle_lines(int(px), int(py), mouth_dot_r,     rl.Color(200, 200, 200, int(min(255, a * 0.95))))
        rl.draw_circle_lines(int(px), int(py), mouth_dot_r + 2, rl.Color(255, 255, 255, int(a * (0.38 + hihat_flash * 0.30))))
        rl.draw_circle_lines(int(px), int(py), mouth_dot_r + 4, rl.Color(255, 255, 255, int(a * (0.15 + hihat_flash * 0.15))))
      else:
        rl.draw_circle(int(px), int(py), mouth_dot_r, face_col)
      dot_idx += 1

    # ---- Waveform eyebrows — only after intro is complete ----
    # brow_calm reaches 0 early in the outro so eyebrows go quiet well before the wink
    if not doing_intro and brow_calm > 0.01:
      brow_baseline = eye_y - dot_gap * 2.3
      brow_half_w   = dot_gap * 2.8
      max_amp       = dot_gap * 3.8 * brow_calm
      spike         = eff_beat * hype * dot_gap * 2.8 * brow_calm
      line_thick    = max(4.0, dot_r * 0.58 * (1.0 + eff_beat * 0.7 * hype)) * brow_calm
      bar_w         = max(3, int(brow_half_w * 2 / _N_WAVE_PTS * 0.55))

      # Cap only the beat spike so it never pushes the peak above the top
      # clearance — max_amp (which drives bar heights) is left untouched.
      top_clearance = rect.y + h * 0.06
      available     = brow_baseline - top_clearance
      spike = min(spike, max(0.0, available - max_amp * hype))

      for side, ecx in enumerate((eye_lx, eye_rx)):
        pts: list[rl.Vector2] = []
        for i in range(_N_WAVE_PTS):
          frac     = i / (_N_WAVE_PTS - 1)
          px       = ecx - brow_half_w + brow_half_w * 2.0 * frac
          edge_env = math.sin(frac * math.pi) ** 1.4  # steeper taper toward tips

          if bands is not None and len(bands) >= 16:
            band_frac  = frac * 7.0
            b0         = min(int(band_frac), 7)
            b1         = min(b0 + 1, 7)
            t_lin      = band_frac - b0
            t_cos      = 0.5 - 0.5 * math.cos(t_lin * math.pi)  # smoother than linear
            offset     = 8 if side else 0
            amp = (float(bands[b0 + offset]) * (1 - t_cos) +
                   float(bands[b1 + offset]) * t_cos) * max_amp * hype
          else:
            phase = t * _BROW_SWAY_SPD + frac * math.pi * 2.2 + (math.pi * 0.55 if side else 0)
            amp   = (0.18 + 0.38 * abs(math.sin(phase))) * dot_gap * ease * hype * brow_calm

          py = brow_baseline - amp * edge_env - spike * edge_env
          pts.append(rl.Vector2(px, py))

          bar_h   = max(2, int(brow_baseline - py))
          bar_col = hsv_to_color((hue + 160) % 360, sat, 0.9, int(a * 0.38))
          rl.draw_rectangle(int(px - bar_w / 2), int(py), bar_w, bar_h, bar_col)

        for j in range(len(pts) - 1):
          # Glow halo drawn first so the sharp line sits on top
          rl.draw_line_ex(pts[j], pts[j + 1], line_thick * 4.0,
                          rl.Color(brow_col.r, brow_col.g, brow_col.b, int(a * 0.10)))
          rl.draw_line_ex(pts[j], pts[j + 1], line_thick * 1.8,
                          rl.Color(brow_col.r, brow_col.g, brow_col.b, int(a * 0.30)))
          rl.draw_line_ex(pts[j], pts[j + 1], line_thick, brow_col)

    # ---- Outro: simple arched eyebrows replace the waveforms ----
    # Smooth parabolic arch (sin curve) = happy/relaxed brows, not angry.
    # Right eyebrow dips toward the closing eye — like a real wink.
    if not doing_intro and outro_frac > 0.05:
      brow_baseline = eye_y - dot_gap * 2.9
      brow_half_w   = dot_gap * 2.8
      outro_brow_a  = int(_ramp(outro_frac, 0.30, 0.39) * 255)
      outro_thick   = max(9.0, dot_r * 1.6)
      arch_h        = dot_gap * 0.45   # how high the arch rises above baseline
      _N_ARCH       = 12               # segments — enough for a smooth curve

      brow_grey = rl.Color(185, 185, 185, outro_brow_a)

      for side, ecx in enumerate((eye_lx, eye_rx)):
        # Right eyebrow: dips down AND flattens as the eye closes, then restores
        brow_y        = brow_baseline + (dot_gap * (1.0 - wink_scale) * 1.3 if side == 1 else 0.0)
        eff_arch_h    = arch_h * wink_scale if side == 1 else arch_h

        # Parabolic arch: sin(frac*π) peaks in the middle → happy upward curve
        arch_pts = [
          rl.Vector2(ecx - brow_half_w + brow_half_w * 2.0 * k / _N_ARCH,
                     brow_y - eff_arch_h * math.sin(k / _N_ARCH * math.pi))
          for k in range(_N_ARCH + 1)
        ]

        for j in range(len(arch_pts) - 1):
          rl.draw_line_ex(arch_pts[j], arch_pts[j + 1], outro_thick, brow_grey)

    # ---- Particle bursts — kick only, not during outro ----
    is_kick = bands is not None and len(bands) >= 1 and float(bands[0]) > 0.60
    if beat_flash > 0.85 and self._prev_beat_flash <= 0.85 and is_kick and outro_frac < 0.15:
      for ecx in (eye_lx, eye_rx):
        for _ in range(7):
          angle = random.uniform(0, 2 * math.pi)
          speed = random.uniform(80, 240) * hype
          self._particles.append({
            'x0': ecx, 'y0': eye_y,
            'vx': math.cos(angle) * speed,
            'vy': math.sin(angle) * speed - 50,
            'born': now,
            'hue': (hue + random.uniform(-60, 60)) % 360,
          })
    self._prev_beat_flash = beat_flash

    alive = []
    for p in self._particles:
      age = now - p['born']
      if age > _BILLY_PTCL_LIFE:
        continue
      frac   = age / _BILLY_PTCL_LIFE
      px     = p['x0'] + p['vx'] * age
      py     = p['y0'] + p['vy'] * age + 100 * age * age
      alpha  = int(255 * (1 - frac) * ease)
      radius = max(1, int(8 * (1 - frac) * hype))
      rl.draw_circle(int(px), int(py), radius, hsv_to_color(p['hue'], 1.0, 1.0, alpha))
      alive.append(p)
    self._particles = alive

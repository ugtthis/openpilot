"""
Music beat analysis and dancing figure primitives for the MICI settings screen.

AudioAnalysis: decodes the MP3 via ffmpeg, computes beat timestamps and per-frame
spectral energy using numpy FFT.

DancingFigure: draws a procedural stick figure (hat, head, body, arms, legs) that
dances to the music using sin/cos animation driven by playback time + beat events.
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
    self.done = True

  def _find_beat_drop(self, rms: np.ndarray) -> float:
    """Return the timestamp (seconds) of the main energy surge / beat drop."""
    fps = SAMPLE_RATE // HOP_SIZE        # frames per second
    win = max(1, fps)                    # 1-second windows
    n_wins = len(rms) // win
    if n_wins < 6:
      return 0.0

    sec_e = np.array([float(np.mean(rms[i * win:(i + 1) * win])) for i in range(n_wins)])
    max_e = float(np.max(sec_e)) if np.max(sec_e) > 0 else 1.0

    # Find the first second where:
    #   - energy >= 35% of peak  (it's a loud section)
    #   - energy >= 1.6x the preceding 3-second average  (sudden jump)
    #   - we're at least 5 s into the track  (skip short intros)
    for i in range(5, n_wins):
      prev_avg = float(np.mean(sec_e[max(0, i - 3):i]))
      if sec_e[i] >= 0.35 * max_e and (prev_avg < 1e-4 or sec_e[i] >= 1.6 * prev_avg):
        return float(i)

    # Fallback: first time energy crosses 35% of peak
    for i in range(n_wins):
      if sec_e[i] >= 0.35 * max_e:
        return float(i)

    return float(n_wins // 2)

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

# Mouth: 3 small dots in a gentle V
_MOUTH_DOTS: list[tuple[float, float]] = [
  (-1.0, 0.0), (0.0, 0.7), (1.0, 0.0),
]

_N_WAVE_PTS    = 16    # polyline sample points per eyebrow
_BROW_SWAY_SPD = 2.6   # fallback sine speed when analysis not ready
_BILLY_PTCL_LIFE = 0.6


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
    # Each dot originates from a letter in "eyebrow" (7 letters)
    n_letters = 7
    self._dot_letter  = [i * n_letters // n_total for i in range(n_total)]

  # ------------------------------------------------------------------
  def draw(self, rect: rl.Rectangle, t: float, base_hue: float,
           beat_flash: float, energy: float,
           bands: np.ndarray | None = None,
           intro_frac: float = 1.0,
           transition: float = 1.0, hype: float = 1.0) -> None:
    w, h = rect.width, rect.height
    now  = rl.get_time()

    ease = 1.0 - (1.0 - min(transition, 1.0)) ** 3
    a    = int(255 * ease)

    hue      = base_hue % 360
    sat      = 0.15 + 0.85 * hype
    brow_col = hsv_to_color(hue, sat, 1.0, a)

    doing_intro = intro_frac < 0.99
    # Dots appear instantly at explosion start then stay fully opaque
    dot_a = int(255 * min(1.0, intro_frac / 0.12)) if doing_intro else a
    white = rl.Color(255, 255, 255, dot_a)

    # ---- Face layout (target positions) ----
    bounce  = 0.0 if doing_intro else abs(math.sin(t * 1.9)) * h * 0.009 * hype
    eye_y   = rect.y + h * 0.44 - bounce
    eye_lx  = rect.x + w * 0.32
    eye_rx  = rect.x + w * 0.68
    dot_r   = max(8,  int(h * 0.028))
    dot_gap = max(18, int(h * 0.062))

    # Dots throb on beat even while scattered
    pulse_r = max(dot_r, int(dot_r * (1.0 + beat_flash * 0.40)))

    # ---- Intro position resolver ----
    max_scat = max(w, h) * 0.55

    def _intro_pos(idx: int, tx: float, ty: float) -> tuple[float, float]:
      angle  = self._scat_angle[idx]
      speed  = self._scat_speed[idx]
      dfreq  = self._scat_dfreq[idx]
      dphase = self._scat_dphase[idx]
      letter = self._dot_letter[idx]

      # Each dot originates from its letter's horizontal position in the button
      ox = self._btn_lx + self._btn_w * (letter + 0.5) / 7.0
      oy = self._btn_cy

      # Explosion radius grows during phase 1, stays at max through phase 2
      if intro_frac < 0.25:
        r_t    = intro_frac / 0.25
        r_ease = 1.0 - (1.0 - r_t) ** 2   # ease-out: fast start, settling
        r = max_scat * speed * r_ease
      else:
        r = max_scat * speed

      # Slow angular drift during float phase
      drift  = math.sin(intro_frac * math.pi * 2.0 * dfreq + dphase) * 0.38
      eff_a  = angle + drift
      # Flatten vertically — letters were arranged horizontally in the button
      sx = ox + math.cos(eff_a) * r
      sy = oy + math.sin(eff_a) * r * 0.46

      # Phase 3: smooth coalesce toward target face position
      if intro_frac >= 0.70:
        t_c    = (intro_frac - 0.70) / 0.30
        ease_c = t_c * t_c * (3.0 - 2.0 * t_c)   # smooth-step
        sx += (tx - sx) * ease_c
        sy += (ty - sy) * ease_c

      return sx, sy

    # ---- Dot-matrix eyes ----
    dot_idx = 0
    for ecx in (eye_lx, eye_rx):
      for (dc, dr) in _EYE_DOTS:
        tx, ty = ecx + dc * dot_gap, eye_y + dr * dot_gap
        px, py = _intro_pos(dot_idx, tx, ty) if doing_intro else (tx, ty)
        rl.draw_circle(int(px), int(py), pulse_r, white)
        dot_idx += 1

    # ---- Small V mouth ----
    mouth_gap   = max(12, int(dot_gap * 0.75))
    mouth_dot_r = max(5,  int(dot_r  * 0.65))
    mouth_cx    = rect.x + w * 0.50
    mouth_y     = rect.y + h * 0.63
    spread      = 1.0 if doing_intro else 1.0 + beat_flash * 0.12 * hype
    for (dc, dr) in _MOUTH_DOTS:
      tx, ty = mouth_cx + dc * mouth_gap * spread, mouth_y + dr * mouth_gap
      px, py = _intro_pos(dot_idx, tx, ty) if doing_intro else (tx, ty)
      rl.draw_circle(int(px), int(py), mouth_dot_r, white)
      dot_idx += 1

    # ---- Waveform eyebrows — only after intro is complete ----
    if not doing_intro:
      brow_baseline = eye_y - dot_gap * 2.3
      brow_half_w   = dot_gap * 2.8
      max_amp       = dot_gap * 3.8
      spike         = beat_flash * hype * dot_gap * 2.8
      line_thick    = max(4.0, dot_r * 0.58 * (1.0 + beat_flash * 0.7 * hype))
      bar_w         = max(3, int(brow_half_w * 2 / _N_WAVE_PTS * 0.55))

      for side, ecx in enumerate((eye_lx, eye_rx)):
        pts: list[rl.Vector2] = []
        for i in range(_N_WAVE_PTS):
          frac     = i / (_N_WAVE_PTS - 1)
          px       = ecx - brow_half_w + brow_half_w * 2.0 * frac
          edge_env = math.sin(frac * math.pi)

          if bands is not None and len(bands) >= 16:
            band_frac = frac * 7.0
            b0        = min(int(band_frac), 7)
            b1        = min(b0 + 1, 7)
            lerp_t    = band_frac - b0
            offset    = 8 if side else 0
            amp = (float(bands[b0 + offset]) * (1 - lerp_t) +
                   float(bands[b1 + offset]) * lerp_t) * max_amp * hype
          else:
            phase = t * _BROW_SWAY_SPD + frac * math.pi * 2.2 + (math.pi * 0.55 if side else 0)
            amp   = (0.18 + 0.38 * abs(math.sin(phase))) * dot_gap * ease * hype

          py = brow_baseline - amp * edge_env - spike * edge_env
          pts.append(rl.Vector2(px, py))

          bar_h = max(2, int(brow_baseline - py))
          rl.draw_rectangle(int(px - bar_w / 2), int(py), bar_w, bar_h,
                            rl.Color(brow_col.r, brow_col.g, brow_col.b, int(a * 0.35)))

        for j in range(len(pts) - 1):
          rl.draw_line_ex(pts[j], pts[j + 1], line_thick, brow_col)

    # ---- Particle bursts — kick only ----
    is_kick = bands is not None and len(bands) >= 1 and float(bands[0]) > 0.60
    if beat_flash > 0.85 and self._prev_beat_flash <= 0.85 and is_kick:
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

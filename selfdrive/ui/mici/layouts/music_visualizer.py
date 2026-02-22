"""
Music beat analysis and dancing figure primitives for the MICI settings screen.

AudioAnalysis: decodes the MP3 via ffmpeg, computes beat timestamps and per-frame
spectral energy using numpy FFT.

DancingFigure: draws a procedural stick figure (hat, head, body, arms, legs) that
dances to the music using sin/cos animation driven by playback time + beat events.
"""

import math
import os
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
    self.done = True

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

_DANCE_SPEED = 4.5
_SWAY_SPEED  = 2.2


class DancingFigure:
  """
  Procedural stick figure that scales to fill any button rect.

  The body is a tall skinny rounded rectangle; arms and legs extend
  beyond the body into the unused space around it, giving the illusion
  that the whole button "became" a dancing character.
  """

  def __init__(self, hue_offset: float = 0.0):
    self.hue_offset = hue_offset
    self._phase = hue_offset * 0.6

  # ------------------------------------------------------------------
  def draw(self, rect: rl.Rectangle, t: float, base_hue: float,
           beat_flash: float, energy: float) -> None:
    """Draw the dancing figure scaled to rect."""
    h = rect.height
    w = rect.width

    # Scale all anatomical sizes from rect dimensions so the figure
    # always looks proportional regardless of button size.
    body_h     = h * 0.38
    body_w     = min(w * 0.14, body_h * 0.35)   # skinny!
    head_r     = body_w * 1.05
    arm_len    = w * 0.30
    leg_len    = h * 0.28
    arm_thick  = max(3.0, body_w * 0.28)
    leg_thick  = max(4.0, body_w * 0.34)
    hat_cw     = body_w * 1.8
    hat_ch     = body_w * 0.9
    hat_bw     = body_w * 2.6
    hat_bh     = body_w * 0.32

    hue = (base_hue + self.hue_offset) % 360

    # ---- Animation ----
    amp      = 1.0 + beat_flash * 2.0 + energy * 0.6
    sway     = math.sin(t * _SWAY_SPEED  + self._phase) * body_w * 0.5 * amp
    bounce   = abs(math.sin(t * _DANCE_SPEED + self._phase)) * h * 0.025 * amp
    arm_l    =  math.sin(t * _DANCE_SPEED + self._phase)            * 50 * amp
    arm_r    =  math.sin(t * _DANCE_SPEED + self._phase + math.pi)  * 50 * amp
    leg_l    =  math.sin(t * _DANCE_SPEED + self._phase)            * 40 * amp
    leg_r    =  math.sin(t * _DANCE_SPEED + self._phase + math.pi)  * 40 * amp
    hat_pop  = beat_flash * head_r * 0.6

    # ---- Anchor points ----
    cx = rect.x + w * 0.5 + sway
    # body fills roughly the middle-lower 40% of the rect; head+hat sit above
    body_top = rect.y + h * 0.30 - bounce
    body_bot = body_top + body_h
    head_cy  = body_top - head_r - h * 0.03
    shoulder_y = body_top + body_h * 0.15
    hip_y    = body_bot

    # ---- Colors ----
    body_color = hsv_to_color(hue,              0.9, 1.0, 245)
    limb_color = hsv_to_color((hue + 30)  % 360, 0.8, 1.0, 230)
    hat_color  = hsv_to_color((hue + 180) % 360, 1.0, 0.95, 255)
    skin_color = hsv_to_color((hue + 60)  % 360, 0.45, 1.0, 255)
    eye_color  = rl.Color(10, 10, 10, 255)

    # ---- Draw (back to front) ----

    # Legs first so they appear behind the body
    self._draw_leg(cx, hip_y, leg_l, left=True,  len_=leg_len, thick=leg_thick, color=limb_color)
    self._draw_leg(cx, hip_y, leg_r, left=False, len_=leg_len, thick=leg_thick, color=limb_color)

    # Arms behind body too
    self._draw_arm(cx, shoulder_y, arm_l, left=True,  len_=arm_len, thick=arm_thick, color=limb_color)
    self._draw_arm(cx, shoulder_y, arm_r, left=False, len_=arm_len, thick=arm_thick, color=limb_color)

    # Body (skinny rounded rect IS the torso/button)
    body_rect = rl.Rectangle(cx - body_w / 2, body_top, body_w, body_h)
    rl.draw_rectangle_rounded(body_rect, 0.45, 8, body_color)

    # Head
    rl.draw_circle(int(cx), int(head_cy), head_r, skin_color)

    # Eyes
    eye_off = head_r * 0.30
    rl.draw_circle(int(cx - eye_off), int(head_cy - head_r * 0.1), max(1, int(head_r * 0.18)), eye_color)
    rl.draw_circle(int(cx + eye_off), int(head_cy - head_r * 0.1), max(1, int(head_r * 0.18)), eye_color)

    # Smile
    for di in range(-4, 5):
      sx_ = cx + di * head_r * 0.11
      sy_ = head_cy + head_r * 0.35 + abs(di) * head_r * 0.04
      rl.draw_circle(int(sx_), int(sy_), max(1, int(head_r * 0.09)), eye_color)

    # Hat (crown + brim), pops up on beat
    hat_y = head_cy - head_r - hat_ch - hat_pop
    crown = rl.Rectangle(cx - hat_cw / 2, hat_y, hat_cw, hat_ch)
    brim  = rl.Rectangle(cx - hat_bw / 2, hat_y + hat_ch - 1, hat_bw, hat_bh)
    rl.draw_rectangle_rounded(crown, 0.15, 6, hat_color)
    rl.draw_rectangle_rounded(brim,  0.5,  6, hat_color)

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
    base  = 90.0 + side * -20.0   # left≈110°, right≈70°  (screen: 90°=down)
    total = math.radians(base + angle_deg * 0.55)
    ex = hx + math.cos(total) * len_
    ey = hy + math.sin(total) * len_
    rl.draw_line_ex(rl.Vector2(hx, hy), rl.Vector2(ex, ey), thick, color)

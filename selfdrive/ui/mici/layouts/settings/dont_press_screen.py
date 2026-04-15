from __future__ import annotations

import math
import wave
from pathlib import Path
from typing import Any, ClassVar

import numpy as np
import pyray as rl

from openpilot.common.basedir import BASEDIR
from openpilot.system.ui.lib.application import gui_app, FontWeight, MouseEvent, MousePos
from openpilot.system.ui.widgets.nav_widget import NavWidget

BLUE_SCREEN = rl.Color(0, 120, 220, 255)

_DONT_PRESS_WAV = Path(BASEDIR) / "selfdrive/assets/sounds/dont_press_better_now.wav"
_LIGHTSHOW_NPZ = Path(BASEDIR) / "selfdrive/assets/sounds/dont_press_lightshow.npz"

SLIDER_ZONE_H = 100
MARGIN_X = 28
MARGIN_BOTTOM = 16
TRACK_H = 20
THUMB_R = 18
DEFAULT_VOLUME = 0.7

DROP_WINDOW_SEC = 1.0

# Smoothed spectrum meters: attack/release (seconds). Short release keeps motion visible.
_SMOOTH_TAU_ATTACK = 0.042
_SMOOTH_TAU_RELEASE = 0.15
# Mix a bit of raw target into the draw — keeps flow without looking stuck
_SMOOTH_PEP = 0.22

# Dark background ↔ accent: loudness + spectral tilt (bass vs treble)
BG_DARK = (14, 8, 32)
BG_ACCENT_BASS = (55, 25, 95)
BG_ACCENT_TREBLE = (15, 55, 85)

# Spectrum bars: bass (warm) → treble (cool) — one clear readable metaphor
BAR_LOW = (180, 90, 220)
BAR_HIGH = (80, 220, 210)


def _lerp_color(c0: tuple[int, int, int], c1: tuple[int, int, int], u: float) -> tuple[int, int, int]:
  u = max(0.0, min(1.0, u))
  return (
    int(c0[0] + (c1[0] - c0[0]) * u),
    int(c0[1] + (c1[1] - c0[1]) * u),
    int(c0[2] + (c1[2] - c0[2]) * u),
  )


def _wav_duration_sec(path: Path) -> float | None:
  try:
    with wave.open(str(path), "r") as w:
      return w.getnframes() / float(w.getframerate())
  except (wave.Error, OSError, EOFError):
    return None


def _beat_pulse(t: float, beats: np.ndarray) -> float:
  """Gentle 0..1 envelope after each beat — no flash, just emphasis."""
  if beats.size == 0 or t < 0:
    return 0.0
  i = int(np.searchsorted(beats, t, side="right")) - 1
  if i < 0:
    return 0.0
  dt = t - float(beats[i])
  if dt < 0 or dt > 0.35:
    return 0.0
  return math.exp(-9.0 * dt)


class DontPressScreen(NavWidget):
  """Blue panel with optional NPZ-driven visualization; swipe down to dismiss."""

  BACK_TOUCH_AREA_PERCENTAGE = 1.0
  _chime: ClassVar[Any] = None

  def __init__(self):
    super().__init__()
    self._volume = DEFAULT_VOLUME
    self._volume_dragging = False
    self._light_t0: float = 0.0
    self._ls_bands: np.ndarray | None = None
    self._ls_rms: np.ndarray | None = None
    self._ls_beats: np.ndarray | None = None
    self._ls_n_frames: int = 0
    self._ls_drop: float = 7.0
    self._track_duration_sec: float = 14.85
    self._audio_end_sec: float = 15.0
    self._auto_close_done: bool = False
    self._smooth_bands: np.ndarray | None = None

  def _interpolated_bands(self, t: float) -> np.ndarray:
    if self._ls_bands is None or self._ls_n_frames <= 0:
      return np.zeros(16, dtype=np.float32)
    u = (t / self._track_duration_sec) * float(self._ls_n_frames - 1)
    u = float(np.clip(u, 0.0, float(self._ls_n_frames - 1)))
    fi = int(u)
    fi2 = min(fi + 1, self._ls_n_frames - 1)
    w = u - fi
    a = self._ls_bands[fi]
    b = self._ls_bands[fi2]
    return ((1.0 - w) * a + w * b).astype(np.float32)

  def _interpolated_rms(self, t: float) -> float:
    if self._ls_rms is None or self._ls_n_frames <= 0:
      return 0.0
    u = (t / self._track_duration_sec) * float(self._ls_n_frames - 1)
    u = float(np.clip(u, 0.0, float(self._ls_n_frames - 1)))
    fi = int(u)
    fi2 = min(fi + 1, self._ls_n_frames - 1)
    w = u - fi
    return float((1.0 - w) * float(self._ls_rms[fi]) + w * float(self._ls_rms[fi2]))

  def _spatial_smooth_bands(self, bands: np.ndarray) -> np.ndarray:
    """Light blur along frequency — ties neighbors without washing out motion."""
    if bands.size == 0:
      return bands
    p = np.pad(bands.astype(np.float32, copy=False), (1, 1), mode="edge")
    k = np.array([0.04, 0.92, 0.04], dtype=np.float32)
    return np.convolve(p, k, mode="valid")

  def _update_smooth_bands(self, target: np.ndarray) -> np.ndarray:
    """Asymmetric EMA: quick rise, slow decay — reads like a musical level meter."""
    dt = float(rl.get_frame_time())
    if dt <= 0.0 or dt > 0.5:
      dt = 1.0 / 60.0
    if self._smooth_bands is None:
      self._smooth_bands = target.astype(np.float32, copy=True)
      return self._smooth_bands
    out = self._smooth_bands
    for i in range(16):
      d = float(target[i]) - float(out[i])
      tau = _SMOOTH_TAU_ATTACK if d > 0.0 else _SMOOTH_TAU_RELEASE
      k = min(1.0, 1.0 - math.exp(-dt / tau))
      out[i] += d * k
    return out

  def _tick_auto_close(self) -> None:
    if self._auto_close_done:
      return
    if rl.get_time() - self._light_t0 < self._audio_end_sec:
      return
    self._auto_close_done = True
    gui_app.remove_nav_stack_tick(self._tick_auto_close)
    self.dismiss()

  def _load_lightshow(self) -> None:
    self._ls_bands = None
    self._ls_rms = None
    self._ls_beats = None
    self._ls_n_frames = 0
    try:
      data = np.load(_LIGHTSHOW_NPZ, allow_pickle=False)
      self._ls_bands = np.asarray(data["band_frames"], dtype=np.float32)
      self._ls_rms = np.asarray(data["rms_frames"], dtype=np.float32)
      self._ls_beats = np.asarray(data["beats"], dtype=np.float32)
      self._ls_n_frames = int(data["n_frames"])
      self._ls_drop = float(np.asarray(data["beat_drop_time"]).reshape(-1)[0])
      bmax = float(self._ls_beats.max()) if self._ls_beats.size else 14.85
      self._track_duration_sec = max(bmax + 0.12, float(self._ls_n_frames) / 88.0)
    except (OSError, KeyError, ValueError, TypeError):
      pass

  def _in_drop(self, t: float) -> float:
    d = abs(t - self._ls_drop)
    if d >= DROP_WINDOW_SEC:
      return 0.0
    return 1.0 - d / DROP_WINDOW_SEC

  def _volume_zone_top(self) -> float:
    return self._rect.y + self._rect.height - SLIDER_ZONE_H

  def _volume_slider_hit_rect(self) -> rl.Rectangle:
    return rl.Rectangle(self._rect.x, self._volume_zone_top(), self._rect.width, float(SLIDER_ZONE_H))

  def _volume_zone_contains(self, pos: MousePos) -> bool:
    return pos.y >= self._volume_zone_top()

  def _volume_track_rect(self) -> rl.Rectangle:
    tw = self._rect.width - 2 * MARGIN_X
    y = self._rect.y + self._rect.height - MARGIN_BOTTOM - TRACK_H
    return rl.Rectangle(self._rect.x + MARGIN_X, y, tw, TRACK_H)

  def _volume_from_pointer_x(self, x: float) -> float:
    tr = self._volume_track_rect()
    if tr.width <= 0:
      return self._volume
    xc = max(tr.x, min(tr.x + tr.width, x))
    return max(0.0, min(1.0, (xc - tr.x) / tr.width))

  def _volume_mouse_start(self, mouse_event: MouseEvent) -> None:
    hit = self._volume_slider_hit_rect()
    if mouse_event.left_pressed and rl.check_collision_point_rec(mouse_event.pos, hit):
      self._volume_dragging = True
      self._volume = self._volume_from_pointer_x(mouse_event.pos.x)

  def _handle_mouse_event(self, mouse_event: MouseEvent) -> None:
    if self._volume_dragging:
      if mouse_event.left_down or mouse_event.left_pressed:
        self._volume = self._volume_from_pointer_x(mouse_event.pos.x)
      if mouse_event.left_released:
        self._volume_dragging = False
      return

    if self._volume_zone_contains(mouse_event.pos):
      self._volume_mouse_start(mouse_event)
      return
    super()._handle_mouse_event(mouse_event)

  def show_event(self):
    super().show_event()
    self._volume = DEFAULT_VOLUME
    self._volume_dragging = False
    self._auto_close_done = False
    self._smooth_bands = None
    self._load_lightshow()
    wd = _wav_duration_sec(_DONT_PRESS_WAV)
    self._audio_end_sec = wd if wd is not None else self._track_duration_sec
    self._light_t0 = rl.get_time()
    gui_app.add_nav_stack_tick(self._tick_auto_close)

    if not rl.is_audio_device_ready():
      return
    if DontPressScreen._chime is None:
      DontPressScreen._chime = rl.load_sound(str(_DONT_PRESS_WAV))
    if DontPressScreen._chime is not None and rl.is_sound_valid(DontPressScreen._chime):
      rl.set_sound_volume(DontPressScreen._chime, self._volume)
      rl.play_sound(DontPressScreen._chime)

  def hide_event(self):
    gui_app.remove_nav_stack_tick(self._tick_auto_close)
    super().hide_event()
    if DontPressScreen._chime is not None and rl.is_sound_valid(DontPressScreen._chime):
      rl.stop_sound(DontPressScreen._chime)

  def _draw_lightshow(self, rect: rl.Rectangle, t: float) -> None:
    if self._ls_bands is None or self._ls_rms is None:
      rl.draw_rectangle_rec(rect, BLUE_SCREEN)
      return

    ibs = self._spatial_smooth_bands(self._interpolated_bands(t))
    rms = self._interpolated_rms(t)
    drop = self._in_drop(t)
    beats = self._ls_beats
    beat_em = _beat_pulse(t, beats) if beats is not None and beats.size else 0.0

    bar_gain = 1.0 + 0.11 * beat_em + 0.1 * drop
    target = ibs * float(bar_gain)
    smoothed = self._update_smooth_bands(target)
    p = _SMOOTH_PEP
    bands = smoothed * (1.0 - p) + target * p

    # Spectral tilt from display bands
    s = float(np.sum(bands)) + 1e-6
    tilt = float(np.dot(bands, np.arange(16, dtype=np.float32)) / (15.0 * s))

    # Background: dark → gentle accent from loudness + bass/treble balance (no strobe)
    energy = min(1.0, 0.15 + 0.65 * rms + 0.12 * drop)
    hue_side = _lerp_color(BG_ACCENT_BASS, BG_ACCENT_TREBLE, tilt)
    bg = _lerp_color(BG_DARK, hue_side, energy * 0.85)
    rl.draw_rectangle_rec(rect, rl.Color(bg[0], bg[1], bg[2], 255))

    viz_top = rect.y + 8
    viz_bottom = self._volume_zone_top() - 12
    viz_h = max(24.0, viz_bottom - viz_top)
    cx = rect.x + rect.width * 0.5
    cy = viz_top + viz_h * 0.5

    # One soft ring on beat — low alpha, decays smoothly (not alternating / flashy)
    if beat_em > 0.04:
      rad = 28.0 + (1.0 - beat_em) * min(rect.width, viz_h) * 0.38
      a = int(45 * beat_em)
      rl.draw_circle_lines(int(cx), int(cy), int(rad), rl.Color(120, 200, 255, a))

    # 16-band spectrum: height = energy, color = frequency (bass → treble)
    col_w = rect.width / 16.0
    bar_scale = 0.91
    for b in range(16):
      e = float(bands[b])
      h_bar = e * viz_h * bar_scale
      x0 = rect.x + b * col_w + 1.5
      bar_w = max(2.0, col_w - 3.0)
      y0 = viz_bottom - h_bar
      bh = rl.Rectangle(x0, y0, bar_w, h_bar)
      bc = _lerp_color(BAR_LOW, BAR_HIGH, b / 15.0)
      active = min(1.0, e * 1.15)
      fill = _lerp_color(bg, bc, 0.55 + 0.45 * active)
      rl.draw_rectangle_rounded(bh, 0.25, 4, rl.Color(fill[0], fill[1], fill[2], 245))

  def _render(self, rect: rl.Rectangle):
    t = max(0.0, rl.get_time() - self._light_t0)

    viz_rect = rl.Rectangle(rect.x, rect.y, rect.width, max(1.0, self._volume_zone_top() - rect.y))
    self._draw_lightshow(viz_rect, t)

    strip = rl.Rectangle(rect.x, self._volume_zone_top(), rect.width, float(SLIDER_ZONE_H))
    rl.draw_rectangle_rec(strip, rl.Color(0, 25, 55, 200))

    tr = self._volume_track_rect()
    rl.draw_rectangle_rounded(tr, 1.0, 8, rl.Color(0, 60, 110, 255))
    if self._volume > 0.0:
      fill = rl.Rectangle(tr.x, tr.y, tr.width * self._volume, tr.height)
      rl.draw_rectangle_rounded(fill, 1.0, 8, rl.Color(255, 255, 255, 180))

    travel = tr.width - 2 * THUMB_R
    cx = tr.x + THUMB_R + self._volume * travel
    cy = tr.y + tr.height / 2
    rl.draw_circle(int(cx), int(cy), THUMB_R, rl.WHITE)
    rl.draw_circle_lines(int(cx), int(cy), THUMB_R, rl.Color(0, 80, 140, 255))

    pct = int(round(self._volume * 100))
    font = gui_app.font(FontWeight.MEDIUM)
    label = f"volume  {pct}%"
    sz = 28
    rl.draw_text_ex(font, label, rl.Vector2(self._rect.x + MARGIN_X, self._volume_zone_top() + 8), sz, 0, rl.Color(255, 255, 255, 230))

    if DontPressScreen._chime is not None and rl.is_sound_valid(DontPressScreen._chime):
      if rl.is_sound_playing(DontPressScreen._chime):
        rl.set_sound_volume(DontPressScreen._chime, self._volume)

    return None

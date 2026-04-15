from __future__ import annotations

import math
import wave
from pathlib import Path

import pyray as rl

from openpilot.common.basedir import BASEDIR
from openpilot.common.filter_simple import FirstOrderFilter
from openpilot.selfdrive.ui.mici.layouts.settings.music_visualizer import AudioAnalysis, EyebrowBilly
from openpilot.system.ui.lib.application import gui_app
from openpilot.system.ui.widgets.nav_widget import NavWidget

BLUE_SCREEN = rl.Color(0, 120, 220, 255)

_DONT_PRESS_WAV = Path(BASEDIR) / "selfdrive/assets/sounds/dont_press_better_now.wav"
_LIGHTSHOW_NPZ = Path(BASEDIR) / "selfdrive/assets/sounds/dont_press_lightshow.npz"

_OUTRO_SECS = 3.25


def _wav_duration_sec(path: Path) -> float | None:
  try:
    with wave.open(str(path), "r") as w:
      return w.getnframes() / float(w.getframerate())
  except (wave.Error, OSError, EOFError):
    return None


class DontPressScreen(NavWidget):
  """Full-panel EyebrowBilly light show; swipe down to dismiss."""

  BACK_TOUCH_AREA_PERCENTAGE = 1.0

  def __init__(self):
    super().__init__()
    self._auto_close_done = False

    self._music: rl.Music | None = None
    self._analysis: AudioAnalysis | None = None
    self._eyebrow_billy: EyebrowBilly | None = None

    self._beat_idx = 0
    self._prev_music_t = 0.0
    self._on_beat = False
    self._beat_flash = 0.0
    self._energy = 0.0
    self._hue = 0.0
    self._hype = 0.05
    self._is_in_drop = False
    self._energy_filter = FirstOrderFilter(0.0, 0.05, 1 / 60)
    self._music_started = False
    self._screen_start_time = 0.0

  def _reset_visualizer_state(self) -> None:
    self._beat_idx = 0
    self._prev_music_t = 0.0
    self._on_beat = False
    self._beat_flash = 0.0
    self._energy = 0.0
    self._hue = 0.0
    self._hype = 0.05
    self._is_in_drop = False
    self._energy_filter = FirstOrderFilter(0.0, 0.05, 1 / 60)
    self._music_started = False

  def _stop_music(self) -> None:
    if self._music is not None and rl.is_music_valid(self._music):
      rl.stop_music_stream(self._music)
      rl.unload_music_stream(self._music)
    self._music = None

  def _tick_auto_close(self) -> None:
    if self._auto_close_done:
      return
    if self._screen_start_time <= 0.0:
      return
    wd = _wav_duration_sec(_DONT_PRESS_WAV)
    if wd is None:
      return
    if rl.get_time() - self._screen_start_time < wd + 2.0:
      return
    self._auto_close_done = True
    gui_app.remove_nav_stack_tick(self._tick_auto_close)
    self.dismiss()

  def _maybe_dismiss_music_ended(self) -> None:
    if self._auto_close_done or self._music is None:
      return
    if not self._music_started:
      return
    if rl.is_music_stream_playing(self._music):
      return
    self._auto_close_done = True
    gui_app.remove_nav_stack_tick(self._tick_auto_close)
    self.dismiss()

  def _update_music_state(self) -> None:
    if self._music is None or not rl.is_music_valid(self._music):
      return

    rl.update_music_stream(self._music)

    t = rl.get_music_time_played(self._music)
    dt = max(0.0, t - self._prev_music_t)
    self._prev_music_t = t

    self._on_beat = False
    if self._analysis is not None and self._analysis.done and self._analysis.beats:
      while self._beat_idx < len(self._analysis.beats) and self._analysis.beats[self._beat_idx] <= t:
        self._beat_idx += 1
        self._on_beat = True

    if self._analysis is not None and self._analysis.done and self._analysis.rms_frames is not None:
      fi = self._analysis.frame_at(t)
      self._energy = float(self._energy_filter.update(float(self._analysis.rms_frames[fi])))
    else:
      self._energy = 0.15 + 0.05 * math.sin(t * 3.7)

    if self._on_beat:
      self._beat_flash = 1.0
    self._beat_flash = max(0.0, self._beat_flash - dt * 5.0)

    if self._analysis is not None and self._analysis.done:
      drop_t = self._analysis.beat_drop_time
      self._is_in_drop = drop_t > 0 and t >= drop_t
      if drop_t > 0 and t >= drop_t:
        self._hype = min(1.0, (t - drop_t) / 0.5)
      elif drop_t > 0:
        self._hype = 0.55 + 0.15 * min(1.0, t / drop_t)
      else:
        self._hype = min(1.0, t / 0.5)
    else:
      self._is_in_drop = False

    self._hue = (self._hue + dt * 10.0) % 360.0

    if t > 0.5:
      self._music_started = True

    self._maybe_dismiss_music_ended()

  def _update_state(self) -> None:
    # Same order as reference SettingsLayout: music stream + beats/hype/hue before layout/render.
    super()._update_state()
    self._update_music_state()

  def show_event(self):
    super().show_event()
    self._auto_close_done = False
    self._reset_visualizer_state()

    self._analysis = AudioAnalysis.from_cache(str(_LIGHTSHOW_NPZ))
    self._eyebrow_billy = EyebrowBilly(origin_rect=None)

    self._stop_music()
    if not rl.is_audio_device_ready():
      rl.init_audio_device()

    mus = rl.load_music_stream(str(_DONT_PRESS_WAV))
    if rl.is_music_valid(mus):
      self._music = mus
      self._music.looping = False
      rl.set_music_volume(self._music, 1.0)
      rl.play_music_stream(self._music)
    else:
      self._music = None

    self._screen_start_time = rl.get_time()
    gui_app.add_nav_stack_tick(self._tick_auto_close)

  def hide_event(self):
    gui_app.remove_nav_stack_tick(self._tick_auto_close)
    self._stop_music()
    self._eyebrow_billy = None
    self._analysis = None
    super().hide_event()

  def _draw_eyebrow_region(self, rect: rl.Rectangle) -> None:
    if self._eyebrow_billy is None:
      rl.draw_rectangle_rec(rect, BLUE_SCREEN)
      return

    rl.draw_rectangle(int(rect.x), int(rect.y), int(rect.width), int(rect.height), rl.BLACK)

    now = rl.get_time()
    t = rl.get_music_time_played(self._music) if self._music is not None and rl.is_music_valid(self._music) else 0.0

    bands = None
    if (self._analysis is not None and self._analysis.done
        and self._analysis.band_frames is not None):
      fi = self._analysis.frame_at(t)
      bands = self._analysis.band_frames[fi]

    drop_t = self._analysis.beat_drop_time if (self._analysis is not None and self._analysis.done) else 0.0
    intro_dur = drop_t if drop_t > 1.0 else 6.0
    intro_frac = float(min(1.0, (now - self._screen_start_time) / intro_dur))

    song_len = rl.get_music_time_length(self._music) if self._music is not None and rl.is_music_valid(self._music) else 0.0
    time_left = max(0.0, song_len - t)
    outro_frac = max(0.0, 1.0 - time_left / _OUTRO_SECS) if song_len > _OUTRO_SECS else 0.0

    self._eyebrow_billy.draw(
      rect, t, self._hue, self._beat_flash, self._energy,
      bands=bands, intro_frac=intro_frac, outro_frac=outro_frac,
      transition=1.0, hype=self._hype, is_in_drop=self._is_in_drop,
    )

  def _render(self, rect: rl.Rectangle):
    # Full panel: EyebrowBilly paints tint/bg edge-to-edge. Nav swipe handle draws on top in NavWidget.render.
    self._draw_eyebrow_region(rect)
    return None

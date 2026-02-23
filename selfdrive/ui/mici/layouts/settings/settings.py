import math
import threading
from collections.abc import Callable

import pyray as rl

from openpilot.common.filter_simple import FirstOrderFilter
from openpilot.system.ui.widgets.scroller import Scroller
from openpilot.selfdrive.ui.mici.widgets.button import BigButton, PRESSED_SCALE
from openpilot.selfdrive.ui.mici.layouts.settings.toggles import TogglesLayoutMici
from openpilot.selfdrive.ui.mici.layouts.settings.network import NetworkLayoutMici
from openpilot.selfdrive.ui.mici.layouts.settings.device import DeviceLayoutMici, PairBigButton
from openpilot.selfdrive.ui.mici.layouts.settings.developer import DeveloperLayoutMici
from openpilot.selfdrive.ui.mici.layouts.settings.firehose import FirehoseLayout
from openpilot.selfdrive.ui.mici.layouts.music_visualizer import AudioAnalysis, EyebrowBilly, MUSIC_PATH
from openpilot.system.ui.lib.application import gui_app, MousePos
from openpilot.system.ui.widgets import Widget
from openpilot.system.ui.widgets.nav_widget import NavWidget


class SettingsBigButton(BigButton):
  """BigButton that blocks its own tap handler while eyebrow mode is active."""

  def __init__(self, *args, is_dance_active: Callable[[], bool] | None = None, **kwargs):
    super().__init__(*args, **kwargs)
    self._is_dance_active: Callable[[], bool] = is_dance_active or (lambda: False)

  def _get_label_font_size(self) -> int:
    return 64

  def _handle_mouse_release(self, mouse_pos: MousePos) -> None:
    if not self._is_dance_active():
      super()._handle_mouse_release(mouse_pos)


class EyebrowBigButton(SettingsBigButton):
  """The red dome PNG is the entire button — centered, bounces on press."""

  _DOME_RATIO = 0.75  # dome diameter relative to button height

  def __init__(self, *args, **kwargs):
    super().__init__(*args, **kwargs)
    # Compact square-ish slot so the dome doesn't float in a wide empty rectangle.
    h = self._rect.height
    self.set_rect(rl.Rectangle(0, 0, h * 1.3, h))

  def _load_images(self):
    super()._load_images()
    self._txt_dome = gui_app.texture("icons_mici/red_dome_button.png", 256, 256)

  def _render(self, _: rl.Rectangle) -> None:
    scale = self._scale_filter.update(PRESSED_SCALE if self.is_pressed else 1.0)
    size  = self._rect.height * self._DOME_RATIO * scale
    tint  = rl.Color(180, 180, 180, 255) if self.is_pressed else rl.WHITE
    rl.draw_texture_ex(
      self._txt_dome,
      rl.Vector2(self._rect.x + (self._rect.width  - size) / 2,
                 self._rect.y + (self._rect.height - size) / 2),
      0.0, size / self._txt_dome.width, tint,
    )


class SettingsLayout(NavWidget):
  def __init__(self):
    super().__init__()

    # Shared closure so every button can query live dance state
    is_dancing: Callable[[], bool] = lambda: self._dance_active

    # ---- Build all buttons ----
    toggles_panel = TogglesLayoutMici()
    self._toggles_btn = SettingsBigButton("toggles", "", "icons_mici/settings.png", is_dance_active=is_dancing)
    self._toggles_btn.set_click_callback(lambda: gui_app.push_widget(toggles_panel))

    network_panel = NetworkLayoutMici()
    self._network_btn = SettingsBigButton("network", "", "icons_mici/settings/network/wifi_strength_full.png",
                                         icon_size=(76, 56), is_dance_active=is_dancing)
    self._network_btn.set_click_callback(lambda: gui_app.push_widget(network_panel))

    device_panel = DeviceLayoutMici()
    self._device_btn = SettingsBigButton("device", "", "icons_mici/settings/device_icon.png",
                                        icon_size=(74, 60), is_dance_active=is_dancing)
    self._device_btn.set_click_callback(lambda: gui_app.push_widget(device_panel))

    self._pair_btn = PairBigButton()

    developer_panel = DeveloperLayoutMici()
    self._developer_btn = SettingsBigButton("developer", "", "icons_mici/settings/developer_icon.png",
                                           icon_size=(64, 60), is_dance_active=is_dancing)
    self._developer_btn.set_click_callback(lambda: gui_app.push_widget(developer_panel))

    firehose_panel = FirehoseLayout()
    self._firehose_btn = SettingsBigButton("firehose", "", "icons_mici/settings/firehose.png",
                                          icon_size=(52, 62), is_dance_active=is_dancing)
    self._firehose_btn.set_click_callback(lambda: gui_app.push_widget(firehose_panel))

    self._eyebrow_btn = EyebrowBigButton("", is_dance_active=is_dancing)
    self._eyebrow_btn.set_click_callback(self._start_eyebrow_dance)

    self._btn_list: list[Widget] = [
      self._eyebrow_btn,
      self._toggles_btn,
      self._network_btn,
      self._device_btn,
      self._pair_btn,
      self._firehose_btn,
      self._developer_btn,
    ]

    self._scroller = Scroller(list(self._btn_list))
    self.set_back_callback(gui_app.pop_widget)

    # ---- Shared audio / beat-tracking state ----
    self._dance_active = False      # True while eyebrow mode is running (blocks button nav)
    self._music: rl.Music | None = None
    self._audio_initialized = False
    self._analysis: AudioAnalysis | None = None
    self._analysis_thread: threading.Thread | None = None

    self._beat_idx = 0
    self._prev_time = 0.0
    self._on_beat = False
    self._beat_flash = 0.0
    self._energy = 0.0
    self._hue = 0.0
    self._hype = 0.05        # 0=subtle gray, 1=full party
    self._is_in_drop = False # True only after the detected beat drop
    self._energy_filter = FirstOrderFilter(0.0, 0.05, 1 / 60)
    self._music_started = False

    # ---- Eyebrow Billy state ----
    self._eyebrow_active = False
    self._eyebrow_billy: EyebrowBilly | None = None
    self._eyebrow_start_time = 0.0

  # -------------------------------------------------------------------------
  # Eyebrow Billy start / stop
  # -------------------------------------------------------------------------

  def _start_eyebrow_dance(self) -> None:
    if self._dance_active or self._eyebrow_active:
      return

    self._eyebrow_active = True
    self._dance_active   = True   # blocks button navigation for all SettingsBigButtons
    self._eyebrow_start_time = rl.get_time()

    rl.init_audio_device()
    self._audio_initialized = True
    self._music = rl.load_music_stream(MUSIC_PATH)
    self._music.looping = False
    rl.set_music_volume(self._music, 1.0)
    rl.play_music_stream(self._music)

    # Reset beat/energy/hype state
    self._beat_idx     = 0
    self._prev_time    = 0.0
    self._on_beat      = False
    self._beat_flash   = 0.0
    self._energy       = 0.0
    self._hue          = 0.0
    self._hype         = 0.05
    self._is_in_drop   = False
    self._music_started = False

    # Analysis (background thread)
    self._analysis = AudioAnalysis(MUSIC_PATH)
    self._analysis_thread = threading.Thread(target=self._analysis.run, daemon=True)
    self._analysis_thread.start()

    self._eyebrow_billy = EyebrowBilly(origin_rect=self._eyebrow_btn.rect)
    self._scroller.scroll_to(self._eyebrow_btn.rect.x, smooth=True)

  def _stop_eyebrow_dance(self) -> None:
    if not self._eyebrow_active:
      return

    self._eyebrow_active = False
    self._dance_active   = False
    self._eyebrow_billy  = None
    self._analysis       = None

    if self._music is not None:
      rl.stop_music_stream(self._music)
      rl.unload_music_stream(self._music)
      self._music = None
    if self._audio_initialized:
      rl.close_audio_device()
      self._audio_initialized = False

    self._scroller.scroll_to(self._btn_list[0].rect.x, smooth=True)

  # -------------------------------------------------------------------------
  # State update (called every frame by Widget.render)
  # -------------------------------------------------------------------------

  def _update_state(self) -> None:
    super()._update_state()

    if not self._dance_active or self._music is None:
      return

    rl.update_music_stream(self._music)

    t  = rl.get_music_time_played(self._music)
    dt = max(0.0, t - self._prev_time)
    self._prev_time = t

    # Beat detection
    self._on_beat = False
    if self._analysis is not None and self._analysis.done and self._analysis.beats:
      while (self._beat_idx < len(self._analysis.beats)
             and self._analysis.beats[self._beat_idx] <= t):
        self._beat_idx += 1
        self._on_beat = True

    # Energy from RMS (or sine fallback while analysis runs)
    if self._analysis is not None and self._analysis.done and self._analysis.rms_frames is not None:
      fi = self._analysis.frame_at(t)
      self._energy = float(self._energy_filter.update(float(self._analysis.rms_frames[fi])))
    else:
      self._energy = 0.15 + 0.05 * math.sin(t * 3.7)

    # Beat flash — instant spike, exponential decay
    if self._on_beat:
      self._beat_flash = 1.0
    self._beat_flash = max(0.0, self._beat_flash - dt * 5.0)

    # Hype drives eyebrow animation energy (0=still, 1=full party).
    # is_in_drop is the separate gating flag for the background light show.
    if self._analysis is not None and self._analysis.done:
      drop_t = self._analysis.beat_drop_time
      self._is_in_drop = drop_t > 0 and t >= drop_t
      if drop_t > 0 and t >= drop_t:
        # At the drop: rocket to full energy over 0.5 s
        self._hype = min(1.0, (t - drop_t) / 0.5)
      elif drop_t > 0:
        # Pre-drop: eyebrows stay obviously active (0.55→0.70) and build gently.
        # They jump to 1.0 at the drop for a clear "wow" moment.
        self._hype = 0.55 + 0.15 * min(1.0, t / drop_t)
      else:
        # No drop detected: ramp to full quickly so eyebrows always animate.
        # Background stays black because is_in_drop remains False.
        self._hype = min(1.0, t / 0.5)

    # Hue rotation (slow before drop, fast after)
    self._hue = (self._hue + dt * 20.0 * max(0.1, self._hype)) % 360.0

    # Latch "music started" flag (raylib resets get_music_time_played to 0 at song end)
    if t > 0.5:
      self._music_started = True

    if self._music_started and not rl.is_music_stream_playing(self._music):
      self._stop_eyebrow_dance()

  # -------------------------------------------------------------------------
  # Input
  # -------------------------------------------------------------------------

  def _handle_mouse_release(self, mouse_pos: MousePos) -> None:
    if self._eyebrow_active:
      # Tap anywhere exits the full-screen mode (guard against the tap that opened it)
      if rl.get_time() - self._eyebrow_start_time > 0.5:
        self._stop_eyebrow_dance()
    else:
      super()._handle_mouse_release(mouse_pos)

  # -------------------------------------------------------------------------
  # Lifecycle
  # -------------------------------------------------------------------------

  def show_event(self) -> None:
    super().show_event()
    self._scroller.show_event()

  def hide_event(self) -> None:
    super().hide_event()
    self._scroller.hide_event()
    self._stop_eyebrow_dance()

  # -------------------------------------------------------------------------
  # Render
  # -------------------------------------------------------------------------

  def _render(self, rect: rl.Rectangle) -> None:
    # ---- Full-screen Eyebrow Billy mode — skip scroller entirely ----
    if self._eyebrow_active and self._eyebrow_billy is not None:
      rl.draw_rectangle(int(rect.x), int(rect.y), int(rect.width), int(rect.height), rl.BLACK)

      now = rl.get_time()
      t   = rl.get_music_time_played(self._music) if self._music else now

      bands = None
      if (self._analysis is not None and self._analysis.done
          and self._analysis.band_frames is not None):
        fi    = self._analysis.frame_at(t)
        bands = self._analysis.band_frames[fi]

      # Face assembles in 6 s flat — always done well before the beat drop.
      # The assembled face then sits still until the drop fires the eyebrows.
      intro_frac = float(min(1.0, (now - self._eyebrow_start_time) / 6.0))

      # Outro: calm effects + wink over the last 4 s of the song.
      _OUTRO_SECS = 4.0
      song_len   = rl.get_music_time_length(self._music) if self._music else 0.0
      time_left  = max(0.0, song_len - t)
      outro_frac = max(0.0, 1.0 - time_left / _OUTRO_SECS) if song_len > _OUTRO_SECS else 0.0

      self._eyebrow_billy.draw(rect, t, self._hue, self._beat_flash, self._energy,
                               bands=bands, intro_frac=intro_frac, outro_frac=outro_frac,
                               transition=1.0, hype=self._hype, is_in_drop=self._is_in_drop)
      return

    self._scroller.render(rect)

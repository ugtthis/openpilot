import math
import threading

import pyray as rl

from openpilot.common.filter_simple import FirstOrderFilter
from openpilot.common.params import Params
from openpilot.system.ui.widgets.scroller import Scroller
from openpilot.selfdrive.ui.mici.widgets.button import BigButton
from openpilot.selfdrive.ui.mici.layouts.settings.toggles import TogglesLayoutMici
from openpilot.selfdrive.ui.mici.layouts.settings.network import NetworkLayoutMici
from openpilot.selfdrive.ui.mici.layouts.settings.device import DeviceLayoutMici, PairBigButton
from openpilot.selfdrive.ui.mici.layouts.settings.developer import DeveloperLayoutMici
from openpilot.selfdrive.ui.mici.layouts.settings.firehose import FirehoseLayout
from openpilot.selfdrive.ui.mici.layouts.music_visualizer import AudioAnalysis, DancingFigure, hsv_to_color, MUSIC_PATH
from openpilot.system.ui.lib.application import gui_app, FontWeight, MousePos
from openpilot.system.ui.widgets import Widget
from openpilot.system.ui.widgets.nav_widget import NavWidget

# How many beats between each infection
BEATS_PER_INFECTION = 3


class SettingsBigButton(BigButton):
  def _get_label_font_size(self):
    return 64


class SettingsLayout(NavWidget):
  def __init__(self):
    super().__init__()
    self._params = Params()

    # ---- Build all buttons ----
    toggles_panel = TogglesLayoutMici()
    self._toggles_btn = SettingsBigButton("toggles", "", "icons_mici/settings.png")
    self._toggles_btn.set_click_callback(lambda: gui_app.push_widget(toggles_panel))

    network_panel = NetworkLayoutMici()
    self._network_btn = SettingsBigButton("network", "", "icons_mici/settings/network/wifi_strength_full.png", icon_size=(76, 56))
    self._network_btn.set_click_callback(lambda: gui_app.push_widget(network_panel))

    device_panel = DeviceLayoutMici()
    self._device_btn = SettingsBigButton("device", "", "icons_mici/settings/device_icon.png", icon_size=(74, 60))
    self._device_btn.set_click_callback(lambda: gui_app.push_widget(device_panel))

    self._pair_btn = PairBigButton()

    developer_panel = DeveloperLayoutMici()
    self._developer_btn = SettingsBigButton("developer", "", "icons_mici/settings/developer_icon.png", icon_size=(64, 60))
    self._developer_btn.set_click_callback(lambda: gui_app.push_widget(developer_panel))

    firehose_panel = FirehoseLayout()
    self._firehose_btn = SettingsBigButton("firehose", "", "icons_mici/settings/firehose.png", icon_size=(52, 62))
    self._firehose_btn.set_click_callback(lambda: gui_app.push_widget(firehose_panel))

    self._music_btn = SettingsBigButton("don't press...", "", "icons_mici/offroad_alerts/orange_warning.png", icon_size=(62, 62))
    self._music_btn.set_click_callback(self._start_dance)

    # music_btn is FIRST (index 0) -- infection spreads right through the rest
    self._btn_list: list[Widget] = [
      self._music_btn,      # index 0 -- the trigger, shown first
      self._toggles_btn,
      self._network_btn,
      self._device_btn,
      self._pair_btn,
      self._firehose_btn,
      self._developer_btn,
    ]

    self._scroller = Scroller(list(self._btn_list))

    self.set_back_callback(gui_app.pop_widget)

    self._font_medium = gui_app.font(FontWeight.MEDIUM)

    # ---- Dance mode state ----
    self._dance_active = False
    self._dance_start_time = 0.0          # guard against same-frame stop
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
    self._energy_filter = FirstOrderFilter(0.0, 0.05, 1 / 60)
    self._beats_since_last_infect = 0

    # index -> DancingFigure
    self._dancers: dict[int, DancingFigure] = {}
    # order in which buttons get infected (built on start)
    self._infect_queue: list[int] = []

  # -------------------------------------------------------------------------
  # Dance mode start / stop
  # -------------------------------------------------------------------------

  def _start_dance(self) -> None:
    if self._dance_active:
      return

    self._dance_active = True
    self._dance_start_time = rl.get_time()

    # Audio
    rl.init_audio_device()
    self._audio_initialized = True
    self._music = rl.load_music_stream(MUSIC_PATH)
    rl.set_music_volume(self._music, 1.0)
    rl.play_music_stream(self._music)

    # Reset beat state
    self._beat_idx = 0
    self._prev_time = 0.0
    self._on_beat = False
    self._beat_flash = 0.0
    self._energy = 0.0
    self._hue = 0.0
    self._beats_since_last_infect = 0

    # Analysis
    self._analysis = AudioAnalysis(MUSIC_PATH)
    self._analysis_thread = threading.Thread(target=self._analysis.run, daemon=True)
    self._analysis_thread.start()

    # Infection: music_btn is index 0 (first), then spread right
    self._dancers = {}
    self._infect_queue = list(range(len(self._btn_list)))

    # Infect the trigger button immediately
    self._infect_next()

  def _stop_dance(self) -> None:
    if not self._dance_active:
      return

    self._dance_active = False

    if self._music is not None:
      rl.stop_music_stream(self._music)
      rl.unload_music_stream(self._music)
      self._music = None
    if self._audio_initialized:
      rl.close_audio_device()
      self._audio_initialized = False

    self._dancers = {}
    self._infect_queue = []
    self._analysis = None

  def _infect_next(self) -> None:
    """Give the next queued button a dancing figure."""
    if not self._infect_queue:
      return
    idx = self._infect_queue.pop(0)
    hue_offset = idx * (360 / len(self._btn_list))
    self._dancers[idx] = DancingFigure(hue_offset=hue_offset)
    self._beats_since_last_infect = 0

    # Auto-scroll scroller so the newly infected button is visible
    btn = self._btn_list[idx]
    self._scroller.scroll_to(btn.rect.x, smooth=True)

  # -------------------------------------------------------------------------
  # State update
  # -------------------------------------------------------------------------

  def _update_state(self) -> None:
    if not self._dance_active or self._music is None:
      return

    rl.update_music_stream(self._music)

    t = rl.get_music_time_played(self._music)
    dt = max(0.0, t - self._prev_time)
    self._prev_time = t

    # Detect beats
    self._on_beat = False
    if self._analysis is not None and self._analysis.done and self._analysis.beats:
      while (self._beat_idx < len(self._analysis.beats)
             and self._analysis.beats[self._beat_idx] <= t):
        self._beat_idx += 1
        self._on_beat = True

    # Energy
    if self._analysis is not None and self._analysis.done and self._analysis.rms_frames is not None:
      fi = self._analysis.frame_at(t)
      self._energy = float(self._energy_filter.update(float(self._analysis.rms_frames[fi])))
    else:
      self._energy = 0.15 + 0.05 * math.sin(t * 3.7)

    # Beat effects
    if self._on_beat:
      self._beat_flash = 1.0
      self._beats_since_last_infect += 1
      if self._beats_since_last_infect >= BEATS_PER_INFECTION and self._infect_queue:
        self._infect_next()

    self._beat_flash = max(0.0, self._beat_flash - dt * 5.0)
    self._hue = (self._hue + dt * 20.0) % 360.0

    # Stop when song ends
    if t > 0 and not rl.is_music_stream_playing(self._music):
      self._stop_dance()

  # -------------------------------------------------------------------------
  # Input: tap to exit dance mode
  # -------------------------------------------------------------------------

  def _handle_mouse_release(self, mouse_pos: MousePos) -> None:
    if self._dance_active:
      # Ignore the same tap that started the dance (both BigButton and SettingsLayout
      # receive the same mouse release event; the 1-second guard prevents an
      # immediate stop on the initiating tap).
      if rl.get_time() - self._dance_start_time > 1.0:
        self._stop_dance()
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
    self._stop_dance()

  # -------------------------------------------------------------------------
  # Render
  # -------------------------------------------------------------------------

  def _render(self, rect: rl.Rectangle) -> None:
    self._scroller.render(rect)

    if not self._dance_active or not self._dancers:
      return

    t = rl.get_music_time_played(self._music) if self._music else rl.get_time()

    # Draw dancing figures on top of each infected button, clipped to the
    # scroller viewport so figures don't bleed outside the settings panel
    rl.begin_scissor_mode(int(rect.x), int(rect.y), int(rect.width), int(rect.height))

    for idx, figure in self._dancers.items():
      btn = self._btn_list[idx]
      btn_rect = btn.rect

      # Fully opaque background -- completely replaces the button so the
      # dancing figure is all that's visible (no button text bleeding through)
      rl.draw_rectangle_rounded(btn_rect, 0.2, 6, rl.Color(10, 10, 30, 255))

      # Rainbow border that pulses on beat.
      # draw_rectangle_rounded_lines in raylib 5.x takes (rec, roundness, segments, color) -- no lineThick.
      # For a thicker border on beats, draw the outline twice with slight inflation.
      border_hue = (self._hue + figure.hue_offset) % 360
      border_alpha = int(200 + 55 * self._beat_flash)
      border_color = hsv_to_color(border_hue, 1.0, 1.0, border_alpha)
      rl.draw_rectangle_rounded_lines(btn_rect, 0.2, 6, border_color)
      if self._beat_flash > 0.3:
        rl.draw_rectangle_rounded_lines(btn_rect, 0.2, 6, border_color)

      # The stick figure
      figure.draw(btn_rect, t, self._hue, self._beat_flash, self._energy)

    rl.end_scissor_mode()

    # Tap-to-exit hint, fades in after all buttons are infected
    if not self._infect_queue:
      font = gui_app.font()
      alpha = int(100 + 60 * math.sin(rl.get_time() * 2))
      hint_color = rl.Color(255, 255, 255, alpha)
      rl.draw_text_ex(font, "tap to stop", rl.Vector2(rect.x + 8, rect.y + rect.height - 28), 22, 0, hint_color)

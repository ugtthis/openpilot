import math
import threading
from collections.abc import Callable

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
# Seconds for a button to morph into the dancing figure
TRANSITION_DURATION = 0.7


class SettingsBigButton(BigButton):
  """BigButton that can transform into a dancing stick figure."""

  def __init__(self, *args, **kwargs):
    super().__init__(*args, **kwargs)
    self._figure: DancingFigure | None = None
    self._infect_time: float = 0.0
    self._get_dance_state: Callable | None = None

  def start_dancing(self, figure: DancingFigure, get_state: Callable) -> None:
    """Switch this button into dance mode. get_state() → (t, hue, beat_flash, energy)."""
    self._figure = figure
    self._infect_time = rl.get_time()
    self._get_dance_state = get_state

  def stop_dancing(self) -> None:
    self._figure = None
    self._get_dance_state = None

  def _get_label_font_size(self):
    return 64

  def _handle_mouse_release(self, mouse_pos: MousePos) -> None:
    if self._figure is not None:
      # Tapping a dancing button triggers a boost spin instead of navigating
      self._figure.trigger_boost()
    else:
      super()._handle_mouse_release(mouse_pos)

  def _render(self, rect: rl.Rectangle):
    # When dancing, the button draws ITSELF as the animated figure —
    # no external overlay required.
    if self._figure is None or self._get_dance_state is None:
      return super()._render(rect)

    t, hue, beat_flash, energy, hype = self._get_dance_state()
    transition = min(1.0, (rl.get_time() - self._infect_time) / TRANSITION_DURATION)

    self._figure.draw(rect, t, hue, beat_flash, energy, transition, hype)

    # Rainbow border: fades in with transition AND hype (gray before drop, vivid after)
    border_hue   = (hue + self._figure.hue_offset) % 360
    border_sat   = 0.1 + 0.9 * hype
    border_alpha = int((180 + 75 * beat_flash) * transition)
    border_color = hsv_to_color(border_hue, border_sat, 1.0, border_alpha)
    rl.draw_rectangle_rounded_lines(rect, 0.2, 6, border_color)
    if beat_flash > 0.3 and transition > 0.8 and hype > 0.5:
      rl.draw_rectangle_rounded_lines(rect, 0.2, 6, border_color)

    return None


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

    # music_btn is FIRST (index 0) — infection spreads right
    self._btn_list: list[Widget] = [
      self._music_btn,
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
    self._dance_start_time = 0.0
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
    self._hype = 0.05         # 0=still, 1=full party — ramps up at beat drop
    self._energy_filter = FirstOrderFilter(0.0, 0.05, 1 / 60)
    self._beats_since_last_infect = 0
    self._music_started = False

    # Tracks which button indices are currently dancing (for beat logic)
    self._dancers: dict[int, DancingFigure] = {}
    self._infect_queue: list[int] = []

    # Infection spark arc state
    self._infect_arc: tuple | None = None    # (from_idx, to_idx, born_time)
    self._last_infected_idx: int | None = None

  # -------------------------------------------------------------------------
  # Shared state getter — passed into each button so it can query live values
  # -------------------------------------------------------------------------

  def _get_dance_state(self) -> tuple[float, float, float, float, float]:
    """Returns (music_t, hue, beat_flash, energy, hype) for the current frame."""
    t = rl.get_music_time_played(self._music) if self._music else rl.get_time()
    return t, self._hue, self._beat_flash, self._energy, self._hype

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
    self._music.looping = False
    rl.set_music_volume(self._music, 1.0)
    rl.play_music_stream(self._music)

    # Reset beat state
    self._beat_idx = 0
    self._prev_time = 0.0
    self._on_beat = False
    self._beat_flash = 0.0
    self._energy = 0.0
    self._hue = 0.0
    self._hype = 0.05
    self._beats_since_last_infect = 0
    self._music_started = False   # True once we've seen t > 0 from raylib

    # Analysis
    self._analysis = AudioAnalysis(MUSIC_PATH)
    self._analysis_thread = threading.Thread(target=self._analysis.run, daemon=True)
    self._analysis_thread.start()

    # Infection queue: music_btn first, then the rest left→right
    self._dancers = {}
    self._infect_queue = list(range(len(self._btn_list)))
    self._infect_arc = None
    self._last_infected_idx = None
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

    # Restore all buttons to their normal appearance
    for idx in list(self._dancers.keys()):
      btn = self._btn_list[idx]
      if isinstance(btn, SettingsBigButton):
        btn.stop_dancing()

    self._dancers = {}
    self._infect_queue = []
    self._infect_arc = None
    self._last_infected_idx = None
    self._analysis = None

    # Smoothly return to the first button ("don't press...").
    # scroll_to(pos) computes: new_offset = current_offset - pos, so passing
    # the first button's current screen-x (negative when scrolled right)
    # drives the panel back to the beginning.
    self._scroller.scroll_to(self._btn_list[0].rect.x, smooth=True)

  def _infect_next(self) -> None:
    """Transform the next queued button into a dancing figure."""
    if not self._infect_queue:
      return
    idx = self._infect_queue.pop(0)
    hue_offset = idx * (360 / len(self._btn_list))
    figure = DancingFigure(hue_offset=hue_offset)
    self._dancers[idx] = figure

    btn = self._btn_list[idx]
    if isinstance(btn, SettingsBigButton):
      btn.start_dancing(figure, self._get_dance_state)

    # Spark arc from previous infected button → this one
    if self._last_infected_idx is not None:
      self._infect_arc = (self._last_infected_idx, idx, rl.get_time())
    self._last_infected_idx = idx
    self._beats_since_last_infect = 0

    # Auto-scroll so the newly infected button is visible
    self._scroller.scroll_to(btn.rect.x, smooth=True)

  # -------------------------------------------------------------------------
  # State update (called every frame by Widget.render)
  # -------------------------------------------------------------------------

  def _update_state(self) -> None:
    if not self._dance_active or self._music is None:
      return

    rl.update_music_stream(self._music)

    t = rl.get_music_time_played(self._music)
    dt = max(0.0, t - self._prev_time)
    self._prev_time = t

    # Beat detection
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

    # Hype: stays near 0 until the beat drop, then ramps to 1 in 0.5 s
    if self._analysis is not None and self._analysis.done:
      drop_t = self._analysis.beat_drop_time
      if t < drop_t:
        # Gentle build-up: 0.05 → 0.15 as we approach the drop
        self._hype = 0.05 + 0.10 * min(1.0, t / drop_t) if drop_t > 0 else 0.05
      else:
        # Explosive ramp-up over 0.5 s, then stays at 1
        self._hype = min(1.0, (t - drop_t) / 0.5)
    # else: analysis still running, _hype stays at 0.05

    # Hue rotates slowly before drop, fast after
    self._hue = (self._hue + dt * 20.0 * max(0.1, self._hype)) % 360.0

    # Track that music has actually started (raylib resets t→0 when song ends,
    # so we can't use "t > 0" as the end-of-song guard).
    if t > 0.5:
      self._music_started = True

    # Stop when song ends (only after it has started to avoid a false trigger
    # on the first frame where both t==0 and is_playing may flicker).
    if self._music_started and not rl.is_music_stream_playing(self._music):
      self._stop_dance()

  # -------------------------------------------------------------------------
  # Input: tap to stop dance mode
  # -------------------------------------------------------------------------

  def _handle_mouse_release(self, mouse_pos: MousePos) -> None:
    if self._dance_active:
      # Guard: ignore the same release that triggered _start_dance
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
    # The scroller renders all buttons; infected buttons render themselves
    # as dancing figures via SettingsBigButton._render override.
    self._scroller.render(rect)

    # ---- Infection spark arc ----
    _ARC_DURATION = 0.5
    if self._infect_arc is not None:
      from_idx, to_idx, born = self._infect_arc
      age = rl.get_time() - born
      if age < _ARC_DURATION:
        from_btn = self._btn_list[from_idx]
        to_btn   = self._btn_list[to_idx]
        fx = from_btn.rect.x + from_btn.rect.width  / 2
        fy = from_btn.rect.y + from_btn.rect.height / 2
        tx = to_btn.rect.x   + to_btn.rect.width    / 2
        ty = to_btn.rect.y   + to_btn.rect.height   / 2

        progress  = age / _ARC_DURATION
        arc_hue   = (self._hue + 60) % 360

        # Trail: several dots behind the traveling spark
        for i in range(9):
          trail_p   = max(0.0, progress - i * 0.06)
          trail_x   = fx + (tx - fx) * trail_p
          trail_y   = fy + (ty - fy) * trail_p
          trail_a   = int(255 * (1 - i / 9) * (1 - progress * 0.4))
          trail_r   = max(1, 8 - i)
          rl.draw_circle(int(trail_x), int(trail_y), trail_r,
                         hsv_to_color(arc_hue, 1.0, 1.0, trail_a))

        # Leading spark
        dot_x = fx + (tx - fx) * progress
        dot_y = fy + (ty - fy) * progress
        rl.draw_circle(int(dot_x), int(dot_y), 9,
                       hsv_to_color(arc_hue, 0.3, 1.0, 255))

        # Burst ring at destination on arrival
        if progress > 0.82:
          burst_frac = (progress - 0.82) / 0.18
          burst_r    = int(30 * burst_frac)
          burst_a    = int(255 * (1 - burst_frac))
          rl.draw_circle_lines(int(tx), int(ty), max(1, burst_r),
                               hsv_to_color(arc_hue, 1.0, 1.0, burst_a))
      else:
        self._infect_arc = None


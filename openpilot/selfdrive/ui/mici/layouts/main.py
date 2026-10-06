import pyray as rl
from openpilot.cereal import messaging
from openpilot.selfdrive.ui.mici.layouts.home import MiciHomeLayout
from openpilot.selfdrive.ui.mici.layouts.settings.settings import SettingsLayout
from openpilot.selfdrive.ui.mici.layouts.offroad_alerts import MiciOffroadAlerts
from openpilot.selfdrive.ui.ui_state import device, ui_state
from openpilot.selfdrive.ui.mici.layouts.onboarding import OnboardingWindow
from openpilot.selfdrive.ui.mici.layouts.camcorder_view import CamcorderView, MODE_PULL_RESISTANCE, camcorder_available
from openpilot.selfdrive.ui.mici.onroad.augmented_road_view import AugmentedRoadView
from openpilot.selfdrive.ui.body.layouts.onroad import BodyLayout
from openpilot.system.ui.widgets import Widget
from openpilot.system.ui.widgets.scroller import Scroller
from openpilot.system.ui.lib.scroll_panel2 import ScrollState
from openpilot.system.ui.lib.application import gui_app


ONROAD_DELAY = 2.5  # seconds


class SwipeLeftPage(Widget):
  """Stable scroller page that selects the view appropriate for the device state."""

  def __init__(self, camcorder: CamcorderView, onroad: AugmentedRoadView, body: BodyLayout):
    super().__init__()
    self._camcorder = self._child(camcorder)
    self._onroad = self._child(onroad)
    self._body = self._child(body)
    for view in (self._camcorder, self._onroad, self._body):
      view.set_enabled(lambda: self.enabled)
      view.set_touch_valid_callback(self._touch_valid)

  @property
  def active_view(self) -> Widget:
    if ui_state.is_body:
      return self._body
    if camcorder_available(bool(ui_state.is_body), ui_state.ignition, ui_state.panda_type):
      return self._camcorder
    return self._onroad

  @property
  def showing_camcorder(self) -> bool:
    return self.active_view is self._camcorder

  def _render(self, rect: rl.Rectangle):
    self.active_view.render(rect)


class MiciMainLayout(Scroller):
  def __init__(self):
    super().__init__(snap_items=True, spacing=0, pad=0, scroll_indicator=False, edge_shadows=False)

    self._prev_onroad = False
    self._prev_standstill = False
    self._onroad_time_delay: float | None = None
    self._setup = False

    # Initialize widgets
    self._pm = messaging.PubMaster(['bookmarkButton', 'userBookmark'])
    self._home_layout = MiciHomeLayout()
    self._alerts_layout = MiciOffroadAlerts()
    self._settings_layout = SettingsLayout()
    self._camcorder_view = CamcorderView()
    self._onroad_view = AugmentedRoadView(bookmark_callback=self._on_bookmark_clicked)
    self._body_onroad_layout = BodyLayout()
    self._swipe_left_page = SwipeLeftPage(self._camcorder_view, self._onroad_view, self._body_onroad_layout)

    # Initialize widget rects
    for widget in (self._home_layout, self._alerts_layout, self._settings_layout, self._swipe_left_page):
      # TODO: set parent rect and use it if never passed rect from render (like in Scroller)
      widget.set_rect(rl.Rectangle(0, 0, gui_app.width, gui_app.height))

    self._scroller.add_widgets([
      self._alerts_layout,
      self._home_layout,
      self._swipe_left_page,
    ])
    self.set_overscroll_resistance(MODE_PULL_RESISTANCE)
    self.set_scroll_observer(self._handle_mode_pull)
    self._scroller.set_reset_scroll_at_show(False)
    self._scroller.set_scrolling_enabled(lambda: not self._onroad_view.is_swiping_left() and
                                         not self._camcorder_view.settings_active)

    # Set callbacks
    self._setup_callbacks()

    gui_app.add_nav_stack_tick(self._handle_transitions)
    gui_app.add_nav_stack_tick(self._update_camcorder_warmup)
    gui_app.push_widget(self)

    # Start onboarding if terms or training not completed, make sure to push after self
    self._onboarding_window = OnboardingWindow(lambda: gui_app.pop_widgets_to(self))
    if not self._onboarding_window.completed:
      gui_app.push_widget(self._onboarding_window)

  def _setup_callbacks(self):
    self._alerts_layout.set_enabled(lambda: self.enabled)
    self._alerts_layout.set_pairing_callback(self._settings_layout.show_pairing)
    self._home_layout.set_callbacks(
      on_settings=lambda: gui_app.push_widget(self._settings_layout),
      on_alerts=lambda: self._scroll_to(self._alerts_layout),
      alert_count_callback=self._alerts_layout.active_alerts,
      alert_icon_callback=self._alerts_layout.highest_severity_icon,
    )
    for layout in (self._onroad_view, self._body_onroad_layout):
      layout.set_click_callback(lambda: self._scroll_to(self._home_layout))

    device.add_interactive_timeout_callback(self._on_interactive_timeout)
    ui_state.add_ignition_transition_callback(self._on_ignition_changed)

  def _scroll_to(self, layout: Widget):
    layout_x = int(layout.rect.x)
    self._scroller.scroll_to(layout_x, smooth=True)

  def _on_bookmark_clicked(self):
    for service in ('bookmarkButton', 'userBookmark'):
      msg = messaging.new_message(service, valid=True)
      self._pm.send(service, msg)

  def _handle_mode_pull(self, offset: float, min_offset: float, state: ScrollState):
    overscroll = max(0.0, min_offset - offset) if self._swipe_left_page.showing_camcorder else 0.0
    self._camcorder_view.update_mode_pull(overscroll, state == ScrollState.MANUAL_SCROLL)

  def _update_state(self):
    super()._update_state()
    # TODO: Hack to run alert updates while not in view. Add a nav stack tick?
    self._alerts_layout._update_state()

  def _render(self, _):
    if not self._setup:
      if self._alerts_layout.active_alerts() > 0:
        self._scroller.scroll_to(self._alerts_layout.rect.x)
      else:
        self._scroller.scroll_to(self._rect.width)
      self._setup = True

    if self._swipe_left_page.showing_camcorder:
      # The camcorder page reveals and then covers this indicator as it pulls and rebounds.
      self._camcorder_view.draw_mode_pull_indicator()
    super()._render(self._rect)

  def _update_camcorder_warmup(self):
    # The offroad interactive timeout blanks the screen and scrolls home, which
    # also bounds how long an idle camcorder page keeps capture running.
    page = self._swipe_left_page
    on_screen = (self._setup and device.awake and gui_app.get_active_widget() is self and
                 page.showing_camcorder and abs(page.rect.x - self._rect.x) < self._rect.width / 2)
    self._camcorder_view.set_warm(on_screen)

  def _handle_transitions(self):
    # Don't pop if onboarding
    if gui_app.widget_in_stack(self._onboarding_window):
      return

    if ui_state.started != self._prev_onroad:
      self._prev_onroad = ui_state.started

      # ignition on: after delay, leave settings and show swipe-left page
      # ignition off: go home (keep settings on the stack)
      if ui_state.started:
        # Keep the established delayed transition as a fallback if the
        # immediate ignition navigation was interrupted.
        self._onroad_time_delay = rl.get_time()
      else:
        self._onroad_time_delay = None
        self._scroll_to(self._home_layout)

    # FIXME: these two pops can interrupt user interacting in the settings
    if self._onroad_time_delay is not None and rl.get_time() - self._onroad_time_delay >= ONROAD_DELAY:
      gui_app.pop_widgets_to(self, lambda: self._scroll_to(self._swipe_left_page))
      self._onroad_time_delay = None

    # When the car starts moving, leave settings and show swipe-left page
    CS = ui_state.sm["carState"]
    if not CS.standstill and self._prev_standstill:
      gui_app.pop_widgets_to(self, lambda: self._scroll_to(self._swipe_left_page))
    self._prev_standstill = CS.standstill

  def _on_interactive_timeout(self):
    # Don't pop if onboarding
    if gui_app.widget_in_stack(self._onboarding_window):
      return

    if ui_state.started:
      # Don't pop if at standstill
      if not ui_state.sm["carState"].standstill:
        gui_app.pop_widgets_to(self, lambda: self._scroll_to(self._swipe_left_page))
    else:
      # Screen turns off on timeout offroad, so pop immediately without animation
      gui_app.pop_widgets_to(self, instant=True)
      self._scroll_to(self._home_layout)

  def _on_ignition_changed(self):
    self._camcorder_view.on_ignition_transition()

    # Do not dismiss onboarding, but still stop capture above.
    if gui_app.widget_in_stack(self._onboarding_window):
      return

    if ui_state.ignition:
      gui_app.pop_widgets_to(self, lambda: self._scroll_to(self._swipe_left_page))
    else:
      self._onroad_time_delay = None
      self._scroll_to(self._home_layout)

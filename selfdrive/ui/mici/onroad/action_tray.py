import pyray as rl

from enum import IntEnum

from openpilot.common.filter_simple import BounceFilter
from openpilot.selfdrive.ui.mici.widgets.button import PRESSED_SCALE
from openpilot.selfdrive.ui.ui_state import ui_state
from openpilot.system.ui.lib.application import MouseEvent, gui_app
from openpilot.system.ui.widgets import Widget

BUTTON_SIZE = 150
BUTTON_GAP = 12
RIGHT_MARGIN = 6
OPEN_LATCH_DRAG_DISTANCE = 70
OVERSWIPE_EXTRA_DRAG_DISTANCE = 50
MAX_TRAY_DRAG_DISTANCE = OPEN_LATCH_DRAG_DISTANCE + OVERSWIPE_EXTRA_DRAG_DISTANCE
CENTER_Y_FRAC = 0.50
HIDDEN_TRAY_GAP = 12


class TrayState(IntEnum):
  COLLAPSED = 0
  DRAGGING = 1
  EXPANDED = 2


class TrayActionButton(Widget):
  def __init__(self, click_callback=None):
    super().__init__()
    self._click_delay = 0.075
    self._scale_filter = BounceFilter(1.0, 0.1, 1 / gui_app.target_fps)
    self._click_callback = click_callback
    self.set_rect(rl.Rectangle(0, 0, BUTTON_SIZE, BUTTON_SIZE))

  def scaled_rect(self) -> rl.Rectangle:
    scale = self._scale_filter.update(PRESSED_SCALE if self.is_pressed else 1.0)
    return rl.Rectangle(
      self.rect.x + (self.rect.width * (1 - scale)) / 2,
      self.rect.y + (self.rect.height * (1 - scale)) / 2,
      self.rect.width * scale,
      self.rect.height * scale,
    )


class BookmarkActionButton(TrayActionButton):
  def __init__(self, click_callback):
    super().__init__(click_callback)
    self._texture = gui_app.texture("icons_mici/onroad/bookmark.png", BUTTON_SIZE, BUTTON_SIZE)

  def _render(self, _) -> None:
    rect = self.scaled_rect()
    tint = rl.Color(220, 220, 220, 255) if self.is_pressed else rl.WHITE
    rl.draw_texture_ex(self._texture, rl.Vector2(rect.x, rect.y), 0.0, rect.width / self._texture.width, tint)


class NavActionButton(TrayActionButton):
  def __init__(self, primary_click_callback, secondary_click_callback, is_secondary_active):
    super().__init__()
    self._primary_click_callback = primary_click_callback
    self._secondary_click_callback = secondary_click_callback
    self._is_secondary_active = is_secondary_active
    self._primary_texture = gui_app.texture("icons_mici/onroad/bookmark.png", BUTTON_SIZE, BUTTON_SIZE)
    self._secondary_bg = gui_app.texture("icons_mici/buttons/button_circle.png", BUTTON_SIZE, BUTTON_SIZE)
    self._secondary_bg_pressed = gui_app.texture("icons_mici/buttons/button_circle_pressed.png", BUTTON_SIZE, BUTTON_SIZE)
    self._secondary_texture = gui_app.texture("icons_mici/settings.png", 64, 64)

  def _handle_mouse_release(self, mouse_pos) -> None:
    self._click_callback = self._secondary_click_callback if self._is_secondary_active() else self._primary_click_callback
    super()._handle_mouse_release(mouse_pos)

  def _render(self, _) -> None:
    rect = self.scaled_rect()
    tint = rl.Color(220, 220, 220, 255) if self.is_pressed else rl.WHITE
    if self._is_secondary_active():
      bg = self._secondary_bg_pressed if self.is_pressed else self._secondary_bg
      rl.draw_texture_ex(bg, rl.Vector2(rect.x, rect.y), 0.0, rect.width / bg.width, rl.WHITE)
      x = rect.x + (rect.width - self._secondary_texture.width) / 2
      y = rect.y + (rect.height - self._secondary_texture.height) / 2
      rl.draw_texture_ex(self._secondary_texture, rl.Vector2(x, y), 0.0, 1.0, tint)
    else:
      rl.draw_texture_ex(self._primary_texture, rl.Vector2(rect.x, rect.y), 0.0, rect.width / self._primary_texture.width, tint)


class DACActionButton(TrayActionButton):
  def __init__(self, click_callback, is_dac_active):
    super().__init__(click_callback)
    self._is_dac_active = is_dac_active
    self._bg = gui_app.texture("icons_mici/buttons/button_circle.png", BUTTON_SIZE, BUTTON_SIZE)
    self._bg_pressed = gui_app.texture("icons_mici/buttons/button_circle_pressed.png", BUTTON_SIZE, BUTTON_SIZE)
    self._dac_icon = gui_app.texture("icons_dac/dac-btn.png", 64, 64)
    self._onroad_icon = gui_app.texture("icons_dac/onroad-ui-icon.png", 64, 32)

  def _render(self, _) -> None:
    rect = self.scaled_rect()
    bg = self._bg_pressed if self.is_pressed else self._bg
    tint = rl.Color(220, 220, 220, 255) if self.is_pressed else rl.WHITE
    rl.draw_texture_ex(bg, rl.Vector2(rect.x, rect.y), 0.0, rect.width / bg.width, rl.WHITE)
    icon = self._onroad_icon if self._is_dac_active() else self._dac_icon
    x = rect.x + (rect.width - icon.width) / 2
    y = rect.y + (rect.height - icon.height) / 2
    rl.draw_texture_ex(icon, rl.Vector2(x, y), 0.0, 1.0, tint)


class DAC2ActionButton(TrayActionButton):
  def __init__(self, click_callback, is_dac2_active):
    super().__init__(click_callback)
    self._is_dac2_active = is_dac2_active
    self._bg = gui_app.texture("icons_mici/buttons/button_circle.png", BUTTON_SIZE, BUTTON_SIZE)
    self._bg_pressed = gui_app.texture("icons_mici/buttons/button_circle_pressed.png", BUTTON_SIZE, BUTTON_SIZE)
    self._steering_icon = gui_app.texture("icons_mici/wheel.png", 64, 64)
    self._onroad_icon = gui_app.texture("icons_dac/onroad-ui-icon.png", 64, 32)

  def _render(self, _) -> None:
    rect = self.scaled_rect()
    bg = self._bg_pressed if self.is_pressed else self._bg
    tint = rl.Color(220, 220, 220, 255) if self.is_pressed else rl.WHITE
    rl.draw_texture_ex(bg, rl.Vector2(rect.x, rect.y), 0.0, rect.width / bg.width, rl.WHITE)
    icon = self._onroad_icon if self._is_dac2_active() else self._steering_icon
    x = rect.x + (rect.width - icon.width) / 2
    y = rect.y + (rect.height - icon.height) / 2
    rl.draw_texture_ex(icon, rl.Vector2(x, y), 0.0, 1.0, tint)


class SidePanelActionTray(Widget):
  def __init__(self, bookmark_callback, settings_callback, dac_callback, dac2_callback, is_secondary_active, is_dac_active, is_dac2_active):
    super().__init__()
    self._state = TrayState.COLLAPSED
    self._open_filter = BounceFilter(0.0, 0.1, 1 / gui_app.target_fps)
    self._swipe_start_x = 0.0
    self._swipe_current_x = 0.0
    self._drag_progress = 0.0
    self._is_swiping_left = False
    self._touch_handled_by_tray = False
    self._dismiss_tray_on_release = False

    self._bookmark_button = self._child(NavActionButton(self._on_bookmark_clicked, self._on_settings_clicked, is_secondary_active))
    self._dac_button = self._child(DACActionButton(self._on_dac_clicked, is_dac_active))
    self._dac2_button = self._child(DAC2ActionButton(self._on_dac2_clicked, is_dac2_active))
    self._bookmark_callback = bookmark_callback
    self._settings_callback = settings_callback
    self._dac_callback = dac_callback
    self._dac2_callback = dac2_callback

    for button in (self._bookmark_button, self._dac_button, self._dac2_button):
      button.set_touch_valid_callback(self._buttons_can_receive_taps)

  @property
  def open_progress(self) -> float:
    return self._drag_progress if self._state == TrayState.DRAGGING else self._open_filter.x

  def is_swiping_left(self) -> bool:
    return self._is_swiping_left

  def _buttons_can_receive_taps(self) -> bool:
    return self._state == TrayState.EXPANDED and self.open_progress >= 0.99

  def consume_touch_handled_by_tray(self) -> bool:
    touch_handled_by_tray, self._touch_handled_by_tray = self._touch_handled_by_tray, False
    return touch_handled_by_tray

  def collapse(self) -> None:
    self._state = TrayState.COLLAPSED
    self._dismiss_tray_on_release = False
    self._drag_progress = 0.0

  def _drag_distance(self) -> float:
    return max(self._swipe_start_x - self._swipe_current_x, 0.0)

  def _tray_positions(self, progress: float) -> tuple[float, float, float, float]:
    y = self.rect.y + self.rect.height * CENTER_Y_FRAC - BUTTON_SIZE / 2
    visible_bookmark_x = self.rect.x + self.rect.width - (BUTTON_SIZE * 3 + BUTTON_GAP * 2 + RIGHT_MARGIN)
    visible_dac_x = visible_bookmark_x + BUTTON_SIZE + BUTTON_GAP
    visible_dac2_x = visible_dac_x + BUTTON_SIZE + BUTTON_GAP

    # Closed means the whole tray sits offscreen to the right.
    hidden_bookmark_x = self.rect.x + self.rect.width + HIDDEN_TRAY_GAP
    hidden_dac_x = hidden_bookmark_x + BUTTON_SIZE + BUTTON_GAP
    hidden_dac2_x = hidden_dac_x + BUTTON_SIZE + BUTTON_GAP

    if progress <= 1.0:
      t = max(0.0, progress)
      bookmark_x = hidden_bookmark_x + (visible_bookmark_x - hidden_bookmark_x) * t
      dac_x = hidden_dac_x + (visible_dac_x - hidden_dac_x) * t
      dac2_x = hidden_dac2_x + (visible_dac2_x - hidden_dac2_x) * t
    else:
      overswipe_distance = min((progress - 1.0) * OPEN_LATCH_DRAG_DISTANCE, OVERSWIPE_EXTRA_DRAG_DISTANCE)
      bookmark_x = visible_bookmark_x - overswipe_distance
      dac_x = visible_dac_x - overswipe_distance
      dac2_x = visible_dac2_x - overswipe_distance

    return y, bookmark_x, dac_x, dac2_x

  def _on_bookmark_clicked(self) -> None:
    self._touch_handled_by_tray = True
    self.collapse()
    self._bookmark_callback()

  def _on_settings_clicked(self) -> None:
    self._touch_handled_by_tray = True
    self.collapse()
    self._settings_callback()

  def _on_dac_clicked(self) -> None:
    self._touch_handled_by_tray = True
    self.collapse()
    self._dac_callback()

  def _on_dac2_clicked(self) -> None:
    self._touch_handled_by_tray = True
    self.collapse()
    self._dac2_callback()

  def _update_state(self) -> None:
    if self._state == TrayState.DRAGGING:
      drag_distance = min(self._drag_distance(), MAX_TRAY_DRAG_DISTANCE)
      self._drag_progress = drag_distance / OPEN_LATCH_DRAG_DISTANCE
      self._open_filter.x = self._drag_progress
    else:
      target = 1.0 if self._state == TrayState.EXPANDED else 0.0
      self._open_filter.update(target)

  def _layout(self) -> None:
    y, bookmark_x, dac_x, dac2_x = self._tray_positions(self.open_progress)
    self._bookmark_button.set_rect(rl.Rectangle(bookmark_x, y, BUTTON_SIZE, BUTTON_SIZE))
    self._dac_button.set_rect(rl.Rectangle(dac_x, y, BUTTON_SIZE, BUTTON_SIZE))
    self._dac2_button.set_rect(rl.Rectangle(dac2_x, y, BUTTON_SIZE, BUTTON_SIZE))

  def _handle_mouse_event(self, ev: MouseEvent) -> None:
    if not ui_state.started:
      return

    if ev.left_pressed:
      if self._state == TrayState.EXPANDED:
        if not (
          rl.check_collision_point_rec(ev.pos, self._bookmark_button.rect) or
          rl.check_collision_point_rec(ev.pos, self._dac_button.rect) or
          rl.check_collision_point_rec(ev.pos, self._dac2_button.rect)
        ):
          self._dismiss_tray_on_release = True
          self._touch_handled_by_tray = True
      else:
        self._state = TrayState.DRAGGING
        self._swipe_start_x = ev.pos.x
        self._swipe_current_x = ev.pos.x
        self._drag_progress = 0.0
        self._is_swiping_left = False

    elif ev.left_down and self._state == TrayState.DRAGGING:
      self._swipe_current_x = ev.pos.x
      self._is_swiping_left = self._drag_distance() > 0
      if self._is_swiping_left:
        self._touch_handled_by_tray = True

    elif ev.left_released:
      if self._state == TrayState.DRAGGING:
        drag_distance = min(self._drag_distance(), MAX_TRAY_DRAG_DISTANCE)
        self._state = TrayState.EXPANDED if drag_distance > OPEN_LATCH_DRAG_DISTANCE else TrayState.COLLAPSED
        self._drag_progress = drag_distance / OPEN_LATCH_DRAG_DISTANCE
        self._is_swiping_left = False
      elif self._state == TrayState.EXPANDED:
        self._is_swiping_left = False
        if self._dismiss_tray_on_release:
          self._dismiss_tray_on_release = False
          self.collapse()

  def _render(self, _) -> None:
    if self.open_progress <= 1e-2:
      return
    self._bookmark_button.render(self._bookmark_button.rect)
    self._dac_button.render(self._dac_button.rect)
    self._dac2_button.render(self._dac2_button.rect)

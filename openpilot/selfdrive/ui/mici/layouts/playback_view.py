import pyray as rl

from openpilot.selfdrive.ui.mici.layouts.camcorder_style import BODY_COLOR, TEXT_COLOR, draw_physical_button
from openpilot.system.ui.lib.application import FontWeight, MousePos, TextAlignment, TextAlignmentVertical, gui_app
from openpilot.system.ui.widgets import Widget
from openpilot.system.ui.widgets.label import UnifiedLabel

BACK_SIZE = rl.Vector2(120, 52)


class PlaybackView(Widget):
  """Clip list. Clips come later; back returns to the viewfinder."""

  def __init__(self):
    super().__init__()
    self._back_press_started = False
    self._back_rect = rl.Rectangle()
    self._title = UnifiedLabel("playback", 48, FontWeight.DISPLAY,
                               text_color=TEXT_COLOR,
                               alignment=TextAlignment.CENTER,
                               alignment_vertical=TextAlignmentVertical.MIDDLE)
    self._back_label = UnifiedLabel("back", 32, FontWeight.DISPLAY,
                                    text_color=TEXT_COLOR,
                                    alignment=TextAlignment.CENTER,
                                    alignment_vertical=TextAlignmentVertical.MIDDLE)

  def _layout(self):
    self._back_rect = rl.Rectangle(self.rect.x + 12, self.rect.y + 8, BACK_SIZE.x, BACK_SIZE.y)

  def _handle_mouse_press(self, mouse_pos: MousePos):
    self._back_press_started = rl.check_collision_point_rec(mouse_pos, self._back_rect)

  def _handle_mouse_release(self, mouse_pos: MousePos):
    back_pressed = self._back_press_started
    self._back_press_started = False
    if back_pressed and rl.check_collision_point_rec(mouse_pos, self._back_rect):
      gui_app.pop_widget()

  def _render(self, rect: rl.Rectangle):
    rl.draw_rectangle_rec(rect, BODY_COLOR)
    back_face = draw_physical_button(self._back_rect, self.is_pressed and self._back_press_started)
    self._back_label.render(back_face)
    self._title.render(rect)

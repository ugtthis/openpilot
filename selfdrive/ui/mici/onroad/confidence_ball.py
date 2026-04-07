import math
import pyray as rl
from openpilot.selfdrive.ui.mici.onroad import SIDE_PANEL_WIDTH
from openpilot.selfdrive.ui.ui_state import ui_state, UIStatus
from openpilot.system.ui.widgets import Widget
from openpilot.system.ui.lib.application import gui_app
from openpilot.common.filter_simple import FirstOrderFilter


def draw_circle_gradient(center_x: float, center_y: float, radius: int,
                         top: rl.Color, bottom: rl.Color) -> None:
  # Draw a square with the gradient
  rl.draw_rectangle_gradient_v(int(center_x - radius), int(center_y - radius),
                               radius * 2, radius * 2,
                               top, bottom)

  # Paint over square with a ring
  outer_radius = math.ceil(radius * math.sqrt(2)) + 1
  rl.draw_ring(rl.Vector2(int(center_x), int(center_y)), radius, outer_radius,
               0.0, 360.0,
               20, rl.BLACK)


def _confidence_dot_colors(confidence: float, status: UIStatus, demo: bool) -> tuple[rl.Color, rl.Color]:
  if status == UIStatus.ENGAGED or demo:
    if confidence > 0.5:
      return rl.Color(0, 255, 204, 255), rl.Color(0, 255, 38, 255)
    if confidence > 0.2:
      return rl.Color(255, 200, 0, 255), rl.Color(255, 115, 0, 255)
    return rl.Color(255, 0, 21, 255), rl.Color(255, 0, 89, 255)

  if status == UIStatus.OVERRIDE:
    return rl.Color(255, 255, 255, 255), rl.Color(82, 82, 82, 255)

  return rl.Color(50, 50, 50, 255), rl.Color(13, 13, 13, 255)


def draw_confidence_ball_in_rect(content_rect: rl.Rectangle, confidence: float, status: UIStatus, demo: bool = False,
                                 align_right: bool = True) -> None:
  status_dot_radius = 24
  dot_height = (1 - confidence) * (content_rect.height - 2 * status_dot_radius) + status_dot_radius
  dot_height = content_rect.y + dot_height
  dot_center_x = content_rect.x + content_rect.width - status_dot_radius if align_right else content_rect.x + content_rect.width / 2

  top_dot_color, bottom_dot_color = _confidence_dot_colors(confidence, status, demo)
  draw_circle_gradient(dot_center_x, dot_height, status_dot_radius, top_dot_color, bottom_dot_color)


class ConfidenceBall(Widget):
  def __init__(self, demo: bool = False):
    super().__init__()
    self._demo = demo
    self._confidence_filter = FirstOrderFilter(-0.5, 0.5, 1 / gui_app.target_fps)

  def update_filter(self, value: float):
    self._confidence_filter.update(value)

  def render_in_rect(self, content_rect: rl.Rectangle, align_right: bool = True) -> None:
    self._update_state()
    draw_confidence_ball_in_rect(content_rect, self._confidence_filter.x, ui_state.status, self._demo, align_right)

  def _update_state(self):
    if self._demo:
      return

    # animate status dot in from bottom
    if ui_state.status == UIStatus.DISENGAGED:
      self._confidence_filter.update(-0.5)
    else:
      self._confidence_filter.update((1 - max(ui_state.sm['modelV2'].meta.disengagePredictions.brakeDisengageProbs or [1])) *
                                                        (1 - max(ui_state.sm['modelV2'].meta.disengagePredictions.steerOverrideProbs or [1])))

  def _render(self, _):
    content_rect = rl.Rectangle(
      self.rect.x + self.rect.width - SIDE_PANEL_WIDTH,
      self.rect.y,
      SIDE_PANEL_WIDTH,
      self.rect.height,
    )
    draw_confidence_ball_in_rect(content_rect, self._confidence_filter.x, ui_state.status, self._demo, align_right=True)

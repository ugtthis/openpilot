import pyray as rl

from openpilot.cereal.visionipc import VisionStreamType
from openpilot.selfdrive.ui.mici.onroad.cameraview import CameraView
from openpilot.system.ui.lib.application import FontWeight, MousePos, TextAlignment, TextAlignmentVertical
from openpilot.system.ui.widgets.label import UnifiedLabel

# wide = main lens. cabin = driver-monitor (IR).
WIDE = VisionStreamType.VISION_STREAM_WIDE_ROAD
CABIN = VisionStreamType.VISION_STREAM_CABIN

FLIP_SIZE = rl.Vector2(128, 52)


class CamcorderView(CameraView):
  """Camcorder viewfinder. Wide by default, flip to cabin. Record comes later."""

  def __init__(self):
    super().__init__("camerad", WIDE)
    self._set_placeholder_color(rl.BLACK)
    self._title = UnifiedLabel("camcorder", 40, FontWeight.DISPLAY)
    self._waiting = UnifiedLabel("waiting for camera", 32, FontWeight.ROMAN,
                                 text_color=rl.Color(255, 255, 255, 140),
                                 alignment=TextAlignment.CENTER,
                                 alignment_vertical=TextAlignmentVertical.MIDDLE)
    self._flip_label = UnifiedLabel("cabin", 32, FontWeight.DISPLAY,
                                    alignment=TextAlignment.CENTER,
                                    alignment_vertical=TextAlignmentVertical.MIDDLE)
    self._flip_rect = rl.Rectangle(0, 0, FLIP_SIZE.x, FLIP_SIZE.y)

  def _showing_cabin(self) -> bool:
    return self.stream_type == CABIN

  def _flip_camera(self):
    self.switch_stream(WIDE if self._showing_cabin() else CABIN)

  def _handle_mouse_release(self, mouse_pos: MousePos):
    # Flip stays here. Tap anywhere else goes home.
    if rl.check_collision_point_rec(mouse_pos, self._flip_rect):
      self._flip_camera()
      return
    super()._handle_mouse_release(mouse_pos)

  def _update_texture_color_filtering(self):
    # Cabin IR needs the driver-view boost or it looks black.
    self._enhance_driver_val[0] = 1 if self._showing_cabin() else 0
    super()._update_texture_color_filtering()

  def _render(self, rect: rl.Rectangle):
    super()._render(rect)

    self._title.set_position(rect.x + 12, rect.y + 8)
    self._title.render()

    self._flip_rect = rl.Rectangle(rect.x + rect.width - FLIP_SIZE.x - 12, rect.y + 8, FLIP_SIZE.x, FLIP_SIZE.y)
    rl.draw_rectangle_rounded(self._flip_rect, 0.45, 8, rl.Color(255, 255, 255, 48))
    next_camera = "wide" if self._showing_cabin() else "cabin"
    self._flip_label.set_text(next_camera)
    self._flip_label.render(self._flip_rect)

    if self.frame is None:
      self._waiting.render(rect)

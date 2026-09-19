import pyray as rl

from openpilot.cereal.visionipc import VisionStreamType
from openpilot.selfdrive.ui.mici.onroad.cameraview import CameraView
from openpilot.system.ui.lib.application import FontWeight, MousePos, TextAlignment, TextAlignmentVertical
from openpilot.system.ui.widgets.label import UnifiedLabel

# wide = main lens. cabin = driver-monitor (IR).
WIDE = VisionStreamType.VISION_STREAM_WIDE_ROAD
CABIN = VisionStreamType.VISION_STREAM_CABIN

# One number drives crop and dest. Change this, both stay matched.
FEED_ASPECT = 4 / 3
TOP_SHARE = 0.5
FLIP_SIZE = rl.Vector2(128, 52)
TOP_COLOR = rl.Color(180, 180, 180, 255)
BOT_COLOR = rl.Color(220, 220, 220, 255)
FLIP_COLOR = rl.Color(255, 255, 255, 90)


def _aspect(rec: rl.Rectangle) -> float:
  return abs(rec.width) / abs(rec.height) if rec.height else 0.0


def _center_crop(fw: float, fh: float, aspect: float) -> rl.Rectangle:
  # Middle window of `aspect`. Wider sensor → cut sides. Taller → cut top/bottom.
  if fw / fh > aspect:
    w = fh * aspect
    return rl.Rectangle((fw - w) / 2, 0, w, fh)
  h = fw / aspect
  return rl.Rectangle(0, (fh - h) / 2, fw, h)


def _right_pane(rect: rl.Rectangle, aspect: float) -> rl.Rectangle:
  # Largest `aspect` box that fits, pinned right. Leftover width is the rail.
  w = min(rect.width, rect.height * aspect)
  return rl.Rectangle(rect.x + rect.width - w, rect.y, w, rect.height)


class CamcorderView(CameraView):
  """Viewfinder: 4:3 center crop on the right, two flush blocks on the left. Record later."""

  def __init__(self):
    super().__init__("camerad", WIDE)
    self._set_placeholder_color(rl.BLACK)
    self._rail = rl.Rectangle()
    self._feed = rl.Rectangle()
    self._top = rl.Rectangle()
    self._bot = rl.Rectangle()
    self._flip = rl.Rectangle()
    self._waiting = UnifiedLabel("waiting for camera", 32, FontWeight.ROMAN,
                                 text_color=rl.Color(255, 255, 255, 140),
                                 alignment=TextAlignment.CENTER,
                                 alignment_vertical=TextAlignmentVertical.MIDDLE)
    self._flip_label = UnifiedLabel("cabin", 32, FontWeight.DISPLAY,
                                    alignment=TextAlignment.CENTER,
                                    alignment_vertical=TextAlignmentVertical.MIDDLE)

  def _showing_cabin(self) -> bool:
    return self.stream_type == CABIN

  def _flip_camera(self):
    self.switch_stream(WIDE if self._showing_cabin() else CABIN)

  def _source_rect(self) -> rl.Rectangle:
    # CameraView draws this window and fits dest to its aspect. Must stay FEED_ASPECT.
    src = _center_crop(float(self.frame.width), float(self.frame.height), FEED_ASPECT)
    if self._stream_type == CABIN:
      src.width = -src.width
    return src

  def _layout(self):
    self._feed = _right_pane(self.rect, FEED_ASPECT)
    self._rail = rl.Rectangle(self.rect.x, self.rect.y, self._feed.x - self.rect.x, self.rect.height)
    top_h = self._rail.height * TOP_SHARE
    self._top = rl.Rectangle(self._rail.x, self._rail.y, self._rail.width, top_h)
    self._bot = rl.Rectangle(self._rail.x, self._rail.y + top_h, self._rail.width, self._rail.height - top_h)
    self._flip = rl.Rectangle(self._feed.x + self._feed.width - FLIP_SIZE.x - 12,
                              self._feed.y + 8, FLIP_SIZE.x, FLIP_SIZE.y)

  def _handle_mouse_release(self, mouse_pos: MousePos):
    if rl.check_collision_point_rec(mouse_pos, self._flip):
      self._flip_camera()
      return
    if rl.check_collision_point_rec(mouse_pos, self._rail):
      return
    super()._handle_mouse_release(mouse_pos)

  def _update_texture_color_filtering(self):
    # Cabin IR needs the driver-view boost or it looks black.
    self._enhance_driver_val[0] = 1 if self._showing_cabin() else 0
    super()._update_texture_color_filtering()

  def _render(self, rect: rl.Rectangle):
    if self.frame is not None and self._feed.height > 0:
      assert abs(_aspect(self._source_rect()) - FEED_ASPECT) < 0.02
      assert abs(_aspect(self._feed) - FEED_ASPECT) < 0.02

    rl.draw_rectangle_rec(rect, rl.BLACK)
    super()._render(self._feed)
    rl.draw_rectangle_rec(self._top, TOP_COLOR)
    rl.draw_rectangle_rec(self._bot, BOT_COLOR)
    rl.draw_rectangle_rec(self._flip, FLIP_COLOR)
    self._flip_label.set_text("wide" if self._showing_cabin() else "cabin")
    self._flip_label.render(self._flip)
    if self.frame is None:
      self._waiting.render(self._feed)

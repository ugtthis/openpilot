from typing import Literal

import pyray as rl

from openpilot.cereal.visionipc import VisionStreamType
from openpilot.selfdrive.ui.mici.layouts.camcorder_style import (
  BODY_COLOR, DIVIDER_COLOR, OSD_BACKGROUND, OSD_COLOR, RECORD_COLOR,
  VIEWFINDER_EDGE_DARK, VIEWFINDER_EDGE_LIGHT, VIEWFINDER_PANEL_COLOR,
  draw_physical_button, expand, inset, offset,
)
from openpilot.selfdrive.ui.mici.layouts.playback_view import PlaybackView
from openpilot.selfdrive.ui.mici.onroad.cameraview import CameraView
from openpilot.system.ui.lib.application import FontWeight, MousePos, TextAlignment, TextAlignmentVertical, gui_app
from openpilot.system.ui.widgets.label import UnifiedLabel

WIDE = VisionStreamType.VISION_STREAM_WIDE_ROAD
CABIN = VisionStreamType.VISION_STREAM_CABIN

FEED_ASPECT = 4 / 3
PLAYBACK_SLOT_SHARE = 0.5
CAMERA_SWITCH_SIZE = rl.Vector2(128, 52)
CAMERA_SWITCH_RIGHT_MARGIN = 12
CAMERA_SWITCH_TOP_MARGIN = 8
FOLDER_ICON_SIZE = 40
VIEWFINDER_MARGIN = 12
VIEWFINDER_BEZEL_WIDTH = 7
VIEWFINDER_SCREEN_LIP = 2

Control = Literal["playback", "record", "camera_switch"]


def _aspect(rec: rl.Rectangle) -> float:
  return abs(rec.width) / abs(rec.height) if rec.height else 0.0


def _center_crop(fw: float, fh: float, aspect: float) -> rl.Rectangle:
  if fw / fh > aspect:
    w = fh * aspect
    return rl.Rectangle((fw - w) / 2, 0, w, fh)
  h = fw / aspect
  return rl.Rectangle(0, (fh - h) / 2, fw, h)


def _right_pane(rect: rl.Rectangle, aspect: float) -> rl.Rectangle:
  w = min(rect.width, rect.height * aspect)
  return rl.Rectangle(rect.x + rect.width - w, rect.y, w, rect.height)


def _fit_inside(rect: rl.Rectangle, aspect: float) -> rl.Rectangle:
  if rect.width / rect.height > aspect:
    w, h = rect.height * aspect, rect.height
  else:
    w, h = rect.width, rect.width / aspect
  return rl.Rectangle(rect.x + (rect.width - w) / 2,
                      rect.y + (rect.height - h) / 2, w, h)


def _icon_center(rec: rl.Rectangle) -> tuple[float, float, float]:
  return rec.x + rec.width / 2, rec.y + rec.height / 2, min(rec.width, rec.height) * 0.18


def _draw_recessed_viewfinder(pane: rl.Rectangle, feed: rl.Rectangle):
  rl.draw_rectangle_rec(pane, VIEWFINDER_PANEL_COLOR)
  bezel = expand(feed, VIEWFINDER_BEZEL_WIDTH)
  screen_well = rl.Color(3, 3, 3, 255)
  rl.draw_rectangle_rounded(offset(bezel, 1, 1), 0.04, 6, VIEWFINDER_EDGE_LIGHT)
  rl.draw_rectangle_rounded(offset(bezel, -1, -1), 0.04, 6, VIEWFINDER_EDGE_DARK)
  rl.draw_rectangle_rounded(expand(feed, VIEWFINDER_SCREEN_LIP), 0.02, 6, screen_well)


def _draw_pixel_aligned_icon(rec: rl.Rectangle, tex: rl.Texture):
  x = round(rec.x + (rec.width - tex.width) / 2)
  y = round(rec.y + (rec.height - tex.height) / 2)
  rl.draw_texture_v(tex, rl.Vector2(x, y), rl.WHITE)


def _draw_record_icon(rec: rl.Rectangle):
  cx, cy, r = _icon_center(rec)
  rl.draw_circle(int(cx), int(cy), r, RECORD_COLOR)


def _draw_stop_icon(rec: rl.Rectangle):
  cx, cy, r = _icon_center(rec)
  rl.draw_rectangle(int(cx - r), int(cy - r), int(2 * r), int(2 * r), RECORD_COLOR)


class CamcorderView(CameraView):
  def __init__(self):
    super().__init__("camerad", WIDE)
    self._set_placeholder_color(rl.BLACK)
    self._record_button_active = False
    self._pressed_control: Control | None = None
    self._playback = PlaybackView()
    self._folder_icon = gui_app.texture("icons/folder.png", FOLDER_ICON_SIZE, FOLDER_ICON_SIZE)
    self._rail = rl.Rectangle()
    self._camera_pane = rl.Rectangle()
    self._feed = rl.Rectangle()
    self._playback_slot = rl.Rectangle()
    self._record_slot = rl.Rectangle()
    self._camera_switch = rl.Rectangle()
    self._waiting = UnifiedLabel("waiting for camera", 32, FontWeight.ROMAN,
                                 text_color=OSD_COLOR,
                                 alignment=TextAlignment.CENTER,
                                 alignment_vertical=TextAlignmentVertical.MIDDLE)
    self._camera_switch_label = UnifiedLabel("cabin", 32, FontWeight.DISPLAY,
                                             text_color=OSD_COLOR,
                                             alignment=TextAlignment.CENTER,
                                             alignment_vertical=TextAlignmentVertical.MIDDLE)

  def _showing_cabin(self) -> bool:
    return self.stream_type == CABIN

  def _switch_camera(self):
    self.switch_stream(WIDE if self._showing_cabin() else CABIN)

  def _source_rect(self) -> rl.Rectangle:
    assert self.frame is not None
    src = _center_crop(float(self.frame.width), float(self.frame.height), FEED_ASPECT)
    if self._showing_cabin():
      src.width = -src.width
    return src

  def _layout(self):
    self._camera_pane = _right_pane(self.rect, FEED_ASPECT)
    self._rail = rl.Rectangle(self.rect.x, self.rect.y,
                              self._camera_pane.x - self.rect.x, self.rect.height)
    self._feed = _fit_inside(inset(self._camera_pane, VIEWFINDER_MARGIN), FEED_ASPECT)
    playback_height = self._rail.height * PLAYBACK_SLOT_SHARE
    self._playback_slot = rl.Rectangle(self._rail.x, self._rail.y, self._rail.width, playback_height)
    self._record_slot = rl.Rectangle(self._rail.x, self._rail.y + playback_height,
                                     self._rail.width, self._rail.height - playback_height)
    self._camera_switch = rl.Rectangle(
      self._feed.x + self._feed.width - CAMERA_SWITCH_SIZE.x - CAMERA_SWITCH_RIGHT_MARGIN,
      self._feed.y + CAMERA_SWITCH_TOP_MARGIN,
      CAMERA_SWITCH_SIZE.x,
      CAMERA_SWITCH_SIZE.y,
    )

  def _handle_mouse_press(self, mouse_pos: MousePos):
    self._pressed_control = None
    if rl.check_collision_point_rec(mouse_pos, self._playback_slot):
      self._pressed_control = "playback"
    elif rl.check_collision_point_rec(mouse_pos, self._record_slot):
      self._pressed_control = "record"
    elif rl.check_collision_point_rec(mouse_pos, self._camera_switch):
      self._pressed_control = "camera_switch"

  def _handle_mouse_release(self, mouse_pos: MousePos):
    pressed_control = self._pressed_control
    self._pressed_control = None
    if pressed_control == "camera_switch" and rl.check_collision_point_rec(mouse_pos, self._camera_switch):
      self._switch_camera()
      return
    if pressed_control == "playback" and rl.check_collision_point_rec(mouse_pos, self._playback_slot):
      gui_app.push_widget(self._playback)
      return
    if pressed_control == "record" and rl.check_collision_point_rec(mouse_pos, self._record_slot):
      self._record_button_active = not self._record_button_active
      return
    super()._handle_mouse_release(mouse_pos)

  def _update_texture_color_filtering(self):
    enhance_cabin_ir = self._showing_cabin()
    self._enhance_driver_val[0] = int(enhance_cabin_ir)
    super()._update_texture_color_filtering()

  def _render(self, rect: rl.Rectangle):
    if self.frame is not None and self._feed.height > 0:
      assert abs(_aspect(self._source_rect()) - FEED_ASPECT) < 0.02
      assert abs(_aspect(self._feed) - FEED_ASPECT) < 0.02

    rl.draw_rectangle_rec(rect, rl.BLACK)
    _draw_recessed_viewfinder(self._camera_pane, self._feed)
    super()._render(self._feed)
    rl.draw_rectangle_rec(self._rail, BODY_COLOR)
    rl.draw_line_ex(rl.Vector2(self._rail.x, self._record_slot.y),
                    rl.Vector2(self._rail.x + self._rail.width, self._record_slot.y),
                    2, DIVIDER_COLOR)

    playback_pressed = self.is_pressed and self._pressed_control == "playback"
    record_pressed = self._record_button_active or (self.is_pressed and self._pressed_control == "record")
    playback_face = draw_physical_button(self._playback_slot, playback_pressed)
    record_face = draw_physical_button(self._record_slot, record_pressed)
    _draw_pixel_aligned_icon(playback_face, self._folder_icon)
    if self._record_button_active:
      _draw_stop_icon(record_face)
    else:
      _draw_record_icon(record_face)
    rl.draw_rectangle_rounded(self._camera_switch, 0.15, 6, OSD_BACKGROUND)
    rl.draw_rectangle_rounded_lines_ex(self._camera_switch, 0.15, 6, 1, OSD_COLOR)
    self._camera_switch_label.set_text("wide" if self._showing_cabin() else "cabin")
    self._camera_switch_label.render(self._camera_switch)
    if self.frame is None:
      self._waiting.render(self._feed)

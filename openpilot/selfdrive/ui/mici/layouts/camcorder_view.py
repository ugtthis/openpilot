import pyray as rl

from openpilot.cereal.visionipc import VisionStreamType
from openpilot.selfdrive.ui.mici.layouts.camcorder_clips import ClipRecorder, center_crop, format_timecode
from openpilot.selfdrive.ui.mici.layouts.camcorder_style import (
  OSD_BACKGROUND, OSD_COLOR, RECORD_COLOR,
  camera_body, draw_centered_texture, draw_physical_button, draw_rail, draw_recessed_viewfinder, hit_name, split_rail,
)
from openpilot.selfdrive.ui.mici.layouts.playback_view import PlaybackView
from openpilot.selfdrive.ui.mici.onroad.cameraview import CameraView
from openpilot.selfdrive.ui.ui_state import device
from openpilot.system.ui.lib.application import FontWeight, MousePos, TextAlignment, TextAlignmentVertical, gui_app
from openpilot.system.ui.widgets.label import UnifiedLabel

WIDE = VisionStreamType.VISION_STREAM_WIDE_ROAD
CABIN = VisionStreamType.VISION_STREAM_CABIN
FOLDER_ICON_SIZE = 40
RECORD_TIMEOUT_S = 3600


def _icon_center(rec: rl.Rectangle) -> tuple[float, float, float]:
  return rec.x + rec.width / 2, rec.y + rec.height / 2, min(rec.width, rec.height) * 0.18


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
    self._recorder = ClipRecorder()
    self._pressed: str | None = None
    self._playback = PlaybackView()
    self._folder_icon = gui_app.texture("icons/folder.png", FOLDER_ICON_SIZE, FOLDER_ICON_SIZE)
    self._rail = rl.Rectangle()
    self._camera_pane = rl.Rectangle()
    self._feed = rl.Rectangle()
    self._playback_slot = rl.Rectangle()
    self._record_slot = rl.Rectangle()
    self._waiting = UnifiedLabel("waiting for camera", 32, FontWeight.ROMAN,
                                 text_color=OSD_COLOR,
                                 alignment=TextAlignment.CENTER,
                                 alignment_vertical=TextAlignmentVertical.MIDDLE)
    self._rec_osd = UnifiedLabel("", 22, FontWeight.DISPLAY,
                                 text_color=OSD_COLOR,
                                 alignment=TextAlignment.LEFT,
                                 alignment_vertical=TextAlignmentVertical.MIDDLE)

  def _showing_cabin(self) -> bool:
    return self.stream_type == CABIN

  def _switch_camera(self):
    if self._recorder.recording:
      return
    self.switch_stream(WIDE if self._showing_cabin() else CABIN)

  def _toggle_record(self):
    if self._recorder.recording:
      clip = self._recorder.stop()
      device.set_override_interactive_timeout(None)
      if clip is not None:
        self._playback.review(clip)
      return
    if self._recorder.start(self.stream_type):
      device.set_override_interactive_timeout(RECORD_TIMEOUT_S)

  def _source_rect(self) -> rl.Rectangle:
    if self.frame is None:
      return rl.Rectangle()
    x, y, w, h = center_crop(self.frame.width, self.frame.height)
    if self._showing_cabin():
      w = -w
    return rl.Rectangle(x, y, w, h)

  def _controls(self) -> list[tuple[str, rl.Rectangle]]:
    hits = [("record", self._record_slot), ("feed", self._feed)]
    if not self._recorder.recording:
      hits.insert(0, ("playback", self._playback_slot))
    return hits

  def _layout(self):
    self._rail, self._camera_pane, self._feed = camera_body(self.rect)
    self._playback_slot, self._record_slot = split_rail(self._rail, 2)

  def _handle_mouse_press(self, mouse_pos: MousePos):
    self._pressed = hit_name(mouse_pos, self._controls())

  def _handle_mouse_release(self, mouse_pos: MousePos):
    pressed = self._pressed
    self._pressed = None
    if pressed is None or hit_name(mouse_pos, self._controls()) != pressed:
      return
    if pressed == "feed":
      self._switch_camera()
    elif pressed == "playback":
      gui_app.push_widget(self._playback)
    elif pressed == "record":
      self._toggle_record()

  def _update_texture_color_filtering(self):
    self._enhance_driver_val[0] = int(self._showing_cabin())
    super()._update_texture_color_filtering()

  def _draw_rec_osd(self):
    elapsed = self._recorder.elapsed_s
    chip = rl.Rectangle(self._feed.x + 8, self._feed.y + 8, 108, 28)
    rl.draw_rectangle_rounded(chip, 0.3, 6, OSD_BACKGROUND)
    if int(elapsed * 2) % 2 == 0:
      rl.draw_circle(int(chip.x + 12), int(chip.y + chip.height / 2), 5, RECORD_COLOR)
    self._rec_osd.set_text(format_timecode(elapsed))
    self._rec_osd.render(rl.Rectangle(chip.x + 22, chip.y, chip.width - 26, chip.height))

  def _render(self, rect: rl.Rectangle):
    recording = self._recorder.recording
    rl.draw_rectangle_rec(rect, rl.BLACK)
    draw_recessed_viewfinder(self._camera_pane, self._feed)
    super()._render(self._feed)
    draw_rail(self._rail, [self._playback_slot, self._record_slot])

    playback_face = draw_physical_button(self._playback_slot, self.is_pressed and self._pressed == "playback")
    record_face = draw_physical_button(self._record_slot, recording or (self.is_pressed and self._pressed == "record"))
    folder_color = rl.Color(255, 255, 255, 70) if recording else rl.WHITE
    draw_centered_texture(playback_face, self._folder_icon, folder_color)
    if recording:
      _draw_stop_icon(record_face)
      self._draw_rec_osd()
    else:
      _draw_record_icon(record_face)
    if self.frame is None:
      self._waiting.render(self._feed)

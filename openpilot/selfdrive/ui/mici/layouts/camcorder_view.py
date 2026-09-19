import math

import pyray as rl

from openpilot.cereal.visionipc import VisionStreamType
from openpilot.common.swaglog import cloudlog
from openpilot.selfdrive.ui.mici.layouts.camcorder_recorder import ClipRecorder
from openpilot.selfdrive.ui.mici.layouts.clip_storage import ClipWriter, center_crop, extract_clip_rgb, format_timecode
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
SNAPSHOT_FLASH_S = 0.12

MODE_PULL_RESISTANCE = 0.45
MODE_PULL_START_PX = 20
MODE_PULL_COMMIT_PX = 170
MODE_PULL_DISARM_PROGRESS = 0.75
MODE_BOUNCE_S = 0.35
MODE_BOUNCE_DAMPING = 10.0
MODE_BOUNCE_FREQUENCY = 20.0
MODE_BOUNCE_AMPLITUDE = 0.16
MODE_FADE_END_PROGRESS = 0.16
MODE_FILL_START_PROGRESS = 0.62
MODE_GLOW_START_PROGRESS = 0.82
MODE_RING_MIN_RADIUS = 36
MODE_RING_MAX_RADIUS = 68
MODE_RING_WIDTH = 6
MODE_RING_MIN_SEGMENTS = 96
MODE_RING_SEGMENTS_PER_RADIUS = 2.5
MODE_ICON_MIN_SIZE = 28
MODE_ICON_MAX_SIZE = 56


def _clamp01(value: float) -> float:
  return min(1.0, max(0.0, value))


def _smoothstep(value: float) -> float:
  value = _clamp01(value)
  return value * value * (3.0 - 2.0 * value)


class ModePullGesture:
  """State machine for the pull-to-toggle camera mode gesture."""

  def __init__(self):
    self.progress = 0.0
    self.bounce_started = -MODE_BOUNCE_S
    self._armed = False
    self._committed = False
    self._dragging = False

  def reset(self):
    self.progress = 0.0
    self.bounce_started = -MODE_BOUNCE_S
    self._armed = False
    self._committed = False
    self._dragging = False

  def update(self, overscroll: float, dragging: bool, now: float) -> bool:
    raw_progress = _clamp01((overscroll - MODE_PULL_START_PX) /
                            (MODE_PULL_COMMIT_PX - MODE_PULL_START_PX))
    if raw_progress >= 1.0 and self.progress < 1.0:
      self.bounce_started = now

    if dragging:
      if raw_progress >= 1.0:
        self._armed = True
      elif raw_progress < MODE_PULL_DISARM_PROGRESS:
        self._armed = False

    toggled = self._dragging and not dragging and self._armed
    if toggled:
      self._committed = True

    if self._committed and raw_progress > 0.0:
      self.progress = 1.0
    else:
      if raw_progress <= 0.0:
        self._committed = False
      self.progress = raw_progress

    if not dragging:
      self._armed = False
    self._dragging = dragging
    return toggled


def _icon_center(rec: rl.Rectangle) -> tuple[float, float, float]:
  return rec.x + rec.width / 2, rec.y + rec.height / 2, min(rec.width, rec.height) * 0.18


def _draw_record_icon(rec: rl.Rectangle):
  cx, cy, r = _icon_center(rec)
  rl.draw_circle(int(cx), int(cy), r, RECORD_COLOR)


def _draw_stop_icon(rec: rl.Rectangle):
  cx, cy, r = _icon_center(rec)
  rl.draw_rectangle(int(cx - r), int(cy - r), int(2 * r), int(2 * r), RECORD_COLOR)


def _draw_snapshot_shutter(rec: rl.Rectangle):
  inset = min(rec.width, rec.height) * 0.18
  inner = rl.Rectangle(rec.x + inset, rec.y + inset, rec.width - 2 * inset, rec.height - 2 * inset)
  rl.draw_rectangle_rounded_lines_ex(inner, 0.2, 8, 3, OSD_COLOR)
  inner_inset = 5
  inner2 = rl.Rectangle(inner.x + inner_inset, inner.y + inner_inset,
                        inner.width - 2 * inner_inset, inner.height - 2 * inner_inset)
  rl.draw_rectangle_rounded_lines_ex(inner2, 0.18, 8, 1, OSD_COLOR)


class CamcorderView(CameraView):
  def __init__(self):
    super().__init__("camerad", WIDE)
    self._set_placeholder_color(rl.BLACK)
    self._recorder = ClipRecorder()
    self._photo_mode = False
    self._mode_pull = ModePullGesture()
    self._mode_pull_target_photo = True
    self._snapshot_flash_until = 0.0
    self._pressed: str | None = None
    self._playback = PlaybackView()
    self._folder_icon = gui_app.texture("icons/folder.png", FOLDER_ICON_SIZE, FOLDER_ICON_SIZE)
    self._camera_icon = gui_app.texture("icons/camera.png", 64, 64)
    self._video_icon = gui_app.texture("icons/video_camera.png", 64, 64)
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

  def update_mode_pull(self, overscroll: float, dragging: bool):
    if self._recorder.recording:
      self._mode_pull.reset()
      return

    was_hidden = self._mode_pull.progress <= 0.0
    toggled = self._mode_pull.update(overscroll, dragging, rl.get_time())
    if was_hidden and self._mode_pull.progress > 0.0:
      self._mode_pull_target_photo = not self._photo_mode
    if toggled:
      self._photo_mode = self._mode_pull_target_photo

  def _take_photo(self):
    if self.frame is None:
      return
    writer = None
    try:
      rgb = extract_clip_rgb(self.frame.data, self.frame.width, self.frame.height,
                             self.frame.stride, self.frame.uv_offset,
                             flip_h=self._showing_cabin(), enhance=self._showing_cabin())
      writer = ClipWriter("cabin" if self._showing_cabin() else "wide", media_type="photo")
      writer.add_frame(rgb, 0)
      writer.finalize()
      self._snapshot_flash_until = rl.get_time() + SNAPSHOT_FLASH_S
    except Exception:
      if writer is not None:
        writer.abort()
      cloudlog.exception("camcorder snapshot failed")

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
      if self._photo_mode:
        self._take_photo()
      else:
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

  def draw_mode_pull_indicator(self):
    progress = self._mode_pull.progress
    if progress <= 0.0:
      return
    scale_progress = _smoothstep(progress)
    bounce_age = rl.get_time() - self._mode_pull.bounce_started
    bounce = (math.exp(-MODE_BOUNCE_DAMPING * bounce_age) *
              math.sin(MODE_BOUNCE_FREQUENCY * bounce_age) *
              MODE_BOUNCE_AMPLITUDE) if 0.0 <= bounce_age < MODE_BOUNCE_S else 0.0
    fade = _smoothstep(progress / MODE_FADE_END_PROGRESS)
    brightness = round(58 + (207 - 58) * progress)
    alpha = round((70 + 185 * progress) * fade)
    color = rl.Color(brightness, max(0, brightness - 5), max(0, brightness - 20), alpha)
    cx = gui_app.width - MODE_PULL_COMMIT_PX / 2
    cy = gui_app.height / 2
    radius = (MODE_RING_MIN_RADIUS +
              (MODE_RING_MAX_RADIUS - MODE_RING_MIN_RADIUS) * scale_progress) * (1.0 + bounce)
    icon_size = (MODE_ICON_MIN_SIZE +
                 (MODE_ICON_MAX_SIZE - MODE_ICON_MIN_SIZE) * scale_progress) * (1.0 + bounce)

    fill_progress = _smoothstep((progress - MODE_FILL_START_PROGRESS) / (1.0 - MODE_FILL_START_PROGRESS))
    if radius > 4:
      ring_segments = max(MODE_RING_MIN_SEGMENTS, round(radius * MODE_RING_SEGMENTS_PER_RADIUS))
      fill_alpha = round(105 * fill_progress)
      rl.draw_circle(int(cx), int(cy), radius - MODE_RING_WIDTH, rl.Color(20, 20, 18, fill_alpha))
      rl.draw_ring(rl.Vector2(cx, cy), radius - MODE_RING_WIDTH, radius,
                   -90.0, -90.0 + 360.0 * progress, ring_segments, color)

    armed_progress = _smoothstep((progress - MODE_GLOW_START_PROGRESS) / (1.0 - MODE_GLOW_START_PROGRESS))
    if armed_progress > 0.0 and radius > 4:
      rl.draw_ring(rl.Vector2(cx, cy), radius + 3, radius + 6,
                   0.0, 360.0, ring_segments, rl.Color(207, 202, 187, round(110 * armed_progress)))

    if icon_size > 4:
      icon = self._camera_icon if self._mode_pull_target_photo else self._video_icon
      icon_scale = icon_size / icon.width
      icon_pos = rl.Vector2(cx - icon.width * icon_scale / 2, cy - icon.height * icon_scale / 2)
      rl.draw_texture_ex(icon, icon_pos, 0.0, icon_scale, color)

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
    elif self._photo_mode:
      _draw_snapshot_shutter(record_face)
    else:
      _draw_record_icon(record_face)
    if self.frame is None:
      self._waiting.render(self._feed)
    if self._snapshot_flash_until > rl.get_time():
      remaining = (self._snapshot_flash_until - rl.get_time()) / SNAPSHOT_FLASH_S
      rl.draw_rectangle_rec(self._feed, rl.Color(255, 255, 255, round(150 * remaining)))

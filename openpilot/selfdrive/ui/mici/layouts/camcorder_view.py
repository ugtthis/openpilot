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
from openpilot.system.ui.lib.application import GL_VERSION, FontWeight, MousePos, TextAlignment, TextAlignmentVertical, gui_app
from openpilot.system.ui.widgets.label import UnifiedLabel

WIDE = VisionStreamType.VISION_STREAM_WIDE_ROAD
CABIN = VisionStreamType.VISION_STREAM_CABIN
FOLDER_ICON_SIZE = 40
RECORD_TIMEOUT_S = 3600
SNAPSHOT_FLASH_S = 0.12
SNAPSHOT_COUNTDOWN_S = 3.0
SNAPSHOT_COUNTDOWN_POP_S = 0.18
SNAPSHOT_COUNTDOWN_POP = 0.12
SNAPSHOT_COUNTDOWN_FONT = 0.46
SNAPSHOT_COUNTDOWN_RING_WIDTH = 9

SNAPSHOT_COUNTDOWN_VERTEX_SHADER = GL_VERSION + """
in vec3 vertexPosition;
in vec2 vertexTexCoord;
uniform mat4 mvp;
out vec2 fragTexCoord;
void main() {
  fragTexCoord = vertexTexCoord;
  gl_Position = mvp * vec4(vertexPosition, 1.0);
}
"""

SNAPSHOT_COUNTDOWN_FRAGMENT_SHADER = GL_VERSION + """
in vec2 fragTexCoord;
out vec4 finalColor;

uniform float progress;
uniform float innerRadius;
uniform float outerRadius;

void main() {
  const float PI = 3.14159265359;
  const float TWO_PI = 6.28318530718;
  vec2 p = fragTexCoord * 2.0 - 1.0;
  float distanceFromCenter = length(p);
  float edge = max(fwidth(distanceFromCenter) * 1.25, 0.001);

  float disc = 1.0 - smoothstep(outerRadius - edge, outerRadius + edge, distanceFromCenter);
  float ring = smoothstep(innerRadius - edge, innerRadius + edge, distanceFromCenter) * disc;

  float angleFromTop = mod(atan(p.y, p.x) + PI * 0.5 + TWO_PI, TWO_PI);
  float sweepEnd = progress * TWO_PI;
  float arcEdge = max(fwidth(angleFromTop), 0.002);
  float arc = 1.0 - smoothstep(sweepEnd - arcEdge, sweepEnd + arcEdge, angleFromTop);

  vec4 background = vec4(0.031, 0.031, 0.031, 0.686 * disc);
  finalColor = mix(background, vec4(1.0, 1.0, 1.0, 0.92), ring * arc);
}
"""

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


class SnapshotCountdown:
  """3-2-1 timer used before a cabin-camera still."""

  def __init__(self, duration_s: float = SNAPSHOT_COUNTDOWN_S):
    self.duration_s = duration_s
    self._until = 0.0

  def start(self, now: float):
    self._until = now + self.duration_s

  def cancel(self):
    self._until = 0.0

  def active(self, now: float) -> bool:
    return self._until > now

  def remaining(self, now: float) -> float:
    return max(0.0, self._until - now) if self._until > 0.0 else 0.0

  def digit(self, now: float) -> int | None:
    remaining = self.remaining(now)
    if remaining <= 0.0:
      return None
    return max(1, math.ceil(remaining))

  def tick(self, now: float) -> bool:
    if self._until <= 0.0 or now < self._until:
      return False
    self._until = 0.0
    return True


class _SnapshotCountdownRing:
  """Draws a smooth countdown ring without risking UI startup."""

  def __init__(self):
    self._shader = None
    self._unavailable = False
    self._progress = rl.ffi.new("float[1]")
    self._inner_radius = rl.ffi.new("float[1]")
    self._outer_radius = rl.ffi.new("float[1]")

  def _initialize(self) -> bool:
    if self._shader is not None:
      return True
    if self._unavailable:
      return False

    shader = None
    try:
      shader = rl.load_shader_from_memory(SNAPSHOT_COUNTDOWN_VERTEX_SHADER, SNAPSHOT_COUNTDOWN_FRAGMENT_SHADER)
      locations = (
        rl.get_shader_location(shader, "progress"),
        rl.get_shader_location(shader, "innerRadius"),
        rl.get_shader_location(shader, "outerRadius"),
      )
      if not shader.id or any(location < 0 for location in locations):
        raise RuntimeError("countdown shader failed to compile or link")

      texture = rl.get_shapes_texture()
      source = rl.Rectangle(0, 0, texture.width, texture.height)
      self._shader = shader
      self._progress_loc, self._inner_radius_loc, self._outer_radius_loc = locations
      self._texture = texture
      self._source = source
      return True
    except Exception:
      if shader is not None and shader.id:
        rl.unload_shader(shader)
      self._unavailable = True
      cloudlog.exception("countdown ring unavailable")
      return False

  def draw(self, center: rl.Vector2, radius: float, width: float, progress: float):
    if not self._initialize():
      return

    try:
      padding = 2.0
      half_size = radius + padding
      destination = rl.Rectangle(center.x - half_size, center.y - half_size, half_size * 2, half_size * 2)
      self._progress[0] = _clamp01(progress)
      self._inner_radius[0] = (radius - width) / half_size
      self._outer_radius[0] = radius / half_size
      rl.set_shader_value(self._shader, self._progress_loc, self._progress,
                          rl.ShaderUniformDataType.SHADER_UNIFORM_FLOAT)
      rl.set_shader_value(self._shader, self._inner_radius_loc, self._inner_radius,
                          rl.ShaderUniformDataType.SHADER_UNIFORM_FLOAT)
      rl.set_shader_value(self._shader, self._outer_radius_loc, self._outer_radius,
                          rl.ShaderUniformDataType.SHADER_UNIFORM_FLOAT)
      rl.begin_shader_mode(self._shader)
      try:
        rl.draw_texture_pro(self._texture, self._source, destination, rl.Vector2(), 0.0, rl.WHITE)
      finally:
        rl.end_shader_mode()
    except Exception:
      self.close()
      self._unavailable = True
      cloudlog.exception("countdown ring draw failed")

  def close(self):
    if self._shader is not None and self._shader.id:
      rl.unload_shader(self._shader)
      self._shader.id = 0
    self._shader = None


def _icon_center(rec: rl.Rectangle) -> tuple[float, float, float]:
  return rec.x + rec.width / 2, rec.y + rec.height / 2, min(rec.width, rec.height) * 0.18


def _draw_record_icon(rec: rl.Rectangle):
  cx, cy, r = _icon_center(rec)
  rl.draw_circle(int(cx), int(cy), r, RECORD_COLOR)


def _draw_stop_icon(rec: rl.Rectangle):
  cx, cy, r = _icon_center(rec)
  rl.draw_rectangle(int(cx - r), int(cy - r), int(2 * r), int(2 * r), RECORD_COLOR)


def _draw_snapshot_shutter(rec: rl.Rectangle):
  size = min(rec.width, rec.height)
  inset = size * 0.16
  lip = max(2, round(size * 0.025))
  groove = max(3, round(size * 0.035))

  # outer lip: light catches the top-left edge, shadow falls bottom-right
  rim = rl.Rectangle(rec.x + inset, rec.y + inset, rec.width - 2 * inset, rec.height - 2 * inset)
  rim_shadow = rl.Rectangle(rim.x + 2, rim.y + 2, rim.width, rim.height)
  rim_highlight = rl.Rectangle(rim.x - 1, rim.y - 1, rim.width, rim.height)
  rl.draw_rectangle_rounded(rim_shadow, 0.22, 12, rl.Color(6, 6, 6, 200))
  rl.draw_rectangle_rounded(rim_highlight, 0.22, 12, rl.Color(168, 164, 152, 255))
  rl.draw_rectangle_rounded(rim, 0.22, 12, rl.Color(126, 123, 114, 255))

  # dark recessed groove between lip and center
  channel = rl.Rectangle(rim.x + lip, rim.y + lip, rim.width - 2 * lip, rim.height - 2 * lip)
  rl.draw_rectangle_rounded(channel, 0.2, 12, rl.Color(10, 10, 10, 255))

  # raised center: bright top edge, dimmer body, dark seat underneath
  center_inset = lip + groove
  center = rl.Rectangle(rim.x + center_inset, rim.y + center_inset,
                        rim.width - 2 * center_inset, rim.height - 2 * center_inset)
  center_highlight = rl.Rectangle(center.x - 1, center.y - 1, center.width, center.height)
  rl.draw_rectangle_rounded(center_highlight, 0.18, 12, rl.Color(150, 146, 135, 255))
  rl.draw_rectangle_rounded(center, 0.18, 12, rl.Color(46, 46, 43, 255))
  rl.draw_rectangle_rounded_lines_ex(center, 0.18, 12, 2, rl.Color(118, 115, 106, 255))


class CamcorderView(CameraView):
  def __init__(self):
    super().__init__("camerad", WIDE)
    self._set_placeholder_color(rl.BLACK)
    self._recorder = ClipRecorder()
    self._photo_mode = False
    self._mode_pull = ModePullGesture()
    self._mode_pull_target_photo = True
    self._snapshot_countdown = SnapshotCountdown()
    self._snapshot_flash_until = 0.0
    self._pressed: str | None = None
    self._playback = PlaybackView()
    self._folder_icon = gui_app.texture("icons/folder.png", FOLDER_ICON_SIZE, FOLDER_ICON_SIZE)
    self._camera_icon = gui_app.texture("icons/camera.png", 64, 64)
    self._video_icon = gui_app.texture("icons/video_camera.png", 64, 64)
    self._countdown_ring = _SnapshotCountdownRing()
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
    self._countdown_label = UnifiedLabel("", 96, FontWeight.DISPLAY,
                                         text_color=rl.WHITE,
                                         alignment=TextAlignment.CENTER,
                                         alignment_vertical=TextAlignmentVertical.MIDDLE,
                                         wrap_text=False)

  def close(self) -> None:
    if getattr(self, "_countdown_ring", None):
      self._countdown_ring.close()
    super().close()

  def _showing_cabin(self) -> bool:
    return self.stream_type == CABIN

  def _countdown_active(self) -> bool:
    return self._snapshot_countdown.active(rl.get_time())

  def _switch_camera(self):
    if self._recorder.recording or self._countdown_active():
      return
    self.switch_stream(WIDE if self._showing_cabin() else CABIN)

  def update_mode_pull(self, overscroll: float, dragging: bool):
    if self._recorder.recording or self._countdown_active():
      self._mode_pull.reset()
      return

    was_hidden = self._mode_pull.progress <= 0.0
    toggled = self._mode_pull.update(overscroll, dragging, rl.get_time())
    if was_hidden and self._mode_pull.progress > 0.0:
      self._mode_pull_target_photo = not self._photo_mode
    if toggled:
      self._photo_mode = self._mode_pull_target_photo

  def _on_shutter(self):
    if self._showing_cabin():
      if self._countdown_active():
        self._snapshot_countdown.cancel()
      else:
        self._snapshot_countdown.start(rl.get_time())
      return
    self._take_photo()

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
      self._snapshot_countdown.cancel()
      gui_app.push_widget(self._playback)
    elif pressed == "record":
      if self._photo_mode:
        self._on_shutter()
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

  def _draw_snapshot_countdown(self, now: float):
    digit = self._snapshot_countdown.digit(now)
    if digit is None:
      return
    remaining = self._snapshot_countdown.remaining(now)
    age = digit - remaining
    pop = 1.0 + SNAPSHOT_COUNTDOWN_POP * (1.0 - _smoothstep(min(1.0, age / SNAPSHOT_COUNTDOWN_POP_S)))
    size = min(self._feed.width, self._feed.height)
    cx = self._feed.x + self._feed.width / 2
    cy = self._feed.y + self._feed.height / 2
    radius = size * 0.38
    self._countdown_ring.draw(rl.Vector2(cx, cy), radius, SNAPSHOT_COUNTDOWN_RING_WIDTH,
                              remaining / SNAPSHOT_COUNTDOWN_S)
    self._countdown_label.set_text(str(digit))
    self._countdown_label.set_font_size(max(40, round(size * SNAPSHOT_COUNTDOWN_FONT * pop)))
    self._countdown_label.render(self._feed)

  def _render(self, rect: rl.Rectangle):
    recording = self._recorder.recording
    now = rl.get_time()
    if self._snapshot_countdown.active(now) and (not self._photo_mode or not self._showing_cabin()):
      self._snapshot_countdown.cancel()
    counting_down = self._snapshot_countdown.active(now)

    rl.draw_rectangle_rec(rect, rl.BLACK)
    draw_recessed_viewfinder(self._camera_pane, self._feed)
    super()._render(self._feed)
    draw_rail(self._rail, [self._playback_slot, self._record_slot])

    playback_face = draw_physical_button(self._playback_slot, self.is_pressed and self._pressed == "playback")
    record_face = draw_physical_button(self._record_slot, recording or counting_down or
                                      (self.is_pressed and self._pressed == "record"))
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
    if counting_down:
      self._draw_snapshot_countdown(now)
    elif self._snapshot_countdown.tick(now):
      self._take_photo()
    if self._snapshot_flash_until > now:
      remaining = (self._snapshot_flash_until - now) / SNAPSHOT_FLASH_S
      rl.draw_rectangle_rec(self._feed, rl.Color(255, 255, 255, round(150 * remaining)))

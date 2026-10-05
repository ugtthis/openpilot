import time

import numpy as np
import pyray as rl
from PIL import Image

from openpilot.common.swaglog import cloudlog
from openpilot.selfdrive.ui.mici.layouts.audio_playback import ClipAudioPlayer
from openpilot.selfdrive.ui.mici.layouts.playback_transport import PlaybackTransport
from openpilot.system.camcorder.clip_storage import (
  Clip, ClipReader, center_crop, delete_all_clips, delete_clip, format_timecode, list_clips,
)
from openpilot.selfdrive.ui.mici.layouts.camcorder_style import (
  BODY_COLOR, OSD_BACKGROUND, OSD_COLOR, TEXT_COLOR, TRASH_ICON,
  PressTracker, camera_body, draw_centered_texture, draw_list_row, draw_physical_button, draw_rail,
  SECOND_ROW_VISIBLE_FRACTION, draw_recessed_viewfinder, fit_inside,
  row_height_for_peek, split_rail, thumb_size_for_row,
)
from openpilot.selfdrive.ui.mici.widgets.dialog import BigConfirmationDialog
from openpilot.selfdrive.ui.ui_state import device
from openpilot.system.ui.lib.application import FontWeight, MousePos, TextAlignment, TextAlignmentVertical, gui_app
from openpilot.system.ui.lib.scroll_panel import GuiScrollPanel
from openpilot.system.ui.widgets import Widget
from openpilot.system.ui.widgets.label import UnifiedLabel

BACK_SIZE = rl.Vector2(156, 64)
HEADER_H = 72
THUMB_TEXT_GAP = 16
TEXT_PAD = 10
ROW_GAP = 2
SCRUB_H = 16
ROW_TRASH_W = 88
ROW_TITLE_SIZE = 44
ROW_META_SIZE = 28
OVERLAY_BUTTON_SIZE = 48


def _rgba(rgb: np.ndarray) -> np.ndarray:
  rgba = np.empty((rgb.shape[0], rgb.shape[1], 4), dtype=np.uint8)
  rgba[..., :3] = rgb
  rgba[..., 3] = 255
  return np.ascontiguousarray(rgba)


class _FrameTexture:
  def __init__(self):
    self.texture: rl.Texture | None = None
    self._rgba: np.ndarray | None = None

  def unload(self):
    if self.texture is not None:
      rl.unload_texture(self.texture)
      self.texture = None
    self._rgba = None

  def show(self, rgb: np.ndarray):
    h, w = rgb.shape[:2]
    if self.texture is None or self.texture.width != w or self.texture.height != h:
      self.unload()
      image = rl.gen_image_color(w, h, rl.BLACK)
      self.texture = rl.load_texture_from_image(image)
      rl.unload_image(image)
    self._rgba = _rgba(rgb)
    rl.update_texture(self.texture, rl.ffi.cast("void *", rl.ffi.from_buffer(self._rgba)))


def _draw_triangle(p1: rl.Vector2, p2: rl.Vector2, p3: rl.Vector2):
  # Draw both windings so the fill is not culled.
  rl.draw_triangle(p1, p2, p3, TEXT_COLOR)
  rl.draw_triangle(p1, p3, p2, TEXT_COLOR)


def _draw_play_icon(rec: rl.Rectangle):
  cx, cy = rec.x + rec.width / 2, rec.y + rec.height / 2
  s = min(rec.width, rec.height) * 0.22
  _draw_triangle(rl.Vector2(cx - s * 0.55, cy - s),
                 rl.Vector2(cx - s * 0.55, cy + s),
                 rl.Vector2(cx + s * 0.85, cy))


def _draw_pause_icon(rec: rl.Rectangle):
  cx, cy = rec.x + rec.width / 2, rec.y + rec.height / 2
  s = min(rec.width, rec.height) * 0.18
  gap = s * 0.45
  rl.draw_rectangle(int(cx - gap - s * 0.45), int(cy - s), int(s * 0.45), int(2 * s), TEXT_COLOR)
  rl.draw_rectangle(int(cx + gap), int(cy - s), int(s * 0.45), int(2 * s), TEXT_COLOR)


def _draw_back_icon(rec: rl.Rectangle):
  cx, cy = rec.x + rec.width / 2, rec.y + rec.height / 2
  size = min(rec.width, rec.height) * 0.20
  _draw_triangle(rl.Vector2(cx - size, cy),
                 rl.Vector2(cx - size * 0.05, cy + size * 0.85),
                 rl.Vector2(cx - size * 0.05, cy - size * 0.85))
  rl.draw_line_ex(rl.Vector2(cx - size * 0.05, cy),
                  rl.Vector2(cx + size, cy), 3, TEXT_COLOR)


def _draw_speaker_icon(rec: rl.Rectangle, muted: bool):
  cx, cy = rec.x + rec.width / 2, rec.y + rec.height / 2
  size = min(rec.width, rec.height) * 0.18
  rl.draw_rectangle(int(cx - size), int(cy - size * 0.42),
                    int(size * 0.7), int(size * 0.84), TEXT_COLOR)
  _draw_triangle(rl.Vector2(cx - size * 0.3, cy - size * 0.42),
                 rl.Vector2(cx + size * 0.45, cy - size),
                 rl.Vector2(cx + size * 0.45, cy + size))
  if muted:
    rl.draw_line_ex(rl.Vector2(cx + size * 0.65, cy - size * 0.65),
                    rl.Vector2(cx + size * 1.45, cy + size * 0.65), 3, TEXT_COLOR)
    rl.draw_line_ex(rl.Vector2(cx + size * 1.45, cy - size * 0.65),
                    rl.Vector2(cx + size * 0.65, cy + size * 0.65), 3, TEXT_COLOR)
  else:
    rl.draw_line_ex(rl.Vector2(cx + size * 0.7, cy - size * 0.45),
                    rl.Vector2(cx + size * 1.05, cy), 3, TEXT_COLOR)
    rl.draw_line_ex(rl.Vector2(cx + size * 1.05, cy),
                    rl.Vector2(cx + size * 0.7, cy + size * 0.45), 3, TEXT_COLOR)


class ClipRow(Widget):
  def __init__(self, clip: Clip, on_open, on_delete):
    super().__init__()
    self.clip = clip
    self._on_open = on_open
    self._on_delete = on_delete
    self._press = PressTracker()
    self._trash_rect = rl.Rectangle()
    self._thumb = _FrameTexture()
    self._thumb_loaded = False
    self._thumb_w = 0
    self._thumb_h = 0
    self._trash_icon = gui_app.texture(TRASH_ICON, 32, 38)
    self._title = UnifiedLabel(clip.time_label, ROW_TITLE_SIZE, FontWeight.DISPLAY,
                               text_color=TEXT_COLOR,
                               alignment=TextAlignment.LEFT,
                               alignment_vertical=TextAlignmentVertical.BOTTOM)
    detail = "photo" if clip.is_photo else clip.duration_label
    recovered = "  Recovered" if clip.recovered else ""
    self._meta = UnifiedLabel(f"{detail}  {clip.camera}{recovered}", ROW_META_SIZE, FontWeight.ROMAN,
                              text_color=OSD_COLOR,
                              alignment=TextAlignment.LEFT,
                              alignment_vertical=TextAlignmentVertical.TOP)

  def hide_event(self):
    super().hide_event()
    self._thumb.unload()
    self._thumb_loaded = False

  def set_thumb_size(self, width: int, height: int) -> None:
    self._thumb_w, self._thumb_h = width, height

  def _ensure_thumb(self):
    if self._thumb_loaded or self._parent_rect is None or not rl.check_collision_recs(self.rect, self._parent_rect):
      return
    self._thumb_loaded = True
    try:
      with Image.open(self.clip.thumb_path) as image:
        self._thumb.show(np.asarray(image.convert("RGB")))
    except OSError:
      self._thumb.unload()

  def _controls(self) -> list[tuple[str, rl.Rectangle]]:
    return [("delete", self._trash_rect), ("open", self.rect)]

  def _layout(self):
    self._trash_rect = rl.Rectangle(self.rect.x + self.rect.width - ROW_TRASH_W, self.rect.y,
                                    ROW_TRASH_W, self.rect.height)

  def _handle_mouse_press(self, mouse_pos: MousePos):
    self._press.press(mouse_pos, self._controls())

  def _handle_mouse_release(self, mouse_pos: MousePos):
    pressed = self._press.release(mouse_pos, self._controls())
    if pressed == "delete":
      self._on_delete(self.clip)
    elif pressed == "open":
      self._on_open(self.clip)

  def _render(self, rect: rl.Rectangle):
    self._ensure_thumb()
    face = draw_list_row(rect, self.is_pressed)
    thumb = rl.Rectangle(face.x, face.y, min(self._thumb_w, face.width), face.height)
    rl.draw_rectangle_rec(thumb, OSD_BACKGROUND)
    if self._thumb.texture is not None:
      rl.draw_texture_pro(self._thumb.texture,
                          rl.Rectangle(0, 0, self._thumb.texture.width, self._thumb.texture.height),
                          thumb, rl.Vector2(0, 0), 0.0, rl.WHITE)
    text_x = thumb.x + thumb.width + THUMB_TEXT_GAP
    text = rl.Rectangle(text_x, face.y + TEXT_PAD,
                        max(0, self._trash_rect.x - text_x - TEXT_PAD), face.height - 2 * TEXT_PAD)
    self._title.render(rl.Rectangle(text.x, text.y, text.width, text.height * 0.55))
    self._meta.render(rl.Rectangle(text.x, text.y + text.height * 0.5, text.width, text.height * 0.5))
    tint = rl.Color(255, 255, 255, 160) if self.is_pressed and self._press.is_down("delete") else rl.WHITE
    draw_centered_texture(self._trash_rect, self._trash_icon, tint)


class ClipPlayerView(Widget):
  def __init__(self, on_delete):
    super().__init__()
    self._on_delete = on_delete
    self._clip: Clip | None = None
    self._reader: ClipReader | None = None
    self._audio: ClipAudioPlayer | None = None
    self._frame = _FrameTexture()
    self._shown_index = -1
    self._transport = PlaybackTransport()
    self._fullscreen = False
    self._press = PressTracker()
    self._rail = rl.Rectangle()
    self._top_slot = rl.Rectangle()
    self._back_rect = rl.Rectangle()
    self._volume_rect = rl.Rectangle()
    self._play_slot = rl.Rectangle()
    self._delete_slot = rl.Rectangle()
    self._fullscreen_back_rect = rl.Rectangle()
    self._fullscreen_volume_rect = rl.Rectangle()
    self._camera_pane = rl.Rectangle()
    self._feed = rl.Rectangle()
    self._scrub = rl.Rectangle()
    self._trash_icon = gui_app.texture(TRASH_ICON, 29, 35)
    self._back_label = UnifiedLabel("back", 32, FontWeight.DISPLAY,
                                    text_color=TEXT_COLOR,
                                    alignment=TextAlignment.CENTER,
                                    alignment_vertical=TextAlignmentVertical.MIDDLE)
    self._time_label = UnifiedLabel("", 22, FontWeight.DISPLAY,
                                    text_color=OSD_COLOR,
                                    alignment=TextAlignment.CENTER,
                                    alignment_vertical=TextAlignmentVertical.MIDDLE)

  @property
  def clip(self) -> Clip | None:
    return self._clip

  def release_files(self):
    self._transport.stop()
    self._close_audio()
    self._close_reader()
    self._frame.unload()
    self._clip = None

  def open(self, clip: Clip):
    self.release_files()
    self._clip = clip
    self._fullscreen = False
    self._transport.reset_for_open()

  def show_event(self):
    super().show_event()
    device.set_override_interactive_timeout(300)
    if self._clip is None:
      return
    try:
      self._reader = ClipReader(self._clip)
    except Exception:
      cloudlog.exception("camcorder could not open clip")
      self._reader = None
      return
    if self._clip.has_audio:
      audio = ClipAudioPlayer(self._clip)
      if audio.open():
        audio.set_muted(self._transport.muted)
        self._audio = audio
    self._shown_index = -1
    self._transport.reset_for_show(self._clip.duration_s, self._clip.is_photo, time.monotonic())
    self._sync_audio()

  def hide_event(self):
    super().hide_event()
    self._transport.stop()
    self._close_audio()
    self._close_reader()
    self._frame.unload()

  def _close_reader(self):
    if self._reader is not None:
      self._reader.close()
      self._reader = None

  def _close_audio(self):
    if self._audio is not None:
      self._audio.close()
      self._audio = None

  def _sync_audio(self, state: tuple[float, bool] | None = None):
    if self._audio is not None:
      self._audio.sync(*(state or (self._transport.playhead, self._transport.playing)))

  def _has_playable_audio(self) -> bool:
    return self._audio is not None and self._audio.available

  def _duration(self) -> float:
    return self._transport.duration

  def _set_playhead(self, seconds: float, playing: bool | None = None):
    self._transport.set_playhead(seconds, playing, time.monotonic())
    self._sync_audio()

  def _seek(self, seconds: float):
    self._set_playhead(seconds)
    self._shown_index = -1

  def _toggle_play(self):
    if self._reader is None or self._clip is None or self._clip.is_photo:
      return
    restarting = self._transport.at_end
    sync_state = self._transport.toggle(time.monotonic())
    if sync_state is not None:
      if restarting:
        self._shown_index = -1
      self._sync_audio(sync_state)

  def _toggle_volume(self):
    muted = self._transport.toggle_mute()
    if self._audio is not None:
      self._audio.set_muted(muted)

  def _toggle_fullscreen(self):
    if self._clip is None:
      return
    self._fullscreen = not self._fullscreen

  def _controls(self) -> list[tuple[str, rl.Rectangle]]:
    if self._fullscreen:
      controls = [
        ("fullscreen_back", self._fullscreen_back_rect),
      ]
      if self._has_playable_audio():
        controls.append(("volume", self._fullscreen_volume_rect))
      if self._clip is not None and not self._clip.is_photo:
        controls.append(("scrub", self._scrub))
      controls.append(("feed", self._feed))
      return controls
    controls = [
      ("back", self._back_rect),
      ("delete", self._delete_slot),
    ]
    if self._clip is not None and not self._clip.is_photo:
      controls.extend((("play", self._play_slot), ("scrub", self._scrub)))
      if self._has_playable_audio():
        controls.append(("volume", self._volume_rect))
    controls.append(("feed", self._feed))
    return controls

  def _layout(self):
    self._rail, self._camera_pane, self._feed = camera_body(self.rect)
    if self._clip is not None and self._clip.is_photo:
      self._back_rect, self._delete_slot = split_rail(self._rail, 2)
      self._top_slot = self._back_rect
      self._volume_rect = rl.Rectangle()
      self._play_slot = rl.Rectangle()
    else:
      self._top_slot, self._play_slot, self._delete_slot = split_rail(self._rail, 3)
      if self._has_playable_audio():
        half_width = self._top_slot.width / 2
        self._back_rect = rl.Rectangle(self._top_slot.x, self._top_slot.y,
                                       half_width, self._top_slot.height)
        self._volume_rect = rl.Rectangle(self._top_slot.x + half_width, self._top_slot.y,
                                         half_width, self._top_slot.height)
      else:
        self._back_rect = self._top_slot
        self._volume_rect = rl.Rectangle()
    if self._fullscreen and self._clip is not None:
      self._camera_pane = self.rect
      self._rail = rl.Rectangle()
      self._feed = fit_inside(self.rect, self._clip.width / self._clip.height)
      self._fullscreen_back_rect = rl.Rectangle(self.rect.x + 4, self.rect.y + 4,
                                                OVERLAY_BUTTON_SIZE, OVERLAY_BUTTON_SIZE)
      self._fullscreen_volume_rect = rl.Rectangle(
        self._fullscreen_back_rect.x + self._fullscreen_back_rect.width + 4,
        self._fullscreen_back_rect.y, OVERLAY_BUTTON_SIZE, OVERLAY_BUTTON_SIZE,
      )
    self._scrub = rl.Rectangle(self._feed.x, self._feed.y + self._feed.height - SCRUB_H,
                               self._feed.width, SCRUB_H)

  def _handle_mouse_press(self, mouse_pos: MousePos):
    self._press.press(mouse_pos, self._controls())

  def _handle_mouse_release(self, mouse_pos: MousePos):
    pressed = self._press.release(mouse_pos, self._controls())
    if pressed == "fullscreen_back":
      self._fullscreen = False
    elif pressed == "back":
      gui_app.pop_widget()
    elif pressed == "volume":
      self._toggle_volume()
    elif pressed == "play":
      self._toggle_play()
    elif pressed == "feed":
      self._toggle_fullscreen()
    elif pressed == "delete" and self._clip is not None:
      self._transport.stop()
      self._on_delete(self._clip)
    elif pressed == "scrub" and self._scrub.width:
      self._seek((mouse_pos.x - self._scrub.x) / self._scrub.width * self._duration())

  def _show_frame(self, index: int):
    if self._reader is None or index == self._shown_index:
      return
    try:
      self._frame.show(self._reader.frame(index))
    except Exception:
      cloudlog.exception("camcorder could not decode frame")
    self._shown_index = index

  def _update_state(self):
    if self._clip is None or self._reader is None:
      return
    audio_playhead = self._audio.playhead_s if self._transport.playing and self._has_playable_audio() else None
    if self._transport.tick(time.monotonic(), audio_playhead):
      self._sync_audio()
    self._show_frame(self._reader.frame_index_at_ms(self._transport.playhead_ms))

  def _source_rect(self) -> rl.Rectangle:
    if self._frame.texture is None:
      return rl.Rectangle()
    width, height = float(self._frame.texture.width), float(self._frame.texture.height)
    if self._fullscreen or self._clip is None or not self._clip.has_full_frame_preview:
      return rl.Rectangle(0, 0, width, height)
    x, y, crop_width, crop_height = center_crop(width, height)
    return rl.Rectangle(x, y, crop_width, crop_height)

  def _render(self, rect: rl.Rectangle):
    rl.draw_rectangle_rec(rect, rl.BLACK)
    if not self._fullscreen:
      draw_recessed_viewfinder(self._camera_pane, self._feed)
    if self._frame.texture is not None:
      rl.draw_texture_pro(self._frame.texture, self._source_rect(), self._feed, rl.Vector2(0, 0), 0.0, rl.WHITE)
    else:
      rl.draw_rectangle_rec(self._feed, rl.BLACK)

    if self._fullscreen:
      back_face = draw_physical_button(self._fullscreen_back_rect,
                                       self.is_pressed and self._press.is_down("fullscreen_back"))
      _draw_back_icon(back_face)
      if self._has_playable_audio():
        volume_face = draw_physical_button(self._fullscreen_volume_rect,
                                           self.is_pressed and self._press.is_down("volume"))
        _draw_speaker_icon(volume_face, self._transport.muted)
    else:
      photo = self._clip is not None and self._clip.is_photo
      slots = [self._back_rect, self._delete_slot] if photo else [self._top_slot, self._play_slot, self._delete_slot]
      draw_rail(self._rail, slots)
      back_face = draw_physical_button(self._back_rect, self.is_pressed and self._press.is_down("back"))
      delete_face = draw_physical_button(self._delete_slot, self.is_pressed and self._press.is_down("delete"))
      self._back_label.render(back_face)
      if not photo:
        if self._has_playable_audio():
          volume_face = draw_physical_button(self._volume_rect,
                                             self.is_pressed and self._press.is_down("volume"))
          _draw_speaker_icon(volume_face, self._transport.muted)
        play_face = draw_physical_button(self._play_slot, not self._transport.playing or
                                         (self.is_pressed and self._press.is_down("play")))
        if self._transport.playing:
          _draw_pause_icon(play_face)
        else:
          _draw_play_icon(play_face)
      draw_centered_texture(delete_face, self._trash_icon)

    if self._clip is not None and not self._clip.is_photo:
      duration = self._duration()
      rl.draw_rectangle_rec(self._scrub, OSD_BACKGROUND)
      rl.draw_rectangle_rec(
        rl.Rectangle(self._scrub.x, self._scrub.y, self._scrub.width * self._transport.progress, self._scrub.height),
        OSD_COLOR,
      )
      self._time_label.set_text(f"{format_timecode(self._transport.playhead)} / {format_timecode(duration)}")
      if self._fullscreen:
        overlay_button = self._fullscreen_volume_rect if self._has_playable_audio() else self._fullscreen_back_rect
        time_x = overlay_button.x + overlay_button.width
        self._time_label.render(rl.Rectangle(time_x, self.rect.y + 6,
                                             max(0, self.rect.width - time_x - 8), OVERLAY_BUTTON_SIZE - 4))
      else:
        self._time_label.render(rl.Rectangle(self._feed.x, self._feed.y + 6, self._feed.width, 24))


class PlaybackView(Widget):
  def __init__(self):
    super().__init__()
    self._press = PressTracker()
    self._back_rect = rl.Rectangle()
    self._delete_all_rect = rl.Rectangle()
    self._list_rect = rl.Rectangle()
    self._row_h = 1
    self._rows: list[ClipRow] = []
    self._scroll = GuiScrollPanel()
    self._player = ClipPlayerView(self._confirm_delete)
    self._trash_confirm_icon = gui_app.texture(TRASH_ICON, 54, 64)
    self._title = UnifiedLabel("playback", 36, FontWeight.DISPLAY,
                               text_color=TEXT_COLOR,
                               alignment=TextAlignment.CENTER,
                               alignment_vertical=TextAlignmentVertical.MIDDLE)
    self._back_label = UnifiedLabel("back", 32, FontWeight.DISPLAY,
                                    text_color=TEXT_COLOR,
                                    alignment=TextAlignment.CENTER,
                                    alignment_vertical=TextAlignmentVertical.MIDDLE)
    self._delete_all_label = UnifiedLabel("delete all", 26, FontWeight.DISPLAY,
                                          text_color=TEXT_COLOR,
                                          alignment=TextAlignment.CENTER,
                                          alignment_vertical=TextAlignmentVertical.MIDDLE)
    self._empty = UnifiedLabel("no clips", 32, FontWeight.ROMAN,
                               text_color=OSD_COLOR,
                               alignment=TextAlignment.CENTER,
                               alignment_vertical=TextAlignmentVertical.MIDDLE)

  def show_event(self):
    super().show_event()
    device.set_override_interactive_timeout(300)
    self._reload()
    self._scroll.set_offset(0.0)

  def hide_event(self):
    self._clear_rows()
    device.set_override_interactive_timeout(None)
    super().hide_event()

  def _reload(self):
    self._clear_rows()
    self._rows = [ClipRow(clip, self._open_clip, self._confirm_delete) for clip in list_clips()]
    for row in self._rows:
      row.set_enabled(lambda: self.enabled)
      row.set_touch_valid_callback(self._scroll.is_touch_valid)
      self._child(row)

  def _clear_rows(self):
    for row in self._rows:
      row.hide_event()
      if row in self._children:
        self._children.remove(row)
    self._rows = []

  def _open_clip(self, clip: Clip):
    self._player.open(clip)
    gui_app.push_widget(self._player)

  def review(self, clip: Clip):
    """Stop-record lands here: list under the player so back returns to playback."""
    self._player.open(clip)
    gui_app.push_widget(self)
    gui_app.push_widget(self._player)

  def _confirm_delete(self, clip: Clip):
    gui_app.push_widget(BigConfirmationDialog("slide to delete", self._trash_confirm_icon,
                                              lambda: self._delete(clip), red=True))

  def _confirm_delete_all(self):
    if not self._rows:
      return
    gui_app.push_widget(BigConfirmationDialog("slide to delete all", self._trash_confirm_icon,
                                              self._delete_all, red=True))

  def _delete(self, clip: Clip):
    reviewing = self._player.clip is not None and self._player.clip.clip_id == clip.clip_id
    if reviewing:
      self._player.release_files()
    delete_clip(clip)
    if reviewing and gui_app.widget_in_stack(self._player):
      gui_app.pop_widget()
    self._reload()

  def _delete_all(self):
    if self._player.clip is not None:
      self._player.release_files()
    delete_all_clips()
    if gui_app.widget_in_stack(self._player):
      gui_app.pop_widget()
    self._reload()

  def _header_controls(self) -> list[tuple[str, rl.Rectangle]]:
    controls = [("back", self._back_rect)]
    if self._rows:
      controls.append(("delete_all", self._delete_all_rect))
    return controls

  def _layout(self):
    header_y = self.rect.y + (HEADER_H - BACK_SIZE.y) / 2
    self._back_rect = rl.Rectangle(self.rect.x + 8, header_y, BACK_SIZE.x, BACK_SIZE.y)
    self._delete_all_rect = rl.Rectangle(self.rect.x + self.rect.width - BACK_SIZE.x - 8,
                                         header_y, BACK_SIZE.x, BACK_SIZE.y)
    self._list_rect = rl.Rectangle(self.rect.x, self.rect.y + HEADER_H,
                                   self.rect.width, max(0, self.rect.height - HEADER_H))
    self._row_h = row_height_for_peek(self._list_rect.height, ROW_GAP, SECOND_ROW_VISIBLE_FRACTION)
    thumb_w, thumb_h = thumb_size_for_row(self._row_h)
    for row in self._rows:
      row.set_thumb_size(thumb_w, thumb_h)

  def _handle_mouse_press(self, mouse_pos: MousePos):
    self._press.press(mouse_pos, self._header_controls())

  def _handle_mouse_release(self, mouse_pos: MousePos):
    pressed = self._press.release(mouse_pos, self._header_controls())
    if pressed == "back":
      gui_app.pop_widget()
    elif pressed == "delete_all":
      self._confirm_delete_all()

  def _render(self, rect: rl.Rectangle):
    rl.draw_rectangle_rec(rect, BODY_COLOR)
    self._title.render(rl.Rectangle(self.rect.x, self.rect.y, self.rect.width, HEADER_H))
    back_face = draw_physical_button(self._back_rect, self.is_pressed and self._press.is_down("back"))
    self._back_label.render(back_face)
    if self._rows:
      delete_face = draw_physical_button(
        self._delete_all_rect, self.is_pressed and self._press.is_down("delete_all"),
      )
      self._delete_all_label.render(delete_face)

    if not self._rows:
      self._empty.render(self._list_rect)
      return
    if not self.enabled:
      return

    stride = self._row_h + ROW_GAP
    content = rl.Rectangle(self._list_rect.x, self._list_rect.y, self._list_rect.width,
                           len(self._rows) * stride)
    offset = self._scroll.update(self._list_rect, content)
    rl.begin_scissor_mode(int(self._list_rect.x), int(self._list_rect.y),
                          int(self._list_rect.width), int(self._list_rect.height))
    for i, row in enumerate(self._rows):
      row_rect = rl.Rectangle(self._list_rect.x, self._list_rect.y + offset + i * stride,
                              self._list_rect.width, self._row_h)
      row.set_parent_rect(self._list_rect)
      row.render(row_rect)
    rl.end_scissor_mode()

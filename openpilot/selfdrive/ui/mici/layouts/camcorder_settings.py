from collections import deque
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime
import time
from typing import NamedTuple
from zoneinfo import ZoneInfo

import pyray as rl

from openpilot.common.filter_simple import FirstOrderFilter
from openpilot.common.params import Params
from openpilot.selfdrive.ui.mici.layouts.camcorder_style import (
  BODY_COLOR, BUTTON_FACE_COLOR, BUTTON_FACE_PRESSED_COLOR, OSD_COLOR, TEXT_COLOR, PressTracker,
)
from openpilot.selfdrive.ui.mici.widgets.button import BigButton
from openpilot.system.camcorder.clip_meta import format_clock
from openpilot.system.camcorder.settings import FRAME_RATES, TIME_ZONES, CamcorderSettings, Quality, Resolution
from openpilot.system.ui.lib.application import FontWeight, MousePos, TextAlignment, TextAlignmentVertical, gui_app
from openpilot.system.ui.lib.scroll_panel2 import weighted_velocity
from openpilot.system.ui.widgets.label import UnifiedLabel
from openpilot.system.ui.widgets.scroller import NavScroller

PULL_LOCK_PX = 16  # travel along the pull before the sheet takes the touch
PULL_BLOCK_PX = 60  # sideways travel that cancels a pull before it locks in, as in NavWidget
PULL_COMMIT_PX = 60  # release at least this far along to open or close
PULL_FLING_PX_S = 500  # or flick at least this fast
PULL_VELOCITY_SAMPLES = 4
SHEET_SLIDE_RC = 0.08
# Four rows must fit the 240 px MICI screen: rows come out ~39 px, above the 26-28 px text.
SHEET_PADDING = 10
SHEET_TITLE_HEIGHT = 30
SHEET_ROW_GAP = 6
ROW_LABEL_SIZE = 28
OPTION_TEXT_SIZE = 26
SEGMENT_WIDTH = 104  # three fps segments still leave ~190 px for the row label
SEGMENT_GAP = 6
ZONE_BUTTON_WIDTH = 2 * SEGMENT_WIDTH + SEGMENT_GAP  # lines up with the two option columns
TIME_ZONE_CONTROL = "time_zone"
SELECTED_TEXT_COLOR = rl.Color(24, 24, 24, 255)
HANDLE_WIDTH = 56
HANDLE_HEIGHT = 5


class Option(NamedTuple):
  field: str  # the CamcorderSettings field this option sets
  value: Quality | Resolution | int
  text: str

  @property
  def name(self) -> str:
    return f"{self.field}={self.value}"


ROWS = (
  ("wide quality", (Option("quality", Quality.STOCK, "stock"), Option("quality", Quality.MAX, "max"))),
  ("wide res", (Option("resolution", Resolution.STOCK, "1 MP"), Option("resolution", Resolution.FULL, "4 MP"))),
  ("wide fps", tuple(Option("frame_rate", fps, f"{fps} fps") for fps in FRAME_RATES)),
)


class TimeZoneSelectPage(NavScroller):
  """Pick the zone clip labels use; tapping one saves it and goes back, like BranchSelectPage."""

  def __init__(self, current: str, on_select: Callable[[str], None]):
    super().__init__()
    check_icon = gui_app.texture("icons_mici/settings/device/up_to_date.png", 64, 64)
    now = datetime.now(UTC)
    buttons = []
    for name, label in TIME_ZONES.items():
      # The zone's time right now, so the right one is the one matching your watch.
      clock = format_clock(now.astimezone(ZoneInfo(name) if name else UTC))
      button = BigButton(label, clock, check_icon if name == current else None, scroll=True)
      button.set_click_callback(lambda name=name: self.dismiss(lambda: on_select(name)))
      buttons.append(button)
    self._scroller.add_widgets(buttons)


class VerticalPull:
  """Claim a vertical drag in one direction and report how far it travelled.

  Once claimed, sideways drift is ignored. A claimed drag is never a tap, so callers
  must drop any pending tap once `claimed` turns true.
  """

  def __init__(self, direction: int):
    assert direction in (1, -1)
    self._direction = direction
    self._start: MousePos | None = None
    self._last: tuple[float, float] | None = None
    self._velocities: deque[float] = deque(maxlen=PULL_VELOCITY_SAMPLES)
    self._distance = 0.0
    self.claimed = False

  def press(self, pos: MousePos, t: float) -> None:
    self.reset()
    self._start = pos
    self._last = (pos.y, t)

  def move(self, pos: MousePos, t: float) -> None:
    if self._start is None or self._last is None:
      return
    last_y, last_t = self._last
    if t > last_t:
      self._velocities.append((pos.y - last_y) * self._direction / (t - last_t))
      self._last = (pos.y, t)
    along = (pos.y - self._start.y) * self._direction
    if not self.claimed:
      if abs(pos.x - self._start.x) > PULL_BLOCK_PX:
        self.reset()
        return
      self.claimed = along >= PULL_LOCK_PX and along > abs(pos.x - self._start.x)
    if self.claimed:
      self._distance = max(0.0, along)

  def progress(self, travel: float) -> float:
    return min(1.0, self._distance / travel) if self.claimed and travel > 0 else 0.0

  def release(self, pos: MousePos, t: float) -> bool:
    """Finish the drag; returns whether it went far or fast enough along the pull to commit."""
    self.move(pos, t)
    velocity = weighted_velocity(self._velocities)
    committed = self.claimed and (velocity >= PULL_FLING_PX_S or
                                  (self._distance >= PULL_COMMIT_PX and velocity > -PULL_FLING_PX_S))
    self.reset()
    return committed

  def reset(self) -> None:
    self._start = None
    self._last = None
    self._velocities.clear()
    self._distance = 0.0
    self.claimed = False


class SettingsSheet:
  """Settings panel that slides down over the camcorder page and back up to close."""

  def __init__(self, params: Params | None = None):
    self._params = params or Params()
    self.settings = CamcorderSettings.load(self._params)
    self._open_pull = VerticalPull(1)
    self._close_pull = VerticalPull(-1)
    self._press = PressTracker()
    self._is_open = False
    self._switching_until = 0.0
    self._shown = FirstOrderFilter(0.0, SHEET_SLIDE_RC, 1 / gui_app.target_fps)  # 0 hidden, 1 covering the page
    self._rect = rl.Rectangle()
    self._title = UnifiedLabel(lambda: "switching camera" if time.monotonic() < self._switching_until else "settings",
                               30, FontWeight.DISPLAY, TEXT_COLOR,
                               alignment_vertical=TextAlignmentVertical.MIDDLE)
    self._rows = [
      (UnifiedLabel(title, ROW_LABEL_SIZE, FontWeight.MEDIUM, OSD_COLOR, alignment_vertical=TextAlignmentVertical.MIDDLE),
       [(option, UnifiedLabel(option.text, OPTION_TEXT_SIZE, FontWeight.MEDIUM, TEXT_COLOR, alignment=TextAlignment.CENTER,
                              alignment_vertical=TextAlignmentVertical.MIDDLE)) for option in options])
      for title, options in ROWS
    ]
    self._zone_row = UnifiedLabel("time zone", ROW_LABEL_SIZE, FontWeight.MEDIUM, OSD_COLOR, alignment_vertical=TextAlignmentVertical.MIDDLE)
    self._zone_value = UnifiedLabel(lambda: TIME_ZONES[self.settings.time_zone], OPTION_TEXT_SIZE, FontWeight.MEDIUM, TEXT_COLOR,
                                    alignment=TextAlignment.CENTER, alignment_vertical=TextAlignmentVertical.MIDDLE)

  @property
  def is_open(self) -> bool:
    return self._is_open

  @property
  def visible(self) -> bool:
    return self._is_open or self._shown.x > 0.0 or self._open_pull.claimed

  @property
  def owns_touch(self) -> bool:
    """While true the page must not act on the current touch."""
    return self._is_open or self._open_pull.claimed

  def close(self) -> None:
    self._is_open = False
    self._shown.x = 0.0
    self.cancel_touch()

  def cancel_touch(self) -> None:
    self._open_pull.reset()
    self._close_pull.reset()
    self._press.clear()

  def press(self, pos: MousePos, t: float, can_open: bool) -> None:
    if self._is_open:
      self._close_pull.press(pos, t)
      self._press.press(pos, self._controls())
    elif can_open:
      self._open_pull.press(pos, t)

  def move(self, pos: MousePos, t: float) -> None:
    if self._is_open:
      self._close_pull.move(pos, t)
      if self._close_pull.claimed:
        self._press.clear()
    else:
      self._open_pull.move(pos, t)

  def release(self, pos: MousePos, t: float) -> bool:
    """Finish the touch; returns whether the sheet consumed it."""
    if self._is_open:
      if self._close_pull.claimed:
        self._is_open = not self._close_pull.release(pos, t)
      else:
        self._close_pull.reset()
        self._select(self._press.release(pos, self._controls()))
      return True
    claimed = self._open_pull.claimed
    self._is_open = self._open_pull.release(pos, t)
    return claimed

  def _select(self, name: str | None) -> None:
    if name == TIME_ZONE_CONTROL:
      gui_app.push_widget(TimeZoneSelectPage(self.settings.time_zone, lambda zone: self._apply(time_zone=zone)))
      return
    option = next((option for _, options in ROWS for option in options if option.name == name), None)
    if option is not None:
      self._apply(**{option.field: option.value})

  def _apply(self, **changes) -> None:
    chosen = replace(self.settings, **changes)
    if chosen != self.settings:
      if chosen.encoder_mode != self.settings.encoder_mode:
        self._switching_until = time.monotonic() + 3.0
      self.settings = chosen
      chosen.save(self._params)

  def update(self, rect: rl.Rectangle) -> None:
    self._rect = rect
    if self._open_pull.claimed:
      self._shown.x = self._open_pull.progress(rect.height)
    elif self._close_pull.claimed:
      self._shown.x = 1.0 - self._close_pull.progress(rect.height)
    else:
      target = 1.0 if self._is_open else 0.0
      if abs(target - self._shown.update(target)) < 0.002:
        self._shown.x = target

  def _geometry(self) -> tuple[rl.Rectangle, list, rl.Rectangle, rl.Rectangle]:
    """The sheet rect at its current slide position, each option row's label, rect and segments,
    and the time zone row and its button below them."""
    r = self._rect
    sheet = rl.Rectangle(r.x, r.y - r.height * (1.0 - self._shown.x), r.width, r.height)
    top = sheet.y + SHEET_PADDING + SHEET_TITLE_HEIGHT + SHEET_ROW_GAP
    count = len(self._rows) + 1
    row_height = (sheet.y + sheet.height - SHEET_PADDING * 2 - top - SHEET_ROW_GAP * (count - 1)) / count

    def row_rect(i: int) -> rl.Rectangle:
      return rl.Rectangle(sheet.x + SHEET_PADDING, top + i * (row_height + SHEET_ROW_GAP), sheet.width - SHEET_PADDING * 2, row_height)

    rows = []
    for i, (row_label, options) in enumerate(self._rows):
      row = row_rect(i)
      x = row.x + row.width - len(options) * SEGMENT_WIDTH - (len(options) - 1) * SEGMENT_GAP
      segments = [(option, label, rl.Rectangle(x + j * (SEGMENT_WIDTH + SEGMENT_GAP), row.y, SEGMENT_WIDTH, row.height))
                  for j, (option, label) in enumerate(options)]
      rows.append((row_label, row, segments))
    zone_row = row_rect(len(self._rows))
    zone_button = rl.Rectangle(zone_row.x + zone_row.width - ZONE_BUTTON_WIDTH, zone_row.y, ZONE_BUTTON_WIDTH, zone_row.height)
    return sheet, rows, zone_row, zone_button

  def _controls(self) -> list[tuple[str, rl.Rectangle]]:
    _, rows, _, zone_button = self._geometry()
    return [(option.name, rect) for _, _, segments in rows for option, _, rect in segments] + [(TIME_ZONE_CONTROL, zone_button)]

  def render(self) -> None:
    if not self.visible:
      return
    sheet, rows, zone_row, zone_button = self._geometry()
    rl.draw_rectangle_rec(sheet, BODY_COLOR)
    self._title.render(rl.Rectangle(sheet.x + SHEET_PADDING, sheet.y + SHEET_PADDING,
                                    sheet.width - SHEET_PADDING * 2, SHEET_TITLE_HEIGHT))
    for row_label, row, segments in rows:
      row_label.render(row)
      for option, label, rect in segments:
        selected = getattr(self.settings, option.field) == option.value
        pressed = self._press.is_down(option.name)
        rl.draw_rectangle_rounded(rect, 0.3, 8, TEXT_COLOR if selected else
                                  (BUTTON_FACE_PRESSED_COLOR if pressed else BUTTON_FACE_COLOR))
        label.set_text_color(SELECTED_TEXT_COLOR if selected else TEXT_COLOR)
        label.render(rect)
    self._zone_row.render(zone_row)
    rl.draw_rectangle_rounded(zone_button, 0.3, 8, BUTTON_FACE_PRESSED_COLOR if self._press.is_down(TIME_ZONE_CONTROL) else BUTTON_FACE_COLOR)
    self._zone_value.render(zone_button)
    handle = rl.Rectangle(sheet.x + (sheet.width - HANDLE_WIDTH) / 2,
                          sheet.y + sheet.height - SHEET_PADDING / 2 - HANDLE_HEIGHT, HANDLE_WIDTH, HANDLE_HEIGHT)
    rl.draw_rectangle_rounded(handle, 1.0, 6, OSD_COLOR)

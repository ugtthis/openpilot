from collections import deque
from dataclasses import replace
from typing import NamedTuple

import pyray as rl

from openpilot.common.filter_simple import FirstOrderFilter
from openpilot.common.params import Params
from openpilot.selfdrive.ui.mici.layouts.camcorder_style import (
  BODY_COLOR, BUTTON_FACE_COLOR, BUTTON_FACE_PRESSED_COLOR, OSD_COLOR, TEXT_COLOR, PressTracker,
)
from openpilot.system.camcorder.settings import FRAME_RATES, CamcorderSettings, Quality
from openpilot.system.ui.lib.application import FontWeight, MousePos, TextAlignment, TextAlignmentVertical, gui_app
from openpilot.system.ui.lib.scroll_panel2 import weighted_velocity
from openpilot.system.ui.widgets.label import UnifiedLabel

PULL_LOCK_PX = 16  # travel along the pull before the sheet takes the touch
PULL_BLOCK_PX = 60  # sideways travel that cancels a pull before it locks in, as in NavWidget
PULL_COMMIT_PX = 60  # release at least this far along to open or close
PULL_FLING_PX_S = 500  # or flick at least this fast
PULL_VELOCITY_SAMPLES = 4
SHEET_SLIDE_RC = 0.08
SHEET_PADDING = 18
SHEET_TITLE_HEIGHT = 40
SHEET_ROW_GAP = 12
SEGMENT_WIDTH = 120
SEGMENT_GAP = 8
SELECTED_TEXT_COLOR = rl.Color(24, 24, 24, 255)
HANDLE_WIDTH = 56
HANDLE_HEIGHT = 5


class Option(NamedTuple):
  field: str  # the CamcorderSettings field this option sets
  value: Quality | int
  text: str

  @property
  def name(self) -> str:
    return f"{self.field}={self.value}"


ROWS = (
  ("quality", (Option("quality", Quality.STOCK, "stock"), Option("quality", Quality.MAX, "max"))),
  ("frame rate", tuple(Option("frame_rate", fps, f"{fps} fps") for fps in FRAME_RATES)),
)


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
    self._shown = FirstOrderFilter(0.0, SHEET_SLIDE_RC, 1 / gui_app.target_fps)  # 0 hidden, 1 covering the page
    self._rect = rl.Rectangle()
    self._title = UnifiedLabel("settings", 30, FontWeight.DISPLAY, TEXT_COLOR,
                               alignment_vertical=TextAlignmentVertical.MIDDLE)
    self._rows = [
      (UnifiedLabel(title, 28, FontWeight.MEDIUM, OSD_COLOR, alignment_vertical=TextAlignmentVertical.MIDDLE),
       [(option, UnifiedLabel(option.text, 26, FontWeight.MEDIUM, TEXT_COLOR, alignment=TextAlignment.CENTER,
                              alignment_vertical=TextAlignmentVertical.MIDDLE)) for option in options])
      for title, options in ROWS
    ]

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
    option = next((option for _, options in ROWS for option in options if option.name == name), None)
    if option is None:
      return
    chosen = replace(self.settings, **{option.field: option.value})
    if chosen != self.settings:
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

  def _geometry(self) -> tuple[rl.Rectangle, list]:
    """The sheet rect at its current slide position, and each row's label, rect and option segments."""
    r = self._rect
    sheet = rl.Rectangle(r.x, r.y - r.height * (1.0 - self._shown.x), r.width, r.height)
    top = sheet.y + SHEET_PADDING + SHEET_TITLE_HEIGHT + SHEET_ROW_GAP
    row_height = (sheet.y + sheet.height - SHEET_PADDING * 2 - top - SHEET_ROW_GAP * (len(self._rows) - 1)) / len(self._rows)
    rows = []
    for i, (row_label, options) in enumerate(self._rows):
      row = rl.Rectangle(sheet.x + SHEET_PADDING, top + i * (row_height + SHEET_ROW_GAP), sheet.width - SHEET_PADDING * 2, row_height)
      x = row.x + row.width - len(options) * SEGMENT_WIDTH - (len(options) - 1) * SEGMENT_GAP
      segments = [(option, label, rl.Rectangle(x + j * (SEGMENT_WIDTH + SEGMENT_GAP), row.y, SEGMENT_WIDTH, row.height))
                  for j, (option, label) in enumerate(options)]
      rows.append((row_label, row, segments))
    return sheet, rows

  def _controls(self) -> list[tuple[str, rl.Rectangle]]:
    return [(option.name, rect) for _, _, segments in self._geometry()[1] for option, _, rect in segments]

  def render(self) -> None:
    if not self.visible:
      return
    sheet, rows = self._geometry()
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
    handle = rl.Rectangle(sheet.x + (sheet.width - HANDLE_WIDTH) / 2,
                          sheet.y + sheet.height - SHEET_PADDING / 2 - HANDLE_HEIGHT, HANDLE_WIDTH, HANDLE_HEIGHT)
    rl.draw_rectangle_rounded(handle, 1.0, 6, OSD_COLOR)

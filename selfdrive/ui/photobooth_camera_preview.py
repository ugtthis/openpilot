import time

import pyray as rl
from msgq.visionipc import VisionStreamType

from openpilot.selfdrive.ui.onroad.cameraview import CameraView
from openpilot.selfdrive.ui.ui_state import ui_state, device
from openpilot.system.ui.lib.application import FontWeight
from openpilot.system.ui.lib.multilang import tr
from openpilot.system.ui.widgets.label import gui_label


class PhotoboothCameraPreview(CameraView):
  """Photobooth-specific DM preview without driver-monitoring overlays."""

  _WAKE_OVERRIDE_SEC = 300
  _DEFAULT_COUNTDOWN_SEC = 3

  def __init__(self) -> None:
    super().__init__("camerad", VisionStreamType.VISION_STREAM_DRIVER)

  def show_event(self):
    super().show_event()
    device.set_override_interactive_timeout(self._WAKE_OVERRIDE_SEC)
    ui_state.params.put_bool("IsDriverViewEnabled", True)

  def hide_event(self):
    device.set_override_interactive_timeout(None)
    ui_state.params.put_bool("PhotoboothSessionActive", False)
    ui_state.params.put_bool("PhotoboothStreamActive", False)
    super().hide_event()
    ui_state.params.put_bool("IsDriverViewEnabled", False)
    self.close()

  def _countdown_remaining(self) -> int:
    start_ms_raw = ui_state.params.get("PhotoboothCountdownStartMs")
    if not start_ms_raw:
      return 0
    duration_raw = ui_state.params.get("PhotoboothCountdownDurationSec")
    try:
      start_ms = int(start_ms_raw)
    except (TypeError, ValueError):
      return 0
    try:
      duration_sec = int(duration_raw) if duration_raw else self._DEFAULT_COUNTDOWN_SEC
    except (TypeError, ValueError):
      duration_sec = self._DEFAULT_COUNTDOWN_SEC
    duration_sec = min(max(duration_sec, 1), 10)
    elapsed_sec = int((time.monotonic() * 1000 - start_ms) / 1000)
    return max(0, duration_sec - elapsed_sec)

  def _render(self, rect):
    super()._render(rect)

    if not self.frame:
      gui_label(
        rect,
        tr("camera starting"),
        font_size=100,
        font_weight=FontWeight.BOLD,
        alignment=rl.GuiTextAlignment.TEXT_ALIGN_CENTER,
      )
      return -1

    remaining = self._countdown_remaining()
    if remaining > 0:
      # Keep styling minimal and readable over varied camera backgrounds.
      overlay = rl.Rectangle(rect.x, rect.y, rect.width, rect.height)
      rl.draw_rectangle_rec(overlay, rl.Color(0, 0, 0, 60))
      gui_label(
        rect,
        str(remaining),
        font_size=220,
        font_weight=FontWeight.BOLD,
        alignment=rl.GuiTextAlignment.TEXT_ALIGN_CENTER,
      )
    return -1

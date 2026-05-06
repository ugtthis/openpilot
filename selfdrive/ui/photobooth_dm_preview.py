import pyray as rl

from openpilot.system.ui.widgets.nav_widget import NavWidget
from openpilot.selfdrive.ui.onroad.driver_camera_dialog import DriverCameraDialog


class PhotoboothDmPreview(NavWidget):
  """Full-screen DM preview during Photobooth; dismiss only via NavWidget swipe-down (same as other full-screen panels)."""

  def __init__(self) -> None:
    super().__init__()
    self._camera = self._child(DriverCameraDialog(photobooth_session=True))

  def _render(self, rect: rl.Rectangle) -> int:
    self._camera.set_rect(rect)
    return self._camera.render()

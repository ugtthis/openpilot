import time
from enum import IntEnum
import numpy as np
import pyray as rl
from cereal import messaging, car, log
from msgq.visionipc import VisionStreamType
from openpilot.selfdrive.ui.ui_state import ui_state, UIStatus
from openpilot.selfdrive.ui.mici.onroad import SIDE_PANEL_WIDTH
from openpilot.selfdrive.ui.mici.onroad.alert_renderer import AlertRenderer
from openpilot.selfdrive.ui.mici.onroad.driver_state import DriverStateRenderer
from openpilot.selfdrive.ui.mici.onroad.hud_renderer import HudRenderer
from openpilot.selfdrive.ui.mici.onroad.model_renderer import ModelRenderer
from openpilot.selfdrive.ui.mici.onroad.confidence_ball import ConfidenceBall
from openpilot.selfdrive.ui.mici.onroad.cameraview import CameraView
from openpilot.selfdrive.ui.mici.onroad.action_tray import SidePanelActionTray
from openpilot.selfdrive.ui.mici.onroad.dac_view import DACView
from openpilot.selfdrive.ui.mici.onroad.dac_2_view import DAC2View
from openpilot.selfdrive.ui.mici.onroad.dac_3_view import DAC3View
from openpilot.system.ui.lib.application import FontWeight, gui_app, MousePos
from openpilot.system.ui.widgets.label import UnifiedLabel
from openpilot.common.transformations.camera import DEVICE_CAMERAS, DeviceCameraConfig, view_frame_from_device_frame
from openpilot.common.transformations.orientation import rot_from_euler

CALIBRATED = log.LiveCalibrationData.Status.calibrated
ROAD_CAM = VisionStreamType.VISION_STREAM_ROAD
WIDE_CAM = VisionStreamType.VISION_STREAM_WIDE_ROAD
DEFAULT_DEVICE_CAMERA = DEVICE_CAMERAS["tici", "ar0231"]

WIDE_CAM_MAX_SPEED = 5.0   # m/s (~10 mph) – switch to wide below this
ROAD_CAM_MIN_SPEED = 10.0  # m/s (~25 mph) – switch to road above this
CAM_Y_OFFSET = 20


class OnroadContentMode(IntEnum):
  ROAD = 0
  DAC = 1
  DAC2 = 2
  DAC3 = 3


class AugmentedRoadView(CameraView):
  def __init__(self, bookmark_callback=None, settings_callback=None, stream_type: VisionStreamType = VisionStreamType.VISION_STREAM_ROAD):
    super().__init__("camerad", stream_type)
    self._set_placeholder_color(rl.BLACK)

    self.device_camera: DeviceCameraConfig | None = None
    self.view_from_calib      = view_frame_from_device_frame.copy()
    self.view_from_wide_calib = view_frame_from_device_frame.copy()

    self._last_calib_time  = 0
    self._last_rect_dims   = (0.0, 0.0)
    self._last_stream_type = stream_type
    self._cached_matrix: np.ndarray | None = None
    self._content_rect = rl.Rectangle()

    self._content_mode = OnroadContentMode.ROAD
    self._model_renderer       = ModelRenderer()
    self._hud_renderer         = HudRenderer()
    self._alert_renderer       = AlertRenderer()
    self._driver_state_renderer = DriverStateRenderer()
    self._fade_texture          = gui_app.texture("icons_mici/onroad/onroad_fade.png")
    self._confidence_ball = ConfidenceBall()
    self._dac_view = DACView(bookmark_callback)
    self._dac_2_view = DAC2View(bookmark_callback, confidence_ball=self._confidence_ball)
    self._dac_3_view = DAC3View(bookmark_callback, confidence_ball=self._confidence_ball)
    self._action_tray = SidePanelActionTray(
      bookmark_callback,
      settings_callback,
      self._toggle_dac,
      self._toggle_dac_2,
      self._toggle_dac_3,
      self._is_non_road_mode_active,
      self._is_dac_mode_active,
      self._is_dac_2_mode_active,
      self._is_dac_3_mode_active,
    )
    self._offroad_label = UnifiedLabel(
      "start the car to\nuse openpilot", 54, FontWeight.DISPLAY,
      text_color=rl.Color(255, 255, 255, int(255 * 0.9)),
      alignment=rl.GuiTextAlignment.TEXT_ALIGN_CENTER,
      alignment_vertical=rl.GuiTextAlignmentVertical.TEXT_ALIGN_MIDDLE,
    )
    self._pm = messaging.PubMaster(['uiDebug'])

  def is_swiping_left(self) -> bool:
    return self._action_tray.is_swiping_left()

  def _is_non_road_mode_active(self) -> bool:
    return self._content_mode != OnroadContentMode.ROAD

  def _is_dac_mode_active(self) -> bool:
    return self._content_mode == OnroadContentMode.DAC

  def _is_dac_2_mode_active(self) -> bool:
    return self._content_mode == OnroadContentMode.DAC2

  def _is_dac_3_mode_active(self) -> bool:
    return self._content_mode == OnroadContentMode.DAC3

  def _toggle_dac(self) -> None:
    self._content_mode = (
      OnroadContentMode.ROAD
      if self._content_mode == OnroadContentMode.DAC
      else OnroadContentMode.DAC
    )

  def _toggle_dac_2(self) -> None:
    self._content_mode = (
      OnroadContentMode.ROAD
      if self._content_mode == OnroadContentMode.DAC2
      else OnroadContentMode.DAC2
    )

  def _toggle_dac_3(self) -> None:
    self._content_mode = (
      OnroadContentMode.ROAD
      if self._content_mode == OnroadContentMode.DAC3
      else OnroadContentMode.DAC3
    )

  def _update_state(self) -> None:
    super()._update_state()
    if not ui_state.started:
      self._content_mode = OnroadContentMode.ROAD
      self._action_tray.collapse()
    if ui_state.panda_type == log.PandaState.PandaType.unknown:
      self._offroad_label.set_text("system booting")
    else:
      self._offroad_label.set_text("start the car to\nuse openpilot")

  def _handle_mouse_release(self, mouse_pos: MousePos) -> None:
    if not self._action_tray.consume_touch_handled_by_tray():
      if self._is_non_road_mode_active() and rl.check_collision_point_rec(mouse_pos, self.rect):
        return
      super()._handle_mouse_release(mouse_pos)

  def _render(self, _) -> None:
    start_draw = time.monotonic()
    self._content_rect = rl.Rectangle(
      self.rect.x,
      self.rect.y,
      self.rect.width - SIDE_PANEL_WIDTH,
      self.rect.height,
    )
    render_rect = self.rect if self._is_non_road_mode_active() else self._content_rect
    rl.begin_scissor_mode(
      int(render_rect.x),
      int(render_rect.y),
      int(render_rect.width),
      int(render_rect.height),
    )

    if self._content_mode == OnroadContentMode.DAC:
      self._render_dac_content()
    elif self._content_mode == OnroadContentMode.DAC2:
      self._render_dac_2_content()
    elif self._content_mode == OnroadContentMode.DAC3:
      self._render_dac_3_content()
    elif ui_state.started:
      # Offroad must skip camera rendering entirely or a buffered VisionIPC frame
      # can linger under the dark overlay after demo replay stops.
      self._render_road_content()

    if not self._is_non_road_mode_active():
      rl.draw_rectangle_rounded_lines_ex(self._content_rect, 0.2 * 1.02, 10, 50, rl.BLACK)
    rl.end_scissor_mode()

    self._render_side_panel()
    if not ui_state.started:
      rl.draw_rectangle(
        int(self.rect.x), int(self.rect.y),
        int(self.rect.width), int(self.rect.height),
        rl.Color(0, 0, 0, 175),
      )
      self._offroad_label.render(self._rect)

    msg = messaging.new_message('uiDebug')
    msg.uiDebug.drawTimeMillis = (time.monotonic() - start_draw) * 1000
    self._pm.send('uiDebug', msg)

  def _render_road_content(self) -> None:
    self._switch_stream_if_needed(ui_state.sm)
    self._update_calibration()
    super()._render(self._content_rect)
    self._model_renderer.render(self._content_rect)
    rl.draw_texture_ex(
      self._fade_texture,
      rl.Vector2(self._content_rect.x, self._content_rect.y),
      0.0, 1.0, rl.WHITE,
    )

    alert_to_render, not_animating_out = self._alert_renderer.will_render()
    should_draw_dmoji = (
      not self._hud_renderer.drawing_top_icons()
      and ui_state.is_onroad()
      and (ui_state.status != UIStatus.DISENGAGED or ui_state.always_on_dm)
    )
    self._driver_state_renderer.set_should_draw(should_draw_dmoji)
    self._driver_state_renderer.set_position(self._rect.x + 16, self._rect.y + 10)
    self._driver_state_renderer.render()
    self._hud_renderer.set_can_draw_top_icons(alert_to_render is None)
    self._hud_renderer.set_wheel_critical_icon(
      alert_to_render is not None
      and not not_animating_out
      and alert_to_render.visual_alert == car.CarControl.HUDControl.VisualAlert.steerRequired
    )
    if ui_state.started:
      self._alert_renderer.render(self._content_rect)
    self._hud_renderer.render(self._content_rect)

  def _render_dac_content(self) -> None:
    self._dac_view.set_rect(self.rect)
    self._dac_view.render(self.rect)

  def _render_dac_2_content(self) -> None:
    alert_to_render, _ = self._alert_renderer.will_render()
    self._hud_renderer.set_can_draw_top_icons(alert_to_render is None)
    set_speed_alpha = self._hud_renderer.tick_set_speed_visibility_dac()
    self._dac_2_view.set_set_speed_overlay(set_speed_alpha, self._hud_renderer.current_set_speed_text())
    self._dac_2_view.set_rect(self.rect)
    self._dac_2_view.render(self.rect)

  def _render_dac_3_content(self) -> None:
    alert_to_render, _ = self._alert_renderer.will_render()
    self._hud_renderer.set_can_draw_top_icons(alert_to_render is None)
    set_speed_alpha = self._hud_renderer.tick_set_speed_visibility_dac()
    self._dac_3_view.set_set_speed_overlay(set_speed_alpha, self._hud_renderer.current_set_speed_text())
    self._dac_3_view.set_rect(self.rect)
    self._dac_3_view.render(self.rect)

  def _render_side_panel(self) -> None:
    if not self._is_non_road_mode_active():
      self._confidence_ball.render(self.rect)
    self._action_tray.render(self.rect)

  def _switch_stream_if_needed(self, sm) -> None:
    if sm['selfdriveState'].experimentalMode and WIDE_CAM in self.available_streams:
      v_ego = sm['carState'].vEgo
      if v_ego < WIDE_CAM_MAX_SPEED:
        target = WIDE_CAM
      elif v_ego > ROAD_CAM_MIN_SPEED:
        target = ROAD_CAM
      else:
        target = self.stream_type
    else:
      target = ROAD_CAM

    if self.stream_type != target:
      self.switch_stream(target)

  def _update_calibration(self) -> None:
    sm = ui_state.sm
    if not self.device_camera and sm.seen['roadCameraState'] and sm.seen['deviceState']:
      self.device_camera = DEVICE_CAMERAS[
        (str(sm['deviceState'].deviceType), str(sm['roadCameraState'].sensor))
      ]

    if not (sm.updated["liveCalibration"] and sm.valid['liveCalibration']):
      return

    calib = sm['liveCalibration']
    if len(calib.rpyCalib) != 3 or calib.calStatus != CALIBRATED:
      return

    device_from_calib = rot_from_euler(calib.rpyCalib)
    self.view_from_calib = view_frame_from_device_frame @ device_from_calib

    if hasattr(calib, 'wideFromDeviceEuler') and len(calib.wideFromDeviceEuler) == 3:
      wide_from_device = rot_from_euler(calib.wideFromDeviceEuler)
      self.view_from_wide_calib = view_frame_from_device_frame @ wide_from_device @ device_from_calib

  def _calc_frame_matrix(self, rect: rl.Rectangle) -> np.ndarray:
    calib_time   = ui_state.sm.recv_frame['liveCalibration']
    current_dims = (self._content_rect.width, self._content_rect.height)
    device_camera = self.device_camera or DEFAULT_DEVICE_CAMERA
    is_wide = self.stream_type == WIDE_CAM

    intrinsic   = device_camera.ecam.intrinsics if is_wide else device_camera.fcam.intrinsics
    calibration = self.view_from_wide_calib    if is_wide else self.view_from_calib
    zoom = 0.7 * 1.5 if is_wide else np.interp(ui_state.sm['carState'].vEgo, [10, 30], [0.8, 1.0])

    inf_point      = np.array([1000.0, 0.0, 0.0])
    calib_transform = intrinsic @ calibration
    kep            = calib_transform @ inf_point

    x, y = self._content_rect.x, self._content_rect.y
    w, h = self._content_rect.width, self._content_rect.height
    cx, cy = intrinsic[0, 2], intrinsic[1, 2]

    margin       = 5
    max_x_offset = cx * zoom - w / 2 - margin
    max_y_offset = cy * zoom - h / 2 - margin

    try:
      if abs(kep[2]) > 1e-6:
        x_offset = np.clip((kep[0] / kep[2] - cx) * zoom, -max_x_offset, max_x_offset)
        y_offset = np.clip((kep[1] / kep[2] - cy) * zoom + CAM_Y_OFFSET, -max_y_offset, max_y_offset)
      else:
        x_offset, y_offset = 0, 0
    except (ZeroDivisionError, OverflowError):
      x_offset, y_offset = 0, 0

    self._last_calib_time  = calib_time
    self._last_rect_dims   = current_dims
    self._last_stream_type = self.stream_type
    self._cached_matrix = np.array([
      [zoom * 2 * cx / w, 0, -x_offset / w * 2],
      [0, zoom * 2 * cy / h, -y_offset / h * 2],
      [0, 0, 1.0],
    ])

    video_transform = np.array([
      [zoom, 0.0, (w / 2 + x - x_offset) - (cx * zoom)],
      [0.0, zoom, (h / 2 + y - y_offset) - (cy * zoom)],
      [0.0, 0.0, 1.0],
    ])
    self._model_renderer.set_transform(video_transform @ calib_transform)
    return self._cached_matrix


# ── Dev entrypoint ────────────────────────────────────────────────────────────

if __name__ == "__main__":
  gui_app.init_window("OnRoad Camera View")
  road_camera_view = AugmentedRoadView(lambda: None, stream_type=ROAD_CAM)
  print("*** press SPACE to switch camera / D to toggle DAC view ***")
  try:
    for _ in gui_app.render():
      ui_state.update()
      if rl.is_key_released(rl.KeyboardKey.KEY_SPACE):
        if WIDE_CAM in road_camera_view.available_streams:
          stream = ROAD_CAM if road_camera_view.stream_type == WIDE_CAM else WIDE_CAM
          road_camera_view.switch_stream(stream)
      if rl.is_key_released(rl.KeyboardKey.KEY_D):
        road_camera_view._toggle_dac()
      road_camera_view.render(rl.Rectangle(0, 0, gui_app.width, gui_app.height))
  finally:
    road_camera_view.close()

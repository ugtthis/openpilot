from openpilot.cereal.visionipc import VisionStreamType
from openpilot.common.params import Params
from openpilot.common.test import OpenpilotTestCase
from openpilot.system.camcorder.settings import (
  ENCODER_MODE_PARAM, FRAME_RATE_PARAM, QUALITY_PARAM, RESOLUTION_PARAM, TIME_ZONE_PARAM, TIME_ZONES, CamcorderSettings, Quality, Resolution,
)


class TestCamcorderSettings(OpenpilotTestCase):
  def test_defaults_to_stock_openpilot_capture(self):
    assert CamcorderSettings.load() == CamcorderSettings(Quality.STOCK, 20, "")
    assert CamcorderSettings.load().zone is None

  def test_saved_settings_survive_a_reload(self):
    CamcorderSettings(Quality.MAX, 60, "America/Los_Angeles", Resolution.FULL).save()
    assert CamcorderSettings.load() == CamcorderSettings(Quality.MAX, 60, "America/Los_Angeles", Resolution.FULL)
    assert CamcorderSettings.load().sensor_mode == "2688x1520@60"
    assert CamcorderSettings.load().bitrate == 80_000_000

  def test_choosing_utc_clears_the_time_zone(self):
    CamcorderSettings(time_zone="America/Los_Angeles").save()
    CamcorderSettings(time_zone="").save()
    assert Params().get(TIME_ZONE_PARAM) is None

  def test_unknown_values_fall_back_to_stock(self):
    params = Params()
    params.put(QUALITY_PARAM, "ultra", block=True)
    params.put(FRAME_RATE_PARAM, 90, block=True)
    params.put(RESOLUTION_PARAM, "8k", block=True)
    params.put(TIME_ZONE_PARAM, "Mars/Olympus_Mons", block=True)
    assert CamcorderSettings.load(params) == CamcorderSettings(Quality.STOCK, 20, "")

  def test_every_offered_zone_loads(self):
    for name in TIME_ZONES:
      zone = CamcorderSettings(time_zone=name).zone
      assert (zone is None) == (name == "")

  def test_recording_uses_the_applied_mode_not_a_pending_choice(self):
    params = Params()
    CamcorderSettings(Quality.MAX, 60, resolution=Resolution.FULL).save(params)
    assert CamcorderSettings.applied(params) == CamcorderSettings()  # manager hasn't switched yet
    params.put(ENCODER_MODE_PARAM, "2688x1520@60:max", block=True)
    assert CamcorderSettings.applied(params) == CamcorderSettings(Quality.MAX, 60, resolution=Resolution.FULL)
    params.put(ENCODER_MODE_PARAM, "stock:max", block=True)
    assert CamcorderSettings.applied(params) == CamcorderSettings(Quality.MAX)
    for malformed in ("junk@60:max", "2688x1520@60", "2688x1520@90:max", "1344x760@20:stock"):
      params.put(ENCODER_MODE_PARAM, malformed, block=True)
      assert CamcorderSettings.applied(params) == CamcorderSettings(), malformed

  def test_camcorder_modes_apply_to_the_wide_camera_only(self):
    params = Params()
    params.put(ENCODER_MODE_PARAM, "2688x1520@60:max", block=True)
    wide = CamcorderSettings.applied_for(VisionStreamType.VISION_STREAM_WIDE_ROAD, params)
    cabin = CamcorderSettings.applied_for(VisionStreamType.VISION_STREAM_CABIN, params)
    assert (wide.native_size, wide.frame_rate) == ((2688, 1520), 60)
    assert (cabin.native_size, cabin.frame_rate, cabin.bitrate) == ((1344, 760), 20, 5_000_000)

  def test_stock_resolution_and_frame_rate_use_the_exact_stock_mode(self):
    assert CamcorderSettings().sensor_mode == "stock"
    assert CamcorderSettings().bitrate == 5_000_000

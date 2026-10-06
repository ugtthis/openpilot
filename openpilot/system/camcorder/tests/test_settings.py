from openpilot.common.params import Params
from openpilot.common.test import OpenpilotTestCase
from openpilot.system.camcorder.settings import FRAME_RATE_PARAM, QUALITY_PARAM, TIME_ZONE_PARAM, TIME_ZONES, CamcorderSettings, Quality


class TestCamcorderSettings(OpenpilotTestCase):
  def test_defaults_to_stock_openpilot_capture(self):
    assert CamcorderSettings.load() == CamcorderSettings(Quality.STOCK, 20, "")
    assert CamcorderSettings.load().zone is None

  def test_saved_settings_survive_a_reload(self):
    CamcorderSettings(Quality.MAX, 30, "America/Los_Angeles").save()
    assert CamcorderSettings.load() == CamcorderSettings(Quality.MAX, 30, "America/Los_Angeles")

  def test_choosing_utc_clears_the_time_zone(self):
    CamcorderSettings(time_zone="America/Los_Angeles").save()
    CamcorderSettings(time_zone="").save()
    assert Params().get(TIME_ZONE_PARAM) is None

  def test_unknown_values_fall_back_to_stock(self):
    params = Params()
    params.put(QUALITY_PARAM, "ultra", block=True)
    params.put(FRAME_RATE_PARAM, 60, block=True)
    params.put(TIME_ZONE_PARAM, "Mars/Olympus_Mons", block=True)
    assert CamcorderSettings.load(params) == CamcorderSettings(Quality.STOCK, 20, "")

  def test_every_offered_zone_loads(self):
    for name in TIME_ZONES:
      zone = CamcorderSettings(time_zone=name).zone
      assert (zone is None) == (name == "")

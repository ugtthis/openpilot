from openpilot.common.params import Params
from openpilot.common.test import OpenpilotTestCase
from openpilot.system.camcorder.settings import FRAME_RATE_PARAM, QUALITY_PARAM, CamcorderSettings, Quality


class TestCamcorderSettings(OpenpilotTestCase):
  def test_defaults_to_stock_openpilot_capture(self):
    assert CamcorderSettings.load() == CamcorderSettings(Quality.STOCK, 20)

  def test_saved_settings_survive_a_reload(self):
    CamcorderSettings(Quality.MAX, 30).save()
    assert CamcorderSettings.load() == CamcorderSettings(Quality.MAX, 30)

  def test_unknown_values_fall_back_to_stock(self):
    params = Params()
    params.put(QUALITY_PARAM, "ultra", block=True)
    params.put(FRAME_RATE_PARAM, 60, block=True)
    assert CamcorderSettings.load(params) == CamcorderSettings(Quality.STOCK, 20)

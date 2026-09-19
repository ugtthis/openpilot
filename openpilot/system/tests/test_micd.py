import numpy as np

from openpilot.system.micd import SAMPLE_RATE, preferred_input_device, resample_for_spl


class FakeSoundDevice:
  @staticmethod
  def query_devices():
    return [
      {"name": "built-in", "max_input_channels": 1, "default_samplerate": 16000.0},
      {"name": "Wireless Mic Rx: USB Audio", "max_input_channels": 2, "default_samplerate": 48000.0},
    ]


def test_usb_microphone_is_preferred():
  assert preferred_input_device(FakeSoundDevice()) == (1, 48000)


def test_falls_back_to_default_input():
  class BuiltInOnly:
    @staticmethod
    def query_devices():
      return [{"name": "built-in", "max_input_channels": 1, "default_samplerate": 16000.0}]

  assert preferred_input_device(BuiltInOnly()) == (None, SAMPLE_RATE)


def test_resample_for_spl_preserves_50_ms():
  source = np.ones(2400, dtype=np.float32)
  output = resample_for_spl(source, 48000)
  assert output.shape == (800,)
  np.testing.assert_allclose(output, 1.0)

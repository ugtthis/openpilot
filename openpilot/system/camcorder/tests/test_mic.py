from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace

from openpilot.system.camcorder.mic import CamcorderMic, MicPacket, adc_start_ns, select_input_device


class SoundDevice:
  def __init__(self, devices, default=(0, None)):
    self._devices = devices
    self.default = SimpleNamespace(device=default)

  def query_devices(self):
    return self._devices


def device(name, channels, rate):
  return {"name": name, "max_input_channels": channels, "default_samplerate": rate}


def packet(start_ms: int, frames: int = 5, rate: int = 100, channels: int = 1) -> MicPacket:
  return MicPacket(b"\1\0" * frames * channels, rate, channels, start_ms * 1_000_000, frames)


def test_usb_input_wins_and_keeps_its_native_format():
  selected = select_input_device(SoundDevice([
    device("Built-in Mic", 1, 16000),
    device("DJI USB Audio", 2, 48000),
    device("USB output", 0, 48000),
  ]))
  assert (selected.index, selected.name, selected.sample_rate, selected.channels) == (1, "DJI USB Audio", 48000, 2)


def test_default_input_is_used_without_usb():
  selected = select_input_device(SoundDevice([
    device("Built-in Mic", 1, 16000),
    device("Other Mic", 2, 44100),
  ], default=(1, None)))
  assert (selected.index, selected.sample_rate, selected.channels) == (1, 44100, 2)


def test_adc_timestamp_uses_the_first_sample_on_the_boot_clock():
  time_info = SimpleNamespace(currentTime=20.0, inputBufferAdcTime=19.95)
  assert adc_start_ns(time_info, 2400, 48000, now_ns=10_000_000_000) == 9_950_000_000


def test_direct_mic_writes_preroll_overlap_then_live_packets():
  mic = CamcorderMic()
  mic._handle(packet(0))
  mic._handle(packet(50))
  mic._handle(packet(100))

  with TemporaryDirectory() as directory:
    path = Path(directory)
    mic.attach(path, start_ns=75_000_000)
    mic._handle(packet(150))
    audio = mic.finish(stop_ns=175_000_000)

    assert audio is not None
    assert (audio.first_log_mono_ns, audio.frame_count, audio.channels) == (50_000_000, 15, 1)
    assert (path / "audio.s16le").stat().st_size == 30


def test_direct_mic_preroll_is_bounded():
  mic = CamcorderMic()
  for start_ms in range(0, 10_000, 50):
    mic._handle(packet(start_ms))
  assert mic._buffer[0].start_ns >= 4_950_000_000

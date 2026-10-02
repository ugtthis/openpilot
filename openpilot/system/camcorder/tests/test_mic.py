import contextlib
import sys
import threading
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace

import numpy as np
import pytest

from openpilot.system.camcorder import mic as mic_module
from openpilot.system.camcorder.mic import CamcorderMic, MicPacket, adc_start_ns, select_input_device


class SoundDevice:
  def __init__(self, devices, default=(0, None), silent=False, stream_started=None):
    self._devices = devices
    self.default = SimpleNamespace(device=default)
    self.silent = silent
    self.stream_started = stream_started

  def query_devices(self):
    return self._devices

  def _terminate(self):
    pass

  def _initialize(self):
    pass

  def InputStream(self, callback, channels, **kwargs):
    if self.stream_started is not None:
      self.stream_started.set()
    if self.silent:
      return contextlib.nullcontext()  # never calls back, like an unplugged ALSA device
    return LiveStream(callback, channels)


class LiveStream:
  def __init__(self, callback, channels):
    self._callback = callback
    self._channels = channels
    self._closed = threading.Event()

  def __enter__(self):
    threading.Thread(target=self._run, daemon=True).start()

  def __exit__(self, *args):
    self._closed.set()

  def _run(self):
    while not self._closed.wait(0.01):
      self._callback(np.zeros((5, self._channels), dtype="<i2"), 5,
                     SimpleNamespace(currentTime=1.0, inputBufferAdcTime=0.99), None)


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


def test_stable_portaudio_clock_mapping_ignores_callback_scheduling_jitter():
  first = SimpleNamespace(currentTime=20.0, inputBufferAdcTime=19.95)
  second = SimpleNamespace(currentTime=20.07, inputBufferAdcTime=20.00)
  offset = 10_000_000_000 - round(first.currentTime * 1e9)
  assert adc_start_ns(first, 2400, 48000, now_ns=10_000_000_000, clock_offset_ns=offset) == 9_950_000_000
  assert adc_start_ns(second, 2400, 48000, now_ns=10_075_000_000, clock_offset_ns=offset) == 10_000_000_000


def test_direct_mic_writes_preroll_overlap_then_live_packets():
  mic = CamcorderMic()
  mic._handle(packet(0))
  mic._handle(packet(50))
  mic._handle(packet(100))

  with TemporaryDirectory() as directory:
    path = Path(directory)
    mic.attach(path, start_ns=75_000_000)
    mic._handle(packet(150))
    audio = mic.finish(end_ns=175_000_000)

    assert audio is not None
    assert (audio.first_log_mono_ns, audio.frame_count, audio.channels) == (50_000_000, 13, 1)
    assert (path / "audio.s16le").stat().st_size == 26


def test_direct_mic_preroll_is_bounded():
  mic = CamcorderMic()
  for start_ms in range(0, 10_000, 50):
    mic._handle(packet(start_ms))
  assert mic._buffer[0].start_ns >= 4_950_000_000


def test_unplugged_mic_pads_silence_to_the_stop_time_and_marks_the_error():
  mic = CamcorderMic()
  mic.device_name = "DJI USB Audio"
  mic.error = "device unplugged"
  mic._handle(packet(0))
  mic._handle(packet(50))

  with TemporaryDirectory() as directory:
    mic.attach(Path(directory), start_ns=0)
    audio = mic.finish(end_ns=200_000_000)

  assert audio is not None
  assert (audio.frame_count, audio.gap_count, audio.gap_frame_count) == (20, 1, 10)
  assert (audio.device_name, audio.error) == ("DJI USB Audio", "device unplugged")


def test_stalled_stream_fails_so_the_mic_can_restart(monkeypatch):
  monkeypatch.setitem(sys.modules, "sounddevice", SoundDevice([device("DJI USB Audio", 2, 48000)], silent=True))
  monkeypatch.setattr(mic_module, "usb_audio_present", lambda: False)
  monkeypatch.setattr(mic_module, "STALL_TIMEOUT_S", 0.1)
  mic = CamcorderMic()
  mic.start()
  assert mic._thread is not None
  mic._thread.join(timeout=2.0)

  assert not mic.running and not mic.ready
  assert mic.error == "microphone stream stalled"


def test_unplugging_usb_audio_during_a_take_reports_disconnection(monkeypatch):
  usb = threading.Event()
  usb.set()
  stream_started = threading.Event()
  monkeypatch.setitem(sys.modules, "sounddevice",
                      SoundDevice([device("DJI USB Audio", 2, 48000)], silent=True, stream_started=stream_started))
  monkeypatch.setattr(mic_module, "usb_audio_present", usb.is_set)
  monkeypatch.setattr(mic_module, "STALL_TIMEOUT_S", 0.1)
  mic = CamcorderMic()
  mic.start()
  assert stream_started.wait(1.0)
  with TemporaryDirectory() as directory:
    mic.attach(Path(directory), start_ns=0)
    usb.clear()
    assert mic._thread is not None
    mic._thread.join(timeout=2.0)
    mic.abort()

  assert not mic.running
  assert mic.error == "Microphone disconnected"


def test_plugging_in_usb_audio_reselects_the_input_between_takes(monkeypatch):
  usb = threading.Event()
  monkeypatch.setitem(sys.modules, "sounddevice", SoundDevice([device("Built-in Mic", 1, 16000)]))
  monkeypatch.setattr(mic_module, "usb_audio_present", usb.is_set)
  mic = CamcorderMic()
  mic.start()
  assert mic._thread is not None
  usb.set()
  mic._thread.join(timeout=2.0)

  assert not mic.running and mic.error == ""
  mic.start()  # immediate, without the failure backoff
  assert mic.running
  mic.stop()


def test_unplugging_usb_audio_between_takes_switches_inputs_without_an_error(monkeypatch):
  usb = threading.Event()
  usb.set()
  monkeypatch.setitem(sys.modules, "sounddevice", SoundDevice([device("DJI USB Audio", 2, 48000)], silent=True))
  monkeypatch.setattr(mic_module, "usb_audio_present", usb.is_set)
  mic = CamcorderMic()
  mic.start()
  assert mic._thread is not None
  usb.clear()
  mic._thread.join(timeout=2.0)

  assert not mic.running and mic.error == ""


def test_builtin_capture_drops_hardware_channels_that_are_exactly_silent():
  mic = CamcorderMic()
  mic.device_name = "sdm845-tavil-snd-card"
  samples = np.zeros((5, 8), dtype="<i2")
  samples[:, 2] = [1, 2, 3, 4, 5]
  mic._handle(MicPacket(samples.tobytes(), 100, 8, 0, 5))

  with TemporaryDirectory() as directory:
    path = Path(directory)
    mic.attach(path, start_ns=0)
    audio = mic.finish(end_ns=50_000_000)
    recorded = np.fromfile(path / "audio.s16le", dtype="<i2")

  assert audio is not None and audio.channels == 1
  np.testing.assert_array_equal(recorded, samples[:, 2])


def test_usb_capture_keeps_all_advertised_channels():
  mic = CamcorderMic()
  mic.device_name = "DJI USB Audio"
  samples = np.zeros((5, 2), dtype="<i2")
  samples[:, 0] = 1
  mic._handle(MicPacket(samples.tobytes(), 100, 2, 0, 5))

  with TemporaryDirectory() as directory:
    mic.attach(Path(directory), start_ns=0)
    audio = mic.finish(end_ns=50_000_000)

  assert audio is not None and audio.channels == 2


def test_audio_write_failure_stops_the_take_instead_of_dropping_the_track():
  class BrokenWriter:
    def add_packet(self, *args):
      raise OSError("disk full")

  mic = CamcorderMic()
  mic._writer = BrokenWriter()

  with pytest.raises(RuntimeError, match="audio write failed: disk full"):
    mic._write(packet(0))

  assert mic.write_error == "audio write failed: disk full"
  assert mic.error == mic.write_error

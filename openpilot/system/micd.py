#!/usr/bin/env python3
import numpy as np
from functools import cache
import threading

from openpilot.cereal import messaging
from openpilot.common.realtime import Ratekeeper
from openpilot.common.utils import retry
from openpilot.common.swaglog import cloudlog
from openpilot.system.audio_utils import PCM_DTYPE, patch_sounddevice

RATE = 10
FFT_SAMPLES = 1600  # 100 ms at SAMPLE_RATE
REFERENCE_SPL = 2e-5  # newtons/m^2
SAMPLE_RATE = 16000
CAPTURE_BLOCK_DURATION_S = 0.05
USB_AUDIO_NAME = "usb audio"


def preferred_input_device(sd) -> tuple[int | None, int]:
  """Prefer a USB microphone and otherwise use the configured default input.

  PortAudio does not expose physical USB-port topology. ALSA does identify USB
  interfaces with "USB Audio" in their PortAudio name, which lets the side-port
  receiver take priority without hard-coding a product-specific microphone.
  """
  for index, device in enumerate(sd.query_devices()):
    if device["max_input_channels"] > 0 and USB_AUDIO_NAME in device["name"].lower():
      sample_rate = round(device["default_samplerate"])
      if sample_rate > 0:
        return index, sample_rate
  return None, SAMPLE_RATE


def resample_for_spl(samples: np.ndarray, source_rate: int) -> np.ndarray:
  if source_rate == SAMPLE_RATE:
    return samples
  output_count = round(len(samples) * SAMPLE_RATE / source_rate)
  if output_count <= 0:
    return np.empty(0, dtype=samples.dtype)
  source_positions = np.arange(len(samples), dtype=np.float64)
  output_positions = np.arange(output_count, dtype=np.float64) * source_rate / SAMPLE_RATE
  return np.interp(output_positions, source_positions, samples).astype(samples.dtype)


@cache
def get_a_weighting_filter():
  # Calculate the A-weighting filter
  # https://en.wikipedia.org/wiki/A-weighting
  freqs = np.fft.fftfreq(FFT_SAMPLES, d=1 / SAMPLE_RATE)
  A = 12194 ** 2 * freqs ** 4 / ((freqs ** 2 + 20.6 ** 2) * (freqs ** 2 + 12194 ** 2) * np.sqrt((freqs ** 2 + 107.7 ** 2) * (freqs ** 2 + 737.9 ** 2)))
  return A / np.max(A)


def calculate_spl(measurements):
  # https://www.engineeringtoolbox.com/sound-pressure-d_711.html
  sound_pressure = np.sqrt(np.mean(measurements ** 2))  # RMS of amplitudes
  if sound_pressure > 0:
    sound_pressure_level = 20 * np.log10(sound_pressure / REFERENCE_SPL)  # dB
  else:
    sound_pressure_level = 0
  return sound_pressure, sound_pressure_level


def apply_a_weighting(measurements: np.ndarray) -> np.ndarray:
  # Generate a Hanning window of the same length as the audio measurements
  measurements_windowed = measurements * np.hanning(len(measurements))

  # Apply the A-weighting filter to the signal
  return np.abs(np.fft.ifft(np.fft.fft(measurements_windowed) * get_a_weighting_filter()))


class Mic:
  def __init__(self):
    self.rk = Ratekeeper(RATE)
    self.pm = messaging.PubMaster(['soundPressure', 'rawAudioData'])
    self.capture_sample_rate = SAMPLE_RATE

    self.measurements = np.empty(0)

    self.sound_pressure = 0
    self.sound_pressure_weighted = 0
    self.sound_pressure_level_weighted = 0

    self.lock = threading.Lock()

  def update(self):
    with self.lock:
      sound_pressure = self.sound_pressure
      sound_pressure_weighted = self.sound_pressure_weighted
      sound_pressure_level_weighted = self.sound_pressure_level_weighted

    msg = messaging.new_message('soundPressure', valid=True)
    msg.soundPressure.soundPressure = float(sound_pressure)
    msg.soundPressure.soundPressureWeighted = float(sound_pressure_weighted)
    msg.soundPressure.soundPressureWeightedDb = float(sound_pressure_level_weighted)

    self.pm.send('soundPressure', msg)
    self.rk.keep_time()

  def callback(self, indata, frames, time, status):
    """
    Using amplitude measurements, calculate an uncalibrated sound pressure and sound pressure level.
    Then apply A-weighting to the raw amplitudes and run the same calculations again.

    Logged A-weighted equivalents are rough approximations of the human-perceived loudness.
    """
    msg = messaging.new_message('rawAudioData', valid=True)
    audio_data_int_16 = (indata[:, 0] * 32767).astype(PCM_DTYPE)
    msg.rawAudioData.data = audio_data_int_16.tobytes()
    msg.rawAudioData.sampleRate = self.capture_sample_rate
    self.pm.send('rawAudioData', msg)

    with self.lock:
      spl_samples = resample_for_spl(indata[:, 0], self.capture_sample_rate)
      self.measurements = np.concatenate((self.measurements, spl_samples))

      while self.measurements.size >= FFT_SAMPLES:
        measurements = self.measurements[:FFT_SAMPLES]

        self.sound_pressure, _ = calculate_spl(measurements)
        measurements_weighted = apply_a_weighting(measurements)
        self.sound_pressure_weighted, self.sound_pressure_level_weighted = calculate_spl(measurements_weighted)

        self.measurements = self.measurements[FFT_SAMPLES:]

  @retry(attempts=10, delay=3)
  def get_stream(self, sd):
    # reload sounddevice to reinitialize portaudio
    sd._terminate()
    sd._initialize()
    device, self.capture_sample_rate = preferred_input_device(sd)
    blocksize = round(CAPTURE_BLOCK_DURATION_S * self.capture_sample_rate)
    return sd.InputStream(device=device, channels=1, samplerate=self.capture_sample_rate,
                          callback=self.callback, blocksize=blocksize)

  def micd_thread(self):
    # sounddevice must be imported after forking processes
    import sounddevice as sd
    patch_sounddevice(sd)

    with self.get_stream(sd) as stream:
      cloudlog.info(f"micd stream started: {stream.samplerate=} {stream.channels=} {stream.dtype=} {stream.device=}, {stream.blocksize=}")
      while True:
        self.update()


def main():
  mic = Mic()
  mic.micd_thread()


if __name__ == "__main__":
  main()

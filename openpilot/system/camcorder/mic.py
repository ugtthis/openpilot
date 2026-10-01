"""Direct, lossless microphone capture for camcorderd."""

import queue
import threading
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from openpilot.common.swaglog import cloudlog
from openpilot.system.audio_utils import PCM_DTYPE, patch_sounddevice
from openpilot.system.camcorder.clip_storage import AudioInfo, AudioWriter
from openpilot.system.camcorder.timing import boot_time_ns

BLOCK_DURATION_S = 0.05
PREROLL_NS = 5_000_000_000
STOP_TIMEOUT_S = 1.0
USB_AUDIO_NAME = "usb audio"


@dataclass(frozen=True, slots=True)
class MicDevice:
  index: int | None
  name: str
  sample_rate: int
  channels: int


@dataclass(frozen=True, slots=True)
class MicPacket:
  data: bytes
  sample_rate: int
  channels: int
  start_ns: int
  frames: int

  @property
  def end_ns(self) -> int:
    return self.start_ns + self.frames * 1_000_000_000 // self.sample_rate


def select_input_device(sd) -> MicDevice:
  devices = list(sd.query_devices())
  inputs = [(i, device) for i, device in enumerate(devices) if int(device["max_input_channels"]) > 0]
  preferred = next(((i, d) for i, d in inputs if USB_AUDIO_NAME in str(d["name"]).lower()), None)
  if preferred is None:
    default = getattr(sd.default, "device", (None, None))
    default_index = default[0] if isinstance(default, (tuple, list)) else default
    preferred = next(((i, d) for i, d in inputs if i == default_index), None)
  if preferred is None and inputs:
    preferred = inputs[0]
  if preferred is None:
    raise RuntimeError("no microphone input is available")
  index, device = preferred
  sample_rate = round(float(device["default_samplerate"]))
  channels = int(device["max_input_channels"])
  if sample_rate <= 0 or channels <= 0:
    raise RuntimeError("microphone has an invalid native format")
  return MicDevice(index, str(device["name"]), sample_rate, channels)


def adc_start_ns(time_info, frames: int, sample_rate: int, now_ns: int | None = None) -> int:
  now_ns = boot_time_ns() if now_ns is None else now_ns
  try:
    delay_s = float(time_info.currentTime) - float(time_info.inputBufferAdcTime)
  except (AttributeError, TypeError, ValueError):
    delay_s = frames / sample_rate
  if delay_s <= 0 or delay_s > 1.0:
    delay_s = frames / sample_rate
  return now_ns - round(delay_s * 1e9)


class CamcorderMic:
  def __init__(self):
    self._lock = threading.Lock()
    self._stop = threading.Event()
    self._packet_ready = threading.Event()
    self._packets: queue.SimpleQueue[MicPacket] = queue.SimpleQueue()
    self._buffer: deque[MicPacket] = deque()
    self._thread: threading.Thread | None = None
    self._writer: AudioWriter | None = None
    self._write_after_ns = 0
    self._last_end_ns = 0
    self.device_name = ""
    self.sample_rate = 0
    self.channels = 0
    self.overflow_count = 0
    self.error = ""
    self._retry_after = 0.0

  @property
  def running(self) -> bool:
    return self._thread is not None and self._thread.is_alive()

  def start(self) -> None:
    if self.running or time.monotonic() < self._retry_after:
      return
    self._stop.clear()
    self.error = ""
    self._thread = threading.Thread(target=self._capture, name="camcorder-mic", daemon=True)
    self._thread.start()

  def attach(self, clip_path: Path, start_ns: int) -> None:
    with self._lock:
      if self._writer is not None:
        raise RuntimeError("microphone writer is already attached")
      self._writer = AudioWriter(clip_path)
      self._write_after_ns = start_ns
      for packet in self._buffer:
        if packet.end_ns > start_ns:
          self._write(packet)

  def finish(self, stop_ns: int) -> AudioInfo | None:
    deadline = time.monotonic() + STOP_TIMEOUT_S
    while self.running and self._last_end_ns < stop_ns and time.monotonic() < deadline:
      self._packet_ready.wait(min(0.05, max(0.0, deadline - time.monotonic())))
      self._packet_ready.clear()
    with self._lock:
      writer, self._writer = self._writer, None
      self._write_after_ns = 0
    return writer.finalize() if writer is not None else None

  def stop(self) -> None:
    self._stop.set()
    thread = self._thread
    if thread is not None:
      thread.join(timeout=2.0)
    self._thread = None

  def abort(self) -> None:
    with self._lock:
      writer, self._writer = self._writer, None
    if writer is not None:
      writer.abort()

  def _capture(self) -> None:
    # PortAudio must be initialized after manager forks this process.
    import sounddevice as sd
    patch_sounddevice(sd)
    try:
      sd._terminate()
      sd._initialize()
      device = select_input_device(sd)
      self.device_name = device.name
      self.sample_rate = device.sample_rate
      self.channels = device.channels
      blocksize = round(BLOCK_DURATION_S * device.sample_rate)

      def callback(indata, frames, time_info, status):
        if status:
          self.overflow_count += 1
        data = np.asarray(indata, dtype=PCM_DTYPE).tobytes()
        self._packets.put(MicPacket(data, device.sample_rate, device.channels,
                                    adc_start_ns(time_info, frames, device.sample_rate), frames))

      with sd.InputStream(device=device.index, channels=device.channels, samplerate=device.sample_rate,
                          dtype="int16", callback=callback, blocksize=blocksize):
        cloudlog.info(f"camcorder mic started: {device=}")
        while not self._stop.is_set():
          try:
            packet = self._packets.get(timeout=0.1)
          except queue.Empty:
            continue
          self._handle(packet)
        while not self._packets.empty():
          self._handle(self._packets.get())
    except Exception as exc:
      self.error = str(exc)
      self._retry_after = time.monotonic() + 3.0
      cloudlog.exception("camcorder microphone failed")

  def _handle(self, packet: MicPacket) -> None:
    with self._lock:
      self._buffer.append(packet)
      while self._buffer and self._buffer[0].start_ns < packet.start_ns - PREROLL_NS:
        self._buffer.popleft()
      if self._writer is not None and packet.end_ns > self._write_after_ns:
        self._write(packet)
      self._last_end_ns = packet.end_ns
    self._packet_ready.set()

  def _write(self, packet: MicPacket) -> None:
    assert self._writer is not None
    self._writer.add_packet(packet.data, packet.sample_rate, packet.start_ns, packet.channels)

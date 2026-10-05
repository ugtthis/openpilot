"""Direct PCM track writing and interrupted-track recovery."""

from dataclasses import dataclass
from pathlib import Path

from openpilot.system.audio_utils import PCM_SAMPLE_BYTES
from openpilot.system.camcorder.journal import PeriodicSync, read_json, write_json_atomic

_AUDIO_PCM = "audio.s16le"
_AUDIO_PARTIAL = "audio.s16le.partial"
_AUDIO_INFO = "audio.info"
# ADC-start timestamps do not include process scheduling jitter. Allow small
# hardware-clock noise, but detect even one missing 50 ms capture block.
_AUDIO_GAP_TOLERANCE_NS = 10_000_000
# Real mic clocks are within a few hundred ppm; anything further is a bad timestamp.
_AUDIO_MAX_CLOCK_ERROR = 0.01


@dataclass(frozen=True)
class AudioInfo:
  filename: str
  sample_rate: int
  channels: int
  frame_count: int
  first_log_mono_ns: int
  gap_count: int = 0
  gap_frame_count: int = 0
  device_name: str = ""
  overflow_count: int = 0
  error: str = ""
  measured_sample_rate: float = 0.0


class AudioWriter:
  """Atomically persist one fixed-format little-endian int16 PCM stream."""

  def __init__(self, clip_path: Path):
    self._path = clip_path / _AUDIO_PCM
    self._partial = clip_path / _AUDIO_PARTIAL
    self._info_path = clip_path / _AUDIO_INFO
    self._file = open(self._partial, "wb", buffering=0)
    self._sync = PeriodicSync(self._file)
    self._sample_rate = 0
    self._channels = 1
    self._frame_count = 0
    self._first_log_mono_ns = 0
    self._end_ns = 0
    self._gap_count = 0
    self._gap_frame_count = 0

  def add_packet(self, data: bytes, sample_rate: int, log_mono_ns: int, channels: int = 1) -> None:
    if self._file.closed:
      raise RuntimeError("audio writer is closed")
    if sample_rate <= 0 or channels <= 0:
      raise ValueError("invalid int16 audio packet")
    frame_size = PCM_SAMPLE_BYTES * channels
    if len(data) % frame_size:
      raise ValueError("invalid int16 audio packet")
    if self._sample_rate and (sample_rate != self._sample_rate or channels != self._channels):
      raise ValueError("audio format changed during recording")
    frames = len(data) // frame_size
    if not self._sample_rate:
      self._sample_rate = sample_rate
      self._channels = channels
      self._first_log_mono_ns = log_mono_ns
      self._write_info()
    elif log_mono_ns - self._end_ns > _AUDIO_GAP_TOLERANCE_NS:
      # Compare against the previous block, not the nominal rate: mic clocks are
      # off by hundreds of ppm, which is drift for the exporter, not lost audio.
      self._pad_to(log_mono_ns)
    self._file.write(data)
    self._frame_count += frames
    self._end_ns = log_mono_ns + frames * 1_000_000_000 // sample_rate
    if self._sync.maybe_sync():
      self._write_info()

  def _write_silence(self, frames: int) -> None:
    frame_size = PCM_SAMPLE_BYTES * self._channels
    silence = bytes(self._sample_rate * frame_size)
    remaining = frames
    while remaining > 0:
      count = min(remaining, self._sample_rate)
      self._file.write(silence[:count * frame_size])
      remaining -= count

  def _pad_to(self, end_ns: int) -> None:
    missing = max(0, (end_ns - self._end_ns) * self._sample_rate // 1_000_000_000)
    if not missing:
      return
    self._write_silence(missing)
    self._frame_count += missing
    self._end_ns = end_ns
    self._gap_count += 1
    self._gap_frame_count += missing

  def _end_at(self, end_ns: int) -> None:
    if end_ns >= self._end_ns:
      self._pad_to(end_ns)
      return
    excess = (self._end_ns - end_ns) * self._sample_rate // 1_000_000_000
    self._frame_count = max(0, self._frame_count - excess)
    self._end_ns = end_ns

  def finalize(self, end_ns: int | None = None) -> AudioInfo | None:
    """Publish the track, trimmed or silence-padded to end exactly at end_ns."""
    if self._sample_rate:
      if end_ns is not None:
        self._end_at(end_ns)
      self._file.truncate(self._frame_count * PCM_SAMPLE_BYTES * self._channels)
      self._sync.sync()
      self._write_info()
    self._file.close()
    if self._frame_count <= 0:
      self._partial.unlink(missing_ok=True)
      self._info_path.unlink(missing_ok=True)
      return None
    self._partial.replace(self._path)
    return AudioInfo(self._path.name, self._sample_rate, self._channels,
                     self._frame_count, self._first_log_mono_ns,
                     self._gap_count, self._gap_frame_count,
                     measured_sample_rate=self._measured_sample_rate())

  def _measured_sample_rate(self) -> float:
    """Samples per second of boot clock, so long takes don't drift against video."""
    span_ns = self._end_ns - self._first_log_mono_ns
    if span_ns <= 0:
      return float(self._sample_rate)
    measured = self._frame_count * 1e9 / span_ns
    if abs(measured / self._sample_rate - 1) > _AUDIO_MAX_CLOCK_ERROR:
      return float(self._sample_rate)
    return measured

  def _write_info(self) -> None:
    write_json_atomic(self._info_path, {
      "sample_rate": self._sample_rate,
      "measured_sample_rate": self._measured_sample_rate(),
      "channels": self._channels,
      "first_log_mono_ns": self._first_log_mono_ns,
    })

  def abort(self) -> None:
    if not self._file.closed:
      self._file.close()
    self._partial.unlink(missing_ok=True)
    self._path.unlink(missing_ok=True)
    self._info_path.unlink(missing_ok=True)


def recover_audio(clip_path: Path) -> AudioInfo | None:
  partial = clip_path / _AUDIO_PARTIAL
  output = clip_path / _AUDIO_PCM
  source = output if output.is_file() else partial
  info_path = clip_path / _AUDIO_INFO
  info = read_json(info_path)
  if info is None or not source.is_file():
    return None
  try:
    sample_rate = int(info["sample_rate"])
    channels = int(info["channels"])
    first_log_mono_ns = int(info["first_log_mono_ns"])
    measured_sample_rate = float(info.get("measured_sample_rate") or sample_rate)
    if sample_rate <= 0 or channels <= 0:
      return None
    frame_size = PCM_SAMPLE_BYTES * channels
    frame_count = source.stat().st_size // frame_size
    if frame_count <= 0:
      return None
    with open(source, "r+b") as file:
      file.truncate(frame_count * frame_size)
    if source == partial:
      partial.replace(output)
    return AudioInfo(output.name, sample_rate, channels, frame_count, first_log_mono_ns,
                     error="recording was interrupted", measured_sample_rate=measured_sample_rate)
  except (KeyError, OSError, TypeError, ValueError):
    return None
  finally:
    partial.unlink(missing_ok=True)
    info_path.unlink(missing_ok=True)

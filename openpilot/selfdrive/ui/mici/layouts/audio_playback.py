import threading

import numpy as np

from openpilot.common.swaglog import cloudlog
from openpilot.selfdrive.ui.mici.layouts.clip_storage import Clip
from openpilot.system.audio_utils import PCM_DTYPE, PCM_SAMPLE_BYTES, patch_sounddevice

_CALLBACKS_PER_SECOND = 50


class ClipAudioPlayer:
  """Play a clip's raw PCM sidecar on the same timeline as its video.

  ``sync`` positions audio after play/pause/seek events. Between those events,
  the PortAudio callback advances a sample cursor continuously so UI scheduling
  jitter cannot make playback skip backward or forward.
  """

  def __init__(self, clip: Clip):
    self._clip = clip
    self._lock = threading.Lock()
    self._samples: np.memmap | None = None
    self._stream = None
    self._muted = False
    self._playing = False
    self._cursor = 0
    start_ns = clip.recording_start_mono_ns or clip.video_start_mono_ns
    self._audio_offset_s = ((clip.audio_start_mono_ns - start_ns) / 1e9
                            if clip.audio_start_mono_ns and start_ns else 0.0)

  @property
  def available(self) -> bool:
    return self._samples is not None and self._stream is not None

  def open(self) -> bool:
    if not self._clip.has_audio:
      return False
    try:
      sample_path = self._clip.path / str(self._clip.audio)
      sample_count = sample_path.stat().st_size // PCM_SAMPLE_BYTES
      usable_frames = min(self._clip.audio_frame_count,
                          sample_count // self._clip.audio_channels)
      if usable_frames <= 0:
        return False
      self._samples = np.memmap(sample_path, dtype=PCM_DTYPE, mode="r",
                                shape=(usable_frames, self._clip.audio_channels))

      # sounddevice must be imported after manager forks the UI process.
      import sounddevice as sd
      patch_sounddevice(sd)
      sd._terminate()
      sd._initialize()
      self._stream = sd.OutputStream(
        channels=self._clip.audio_channels,
        samplerate=self._clip.audio_sample_rate,
        dtype=PCM_DTYPE.name,
        blocksize=max(1, self._clip.audio_sample_rate // _CALLBACKS_PER_SECOND),
        callback=self._callback,
      )
      self._stream.start()
      return True
    except Exception:
      cloudlog.exception("camcorder could not open clip audio")
      self.close()
      return False

  def close(self) -> None:
    stream, self._stream = self._stream, None
    if stream is not None:
      try:
        stream.stop()
        stream.close()
      except Exception:
        cloudlog.exception("camcorder could not close clip audio")
    self._samples = None

  def set_muted(self, muted: bool) -> None:
    with self._lock:
      self._muted = muted

  def sync(self, playhead_s: float, playing: bool) -> None:
    """Apply a clip-timeline discontinuity without resetting every callback."""
    with self._lock:
      self._cursor = round((playhead_s - self._audio_offset_s) * self._clip.audio_sample_rate)
      self._playing = playing

  def _callback(self, outdata, frames, _time_info, _status) -> None:
    outdata.fill(0)
    samples = self._samples
    if samples is None:
      return
    with self._lock:
      muted = self._muted
      playing = self._playing
      source_start = self._cursor
      if playing:
        self._cursor += frames
    if not playing:
      return

    destination_start = max(0, -source_start)
    source_start = max(0, source_start)
    count = min(frames - destination_start, len(samples) - source_start)
    if not muted and count > 0:
      outdata[destination_start:destination_start + count] = samples[source_start:source_start + count]

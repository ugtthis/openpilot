"""Shared raw-audio and sounddevice compatibility helpers."""

import numpy as np

# rawAudioData and camcorder sidecars use little-endian signed 16-bit PCM.
PCM_DTYPE = np.dtype("<i2")
PCM_SAMPLE_BYTES = PCM_DTYPE.itemsize


def patch_sounddevice(sd) -> None:
  """Work around old sounddevice versions that call removed NumPy APIs."""
  def sounddevice_array(buffer, channels, dtype):
    return np.frombuffer(buffer, dtype=dtype).reshape(-1, channels)

  sd._array = sounddevice_array

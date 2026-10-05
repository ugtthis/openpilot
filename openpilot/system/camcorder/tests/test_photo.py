import struct

import numpy as np

from openpilot.common.test import OpenpilotTestCase
from openpilot.system.camcorder.cameras import CABIN_CAMERA
from openpilot.system.camcorder.photo import take_photo
from openpilot.system.camcorder.preview_track import ClipReader


class TestPhoto(OpenpilotTestCase):
  def test_daemon_photo_path_writes_a_native_lossless_still(self):
    image = np.arange(12 * 8 * 3, dtype=np.uint8).reshape(8, 12, 3)
    clip = take_photo(CABIN_CAMERA.stream_type, grab=lambda _stream: (image, 12, 8))

    assert clip is not None
    assert clip.is_photo
    assert clip.duration_s == 0.0
    assert (clip.width, clip.height) == (12, 8)
    assert clip.camera == CABIN_CAMERA.clip_name
    assert clip.flip_h == CABIN_CAMERA.flip_h

    frames = (clip.path / "frames.bin").read_bytes()
    size, = struct.unpack_from("<I", frames)
    assert size == len(frames) - 4
    assert not frames[4:].startswith(b"\xff\xd8")
    with ClipReader(clip) as reader:
      np.testing.assert_array_equal(reader.frame(0), image)

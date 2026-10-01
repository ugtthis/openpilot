import json
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory

import numpy as np

from openpilot.common.test import OpenpilotTestCase
from openpilot.selfdrive.ui.mici.layouts.audio_playback import ClipAudioPlayer
from openpilot.system.camcorder.clip_storage import (
  CLIP_ASPECT, CLIP_HEIGHT, CLIP_WIDTH, AudioWriter, ClipReader, ClipWriter, center_crop, delete_all_clips, delete_clip,
  extract_clip_rgb, format_timecode, list_clips, load_clip, preview_size, recover_interrupted_clips, scale_rgb,
)
from openpilot.system.camcorder.hevc_writer import HevcWriter


def _make_nv12(width: int, height: int, stride: int | None = None, y=128, u=128, v=128) -> tuple[np.ndarray, int, int]:
  stride = stride or width
  uv_height = ((height // 2) + 15) // 16 * 16
  uv_offset = stride * height
  buf = np.zeros(uv_offset + stride * uv_height, dtype=np.uint8)
  buf[:uv_offset].reshape(-1, stride)[:height, :width] = y
  uv = buf[uv_offset:].reshape(-1, stride)
  uv[:height // 2, 0:width:2] = u
  uv[:height // 2, 1:width:2] = v
  return buf, stride, uv_offset


class TestCamcorderClips(OpenpilotTestCase):
  def test_format_timecode(self):
    assert format_timecode(0) == "0:00"
    assert format_timecode(12.9) == "0:12"
    assert format_timecode(75) == "1:15"
    assert format_timecode(3661) == "1:01:01"

  def test_center_crop_matches_clip_aspect(self):
    x, y, w, h = center_crop(1920, 1080)
    assert abs(w / h - CLIP_ASPECT) < 1e-6
    assert x > 0 and y == 0
    assert abs(x * 2 + w - 1920) < 1e-6
    x, y, w, h = center_crop(480, 640)
    assert abs(w / h - CLIP_ASPECT) < 1e-6
    assert x == 0 and y > 0
    assert abs(y * 2 + h - 640) < 1e-6
    assert center_crop(0, 10) == (0.0, 0.0, 0.0, 0.0)

  def test_extract_neutral_gray(self):
    buf, stride, uv_offset = _make_nv12(64, 48)
    rgb = extract_clip_rgb(buf, 64, 48, stride, uv_offset, out_w=16, out_h=12)
    assert rgb.shape == (12, 16, 3)
    assert np.abs(rgb.astype(np.int16) - 128).max() <= 2

  def test_extract_flip_swaps_left_right(self):
    buf, stride, uv_offset = _make_nv12(64, 48, y=16)
    y = buf[:uv_offset].reshape(-1, stride)
    y[:48, 48:] = 220
    left = extract_clip_rgb(buf, 64, 48, stride, uv_offset, out_w=16, out_h=12, flip_h=False)
    right = extract_clip_rgb(buf, 64, 48, stride, uv_offset, out_w=16, out_h=12, flip_h=True)
    assert left[6, 2, 0] < left[6, 13, 0]
    assert right[6, 2, 0] > right[6, 13, 0]

  def test_uncropped_preview_keeps_full_width(self):
    buf, stride, uv_offset = _make_nv12(64, 40, y=16)
    y = buf[:uv_offset].reshape(-1, stride)
    y[:40, :8] = 220
    out = extract_clip_rgb(buf, 64, 40, stride, uv_offset,
                           out_w=32, out_h=20, crop_aspect=None)
    assert out.shape == (20, 32, 3)
    assert out[10, 1, 0] > out[10, 16, 0]

  def test_preview_size_preserves_processed_camera_aspect(self):
    assert preview_size(1344, 760) == (636, 360)
    assert preview_size(0, 0) == (CLIP_WIDTH, CLIP_HEIGHT)

  def test_scale_rgb(self):
    src = np.zeros((20, 40, 3), dtype=np.uint8)
    src[:, :20] = (255, 0, 0)
    out = scale_rgb(src, 8, 4)
    assert out.shape == (4, 8, 3)
    assert out[0, 0, 0] == 255
    assert out[0, 7, 0] == 0

  def test_write_list_and_read_clip(self):
    frames = [np.full((CLIP_HEIGHT, CLIP_WIDTH, 3), i * 20, dtype=np.uint8) for i in range(1, 4)]
    writer = ClipWriter("wide")
    for i, frame in enumerate(frames):
      writer.add_frame(frame, i * 50)
    clip = writer.finalize()
    assert clip is not None
    assert clip.camera == "wide"
    assert clip.frame_count == 3
    assert clip.duration_s == 0.15

    listed = list_clips()
    assert [item.clip_id for item in listed] == [clip.clip_id]

    with ClipReader(clip) as reader:
      assert reader.timestamps_ms() == [0, 50, 100]
      assert reader.frame_index_at_ms(0) == 0
      assert reader.frame_index_at_ms(49) == 0
      assert reader.frame_index_at_ms(50) == 1
      assert reader.frame_index_at_ms(5000) == 2
      np.testing.assert_array_equal(reader.frame(1), frames[1])
      np.testing.assert_array_equal(reader.frame(99), frames[2])

  def test_delete_clip_removes_it_from_the_library(self):
    writer = ClipWriter("wide")
    writer.add_frame(np.zeros((CLIP_HEIGHT, CLIP_WIDTH, 3), dtype=np.uint8), 0)
    clip = writer.finalize()
    assert clip is not None
    assert delete_clip(clip)
    assert not clip.path.exists()
    assert list_clips() == []

  def test_delete_all_clips_clears_the_library(self):
    for camera in ("wide", "cabin"):
      writer = ClipWriter(camera)
      writer.add_frame(np.zeros((CLIP_HEIGHT, CLIP_WIDTH, 3), dtype=np.uint8), 0)
      assert writer.finalize() is not None
    assert len(list_clips()) == 2
    assert delete_all_clips() == 2
    assert list_clips() == []

  def test_empty_writer_is_discarded(self):
    writer = ClipWriter("cabin")
    path = writer.path
    assert writer.finalize() is None
    assert not path.exists()
    assert list_clips() == []

  def test_in_progress_clip_is_hidden(self):
    writer = ClipWriter("wide")
    writer.add_frame(np.zeros((CLIP_HEIGHT, CLIP_WIDTH, 3), dtype=np.uint8), 0)
    assert list_clips() == []
    writer.abort()
    assert list_clips() == []

  def test_full_frame_preview_metadata_round_trip(self):
    writer = ClipWriter("wide", 636, 360, preview_contains_full_frame=True)
    writer.add_frame(np.zeros((360, 636, 3), dtype=np.uint8), 0)
    clip = writer.finalize()
    assert clip is not None
    assert clip.has_full_frame_preview
    assert (clip.width, clip.height) == (636, 360)

  def test_photo_metadata_round_trip(self):
    writer = ClipWriter("wide", media_type="photo")
    writer.add_frame(np.zeros((CLIP_HEIGHT, CLIP_WIDTH, 3), dtype=np.uint8), 0)
    photo = writer.finalize()
    assert photo is not None
    assert photo.is_photo
    assert photo.duration_s == 0.0
    with ClipReader(photo) as reader:
      assert reader.frame(0).shape == (CLIP_HEIGHT, CLIP_WIDTH, 3)

  def test_audio_metadata_round_trip(self):
    writer = ClipWriter("wide", recording_start_mono_ns=1_000_000_000)
    writer.add_frame(np.zeros((CLIP_HEIGHT, CLIP_WIDTH, 3), dtype=np.uint8), 0)
    audio_writer = AudioWriter(writer.path)
    samples = np.arange(20, dtype=np.int16)
    audio_writer.add_packet(samples.tobytes(), sample_rate=10, log_mono_ns=1_200_000_000)
    audio = audio_writer.finalize()
    assert audio is not None
    clip = writer.finalize(audio=audio)
    assert clip is not None
    assert clip.has_audio
    assert clip.recording_start_mono_ns == 1_000_000_000
    assert clip.audio_start_mono_ns == 1_200_000_000
    assert clip.audio_sample_rate == 10
    assert clip.audio_channels == 1
    assert clip.audio_frame_count == 20
    assert clip.audio_timestamp == "adc_start_boottime"
    assert clip.audio_overflow_count == 0
    assert clip.audio_error == ""
    np.testing.assert_array_equal(np.fromfile(clip.path / str(clip.audio), dtype=np.int16), samples)

  def test_audio_writer_abort_removes_partial(self):
    writer = ClipWriter("wide")
    audio_writer = AudioWriter(writer.path)
    audio_writer.add_packet(np.zeros(10, dtype=np.int16).tobytes(), 16000, 1)
    audio_writer.abort()
    assert not (writer.path / "audio.s16le").exists()
    assert not (writer.path / "audio.s16le.partial").exists()
    writer.abort()

  def test_audio_writer_fills_dropped_packets_with_silence(self):
    writer = ClipWriter("wide")
    audio_writer = AudioWriter(writer.path)
    packet = np.ones(5, dtype=np.int16).tobytes()
    audio_writer.add_packet(packet, sample_rate=100, log_mono_ns=0)
    audio_writer.add_packet(packet, sample_rate=100, log_mono_ns=50_000_000)
    # The 50 ms packet starting at 100 ms never arrived.
    audio_writer.add_packet(packet, sample_rate=100, log_mono_ns=150_000_000)
    audio = audio_writer.finalize()
    assert audio is not None
    assert audio.frame_count == 20
    assert (audio.gap_count, audio.gap_frame_count) == (1, 5)
    samples = np.fromfile(writer.path / audio.filename, dtype=np.int16)
    np.testing.assert_array_equal(samples, [1] * 10 + [0] * 5 + [1] * 5)

    writer.add_frame(np.zeros((CLIP_HEIGHT, CLIP_WIDTH, 3), dtype=np.uint8), 0)
    clip = writer.finalize(audio=audio)
    assert clip is not None
    meta = json.loads((clip.path / "clip.json").read_text())
    assert (meta["audio_gap_count"], meta["audio_gap_frame_count"]) == (1, 5)

  def test_audio_writer_ignores_send_jitter(self):
    writer = ClipWriter("wide")
    audio_writer = AudioWriter(writer.path)
    packet = np.ones(5, dtype=np.int16).tobytes()
    for log_mono_ns in (0, 52_000_000, 99_000_000, 151_000_000):
      audio_writer.add_packet(packet, sample_rate=100, log_mono_ns=log_mono_ns)
    audio = audio_writer.finalize()
    assert audio is not None
    assert audio.frame_count == 20
    writer.abort()

  def test_audio_writer_treats_slow_mic_clock_as_drift_not_gaps(self):
    writer = ClipWriter("wide")
    audio_writer = AudioWriter(writer.path)
    packet = np.ones(5, dtype=np.int16).tobytes()
    # Each 50 ms block arrives 1 ms late: 2% slow, 20 ms behind after 20 blocks.
    for i in range(20):
      audio_writer.add_packet(packet, sample_rate=100, log_mono_ns=i * 51_000_000)
    audio = audio_writer.finalize()
    assert audio is not None
    assert (audio.frame_count, audio.gap_count) == (100, 0)
    writer.abort()

  def test_audio_writer_measures_the_mic_clock_against_boot_time(self):
    writer = ClipWriter("wide")
    writer.add_frame(np.zeros((CLIP_HEIGHT, CLIP_WIDTH, 3), dtype=np.uint8), 0)
    audio_writer = AudioWriter(writer.path)
    packet = np.ones(50, dtype=np.int16).tobytes()
    # A "1000 Hz" mic that really delivers 1001 samples per boot-clock second.
    for i in range(100):
      audio_writer.add_packet(packet, sample_rate=1000, log_mono_ns=round(i * 50e9 / 1001))
    audio = audio_writer.finalize()
    assert audio is not None
    assert abs(audio.measured_sample_rate - 1001) < 0.1

    clip = writer.finalize(audio=audio)
    assert clip is not None
    assert load_clip(clip.path).audio_measured_sample_rate == audio.measured_sample_rate

  def test_audio_playback_sync_and_mute(self):
    writer = ClipWriter("wide", recording_start_mono_ns=1_000_000_000)
    writer.add_frame(np.zeros((CLIP_HEIGHT, CLIP_WIDTH, 3), dtype=np.uint8), 0)
    audio_writer = AudioWriter(writer.path)
    samples = np.arange(20, dtype=np.int16)
    audio_writer.add_packet(samples.tobytes(), sample_rate=10, log_mono_ns=1_200_000_000)
    audio = audio_writer.finalize()
    clip = writer.finalize(audio=audio)
    assert clip is not None

    player = ClipAudioPlayer(clip)
    player._samples = samples.reshape(-1, 1)
    player.sync(playhead_s=0.2, playing=True)
    output = np.zeros((4, 1), dtype=np.int16)
    player._callback(output, 4, None, None)
    np.testing.assert_array_equal(output[:, 0], samples[:4])

    player.set_muted(True)
    output.fill(-1)
    player._callback(output, 4, None, None)
    assert not output.any()
    player.set_muted(False)
    player._callback(output, 4, None, None)
    np.testing.assert_array_equal(output[:, 0], samples[8:12])
    player._samples = None

  def test_audio_playback_follows_the_measured_mic_clock(self):
    writer = ClipWriter("wide", recording_start_mono_ns=1_000_000_000)
    writer.add_frame(np.zeros((CLIP_HEIGHT, CLIP_WIDTH, 3), dtype=np.uint8), 0)
    audio_writer = AudioWriter(writer.path)
    samples = (np.arange(20) * 10).astype(np.int16)
    audio_writer.add_packet(samples.tobytes(), sample_rate=10, log_mono_ns=1_200_000_000)
    audio = audio_writer.finalize()
    clip = writer.finalize(audio=audio)
    assert clip is not None

    player = ClipAudioPlayer(replace(clip, audio_measured_sample_rate=11))
    player._samples = samples.reshape(-1, 1)
    player.sync(playhead_s=0.2, playing=True)
    output = np.zeros((5, 1), dtype=np.int16)
    player._callback(output, 5, None, None)

    np.testing.assert_array_equal(output[:, 0], [0, 11, 22, 33, 44])
    assert player._cursor == 5.5
    player._samples = None

  def test_hevc_writer_starts_at_keyframe_and_publishes_atomically(self):
    with TemporaryDirectory() as directory:
      path = Path(directory)
      writer = HevcWriter(path)
      writer.add_packet(b"", b"drop", False, 1344, 760)
      writer.add_packet(b"header", b"key", True, 1344, 760, timestamp_ns=1234)
      writer.add_packet(b"", b"delta", False, 1344, 760)
      assert not (path / "video.hevc").exists()
      master = writer.finalize()
      assert master is not None
      assert (master.width, master.height, master.frame_count) == (1344, 760, 2)
      assert master.first_timestamp_ns == 1234
      assert (path / "video.hevc").read_bytes() == b"headerkeydelta"

  def test_interrupted_take_recovers_only_fully_written_media(self):
    writer = ClipWriter("wide", recording_start_mono_ns=1_000_000_000)
    frame = np.zeros((CLIP_HEIGHT, CLIP_WIDTH, 3), dtype=np.uint8)
    writer.add_frame(frame, 0)
    writer.add_frame(frame, 50)

    hevc = HevcWriter(writer.path)
    hevc.add_packet(b"header", b"key", True, 1344, 760, 1_000_000_000)
    hevc.add_packet(b"", b"delta", False, 1344, 760, 1_050_000_000)

    audio_writer = AudioWriter(writer.path)
    audio_writer.add_packet(np.arange(10, dtype=np.int16).tobytes(), 100, 1_000_000_000)

    # Emulate a killed process and torn writes after the last journaled units.
    writer._close_files()
    hevc._close()
    audio_writer._file.close()
    frames_path = writer.path / "frames.bin"
    frames_path.write_bytes(frames_path.read_bytes()[:-1])
    with open(writer.path / "video.hevc.partial", "ab") as file:
      file.write(b"torn")
    with open(writer.path / "audio.s16le.partial", "ab") as file:
      file.write(b"\0")

    recovered = recover_interrupted_clips(writer.path.parent)

    assert len(recovered) == 1
    clip = recovered[0]
    assert clip.frame_count == 1
    assert clip.native_frame_count == 2
    assert clip.audio_frame_count == 10
    assert clip.audio_error == "recording was interrupted"
    assert (clip.path / "video.hevc").read_bytes() == b"headerkeydelta"
    assert (clip.path / "audio.s16le").stat().st_size == 20
    meta = json.loads((clip.path / "clip.json").read_text())
    assert meta["status"] == "ready" and meta["recovered"]

  def test_hevc_writer_counts_dropped_frames(self):
    with TemporaryDirectory() as directory:
      writer = HevcWriter(Path(directory))
      for timestamp_ms in (0, 50, 101, 250, 300, 450):
        writer.add_packet(b"header", b"frame", timestamp_ms == 0, 1344, 760, timestamp_ns=1_000_000_000 + timestamp_ms * 1_000_000)
      master = writer.finalize()
      assert master is not None
      assert (master.gap_count, master.dropped_frame_count) == (2, 4)

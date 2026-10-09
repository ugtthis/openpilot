import json
import struct
import zlib
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from zoneinfo import ZoneInfo

import numpy as np
from PIL import Image

from openpilot.common.test import OpenpilotTestCase
from openpilot.selfdrive.ui.mici.layouts.audio_playback import ClipAudioPlayer
from openpilot.system.camcorder.audio_track import recover_audio
from openpilot.system.camcorder.cameras import CABIN_CAMERA, CAMERAS, WIDE_ROAD_CAMERA
from openpilot.system.camcorder.clip_storage import (
  CLIP_ASPECT, CLIP_HEIGHT, CLIP_WIDTH, AudioWriter, Clip, ClipReader, ClipWriter, center_crop, delete_all_clips, delete_clip,
  clip_format_version, clips_root, extract_clip_rgb, format_timecode, list_clips, load_clip, preview_size,
  recover_interrupted_clips, scale_rgb, _clip_from_metadata, _clip_metadata,
)
from openpilot.system.camcorder.hevc_writer import HevcWriter, recover_hevc
from openpilot.system.camcorder.image import THUMB_HEIGHT, THUMB_WIDTH
from openpilot.system.camcorder.preview_track import publish_preview


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


def _load_metadata_payload(payload: dict) -> Clip | None:
  path = clips_root() / payload["id"]
  path.mkdir(parents=True)
  (path / "frames.bin").write_bytes(struct.pack("<I", 1) + b"\0")
  (path / "index.bin").write_bytes(struct.pack("<QI", 0, 0))
  (path / "clip.json").write_text(json.dumps(payload), encoding="utf-8")
  return load_clip(path)


class TestCamcorderClips(OpenpilotTestCase):
  def test_timeline_gap_ignores_one_frame_of_tolerance(self):
    clip = Clip("clip", Path("."), "wide", datetime(2026, 1, 1), 636, 360, 20, 200, 10.0,
                native_frame_count=200, video_start_mono_ns=1_000_000_000,
                audio_sample_rate=48_000, audio_measured_sample_rate=48_000,
                audio_frame_count=480_000, audio_start_mono_ns=1_000_000_000)

    assert not clip.has_timeline_gap
    assert not replace(clip, audio_gap_frame_count=2_400).has_timeline_gap
    assert replace(clip, audio_gap_frame_count=2_401).has_timeline_gap
    assert replace(clip, audio_frame_count=482_401).has_timeline_gap

  def test_any_dropped_video_frame_is_a_timeline_gap(self):
    clip = Clip("clip", Path("."), "wide", datetime(2026, 1, 1), 636, 360, 20, 200, 10.0,
                video_dropped_frame_count=1)

    assert clip.has_timeline_gap

  def test_format_timecode(self):
    assert format_timecode(0) == "0:00"
    assert format_timecode(12.9) == "0:12"
    assert format_timecode(75) == "1:15"
    assert format_timecode(3661) == "1:01:01"

  def test_time_and_date_labels_in_a_chosen_zone(self):
    pacific = ZoneInfo("America/Los_Angeles")
    # 4:39 UTC on Oct 6 is still the evening before in Pacific time.
    clip = Clip("clip", Path("."), "wide", datetime(2026, 10, 6, 4, 39, 15), 636, 360, 20, 1, 0.05)
    assert clip.time_label(pacific) == "9:39 PM PDT"
    assert clip.date_label(pacific) == "Oct 5"

  def test_time_label_follows_daylight_saving(self):
    pacific = ZoneInfo("America/Los_Angeles")
    winter = Clip("clip", Path("."), "wide", datetime(2026, 12, 1, 21, 5), 636, 360, 20, 1, 0.05)
    assert winter.time_label(pacific) == "1:05 PM PST"

  def test_labels_are_utc_without_a_zone(self):
    clip = Clip("clip", Path("."), "wide", datetime(2026, 10, 3, 0, 5), 636, 360, 20, 1, 0.05)
    assert clip.time_label(None) == "12:05 AM UTC"
    assert clip.date_label(None) == "Oct 3"

  def test_format_version_is_derived_from_clip_contents(self):
    clip = Clip("clip", Path("."), "wide", datetime(2026, 1, 1), 480, 360, 20, 1, 0.05)
    assert clip_format_version(clip) == 1
    assert clip_format_version(replace(clip, preview_contains_full_frame=True)) == 2
    assert clip_format_version(replace(clip, media_type="photo")) == 2
    assert clip_format_version(replace(clip, audio="audio.s16le")) == 4

  def test_clip_schema_round_trips_video_photo_and_recovered_clip(self):
    video = Clip(
      "video", Path("/clips/video"), "cabin", datetime(2026, 10, 3, 2, 0, 21),
      636, 360, 20, 1926, 96.332,
      preview_contains_full_frame=True, flip_h=True, codec="hevc", master="video.hevc",
      native_width=1344, native_height=760, native_frame_count=1935,
      recording_start_mono_ns=8618946790280, video_start_mono_ns=8618533497539,
      video_gap_count=1, video_dropped_frame_count=2,
      audio="audio.s16le", audio_sample_rate=48000, audio_measured_sample_rate=48001.25,
      audio_channels=2, audio_frame_count=4644810, audio_start_mono_ns=8618517964050,
      audio_timestamp="adc_start_boottime", audio_gap_count=3, audio_gap_frame_count=2400,
      audio_device_name="USB mic", audio_overflow_count=4, audio_error="reconnected",
    )
    photo = Clip(
      "photo", Path("/clips/photo"), "wide", datetime(2026, 10, 3, 20, 1, 21),
      1344, 760, 20, 1, 0.0, media_type="photo", preview_contains_full_frame=True,
    )
    recovered = replace(
      video, clip_id="recovered", path=Path("/clips/recovered"),
      recovered=True, recovery_error="recording was interrupted",
    )

    for clip in (video, photo, recovered):
      assert _clip_from_metadata(clip.path, _clip_metadata(clip, "ready")) == clip

  def test_loads_zlib_era_device_payload(self):
    payload = {
      "format_version": 1,
      "id": "2026-09-20--13-51-18",
      "status": "ready",
      "media_type": "video",
      "camera": "wide",
      "flip_h": False,
      "started_at": "2026-09-20T13:51:18",
      "width": 480,
      "height": 360,
      "fps": 20,
      "frame_count": 42,
      "duration_s": 2.1,
      "preview_contains_full_frame": False,
    }

    clip = _load_metadata_payload(payload)

    assert clip == Clip(
      "2026-09-20--13-51-18", clips_root() / payload["id"], "wide",
      datetime(2026, 9, 20, 13, 51, 18), 480, 360, 20, 42, 2.1,
    )
    assert _clip_metadata(clip, "ready") == payload

  def test_loads_jpeg_era_device_payload(self):
    payload = {
      "format_version": 4,
      "id": "2026-10-03--02-00-21",
      "status": "ready",
      "media_type": "video",
      "camera": "cabin",
      "flip_h": True,
      "started_at": "2026-10-03T02:00:21",
      "width": 636,
      "height": 360,
      "fps": 20,
      "frame_count": 1926,
      "duration_s": 96.332,
      "preview_contains_full_frame": True,
      "recording_start_mono_ns": 8618946790280,
      "codec": "hevc",
      "master": "video.hevc",
      "native_width": 1344,
      "native_height": 760,
      "native_frame_count": 1935,
      "video_start_mono_ns": 8618533497539,
      "video_gap_count": 0,
      "video_dropped_frame_count": 0,
      "audio": "audio.s16le",
      "audio_sample_rate": 48000,
      "audio_measured_sample_rate": 48001.27914617975,
      "audio_channels": 2,
      "audio_frame_count": 4644810,
      "audio_start_mono_ns": 8618517964050,
      "audio_timestamp": "adc_start_boottime",
      "audio_gap_count": 0,
      "audio_gap_frame_count": 0,
      "audio_device_name": "Wireless Mic Rx: USB Audio (hw:1,0)",
      "audio_overflow_count": 0,
      "audio_error": "",
    }

    clip = _load_metadata_payload(payload)

    assert clip is not None
    assert clip.camera == "cabin" and clip.flip_h
    assert (clip.codec, clip.master, clip.native_frame_count) == ("hevc", "video.hevc", 1935)
    assert (clip.audio_sample_rate, clip.audio_channels, clip.audio_frame_count) == (48000, 2, 4644810)
    assert clip.audio_measured_sample_rate == 48001.27914617975
    assert _clip_metadata(clip, "ready") == payload

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

  def test_cabin_enhancement_applies_the_driver_view_tone_curve(self):
    buf, stride, uv_offset = _make_nv12(256, 2)
    buf[:uv_offset].reshape(-1, stride)[:, :256] = np.arange(256, dtype=np.uint8)
    plain = extract_clip_rgb(buf, 256, 2, stride, uv_offset, out_w=256, out_h=2, crop_aspect=None)
    enhanced = extract_clip_rgb(buf, 256, 2, stride, uv_offset, out_w=256, out_h=2, crop_aspect=None, enhance=True)

    x = plain.astype(np.float32) * (1.0 / 255.0)
    x = np.clip((x + 0.15 - 0.5) * 0.88 + 0.5, 0.0, 1.0)
    x = x * x * (3.0 - 2.0 * x)
    np.testing.assert_array_equal(enhanced, (np.power(x, 0.8) * 255.0).astype(np.uint8))

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
    writer = ClipWriter(WIDE_ROAD_CAMERA)
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
    writer = ClipWriter(WIDE_ROAD_CAMERA)
    writer.add_frame(np.zeros((CLIP_HEIGHT, CLIP_WIDTH, 3), dtype=np.uint8), 0)
    clip = writer.finalize()
    assert clip is not None
    assert delete_clip(clip)
    assert not clip.path.exists()
    assert list_clips() == []

  def test_delete_all_clips_clears_the_library(self):
    for camera in CAMERAS:
      writer = ClipWriter(camera)
      writer.add_frame(np.zeros((CLIP_HEIGHT, CLIP_WIDTH, 3), dtype=np.uint8), 0)
      assert writer.finalize() is not None
    assert len(list_clips()) == 2
    assert delete_all_clips() == 2
    assert list_clips() == []

  def test_empty_writer_is_discarded(self):
    writer = ClipWriter(CABIN_CAMERA)
    path = writer.path
    assert writer.finalize() is None
    assert not path.exists()
    assert list_clips() == []

  def test_in_progress_clip_is_hidden(self):
    writer = ClipWriter(WIDE_ROAD_CAMERA)
    writer.add_frame(np.zeros((CLIP_HEIGHT, CLIP_WIDTH, 3), dtype=np.uint8), 0)
    assert list_clips() == []
    writer.abort()
    assert list_clips() == []

  def test_full_frame_preview_metadata_round_trip(self):
    writer = ClipWriter(WIDE_ROAD_CAMERA, 636, 360, preview_contains_full_frame=True)
    writer.add_frame(np.zeros((360, 636, 3), dtype=np.uint8), 0)
    clip = writer.finalize()
    assert clip is not None
    assert clip.has_full_frame_preview
    assert (clip.width, clip.height) == (636, 360)

  def test_first_frame_writes_a_center_cropped_thumbnail(self):
    writer = ClipWriter(WIDE_ROAD_CAMERA, 636, 360, preview_contains_full_frame=True)
    first = np.zeros((360, 636, 3), dtype=np.uint8)
    first[:, :78] = (255, 0, 0)
    first[:, 78:558] = (0, 255, 0)
    first[:, 558:] = (0, 0, 255)
    writer.add_frame(first, 0)
    writer.add_frame(np.zeros((360, 636, 3), dtype=np.uint8), 50)
    clip = writer.finalize()
    assert clip is not None
    with Image.open(clip.thumb_path) as image:
      assert image.size == (THUMB_WIDTH, THUMB_HEIGHT)
      thumb = np.asarray(image.convert("RGB")).astype(int)
    assert thumb[:, :, 1].min() > 200
    assert thumb[:, :, [0, 2]].max() < 60

  def test_photo_metadata_round_trip(self):
    writer = ClipWriter(WIDE_ROAD_CAMERA, media_type="photo")
    writer.add_frame(np.zeros((CLIP_HEIGHT, CLIP_WIDTH, 3), dtype=np.uint8), 0)
    photo = writer.finalize()
    assert photo is not None
    assert photo.is_photo
    assert photo.duration_s == 0.0
    with ClipReader(photo) as reader:
      assert reader.frame(0).shape == (CLIP_HEIGHT, CLIP_WIDTH, 3)

  def test_writer_uses_camera_names_and_flags(self):
    for camera in CAMERAS:
      writer = ClipWriter(camera)
      meta = json.loads((writer.path / "clip.json").read_text())
      assert (meta["camera"], meta["flip_h"]) == (camera.clip_name, camera.flip_h)
      writer.abort()

  def test_video_previews_are_compact_jpeg_while_photos_stay_lossless(self):
    rows = np.linspace(0, 255, CLIP_HEIGHT, dtype=np.uint8)[:, None]
    cols = np.linspace(0, 255, CLIP_WIDTH, dtype=np.uint8)[None, :]
    image = np.dstack([np.broadcast_to(rows, (CLIP_HEIGHT, CLIP_WIDTH)),
                       np.broadcast_to(cols, (CLIP_HEIGHT, CLIP_WIDTH)),
                       np.full((CLIP_HEIGHT, CLIP_WIDTH), 128, dtype=np.uint8)])
    media = {}
    for media_type in ("video", "photo"):
      writer = ClipWriter(WIDE_ROAD_CAMERA, media_type=media_type)
      writer.add_frame(image, 0)
      clip = writer.finalize()
      assert clip is not None
      with ClipReader(clip) as reader:
        media[media_type] = (reader.frame(0).astype(int), (clip.path / "frames.bin").read_bytes()[4:6])

    video, video_magic = media["video"]
    photo, photo_magic = media["photo"]
    assert video_magic == b"\xff\xd8"
    assert np.abs(video - image).mean() < 2
    assert photo_magic != b"\xff\xd8"
    np.testing.assert_array_equal(photo, image)

  def test_reader_still_plays_zlib_video_previews(self):
    image = np.full((CLIP_HEIGHT, CLIP_WIDTH, 3), 77, dtype=np.uint8)
    writer = ClipWriter(WIDE_ROAD_CAMERA)
    writer.add_frame(image, 0)
    clip = writer.finalize()
    assert clip is not None
    legacy = zlib.compress(image.tobytes(), 1)
    (clip.path / "frames.bin").write_bytes(struct.pack("<I", len(legacy)) + legacy)

    with ClipReader(clip) as reader:
      np.testing.assert_array_equal(reader.frame(0), image)

  def test_audio_metadata_round_trip(self):
    writer = ClipWriter(WIDE_ROAD_CAMERA, recording_start_mono_ns=1_000_000_000)
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
    writer = ClipWriter(WIDE_ROAD_CAMERA)
    audio_writer = AudioWriter(writer.path)
    audio_writer.add_packet(np.zeros(10, dtype=np.int16).tobytes(), 16000, 1)
    audio_writer.abort()
    assert not (writer.path / "audio.s16le").exists()
    assert not (writer.path / "audio.s16le.partial").exists()
    writer.abort()

  def test_audio_writer_fills_dropped_packets_with_silence(self):
    writer = ClipWriter(WIDE_ROAD_CAMERA)
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
    writer = ClipWriter(WIDE_ROAD_CAMERA)
    audio_writer = AudioWriter(writer.path)
    packet = np.ones(5, dtype=np.int16).tobytes()
    for log_mono_ns in (0, 52_000_000, 99_000_000, 151_000_000):
      audio_writer.add_packet(packet, sample_rate=100, log_mono_ns=log_mono_ns)
    audio = audio_writer.finalize()
    assert audio is not None
    assert audio.frame_count == 20
    writer.abort()

  def test_audio_writer_treats_slow_mic_clock_as_drift_not_gaps(self):
    writer = ClipWriter(WIDE_ROAD_CAMERA)
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
    writer = ClipWriter(WIDE_ROAD_CAMERA)
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
    writer = ClipWriter(WIDE_ROAD_CAMERA, recording_start_mono_ns=1_000_000_000)
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

  def test_audio_device_clock_sets_the_video_playhead(self):
    writer = ClipWriter(WIDE_ROAD_CAMERA, recording_start_mono_ns=1_000_000_000)
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
    np.testing.assert_array_equal(output[:, 0], [0, 10, 20, 30, 40])

    player._callback(output, 5, None, None)
    assert player._cursor == 10
    assert player.playhead_s == 0.2 + 5 / 11
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

  def test_hevc_master_keeps_only_frames_captured_by_the_end(self):
    with TemporaryDirectory() as directory:
      path = Path(directory)
      writer = HevcWriter(path)
      for timestamp_ms in (1000, 1050, 1100, 1150):
        writer.add_packet(b"H" if timestamp_ms == 1000 else b"", str(timestamp_ms).encode(),
                          timestamp_ms == 1000, 1344, 760, timestamp_ns=timestamp_ms * 1_000_000)
      master = writer.finalize(end_ns=1_120_000_000)
      assert master is not None
      assert master.frame_count == 3
      assert master.end_ns == 1_150_000_000
      assert (path / "video.hevc").read_bytes() == b"H100010501100"

  def test_hevc_timing_uses_the_selected_frame_rate(self):
    with TemporaryDirectory() as directory:
      writer = HevcWriter(Path(directory), fps=60)
      for timestamp_ns in (1_000_000_000, 1_016_666_667, 1_050_000_000):
        writer.add_packet(b"H", b"frame", timestamp_ns == 1_000_000_000,
                          2688, 1520, timestamp_ns=timestamp_ns)
      master = writer.finalize()
      assert master is not None
      assert master.fps == 60
      assert master.end_ns == 1_066_666_667
      assert (master.gap_count, master.dropped_frame_count) == (1, 1)

  def test_audio_ends_exactly_at_the_clip_end(self):
    with TemporaryDirectory() as directory:
      packet = np.ones(5, dtype=np.int16).tobytes()
      trimmed = AudioWriter(Path(directory))
      trimmed.add_packet(packet, sample_rate=100, log_mono_ns=0)
      trimmed.add_packet(packet, sample_rate=100, log_mono_ns=50_000_000)
      audio = trimmed.finalize(end_ns=70_000_000)
      assert audio is not None
      assert (audio.frame_count, audio.gap_count) == (7, 0)

      padded = AudioWriter(Path(directory))
      padded.add_packet(packet, sample_rate=100, log_mono_ns=0)
      audio = padded.finalize(end_ns=100_000_000)
      assert audio is not None
      assert (audio.frame_count, audio.gap_count, audio.gap_frame_count) == (10, 1, 5)

  def test_preview_keeps_only_frames_received_by_the_clip_end(self):
    writer = ClipWriter(WIDE_ROAD_CAMERA, recording_start_mono_ns=1_000_000_000)
    frame = np.zeros((CLIP_HEIGHT, CLIP_WIDTH, 3), dtype=np.uint8)
    for t_ms in (0, 50, 100, 150):
      writer.add_frame(frame, t_ms)
    clip = writer.finalize(end_ns=1_120_000_000)
    assert clip is not None
    assert clip.frame_count == 3
    with ClipReader(clip) as reader:
      assert reader.frame(2).shape == (CLIP_HEIGHT, CLIP_WIDTH, 3)

  def test_clip_metadata_records_capture_fps_and_bitrate(self):
    writer = ClipWriter(WIDE_ROAD_CAMERA, fps=60, bitrate=80_000_000)
    writer.add_frame(np.zeros((CLIP_HEIGHT, CLIP_WIDTH, 3), dtype=np.uint8), 0)
    clip = writer.finalize()
    assert clip is not None
    assert (clip.fps, clip.bitrate) == (60, 80_000_000)
    assert json.loads((clip.path / "clip.json").read_text())["bitrate"] == 80_000_000

  def test_all_journaled_tracks_recover_only_complete_units_at_every_boundary(self):
    with TemporaryDirectory() as directory:
      root = Path(directory)

      audio_data = np.arange(3, dtype=np.int16).tobytes()
      audio_info = json.dumps({
        "sample_rate": 100,
        "channels": 1,
        "first_log_mono_ns": 1,
      })
      for cut in range(len(audio_data) + 1):
        path = root / f"audio-{cut}"
        path.mkdir()
        (path / "audio.s16le.partial").write_bytes(audio_data[:cut])
        (path / "audio.info").write_text(audio_info)

        audio = recover_audio(path)

        expected = cut // 2
        assert (audio.frame_count if audio is not None else 0) == expected
        assert not (path / "audio.s16le.partial").exists()
        assert not (path / "audio.info").exists()
        if expected:
          assert (path / "audio.s16le").stat().st_size == expected * 2
        else:
          assert not (path / "audio.s16le").exists()

      hevc_data = b"aaabbb"
      hevc_index = struct.pack("<QQQQ", 3, 100, 6, 150)

      def recover_torn_hevc(name: str, data: bytes, index: bytes, expected: int) -> None:
        path = root / name
        path.mkdir()
        (path / "video.hevc.partial").write_bytes(data)
        (path / "video.index.partial").write_bytes(index)
        (path / "video.info").write_text('{"width": 10, "height": 8}')

        master = recover_hevc(path)

        assert (master.frame_count if master is not None else 0) == expected
        assert not (path / "video.hevc.partial").exists()
        assert not (path / "video.index.partial").exists()
        assert not (path / "video.info").exists()
        if expected:
          assert (path / "video.hevc").stat().st_size == expected * 3
        else:
          assert not (path / "video.hevc").exists()

      for cut in range(len(hevc_data) + 1):
        recover_torn_hevc(f"hevc-data-{cut}", hevc_data[:cut], hevc_index, int(cut >= 3) + int(cut >= 6))
      for cut in range(len(hevc_index) + 1):
        recover_torn_hevc(f"hevc-index-{cut}", hevc_data, hevc_index[:cut], min(2, cut // 16))

      preview_frames = struct.pack("<I", 3) + b"aaa" + struct.pack("<I", 3) + b"bbb"
      preview_index = struct.pack("<QIQI", 0, 0, 7, 50)

      def recover_torn_preview(name: str, frames: bytes, index: bytes, expected: int) -> None:
        path = root / name
        path.mkdir()
        (path / "frames.bin").write_bytes(frames)
        (path / "index.bin").write_bytes(index)

        recovered = publish_preview(path)

        assert (recovered[0] if recovered is not None else 0) == expected
        if expected:
          assert (path / "frames.bin").stat().st_size == expected * 7
          assert (path / "index.bin").stat().st_size == expected * 12

      for cut in range(len(preview_frames) + 1):
        recover_torn_preview(f"preview-data-{cut}", preview_frames[:cut], preview_index, int(cut >= 7) + int(cut >= 14))
      for cut in range(len(preview_index) + 1):
        recover_torn_preview(f"preview-index-{cut}", preview_frames, preview_index[:cut], min(2, cut // 12))

  def test_interrupted_take_recovers_only_fully_written_media(self):
    writer = ClipWriter(WIDE_ROAD_CAMERA, recording_start_mono_ns=1_000_000_000)
    frame = np.zeros((CLIP_HEIGHT, CLIP_WIDTH, 3), dtype=np.uint8)
    writer.add_frame(frame, 0)
    writer.add_frame(frame, 50)

    hevc = HevcWriter(writer.path)
    hevc.add_packet(b"header", b"key", True, 1344, 760, 1_000_000_000)
    hevc.add_packet(b"", b"delta", False, 1344, 760, 1_050_000_000)

    audio_writer = AudioWriter(writer.path)
    audio_writer.add_packet(np.arange(10, dtype=np.int16).tobytes(), 100, 1_000_000_000)
    audio_writer._sync._last_sync = 0.0
    audio_writer.add_packet(np.arange(10, dtype=np.int16).tobytes(), 100, 1_099_000_000)

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
    assert clip.audio_frame_count == 20
    assert clip.audio_measured_sample_rate > clip.audio_sample_rate
    assert clip.audio_error == "recording was interrupted"
    assert clip.recovered and clip.recovery_error == "recording was interrupted"
    assert (clip.path / "video.hevc").read_bytes() == b"headerkeydelta"
    assert (clip.path / "audio.s16le").stat().st_size == 40
    meta = json.loads((clip.path / "clip.json").read_text())
    assert meta["status"] == "ready" and meta["recovered"]

  def test_recovery_deletes_an_interrupted_take_with_no_video(self):
    writer = ClipWriter(WIDE_ROAD_CAMERA, recording_start_mono_ns=1_000_000_000)
    writer.add_frame(np.zeros((CLIP_HEIGHT, CLIP_WIDTH, 3), dtype=np.uint8), 0)
    writer._close_files()
    path = writer.path

    assert recover_interrupted_clips(writer.path.parent) == []
    assert not path.exists()

  def test_recovery_deletes_an_interrupted_photo(self):
    writer = ClipWriter(WIDE_ROAD_CAMERA, media_type="photo")
    writer.add_frame(np.zeros((CLIP_HEIGHT, CLIP_WIDTH, 3), dtype=np.uint8), 0)
    writer._close_files()
    path = writer.path

    assert recover_interrupted_clips(writer.path.parent) == []
    assert not path.exists()

  def test_hevc_writer_counts_dropped_frames(self):
    with TemporaryDirectory() as directory:
      writer = HevcWriter(Path(directory))
      for timestamp_ms in (0, 50, 101, 250, 300, 450):
        writer.add_packet(b"header", b"frame", timestamp_ms == 0, 1344, 760, timestamp_ns=1_000_000_000 + timestamp_ms * 1_000_000)
      master = writer.finalize()
      assert master is not None
      assert (master.gap_count, master.dropped_frame_count) == (2, 4)

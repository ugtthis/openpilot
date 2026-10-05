from openpilot.cereal.visionipc import VisionStreamType
from openpilot.system.camcorder.cameras import (
  CABIN_CAMERA, CAMERAS, WIDE_ROAD_CAMERA,
  camera_for_clip_name, camera_for_control_name, camera_for_stream,
)


def test_registry_covers_every_camcorder_stream_and_round_trips_names():
  assert {camera.stream_type for camera in CAMERAS} == {
    VisionStreamType.VISION_STREAM_WIDE_ROAD,
    VisionStreamType.VISION_STREAM_CABIN,
  }
  assert len({camera.control_name for camera in CAMERAS}) == len(CAMERAS)
  assert len({camera.clip_name for camera in CAMERAS}) == len(CAMERAS)

  for camera in CAMERAS:
    assert camera_for_stream(camera.stream_type) is camera
    assert camera_for_control_name(camera.control_name) is camera
    assert camera_for_clip_name(camera.clip_name) is camera


def test_camera_capture_behavior_matches_existing_outputs():
  assert (WIDE_ROAD_CAMERA.flip_h, WIDE_ROAD_CAMERA.enhance) == (False, False)
  assert (CABIN_CAMERA.flip_h, CABIN_CAMERA.enhance) == (True, True)

"""Camera-specific names and capture behavior for the camcorder."""

from dataclasses import dataclass

from openpilot.cereal.visionipc import VisionStreamType


@dataclass(frozen=True)
class Camera:
  stream_type: VisionStreamType
  control_name: str
  clip_name: str
  encode_service: str
  flip_h: bool
  enhance: bool


WIDE_ROAD_CAMERA = Camera(
  stream_type=VisionStreamType.VISION_STREAM_WIDE_ROAD,
  control_name="wideRoad",
  clip_name="wide",
  encode_service="wideRoadEncodeData",
  flip_h=False,
  enhance=False,
)
CABIN_CAMERA = Camera(
  stream_type=VisionStreamType.VISION_STREAM_CABIN,
  control_name="cabin",
  clip_name="cabin",
  encode_service="cabinEncodeData",
  flip_h=True,
  enhance=True,
)
CAMERAS = (WIDE_ROAD_CAMERA, CABIN_CAMERA)

_BY_STREAM = {camera.stream_type: camera for camera in CAMERAS}
_BY_CONTROL_NAME = {camera.control_name: camera for camera in CAMERAS}
_BY_CLIP_NAME = {camera.clip_name: camera for camera in CAMERAS}


def camera_for_stream(stream_type: VisionStreamType) -> Camera:
  return _BY_STREAM[stream_type]


def camera_for_control_name(name: str) -> Camera | None:
  return _BY_CONTROL_NAME.get(name)


def camera_for_clip_name(name: str) -> Camera:
  return _BY_CLIP_NAME[name]

"""User-chosen camcorder capture settings, persisted as params.

Unknown or missing values read as the stock defaults, so a bad param can never
change what the camcorder records into something nobody chose.
"""

from dataclasses import dataclass
from enum import StrEnum
from zoneinfo import ZoneInfo

from openpilot.common.params import Params

QUALITY_PARAM = "CamcorderQuality"
FRAME_RATE_PARAM = "CamcorderFrameRate"
RESOLUTION_PARAM = "CamcorderResolution"
ENCODER_MODE_PARAM = "CamcorderEncoderMode"
SENSOR_MODE_PARAM = "CamcorderSensorMode"
TIME_ZONE_PARAM = "CamcorderTimeZone"


class Quality(StrEnum):
  STOCK = "stock"  # the encoder settings openpilot uses for driving logs
  MAX = "max"


class Resolution(StrEnum):
  STOCK = "1344x760"
  FULL = "2688x1520"


FRAME_RATES = (20, 30, 60)

# Zones offered for clip labels, by IANA name. "" is UTC, which is also the unset value.
# Read with the OS time zone database, so no dependency and no system clock change.
TIME_ZONES = {
  "": "UTC",
  "America/Los_Angeles": "Pacific",
  "America/Denver": "Mountain",
  "America/Phoenix": "Arizona",
  "America/Chicago": "Central",
  "America/New_York": "Eastern",
  "America/Anchorage": "Alaska",
  "Pacific/Honolulu": "Hawaii",
  "Europe/London": "London",
  "Europe/Berlin": "Central Europe",
  "Asia/Kolkata": "India",
  "Asia/Tokyo": "Tokyo",
  "Australia/Sydney": "Sydney",
}


@dataclass(frozen=True)
class CamcorderSettings:
  quality: Quality = Quality.STOCK
  frame_rate: int = FRAME_RATES[0]
  time_zone: str = ""
  resolution: Resolution = Resolution.STOCK

  @property
  def sensor_mode(self) -> str:
    """camerad's startup-only mode; stock keeps the exact driving configuration."""
    if self.resolution == Resolution.STOCK and self.frame_rate == 20:
      return "stock"
    return f"{self.resolution}@{self.frame_rate}"

  @property
  def bitrate(self) -> int:
    """Match encoderd's pixel-rate scaling, capped at a tested hardware-safe request."""
    width, height = (1344, 760) if self.resolution == Resolution.STOCK else (2688, 1520)
    stock_pixels_per_second = 1344 * 760 * 20
    bitrate = 5_000_000 * width * height * self.frame_rate // stock_pixels_per_second
    if self.quality == Quality.MAX:
      bitrate *= 2
    return min(max(bitrate, 5_000_000), 80_000_000)

  @property
  def zone(self) -> ZoneInfo | None:
    return ZoneInfo(self.time_zone) if self.time_zone else None

  @classmethod
  def load(cls, params: Params | None = None) -> "CamcorderSettings":
    params = params or Params()
    quality = params.get(QUALITY_PARAM, return_default=True)
    frame_rate = params.get(FRAME_RATE_PARAM, return_default=True)
    resolution = params.get(RESOLUTION_PARAM, return_default=True)
    time_zone = params.get(TIME_ZONE_PARAM)
    return cls(
      quality=Quality(quality) if quality in tuple(Quality) else Quality.STOCK,
      frame_rate=frame_rate if frame_rate in FRAME_RATES else FRAME_RATES[0],
      time_zone=time_zone if time_zone in TIME_ZONES else "",
      resolution=Resolution(resolution) if resolution in tuple(Resolution) else Resolution.STOCK,
    )

  @property
  def encoder_mode(self) -> str:
    return f"{self.sensor_mode}:{self.quality}"

  @classmethod
  def applied(cls, params: Params | None = None) -> "CamcorderSettings":
    """The mode camerad/encoderd are actually running, as published by manager.

    Mirrors encoderd's whitelist: anything unrecognized is stock, which is what
    encoderd falls back to as well.
    """
    params = params or Params()
    mode = params.get(ENCODER_MODE_PARAM, return_default=True)
    for resolution in Resolution:
      for frame_rate in FRAME_RATES:
        for quality in Quality:
          candidate = cls(quality, frame_rate, resolution=resolution)
          if candidate.encoder_mode == mode:
            return candidate
    return cls()

  def save(self, params: Params | None = None) -> None:
    params = params or Params()
    params.put(QUALITY_PARAM, str(self.quality), block=True)
    params.put(FRAME_RATE_PARAM, self.frame_rate, block=True)
    params.put(RESOLUTION_PARAM, str(self.resolution), block=True)
    if self.time_zone:
      params.put(TIME_ZONE_PARAM, self.time_zone, block=True)
    else:
      params.remove(TIME_ZONE_PARAM)

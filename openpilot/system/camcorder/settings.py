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
TIME_ZONE_PARAM = "CamcorderTimeZone"


class Quality(StrEnum):
  STOCK = "stock"  # the encoder settings openpilot uses for driving logs
  MAX = "max"


FRAME_RATES = (20, 30)

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

  @property
  def zone(self) -> ZoneInfo | None:
    return ZoneInfo(self.time_zone) if self.time_zone else None

  @classmethod
  def load(cls, params: Params | None = None) -> "CamcorderSettings":
    params = params or Params()
    quality = params.get(QUALITY_PARAM, return_default=True)
    frame_rate = params.get(FRAME_RATE_PARAM, return_default=True)
    time_zone = params.get(TIME_ZONE_PARAM)
    return cls(
      quality=Quality(quality) if quality in tuple(Quality) else Quality.STOCK,
      frame_rate=frame_rate if frame_rate in FRAME_RATES else FRAME_RATES[0],
      time_zone=time_zone if time_zone in TIME_ZONES else "",
    )

  def save(self, params: Params | None = None) -> None:
    params = params or Params()
    params.put(QUALITY_PARAM, str(self.quality), block=True)
    params.put(FRAME_RATE_PARAM, self.frame_rate, block=True)
    if self.time_zone:
      params.put(TIME_ZONE_PARAM, self.time_zone, block=True)
    else:
      params.remove(TIME_ZONE_PARAM)

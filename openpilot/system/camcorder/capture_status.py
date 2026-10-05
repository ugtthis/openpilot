"""Typed capture failures and their on-wire camcorder notices."""

from dataclasses import dataclass
from enum import StrEnum


class CaptureFailure(StrEnum):
  NONE = "none"
  STORAGE = "storage"
  AUDIO = "audio"
  RECORDING = "recording"


_FAILURE_NOTICE = {
  (CaptureFailure.NONE, False): "recordingFailed",
  (CaptureFailure.NONE, True): "recordingErrorSaved",
  (CaptureFailure.STORAGE, False): "storageFull",
  (CaptureFailure.STORAGE, True): "storageFullSaved",
  (CaptureFailure.AUDIO, False): "recordingFailed",
  (CaptureFailure.AUDIO, True): "audioErrorSaved",
  (CaptureFailure.RECORDING, False): "recordingFailed",
  (CaptureFailure.RECORDING, True): "recordingErrorSaved",
}


@dataclass(frozen=True, slots=True)
class CaptureStatus:
  notice: str = "none"
  detail: str = ""
  failure: CaptureFailure = CaptureFailure.NONE
  clip_saved: bool = False

  @classmethod
  def from_failure(cls, failure: CaptureFailure, clip_saved: bool, detail: str) -> "CaptureStatus":
    return cls(_FAILURE_NOTICE[(failure, clip_saved)], detail, failure, clip_saved)

  @classmethod
  def warming(cls) -> "CaptureStatus":
    return cls(detail="recorder is still warming up")

  @classmethod
  def recovered(cls) -> "CaptureStatus":
    return cls(notice="recordingRecovered", clip_saved=True)

  @classmethod
  def timeline_gap(cls) -> "CaptureStatus":
    return cls(notice="timelineGapSaved", clip_saved=True)

  @classmethod
  def command_failed(cls, detail: str) -> "CaptureStatus":
    return cls(notice="recordingFailed", detail=detail, failure=CaptureFailure.RECORDING)

  def with_mic_error(self, mic_error: str, recording: bool) -> "CaptureStatus":
    if not mic_error or self.detail:
      return self
    notice = "micDisconnected" if recording else "micUnavailable"
    return CaptureStatus(notice, mic_error, self.failure, self.clip_saved)

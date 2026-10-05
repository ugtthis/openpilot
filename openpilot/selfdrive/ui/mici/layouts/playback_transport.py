"""Pure playback clock and transport controls for the camcorder player."""


class PlaybackTransport:
  def __init__(self):
    self.duration = 0.0
    self.photo = False
    self.playhead = 0.0
    self.playing = False
    self.muted = False
    self._origin = 0.0

  def reset_for_open(self) -> None:
    self.muted = False

  def reset_for_show(self, duration_s: float, photo: bool, now: float) -> None:
    self.duration = max(0.0, duration_s)
    self.photo = photo
    self.set_playhead(0.0, playing=not photo, now=now)

  def set_playhead(self, seconds: float, playing: bool | None, now: float) -> None:
    self.playhead = min(max(0.0, seconds), self.duration)
    self._origin = now - self.playhead
    if playing is not None:
      self.playing = playing
    elif self.at_end:
      self.playing = False

  def seek(self, seconds: float, now: float) -> None:
    self.set_playhead(seconds, playing=None, now=now)

  def toggle(self, now: float) -> tuple[float, bool] | None:
    """Toggle playback and return the exact state that audio must sync to.

    Restarting from the end intentionally syncs paused at zero before playback
    resumes, matching the original player ordering.
    """
    if self.photo:
      return None
    if self.at_end:
      self.seek(0.0, now)
      sync_state = (self.playhead, self.playing)
      self.playing = True
      return sync_state
    self.set_playhead(self.playhead, playing=not self.playing, now=now)
    return self.playhead, self.playing

  def tick(self, now: float, audio_playhead: float | None) -> bool:
    """Advance from the audio master clock or wall clock; return True on end-stop."""
    if not self.playing:
      return False
    if audio_playhead is not None:
      self.playhead = max(0.0, audio_playhead)
      self._origin = now - self.playhead
    else:
      self.playhead = now - self._origin
    if self.at_end:
      self.playhead = self.duration
      self.playing = False
      return True
    return False

  def stop(self) -> None:
    self.playing = False

  def toggle_mute(self) -> bool:
    self.muted = not self.muted
    return self.muted

  @property
  def at_end(self) -> bool:
    return self.duration > 0 and self.playhead >= self.duration

  @property
  def playhead_ms(self) -> int:
    return int(self.playhead * 1000)

  @property
  def progress(self) -> float:
    return 0.0 if self.duration <= 0 else min(1.0, self.playhead / self.duration)

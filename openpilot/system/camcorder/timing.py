import time


def boot_time_ns() -> int:
  """Clock used by camerad timestamps; monotonic is the portable test fallback."""
  clock = getattr(time, "CLOCK_BOOTTIME", None)
  return time.clock_gettime_ns(clock) if clock is not None else time.monotonic_ns()

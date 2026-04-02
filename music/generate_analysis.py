#!/usr/bin/env python3
"""
Pre-bake audio analysis for all MP3s in the music/ directory.

Run from the repo root:
  uv run python music/generate_analysis.py

Produces a <name>.analysis.npz alongside each <name>.mp3.
Commit the .npz files so the on-device light show loads them instantly
without needing ffmpeg or runtime FFT computation.
"""
import os
import sys
import numpy as np

from openpilot.common.basedir import BASEDIR
from openpilot.selfdrive.ui.mici.layouts.music_visualizer import AudioAnalysis

MUSIC_DIR = os.path.join(BASEDIR, "music")


def generate(mp3_path: str) -> None:
  out_path = mp3_path.replace(".mp3", ".analysis.npz")
  print(f"Analysing: {mp3_path}")

  a = AudioAnalysis(mp3_path)
  a.run()

  if not a.beats:
    print(f"  WARNING: no beats detected — check that ffmpeg is installed and the MP3 is readable.", file=sys.stderr)

  np.savez_compressed(
    out_path,
    beats=np.array(a.beats, dtype=np.float32),
    band_frames=a.band_frames if a.band_frames is not None else np.zeros((0, 16), dtype=np.float32),
    rms_frames=a.rms_frames if a.rms_frames is not None else np.zeros(0, dtype=np.float32),
    n_frames=np.array(a.n_frames),
    beat_drop_time=np.array(a.beat_drop_time, dtype=np.float32),
  )
  print(f"  Saved:  {out_path}")
  print(f"  beats={len(a.beats)}  beat_drop_time={a.beat_drop_time:.2f}s  n_frames={a.n_frames}")


if __name__ == "__main__":
  mp3s = sorted(f for f in os.listdir(MUSIC_DIR) if f.endswith(".mp3"))
  if not mp3s:
    print("No MP3 files found in music/", file=sys.stderr)
    sys.exit(1)

  for name in mp3s:
    generate(os.path.join(MUSIC_DIR, name))

  print(f"\nDone. Generated {len(mp3s)} .analysis.npz file(s).")
  print("Remember to commit all .npz files alongside their MP3s.")

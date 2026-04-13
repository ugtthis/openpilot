# Notification Demo

## What This Tool Does

- Runs an on-road notification playback demo against the Mici UI.
- Uses runtime `EVENTS` from `selfdrive/selfdrived/events.py` as the source of truth.

## Primary Commands

- Full demo (UI + publisher):
  - `./run_notifications.sh`
- Fast self-test only:
  - `./run_notifications.sh --self-test`
- Self-test with metric text path:
  - `./run_notifications.sh --self-test --demo-args "--metric"`
- Quick smoke playback (short run):
  - `./run_notifications.sh --demo-args "--limit 5 --warmup-seconds 1 --first-dwell-seconds 0.4 --dwell-seconds 0.4"`

## Useful Demo Args

- `--limit N` limit total variants
- `--start-at N` resume from 1-based index
- `--dwell-seconds X` per-alert hold time
- `--first-dwell-seconds X` first-alert hold time
- `--warmup-seconds X` initial warmup
- `--section-pause-seconds X` pause between event families
- `--metric` render metric-speed callback variants

Pass args via:
- `./run_notifications.sh --demo-args "--limit 10 --metric"`

## Interactive Keys

- `space` pause/resume playback
- `s` skip the current alert family

## Important Quirks

- `run_notifications.sh` auto-installs Python from `.python-version`; if exact patch is unavailable, it falls back to the minor version (for example `3.12.13 -> 3.12`).
- On macOS hosts, UI may log D-Bus/LTE errors from `wifi_manager`; these are expected and usually non-fatal for demo playback.
- Publisher startup races can happen on msgq topics; `notification_demo.py` has retry logic for transient `MultiplePublishersError`.
- Static screenshot background mode is enabled by default in the runner:
  - `UI_NOTIFICATION_DEMO_STATIC_BG=1`

## Why UI Files Were Changed

- `selfdrive/ui/mici/onroad/augmented_road_view.py`
  - Adds static screenshot background rendering when `UI_NOTIFICATION_DEMO_STATIC_BG=1`.
  - Uses state-specific backgrounds (`disengaged/engaged/override`) with center-crop to avoid stretch artifacts.
  - Keeps alert/HUD layers visible while removing live-model overlay noise in demo mode.

- `selfdrive/ui/mici/onroad/torque_bar.py`
  - In demo static-background mode, forces torque arc to use demo-published `carOutput` torque.
  - Avoids dependency on live lateral controller internals for deterministic visual output.

- Confidence ball behavior (same reason as torque arc)
  - `notification_demo.py` publishes synthetic `modelV2.meta.disengagePredictions` values.
  - This drives confidence-ball color/visibility even without live camera/model pipelines.
  - Needed so steering/takeover alerts show realistic confidence transitions during static screenshot playback.

## If Something Fails

- Run self-test first:
  - `./run_notifications.sh --self-test`
- If full demo exits early, rerun with a short smoke command and inspect the first exception.
- If UI process exits, demo exits by design when `--ui-pid` is monitored.

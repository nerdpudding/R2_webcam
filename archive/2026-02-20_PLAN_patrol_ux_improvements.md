# Plan: Patrol UX improvements

## Context

The patrol feature works but has two UX problems:
1. The dwell timer starts immediately when the goto command fires, not when the camera arrives. Users don't realize the countdown includes travel time. With short dwell times and far-apart positions, the camera may never fully arrive.
2. The status display ("Position: pos1 | Cycle: 1") is minimal — no countdown, no indication of what's happening.

The Foscam CGI has no "movement complete" callback, so we can't detect arrival. We work with what we have.

## Changes

### 1. Backend: add countdown data to patrol status (`nerdcam/patrol.py`)

In `_loop()`, track `dwell_remaining` and `dwell_total` alongside `current_pos`:
- Before the dwell sleep loop, set `_status["dwell_total"] = dwell`
- Inside the 100ms sleep loop, update `_status["dwell_remaining"] = max(0, dwell - elapsed)`
- In `get_status()`, return these two new fields

### 2. Frontend: live countdown in patrol status (`nerdcam_template.html`)

In `updatePatrolUI()`, change the status text from:
```
Position: pos1 | Cycle: 1
```
to:
```
pos1 — 7s / 10s | Cycle 1
```
This shows the countdown (remaining/total) so the user sees the timer ticking. Polled every 2 seconds (existing interval).

### 3. Frontend: help note in patrol config section (`nerdcam_template.html`)

Update the existing help text at the bottom of the config section to:
```
Set dwell to 0 to skip. Need 2+ with dwell > 0. Dwell includes travel time — use longer values for distant positions.
```

## Files to modify
- `nerdcam/patrol.py` — `_loop()` and `get_status()`
- `nerdcam_template.html` — `updatePatrolUI()` and config help text

## Verification
1. Start server, open viewer
2. Set pos1=15s, pos2=15s, pos3=0, pos4=0, repeat on
3. Start patrol, observe countdown ticking in status
4. Verify countdown resets when switching positions
5. Stop patrol, verify status hides

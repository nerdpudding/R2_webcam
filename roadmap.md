# NerdCam — Roadmap

## Sprint 1: Stream Latency & Sync (current priority)

**Goal:** Reduce A/V latency in the web viewer and improve stream reliability. Work in a separate dev branch.

- [x] Create dev branch for stream architecture work
- [ ] Investigate WebRTC / MSE / HLS for combined A/V in browser
- [ ] Prototype chosen approach — get synced A/V below 2s total latency
- [ ] Investigate RTSP keepalive to prevent camera's 275s session timeout
- [ ] Reduce stale detection threshold (5s → 2s) for faster recovery
- [ ] Test MJPEG reliability for NerdPudding (long-running stability, boundary parser compatibility)
- [ ] Verify `/api/mjpeg` remains unchanged after any architecture changes (NerdPudding contract)
- [ ] Fix PTZ preset parsing (only reads first preset from camera response)

## Sprint 2: Quality of Life

- [ ] Network drive recording location (configurable output path)
- [ ] Image preprocessing pipeline (lighting/contrast adjustments before streaming to NerdPudding — MJPEG quality directly impacts AI inference accuracy)
- [ ] Credential-free RTSP relay endpoint (future-proofing for when NerdPudding adds RTSP reconnect — H.264 gives better AI quality than MJPEG)
- [ ] PTZ patrol improvements (based on findings from Sprint 1 testing)
- [ ] Improve error messages and recovery feedback in web UI

## Sprint 3: Platform Expansion

- [ ] Raspberry Pi compatibility testing
- [ ] Software-only recording fallback when no NVIDIA GPU (already works with libx264/libx265, but test on Pi)
- [ ] Optimize resource usage for low-power hardware
- [ ] Consider alternative camera models / generic ONVIF support

## Status

| Sprint | Status | Notes |
|--------|--------|-------|
| 1 | In progress | Dev branch created, investigation and quick wins phase |
| 2 | Planned | After Sprint 1 fundamentals are solid |
| 3 | Future | Depends on Sprint 1-2 stability |

## Completed

- [x] Full CLI with all camera controls
- [x] Web viewer with live stream, PTZ, IR, image settings
- [x] Encrypted credential storage (PBKDF2)
- [x] Local recording with auto-detected NVENC/software encoding
- [x] Multi-GPU selection for recording
- [x] Patrol feature (server-side daemon)
- [x] OSD overlay control
- [x] Mic audio streaming
- [x] Stream auto-recovery (stale detection + restart)
- [x] UDP probesize fix for reliable stream startup
- [x] Comprehensive logging

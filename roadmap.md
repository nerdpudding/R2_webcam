# NerdCam — Roadmap

## Sprint 1: Stream Latency & Sync (current priority)

**Goal:** Reduce A/V latency in the web viewer and improve stream reliability. Work in a separate dev branch.

- [x] Create dev branch for stream architecture work
- [x] TCP as default RTSP transport — zero post-timeout restart failures
- [x] Reduce stale detection threshold (5s → 2s) for faster recovery (~4s total freeze)
- [x] Investigate RTSP keepalive — confirmed unfixable firmware bug (275s timeout hardcoded, end-of-life camera)
- [x] Investigate WebRTC / MSE / HLS — chose MSE/fMP4 for synced A/V in browser
- [x] Implement `/api/fmp4` endpoint — H.264 copy + AAC in fragmented MP4
- [x] Implement MSE browser engine — codec detection, buffer management, auto-reconnect, fallback
- [x] Hybrid streaming — MJPEG (mic off, ~1s) / MSE (mic on, ~3-3.5s synced A/V)
- [x] Fix PTZ preset parsing — correctly reads all preset positions (was only reading first)
- [x] Verify `/api/mjpeg` contract unchanged after architecture changes (NerdPudding verified)
- [x] Apply Gain button for mic gain (replaces debounced slider to prevent RTSP session exhaustion)
- [ ] PTZ preset Go buttons — save works but Go may not navigate correctly (needs investigation)

## Sprint 2: Quality of Life

- [ ] NerdPudding stream optimization — reduce NerdCam's latency contribution to the end-to-end pipeline (currently ~7-10s total: stream + AI inference + TTS + UI). Tune frame delivery, quality, and timing for both AI frame capture and web UI experience
- [ ] Image preprocessing pipeline (lighting/contrast adjustments before streaming to NerdPudding — MJPEG quality directly impacts AI inference accuracy)
- [ ] Credential-free RTSP relay endpoint (future-proofing for when NerdPudding adds RTSP reconnect — H.264 gives better AI quality than MJPEG)
- [ ] Network drive recording location (configurable output path)
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
| 1 | Nearly complete | All items done except PTZ preset Go buttons |
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
- [x] TCP default transport for reliable post-timeout recovery
- [x] MSE/fMP4 hybrid streaming (synced A/V in web viewer)
- [x] Comprehensive logging

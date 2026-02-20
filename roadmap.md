# NerdCam — Roadmap

## Sprint 1: Stream Latency & Sync

**Goal:** Reduce A/V latency in the web viewer and improve stream reliability.

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

## Sprint 2: Modular Refactor + Quality of Life

**Goal:** Refactor monolith into a proper Python package with SOLID modules. Fix remaining bugs and add small QoL improvements that naturally fit during the refactor. See `claude_plans/PLAN_sprint2_modular_refactor.md` for full plan.

- [ ] Refactor `nerdcam.py` → `nerdcam/` package (12 modules, AppState dataclass)
- [ ] PTZ preset Go buttons — fix name mismatch between save/goto CGI commands (moved from Sprint 1)
- [ ] Network drive recording location (configurable output path)
- [ ] PTZ patrol improvements (partial — obvious fixes during patrol.py extraction, rest to Sprint 3)
- [ ] Improve server error responses (structured JSON errors)
- [ ] Merge dev → main after refactor complete

## Sprint 3: Features + Platform

**Goal:** Build new features on the clean modular codebase.

- [ ] NerdPudding stream optimization — reduce latency contribution to end-to-end pipeline
- [ ] Image preprocessing pipeline (lighting/contrast adjustments before streaming to NerdPudding)
- [ ] Credential-free RTSP relay endpoint (future-proofing for NerdPudding RTSP input)
- [ ] PTZ patrol improvements
- [ ] Improve error messages and recovery feedback in web UI (frontend)
- [ ] Raspberry Pi compatibility testing
- [ ] Software-only recording fallback testing on low-power hardware
- [ ] Optimize resource usage for low-power hardware
- [ ] Consider alternative camera models / generic ONVIF support

## Status

| Sprint | Status | Notes |
|--------|--------|-------|
| 1 | Complete | All items done. PTZ Go bug moved to Sprint 2. |
| 2 | Active | Modular refactor as primary task |
| 3 | Planned | After Sprint 2 codebase is solid |

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

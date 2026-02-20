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

**Goal:** Refactor monolith into a proper Python package with SOLID modules. Fix remaining bugs and add small QoL improvements that naturally fit during the refactor. See `archive/2026-02-20_PLAN_sprint2_modular_refactor.md` for full plan.

- [x] Refactor `nerdcam.py` → `nerdcam/` package (12 modules, AppState dataclass)
- [ ] PTZ preset Go buttons — fix name mismatch between save/goto CGI commands (needs camera testing)
- [x] Network drive recording location (configurable `output_dir` in Recorder)
- [x] PTZ patrol improvements (partial — obvious fixes during patrol.py extraction, rest to Sprint 3)
- [x] Improve server error responses (structured JSON errors)
- [x] Auto time sync on startup (DST-aware)
- [x] Patrol UX overhaul: position indicators, progress bar, countdown, H:M:S time selects, mobile-friendly
- [x] Full regression test (manual — all major features verified)
- [x] Merge dev → main after refactor complete

## Sprint 3: WebRTC + Features

**Goal:** Replace fMP4/MSE with WebRTC for ultra-low latency synced A/V in browser. Build new features on the modular codebase.

### go2rtc integration (primary goal)
go2rtc solves three problems at once: WebRTC, RTSP relay, and 2-way audio.
- [ ] Evaluate go2rtc as RTSP → WebRTC bridge
  - Must support H.264 passthrough (no re-encoding) — critical for Pi 4
  - Test H.264 High profile (avc1.640028) compatibility in browsers
  - Single binary, no heavy dependencies, Docker optional
  - AlexxIT has reverse-engineered Foscam's proprietary 2-way audio protocol
- [ ] Replace fMP4/MSE path with WebRTC in web viewer
  - Target: <500ms synced A/V in browser (vs current ~3s)
  - MJPEG path stays for NerdPudding (needs JPEG frames, not video)
  - Fallback to fMP4/MSE if WebRTC fails (older browsers)
- [ ] Credential-free RTSP relay endpoint (via go2rtc)
  - Future-proofs NerdPudding RTSP input option
- [ ] 2-way audio — speaker control via go2rtc
  - Foscam uses proprietary protocol (HTTP POST with G.711a PCM chunks, not ONVIF Profile T)
  - go2rtc handles the protocol translation, exposes standard WebRTC backchannel
  - Enables browser-based talk-back without touching Foscam's audio API directly
- [ ] Watch concurrent RTSP session limit — go2rtc bridge adds a session

### Features
- [ ] NerdPudding stream optimization — reduce latency contribution to end-to-end pipeline
- [ ] Image preprocessing pipeline (lighting/contrast adjustments before streaming to NerdPudding)
- [ ] PTZ Go bug — fix preset name mismatch between save/goto CGI commands (needs camera testing)
- [ ] Improve error messages and recovery feedback in web UI (frontend)
- [x] Fix video settings CGI — required all params + streamType together, GOP/bitrate now adjustable
- [ ] Consider alternative camera models / generic ONVIF support

## Sprint 4: Raspberry Pi 4

**Goal:** Run NerdCam on Raspberry Pi 4 as a dedicated always-on appliance.

- [ ] Pi 4 compatibility testing (Python 3, ffmpeg, go2rtc/mediamtx)
- [ ] Verify H.264 passthrough works (no re-encoding needed)
- [ ] Software-only recording fallback (no NVENC on Pi)
- [ ] Optimize resource usage (memory, CPU, thermals)
- [ ] Systemd service for auto-start
- [ ] Consider separate lightweight Pi build or same codebase with Pi-specific config

## Status

| Sprint | Status | Notes |
|--------|--------|-------|
| 1 | Complete | All items done. PTZ Go bug moved to Sprint 2. |
| 2 | Complete | Refactor, code review, patrol UX, regression test done. Merged to main. PTZ Go bug moved to Sprint 3. |
| 3 | Planned | WebRTC (go2rtc/mediamtx), features, PTZ Go bug. |
| 4 | Planned | Raspberry Pi 4 dedicated appliance build |

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
- [x] File logging with toggle (default OFF, enable via Settings)
- [x] Modular Python package (`nerdcam/` — 12 modules, AppState dataclass)
- [x] Code review: thread safety, globals-to-AppState migration, dead code cleanup
- [x] Configurable recording output directory (network drive support)
- [x] Structured JSON error responses from proxy server
- [x] Auto time sync on startup (DST-aware)
- [x] Patrol UX overhaul: visual position indicators, progress bar, countdown display, H:M:S time selects (mobile-friendly)

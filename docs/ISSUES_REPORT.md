# NerdCam Issues Report

Date: 2026-02-20
Status: Most issues resolved or mitigated

---

## Issue 1: Audio/Video Sync in Web Viewer

**Status:** Resolved (2026-02-20) — MSE/fMP4 implementation
**Priority:** Was High, now resolved

### Symptom (before fix)
When mic was enabled in the web viewer, audio was ~5 seconds behind video. Video had ~1 second latency. Gap between them was ~4 seconds.

### Root cause
The web viewer used two completely independent streams:
- Video: MJPEG `<img>` tag — browser renders each JPEG frame immediately, ~1s latency
- Audio: `<Audio>` element with MP3 stream — browser buffers ~4-5 seconds before starting playback

These could not be synchronized because they were separate ffmpeg processes connecting to the camera's RTSP stream independently.

### Fix applied (2026-02-20)
Implemented a hybrid streaming approach:
- **Mic OFF:** MJPEG `<img>` for fastest video (~1s latency, no audio)
- **Mic ON:** MSE (MediaSource Extensions) with fragmented MP4 via new `/api/fmp4` endpoint — single stream with both H.264 video (copy) and AAC audio, inherently synchronized

New components:
- `/api/fmp4` endpoint — ffmpeg RTSP → fMP4 (H.264 copy + AAC 128k), `movflags frag_keyframe+empty_moov+default_base_moof`
- MSE JavaScript engine in web viewer — fetch streaming, buffer management, auto-reconnect, codec detection
- Automatic fallback to MJPEG if browser doesn't support MSE

### Trade-off
MSE mode has ~3-3.5s latency (both video and audio, synchronized) vs MJPEG's ~1s video-only. This is inherent to the fMP4/MSE pipeline. The hybrid approach lets the user choose: fast video (mic off) or synced A/V (mic on).

---

## Issue 2: Stream Freezes Every ~4:35

**Status:** Mitigated (2026-02-20) — cannot prevent timeout, but recovery is fast
**Priority:** Was Medium, now Low (acceptable with mitigations)

### Symptom
Video stream freezes completely every ~4 minutes 35 seconds (~275s). Total visible freeze was ~7 seconds (5s stale detection + ~2s restart).

### Root cause — confirmed firmware bug
The Foscam R2 (firmware 2.71.1.81, final version) has a hardcoded RTSP session timeout of ~275 seconds. The camera's RTSP server is non-compliant: it ignores standard keepalive requests (OPTIONS returns 501, GET_PARAMETER ignored). This is well-documented in VLC source code and NVR communities (Home Assistant, Frigate, ZoneMinder).

**Cannot be fixed:** No firmware update available (end-of-life since April 2022). No custom firmware exists for the Ambarella chipset. Camera CGI has no RTSP timeout setting.

### Mitigations applied (2026-02-20)
1. **TCP as default transport** — UDP post-timeout restarts failed 1-4 times (video-not-found race condition). TCP has zero failures, reliable first-attempt recovery.
2. **Stale threshold 5s → 2s** — detection fires faster. Total visible freeze reduced from ~7s to ~4s.
3. **MSE auto-reconnect** — the fMP4/MSE stream automatically reconnects after the timeout with a 3s delay.

### Current state
Total freeze: ~4 seconds every ~275s (2s stale detection + 2s restart). TCP recovery is reliable on first attempt. Acceptable for both web viewer and NerdPudding (which has its own 2s auto-reconnect).

---

## Issue 3: UDP probesize=32 — Unreliable Stream Startup

**Status:** Partially fixed (2026-02-19), fully mitigated by TCP default (2026-02-20)
**Priority:** Resolved

### Symptom (before fix)
On UDP transport, ffmpeg would sometimes (30-40% of attempts) fail to detect the video stream, outputting audio-only or failing entirely. After the camera's 4:35 timeout, the MJPEG source would need up to 4-5 consecutive retries before recovering — causing outages of up to 29 seconds.

### Root cause
`probesize=32` (32 bytes) was too small for ffmpeg to reliably identify the video stream during RTSP negotiation over UDP.

### Fix applied
1. (2026-02-19) Raised UDP `probesize` from `32` to `32768` (32KB) — reduced failure rate but did not eliminate it. Post-timeout UDP restarts still failed 1-4 times.
2. (2026-02-20) Changed default transport to TCP — TCP probesize (500KB) reliably detects both streams. Zero post-timeout failures in all testing.

### Current state
With TCP as default, this issue is fully resolved. UDP is still available as an option but not recommended due to the remaining race condition on post-timeout restarts.

---

## Issue 4: `/api/stream` Endpoint

**Status:** Removed (2026-02-20) — redundant, replaced by `/api/fmp4`

The MPEG-TS endpoint was superseded by `/api/fmp4` (fMP4) which serves the same purpose (synced A/V, H.264 copy + AAC) and works in all the same clients (VLC, ffplay, browser MSE). Removed to reduce unnecessary code.

---

## Latency Summary (updated 2026-02-20)

| Endpoint / Usage | Video latency | Audio latency | Synced? | Notes |
|---|---|---|---|---|
| Web viewer MJPEG (mic off) | ~1s | — | — | Fastest option, default mode |
| Web viewer MSE (mic on) | ~3-3.5s | ~3-3.5s | **Yes** | Hybrid: auto-switches when mic enabled |
| `/api/mjpeg` in VLC | ~2s | — | — | VLC adds ~1s buffer |
| `/api/mjpeg` in NerdPudding | ~7-10s end-to-end | — | — | See NerdPudding note below |
| `/api/fmp4` in VLC | ~3s | ~3s | **Yes** | Similar to browser MSE |

### NerdPudding end-to-end latency

The ~7-10s measured in NerdPudding is **not just stream latency**. It's the full pipeline from real-world action to on-screen result:

1. **NerdCam MJPEG stream** (~1s) — frame capture via OpenCV from `/api/mjpeg`
2. **AI video-to-text processing** — frame analysis timing, model inference
3. **TTS generation** — text-to-speech conversion
4. **NerdPudding UI rendering** — display update

Reducing NerdCam's stream contribution helps, but the majority of this latency sits in the NerdPudding pipeline (AI inference + TTS + UI). Optimizing this is a cross-project effort — both stream tuning on the NerdCam side (quality, latency, frame delivery timing) and processing pipeline tuning on the NerdPudding side. Hardware constraints (GPU for inference) are also a factor.

---

## What is Working Well (updated 2026-02-20)

- **Hybrid web viewer**: MJPEG for fast video (mic off), MSE/fMP4 for synced A/V (mic on)
- **MSE engine**: codec detection, fetch streaming, buffer management, auto-reconnect, fallback to MJPEG
- **MJPEG source**: shared single ffmpeg for all clients (efficient)
- **Stream recovery**: auto-restarts with 2s stale threshold, TCP first-attempt recovery
- **TCP default transport**: zero post-timeout restart failures
- TCP/UDP switchable at runtime without restart
- All PTZ, IR, image, recording, OSD controls working
- PTZ presets: correctly parses all preset positions (was broken, now fixed)
- Patrol feature: server-side daemon, survives browser close
- `/api/mjpeg` contract for NerdPudding: verified intact (correct headers, valid JPEG frames)
- Logging: all stream events, CGI commands, errors captured to `nerdcam.log`

---

## Known Remaining Issues

1. **PTZ preset Go buttons** — Save works, but Go buttons may not navigate to the correct position. Needs further investigation (name mismatch between save/goto CGI commands suspected).
2. **275s RTSP timeout** — unfixable firmware limitation. Mitigated but not eliminated.
3. **MSE latency ~3-3.5s** — inherent to fMP4/MSE pipeline. Acceptable trade-off for synced A/V.
4. **Video settings CGI** — Resolved (2026-02-20). `setVideoStreamParam` requires `streamType` + all parameters together. Fixed: reads current values first, applies override, sends complete parameter set. GOP and bitrate are now adjustable from the CLI.

---

## Foscam R2 V5 — Hardware Specifications (via ONVIF)

Queried via `tools/onvif_probe.py` using ONVIF `GetVideoEncoderConfigurationOptions`.
Firmware: 2.71.1.81, Hardware: 1.11.1.13.

### Main stream (prof0)
| Parameter | Min | Max | Current |
|---|---|---|---|
| Resolution | 320x240 | 1920x1080 | 1920x1080 |
| Bitrate | 20 kbps | **4096 kbps** | 4096 kbps (maximum) |
| Framerate | 1 fps | 30 fps | 20 fps (optimal) |
| GOP (keyframe interval) | 10 | 100 | 20 (= framerate, optimal) |
| H.264 profile | — | — | Main (ONVIF reports Baseline but camera sends High L4.0) |

### Sub stream (prof1)
| Parameter | Min | Max | Current |
|---|---|---|---|
| Resolution | 320x240 | 1280x720 | 1280x720 |
| Bitrate | 20 kbps | 4096 kbps | 512 kbps |
| Framerate | 1 fps | 15 fps | 11 fps |
| GOP | 10 | 100 | 15 |

### Supported resolutions
320x240, 640x360, 640x480, 1280x720, 1920x1080

### Optimal settings for best image quality

The camera has a fixed maximum bitrate of 4096 kbps. This budget must be distributed across all frames. The key trade-offs:

**GOP (keyframe interval):**
- I-frames (keyframes) are large and consume significant bandwidth
- Too low GOP (e.g. 10) = too many I-frames, starving P-frames of bandwidth → worse blocking artifacts between keyframes
- Too high GOP (e.g. 100) = long time between keyframes → trailing artifacts accumulate during motion, slow recovery after stream reconnect
- **Optimal: GOP = framerate** (1 keyframe per second). Balances quality and recovery time.

**Framerate:**
- Higher FPS = same 4 Mbps spread over more frames = less data per frame = more compression artifacts
- Lower FPS = more data per frame = sharper image quality
- **20 fps is the sweet spot** — visually smooth, each frame gets 20% more data than at 25 fps

**VBR (Variable Bit Rate):**
- Should be **on** (isVBR=1). Lets the camera allocate more bits during motion and save bits during still scenes.

**Recommended settings (main stream):**

| Parameter | Value | Reason |
|---|---|---|
| Resolution | 1920x1080 | Maximum supported |
| Bitrate | 4096 kbps (4194304 bps) | Hardware maximum |
| Framerate | 20 fps | More bits per frame than 25fps |
| GOP | 20 | = framerate, 1 keyframe/second |
| VBR | On | Efficient bandwidth allocation |

These settings are applied via the camera CGI (`setVideoStreamParam`) and persist on the camera — they don't need to be set on every app start. Adjustable via Advanced → Video settings in the CLI.

### CGI API note
The CGI API (`setVideoStreamParam`) expects bitrate in **bits per second** (e.g. `4194304` = 4096 kbps). The ONVIF API reports in **kbps**. The camera internally clamps values to the hardware maximum — it does not return an error for out-of-range values, it silently caps them.

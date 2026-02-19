# NerdCam Issues Report

Date: 2026-02-19
Status: Diagnosed, not all resolved

---

## Issue 1: Audio/Video Sync in Web Viewer

**Status:** Diagnosed — architectural limitation, not a bug
**Priority:** High (affects Use Case 1 — standalone app experience)

### Symptom
When mic is enabled in the web viewer, audio is ~5 seconds behind video. Video has ~1 second latency. Gap between them is ~4 seconds.

### Root cause
The web viewer uses two completely independent streams:
- Video: MJPEG `<img>` tag — browser renders each JPEG frame immediately, ~1s latency
- Audio: `<Audio>` element with MP3 stream — browser buffers ~4-5 seconds before starting playback

These cannot be synchronized because they are separate ffmpeg processes connecting to the camera's RTSP stream independently.

### What was ruled out
- Not caused by any code change — the browser's `<Audio>` buffering is inherent and has always been this way
- Not caused by TCP/UDP transport choice — tested both, same 5-second audio delay on both

### What "working this afternoon" actually was
The earlier session where A/V appeared synced was `/api/stream` in VLC (or similar), where both streams are handled by a single ffmpeg process. Both were ~3.5-4 seconds delayed but equally so, appearing in sync. The web viewer's MJPEG is much faster (~1s), which exposed the audio buffer delay.

### Solution options (not yet implemented)
Requires architectural change — a single combined A/V stream in the browser. Options:
- **WebRTC** — lowest latency, most complex
- **MediaSource Extensions (MSE)** with fragmented MP4 — moderate complexity
- **HLS with short segments** — simpler but adds segment latency

This is a future decision, not a quick fix.

---

## Issue 2: Stream Freezes Every ~4:35

**Status:** Diagnosed — camera RTSP timeout. Recovery implemented and working.
**Priority:** Medium (stream auto-recovers, but freeze is visible)

### Symptom
Video stream freezes completely every ~4 minutes 35 seconds. Browser shows static image with "LIVE" indicator still showing. Terminal shows WARNING about stale frames. Total visible freeze: ~7 seconds (5s stale detection threshold + ~2s restart).

### Root cause — confirmed with high confidence
The Foscam R2 camera has a fixed RTSP session timeout of approximately **275 seconds (~4:35)**. When this timeout fires, the camera silently drops the RTSP connection. ffmpeg's process stays alive but stops producing frames. Our stale frame detection fires 5 seconds later, kills ffmpeg, and restarts it.

### Evidence
Tested over 4 consecutive cycles. Interval between freezes: 4:38, 4:39, 4:40, 4:39. Frame count per session: 6863, 6896, 6918, 6903 — all at exactly 25fps right up to the hard cut. Next event was predicted to the second and occurred on schedule.

**Happens on both UDP and TCP transport** — ruling out packet loss as cause.

### Current state
Auto-recovery works reliably. ffmpeg restarts in ~2 seconds, but total visible freeze is ~7 seconds (includes 5s stale detection wait). Each resume connects clean on first try (after probesize fix — see Issue 3 below). Reducing stale threshold from 5s to 2s would cut total freeze to ~4s.

### Remaining options (not yet implemented)
- **Reduce stale detection from 5s to 2s** — recovery would be barely noticeable
- **RTSP keepalive** — send periodic OPTIONS requests to prevent timeout. ffmpeg may support this but needs investigation
- **Camera CGI setting** — check if the camera has a configurable RTSP session timeout

---

## Issue 3: UDP probesize=32 — Unreliable Stream Startup

**Status:** Fixed (2026-02-19)
**Priority:** Was High, now resolved

### Symptom (before fix)
On UDP transport, ffmpeg would sometimes (30-40% of attempts) fail to detect the video stream, outputting audio-only or failing entirely. After the camera's 4:35 timeout, the MJPEG source would need up to 4-5 consecutive retries before recovering — causing outages of up to 29 seconds. The `/api/stream` endpoint produced audio-only in VLC.

### Root cause
`probesize=32` (32 bytes) was too small for ffmpeg to reliably identify the video stream during RTSP negotiation over UDP. It was a race condition — sometimes the first 32 bytes contained the SDP video descriptor, sometimes only audio.

### Fix applied
Raised UDP `probesize` from `32` to `32768` (32KB) on all three ffmpeg endpoints:
- MJPEG source (`/api/mjpeg`)
- Audio stream (`/api/audio`)
- A/V stream (`/api/stream`)

TCP stays at `500000` (unchanged — needed for interleaved data).

### Verified
After fix: all endpoints connect on first attempt, no retries. `/api/stream` in VLC delivers video+audio reliably on first connect. MJPEG connects in 1-2 seconds consistently.

---

## Issue 4: `/api/stream` Latency (~3.5-4 seconds)

**Status:** Diagnosed — acceptable for its use case, not fixable without architectural change
**Priority:** Low

### Symptom
The combined A/V stream (`/api/stream`) has ~3.5-4 second latency for both video and audio in VLC. They are perfectly synced with each other.

### Root cause
MPEG-TS muxing overhead + VLC's own buffering. The stream itself is efficient (H.264 copy, no re-encode), but the MPEG-TS container and VLC's buffer add latency. ffmpeg's `-muxdelay 0 -muxpreload 0` flags are already applied.

### Notes
This endpoint is primarily for situations where A/V sync matters more than latency (e.g. reviewing a scene, casual monitoring). For real-time low-latency viewing, the web viewer's MJPEG is better. For NerdPudding, `/api/mjpeg` is the correct endpoint.

---

## Latency Summary

| Endpoint / Usage | Video latency | Audio latency | Synced? | Notes |
|---|---|---|---|---|
| Web viewer `<img>` | ~1s | — | — | Fastest option |
| Web viewer mic | ~1s video | ~5s audio | No (4s gap) | Architectural limit |
| `/api/mjpeg` in VLC | ~2s | — | — | VLC adds ~1s buffer |
| `/api/mjpeg` in NerdPudding | ~1-2s est. | — | — | Custom reader, less buffer than VLC |
| `/api/stream` in VLC | ~3.5-4s | ~3.5-4s | **Yes** | Best sync, most latency |

---

## What is Working Well

- Web viewer video: smooth 25fps, ~1s latency, reliable
- MJPEG source: shared single ffmpeg for all clients (efficient)
- Stream recovery: auto-restarts (ffmpeg restart ~2s, total visible freeze ~7s with 5s stale threshold)
- TCP/UDP switchable at runtime without restart
- All PTZ, IR, image, recording, OSD controls working
- Patrol feature: server-side daemon, survives browser close
- Logging: all stream events, CGI commands, errors captured to `nerdcam.log`

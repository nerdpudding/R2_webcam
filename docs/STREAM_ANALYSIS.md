# NerdCam Stream Architecture — Analysis Document

Date: 2026-02-19

## 1. Goals / Requirements

### Use Case 1: Standalone Camera Control App

The web viewer (`Open web viewer` option) is used as a standalone app to:
- View the live camera feed
- Control PTZ (pan/tilt, presets, patrol)
- Adjust settings (image, IR, OSD, recording, etc.)
- Listen to camera microphone audio
- Record video locally

**Requirements for Use Case 1:**
- Video + audio must be **perfectly synchronized** in the web viewer
- **Lowest latency possible** between real-world event and display
- **Highest quality** image
- **Smoothest** video (no choppiness/stuttering)
- **Reliable** connection (no freezes, auto-recovery if disrupted)
- Audio via mic is important — must be synced with video when enabled

### Use Case 2: RTSP Stream for AI Processing

Another app (using OpenCV) captures frames continuously from the camera stream for AI analysis.

**Requirements for Use Case 2 (confirmed with NerdPudding):**
- **Protocol:** HTTP MJPEG (`/api/mjpeg`) is the correct and optimal endpoint. NerdPudding has a purpose-built MJPEG reader with auto-reconnect (2s). RTSP via OpenCV is also supported but has no reconnect logic — a stream drop crashes it permanently until restart.
- **Smooth 25fps** — NerdPudding buffers every frame and serves them to its own browser clients via its own `/api/mjpeg` endpoint. If our proxy drops frames or is choppy, NerdPudding's display will be choppy.
- **AI inference:** Only ~2fps goes to the model (configurable `CAPTURE_FPS`). So frame quality matters more than frame rate for AI accuracy.
- **Audio:** Completely ignored. Not needed.
- **Reliability:** Critical. NerdPudding's MJPEG reader auto-reconnects in 2s, so brief drops are tolerated. But frequent drops are disruptive.
- **Quality:** MJPEG quality depends on our proxy's quality setting (1-10 slider). Higher = better image quality. RTSP/H.264 would be better quality but is currently not robust enough (no reconnect in NerdPudding).
- **NerdPudding's browser display** is served from NerdPudding's own internal frame buffer — it never connects to NerdCam directly. So Use Case 1 and Use Case 2 browser views are independent.

**Summary for Use Case 2:** `/api/mjpeg` at high quality (8-9/10), running smoothly at 25fps, with reliable auto-recovery from drops. That is all that is needed.

### Shared Requirement

Both use cases should be able to run at the same time without degrading each other.

---

## 2. Current Implementation

### Stream Endpoints

| Endpoint | Format | Source | Description |
|---|---|---|---|
| `/api/mjpeg` | HTTP MJPEG | Shared ffmpeg process | RTSP → re-encode to MJPEG. One ffmpeg for all clients. Used by web viewer `<img>` tag and previously intended as AI app input |
| `/api/stream` | HTTP MPEG-TS | Per-request ffmpeg | RTSP → H.264 copy + AAC audio → MPEG-TS. New ffmpeg per client. Used by VLC |
| `/api/audio` | HTTP MP3 | Per-request ffmpeg | RTSP → extract audio → MP3. New ffmpeg per client. Used by web viewer `<Audio>` element |
| `/api/snap` | Single JPEG | Camera CGI | One-shot snapshot from camera |
| Camera RTSP | RTSP H.264+audio | Direct from camera | `rtsp://user:pass@ip:88/videoMain` — native stream, requires credentials |

### Web Viewer Architecture

The web viewer displays video and audio using **two completely separate pipelines**:

```
VIDEO:  Camera → RTSP → [shared ffmpeg: H.264 → MJPEG] → reader thread → /api/mjpeg → browser <img> tag
AUDIO:  Camera → RTSP → [separate ffmpeg: audio → MP3]  → /api/audio   → browser <Audio> element
```

These are independent processes connecting to the camera's RTSP stream separately. They cannot be synchronized because they negotiate, buffer, and start at different times.

### MJPEG Shared Source

One ffmpeg process converts the camera's H.264 RTSP stream to MJPEG and pipes it to stdout. A reader thread extracts JPEG frames into a shared buffer. All `/api/mjpeg` clients read from this buffer. This is efficient (one ffmpeg for N clients) but **re-encodes** the video (H.264 → MJPEG), losing quality.

### ffmpeg Settings

All ffmpeg processes use these RTSP input settings:
- **UDP transport**: `-rtsp_transport udp` (configurable, can switch to TCP)
- **Probe size**: `-probesize 32768 -analyzeduration 0` (UDP, was `32` before fix — see Issue 3 in ISSUES_REPORT.md) or `-probesize 500000 -analyzeduration 500000` (TCP)
- **Low delay flags**: `-fflags +nobuffer+flush_packets -flags low_delay`

---

## 3. Current Issues

### Issue 1: Audio/Video Desync in Web Viewer

**Status:** Diagnosed — architectural limitation, not a regression (see ISSUES_REPORT.md Issue 1)
**Severity:** High
**Symptom:** ~5 second delay between video and audio in the web viewer when mic is enabled. Video is near-realtime (~1s), audio lags behind by ~5 seconds.
**Root cause:** The web viewer uses two independent streams: `<img>` MJPEG (renders immediately, ~1s latency) and `<Audio>` MP3 (browser buffers ~4-5s before playing). These are separate ffmpeg processes and can never be synchronized.
**Initial investigation (2026-02-19):** Originally appeared to be a regression introduced by JS audio refactoring (`_stopAudio`/`_startAudio`). Testing confirmed the JS change was not the cause — the browser's `<Audio>` buffering is inherent. The earlier "synced" experience was `/api/stream` in VLC, where a single ffmpeg process delays both tracks equally (~3.5-4s each).
**Resolution:** Requires architectural change — single combined A/V stream in browser (MSE/WebRTC/HLS). See Sprint 1 in roadmap.md.

### Issue 2: Stream Freezes Every ~275 Seconds

**Status:** Root cause confirmed — Foscam R2 RTSP session timeout (see ISSUES_REPORT.md Issue 2)
**Severity:** Medium (auto-recovery works but total visible freeze is ~7s)
**Symptom:** Video stream freezes completely every ~4 minutes 35 seconds. Happens on both UDP and TCP.
**Root cause:** Camera firmware has a fixed ~275s RTSP session timeout. Confirmed over 4 consecutive cycles with sub-second prediction accuracy (6863-6918 frames per session at exactly 25fps, then hard cut).
**Current mitigation:** Stale frame detection (5s threshold) kills and restarts ffmpeg. Restart takes ~2s. Total visible freeze: ~7s (5s stale wait + 2s restart).
**Initial investigation (2026-02-19):** Early log data showed "every 2-5 minutes" frequency. The 2-minute outliers were from the first session (shortened by earlier events). Later 30-minute idle testing confirmed the consistent ~275s pattern.
**Not yet tried:** RTSP keepalive, reducing stale threshold to 2s, camera CGI timeout setting.

### Issue 3: `/api/mjpeg` Re-encodes Video

**Status:** By design — acceptable tradeoff for current NerdPudding integration
**Severity:** Low (was Medium before NerdPudding requirements were confirmed)
**Symptom:** The MJPEG endpoint re-encodes H.264 → MJPEG, losing some quality and adding CPU load.
**Current understanding:** NerdPudding uses `/api/mjpeg` by design — its custom MJPEG reader with auto-reconnect is more robust than its RTSP/OpenCV path (which has no reconnect logic). MJPEG quality is controlled by the quality slider (1-10). Higher values = better AI inference accuracy.
**Future:** If NerdPudding adds proper RTSP reconnect (release + re-open VideoCapture on failure), H.264 would give better quality than MJPEG at the same bitrate. NerdCam would then need a credential-free RTSP relay endpoint. See Sprint 2 in roadmap.md.

### Issue 4: `/api/stream` Has Moderate Latency

**Status:** Diagnosed — acceptable for its use case
**Severity:** Low
**Symptom:** ~3.5-4 second total latency for both audio and video in VLC via `/api/stream`. Audio and video are perfectly synced with each other, but both are ~3.5-4 seconds behind real-time. (Early testing estimated ~5s; measured more precisely at ~3.5-4s after probesize fix.)
**Cause:** MPEG-TS muxing overhead + VLC's own buffering. The `-muxdelay 0 -muxpreload 0 -flush_packets 1` flags are already applied. VLC buffers independently and that is not configurable from the server side.

### Issue 5: Two Separate Stream Processes = No Sync

**Status:** Architecture limitation
**Severity:** High (for Use Case 1 with audio)
**Symptom:** Web viewer video and audio can never be perfectly synced because they come from two independent ffmpeg processes connecting to RTSP separately.
**Impact:** Even if the audio latency regression (Issue 1) is fixed, there will always be some drift between video and audio because they're independent pipelines.

### Issue 6: Camera RTSP Requires Credentials

**Status:** By design
**Severity:** Low (for Use Case 2)
**Symptom:** The AI app would need camera credentials to connect directly to RTSP. The proxy exists specifically to avoid exposing credentials. Currently the proxy only offers HTTP endpoints (MJPEG, MPEG-TS), not RTSP passthrough.
**Impact:** Use Case 2 either needs credentials in the AI app, or needs a credential-free RTSP relay.

---

## 4. Observations from Today's Testing

- **TCP transport works** after fixing probesize (was 32 bytes, too small for TCP interleaved data, now 500KB for TCP)
- **TCP vs UDP quality/latency:** User reports no noticeable difference in video smoothness or latency between TCP and UDP
- **Stream stalls happen on both TCP and UDP** — ruling out UDP packet loss as the sole cause
- **Auto-recovery works** — stale detection + ffmpeg restart takes ~2s (total visible freeze ~7s including 5s detection threshold)
- **VLC `/api/stream`:** Perfect A/V sync but ~3.5-4s total latency (measured after probesize fix)
- **VLC `/api/mjpeg`:** More latency than web viewer (VLC adds its own buffer on top)
- **Web viewer video:** Lowest latency of all options (~0.5s)
- **Patrol:** Works but had initial confusion with config save (need to click "Save Config" before "Start Patrol")
- **PTZ presets:** 4 presets saved, reported issue with camera remembering only 2 — needs investigation with better logging

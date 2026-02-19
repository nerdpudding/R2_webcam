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
- **Minimal probe**: `-probesize 32 -analyzeduration 0` (UDP) or `-probesize 500000 -analyzeduration 500000` (TCP)
- **Low delay flags**: `-fflags +nobuffer+flush_packets -flags low_delay`

---

## 3. Current Issues

### Issue 1: Audio Latency in Web Viewer (REGRESSION)

**Status:** Active, introduced during today's session
**Severity:** High
**Symptom:** ~5 second delay between video and audio in the web viewer when mic is enabled. Video is near-realtime, audio lags behind by ~5 seconds.
**Context:** This was working correctly earlier today (2026-02-19 afternoon) — audio and video were synced with minimal latency. Something changed during the session that introduced this regression.
**What changed:** The audio stop/start functions were refactored:
- Old: `_audioEl.pause(); _audioEl.src = ""; _audioEl = new Audio(url); _audioEl.play();`
- New: `_stopAudio()` (pause + removeAttribute("src") + load()) then `_startAudio()` (new Audio + play().catch())
- The old `src = ""` approach caused "Invalid URI" browser errors but the audio may have started faster due to different browser buffering behavior.
**Root cause:** Unknown — needs investigation. Possibly the `_audioEl.load()` call in `_stopAudio()` changes browser buffering behavior, or the `.catch()` on play() affects timing.

### Issue 2: Stream Freezes During Operation

**Status:** Partially mitigated with auto-recovery
**Severity:** Medium (was High before auto-recovery)
**Symptom:** Video stream freezes (shows static image). Occurs with both UDP and TCP transport. Browser still shows "LIVE" status. Audio (if playing) continues working.
**Frequency:** Every 2-5 minutes based on log data (both UDP and TCP)
**Mitigation:** Stale frame detection (5s timeout) now kills and restarts the ffmpeg process. Recovery takes 2-3 seconds. This works reliably.
**Root cause:** Unknown. Not UDP packet loss (also happens on TCP). Possible causes:
- Camera dropping RTSP connections under load
- ffmpeg RTSP client timeout behavior
- Camera resource contention when handling CGI commands + RTSP simultaneously
**Log evidence:** Stale restarts occur even when no PTZ or other commands are being sent (line 57-62 in today's log — stall at 19:24 with no user action since 19:19).

### Issue 3: `/api/mjpeg` Re-encodes Video

**Status:** By design, but problematic for Use Case 2
**Severity:** Medium
**Symptom:** The MJPEG endpoint re-encodes H.264 → MJPEG, losing quality and adding CPU load. For the AI app (Use Case 2), the requirement is RTSP with original H.264 quality.
**Impact:** The AI app currently cannot get the original quality stream through the proxy. It would need to connect directly to the camera's RTSP (requiring credentials) or a new endpoint/approach is needed.

### Issue 4: `/api/stream` Has High Latency

**Status:** Active
**Severity:** Medium
**Symptom:** ~5 second total latency for both audio and video in VLC via `/api/stream`. Audio and video are perfectly synced with each other, but both are ~5 seconds behind real-time.
**Cause:** MPEG-TS muxing overhead + VLC's own buffering. The `-muxdelay 0 -muxpreload 0` flags help but don't eliminate the latency. VLC also buffers its input.

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
- **Auto-recovery works** — stale detection + restart recovers the stream in 2-3 seconds
- **VLC `/api/stream`:** Perfect A/V sync but ~5s total latency
- **VLC `/api/mjpeg`:** More latency than web viewer (VLC adds its own buffer on top)
- **Web viewer video:** Lowest latency of all options (~0.5s)
- **Patrol:** Works but had initial confusion with config save (need to click "Save Config" before "Start Patrol")
- **PTZ presets:** 4 presets saved, reported issue with camera remembering only 2 — needs investigation with better logging

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

## 2. Current Implementation (updated 2026-02-20, post-Sprint 1)

### Stream Endpoints

| Endpoint | Format | Source | Description |
|---|---|---|---|
| `/api/mjpeg` | HTTP MJPEG | Shared ffmpeg process | RTSP → re-encode to MJPEG. One ffmpeg for all clients. Used by web viewer `<img>` (mic off) and NerdPudding |
| `/api/fmp4` | Fragmented MP4 | Per-request ffmpeg | RTSP → H.264 copy + AAC 128k → fMP4. Per-client ffmpeg. Used by web viewer MSE engine (mic on), VLC, ffplay |
| `/api/audio` | HTTP MP3 | Per-request ffmpeg | RTSP → extract audio → MP3. Legacy, superseded by MSE for synced A/V |
| `/api/snap` | Single JPEG | Camera CGI | One-shot snapshot from camera |
| Camera RTSP | RTSP H.264+audio | Direct from camera | `rtsp://user:pass@ip:88/videoMain` — native stream, requires credentials |

### Web Viewer Architecture (hybrid, post-Sprint 1)

The web viewer uses a **hybrid approach** that switches based on mic state:

```
MIC OFF (default):
  Camera → RTSP → [shared ffmpeg: H.264 → MJPEG] → reader thread → /api/mjpeg → browser <img> tag (~1s latency)

MIC ON (synced A/V):
  Camera → RTSP → [per-client ffmpeg: H.264 copy + AAC → fMP4] → /api/fmp4 → browser MSE <video> (~3-3.5s latency, synced)

FALLBACK (if browser doesn't support MSE):
  Camera → RTSP → [shared ffmpeg] → /api/mjpeg → <img> + [separate ffmpeg] → /api/audio → <Audio> (desynced)
```

The MSE engine handles codec detection, fetch streaming, buffer management, auto-reconnect on 275s timeout, and fallback to MJPEG.

### MJPEG Shared Source

One ffmpeg process converts the camera's H.264 RTSP stream to MJPEG and pipes it to stdout. A reader thread extracts JPEG frames into a shared buffer. All `/api/mjpeg` clients read from this buffer. This is efficient (one ffmpeg for N clients) but **re-encodes** the video (H.264 → MJPEG), losing quality.

### ffmpeg Settings

All ffmpeg processes use these RTSP input settings:
- **TCP transport (default)**: `-rtsp_transport tcp` — zero post-timeout restart failures. UDP available but not recommended.
- **Probe size**: `-probesize 500000 -analyzeduration 500000` (TCP) or `-probesize 32768 -analyzeduration 0` (UDP)
- **Low delay flags**: `-fflags +nobuffer+flush_packets -flags low_delay`

The `/api/fmp4` endpoint additionally uses:
- `-c:v copy -c:a aac -b:a 128k` — H.264 passthrough + AAC audio
- `-f mp4 -movflags frag_keyframe+empty_moov+default_base_moof` — fragmented MP4 for MSE
- `-frag_duration 500000 -min_frag_duration 250000` — ~500ms fragments for low latency

---

## 3. Current Issues (updated 2026-02-20, post-Sprint 1)

### Issue 1: Audio/Video Desync in Web Viewer

**Status:** Resolved (2026-02-20) — MSE/fMP4 hybrid implementation
**Severity:** Was High, now resolved
**Symptom (before fix):** ~5 second delay between video and audio in the web viewer when mic is enabled.
**Root cause:** The web viewer used two independent streams: `<img>` MJPEG (~1s) and `<Audio>` MP3 (~5s browser buffer). Separate ffmpeg processes, impossible to sync.
**Fix:** Hybrid approach — mic OFF uses MJPEG `<img>` (fast, ~1s), mic ON switches to MSE/fMP4 `<video>` (synced A/V, ~3-3.5s). Single ffmpeg process for fMP4 path ensures inherent sync. Browser fallback to old desynced path if MSE unsupported.

### Issue 2: Stream Freezes Every ~275 Seconds

**Status:** Mitigated (2026-02-20) — cannot prevent timeout, recovery is fast
**Severity:** Was Medium, now Low (acceptable)
**Symptom:** Video stream freezes completely every ~4 minutes 35 seconds. Happens on both UDP and TCP.
**Root cause:** Camera firmware (2.71.1.81, final version, end-of-life April 2022) has a hardcoded ~275s RTSP session timeout. OPTIONS returns 501, GET_PARAMETER ignored. No CGI setting exists. Confirmed unfixable through camera CGI queries, ffmpeg research, and online sources (VLC source code, Home Assistant/Frigate/ZoneMinder communities).
**Mitigations applied:** TCP as default (zero post-timeout restart failures), stale threshold 2s (was 5s), MSE auto-reconnect (3s delay). Total freeze: ~4s every ~275s.
**Investigation history (2026-02-19/20):** Confirmed over 4+ cycles. Early "2-5 minute" frequency was first session noise. RTSP keepalive investigated exhaustively — all approaches failed.

### Issue 3: `/api/mjpeg` Re-encodes Video

**Status:** By design — acceptable tradeoff for current NerdPudding integration
**Severity:** Low
**Symptom:** The MJPEG endpoint re-encodes H.264 → MJPEG, losing some quality and adding CPU load.
**Current understanding:** NerdPudding uses `/api/mjpeg` by design — its custom MJPEG reader with auto-reconnect is more robust than its RTSP/OpenCV path. Quality slider (1-10) controls inference accuracy.
**Future:** Sprint 3 — credential-free RTSP relay if NerdPudding adds reconnect logic.

### Issue 4: `/api/fmp4` Latency in VLC

**Status:** Diagnosed — VLC buffering dominates, acceptable
**Severity:** Low
**Symptom:** `/api/fmp4` shows ~3s latency in VLC with perfect A/V sync. (`/api/stream` MPEG-TS endpoint was removed as redundant — fMP4 has equal or better latency.)
**Cause:** fMP4 fragmentation settings allow lower latency than MPEG-TS did. VLC handles fMP4 well.
**Note:** In the browser via MSE, `/api/fmp4` achieves ~3-3.5s (MSE buffer management with periodic live-edge chasing).

### Issue 5: Two Separate Stream Processes = No Sync

**Status:** Resolved for MSE path (2026-02-20), remains for fallback path
**Severity:** Was High, now Low (only affects MSE-unsupported browsers)
**Symptom:** Web viewer video and audio were from two independent ffmpeg processes.
**Resolution:** MSE/fMP4 uses a single ffmpeg process with both H.264 and AAC — inherently synced. The old two-process architecture only remains as a fallback for browsers without MSE support.

### Issue 6: Camera RTSP Requires Credentials

**Status:** By design
**Severity:** Low (for Use Case 2)
**Symptom:** The AI app would need camera credentials to connect directly to RTSP. The proxy exists specifically to avoid exposing credentials.
**Future:** Sprint 3 — credential-free RTSP relay endpoint.

---

## 4. Testing History

### 2026-02-19 (initial investigation)
- TCP transport works after fixing probesize (was 32 bytes, now 500KB for TCP)
- TCP vs UDP: no noticeable difference in video smoothness or latency on LAN
- Stream stalls happen on both TCP and UDP — ruling out UDP packet loss
- VLC `/api/fmp4`: ~3s latency with perfect A/V sync
- VLC `/api/mjpeg`: ~2s latency (VLC adds its own buffer)
- Web viewer MJPEG: ~1s latency (fastest option)

### 2026-02-20 (Sprint 1 implementation)
- TCP as default: zero post-timeout restart failures (UDP: 1-4 failures per timeout)
- Stale threshold 2s: total freeze ~4s (was ~7s with 5s threshold)
- RTSP keepalive: exhaustively investigated, confirmed impossible (firmware bug)
- Camera firmware: 2.71.1.81, final version, end-of-life April 2022
- `/api/fmp4` in VLC: ~3s latency, A/V synced (same as browser MSE)
- `/api/fmp4` via MSE in browser: ~3-3.5s latency, A/V synced
- Camera H.264 profile: High L4.0 (avc1.640028), confirmed via ffprobe
- MSE aggressive per-chunk seeking: made things worse (choppy, no audio). Fixed with gentle periodic check every 3s.
- Hybrid approach works: MJPEG (mic off) ↔ MSE (mic on) switching is clean
- "453 Not Enough Bandwidth": triggered by too many concurrent RTSP sessions. Fixed with Apply Gain button instead of live slider.
- PTZ preset parsing: fixed, now reads all pointN keys (was only reading first). All 8 presets visible.
- PTZ preset Go buttons: still broken (save works, Go doesn't navigate correctly). Suspected name mismatch.
- NerdPudding `/api/mjpeg`: verified working (correct headers, valid JPEG frames, contract intact)
- NerdPudding end-to-end latency: ~7-10s (stream ~1s + AI inference + TTS + UI rendering)

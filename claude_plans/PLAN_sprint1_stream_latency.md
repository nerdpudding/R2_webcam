# Sprint 1: Stream Latency & Sync — Implementation Plan

## Context

NerdCam's web viewer has two fundamental streaming issues:
1. **A/V desync**: Video (~1s latency via MJPEG `<img>`) and audio (~5s latency via MP3 `<Audio>`) are separate streams that cannot be synchronized
2. **Periodic ~7s freeze**: Camera drops RTSP every ~275s, stale detection takes 5s + 2s restart

Both are architectural — they need code changes, not config tweaks. This plan covers all Sprint 1 work on the `dev` branch. `/api/mjpeg` is **never touched** (NerdPudding contract).

## Implementation Order

### Step 0: TCP as default transport (one-line quick win)

**File:** `nerdcam.py`

**Finding (2026-02-19):** Stream-debugger analysis revealed that the UDP probesize fix (32 → 32768) did not fully resolve post-timeout startup failures. After each 275s camera timeout, UDP restarts fail 1-4 times (ffmpeg detects only audio, no video) before succeeding — causing up to 20s blackout. TCP sessions had zero such failures in the same log.

| What | Change |
|------|--------|
| Default transport | `_rtsp_transport = "udp"` → `_rtsp_transport = "tcp"` |

**Effect:** Post-timeout recovery becomes reliable on first attempt. Existing configs keep their saved transport setting (loaded from encrypted config).

**Risk:** Low. On a LAN, TCP and UDP have identical streaming performance. TCP's larger probesize (500KB) adds ~0.5s to startup time but eliminates the video-not-found race condition.

---

### Step 1: Stale threshold 5s → 2s (one-line quick win)

**File:** `nerdcam.py`

| Line | Change |
|------|--------|
| 61 | `_MJPEG_STALE_SECONDS = 5` → `_MJPEG_STALE_SECONDS = 2` |
| 1312 | `no_frame_count >= 250` → `no_frame_count >= 100` (matches 2s at 20ms sleep) |
| 1313 | Update log divisor: `no_frame_count // 50` → `no_frame_count // 50` (keep as-is, it's just a log message) |

**Effect:** Total visible freeze drops from ~7s to ~4s.

**Test:** Start server, wait for 275s timeout, measure freeze with stopwatch. Check `nerdcam.log` for stale warning timing.

**Risk:** Low. At 25fps, 2s without a frame is definitively stale. If PTZ movement on UDP causes brief pauses, 3s is a safe fallback.

---

### Step 2: PTZ preset parsing fix (small bug fix)

**File:** `nerdcam.py`, lines 832-839

**Current:** `ptz_list_presets()` only reads `data.get("point0", "")` — misses all presets after the first.

**Fix:** Iterate all `pointN` keys:
```python
def ptz_list_presets(config):
    data = cgi("getPTZPresetPointList", config)
    if ok(data, "getPTZPresetPointList"):
        presets = []
        for key, val in sorted(data.items()):
            if key.startswith("point") and key[5:].isdigit() and val:
                presets.append(urllib.parse.unquote(val))
        if presets:
            print(f"  Presets: {', '.join(presets)}")
        else:
            print("  No presets saved")
```

**Test:** Save 3-4 presets via web viewer, run CLI PTZ → list presets, verify all show up.

---

### Step 3: RTSP keepalive investigation

**File:** `nerdcam.py`

This is research-first. Try in order, stop when one works:

**3a. Camera CGI** — Query `getRtspConfig`, `getStreamConfig` via raw CGI. If camera exposes an RTSP timeout setting, set it high. Zero code changes needed.

**3b. ffmpeg `-stimeout`** — Add `-stimeout 5000000` (5s socket timeout in microseconds) to all 3 RTSP input locations (lines ~100, ~1338, ~1509). Test if it prevents the 275s drop.

**3c. Python keepalive thread** — If 3a/3b fail, create a daemon thread sending RTSP OPTIONS every 60s. Note: may not work because it's out-of-band from ffmpeg's RTSP session. Worth trying.

**3d. Accept it** — If nothing prevents the camera timeout, the Step 1 reduction (4s freeze every 275s with auto-recovery) is acceptable. Combined with MSE reconnection logic in Step 5, the user experience is still good.

**Test:** Run server 10+ minutes, check `nerdcam.log` for stale warnings. Success = no warnings.

---

### Step 4: New `/api/fmp4` endpoint (server side)

**File:** `nerdcam.py`, insert after line 1543 (after `/api/stream` handler, before static file serving)

New endpoint: RTSP → fragmented MP4 (H.264 copy + AAC audio) piped to HTTP.

**ffmpeg command:**
```
ffmpeg -fflags +nobuffer+flush_packets+genpts -flags low_delay
  -probesize <32768|500000> -analyzeduration <0|500000>
  -rtsp_transport <udp|tcp>
  -i rtsp://...
  -c:v copy -c:a aac -b:a 128k -af volume=<gain>
  -f mp4 -movflags frag_keyframe+empty_moov+default_base_moof
  -frag_duration 500000 -min_frag_duration 250000
  -flush_packets 1 pipe:1
```

Key flags:
- `-c:v copy` — no video re-encoding (pass H.264 through)
- `empty_moov` — write codec init (moov atom) at stream start, not end
- `frag_keyframe` — new fragment at each keyframe
- `default_base_moof` — self-referencing fragments (MSE requirement)
- `frag_duration 500000` — ~500ms fragments for low latency

Handler pattern: identical to existing `/api/stream` handler (per-request ffmpeg, pipe stdout to wfile, kill on disconnect).

**Test before browser integration:**
```bash
curl -s http://localhost:8088/api/fmp4 --output test.mp4 &
sleep 5 && kill %1
ffprobe test.mp4   # should show H.264 video + AAC audio
vlc test.mp4       # should play with both tracks
```

**Risk:** Camera H.264 profile might have B-frames (unlikely for Foscam R2 at 1080p). If so, add `-bsf:v h264_mp4toannexb`. RTSP session count: this adds 1 session per browser client. Combined with shared MJPEG (for NerdPudding) = 2 sessions. Recording adds 3rd.

---

### Step 5: MSE browser implementation

**File:** `nerdcam_template.html`

**HTML changes (line 683):**
Add `<video id="camVideo" autoplay muted style="display:none">` alongside existing `<img>`.

**JavaScript (~200 lines new code, after line 404):**

MSE engine with these functions:
- `_mseSupported()` — check MediaSource API + codec support
- `_mseStart()` — create MediaSource, attach to `<video>`, call `_mseFetch()`
- `_mseFetch()` — `fetch("/api/fmp4")`, get ReadableStream reader
- `_mseReadChunk()` — read chunks from stream, queue for SourceBuffer
- `_mseProcessQueue()` — append chunks to SourceBuffer, clean old buffer (keep last 10s)
- `_mseReconnect()` — cleanup + restart after 3s on error/stream end
- `_mseStop()` / `_mseCleanup()` — teardown
- `_mseFallback()` — switch to MJPEG `<img>` path if MSE fails

**Modify existing functions:**
- `startView()` — try MSE first, fall back to MJPEG `<img>` if unsupported
- `stopView()` — stop MSE if active, restore `<img>` visibility
- `toggleAudio()` — when MSE active, toggle `<video>.muted` instead of separate `<Audio>` element
- `debounceMicGain()` — when MSE active, restart fMP4 stream (gain is server-side)

**Codec string:** Start with `avc1.4D401E` (Main L3.0, typical for IP cameras) + `mp4a.40.2` (AAC-LC). If addSourceBuffer throws, try Baseline and High profiles before falling back to MJPEG.

**Test:**
1. Open web viewer in Chrome — video plays via `<video>` with synced A/V
2. Enable Mic — unmutes `<video>`, audio is synced
3. Wait for 275s timeout — auto-reconnects
4. Test in Firefox — works or falls back cleanly
5. Latency target: < 2s total for both video and audio

---

### Step 6: Integration testing & NerdPudding verification

**Verification checklist:**
- [ ] `git diff` confirms `/api/mjpeg` handler (lines 1283-1318) and MJPEG source (lines 64-155) are untouched
- [ ] `curl http://localhost:8088/api/mjpeg` produces valid multipart/x-mixed-replace with `--ffmpeg` boundaries
- [ ] Web viewer works in Chrome (MSE) and Firefox (MSE or MJPEG fallback)
- [ ] A/V sync: < 2s latency, video and audio synced
- [ ] Stream recovers automatically from 275s camera timeout
- [ ] NerdPudding connects to `/api/mjpeg` and displays frames (if available for testing)
- [ ] Recording works alongside MSE + MJPEG (3 concurrent RTSP sessions)
- [ ] MJPEG fallback works when MSE is disabled

---

## Files Modified

| File | What changes |
|------|-------------|
| `nerdcam.py` | Default transport `"udp"` → `"tcp"` |
| `nerdcam.py` line 61 | Stale threshold 5 → 2 |
| `nerdcam.py` line 1312 | Client-side stale counter 250 → 100 |
| `nerdcam.py` lines 832-839 | PTZ preset parsing fix |
| `nerdcam.py` after line 1543 | New `/api/fmp4` handler (~40 lines) |
| `nerdcam.py` after line 62 | RTSP keepalive thread (~40 lines, if Step 3c) |
| `nerdcam_template.html` line 683 | Add `<video>` element |
| `nerdcam_template.html` JS section | MSE engine + modified startView/stopView/toggleAudio (~200 lines) |

**Not modified:** `/api/mjpeg` handler, MJPEG shared source, `/api/audio`, `/api/stream`, `/api/snap`.

## Architecture After Implementation

```
Browser (MSE path):     Camera → RTSP → ffmpeg (H.264 copy + AAC) → /api/fmp4 → <video> (synced A/V)
Browser (fallback):     Camera → RTSP → ffmpeg (H.264→MJPEG) → /api/mjpeg → <img> (video only)
                        Camera → RTSP → ffmpeg (audio→MP3) → /api/audio → <Audio> (desynced)
NerdPudding (unchanged): Camera → RTSP → [shared ffmpeg] → /api/mjpeg → NerdPudding reader
```

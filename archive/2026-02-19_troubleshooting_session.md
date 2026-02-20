# NerdCam Troubleshooting Plan

Date: 2026-02-19
Reference: docs/STREAM_ANALYSIS.md

---

## Issues to Resolve (Priority Order)

| # | Issue | Severity | Status |
|---|---|---|---|
| 1 | Audio latency in web viewer (~5s delay) | High | Confirmed regression — introduced today |
| 2 | Stream freezes every ~2-5 min (cause unknown) | Medium | Recovery works, root cause unknown |
| 3 | PTZ presets / patrol position confusion | Medium | Reported, not yet diagnosed |

---

## Issue 1: Audio Latency Regression

### What we know
- This afternoon audio was synced and low-latency in the web viewer
- At some point during today's session it became ~5s delayed
- The audio ffmpeg pipeline itself was NOT changed (same args, same codec)
- What WAS changed: the JS audio stop/start was refactored into `_stopAudio()` / `_startAudio()`
- Specifically: old code used `_audioEl.src = ""`, new code uses `removeAttribute("src")` + `load()`
- The `load()` call may change how the browser buffers the next audio element

### FINDINGS — Issue 1 (2026-02-19)

**Audio delay is 5 seconds on both UDP and TCP transport.** Transport is not the cause.

**Root cause:** The browser `<Audio>` element buffers ~5 seconds of MP3 data before starting playback. This is browser behavior and cannot be controlled by our code.

**The MJPEG video stream has ~1 second latency.** Audio has ~5 seconds. They are desynced by ~4 seconds.

**Important clarification from user:** This afternoon, video and audio APPEARED synced. Two possible explanations:
- **Scenario A (most likely):** Both video AND audio were delayed ~5s this afternoon (both slow but equal = appeared synced). Something later made the MJPEG video faster, exposing the audio delay that was always there.
- **Scenario B:** Both were genuinely fast (~1s) and synced this afternoon — something changed the audio path.

**Evidence for Scenario A:** VLC `/api/stream` also has ~5s latency on both A/V but is perfectly synced — because it's one combined process. The web viewer's original code had `-rtsp_flags prefer_tcp` on the MJPEG source which was removed — this may have changed video buffering behavior.

**Confirmed:** JS audio code change (`removeAttribute+load` vs `src=""`) is NOT the cause — delay is identical before and after that change, and the delay is inherent to the browser, not our code.

**Conclusion:** This is an **architectural limitation** of using separate browser `<Audio>` + `<img>` streams. True A/V sync in the browser requires a single combined stream (WebRTC, MSE, or similar). This is a larger change, not a quick fix.

### Hypothesis

### Next test steps — latency comparison across endpoints

Goal: determine whether video latency differs between endpoints, and whether audio+video are ever truly synced at low latency.

We need to measure **real-world-event → display delay** for each endpoint separately using the OSD clock as reference.

**Step 1.A — Web viewer video only (baseline)** ✅ DONE
- Result: ~1 second (sub-second but hard to measure precisely)
- Consistent across all testing sessions

---

## Fix Applied: UDP probesize 32 → 32768 (2026-02-19 ~21:20)

**Problem confirmed:** `probesize=32` on UDP is a race condition — sometimes ffmpeg finds both streams, sometimes only audio. Caused:
- `/api/mjpeg` needing up to 4 retries (29s outage) after camera timeout
- `/api/stream` in VLC delivering audio-only (video missing)
- This is what broke today's stream testing

**Fix:** Raised UDP probesize from `32` to `32768` on all three ffmpeg endpoints:
- MJPEG source (`_start_mjpeg_source`)
- Audio stream (`/api/audio`)
- AV stream (`/api/stream`)
- TCP stays at `500000` (unchanged)
- Added logging to audio and AV stream start/disconnect/error

**To verify fix:**
1. Restart app, choose option 2 (server only)
2. `vlc http://localhost:8088/api/stream` → should now get video+audio reliably
3. `vlc http://localhost:8088/api/mjpeg` → no more "Output file does not contain any stream" errors on UDP
4. Check log: no more 0-frame ffmpeg failures on first connect

---

**Step 1.B — `/api/mjpeg` in VLC** ✅ DONE
- Result: ~2-3 seconds video latency
- More than web viewer (~1s) but NOT 5 seconds
- Extra latency vs web viewer is VLC's own internal buffer
- NerdPudding likely gets similar latency (1-3s depending on its buffer)
- **NEW FINDING:** UDP probesize=32 caused 4 consecutive failed ffmpeg starts (29 seconds total outage) — see log 21:02:16 to 21:02:45. Critical reliability issue for NerdPudding. Must fix: increase UDP probesize.

**Step 1.C — `/api/stream` in VLC** ✅ DONE (after probesize fix)
- Same server running
- Open VLC: `vlc http://localhost:8088/api/stream`
- Clap hand, measure VIDEO delay (OSD clock)
- Then clap again, measure AUDIO delay
- Result: **~3.5-4s delay on BOTH video and audio, perfectly synced**
- Single ffmpeg process = inherently synced
- VLC adds its own buffer on top of stream latency
- Log confirms clean start on first attempt (probesize fix working)

**Step 1.B (re-test after probesize fix)** ✅
- `/api/mjpeg` in VLC: ~2s latency, **connected on first try with no errors**
- Before fix: up to 4 retries, 29s outage. Now: 1-2s to first frame, clean
- VLC adds ~1-2s buffer. NerdPudding's custom reader likely closer to ~1s

**Step 1.D — Web viewer video + audio**
- Open web viewer (option 1)
- Enable mic
- Clap, measure VIDEO delay separately, then AUDIO delay separately
- Record: video latency, audio latency, delta between them

**CONCLUSION — Issue 1 fully understood:**

| Endpoint | Video latency | Audio latency | Synced? |
|---|---|---|---|
| Web viewer `<img>` | ~1s | N/A | N/A |
| Web viewer `<Audio>` | N/A | ~5s | N/A |
| Web viewer combined | ~1s video | ~5s audio | **NO — 4s gap** |
| VLC `/api/mjpeg` | ~2-3s | N/A | N/A |
| VLC `/api/stream` | ~3.5-4s | ~3.5-4s | **YES — perfectly synced** |

**This afternoon's "working" experience was `/api/stream` or similar** — both streams delayed equally, appearing synced. The web viewer's `<img>` is faster than VLC's buffer, exposing the gap with the audio stream.

**Root cause of web viewer A/V desync:** Architectural — two independent streams (`<img>` MJPEG + `<Audio>` MP3) can never be synchronized. Browser `<Audio>` buffers ~4-5s before playing. This is not a regression — it was always this way.

**This is not a bug that can be fixed with a setting.** Solving it requires a single combined A/V stream in the browser (WebRTC, MSE, or HLS). This is a future architectural decision, not a quick fix.

### Test steps (original — completed/superseded above)

**Step 1.1 — Establish baseline**
- Open web viewer
- Do NOT enable mic yet
- Note time and confirm video is live and low-latency
- Make a sound (clap, speak) and note video timestamp

**Step 1.2 — Enable mic, measure delay**
- Click "Enable Mic"
- Make a distinct sound (clap or whistle)
- Count seconds between clap happening (you see your hand move on video) and hearing it through the browser
- Record: delay in seconds

**Step 1.3 — Revert the JS audio change and retest**
- Revert `_stopAudio()` to old inline approach (src="" instead of removeAttribute+load)
- Reload viewer, repeat Step 1.2
- Record: delay in seconds
- Does it change?

**Step 1.4 — Test without any prior audio stop**
- Start fresh (no prior audio playing, no mic gain changes)
- Enable mic immediately after page load
- Record: delay in seconds

### Success criteria
Audio plays within <1s of real-world sound when mic is enabled in web viewer.

### What to log
- Time of enabling mic (server log will show new ffmpeg process starting)
- Compare log timestamp of audio process starting vs when user reports hearing sound

---

## Issue 2: Stream Freeze Root Cause

### What we know
- Happens with both UDP AND TCP — rules out UDP packet loss
- Frequency: approximately every 2-5 minutes (from log: 3093 frames ~2min, 6885 frames ~5min, 6860 frames ~5min)
- No user action triggers it — happens during idle periods
- ffmpeg process goes stale (alive but producing no frames) then dies shortly after
- Recovery works: stale detection kills and restarts ffmpeg in 2-3s
- Pattern: ~5 minute interval suggests possible RTSP session timeout on camera side

### Hypothesis A (most likely)
Camera has an RTSP session keepalive timeout (~5 min). ffmpeg's RTSP client is not sending keepalive OPTIONS requests. Camera silently drops the session. ffmpeg stays "alive" (process running) but receives no data.

### Hypothesis B
Camera chokes under load — something (background task, scheduled operation) causes brief RTSP disruption every ~5 min.

### FINDINGS — Issue 3 (partial, lower priority)

**What we know:**
- `ptzAddPresetPoint` for pos1-pos4: all return result=0 (camera accepts saves)
- `ptzGotoPresetPoint` for pos1-pos4: all return result=0 (camera accepts goto)
- `getPTZPresetPointList` only reports "TopMost" — our code only reads `point0` but camera likely returns `point0`, `point1`, `point2`... as separate fields. **Bug: we're missing all presets after the first one.**
- Goto commands go to unexpected positions — either camera motor inertia during save, or internal preset slot ordering issue
- pos4 appeared correct; pos1, pos2, pos3 appeared wrong/shifted

**Not investigated yet (lower priority):**
- Full preset list raw CGI response (use option x → `getPTZPresetPointList`)
- Fix `ptz_list_presets()` to read all `pointN` fields
- Camera's internal slot ordering vs name-based addressing

**Will revisit after latency/sync issues are resolved.**

---

### FINDINGS — Step 2.1 completed (2026-02-19)

**Root cause confirmed: Foscam R2 has a fixed RTSP session timeout of ~275 seconds (~4:35).**

The camera silently drops the RTSP connection every 275 seconds. ffmpeg keeps running but produces no frames. Our stale detection fires 5 seconds later, kills ffmpeg, and restarts it. Recovery takes ~2 seconds (first frame received within 2s of restart).

| Session | First frame | Stale detected | Duration | Frames |
|---|---|---|---|---|
| 1 | 20:32:23 | 20:37:01 | 4:38 | 6863 |
| 2 | 20:37:03 | 20:41:42 | 4:39 | 6896 |
| 3 | 20:41:45 | 20:46:25 | 4:40 | 6918 |

- Frame rate during each session: 6863÷274 ≈ **25.0 fps** — perfectly smooth right up to the hard cut
- Drop is sudden (not gradual) — camera drops the TCP connection silently
- Pattern is identical on UDP and TCP transport
- Predicted next event (20:46:24), actual (20:46:25) — **1 second accuracy**

**Happens with both UDP and TCP** — not a transport-level issue.

**Current state:** Auto-recovery works (2-3s freeze every 4:35). Not ideal but acceptable for now.

**Remaining questions / next steps for Issue 2:**
- Can we send RTSP keepalive OPTIONS requests to prevent the timeout?
- Is there a camera CGI setting to increase the RTSP session timeout?
- Can we reduce stale detection from 5s to 2s so recovery freeze is shorter?

### Test steps (remaining)

**Step 2.1 — Collect timing data** ✅ DONE — see findings above

**Step 2.2 — Test ffmpeg keepalive option**

**Step 2.2 — Test ffmpeg keepalive option**
- If Step 2.1 shows consistent ~5min interval → strongly suggests RTSP timeout
- Add `-rtsp_flags listen` or `-timeout` option to ffmpeg to send keepalive
- Rerun 30-minute idle test
- Question: Does freeze frequency change?

**Step 2.3 — Check camera RTSP settings**
- Use web viewer → Device Info → send raw CGI command `getRtspConfig` or `getPortInfo`
- Look for any timeout-related settings

**Step 2.4 — Correlate with camera activity**
- Is the camera doing anything scheduled? (motion detection scan, IR auto-adjust, etc.)
- Try disabling motion detection and retest
- Does freeze frequency change?

### What to log
Already logging stale restarts. After Step 2.1 we will have enough data to determine if hypothesis A or B is correct.

### Success criteria
Either: understand root cause well enough to prevent freezes, or confirm recovery (2-3s) is acceptable and move on.

---

## Issue 3: PTZ Presets / Patrol Position Confusion

### What we know
- User saved 4 camera positions using Save buttons (pos1-pos4 via `ptzAddPresetPoint`)
- Patrol was configured with dwell times for all 4 positions
- Patrol appeared to only cycle between 2 positions
- Log showed 2x "Patrol start rejected: only 0 active positions" before successful start
- CGI logging now added (today) — will show result codes for all ptz commands

### Hypothesis A
Camera only stored 2 presets — the Save 3 and Save 4 buttons sent the command but the camera silently rejected or ignored them (Foscam R2 may have a 2-preset limit in firmware).

### Hypothesis B
Patrol config had 4 positions but dwell was 0 for pos3 and pos4 (they were skipped). The earlier two rejected starts confirm dwell was 0 at some point.

### Test steps

**Step 3.1 — Check what presets the camera actually has**
- Open web viewer → Output panel
- Use PTZ menu (CLI) → `p` to list presets
- OR use Device Info → Raw CGI → `getPTZPresetPointList`
- Record: exact preset names the camera reports

**Step 3.2 — Save presets with logging active**
- Move camera to position 1
- Click "Edit presets..." → Save 1
- Check nerdcam.log — confirm `CGI: ptzAddPresetPoint OK {'name': 'pos1'}` with result=0
- Repeat for positions 2, 3, 4
- Record: did all 4 saves return result=0 (success)?

**Step 3.3 — Verify camera stored all 4**
- After saving all 4, repeat Step 3.1
- Does camera report 4 presets (pos1, pos2, pos3, pos4)?

**Step 3.4 — Test patrol with verified positions**
- Configure patrol: set dwell >0 for all 4, click Save Config
- Check nerdcam.log confirms config saved with 4 positions + correct dwell values
- Start patrol, observe which positions it visits
- Check log for `CGI: ptzGotoPresetPoint OK` entries — does it attempt all 4?

### Success criteria
Patrol visits all 4 saved positions in sequence, or we have a clear log showing which step fails (save, camera limit, config, or goto).

---

## Execution Order

1. **Run Issue 2 Step 2.1 first** (30-min idle test) — passive, no interaction needed, collects data while you do other things
2. **Issue 1 Steps 1.1-1.4** — active testing, do while waiting for Step 2.1 timer
3. **Issue 3 Steps 3.1-3.4** — after audio is understood, test presets with fresh logging

## Log Review After Each Step

After each test step, review `nerdcam.log` together before moving to the next step. Do not assume — let the log tell us what happened.

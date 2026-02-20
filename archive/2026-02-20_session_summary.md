# Session Summary — 2026-02-20

## What was completed

### Sprint 2: Modular Refactor (Phase A through E)
- Refactored `nerdcam.py` (2265 lines) into `nerdcam/` package with 12 modules
- `crypto.py`, `camera_cgi.py`, `state.py`, `config.py`, `streaming.py`, `recording.py`, `patrol.py`, `ptz.py`, `camera_control.py`, `server.py`, `cli.py`, `__init__.py`/`__main__.py`
- `AppState` dataclass replaces 22 global variables (bridge functions remain in cli.py for transition)
- Thin launcher `nerdcam.py` in project root

### Server improvements
- Server-side rendered viewer: `nerdcam_template.html` is rendered in-memory per request, no generated file on disk, no credentials stored in files
- Removed `/api/stream` endpoint (MPEG-TS) — redundant with `/api/fmp4`
- Server stop properly kills all active ffmpeg processes (MJPEG, fMP4, audio)
- Structured JSON error responses (`_json_response`, `_error_json`)
- `do_HEAD` handler for API routes

### Stream disconnect/reconnect (cross-browser)
- MJPEG: periodic server watchdog (8s interval) detects both disconnect and reconnect
- MSE/fMP4: health check detects stale data, reconnect with server availability check
- `_checkServer` uses GET `/api/settings` (HEAD was returning 404)
- Works in Firefox and Chromium/Brave (no `onerror` dependency for MJPEG)

### Frontend (nerdcam_template.html)
- Removed empty `src=""` from img tag (attempted Firefox XML error fix — NOT resolved)
- Uses `removeAttribute("src")` instead of `src=""` to clear images
- Server stop shows DISCONNECTED and clears frame in both MJPEG and MSE modes

### CLI menu overhaul
- Main menu simplified: 1=Start/stop server (toggle), 2=Settings, q=Quit
- URLs shown inline when server is running
- Settings restructured into submenus: Camera, Stream, Recording, Network, System
- All submenus clear screen on entry
- Removed "Press Enter to continue" from main menu

### Camera settings
- Fixed `setVideoStreamParam` CGI — requires `streamType` + all params together
- Added VBR/CBR toggle to video settings menu
- `tools/onvif_probe.py` — ONVIF capability query tool (stdlib only, WS-Security auth)
- Documented Foscam R2 V5 hardware specs from ONVIF (max 4096 kbps, GOP 10-100, etc.)
- Optimal settings documented: 1080p, 4096 kbps, CBR, 20 fps, GOP 20

### Other
- Auto time sync on startup (DST-aware, timeZone=0 fix for Foscam)
- Removed `generate_viewer()` dead code
- Roadmap updated: Sprint 3 = go2rtc (WebRTC + RTSP relay + 2-way audio), Sprint 4 = Raspberry Pi 4

## Documentation that needs updating after last commits

The last several commits (server-side rendering, menu overhaul, stream reconnect, /api/stream removal) changed significant behavior. Doc-keeper should verify:

1. **AI_INSTRUCTIONS.md** — project hierarchy still lists `nerdcam.html` as generated at runtime/git-ignored. Now it's served from template, no file on disk. The hierarchy should mention `nerdcam_template.html` is served directly by the server.
2. **README.md** — usage instructions may reference old menu structure (option 1=Open web viewer, etc.). Should reflect new menu (1=Start/stop server, 2=Settings).
3. **docs/ISSUES_REPORT.md** — should document the Firefox XML parsing error as a known remaining issue. The video settings CGI issue is marked resolved but the recommended settings section may need review.
4. **docs/STREAM_ANALYSIS.md** — may still reference `/api/stream` in places, or old latency numbers. Viewer serving model changed (server-side rendered vs file on disk).
5. **concepts/concept.md** — diagram may still show old endpoints or old menu structure.
6. **roadmap.md** — Sprint 2 status needs final update. Todo items from today's session should be reflected.
7. **todo_2026-02-20.md** — needs final status update on all items.
8. **.claude/agents/stream-debugger.md** — may reference old endpoints or old viewer generation.

## Known remaining issues

1. **Firefox XML Parsing Error** — `XML Parsing Error: not well-formed, nerdcam.html:1:1` still appears in Firefox console. Root cause not identified. Attempted fix (remove empty src="") did not resolve it.
2. **PTZ preset Go bug** — not yet investigated this session. Needs camera testing.
3. **Frontend code quality** — the JavaScript in `nerdcam_template.html` has accumulated complexity and could benefit from a cleanup pass. Form fields missing id/name attributes (Chromium warnings).
4. **Regression test** — full regression test not yet done.
5. **Merge dev → main** — pending above items.

## Session learnings

- Chromium/Brave handles MJPEG `<img>` differently from Firefox: `onload` fires once (not per frame), `onerror` fires spuriously
- Foscam CGI API `setVideoStreamParam` requires all parameters sent together with `streamType`
- Foscam CGI `bitRate` is in bits/second, ONVIF reports in kbps
- Server-side template rendering is the correct approach (not generating files on disk)
- `HEAD` requests to API routes need explicit handling (SimpleHTTPRequestHandler only handles static files)
- Never set `img.src = ""` — use `removeAttribute("src")` to avoid Firefox XML parsing errors (though this didn't fully resolve the issue)

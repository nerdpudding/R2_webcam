# Sprint 2: Modular Refactor — Implementation Plan

## Context

`nerdcam.py` is a 2265-line monolith with 22 global variables, ~60 functions, and 9 responsibilities in a single file. Sprint 1 (streaming/latency) is functionally complete — only the PTZ Go button bug remains open. The roadmap calls for more features (NerdPudding optimization, image preprocessing, RTSP relay), which will only make the file larger. Refactoring now prevents it from becoming unmanageable — both for AI tools and for humans.

## Sprint 1 Closure

- Status: **Complete** (PTZ Go bug moves to Sprint 2)
- `PLAN_sprint1_stream_latency.md` → `archive/2026-02-20_sprint1_stream_latency.md`
- `todo_2026-02-20.md` → `archive/daily-schedules/`
- `roadmap.md` Sprint 1 status → "Complete"

## Roadmap Reshuffling

| Item (current roadmap Sprint 2) | Include in Sprint 2? | Rationale |
|---|---|---|
| PTZ Go bug (from Sprint 1) | **Yes** | Small, gets fixed during `ptz.py` extraction |
| Network drive recording location | **Yes** | Trivial: add `output_dir` field during `recording.py` extraction |
| Improve server error responses | **Yes** | Natural moment: server routes are being rewritten anyway |
| NerdPudding stream optimization | **No → Sprint 3** | Feature/tuning work requiring profiling, better done on a clean codebase |
| Image preprocessing pipeline | **No → Sprint 3** | New feature with its own complexity, benefits from modular structure |
| Credential-free RTSP relay | **No → Sprint 3** | New endpoint, new ffmpeg process type |
| PTZ patrol improvements | **Partial** | Obvious improvements during extraction, rest to Sprint 3 |
| Web UI error message improvements | **No → Sprint 3** | Frontend side, separate from backend refactor |
| Raspberry Pi / platform expansion | **No → Sprint 3** | Unchanged |

## Target Package Structure

```
R2_webcam/
  nerdcam.py                    # Thin launcher (5 lines, backward compat)
  nerdcam_template.html         # Unchanged (coupled to API contracts, not backend internals)
  nerdcam/
    __init__.py                 # Package marker + version
    __main__.py                 # python3 -m nerdcam support
    state.py                    # AppState dataclass (replaces 22 globals)
    crypto.py                   # Key derivation, encrypt/decrypt (stateless, zero deps)
    config.py                   # load/save config, settings, onboarding
    camera_cgi.py               # cgi(), ok(), show_dict() (stateless)
    streaming.py                # MjpegSource class (ffmpeg MJPEG shared source)
    recording.py                # Recorder class + codec detection + configurable output dir
    patrol.py                   # PatrolController class
    ptz.py                      # PTZ menu, presets, speed + Go bug fix
    camera_control.py           # image/IR/audio/video/motion/OSD/snapshot/wifi/device menus
    server.py                   # NerdCamServer + ProxyHandler with route dispatcher
    cli.py                      # main(), advanced menu, settings menus
```

## Global State Solution: `AppState` Dataclass

All 22 `global` variables are replaced by a single `AppState` dataclass. Why a dataclass: typed fields (catches typos as `AttributeError`), IDE support, sensible defaults, and it's stdlib-only.

```python
@dataclass
class AppState:
    # Config & auth
    master_pwd: Optional[str] = None
    config: Optional[dict] = None

    # Server
    viewer_server: Any = None

    # Settings (persisted in config.enc)
    stream_quality: int = 7
    mic_gain: float = 3.0
    rtsp_transport: str = "tcp"

    # Stateful components (initialized as instances)
    mjpeg_source: MjpegSource     # via default_factory
    recorder: Recorder            # via default_factory
    patrol: PatrolController      # via default_factory
```

Flow: `main()` creates `state = AppState()`, passes it to all functions. No `global` statements anywhere.

## Dependency Graph (no circular dependencies)

```
                    state.py
                       |
           +-----------+-----------+
           |           |           |
        crypto.py  camera_cgi.py  (stdlib)
           |           |
        config.py      |
           |           |
     +-----+-----+----+----+
     |     |      |    |    |
streaming recording patrol ptz camera_control
     |     |      |    |    |
     +-----+------+----+----+
           |
        server.py
           |
        cli.py
```

`server.py` and `cli.py` are leaf nodes — they import from other modules but nothing imports them.

## Implementation Order

Each step produces a working, testable app. Strategy: copy everything into `nerdcam/cli.py`, then extract modules one by one.

### Phase A: Foundation (no behavior change)

**A1. Package skeleton + entry point**
- Create `nerdcam/` directory with `__init__.py` and `__main__.py`
- Copy full `nerdcam.py` content into `nerdcam/cli.py`
- Replace `nerdcam.py` (root) with thin launcher: `from nerdcam.cli import main; main()`
- **Test:** `python3 nerdcam.py` works identically

**A2. Extract `crypto.py`**
- Move: `_derive_key()`, `_xor_bytes()`, `encrypt_config()`, `decrypt_config()`
- Zero dependencies on other app code — pure functions
- **Test:** Config decrypt/encrypt works

**A3. Extract `camera_cgi.py`**
- Move: `cgi()`, `ok()`, `show_dict()`
- Stateless — takes config dict, returns result
- **Test:** Camera commands from CLI work

### Phase B: State centralization

**B1. Create `state.py` with `AppState` dataclass**

**B2. Extract `config.py` with AppState parameter**
- Move: `load_config()`, `save_config()`, `_load_settings()`, `_save_settings()`, `_onboarding_scan_wifi()`
- Refactor to accept `AppState` parameter instead of globals
- **Test:** Config load/save, settings persistence

### Phase C: Module extraction (one at a time)

**C1. Extract `streaming.py` — MjpegSource class**
- 6 MJPEG globals + 3 functions → class with `start()`, `stop()`, `_reader()`
- **Test:** MJPEG stream, 275s timeout recovery, `/api/mjpeg` contract

**C2. Extract `recording.py` — Recorder class**
- Codec detection + recording management + quality constants
- **Includes:** configurable `output_dir` (roadmap item: network drive recording)
- **Test:** Start/stop recording, codec detection, file output

**C3. Extract `patrol.py` — PatrolController class**
- `start()`, `stop()`, `get_status()`, `get_config()`, `save_config()`, `_loop()`
- **Test:** Start/stop patrol, auto-stop on manual PTZ, config persistence

**C4. Extract `ptz.py` + fix PTZ Go bug**
- All PTZ functions to own module
- **Bug fix:** Debug `ptzGotoPresetPoint` CGI params with raw CGI logging. Suspected cause: camera expects numeric index instead of name string. The `getPTZPresetPointList` response uses `point0`, `point1` keys (numeric), but `ptzGotoPresetPoint` is called with `name=pos1` (string name).
- **Test:** Save preset, Go preset — verify navigation works from both web UI and CLI

**C5. Extract `camera_control.py`**
- All stateless camera menu functions (~400 lines): image, IR, audio, video, motion, OSD, snapshot, wifi, device info, raw command, generate viewer, update credentials
- **Test:** Spot-check 3-4 functions from CLI

### Phase D: Server decomposition

**D1. Extract `server.py` — route dispatcher**
- Largest extraction: 387-line `do_GET` → class with route dict + method per endpoint
- `NerdCamHTTPServer` subclass carries `state` via `self.server.state`
- **Includes:** Structured JSON error responses instead of bare HTTP status codes
- **Test:** Full integration — web viewer, all features, NerdPudding `/api/mjpeg`

### Phase E: CLI cleanup + finalization

**E1. Clean up `cli.py`**
- Only menus + entry point remaining
- All `global` statements removed

**E2. Remove temporary bridges**

**E3. Update docs**
- `AI_INSTRUCTIONS.md` — new package structure in project hierarchy
- `README.md` — any necessary adjustments
- `roadmap.md` — Sprint 2 status, Sprint 3 items

## API Contracts — DO NOT CHANGE

The frontend (`nerdcam_template.html`) is coupled to these contracts. Everything must remain identical:

| Endpoint | Format | Contract |
|---|---|---|
| `/api/cam?cmd=...` | XML | Camera CGI proxy, credential injection |
| `/api/mjpeg` | multipart/x-mixed-replace, `--ffmpeg` boundary | **NerdPudding contract** |
| `/api/fmp4` | fragmented MP4 | MSE browser engine |
| `/api/audio` | MP3 stream | Legacy audio |
| `/api/stream` | ~~MPEG-TS~~ | Removed — redundant with `/api/fmp4` |
| `/api/snap` | JPEG | Snapshot |
| `/api/settings` | JSON | Settings GET/POST with exact field names |
| `/api/record?action=...` | JSON | Recording control |
| `/api/patrol?action=...` | JSON | Patrol control |

## Testing Strategy

No test framework (KISS). Manual but systematic.

**Per-step smoke test:**
1. `python3 nerdcam.py` starts without import errors
2. Config decryption works
3. Device info shows (CGI communication)
4. Web viewer opens (server + streaming)
5. MJPEG stream in browser
6. Mic on → MSE/fMP4
7. PTZ movement
8. Recording start/stop

**API contract verification (before and after refactor):**
```bash
# Before refactor (capture baseline):
curl -s http://localhost:8088/api/settings | python3 -m json.tool > /tmp/settings_before.json
curl -s http://localhost:8088/api/record?action=status > /tmp/record_before.json
curl -s http://localhost:8088/api/patrol?action=status > /tmp/patrol_before.json
# After refactor: diff outputs — structure and field names must match
```

**Full regression test after Phase E:**
- `python3 nerdcam.py` and `python3 -m nerdcam`
- Settings persistence (change, restart, verify)
- 275s timeout recovery
- PTZ presets Save + Go (bug fix verification)
- Patrol start/stop/config
- Recording start/stop + file output
- NerdPudding `/api/mjpeg` consumption

## Risks

| Risk | Impact | Mitigation |
|---|---|---|
| Breaking `/api/mjpeg` contract | High | API contract test before/after. Handler logic moves intact. |
| Import errors from circular deps | Medium | Dependency graph is acyclic by design |
| `python3 nerdcam.py` stops working | Medium | Thin launcher stays in place. Test at every step. |
| Thread safety regression in MjpegSource | Medium | Preserve same GIL-based model (single writer, multiple readers) |
| Losing saved settings | Low | Config format (`config.enc`) does not change |

## Updated Roadmap After Sprint 2

### Sprint 2: Modular Refactor + QoL
Refactor monolith → Python package with SOLID modules. Fix PTZ Go bug, add network drive recording path, improve server error responses.

### Sprint 3: Features + Platform
- NerdPudding stream optimization
- Image preprocessing pipeline
- Credential-free RTSP relay endpoint
- PTZ patrol improvements (remaining)
- Web UI error messages (frontend)
- Raspberry Pi / low-power hardware
- Generic ONVIF support (optional)

## Files Modified

| File | What changes |
|---|---|
| `nerdcam.py` | Replaced with thin launcher (5 lines) |
| `nerdcam/` (new) | 12 module files extracted from monolith |
| `roadmap.md` | Sprint 1 → complete, Sprint 2/3 updated |
| `AI_INSTRUCTIONS.md` | Project hierarchy updated to package structure |
| `README.md` | Minor updates if needed |

# AI Instructions — NerdCam

## Project Overview

NerdCam is a Python tool for controlling and streaming from a Foscam R2 IP camera over the local network. No cloud, no manufacturer apps. It provides a CLI and web viewer with live streaming, PTZ control, recording, audio, and all camera settings. A proxy server hides camera credentials from all consuming applications. Also serves as an MJPEG feed source for NerdPudding (separate AI processing project).

## Principles

- **SOLID, DRY, KISS** — keep it simple, don't over-engineer
- **One source of truth** — no duplicating information across docs
- **Never delete** — archive to `archive/` with date prefix
- **Modularity** — split into focused modules following SOLID principles
- **ALL code, docs, comments, plans, and commit messages MUST be in English** — always, no exceptions. The user communicates in Dutch, but everything written to files must be English.
- **Keep everything up to date** — after any change, verify that docs, agent instructions, and config files still reflect reality. Stale docs are worse than no docs.
- **Learn from mistakes** — when an approach fails or wastes effort, document it in `docs/lessons_learned.md`. This file is persistent context for AI assistants to avoid repeating the same mistakes.
- **Build on existing work** — read existing code and docs before changing anything
- **Use agents** — check agents table below before starting specialized tasks. After changes that affect an agent's domain, update that agent's instructions.
- **Local-first** — no cloud dependencies, everything runs on LAN
- **Python stdlib preferred** — avoid pip dependencies for core functionality
- **ffmpeg is the backbone** — all streaming/recording goes through ffmpeg subprocesses

## Workflow

1. Read this file first
2. Read task tracker (if one exists for today)
3. Read active plans in `claude_plans/`
4. Plan before acting — use plan mode for non-trivial changes
5. Ask approval before implementing
6. Implement
7. Test (manual — run the app and verify behavior)
8. Iterate based on findings
9. Clean up — archive completed plans, update trackers

## Project Hierarchy

```
R2_webcam/
├── AI_INSTRUCTIONS.md          # THIS FILE — AI rules, hierarchy, agents
├── README.md                   # User-facing overview, features, usage
├── nerdcam.py                  # Thin launcher (imports nerdcam.cli.main)
├── nerdcam/                    # Main application package
│   ├── __init__.py             # Package marker + version
│   ├── __main__.py             # python3 -m nerdcam support
│   ├── cli.py                  # Entry point, menus, main loop
│   ├── state.py                # AppState dataclass, constants, paths
│   ├── crypto.py               # Encrypt/decrypt config (PBKDF2 + XOR)
│   ├── config.py               # Load/save config, settings, onboarding
│   ├── camera_cgi.py           # CGI helpers: cgi(), ok(), show_dict()
│   ├── camera_control.py       # Stateless camera menus (image, IR, audio, etc.)
│   ├── streaming.py            # MjpegSource class (shared ffmpeg MJPEG source)
│   ├── recording.py            # Recorder class + codec detection
│   ├── patrol.py               # PatrolController class (PTZ cycling)
│   ├── ptz.py                  # PTZ menus, presets, patrol config
│   └── server.py               # HTTP proxy server + route dispatcher
├── nerdcam_template.html       # Web viewer HTML/JS template (served in-memory per request by the proxy server)
├── config.example.json         # Example config structure for reference
├── config.enc                  # Encrypted credentials (git-ignored)
├── nerdcam.log                 # Runtime log (git-ignored)
├── roadmap.md                  # Sprint-based roadmap and progress
├── todo_YYYY-MM-DD.md          # Daily task tracker (temporary, archive when done)
├── .gitignore                  # Git exclusion rules
│
├── concepts/                   # Initial concept and design thinking
│   └── concept.md              # Vision, architecture, decisions
│
├── docs/                       # Analysis, diagnostics, technical docs
│   ├── ISSUES_REPORT.md        # Current known issues and their status
│   ├── STREAM_ANALYSIS.md      # Stream architecture analysis and findings
│   └── lessons_learned.md      # What worked and didn't (context for AI assistants)
│
├── recordings/                 # Local recording output (git-ignored)
│
├── tools/                      # Standalone utility scripts (not part of the app package)
│   └── onvif_probe.py          # ONVIF capability query tool (queries camera hardware specs via WS-Security)
│
├── claude_plans/               # Active plans from plan mode
│
├── archive/                    # Completed plans, old schedules, superseded docs
│
└── .claude/
    ├── settings.json           # Project-level Claude Code settings
    └── agents/                 # Specialized agent definitions
        ├── doc-keeper.md       # Documentation audit agent
        └── stream-debugger.md  # Stream diagnostics and architecture agent
```

## Agents

| Agent | When to use |
|-------|-------------|
| `doc-keeper` | After making changes — verify docs still match reality. Also for "clean up docs", "check consistency", or periodic maintenance sweeps. |
| `stream-debugger` | When diagnosing stream issues: latency, sync, freezes, ffmpeg behavior, RTSP connectivity, quality. Also for evaluating architecture changes (WebRTC/MSE/HLS). |

## Key Technical Context

### Two Use Cases
1. **Standalone webcam app** — web viewer with live video, audio, PTZ, recording, all settings
2. **MJPEG feed for NerdPudding** — `/api/mjpeg` endpoint consumed by separate AI processing app

### NerdPudding Integration Context
NerdPudding is a separate AI processing app that consumes `/api/mjpeg` as its camera input:
- Uses a **custom HTTP MJPEG boundary parser** (multipart/x-mixed-replace) — any changes to `/api/mjpeg` output format will break it
- **Does not need audio** from NerdCam — only video frames
- Needs **smooth 25fps for display** (re-served to its own browser UI), but AI inference only uses ~2 FPS
- Has **auto-reconnect on MJPEG** (2s) — the 275s camera timeout causes a brief freeze that NerdPudding handles
- **MJPEG quality directly impacts AI accuracy** — higher quality setting = more detail for inference
- **Future possibility**: NerdPudding may switch to RTSP input if they fix their OpenCV reconnect logic (H.264 preserves more detail than MJPEG). NerdCam would then need a credential-free RTSP relay endpoint. Not needed now but keep the door open.

**Rule**: Any streaming architecture changes (MSE/WebRTC for Use Case 1) must leave `/api/mjpeg` completely untouched for Use Case 2.

### Streaming Architecture (post-Sprint 1)
- **Hybrid web viewer** — Mic OFF: MJPEG `<img>` (~1s latency, fast). Mic ON: MSE/fMP4 `<video>` (~3-3.5s latency, synced A/V). Auto-switches. Fallback to MJPEG if browser doesn't support MSE.
- **`/api/fmp4` endpoint** — H.264 copy + AAC 128k in fragmented MP4. Per-client ffmpeg process. Used internally by web viewer MSE engine when mic is enabled.
- **TCP default transport** — UDP post-timeout restarts failed 1-4 times. TCP has zero failures, reliable first-attempt recovery.
- **A/V sync: resolved** — MSE/fMP4 provides inherently synchronized audio and video from a single ffmpeg process. Trade-off: ~3-3.5s latency vs MJPEG's ~1s video-only.

### Known Architectural Limitations
- **Camera RTSP timeout** — Foscam R2 (firmware 2.71.1.81, final version) drops RTSP every ~275s. Confirmed unfixable: OPTIONS returns 501, GET_PARAMETER ignored, no CGI setting, no firmware update (end-of-life April 2022). Auto-recovery: ~4s total freeze (2s stale detection + 2s restart). TCP recovery is reliable on first attempt.
- **MJPEG re-encodes** — `/api/mjpeg` transcodes H.264 to MJPEG, losing quality. By design for browser compatibility and NerdPudding's JPEG-native pipeline. Quality setting matters for AI inference.
- **Concurrent RTSP session limit** — Camera returns "453 Not Enough Bandwidth" when too many sessions open. Typical usage: 1 shared MJPEG source + 1 fMP4 per browser client (when mic on) + recording = 3 sessions. Mic gain uses Apply button (not live slider) to avoid session exhaustion.
- **MSE latency** — ~3-3.5s is inherent to the fMP4/MSE pipeline (fragmentation, browser buffering). Cannot be reduced without switching to WebRTC. Acceptable trade-off for synced A/V.

### Current Priority
Check `roadmap.md` for current sprint status and priorities. `/api/mjpeg` must remain stable throughout all changes.

## Plan Rules

- Plans go in `claude_plans/`
- Immediately after exiting plan mode, rename the plan file to `PLAN_<topic>.md`
- After completing a plan, move to `archive/` with date prefix
- Update progress in the appropriate tracker (one place only)

## Archive Rules

Move to `archive/` with date prefix (e.g. `2026-02-19_plan_name.md`):
- Completed plans
- Superseded documentation
- Old daily task trackers

## Git Commits

- No AI attribution (no "Co-Authored-By: Claude" or similar)
- Only commit when explicitly asked
- English commit messages
- Conventional style: describe what changed and why

## After Compaction

Read order:
1. This file (`AI_INSTRUCTIONS.md`)
2. `docs/lessons_learned.md` — avoid repeating past mistakes
3. Today's task tracker (if exists)
4. Active plans in `claude_plans/`
5. `concepts/concept.md` for context
6. List contents of `claude_plans/`, `docs/`, `archive/` to know the full picture
7. Continue with the current task

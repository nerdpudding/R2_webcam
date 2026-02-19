# AI Instructions — NerdCam

## Project Overview

NerdCam is a Python tool for controlling and streaming from a Foscam R2 IP camera over the local network. No cloud, no manufacturer apps. It provides a CLI and web viewer with live streaming, PTZ control, recording, audio, and all camera settings. A proxy server hides camera credentials from all consuming applications. Also serves as an MJPEG feed source for NerdPudding (separate AI processing project).

## Principles

- **SOLID, DRY, KISS** — keep it simple, don't over-engineer
- **One source of truth** — no duplicating information across docs
- **Never delete** — archive to `archive/` with date prefix
- **Modularity** — single-file is fine while it works; split only when warranted
- **English only** — all code, comments, docs, commit messages
- **Build on existing work** — read existing code and docs before changing anything
- **Use agents** — check agents table below before starting specialized tasks
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
├── nerdcam.py                  # Main application (Python 3 + ffmpeg, ~2200 lines)
├── nerdcam_template.html       # Web viewer HTML/JS template (injected at runtime)
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
│   └── TROUBLESHOOTING_PLAN.md # Diagnostic plans and test results
│
├── recordings/                 # Local recording output (git-ignored)
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

### Known Architectural Limitations
- **A/V desync in browser** — video (`<img>` MJPEG, ~1s latency) and audio (`<Audio>` MP3, ~5s latency) are separate streams. Cannot be synced without architectural change (WebRTC/MSE/HLS). Only affects Use Case 1.
- **Camera RTSP timeout** — Foscam R2 drops RTSP sessions every ~275s. Auto-recovery works but total visible freeze is ~7s (5s stale detection + 2s restart). Affects both use cases. RTSP keepalive may prevent this.
- **MJPEG re-encodes** — `/api/mjpeg` transcodes H.264 to MJPEG, losing quality. By design for browser compatibility and NerdPudding's JPEG-native pipeline. Quality setting matters for AI inference.

### Current Priority
Reduce A/V latency and improve sync — may require rethinking streaming architecture. Work happens in a separate development branch. `/api/mjpeg` must remain stable throughout.

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
2. Today's task tracker (if exists)
3. Active plans in `claude_plans/`
4. `concepts/concept.md` for context
5. Continue with the current task

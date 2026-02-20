# NerdCam — Concept Document

Date: 2026-02-19

## Vision

NerdCam is a fully local, no-cloud replacement for the Foscam R2's outdated official apps. It provides complete camera control through a Python CLI and web viewer, keeping all credentials encrypted on the user's machine. The proxy architecture ensures no application consuming the camera feed ever needs direct access to camera credentials.

## Core Idea

```
                     NerdCam
  ┌────────────────────────────────────────────┐
  │                                            │
  │  CLI (nerdcam.py)      Web Viewer          │
  │    ┌──────────┐       ┌──────────────┐     │
  │    │ Main Menu│       │ Browser UI   │     │
  │    │ PTZ/IR/  │       │ Live stream  │     │
  │    │ Settings │       │ PTZ controls │     │
  │    └────┬─────┘       │ Recording    │     │
  │         │             └──────┬───────┘     │
  │         │                    │              │
  │    ┌────┴────────────────────┴────┐        │
  │    │     HTTP Proxy Server        │        │
  │    │  :8088                       │        │
  │    │  /api/mjpeg  /api/fmp4       │        │
  │    │  /api/stream /api/audio      │        │
  │    │  /api/snap   /api/cam        │        │
  │    │  /api/record /api/patrol     │        │
  │    │  /api/settings               │        │
  │    └────────────┬─────────────────┘        │
  │                 │                           │
  └─────────────────┼───────────────────────────┘
                    │ RTSP + CGI (credentials injected)
                    ▼
            ┌───────────────┐
            │  Foscam R2    │
            │  Camera       │
            │  (LAN only)   │
            └───────────────┘
```

## System Context Diagram (C4 Level 1)

```
┌──────────┐         ┌──────────────┐         ┌───────────┐
│   User   │────────>│   NerdCam    │────────>│ Foscam R2 │
│ (browser │  HTTP   │   (Python)   │  RTSP   │  Camera   │
│  or CLI) │  :8088  │              │  + CGI  │  (LAN)    │
└──────────┘         └──────┬───────┘         └───────────┘
                            │
                     ┌──────┴───────┐
                     │ NerdPudding  │
                     │ (AI app)     │
                     │ reads /mjpeg │
                     └──────────────┘
```

## Container Diagram (C4 Level 2)

```
NerdCam Application
┌──────────────────────────────────────────────────┐
│                                                  │
│  ┌──────────────┐   ┌──────────────────────┐     │
│  │ CLI Module   │   │ HTTP Server           │     │
│  │ - Menus      │   │ (http.server)         │     │
│  │ - PTZ        │   │ - Serves web viewer   │     │
│  │ - Settings   │   │ - Proxies API calls   │     │
│  │ - Recording  │   │ - Injects credentials │     │
│  └──────────────┘   └──────────┬────────────┘     │
│                                │                  │
│  ┌─────────────────────────────┴───────────────┐  │
│  │ Stream Engine                                        │  │
│  │ ┌─────────────┐  ┌───────────┐  ┌────────┐ ┌──────┐ │  │
│  │ │ MJPEG src   │  │ fMP4 src  │  │ AV src │ │Audio │ │  │
│  │ │ (shared     │  │ (per-req  │  │ (per-  │ │(per- │ │  │
│  │ │  ffmpeg)    │  │  ffmpeg)  │  │  req)  │ │ req) │ │  │
│  │ └─────────────┘  └───────────┘  └────────┘ └──────┘ │  │
│  └──────────────────────────────────────────────────────┘  │
│                                                  │
│  ┌──────────────┐   ┌──────────────┐             │
│  │ Config Mgr   │   │ Recorder     │             │
│  │ PBKDF2 enc   │   │ ffmpeg       │             │
│  │ config.enc   │   │ NVENC/SW     │             │
│  └──────────────┘   └──────────────┘             │
│                                                  │
└──────────────────────────────────────────────────┘
```

## Input/Output Design

| Feature | Current | Later |
|---------|---------|-------|
| Video input | RTSP H.264 from camera (TCP default) | Same |
| Video output (browser) | Hybrid: MJPEG `<img>` (mic off, ~1s) / MSE fMP4 `<video>` (mic on, ~3-3.5s synced A/V) | WebRTC for lower latency |
| Video output (NerdPudding) | HTTP MJPEG `/api/mjpeg` (custom boundary parser) | Possibly RTSP relay (if NerdPudding adds reconnect) |
| Audio output (browser) | Combined with video via MSE/fMP4 (synced) | — |
| A/V output (VLC) | MPEG-TS `/api/stream` or fMP4 `/api/fmp4` | Lower latency options |
| Recording | Local MP4 (NVENC/SW) | Network drive storage |
| Image preprocessing | None | Lighting/contrast for NerdPudding |
| Platform | x86 Linux | Raspberry Pi support |

## Key Technical Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Language | Python 3 stdlib only | No pip dependencies, runs anywhere |
| Streaming | ffmpeg subprocess | Handles RTSP, transcoding, muxing |
| Web server | `http.server` stdlib | No external web framework needed |
| Credential storage | PBKDF2+AES encryption | Secure, no external service |
| MJPEG architecture | Single shared ffmpeg | Efficient: one process serves N clients |
| Recording codec | Auto-detected NVENC/SW | Uses GPU if available, falls back gracefully |
| Camera protocol | CGI API + RTSP | Foscam R2 native interface, no cloud |

## Hardware / Constraints

| Item | Details |
|------|---------|
| Camera | Foscam R2 — 1080p, 25fps, WiFi 2.4GHz, pan/tilt, IR |
| Camera RTSP | Fixed ~275s session timeout (camera firmware limitation) |
| GPU (optional) | NVIDIA with NVENC for hardware recording |
| Target platform | Linux x86 (future: Raspberry Pi) |
| Network | LAN only — camera should be blocked from internet |

## Available Resources

| Resource | Location | Purpose |
|----------|----------|---------|
| nerdcam.py | Project root | Main application (2200 lines) |
| nerdcam_template.html | Project root | Web viewer template |
| Foscam CGI API | Camera firmware | Control protocol (no official docs) |
| NerdPudding | Separate project | AI app consuming `/api/mjpeg` |

## Use Cases

### Primary
1. **Live monitoring** — View camera feed in browser with lowest possible latency
2. **Camera control** — PTZ, presets, patrol, IR, image settings via web UI or CLI
3. **Local recording** — Record to MP4 with GPU acceleration when available
4. **AI feed** — Provide reliable MJPEG stream to NerdPudding for AI processing

### Secondary
5. **Audio monitoring** — Listen to camera mic (synced with video via MSE/fMP4 hybrid)
6. **Multi-app streaming** — Multiple applications consuming the proxy simultaneously
7. **Remote-ish access** — Access from any device on the LAN via browser

## Development Approach

Iterative hobby project using sprint-based planning (see `roadmap.md`). SOLID/DRY/KISS principles. The monolith (`nerdcam.py`, ~2200 lines) is being refactored into a modular Python package in Sprint 2 — complexity has reached the point where splitting is warranted.

# NerdCam - Foscam R2 Setup & Control

A custom Python tool for controlling and streaming from a **Foscam R2** IP camera entirely over the local network. No cloud, no outdated manufacturer apps, no browser plugins.

Built as a replacement for the official Foscam Android/PC apps which are outdated and depend on cloud services. NerdCam gives you full control through a simple CLI and an optional web viewer, while keeping your credentials encrypted locally.

## Table of Contents

- [Requirements](#requirements)
- [Quick Start](#quick-start)
- [First Run (Onboarding)](#first-run-onboarding)
- [Configuration](#configuration)
- [Main Menu](#main-menu)
- [Advanced Settings](#advanced-settings)
- [Web Viewer](#web-viewer)
- [Proxy Stream URLs](#proxy-stream-urls)
- [Security Tip: Block Camera Internet Access](#security-tip-block-camera-internet-access)
- [Camera Specs](#camera-specs)
- [Factory Reset](#factory-reset)
- [Files](#files)

## Requirements

**Required:**
- **Python 3.6+** (uses only the standard library, no pip packages needed)
- **ffmpeg** (for streaming, audio, recording, codec detection)
- A **Foscam R2** camera connected to your local network

```bash
# Install ffmpeg on Debian/Ubuntu
sudo apt install ffmpeg
```

**Optional:**
- **NVIDIA GPU + drivers** for hardware-accelerated recording (NVENC H.264/H.265/AV1). Without a GPU, software encoding (libx264/libx265) is used automatically. NVENC uses the GPU's dedicated encoder chip (not CUDA cores), so impact on other GPU workloads is minimal (~1% utilization). Systems with multiple GPUs can select which one to use for recording.
- **OpenCV** (`pip install opencv-python`) for the RTSP test function (CLI only)
- **VLC** or **ffplay** for direct stream playback (CLI only)
- **xdg-open** for auto-opening the browser (present on most Linux desktops)

The app auto-detects available encoders, GPUs, and dependencies at startup.

## Quick Start

```bash
python3 nerdcam.py
```

That's it. On first run you'll be guided through setup. On subsequent runs, just enter your master password.

## First Run (Onboarding)

When no configuration exists yet, the app walks you through an interactive setup:

1. **Camera connection** - Enter the camera's IP address, HTTP port (default 88), username, and password
2. **WiFi setup** - The app tries to scan for available WiFi networks through the camera. Enter your WiFi SSID and password so the camera can connect wirelessly
3. **Master password** - Choose a master password to encrypt all credentials. You'll need this every time you start the app

After setup, all credentials are encrypted into `config.enc` and any plaintext config is deleted. The master password is only kept in memory for the current session and never written to disk.

**Optional:** You can copy `config.example.json` to `config.json` before running the app to pre-fill default values. The onboarding will use these as defaults (press Enter to keep them). The plaintext file is automatically deleted after encryption.

## Configuration

All credentials (camera IP, username, password, WiFi SSID, WiFi password) are stored in `config.enc`, encrypted with PBKDF2 key derivation (100,000 iterations, SHA-256) and a random salt. App settings like stream quality are also saved in the encrypted config, so they persist between sessions.

- `config.enc` - Encrypted credentials and settings (master-password protected)
- `config.json` - Only exists temporarily during first setup, then deleted
- `config.example.json` - Template showing the config structure (for reference)

## Main Menu

After entering your master password, you'll see the main menu:

| Option | Description |
|--------|-------------|
| **1. Open web viewer** | Starts the proxy server and opens the web viewer in your browser with live stream and full camera controls |
| **2. Start stream server** | Starts the proxy server without opening the browser. Use this when you want other apps (VLC, OpenCV, etc.) to consume the stream |
| **3. Show stream URLs** | Displays the local proxy URLs you can use in other applications |
| **4. Advanced settings** | Opens the advanced settings submenu (see below) |
| **5. Stop server** | Stops the proxy server |
| **q. Quit** | Stops the server (if running) and exits |

## Advanced Settings

The advanced menu gives access to all camera features:

### Camera Control

| Option | Feature | What it does |
|--------|---------|-------------|
| **1** | PTZ control | Pan, tilt, and zoom using a numpad-style layout (7=up-left, 8=up, 9=up-right, etc.). Manage speed, save/recall preset positions |
| **2** | Image settings | Adjust brightness, contrast, saturation, sharpness (0-100 range). Toggle mirror and flip |
| **3** | Infrared / night vision | Switch between auto mode (IR follows ambient light), force IR on, or force IR off |
| **4** | Video settings | Change resolution (1080p, 720p, VGA, QVGA), framerate (1-30 FPS), bitrate, and keyframe interval. Changes apply instantly |
| **5** | Motion detection | Enable/disable motion detection and set sensitivity level |
| **6** | Audio settings | Adjust volume (0-100), enable/disable sound alarm, test raw audio commands |

### Stream

| Option | Feature | What it does |
|--------|---------|-------------|
| **7** | Stream compression quality | Set MJPEG quality on a 1-10 scale (10=sharpest, 7=default, 1=lowest latency). Saved between sessions |
| **8** | Watch stream in ffplay | Opens the live RTSP stream directly in ffplay or VLC |
| **9** | Test RTSP (OpenCV) | Attempts to capture a single frame via OpenCV to verify the RTSP connection works |
| **0** | Snapshot | Saves a JPEG snapshot from the camera to disk |

### Audio

| Option | Feature | What it does |
|--------|---------|-------------|
| **m** | Mic gain | Set audio volume multiplier (1.0-5.0x) for the microphone stream. Saved between sessions |

### Overlay

| Option | Feature | What it does |
|--------|---------|-------------|
| **o** | OSD overlay | Toggle timestamp and camera name overlay on the video stream. Set the device name |

### Recording

| Option | Feature | What it does |
|--------|---------|-------------|
| **e** | Local recording | Start/stop recording to MP4 files in `recordings/`. Choose codec, compression level (1-10), and GPU. Auto-detects available encoders and GPUs |

Recording settings are independent: **codec** (what encoder), **compression** (1=studio to 10=max compression), and **GPU** (which NVIDIA GPU, only shown with 2+ GPUs). Available codecs are detected at startup. Files are named `nerdcam_YYYYMMDD_HHMMSS.mp4`.

### Network

| Option | Feature | What it does |
|--------|---------|-------------|
| **w** | WiFi status | Shows current WiFi connection info (SSID, encryption type, connected status) |
| **n** | Configure WiFi | Scan for networks and apply WiFi settings (WPA2-PSK) to the camera |
| **p** | Port info | Shows configured ports (HTTP, HTTPS, RTSP, ONVIF, media) |

### System

| Option | Feature | What it does |
|--------|---------|-------------|
| **i** | Device info | Shows device name, firmware version, hardware version |
| **t** | Sync time | Sync the camera's clock from your PC |
| **r** | Reboot camera | Reboots the camera (takes ~60 seconds to come back) |
| **x** | Raw CGI command | Send any CGI command directly to the camera. Useful for exploring the API |
| **c** | Update credentials | Change camera IP, username, password, WiFi SSID, or WiFi password. Automatically re-encrypted |

## Web Viewer

Option **1** from the main menu opens a web-based control panel in your browser with:

- **Live stream** - MJPEG video feed with proper state tracking (CONNECTING / LIVE / RECONNECTING / STOPPED)
- **Pan/Tilt controls** - Arrow buttons to move the camera, configurable PTZ duration and speed, preset positions
- **Infrared toggle** - Auto, force on, force off
- **Image adjustments** - Brightness, contrast, saturation, sharpness sliders, mirror/flip
- **Video settings** - Resolution, framerate, bitrate controls
- **Audio** - Enable/disable mic with adjustable gain (1.0-5.0x), auto-restarts stream on gain change
- **OSD overlay** - Toggle timestamp and camera name overlay, set device name
- **Recording** - Start/stop local recording with selectable quality preset (auto-detected from available encoders)
- **Motion detection** - Enable/disable with sensitivity setting (detection only, events not captured)
- **Camera speaker** - Volume control for the camera's built-in speaker
- **Device info** - Device details, WiFi status, port config, time sync

The viewer runs on `http://localhost:8088` and communicates with the camera through the proxy server, so your camera credentials never leave your machine.

## Proxy Stream URLs

When the server is running (options 1 or 2), these local URLs are available:

| URL | Format | Use case |
|-----|--------|----------|
| `http://localhost:8088/api/mjpeg` | MJPEG (video only) | Browsers, OpenCV, other apps that consume MJPEG |
| `http://localhost:8088/api/stream` | MPEG-TS (video + audio) | VLC, ffplay, media players |
| `http://localhost:8088/api/audio` | MP3 (audio only) | Browser audio playback |
| `http://localhost:8088/api/snap` | Single JPEG | Quick snapshot from any HTTP client |
| `http://localhost:8088/api/settings` | JSON | Read/write app settings (mic gain, recording quality) |
| `http://localhost:8088/api/record?action=X` | JSON | Start/stop/status for local recording |
| `http://localhost:8088/api/cam?cmd=X` | XML | Proxy for camera CGI commands (credentials added server-side) |

These URLs require **no credentials** - the proxy adds them server-side. Any application on your machine can use them.

```bash
# Examples
vlc http://localhost:8088/api/stream
ffplay http://localhost:8088/api/mjpeg
```

```python
# OpenCV
cap = cv2.VideoCapture("http://localhost:8088/api/mjpeg")
```

## Security Tip: Block Camera Internet Access

This tool is designed for **local-only** use. The camera doesn't need internet access to function.

If you want to prevent the camera from phoning home to cloud servers, you can block its internet access at the router level. On many routers (for example, most ASUS models), this is easy to do through **Parental Controls** - just block internet access for the camera's MAC address. This is simpler than setting up firewall rules or a separate VLAN, and achieves the same result: the camera works fine on your local network but can't reach the internet.

## Camera Specs

| Field | Value |
|-------|-------|
| Model | Foscam R2 |
| Type | Indoor wireless IP camera with pan/tilt |
| Max resolution | 1920x1080 (1080p) |
| Max framerate | 25 FPS |
| Connectivity | WiFi (2.4 GHz) + Ethernet |
| Default HTTP/RTSP port | 88 |
| Video codec | H.264 over RTSP |
| Pan/Tilt | Motorized |
| Infrared | Auto / manual |

## Factory Reset

If you lose access to the camera (forgotten password, bad WiFi config, etc.):

1. **Reset the camera** - With the camera powered on, insert a paperclip into the small pinhole on the bottom of the camera. Hold it for about 30 seconds. The camera will reboot and announce "Reset succeeded" (you can release early if you hear that). All settings are wiped: credentials reset to `admin` with a blank password, network settings back to DHCP.

2. **Connect via Ethernet** - After reset, plug the camera into your router with an Ethernet cable. The camera will get a new IP address from your router via DHCP.

3. **Find the camera's IP address** - Since the IP will have changed, you need to find it again. Easiest ways:
   - Check your **router's DHCP client list** (look for a new device, usually named something like "IPC" or "Foscam")
   - Use a network scanner like [Angry IP Scanner](https://angryip.org/) or `nmap -sn 192.168.1.0/24` to find devices on your network
   - Try the Foscam default: `192.168.1.88` (sometimes works, depends on your network)

4. **Run NerdCam again** - Delete your old `config.enc` and run `python3 nerdcam.py`. The onboarding will walk you through entering the new IP, the default `admin` username, and setting everything up again.

### Recommended: Give the Camera a Static IP

To avoid the camera's IP changing after a reboot or power loss, configure a **DHCP reservation** (also called "static lease") in your router. This binds a specific IP address to the camera's MAC address so it always gets the same one.

**Important:** The Foscam R2 has **two separate MAC addresses** - one for its Ethernet adapter and one for its WiFi adapter. The camera can only use one connection at a time (Ethernet OR WiFi, not both simultaneously). If you set up a DHCP reservation, make sure you use the MAC address of the interface you'll actually be using. If you want to use WiFi, configure WiFi first while connected via Ethernet, then unplug the cable - the camera will switch to its WiFi adapter. You can usually find both MAC addresses in your router's client list or via option **i** (Device Info) in NerdCam.

## Files

| File | Purpose |
|------|---------|
| `nerdcam.py` | Main application (Python 3 + ffmpeg) |
| `nerdcam_template.html` | HTML/JS template for the web viewer |
| `config.example.json` | Example config structure (copy to `config.json` to pre-fill defaults, or just run the app) |
| `recordings/` | Local recording output directory (created automatically, git-ignored) |
| `.gitignore` | Excludes credentials, generated files, and recordings from version control |

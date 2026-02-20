#!/usr/bin/env python3
"""NerdCam - Foscam R2 local control, streaming, and recording tool.

On first run, prompts for camera credentials and WiFi settings,
then encrypts everything into config.enc with a master password.
Run: python3 nerdcam.py
"""

import atexit
import json
import logging
import os
import subprocess
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
import getpass
import threading
import time

from nerdcam.camera_cgi import cgi, ok, show_dict
from nerdcam import config as _config_mod
from nerdcam.state import (AppState, PROJECT_DIR, LOG_PATH,
                           COMPRESSION_LABELS)
from nerdcam.streaming import MjpegSource
from nerdcam.recording import Recorder, detect_codecs
from nerdcam.patrol import PatrolController, get_patrol_config, save_patrol_config
from nerdcam import ptz as _ptz_mod
from nerdcam import camera_control as _cam_ctl

# Module-level state reference, set in main()
_state = None


def cls():
    """Clear terminal screen."""
    os.system("clear" if os.name != "nt" else "cls")


# Logging: DEBUG+ to file, WARNING+ to terminal (doesn't interfere with menu)
log = logging.getLogger("nerdcam")
log.setLevel(logging.DEBUG)
_log_file = logging.FileHandler(LOG_PATH, encoding="utf-8")
_log_file.setLevel(logging.DEBUG)
_log_file.setFormatter(logging.Formatter(
    "%(asctime)s %(levelname)-5s %(message)s", datefmt="%Y-%m-%d %H:%M:%S"))
_log_console = logging.StreamHandler()
_log_console.setLevel(logging.WARNING)
_log_console.setFormatter(logging.Formatter("  %(levelname)s: %(message)s"))
log.addHandler(_log_file)
log.addHandler(_log_console)

_viewer_server = None
_stream_quality = 7  # 1-10 scale: 10=best quality, 1=lowest. Maps to ffmpeg -q:v internally.
_mic_gain = 3.0  # Audio volume multiplier for mic stream (1.0-5.0)
_rtsp_transport = "tcp"  # "tcp" (reliable, zero post-timeout failures) or "udp" (lower startup latency)

# Shared MJPEG source instance
_mjpeg = MjpegSource()


def _start_mjpeg_source(cam):
    """Bridge: start MJPEG source using current globals."""
    _mjpeg.start(cam, _stream_quality, _rtsp_transport)


def _stop_mjpeg_source():
    """Bridge: stop MJPEG source."""
    _mjpeg.stop()

# Recording state
_rec_codec = None
_rec_compression = 5
_rec_gpu = "auto"
_available_gpus = []
REC_CODECS = {}
_DEFAULT_REC_CODEC = "original"
_recorder = Recorder()

# Master password for this session (set once at startup)
_master_pwd = None


# ---------------------------------------------------------------------------
# Bridge functions: sync globals <-> AppState until full migration
# ---------------------------------------------------------------------------

def _save_settings(config):
    """Bridge: save settings via globals -> state -> config.py."""
    if _state is None:
        return
    _state.stream_quality = _stream_quality
    _state.mic_gain = _mic_gain
    _state.rtsp_transport = _rtsp_transport
    _state.rec_codec = _rec_codec
    _state.rec_compression = _rec_compression
    _state.rec_gpu = _rec_gpu
    _state.config = config
    _config_mod.save_settings(_state)


def save_config(config):
    """Bridge: save config via state."""
    if _state is None:
        return
    _state.config = config
    _config_mod.save_config(_state)


# Camera control functions delegated to nerdcam.camera_control

def _rtsp_url(config) -> str:
    return _cam_ctl._rtsp_url(config)


def ptz_menu(config):
    _ptz_mod.ptz_menu(config, _patrol, save_config)


def open_viewer(config, open_browser=True):
    """Start proxy server (and optionally open web viewer in browser)."""
    import http.server
    from urllib.parse import urlparse, parse_qs

    cam = config["camera"]
    cam_base = f"http://{cam['ip']}:{cam['port']}/cgi-bin/CGIProxy.fcgi"

    port = 8088

    class ProxyHandler(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=PROJECT_DIR, **kwargs)

        def log_message(self, format, *args):
            pass

        def do_GET(self):
            global _mic_gain, _rec_codec, _rec_compression, _rec_gpu, _rtsp_transport
            parsed = urlparse(self.path)

            # Proxy: /api/cam?cmd=XXX&param=val -> camera CGI
            if parsed.path == "/api/cam":
                qs = parse_qs(parsed.query)
                params = {k: v[0] for k, v in qs.items()}
                params["usr"] = cam["username"]
                params["pwd"] = cam["password"]
                cam_url = f"{cam_base}?{urllib.parse.urlencode(params)}"
                cmd_name = params.get("cmd", "?")
                # Log PTZ and preset commands with their parameters
                extra_params = {k: v for k, v in params.items() if k not in ("cmd", "usr", "pwd")}
                if extra_params:
                    log.debug("CGI: %s %s", cmd_name, extra_params)
                else:
                    log.debug("CGI: %s", cmd_name)
                try:
                    with urllib.request.urlopen(cam_url, timeout=10) as resp:
                        data = resp.read()
                    # Parse result code for logging
                    try:
                        _root = ET.fromstring(data.decode())
                        _result = {c.tag: c.text or "" for c in _root}
                        _rc = _result.get("result", "?")
                        if _rc != "0":
                            log.warning("CGI: %s returned result=%s", cmd_name, _rc)
                        elif cmd_name.startswith("ptz"):
                            log.info("CGI: %s OK %s", cmd_name, extra_params)
                    except Exception:
                        pass
                    self.send_response(200)
                    self.send_header("Content-Type", "text/xml")
                    self.send_header("Access-Control-Allow-Origin", "*")
                    self.end_headers()
                    self.wfile.write(data)
                except Exception as e:
                    log.error("CGI proxy error (cmd=%s): %s", cmd_name, e)
                    self.send_response(502)
                    self.send_header("Content-Type", "text/plain")
                    self.end_headers()
                    self.wfile.write(f"Camera error: {e}".encode())
                return

            # Proxy: /api/snap -> camera snapshot (returns JPEG)
            if parsed.path == "/api/snap":
                snap_url = (f"{cam_base}?cmd=snapPicture2"
                            f"&usr={urllib.parse.quote(cam['username'])}"
                            f"&pwd={urllib.parse.quote(cam['password'])}")
                try:
                    with urllib.request.urlopen(snap_url, timeout=10) as resp:
                        data = resp.read()
                    self.send_response(200)
                    self.send_header("Content-Type", "image/jpeg")
                    self.send_header("Cache-Control", "no-cache")
                    self.end_headers()
                    self.wfile.write(data)
                except Exception as e:
                    self.send_response(502)
                    self.end_headers()
                return

            # MJPEG stream (shared source: one ffmpeg, multiple clients)
            # OpenCV can read this: cv2.VideoCapture("http://localhost:8088/api/mjpeg")
            if parsed.path == "/api/mjpeg":
                self.connection.settimeout(30)
                _start_mjpeg_source(cam)
                self.send_response(200)
                self.send_header("Content-Type",
                                 "multipart/x-mixed-replace; boundary=ffmpeg")
                self.send_header("Cache-Control", "no-cache")
                self.end_headers()
                log.info("MJPEG client connected from %s", self.client_address[0])
                try:
                    last_id = 0
                    no_frame_count = 0
                    while True:
                        fid = _mjpeg.frame_id
                        frame = _mjpeg.frame
                        if fid > last_id and frame is not None:
                            no_frame_count = 0
                            last_id = fid
                            self.wfile.write(b"--ffmpeg\r\n")
                            self.wfile.write(b"Content-Type: image/jpeg\r\n")
                            self.wfile.write(
                                f"Content-Length: {len(frame)}\r\n".encode())
                            self.wfile.write(b"\r\n")
                            self.wfile.write(frame)
                            self.wfile.write(b"\r\n")
                            self.wfile.flush()
                        else:
                            time.sleep(0.02)
                            no_frame_count += 1
                            if no_frame_count >= 100:  # ~2s no frames
                                log.warning("MJPEG client: %ds no frames, requesting source restart", no_frame_count // 50)
                                _start_mjpeg_source(cam)
                                no_frame_count = 0
                except (BrokenPipeError, ConnectionResetError, OSError):
                    log.info("MJPEG client disconnected from %s", self.client_address[0])
                return

            # Audio-only stream via ffmpeg (RTSP -> MP3, for browser <audio>)
            if parsed.path == "/api/audio":
                self.connection.settimeout(30)
                rtsp_port = cam.get("port", 88)
                rtsp_url = (f"rtsp://{cam['username']}:{cam['password']}"
                            f"@{cam['ip']}:{rtsp_port}/videoMain")
                self.send_response(200)
                self.send_header("Content-Type", "audio/mpeg")
                self.send_header("Cache-Control", "no-cache")
                self.end_headers()
                audio_probe = "500000" if _rtsp_transport == "tcp" else "32768"
                audio_analyze = "500000" if _rtsp_transport == "tcp" else "0"
                log.info("Audio stream starting (transport=%s, gain=%.1f)", _rtsp_transport, _mic_gain)
                try:
                    proc = subprocess.Popen(
                        ["ffmpeg",
                         "-fflags", "+nobuffer+flush_packets",
                         "-flags", "low_delay",
                         "-probesize", audio_probe,
                         "-analyzeduration", audio_analyze,
                         "-rtsp_transport", _rtsp_transport,
                         "-i", rtsp_url,
                         "-vn",
                         "-af", f"volume={_mic_gain}",
                         "-c:a", "libmp3lame",
                         "-b:a", "128k",
                         "-f", "mp3",
                         "-flush_packets", "1",
                         "pipe:1"],
                        stdin=subprocess.DEVNULL,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.DEVNULL
                    )
                    while True:
                        chunk = proc.stdout.read(4096)
                        if not chunk:
                            break
                        self.wfile.write(chunk)
                        self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError, OSError):
                    log.info("Audio stream disconnected")
                except Exception as e:
                    log.error("Audio stream error: %s", e)
                finally:
                    try:
                        proc.kill()
                    except Exception:
                        pass
                return

            # Settings endpoint (GET returns JSON, GET with params updates)
            if parsed.path == "/api/settings":
                qs = parse_qs(parsed.query)
                changed = False
                if "mic_gain" in qs:
                    try:
                        val = float(qs["mic_gain"][0])
                        if 1.0 <= val <= 5.0:
                            _mic_gain = round(val, 1)
                            changed = True
                    except (ValueError, IndexError):
                        pass
                if "rec_codec" in qs:
                    val = qs["rec_codec"][0]
                    if val in REC_CODECS:
                        _rec_codec = val
                        changed = True
                if "rec_compression" in qs:
                    try:
                        val = int(qs["rec_compression"][0])
                        if 1 <= val <= 10:
                            _rec_compression = val
                            changed = True
                    except (ValueError, IndexError):
                        pass
                if "rec_gpu" in qs:
                    val = qs["rec_gpu"][0]
                    valid = {"auto"} | {idx for idx, _ in _available_gpus}
                    if val in valid:
                        _rec_gpu = val
                        changed = True
                if "rtsp_transport" in qs:
                    val = qs["rtsp_transport"][0]
                    if val in ("udp", "tcp"):
                        _rtsp_transport = val
                        changed = True
                        # Force MJPEG source restart with new transport
                        _stop_mjpeg_source()
                        log.info("RTSP transport changed to %s, MJPEG source will restart on next request", val)
                if changed:
                    _save_settings(config)
                import json as _json
                codecs_info = {k: {"desc": v[1]}
                               for k, v in REC_CODECS.items()}
                gpus_info = [{"index": idx, "name": name}
                             for idx, name in _available_gpus]
                resp_data = _json.dumps({
                    "mic_gain": _mic_gain,
                    "stream_quality": _stream_quality,
                    "rec_codec": _rec_codec,
                    "rec_compression": _rec_compression,
                    "rec_gpu": _rec_gpu,
                    "rtsp_transport": _rtsp_transport,
                    "rec_codecs": codecs_info,
                    "gpus": gpus_info,
                })
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(resp_data.encode())
                return

            # Recording endpoint (start/stop/status)
            if parsed.path == "/api/record":
                import json as _json
                qs = parse_qs(parsed.query)
                action = qs.get("action", ["status"])[0]
                if action == "start":
                    _start_recording(config)
                elif action == "stop":
                    _stop_recording()
                status = _recording_status()
                resp_data = _json.dumps(status)
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(resp_data.encode())
                return

            # Patrol endpoint (start/stop/status/config)
            if parsed.path == "/api/patrol":
                import json as _json
                qs = parse_qs(parsed.query)
                action = qs.get("action", ["status"])[0]
                if action == "start":
                    result = _start_patrol(config)
                elif action == "stop":
                    result = _stop_patrol()
                elif action == "config":
                    # Save patrol config if data provided
                    patrol_data = {}
                    if "positions" in qs:
                        try:
                            patrol_data["positions"] = _json.loads(qs["positions"][0])
                        except (ValueError, KeyError):
                            pass
                    if "repeat" in qs:
                        patrol_data["repeat"] = qs["repeat"][0] == "true"
                    if patrol_data:
                        current = _get_patrol_config(config)
                        current.update(patrol_data)
                        _save_patrol_config(config, current)
                    result = _get_patrol_config(config)
                else:
                    result = _get_patrol_status()
                result_with_status = dict(_get_patrol_status())
                if isinstance(result, dict) and "ok" in result:
                    result_with_status.update(result)
                elif action == "config":
                    result_with_status["config"] = result
                resp_data = _json.dumps(result_with_status)
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(resp_data.encode())
                return

            # Full A/V stream via ffmpeg (RTSP -> MPEG-TS, for VLC/ffplay)
            # VLC: vlc http://localhost:8088/api/stream
            if parsed.path == "/api/stream":
                self.connection.settimeout(30)
                rtsp_port = cam.get("port", 88)
                rtsp_url = (f"rtsp://{cam['username']}:{cam['password']}"
                            f"@{cam['ip']}:{rtsp_port}/videoMain")
                self.send_response(200)
                self.send_header("Content-Type", "video/mp2t")
                self.send_header("Cache-Control", "no-cache")
                self.end_headers()
                av_probe = "500000" if _rtsp_transport == "tcp" else "32768"
                av_analyze = "500000" if _rtsp_transport == "tcp" else "0"
                log.info("AV stream starting (transport=%s, client=%s)", _rtsp_transport, self.client_address[0])
                try:
                    proc = subprocess.Popen(
                        ["ffmpeg",
                         "-fflags", "+nobuffer+flush_packets",
                         "-flags", "low_delay",
                         "-probesize", av_probe,
                         "-analyzeduration", av_analyze,
                         "-rtsp_transport", _rtsp_transport,
                         "-i", rtsp_url,
                         "-c:v", "copy",
                         "-c:a", "aac",
                         "-f", "mpegts",
                         "-muxdelay", "0",
                         "-muxpreload", "0",
                         "-flush_packets", "1",
                         "pipe:1"],
                        stdin=subprocess.DEVNULL,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE
                    )
                    while True:
                        chunk = proc.stdout.read(4096)
                        if not chunk:
                            break
                        self.wfile.write(chunk)
                        self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError, OSError):
                    log.info("AV stream disconnected")
                except Exception as e:
                    log.error("AV stream error: %s", e)
                finally:
                    try:
                        err = proc.stderr.read().decode(errors="replace").strip()
                        if err:
                            lines = [l for l in err.splitlines() if l.strip()][-5:]
                            log.warning("AV stream ffmpeg stderr:\n  %s", "\n  ".join(lines))
                        proc.kill()
                    except Exception:
                        pass
                return

            # Fragmented MP4 A/V stream for MSE (RTSP -> fMP4, for browser <video>)
            if parsed.path == "/api/fmp4":
                self.connection.settimeout(30)
                rtsp_port = cam.get("port", 88)
                rtsp_url = (f"rtsp://{cam['username']}:{cam['password']}"
                            f"@{cam['ip']}:{rtsp_port}/videoMain")
                self.send_response(200)
                self.send_header("Content-Type", "video/mp4")
                self.send_header("Cache-Control", "no-cache")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                fmp4_probe = "500000" if _rtsp_transport == "tcp" else "32768"
                fmp4_analyze = "500000" if _rtsp_transport == "tcp" else "0"
                gain = f"volume={_mic_gain:.1f}" if _mic_gain != 1.0 else "volume=1.0"
                log.info("fMP4 stream starting (transport=%s, gain=%.1f, client=%s)",
                         _rtsp_transport, _mic_gain, self.client_address[0])
                try:
                    proc = subprocess.Popen(
                        ["ffmpeg",
                         "-fflags", "+nobuffer+flush_packets+genpts",
                         "-flags", "low_delay",
                         "-probesize", fmp4_probe,
                         "-analyzeduration", fmp4_analyze,
                         "-rtsp_transport", _rtsp_transport,
                         "-i", rtsp_url,
                         "-c:v", "copy",
                         "-c:a", "aac", "-b:a", "128k",
                         "-af", gain,
                         "-f", "mp4",
                         "-movflags", "frag_keyframe+empty_moov+default_base_moof",
                         "-frag_duration", "500000",
                         "-min_frag_duration", "250000",
                         "-flush_packets", "1",
                         "pipe:1"],
                        stdin=subprocess.DEVNULL,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE
                    )
                    while True:
                        chunk = proc.stdout.read(4096)
                        if not chunk:
                            break
                        self.wfile.write(chunk)
                        self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError, OSError):
                    log.info("fMP4 stream disconnected")
                except Exception as e:
                    log.error("fMP4 stream error: %s", e)
                finally:
                    try:
                        err = proc.stderr.read().decode(errors="replace").strip()
                        if err:
                            lines = [l for l in err.splitlines() if l.strip()][-5:]
                            log.warning("fMP4 stream ffmpeg stderr:\n  %s", "\n  ".join(lines))
                        proc.kill()
                    except Exception:
                        pass
                return

            # Default: serve static files
            super().do_GET()

    class ThreadedServer(http.server.ThreadingHTTPServer):
        daemon_threads = True

    # Start server if not already running
    global _viewer_server
    if _viewer_server is None:
        server = ThreadedServer(("127.0.0.1", port), ProxyHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        _viewer_server = server
        log.info("Server started on port %d", port)
        print(f"  Server started on port {port}")
    else:
        print(f"  Server already running on port {port}")

    if open_browser:
        _cam_ctl.generate_viewer(config)
        url = f"http://localhost:{port}/nerdcam.html"
        subprocess.Popen(["xdg-open", url],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        print(f"  Web viewer opened: {url}")




def _stop_server():
    global _viewer_server
    _stop_mjpeg_source()
    if _viewer_server:
        _viewer_server.shutdown()
        _viewer_server = None
        log.info("Server stopped")
        print("  Server stopped.")
        return True
    print("  No server running.")
    return False


# ---------------------------------------------------------------------------
# Main menu
# ---------------------------------------------------------------------------

def _check_dependencies():
    """Check system dependencies and warn about missing ones."""
    missing = []

    # ffmpeg (required for streaming, recording, codec detection)
    try:
        result = subprocess.run(["ffmpeg", "-version"],
                                capture_output=True, text=True, timeout=5)
        ver = result.stdout.split("\n")[0] if result.stdout else "unknown version"
        print(f"  ffmpeg: {ver.split(',')[0]}")
    except (FileNotFoundError, subprocess.TimeoutExpired):
        missing.append("ffmpeg")
        print("  ffmpeg: NOT FOUND (required for streaming and recording)")
        print("    Install: sudo apt install ffmpeg")

    # xdg-open (nice-to-have for opening browser)
    try:
        subprocess.run(["which", "xdg-open"], capture_output=True, timeout=3)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        print("  xdg-open: not found (browser auto-open disabled)")

    if missing:
        print(f"\n  WARNING: Missing required dependencies: {', '.join(missing)}")
        print("  Some features will not work.\n")


def main():
    global _state, _viewer_server, _stream_quality, _mic_gain, _rtsp_transport
    global _rec_codec, _rec_compression, _rec_gpu, _available_gpus
    global REC_CODECS, _DEFAULT_REC_CODEC, _master_pwd

    print("=== NerdCam (Foscam R2) Setup Tool ===\n")
    log.info("=== NerdCam starting ===")
    print(f"  Log file: {LOG_PATH}")

    _check_dependencies()

    # Create centralized state
    state = AppState()
    _state = state
    codecs, default_codec, gpus = detect_codecs()
    state.rec_codecs = codecs
    state.default_rec_codec = default_codec
    state.available_gpus = gpus

    config = _config_mod.load_config(state)

    # Bridge: sync state back to globals until all functions use AppState
    _stream_quality = state.stream_quality
    _mic_gain = state.mic_gain
    _rtsp_transport = state.rtsp_transport
    _rec_codec = state.rec_codec
    _rec_compression = state.rec_compression
    _rec_gpu = state.rec_gpu
    _available_gpus = state.available_gpus
    REC_CODECS = state.rec_codecs
    _DEFAULT_REC_CODEC = state.default_rec_codec
    _master_pwd = state.master_pwd

    cam = config["camera"]
    print(f"\nCamera: {cam['ip']}:{cam['port']} (user: {cam['username']})")

    connected = _cam_ctl.show_device_info(config)
    if not connected:
        print("\nCannot connect. Check IP, port, credentials.")
    else:
        _cam_ctl.sync_time(config, quiet=True)

    first = True
    while True:
        server_status = "RUNNING" if _viewer_server else "stopped"
        quality_label = f"{_stream_quality}/10"

        if not first:
            input("\n  Press Enter to continue...")
        first = False

        cls()
        print(f"--- NerdCam --- [server: {server_status}] [quality: {quality_label}]")
        print("  1. Open web viewer (live stream + camera controls)")
        print("  2. Start stream server (for NerdPudding / VLC / other apps)")
        print("  3. Show stream URLs")
        print("  4. Advanced settings")
        print("  5. Stop server")
        print("  q. Quit")
        choice = input("\nChoice: ").strip().lower()

        if choice == "1":
            open_viewer(config, open_browser=True)
        elif choice == "2":
            open_viewer(config, open_browser=False)
            if _viewer_server:
                print(f"\n  Server running. Stream URLs (no password needed):")
                print(f"    Video only:    http://localhost:8088/api/mjpeg")
                print(f"    Video + audio: http://localhost:8088/api/stream")
                print(f"    Snapshot:      http://localhost:8088/api/snap")
                print(f"\n  Use in NerdPudding or VLC. Server stays running.")
        elif choice == "3":
            _cam_ctl.show_stream_url(config, _viewer_server)
        elif choice == "4":
            _advanced_menu(config)
        elif choice == "5":
            _stop_server()
        elif choice == "q":
            if _patrol.running:
                print("  Stopping patrol...")
                _stop_patrol()
            if _recorder.is_recording:
                print("  Stopping recording...")
                _stop_recording()
            _stop_server() if _viewer_server else None
            break
        else:
            print("  Unknown choice")


def _advanced_menu(config):
    """Advanced settings submenu."""
    global _viewer_server, _stream_quality, _compression_config
    _compression_config = config

    first = True
    while True:
        if not first:
            input("\n  Press Enter to continue...")
        first = False

        cls()
        print("--- Advanced Settings ---")
        print("  CAMERA CONTROL")
        print("    1. PTZ control (pan/tilt/presets/speed)")
        print("    2. Image (brightness/contrast/mirror/flip)")
        print("    3. Infrared / night vision")
        print("    4. Video settings (resolution/framerate/bitrate)")
        print("    5. Motion detection")
        print("    6. Audio settings")
        print("  STREAM")
        print("    7. Stream compression quality")
        print("    8. Watch stream in ffplay")
        print("    9. Test RTSP (OpenCV)")
        print("    0. Snapshot (save JPG)")
        print("  NETWORK")
        print("    w. WiFi status")
        print("    n. Configure WiFi")
        print("    p. Port info")
        print("  AUDIO")
        print("    m. Mic gain")
        print("  OVERLAY")
        print("    o. OSD (timestamp / camera name)")
        print("  RECORDING")
        print("    e. Local recording (start/stop)")
        print("  SYSTEM")
        print("    i. Device info")
        print("    t. Sync time from PC")
        print("    r. Reboot camera")
        print("    x. Raw CGI command")
        print("    c. Update credentials")
        print("    b. Back to main menu")
        choice = input("\nChoice: ").strip().lower()

        if choice == "1":
            ptz_menu(config)
        elif choice == "2":
            _cam_ctl.image_menu(config)
        elif choice == "3":
            _cam_ctl.ir_menu(config)
        elif choice == "4":
            _cam_ctl.video_settings(config)
        elif choice == "5":
            _cam_ctl.motion_detection(config)
        elif choice == "6":
            _cam_ctl.audio_menu(config)
        elif choice == "7":
            _compression_menu()
        elif choice == "8":
            _cam_ctl.watch_stream(config)
        elif choice == "9":
            _cam_ctl.test_rtsp(config)
        elif choice == "0":
            _cam_ctl.take_snapshot(config)
        elif choice == "w":
            _cam_ctl.show_wifi_status(config)
        elif choice == "n":
            _cam_ctl.configure_wifi(config)
        elif choice == "p":
            _cam_ctl.show_ports(config)
        elif choice == "i":
            _cam_ctl.show_device_info(config)
        elif choice == "t":
            _cam_ctl.sync_time(config)
        elif choice == "r":
            _cam_ctl.reboot_camera(config)
        elif choice == "x":
            _cam_ctl.raw_command(config)
        elif choice == "m":
            _mic_gain_menu(config)
        elif choice == "o":
            _cam_ctl.osd_menu(config)
        elif choice == "e":
            _recording_menu(config)
        elif choice == "c":
            _cam_ctl.update_credentials(config, save_config)
        elif choice == "b":
            break
        else:
            print("  Unknown choice")


def _start_recording(config):
    """Bridge: start recording via Recorder instance."""
    return _recorder.start(_rtsp_url(config), _rtsp_transport, _rec_codec,
                           _rec_compression, _rec_gpu, REC_CODECS, _available_gpus)


def _stop_recording():
    """Bridge: stop recording via Recorder instance."""
    return _recorder.stop()


def _recording_status():
    """Bridge: get recording status via Recorder instance."""
    return _recorder.status()


atexit.register(_recorder.cleanup)


# ---------------------------------------------------------------------------
# Patrol (automated PTZ position cycling)
# ---------------------------------------------------------------------------

_patrol = PatrolController()


def _start_patrol(config):
    return _patrol.start(config)


def _stop_patrol():
    return _patrol.stop()


def _get_patrol_status():
    return _patrol.get_status()


def _get_patrol_config(config):
    return get_patrol_config(config)


def _save_patrol_config(config, patrol_cfg):
    save_patrol_config(config, patrol_cfg, save_config)


atexit.register(_patrol.cleanup)


def _recording_menu(config):
    """CLI menu for local recording."""
    global _rec_codec, _rec_compression, _rec_gpu
    print("\n--- Local Recording ---")
    rec_dir = _recorder.output_dir
    print(f"  Save location: {rec_dir}")
    status = _recording_status()
    if status["recording"]:
        print(f"  Currently recording: {status['filename']} ({status['elapsed']}s)")
    else:
        print("  Not recording.")
    codec_desc = REC_CODECS[_rec_codec][1]
    comp_label = COMPRESSION_LABELS.get(_rec_compression, "")
    print(f"  Codec: {_rec_codec} - {codec_desc}")
    print(f"  Compression: {_rec_compression}/10 - {comp_label}")
    if len(_available_gpus) > 1:
        gpu_label = _rec_gpu if _rec_gpu == "auto" else f"GPU {_rec_gpu}: {dict(_available_gpus).get(_rec_gpu, '?')}"
        print(f"  GPU: {gpu_label}")
    opts = "  s=start  x=stop  c=change codec  l=compression level"
    if len(_available_gpus) > 1:
        opts += "  g=select GPU"
    opts += "  q=back"
    print(f"\n  Options:\n  {opts}")

    while True:
        choice = input("  Rec> ").strip().lower()
        if choice == "q":
            break
        elif choice == "s":
            _start_recording(config)
        elif choice == "x":
            _stop_recording()
        elif choice == "c":
            print("\n  Available codecs:")
            for key, (_, desc) in REC_CODECS.items():
                marker = " *" if key == _rec_codec else ""
                print(f"    {key:14s} - {desc}{marker}")
            val = input(f"\n  Codec [{_rec_codec}]: ").strip()
            if not val:
                print("  Unchanged")
            elif val in REC_CODECS:
                _rec_codec = val
                _save_settings(config)
                print(f"  Set to: {val}")
            else:
                print("  Unknown codec")
        elif choice == "l":
            print("\n  Compression level (1-10):")
            for lvl, label in COMPRESSION_LABELS.items():
                marker = " *" if lvl == _rec_compression else ""
                print(f"    {lvl:2d} = {label}{marker}")
            val = input(f"\n  Level [{_rec_compression}]: ").strip()
            if not val:
                print("  Unchanged")
            else:
                try:
                    val = int(val)
                    if 1 <= val <= 10:
                        _rec_compression = val
                        _save_settings(config)
                        print(f"  Set to: {val} - {COMPRESSION_LABELS[val]}")
                    else:
                        print("  Must be 1-10")
                except ValueError:
                    print("  Invalid number")
        elif choice == "g" and len(_available_gpus) > 1:
            print("\n  Available GPUs:")
            marker = " *" if _rec_gpu == "auto" else ""
            print(f"    auto   - Let ffmpeg choose{marker}")
            for idx, name in _available_gpus:
                marker = " *" if _rec_gpu == idx else ""
                print(f"    {idx:6s} - {name}{marker}")
            val = input(f"\n  GPU [{_rec_gpu}]: ").strip()
            if not val:
                print("  Unchanged")
            elif val == "auto" or val in {idx for idx, _ in _available_gpus}:
                _rec_gpu = val
                _save_settings(config)
                print(f"  Set to: {val}")
            else:
                print("  Unknown GPU")
        else:
            print("  Unknown option")


def _mic_gain_menu(config):
    """Set microphone gain for audio stream."""
    global _mic_gain
    print(f"\n--- Mic Gain ---")
    print(f"  Current: {_mic_gain}x")
    print("  Range: 1.0 (quiet) to 5.0 (loud)")
    val = input(f"  New gain [{_mic_gain}]: ").strip()
    if not val:
        print("  Unchanged")
        return
    try:
        val = float(val)
        if 1.0 <= val <= 5.0:
            _mic_gain = round(val, 1)
            _save_settings(config)
            print(f"  Set to {_mic_gain}x")
            if _viewer_server:
                print("  NOTE: Restart audio stream for new gain to take effect.")
        else:
            print("  Must be 1.0-5.0")
    except ValueError:
        print("  Invalid number")


_compression_config = None  # set by _advanced_menu

def _compression_menu():
    """Set stream compression quality."""
    global _stream_quality
    print("\n--- Stream Compression Quality ---")
    print(f"  Current: {_stream_quality}/10")
    print()
    print("  Scale 1-10:")
    print("    10 = best quality (sharpest image, may add slight latency)")
    print("     7 = good quality (default, recommended)")
    print("     5 = medium (balanced)")
    print("     3 = low (fastest encoding, best latency, less detail)")
    print("     1 = lowest (very compressed, minimal latency)")

    val = input(f"\n  Quality (1-10) [{_stream_quality}]: ").strip()
    if not val:
        print("  Unchanged")
        return
    try:
        val = int(val)
        if 1 <= val <= 10:
            _stream_quality = val
            ffmpeg_q = int(2 + (10 - val) * 29 / 9)
            print(f"  Set to {val}/10 (internal ffmpeg q={ffmpeg_q})")
            # Save to encrypted config
            _save_settings(_compression_config)
            if _viewer_server:
                print("  NOTE: Restart the server (stop + start) for changes to take effect.")
        else:
            print("  Must be 1-10")
    except ValueError:
        print("  Invalid number")


if __name__ == "__main__":
    main()

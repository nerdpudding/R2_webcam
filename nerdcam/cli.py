#!/usr/bin/env python3
"""NerdCam - Foscam R2 local control, streaming, and recording tool.

On first run, prompts for camera credentials and WiFi settings,
then encrypts everything into config.enc with a master password.
Run: python3 nerdcam.py
"""

import atexit
import logging
import os
import subprocess

from nerdcam import config as _config_mod
from nerdcam.state import (AppState, PROJECT_DIR, LOG_PATH,
                           COMPRESSION_LABELS)
from nerdcam.streaming import MjpegSource
from nerdcam.recording import Recorder, detect_codecs
from nerdcam.patrol import PatrolController, get_patrol_config, save_patrol_config
from nerdcam import ptz as _ptz_mod
from nerdcam import camera_control as _cam_ctl
from nerdcam.server import NerdCamServer, ServerContext

# Module-level state reference, set in main()
_state = None


def cls():
    """Clear terminal screen."""
    os.system("clear" if os.name != "nt" else "cls")


# Logging: DEBUG+ to file, ERROR+ to terminal (keeps menu clean)
log = logging.getLogger("nerdcam")
log.setLevel(logging.DEBUG)
_log_file = logging.FileHandler(LOG_PATH, encoding="utf-8")
_log_file.setLevel(logging.DEBUG)
_log_file.setFormatter(logging.Formatter(
    "%(asctime)s %(levelname)-5s %(message)s", datefmt="%Y-%m-%d %H:%M:%S"))
_log_console = logging.StreamHandler()
_log_console.setLevel(logging.ERROR)
_log_console.setFormatter(logging.Formatter("  %(levelname)s: %(message)s"))
log.addHandler(_log_file)
log.addHandler(_log_console)

# Shared instances
_mjpeg = MjpegSource()
_server = NerdCamServer()
_server_ctx = None  # ServerContext, initialized in main()
_recorder = Recorder()


def _save_settings():
    """Persist current AppState settings to encrypted config."""
    _config_mod.save_settings(_state)


def save_config(config):
    """Save config dict to encrypted file."""
    _state.config = config
    _config_mod.save_config(_state)


# Camera control functions delegated to nerdcam.camera_control

def _rtsp_url(config) -> str:
    return _cam_ctl._rtsp_url(config)


def ptz_menu(config):
    _ptz_mod.ptz_menu(config, _patrol, save_config)


def _start_server(config):
    """Start the proxy server."""
    if not _server.running:
        _server.start(config, _mjpeg, _server_ctx)
    else:
        print("  Server already running")


def _stop_server():
    if _server.running:
        _server.stop(_mjpeg)
        return True
    _mjpeg.stop()
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
    global _state, _server_ctx

    print("=== NerdCam (Foscam R2) Setup Tool ===\n")
    log.info("=== NerdCam starting ===")
    print(f"  Log file: {LOG_PATH}")

    _check_dependencies()

    # Create centralized state (single source of truth for all settings)
    state = AppState()
    _state = state
    codecs, default_codec, gpus = detect_codecs()
    state.rec_codecs = codecs
    state.default_rec_codec = default_codec
    state.available_gpus = gpus

    config = _config_mod.load_config(state)

    # Build server context: all getters/setters read from AppState directly
    _server_ctx = ServerContext(
        get_stream_quality=lambda: _state.stream_quality,
        get_mic_gain=lambda: _state.mic_gain,
        set_mic_gain=lambda v: setattr(_state, 'mic_gain', v),
        get_rtsp_transport=lambda: _state.rtsp_transport,
        set_rtsp_transport=lambda v: setattr(_state, 'rtsp_transport', v),
        get_rec_codec=lambda: _state.rec_codec,
        set_rec_codec=lambda v: setattr(_state, 'rec_codec', v),
        get_rec_compression=lambda: _state.rec_compression,
        set_rec_compression=lambda v: setattr(_state, 'rec_compression', v),
        get_rec_gpu=lambda: _state.rec_gpu,
        set_rec_gpu=lambda v: setattr(_state, 'rec_gpu', v),
        get_rec_codecs=lambda: _state.rec_codecs,
        get_available_gpus=lambda: _state.available_gpus,
        save_settings=_save_settings,
        start_recording=lambda: _start_recording(config),
        stop_recording=_stop_recording,
        recording_status=_recording_status,
        start_patrol=lambda: _start_patrol(config),
        stop_patrol=_stop_patrol,
        get_patrol_status=_get_patrol_status,
        get_patrol_config=lambda: _get_patrol_config(config),
        save_patrol_config=lambda cfg: _save_patrol_config(config, cfg),
        stop_mjpeg=lambda: _mjpeg.stop(),
        start_mjpeg=lambda cam: _mjpeg.start(cam, _state.stream_quality, _state.rtsp_transport),
    )

    cam = config["camera"]
    print(f"\nCamera: {cam['ip']}:{cam['port']} (user: {cam['username']})")

    connected = _cam_ctl.show_device_info(config)
    if not connected:
        print("\nCannot connect. Check IP, port, credentials.")
    else:
        _cam_ctl.sync_time(config, quiet=True)

    _last_msg = ""

    while True:
        server_status = "RUNNING" if _server.running else "stopped"
        quality_label = f"{_state.stream_quality}/10"

        cls()
        print(f"--- NerdCam --- [server: {server_status}] [quality: {quality_label}]")
        if _last_msg:
            print(f"  {_last_msg}")
            _last_msg = ""
        if _server.running:
            print(f"  Viewer: http://localhost:8088/nerdcam.html")
            print(f"  MJPEG:  http://localhost:8088/api/mjpeg")
            print(f"  fMP4:   http://localhost:8088/api/fmp4")
        print()
        toggle_label = "Stop server" if _server.running else "Start server"
        print(f"  1. {toggle_label}")
        print("  2. Settings")
        print("  q. Quit")
        choice = input("\nChoice: ").strip().lower()

        if choice == "1":
            if _server.running:
                _stop_server()
                _last_msg = "Server stopped"
            else:
                _start_server(config)
                _last_msg = "Server started on port 8088"
        elif choice == "2":
            _settings_menu(config)
        elif choice == "q":
            if _patrol.running:
                _stop_patrol()
            if _recorder.is_recording:
                _stop_recording()
            _stop_server() if _server.running else None
            break


def _settings_menu(config):
    """Settings menu with categorized submenus."""
    while True:
        cls()
        print("--- Settings ---")
        print("  1. Camera (PTZ, image, IR, video, motion, audio)")
        print("  2. Stream (quality, mic gain, snapshot)")
        print("  3. Recording")
        print("  4. Network (WiFi, ports)")
        print("  5. System (device info, time, reboot, credentials)")
        print("  b. Back")
        choice = input("\nChoice: ").strip().lower()

        if choice == "1":
            _camera_menu(config)
        elif choice == "2":
            _stream_menu(config)
        elif choice == "3":
            _recording_menu(config)
        elif choice == "4":
            _network_menu(config)
        elif choice == "5":
            _system_menu(config)
        elif choice == "b":
            break


def _camera_menu(config):
    """Camera control submenu."""
    while True:
        cls()
        print("--- Camera ---")
        print("  1. PTZ control (pan/tilt/presets/patrol)")
        print("  2. Image (brightness/contrast/mirror/flip)")
        print("  3. Infrared / night vision")
        print("  4. Video encoding (resolution/framerate/bitrate/GOP)")
        print("  5. Motion detection")
        print("  6. Audio settings")
        print("  7. OSD overlay (timestamp/name)")
        print("  b. Back")
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
            _cam_ctl.osd_menu(config)
        elif choice == "b":
            break


def _stream_menu(config):
    """Stream settings submenu."""
    while True:
        cls()
        print(f"--- Stream --- [quality: {_state.stream_quality}/10] [mic gain: {_state.mic_gain}x]")
        print("  1. Stream compression quality")
        print("  2. Mic gain")
        print("  3. Take snapshot")
        print("  4. Watch stream in ffplay")
        print("  5. Test RTSP (OpenCV)")
        print("  6. Show stream URLs")
        print("  b. Back")
        choice = input("\nChoice: ").strip().lower()

        if choice == "1":
            _compression_menu(config)
        elif choice == "2":
            _mic_gain_menu(config)
        elif choice == "3":
            _cam_ctl.take_snapshot(config)
            input("\n  Enter to continue...")
        elif choice == "4":
            _cam_ctl.watch_stream(config)
        elif choice == "5":
            _cam_ctl.test_rtsp(config)
            input("\n  Enter to continue...")
        elif choice == "6":
            _cam_ctl.show_stream_url(config, _server.running)
            input("\n  Enter to continue...")
        elif choice == "b":
            break


def _network_menu(config):
    """Network submenu."""
    while True:
        cls()
        print("--- Network ---")
        print("  1. WiFi status")
        print("  2. Configure WiFi")
        print("  3. Port info")
        print("  b. Back")
        choice = input("\nChoice: ").strip().lower()

        if choice == "1":
            _cam_ctl.show_wifi_status(config)
            input("\n  Enter to continue...")
        elif choice == "2":
            _cam_ctl.configure_wifi(config)
        elif choice == "3":
            _cam_ctl.show_ports(config)
            input("\n  Enter to continue...")
        elif choice == "b":
            break


def _system_menu(config):
    """System submenu."""
    while True:
        cls()
        print("--- System ---")
        print("  1. Device info")
        print("  2. Sync time from PC")
        print("  3. Reboot camera")
        print("  4. Raw CGI command")
        print("  5. Update credentials")
        print("  b. Back")
        choice = input("\nChoice: ").strip().lower()

        if choice == "1":
            _cam_ctl.show_device_info(config)
            input("\n  Enter to continue...")
        elif choice == "2":
            _cam_ctl.sync_time(config)
            input("\n  Enter to continue...")
        elif choice == "3":
            _cam_ctl.reboot_camera(config)
        elif choice == "4":
            _cam_ctl.raw_command(config)
            input("\n  Enter to continue...")
        elif choice == "5":
            _cam_ctl.update_credentials(config, save_config)
        elif choice == "b":
            break


def _start_recording(config):
    """Start recording via Recorder instance."""
    return _recorder.start(_rtsp_url(config), _state.rtsp_transport, _state.rec_codec,
                           _state.rec_compression, _state.rec_gpu,
                           _state.rec_codecs, _state.available_gpus)


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
    cls()
    print("\n--- Local Recording ---")
    rec_dir = _recorder.output_dir
    print(f"  Save location: {rec_dir}")
    status = _recording_status()
    if status["recording"]:
        print(f"  Currently recording: {status['filename']} ({status['elapsed']}s)")
    else:
        print("  Not recording.")
    codec_desc = _state.rec_codecs[_state.rec_codec][1]
    comp_label = COMPRESSION_LABELS.get(_state.rec_compression, "")
    print(f"  Codec: {_state.rec_codec} - {codec_desc}")
    print(f"  Compression: {_state.rec_compression}/10 - {comp_label}")
    if len(_state.available_gpus) > 1:
        gpu_label = _state.rec_gpu if _state.rec_gpu == "auto" else f"GPU {_state.rec_gpu}: {dict(_state.available_gpus).get(_state.rec_gpu, '?')}"
        print(f"  GPU: {gpu_label}")
    opts = "  s=start  x=stop  c=change codec  l=compression level"
    if len(_state.available_gpus) > 1:
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
            for key, (_, desc) in _state.rec_codecs.items():
                marker = " *" if key == _state.rec_codec else ""
                print(f"    {key:14s} - {desc}{marker}")
            val = input(f"\n  Codec [{_state.rec_codec}]: ").strip()
            if not val:
                print("  Unchanged")
            elif val in _state.rec_codecs:
                _state.rec_codec = val
                _save_settings()
                print(f"  Set to: {val}")
            else:
                print("  Unknown codec")
        elif choice == "l":
            print("\n  Compression level (1-10):")
            for lvl, label in COMPRESSION_LABELS.items():
                marker = " *" if lvl == _state.rec_compression else ""
                print(f"    {lvl:2d} = {label}{marker}")
            val = input(f"\n  Level [{_state.rec_compression}]: ").strip()
            if not val:
                print("  Unchanged")
            else:
                try:
                    val = int(val)
                    if 1 <= val <= 10:
                        _state.rec_compression = val
                        _save_settings()
                        print(f"  Set to: {val} - {COMPRESSION_LABELS[val]}")
                    else:
                        print("  Must be 1-10")
                except ValueError:
                    print("  Invalid number")
        elif choice == "g" and len(_state.available_gpus) > 1:
            print("\n  Available GPUs:")
            marker = " *" if _state.rec_gpu == "auto" else ""
            print(f"    auto   - Let ffmpeg choose{marker}")
            for idx, name in _state.available_gpus:
                marker = " *" if _state.rec_gpu == idx else ""
                print(f"    {idx:6s} - {name}{marker}")
            val = input(f"\n  GPU [{_state.rec_gpu}]: ").strip()
            if not val:
                print("  Unchanged")
            elif val == "auto" or val in {idx for idx, _ in _state.available_gpus}:
                _state.rec_gpu = val
                _save_settings()
                print(f"  Set to: {val}")
            else:
                print("  Unknown GPU")
        else:
            print("  Unknown option")


def _mic_gain_menu(config):
    """Set microphone gain for audio stream."""
    cls()
    print("--- Mic Gain ---")
    print(f"  Current: {_state.mic_gain}x")
    print("  Range: 1.0 (quiet) to 5.0 (loud)")
    val = input(f"  New gain [{_state.mic_gain}]: ").strip()
    if not val:
        print("  Unchanged")
        return
    try:
        val = float(val)
        if 1.0 <= val <= 5.0:
            _state.mic_gain = round(val, 1)
            _save_settings()
            print(f"  Set to {_state.mic_gain}x")
            if _server.running:
                print("  NOTE: Restart audio stream for new gain to take effect.")
        else:
            print("  Must be 1.0-5.0")
    except ValueError:
        print("  Invalid number")


def _compression_menu(config):
    """Set stream compression quality."""
    cls()
    print("--- Stream Compression Quality ---")
    print(f"  Current: {_state.stream_quality}/10")
    print()
    print("  Scale 1-10:")
    print("    10 = best quality (sharpest image, may add slight latency)")
    print("     7 = good quality (default, recommended)")
    print("     5 = medium (balanced)")
    print("     3 = low (fastest encoding, best latency, less detail)")
    print("     1 = lowest (very compressed, minimal latency)")

    val = input(f"\n  Quality (1-10) [{_state.stream_quality}]: ").strip()
    if not val:
        print("  Unchanged")
        return
    try:
        val = int(val)
        if 1 <= val <= 10:
            _state.stream_quality = val
            ffmpeg_q = int(2 + (10 - val) * 29 / 9)
            print(f"  Set to {val}/10 (internal ffmpeg q={ffmpeg_q})")
            _save_settings()
            if _server.running:
                print("  NOTE: Restart the server (stop + start) for changes to take effect.")
        else:
            print("  Must be 1-10")
    except ValueError:
        print("  Invalid number")


if __name__ == "__main__":
    main()

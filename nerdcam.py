#!/usr/bin/env python3
"""NerdCam - Foscam R2 local control, streaming, and recording tool.

On first run, prompts for camera credentials and WiFi settings,
then encrypts everything into config.enc with a master password.
Run: python3 nerdcam.py
"""

import atexit
import base64
import hashlib
import json
import logging
import os
import subprocess
import sys
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
import getpass
import threading
import time


def cls():
    """Clear terminal screen."""
    os.system("clear" if os.name != "nt" else "cls")


SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_PATH = os.path.join(SCRIPT_DIR, "nerdcam.log")

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
CONFIG_PATH = os.path.join(SCRIPT_DIR, "config.enc")
CONFIG_PLAIN = os.path.join(SCRIPT_DIR, "config.json")

_viewer_server = None
_stream_quality = 7  # 1-10 scale: 10=best quality, 1=lowest. Maps to ffmpeg -q:v internally.
_mic_gain = 3.0  # Audio volume multiplier for mic stream (1.0-5.0)
_rtsp_transport = "udp"  # "udp" (smooth/low latency) or "tcp" (reliable/no packet loss)

# Shared MJPEG source: one ffmpeg process, multiple browser clients
# Uses simple polling (no Condition/Lock) - CPython's GIL makes single
# variable reads/writes atomic, and there's only one writer thread.
_mjpeg_frame = None      # latest JPEG frame bytes (written by reader thread)
_mjpeg_frame_id = 0      # incremented on each new frame (written by reader thread)
_mjpeg_proc = None        # shared ffmpeg process
_mjpeg_quality = None     # quality level when source was started
_mjpeg_last_frame_time = 0  # time.time() when last frame was received
_MJPEG_STALE_SECONDS = 5    # force-restart if no frame for this long


def _start_mjpeg_source(cam):
    """Start shared ffmpeg MJPEG source if not already running."""
    global _mjpeg_proc, _mjpeg_frame, _mjpeg_frame_id, _mjpeg_quality, _mjpeg_last_frame_time
    if _mjpeg_proc:
        if _mjpeg_proc.poll() is None:
            # Process alive — but is it actually producing frames?
            frame_age = time.time() - _mjpeg_last_frame_time if _mjpeg_last_frame_time else 0
            if _mjpeg_quality == _stream_quality and frame_age < _MJPEG_STALE_SECONDS:
                return  # alive and producing frames, nothing to do
            # Stale or quality changed — kill and restart
            log.warning("MJPEG source stale (%.1fs no frames), restarting ffmpeg", frame_age)
            _stop_mjpeg_source()
            time.sleep(0.5)
        else:
            log.info("MJPEG ffmpeg process died (exit=%s), restarting", _mjpeg_proc.returncode)
            try:
                _mjpeg_proc.kill()
            except Exception:
                pass
            _mjpeg_proc = None
            time.sleep(0.5)

    rtsp_port = cam.get("port", 88)
    rtsp_url = (f"rtsp://{cam['username']}:{cam['password']}"
                f"@{cam['ip']}:{rtsp_port}/videoMain")
    _mjpeg_quality = _stream_quality
    # TCP needs larger probesize to find video track in interleaved data.
    # UDP: 32 bytes is too small and unreliable — 32768 is still tiny but
    # reliably captures the RTSP SDP which describes all tracks.
    probe = "500000" if _rtsp_transport == "tcp" else "32768"
    analyze = "500000" if _rtsp_transport == "tcp" else "0"
    log.info("Starting MJPEG source (quality=%d, transport=%s, rtsp=%s:%s)", _stream_quality, _rtsp_transport, cam['ip'], rtsp_port)
    proc = subprocess.Popen(
        ["ffmpeg",
         "-fflags", "+nobuffer+flush_packets",
         "-flags", "low_delay",
         "-probesize", probe,
         "-analyzeduration", analyze,
         "-rtsp_transport", _rtsp_transport,
         "-i", rtsp_url,
         "-f", "mjpeg",
         "-q:v", str(int(2 + (10 - _stream_quality) * 29 / 9)),
         "-r", "25",
         "-an",
         "-threads", "1",
         "-flush_packets", "1",
         "pipe:1"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE
    )
    _mjpeg_proc = proc

    _mjpeg_last_frame_time = time.time()  # reset on new source start

    def _mjpeg_reader(p):
        """Read JPEG frames from ffmpeg stdout into shared buffer."""
        global _mjpeg_frame, _mjpeg_frame_id, _mjpeg_last_frame_time
        buf = b""
        frame_count = 0
        try:
            while True:
                chunk = p.stdout.read(4096)
                if not chunk:
                    break
                buf += chunk
                while True:
                    start = buf.find(b"\xff\xd8")
                    end = buf.find(b"\xff\xd9", start + 2) if start >= 0 else -1
                    if start < 0 or end < 0:
                        break
                    jpeg = buf[start:end + 2]
                    buf = buf[end + 2:]
                    _mjpeg_frame = jpeg
                    _mjpeg_frame_id += 1
                    _mjpeg_last_frame_time = time.time()
                    frame_count += 1
                    if frame_count == 1:
                        log.info("MJPEG source: first frame received (%d bytes)", len(jpeg))
        except Exception as e:
            log.error("MJPEG reader exception: %s", e)
        log.info("MJPEG reader stopped after %d frames", frame_count)
        if p.poll() and p.stderr:
            try:
                err = p.stderr.read().decode(errors="replace").strip()
                if err:
                    lines = err.splitlines()[-5:]
                    log.warning("MJPEG ffmpeg stderr:\n  %s", "\n  ".join(lines))
            except Exception:
                pass

    threading.Thread(target=_mjpeg_reader, args=(proc,), daemon=True).start()


def _stop_mjpeg_source():
    """Stop the shared MJPEG source."""
    global _mjpeg_proc, _mjpeg_frame
    if _mjpeg_proc:
        log.info("Stopping MJPEG source (pid=%s)", _mjpeg_proc.pid)
        try:
            _mjpeg_proc.kill()
        except Exception:
            pass
        _mjpeg_proc = None
    _mjpeg_frame = None

_recording_proc = None
_recording_info = None  # {"filename": str, "started": float}
_max_record_seconds = 3600
_rec_codec = None       # codec key, set by _detect_rec_codecs()
_rec_compression = 5    # 1-10: 1=best quality/largest, 10=max compression/smallest
_rec_gpu = "auto"       # "auto" or GPU index ("0", "1", etc.) for NVENC
_available_gpus = []    # detected NVIDIA GPUs: [(index, name), ...]

# Codec definitions: (key, encoder_name, description, required_ffmpeg_encoder)
# encoder_name=None means -c:v copy (no re-encode, compression level ignored)
_ALL_REC_CODECS = [
    ("nvenc_av1",  "av1_nvenc",  "NVENC AV1 (GPU, best compression)", "av1_nvenc"),
    ("nvenc_h265", "hevc_nvenc", "NVENC H.265 (GPU, recommended)",    "hevc_nvenc"),
    ("nvenc_h264", "h264_nvenc", "NVENC H.264 (GPU, most compatible)","h264_nvenc"),
    ("sw_h265",    "libx265",    "Software H.265 (CPU)",              "libx265"),
    ("sw_h264",    "libx264",    "Software H.264 (CPU, compatible)",  "libx264"),
    ("original",   None,         "Original (no re-encode)",           None),
]

REC_CODECS = {}  # populated by _detect_rec_codecs(): key -> (encoder_name, description)
_DEFAULT_REC_CODEC = "original"

# Quality ranges per encoder: maps compression 1-10 to CQ/CRF values
# Low number = better quality / bigger files, high = more compression / smaller
_QUALITY_RANGES = {
    "av1_nvenc":  (22, 48),   # NVENC AV1: CQ 22 (studio) to 48 (tiny)
    "hevc_nvenc": (18, 42),   # NVENC HEVC: CQ 18 to 42
    "h264_nvenc": (16, 38),   # NVENC H.264: CQ 16 to 38
    "libx265":    (18, 40),   # Software x265: CRF 18 to 40
    "libx264":    (16, 38),   # Software x264: CRF 16 to 38
}

# Compression level labels (shown in UI)
COMPRESSION_LABELS = {
    1: "Studio (largest files)",
    2: "Very high quality",
    3: "High quality",
    4: "Good quality",
    5: "Balanced (default)",
    6: "Moderate compression",
    7: "Compact",
    8: "Small files",
    9: "Very small files",
    10: "Maximum compression",
}


def _rec_video_args():
    """Build ffmpeg video args from current codec + compression level."""
    codec = REC_CODECS.get(_rec_codec)
    if not codec or codec[0] is None:
        return ["-c:v", "copy"]

    encoder = codec[0]
    lo, hi = _QUALITY_RANGES.get(encoder, (18, 42))
    qval = int(lo + (_rec_compression - 1) * (hi - lo) / 9)

    if encoder.endswith("_nvenc"):
        args = ["-c:v", encoder]
        if _rec_gpu != "auto" and len(_available_gpus) > 1:
            args += ["-gpu", _rec_gpu]
        args += ["-cq", str(qval), "-preset", "p4"]
        return args
    else:
        return ["-c:v", encoder, "-crf", str(qval), "-preset", "fast"]


def _detect_rec_codecs():
    """Probe ffmpeg for available encoders and GPUs. Called once at startup."""
    global REC_CODECS, _DEFAULT_REC_CODEC, _available_gpus
    available = set()
    try:
        out = subprocess.run(["ffmpeg", "-encoders"],
                             capture_output=True, text=True, timeout=5)
        for line in out.stdout.splitlines():
            parts = line.split()
            if len(parts) >= 2:
                available.add(parts[1])
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass

    # Detect NVIDIA GPUs
    _available_gpus = []
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=index,name", "--format=csv,noheader"],
            capture_output=True, text=True, timeout=5)
        for line in out.stdout.strip().splitlines():
            parts = [p.strip() for p in line.split(",", 1)]
            if len(parts) == 2:
                _available_gpus.append((parts[0], parts[1]))
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass

    REC_CODECS = {}
    for key, encoder, desc, required in _ALL_REC_CODECS:
        if required is None or required in available:
            REC_CODECS[key] = (encoder, desc)

    for pref in ["nvenc_h265", "nvenc_h264", "sw_h265", "sw_h264", "original"]:
        if pref in REC_CODECS:
            _DEFAULT_REC_CODEC = pref
            break

    if not REC_CODECS:
        REC_CODECS["original"] = (None, "Original (no re-encode)")
        _DEFAULT_REC_CODEC = "original"

    gpu = sum(1 for k in REC_CODECS if k.startswith("nvenc_"))
    sw = sum(1 for k in REC_CODECS if k.startswith("sw_"))
    info = []
    if gpu:
        info.append(f"{gpu} GPU")
        if len(_available_gpus) > 1:
            names = ", ".join(f"{i}:{n}" for i, n in _available_gpus)
            info.append(f"GPUs: [{names}]")
    if sw: info.append(f"{sw} software")
    if "original" in REC_CODECS: info.append("passthrough")
    print(f"  Recording codecs: {', '.join(info)} (default: {_DEFAULT_REC_CODEC})")

# Master password for this session (set once at startup)
_master_pwd = None


# ---------------------------------------------------------------------------
# Encryption (XOR stream cipher with PBKDF2 key derivation)
# ---------------------------------------------------------------------------

def _derive_key(master: str, salt: bytes) -> bytes:
    """Derive a 32-byte key from master password using PBKDF2."""
    return hashlib.pbkdf2_hmac("sha256", master.encode(), salt, 100_000)


def _xor_bytes(data: bytes, key: bytes) -> bytes:
    """XOR data with repeating key."""
    return bytes(d ^ key[i % len(key)] for i, d in enumerate(data))


def encrypt_config(config: dict, master: str) -> None:
    """Encrypt config dict and save to config.enc."""
    salt = os.urandom(16)
    key = _derive_key(master, salt)
    plaintext = json.dumps(config, indent=4).encode()
    ciphertext = _xor_bytes(plaintext, key)
    # Format: base64(salt + ciphertext)
    payload = base64.b64encode(salt + ciphertext).decode()
    with open(CONFIG_PATH, "w") as f:
        f.write(payload)
    os.chmod(CONFIG_PATH, 0o600)
    # Remove plaintext config if it exists
    if os.path.exists(CONFIG_PLAIN):
        os.remove(CONFIG_PLAIN)
        print("  Removed plaintext config.json")
    print(f"  Encrypted config saved to {CONFIG_PATH}")


def decrypt_config(master: str) -> dict:
    """Decrypt config.enc and return config dict."""
    with open(CONFIG_PATH) as f:
        payload = f.read()
    raw = base64.b64decode(payload)
    salt = raw[:16]
    ciphertext = raw[16:]
    key = _derive_key(master, salt)
    plaintext = _xor_bytes(ciphertext, key)
    try:
        return json.loads(plaintext.decode())
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

def _onboarding_scan_wifi(config):
    """Try to scan WiFi during onboarding. Silent fail if camera unreachable."""
    cam = config["camera"]
    if not cam.get("ip") or not cam.get("password"):
        print("  (skipped -- need camera IP and password first)")
        return
    try:
        base = f"http://{cam['ip']}:{cam['port']}/cgi-bin/CGIProxy.fcgi"
        params = urllib.parse.urlencode({
            "cmd": "refreshWifiList", "usr": cam["username"], "pwd": cam["password"]
        })
        urllib.request.urlopen(f"{base}?{params}", timeout=5)
        time.sleep(3)
        params = urllib.parse.urlencode({
            "cmd": "getWifiList", "usr": cam["username"], "pwd": cam["password"]
        })
        with urllib.request.urlopen(f"{base}?{params}", timeout=5) as resp:
            root = ET.fromstring(resp.read().decode())
        data = {child.tag: (child.text or "") for child in root}
        count = int(data.get("totalCnt", 0))
        if count > 0:
            enc_map = {"0": "Open", "1": "WEP", "2": "WPA", "3": "WPA2", "4": "WPA/WPA2"}
            print(f"  Found {count} networks:")
            for i in range(count):
                raw = data.get(f"ap{i}", "")
                if not raw:
                    continue
                parts = urllib.parse.unquote(raw).split("+")
                if len(parts) >= 5:
                    enc = enc_map.get(parts[4], f"type={parts[4]}")
                    print(f"    {parts[0]}  signal={parts[2]}%  enc={enc}")
        else:
            print("  No networks found.")
    except Exception:
        print("  (could not reach camera for WiFi scan)")


def _load_settings(config):
    """Restore app settings from config."""
    global _stream_quality, _mic_gain, _rec_codec, _rec_compression, _rec_gpu, _rtsp_transport
    settings = config.get("settings", {})
    _stream_quality = settings.get("stream_quality", 7)
    _mic_gain = settings.get("mic_gain", 3.0)
    rt = settings.get("rtsp_transport", "udp")
    _rtsp_transport = rt if rt in ("udp", "tcp") else "udp"
    rc = settings.get("rec_codec", _DEFAULT_REC_CODEC)
    _rec_codec = rc if rc in REC_CODECS else _DEFAULT_REC_CODEC
    comp = settings.get("rec_compression", 5)
    _rec_compression = max(1, min(10, int(comp)))
    gpu = settings.get("rec_gpu", "auto")
    valid_gpus = {"auto"} | {idx for idx, _ in _available_gpus}
    _rec_gpu = gpu if gpu in valid_gpus else "auto"


def _save_settings(config):
    """Save app settings into config and encrypt."""
    if "settings" not in config:
        config["settings"] = {}
    config["settings"]["stream_quality"] = _stream_quality
    config["settings"]["mic_gain"] = _mic_gain
    config["settings"]["rec_codec"] = _rec_codec
    config["settings"]["rec_compression"] = _rec_compression
    config["settings"]["rec_gpu"] = _rec_gpu
    config["settings"]["rtsp_transport"] = _rtsp_transport
    save_config(config)


def load_config() -> dict:
    """Load config: try encrypted first, then plaintext, then create new."""
    global _master_pwd

    # Try encrypted config
    if os.path.exists(CONFIG_PATH):
        print("  Encrypted config found.")
        for attempt in range(3):
            _master_pwd = getpass.getpass("  Master password: ")
            config = decrypt_config(_master_pwd)
            if config is not None:
                print("  Config decrypted OK.")
                # Restore saved settings
                _load_settings(config)
                return config
            print("  Wrong master password, try again.")
        print("  Too many failed attempts.")
        sys.exit(1)

    # Try plaintext config (first run or migration)
    if os.path.exists(CONFIG_PLAIN):
        print("  Plaintext config.json found. Migrating to encrypted...")
        with open(CONFIG_PLAIN) as f:
            config = json.load(f)
    else:
        print("  No config found. Setting up...")
        config = {
            "camera": {"ip": "", "port": 88, "username": "", "password": ""},
            "wifi": {"ssid": "", "password": ""},
        }

    # Interactive onboarding
    print("\n  === Camera Setup ===")
    print("  Press Enter to keep the value in [brackets].\n")

    ip = config["camera"].get("ip", "")
    new_ip = input(f"  Camera IP address [{ip or 'none'}]: ").strip()
    if new_ip:
        config["camera"]["ip"] = new_ip

    port = config["camera"].get("port", 88)
    new_port = input(f"  Camera HTTP port [{port}]: ").strip()
    if new_port:
        config["camera"]["port"] = int(new_port)

    user = config["camera"].get("username", "")
    new_user = input(f"  Camera username [{user or 'none'}]: ").strip()
    if new_user:
        config["camera"]["username"] = new_user

    if not config["camera"].get("password"):
        config["camera"]["password"] = getpass.getpass("  Camera password: ")
    else:
        new_pwd = getpass.getpass("  Camera password [****]: ")
        if new_pwd:
            config["camera"]["password"] = new_pwd

    # Scan for WiFi networks if camera is reachable
    print("\n  === WiFi Setup ===")
    print("  Scanning for WiFi networks via the camera...")
    _onboarding_scan_wifi(config)

    ssid = config["wifi"].get("ssid", "")
    new_ssid = input(f"  WiFi SSID [{ssid or 'none'}]: ").strip()
    if new_ssid:
        config["wifi"]["ssid"] = new_ssid

    if not config["wifi"].get("password"):
        config["wifi"]["password"] = getpass.getpass("  WiFi password: ")
    else:
        new_pwd = getpass.getpass("  WiFi password [****]: ")
        if new_pwd:
            config["wifi"]["password"] = new_pwd

    # Set master password and encrypt
    print("\n  Choose a master password to encrypt your config.")
    print("  You'll need this every time you start the app.")
    while True:
        _master_pwd = getpass.getpass("  New master password: ")
        confirm = getpass.getpass("  Confirm master password: ")
        if _master_pwd == confirm:
            break
        print("  Passwords don't match, try again.")

    encrypt_config(config, _master_pwd)
    return config


def save_config(config: dict) -> None:
    """Save config encrypted."""
    global _master_pwd
    if _master_pwd:
        encrypt_config(config, _master_pwd)
    else:
        print("  ERROR: no master password set, cannot save.")


# ---------------------------------------------------------------------------
# CGI helpers
# ---------------------------------------------------------------------------

def cgi(cmd: str, config: dict, **params) -> dict:
    """Send a CGI command and return parsed XML as dict."""
    cam = config["camera"]
    base = f"http://{cam['ip']}:{cam['port']}/cgi-bin/CGIProxy.fcgi"
    params["cmd"] = cmd
    params["usr"] = cam["username"]
    params["pwd"] = cam["password"]
    url = f"{base}?{urllib.parse.urlencode(params)}"
    try:
        with urllib.request.urlopen(url, timeout=10) as resp:
            xml_text = resp.read().decode()
    except Exception as e:
        print(f"  ERROR: {e}")
        return {}
    root = ET.fromstring(xml_text)
    return {child.tag: (child.text or "") for child in root}


def ok(data: dict, label: str) -> bool:
    """Check CGI result code."""
    code = data.get("result", "-1")
    if code == "0":
        print(f"  OK: {label}")
        return True
    elif code == "-2":
        print(f"  FAILED: {label} -- bad credentials")
        return False
    else:
        print(f"  FAILED: {label} -- result code: {code}")
        return False


def show_dict(data: dict, skip=("result",)):
    """Pretty-print a CGI response dict."""
    for key, val in data.items():
        if key not in skip:
            print(f"  {key}: {val}")


# ---------------------------------------------------------------------------
# Device & network
# ---------------------------------------------------------------------------

def show_device_info(config):
    print("\n--- Device Info ---")
    data = cgi("getDevInfo", config)
    if not ok(data, "getDevInfo"):
        return False
    for key in ["devName", "productName", "mac", "firmwareVer", "hardwareVer"]:
        print(f"  {key}: {data.get(key, '?')}")
    return True


def show_wifi_status(config):
    print("\n--- WiFi Status ---")
    data = cgi("getWifiConfig", config)
    if not ok(data, "getWifiConfig"):
        return
    for key in ["isEnable", "isUseWifi", "isConnected", "connectedAP",
                 "ssid", "encryptType", "authMode"]:
        print(f"  {key}: {data.get(key, '?')}")


def scan_wifi(config):
    print("\n--- Scanning WiFi ---")
    cgi("refreshWifiList", config)
    print("  Waiting 4 seconds for scan...")
    time.sleep(4)
    data = cgi("getWifiList", config)
    if not ok(data, "getWifiList"):
        return
    count = int(data.get("totalCnt", 0))
    print(f"  Found {count} networks:")
    enc_map = {"0": "Open", "1": "WEP", "2": "WPA", "3": "WPA2", "4": "WPA/WPA2"}
    for i in range(count):
        raw = data.get(f"ap{i}", "")
        if not raw:
            continue
        parts = urllib.parse.unquote(raw).split("+")
        if len(parts) >= 5:
            enc = enc_map.get(parts[4], f"type={parts[4]}")
            print(f"    {i}: {parts[0]}  signal={parts[2]}%  enc={enc}")
        else:
            print(f"    {i}: {raw}")


def configure_wifi(config):
    print("\n--- Configure WiFi ---")
    wifi = config["wifi"]
    ssid = wifi.get("ssid", "")
    psk = wifi.get("password", "")

    print(f"  Using SSID: {ssid}")
    if not psk:
        psk = getpass.getpass("  WiFi password: ")

    confirm = input("  Apply WiFi settings? (y/n): ").strip().lower()
    if confirm != "y":
        print("  Cancelled")
        return

    print("  Setting WiFi: WPA2-PSK...")
    data = cgi("setWifiSetting", config,
               isEnable="1", isUseWifi="1",
               ssid=ssid, netType="0",
               encryptType="4", psk=psk, authMode="2")

    if not ok(data, "setWifiSetting"):
        print("  Trying setWifiSettingNew...")
        data = cgi("setWifiSettingNew", config,
                    isEnable="1", isUseWifi="1",
                    ssid=ssid, netType="0",
                    encryptType="4", psk=psk, authMode="2")
        ok(data, "setWifiSettingNew")

    print("  Waiting 5 seconds for WiFi to connect...")
    time.sleep(5)
    show_wifi_status(config)


def show_ports(config):
    print("\n--- Port Info ---")
    data = cgi("getPortInfo", config)
    if not ok(data, "getPortInfo"):
        return
    for key in ["webPort", "httpsPort", "mediaPort", "onvifPort", "rtspPort"]:
        print(f"  {key}: {data.get(key, '?')}")


def reboot_camera(config):
    print("\n--- Reboot Camera ---")
    confirm = input("  Reboot the camera? (y/n): ").strip().lower()
    if confirm != "y":
        print("  Cancelled")
        return
    data = cgi("rebootSystem", config)
    ok(data, "rebootSystem")
    print("  Camera is rebooting. Wait ~60 seconds.")


def sync_time(config):
    """Sync camera time from this computer's clock."""
    import datetime as _dt
    import time as _time

    print("\n--- Sync Time ---")
    now = _dt.datetime.now()
    utc_offset = int(now.astimezone().utcoffset().total_seconds())
    sign = "+" if utc_offset >= 0 else ""
    print(f"  PC time: {now.strftime('%Y-%m-%d %H:%M:%S')} (UTC{sign}{utc_offset // 3600})")

    data = cgi("setSystemTime", config,
               timeSource="1",
               ntpServer="",
               dateFormat="0",
               timeFormat="1",
               timeZone=str(utc_offset),
               isDst="0",
               dst="0",
               year=str(now.year),
               mon=str(now.month),
               day=str(now.day),
               hour=str(now.hour),
               minute=str(now.minute),
               sec=str(now.second))
    if ok(data, "setSystemTime"):
        print("  Camera time synced.")
    # Verify
    data = cgi("getSystemTime", config)
    if data:
        print(f"  Camera reports: {data.get('year')}-{data.get('mon')}-{data.get('day')} "
              f"{data.get('hour')}:{data.get('minute')}:{data.get('sec')}")


# ---------------------------------------------------------------------------
# RTSP / streaming
# ---------------------------------------------------------------------------

def _rtsp_url(config) -> str:
    cam = config["camera"]
    data = cgi("getPortInfo", config)
    rtsp_port = data.get("rtspPort", str(cam["port"]))
    return f"rtsp://{cam['username']}:{cam['password']}@{cam['ip']}:{rtsp_port}/videoMain"


def test_rtsp(config):
    print("\n--- RTSP Test ---")
    cam = config["camera"]
    url = _rtsp_url(config)
    print(f"  RTSP URL: rtsp://{cam['username']}:****@{cam['ip']}:88/videoMain")
    try:
        import cv2
        print("  Attempting OpenCV capture...")
        cap = cv2.VideoCapture(url)
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        ret, frame = cap.read()
        if ret:
            print(f"  SUCCESS: frame {frame.shape[1]}x{frame.shape[0]}")
        else:
            print("  FAILED: could not read frame")
        cap.release()
    except ImportError:
        print("  OpenCV not available.")


def watch_stream(config):
    """Open the live stream in ffplay or VLC."""
    print("\n--- Watch Stream ---")
    url = _rtsp_url(config)
    print("  Opening stream (close the player window to return to menu)")
    for player in ["ffplay", "vlc"]:
        try:
            subprocess.Popen([player, url],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            print(f"  Launched {player}")
            return
        except FileNotFoundError:
            continue
    print("  ERROR: neither ffplay nor vlc found. Install ffmpeg or VLC.")


def show_stream_url(config):
    print("\n--- Stream URLs (no credentials needed) ---")
    print("  Start viewer server first (option v)!\n")
    print("  Video only (MJPEG, for browser/OpenCV/NerdPudding):")
    print("    http://localhost:8088/api/mjpeg")
    print()
    print("  Video + Audio (MPEG-TS, for VLC/ffplay):")
    print("    http://localhost:8088/api/stream")
    print()
    print("  Single snapshot (JPEG):")
    print("    http://localhost:8088/api/snap")
    print()
    print("  Examples:")
    print("    vlc http://localhost:8088/api/stream")
    print("    ffplay http://localhost:8088/api/mjpeg")
    print("    cv2.VideoCapture('http://localhost:8088/api/mjpeg')")
    if _viewer_server is None:
        print("\n  WARNING: Server not running! Start it with option v.")


# ---------------------------------------------------------------------------
# PTZ controls
# ---------------------------------------------------------------------------

def ptz_menu(config):
    print("\n--- PTZ Control ---")
    print("  Movement:  7=UL  8=U  9=UR")
    print("             4=L   5=H  6=R")
    print("             1=DL  2=D  3=DR")
    print("  Speed:     s=set speed")
    print("  Presets:   p=list  g=goto  a=add  d=delete")
    print("  Patrol:    t=start  x=stop  c=configure")
    print("  q=back")

    status = _get_patrol_status()
    if status["running"]:
        print(f"  Patrol: RUNNING (pos={status['current_pos']}, cycle={status['cycle']})")

    ptz_cmds = {
        "7": "ptzMoveTopLeft", "8": "ptzMoveUp", "9": "ptzMoveTopRight",
        "4": "ptzMoveLeft",    "5": "ptzReset",  "6": "ptzMoveRight",
        "1": "ptzMoveBottomLeft", "2": "ptzMoveDown", "3": "ptzMoveBottomRight",
    }

    while True:
        choice = input("  PTZ> ").strip().lower()
        if choice == "q":
            break
        elif choice in ptz_cmds:
            if _patrol_running:
                _stop_patrol()
                print("  Patrol auto-stopped (manual PTZ)")
            cgi(ptz_cmds[choice], config)
            time.sleep(0.5)
            cgi("ptzStopRun", config)
        elif choice == "s":
            ptz_set_speed(config)
        elif choice == "p":
            ptz_list_presets(config)
        elif choice == "g":
            if _patrol_running:
                _stop_patrol()
                print("  Patrol auto-stopped (manual preset)")
            ptz_goto_preset(config)
        elif choice == "a":
            ptz_add_preset(config)
        elif choice == "d":
            ptz_delete_preset(config)
        elif choice == "t":
            result = _start_patrol(config)
            if result.get("ok"):
                print("  Patrol started")
            else:
                print(f"  {result.get('error', 'Failed')}")
        elif choice == "x":
            result = _stop_patrol()
            if result.get("ok"):
                print("  Patrol stopped")
            else:
                print(f"  {result.get('error', 'Failed')}")
        elif choice == "c":
            _patrol_config_menu(config)
        else:
            print("  Unknown PTZ command")


def ptz_set_speed(config):
    data = cgi("getPTZSpeed", config)
    current = data.get("speed", "?")
    print(f"  Current speed: {current} (0=fastest, 1=fast, 2=normal, 3=slow, 4=slowest)")
    speed = input("  New speed (0-4): ").strip()
    if speed in ("0", "1", "2", "3", "4"):
        data = cgi("setPTZSpeed", config, speed=speed)
        ok(data, "setPTZSpeed")
    else:
        print("  Invalid speed")


def ptz_list_presets(config):
    data = cgi("getPTZPresetPointList", config)
    if ok(data, "getPTZPresetPointList"):
        names = data.get("point0", "")
        if names:
            print(f"  Presets: {urllib.parse.unquote(names)}")
        else:
            print("  No presets saved")


def ptz_goto_preset(config):
    name = input("  Preset name: ").strip()
    if name:
        data = cgi("ptzGotoPresetPoint", config, name=name)
        ok(data, f"ptzGotoPresetPoint({name})")


def ptz_add_preset(config):
    name = input("  New preset name: ").strip()
    if name:
        data = cgi("ptzAddPresetPoint", config, name=name)
        ok(data, f"ptzAddPresetPoint({name})")


def ptz_delete_preset(config):
    name = input("  Preset name to delete: ").strip()
    if name:
        data = cgi("ptzDeletePresetPoint", config, name=name)
        ok(data, f"ptzDeletePresetPoint({name})")


def _patrol_config_menu(config):
    """Configure patrol positions and dwell times."""
    patrol_cfg = _get_patrol_config(config)
    positions = patrol_cfg.get("positions", [])
    repeat = patrol_cfg.get("repeat", True)
    print("\n  --- Patrol Config ---")
    print("  Current positions:")
    for p in positions:
        print(f"    {p['name']}: dwell={p['dwell']}s")
    print(f"  Repeat: {repeat}")
    print("\n  Enter dwell times (format: pos1:10,pos2:30,pos3:15,pos4:0)")
    print("  Set dwell to 0 to skip a position. Need 2+ with dwell > 0.")
    val = input("  Config: ").strip()
    if not val:
        print("  Unchanged")
        return
    new_positions = []
    for part in val.split(","):
        part = part.strip()
        if ":" not in part:
            print(f"  Invalid format: {part}")
            return
        name, dwell_str = part.split(":", 1)
        try:
            dwell = int(dwell_str)
        except ValueError:
            print(f"  Invalid dwell time: {dwell_str}")
            return
        new_positions.append({"name": name.strip(), "dwell": max(0, dwell)})
    repeat_in = input(f"  Repeat? (y/n) [{'y' if repeat else 'n'}]: ").strip().lower()
    if repeat_in == "y":
        repeat = True
    elif repeat_in == "n":
        repeat = False
    patrol_cfg = {"positions": new_positions, "repeat": repeat}
    _save_patrol_config(config, patrol_cfg)
    print("  Patrol config saved")
    for p in new_positions:
        print(f"    {p['name']}: dwell={p['dwell']}s")


# ---------------------------------------------------------------------------
# Image settings
# ---------------------------------------------------------------------------

def image_menu(config):
    print("\n--- Image Settings ---")

    # Get current settings
    data = cgi("getImageSetting", config)
    if ok(data, "getImageSetting"):
        show_dict(data)
    else:
        # Try alternate command
        data = cgi("getVideoStreamParam", config)
        if ok(data, "getVideoStreamParam"):
            show_dict(data)

    print("\n  Options:")
    print("  b=brightness  c=contrast  s=saturation  h=sharpness")
    print("  m=mirror  f=flip")
    print("  q=back")

    while True:
        choice = input("  Image> ").strip().lower()
        if choice == "q":
            break
        elif choice == "b":
            val = input("  Brightness (0-100): ").strip()
            data = cgi("setBrightness", config, brightness=val)
            ok(data, "setBrightness")
        elif choice == "c":
            val = input("  Contrast (0-100): ").strip()
            data = cgi("setContrast", config, constrast=val)
            ok(data, "setContrast")
        elif choice == "s":
            val = input("  Saturation (0-100): ").strip()
            data = cgi("setSaturation", config, saturation=val)
            ok(data, "setSaturation")
        elif choice == "h":
            val = input("  Sharpness (0-100): ").strip()
            data = cgi("setSharpness", config, sharpness=val)
            ok(data, "setSharpness")
        elif choice == "m":
            val = input("  Mirror (0=off, 1=on): ").strip()
            data = cgi("mirrorVideo", config, isMirror=val)
            ok(data, "mirrorVideo")
        elif choice == "f":
            val = input("  Flip (0=off, 1=on): ").strip()
            data = cgi("flipVideo", config, isFlip=val)
            ok(data, "flipVideo")
        else:
            print("  Unknown option")


# ---------------------------------------------------------------------------
# Infrared / night vision
# ---------------------------------------------------------------------------

def ir_menu(config):
    print("\n--- Infrared (Night Vision) ---")
    data = cgi("getInfraLedConfig", config)
    if ok(data, "getInfraLedConfig"):
        mode = data.get("mode", "?")
        mode_names = {"0": "auto", "1": "manual (off)"}
        print(f"  Current mode: {mode} ({mode_names.get(mode, 'unknown')})")

    print("\n  Options:")
    print("  a=auto (IR follows light level)")
    print("  1=force IR on")
    print("  0=force IR off")
    print("  q=back")

    while True:
        choice = input("  IR> ").strip().lower()
        if choice == "q":
            break
        elif choice == "a":
            data = cgi("setInfraLedConfig", config, mode="0")
            ok(data, "setInfraLedConfig(auto)")
        elif choice == "1":
            data = cgi("openInfraLed", config)
            ok(data, "openInfraLed")
        elif choice == "0":
            cgi("setInfraLedConfig", config, mode="1")
            time.sleep(0.3)
            data = cgi("closeInfraLed", config)
            ok(data, "closeInfraLed (manual mode)")
        else:
            print("  Unknown option")


# ---------------------------------------------------------------------------
# Audio
# ---------------------------------------------------------------------------

def audio_menu(config):
    print("\n--- Audio Settings ---")

    # Try known audio commands to discover what this firmware supports
    print("  Probing audio capabilities...")
    vol_data = cgi("getAudioVolume", config)
    if ok(vol_data, "getAudioVolume"):
        show_dict(vol_data)
    else:
        print("  (getAudioVolume not supported)")

    alarm_data = cgi("getPCAudioAlarmCfg", config)
    if ok(alarm_data, "getPCAudioAlarmCfg"):
        enabled = alarm_data.get("isEnablePCAudioAlarm", "?")
        print(f"  Sound alarm: {'enabled' if enabled == '1' else 'disabled'}")

    print("\n  Options:")
    print("  v=set volume (0-100)")
    print("  a=enable sound alarm   d=disable sound alarm")
    print("  t=test any audio command (raw)")
    print("  q=back")

    while True:
        choice = input("  Audio> ").strip().lower()
        if choice == "q":
            break
        elif choice == "v":
            val = input("  Volume (0-100): ").strip()
            data = cgi("setAudioVolume", config, volume=val)
            ok(data, "setAudioVolume")
        elif choice == "a":
            data = cgi("setPCAudioAlarmCfg", config, isEnablePCAudioAlarm="1")
            ok(data, "setPCAudioAlarmCfg(enable)")
        elif choice == "d":
            data = cgi("setPCAudioAlarmCfg", config, isEnablePCAudioAlarm="0")
            ok(data, "setPCAudioAlarmCfg(disable)")
        elif choice == "t":
            cmd = input("  Audio command name: ").strip()
            if cmd:
                data = cgi(cmd, config)
                if data:
                    show_dict(data)
        else:
            print("  Unknown option")


# ---------------------------------------------------------------------------
# Video stream settings
# ---------------------------------------------------------------------------

def video_settings(config):
    print("\n--- Video Stream Settings ---")

    print("\n  Main stream:")
    data = cgi("getMainVideoStreamType", config)
    if ok(data, "getMainVideoStreamType"):
        show_dict(data)

    print("\n  Sub stream:")
    data = cgi("getSubVideoStreamType", config)
    if ok(data, "getSubVideoStreamType"):
        show_dict(data)

    print("\n  Stream parameters:")
    data = cgi("getVideoStreamParam", config)
    if ok(data, "getVideoStreamParam"):
        show_dict(data)

    print("\n  Options:")
    print("  r=resolution  f=framerate  b=bitrate  k=keyframe interval")
    print("  q=back")

    while True:
        choice = input("  Video> ").strip().lower()
        if choice == "q":
            break
        elif choice == "r":
            print("  Resolutions: 0=720p, 1=VGA, 2=QVGA, 3=1080p")
            val = input("  Resolution: ").strip()
            data = cgi("setVideoStreamParam", config, resolution=val)
            ok(data, "setVideoStreamParam")
        elif choice == "f":
            val = input("  Framerate (1-30): ").strip()
            data = cgi("setVideoStreamParam", config, frameRate=val)
            ok(data, "setVideoStreamParam")
        elif choice == "b":
            val = input("  Bitrate (kbps, e.g. 2048): ").strip()
            data = cgi("setVideoStreamParam", config, bitRate=val)
            ok(data, "setVideoStreamParam")
        elif choice == "k":
            val = input("  Keyframe interval (GOP, e.g. 30): ").strip()
            data = cgi("setVideoStreamParam", config, GOP=val)
            ok(data, "setVideoStreamParam")
        else:
            print("  Unknown option")


# ---------------------------------------------------------------------------
# Motion detection
# ---------------------------------------------------------------------------

def motion_detection(config):
    print("\n--- Motion Detection ---")
    data = cgi("getMotionDetectConfig", config)
    if ok(data, "getMotionDetectConfig"):
        enabled = data.get("isEnable", "?")
        sensitivity = data.get("sensitivity", "?")
        linkage = data.get("linkage", "?")
        print(f"  Enabled: {enabled}")
        print(f"  Sensitivity: {sensitivity} (0=low, 1=medium, 2=high, 3=lower, 4=lowest)")
        print(f"  Linkage: {linkage}")
    else:
        # Try alternate command for newer firmware
        data = cgi("getMotionDetectConfig1", config)
        if ok(data, "getMotionDetectConfig1"):
            show_dict(data)

    print("\n  Options:")
    print("  e=enable  d=disable  s=set sensitivity")
    print("  q=back")

    while True:
        choice = input("  Motion> ").strip().lower()
        if choice == "q":
            break
        elif choice == "e":
            data = cgi("setMotionDetectConfig", config, isEnable="1")
            ok(data, "setMotionDetectConfig(enable)")
        elif choice == "d":
            data = cgi("setMotionDetectConfig", config, isEnable="0")
            ok(data, "setMotionDetectConfig(disable)")
        elif choice == "s":
            val = input("  Sensitivity (0=low, 1=medium, 2=high): ").strip()
            data = cgi("setMotionDetectConfig", config, isEnable="1", sensitivity=val)
            ok(data, "setMotionDetectConfig")
        else:
            print("  Unknown option")


# ---------------------------------------------------------------------------
# Snapshot
# ---------------------------------------------------------------------------

def take_snapshot(config):
    """Save a JPEG snapshot to disk."""
    print("\n--- Snapshot ---")
    cam = config["camera"]
    url = (f"http://{cam['ip']}:{cam['port']}/cgi-bin/CGIProxy.fcgi"
           f"?cmd=snapPicture2&usr={urllib.parse.quote(cam['username'])}"
           f"&pwd={urllib.parse.quote(cam['password'])}")
    try:
        with urllib.request.urlopen(url, timeout=10) as resp:
            img_data = resp.read()
        filename = os.path.join(SCRIPT_DIR, f"snapshot_{int(time.time())}.jpg")
        with open(filename, "wb") as f:
            f.write(img_data)
        print(f"  Saved: {filename} ({len(img_data)} bytes)")
    except Exception as e:
        print(f"  ERROR: {e}")


# ---------------------------------------------------------------------------
# Raw CGI command
# ---------------------------------------------------------------------------

def raw_command(config):
    print("\n--- Raw CGI Command ---")
    print("  Enter a CGI command name (e.g. getDevState, getSystemTime)")
    cmd = input("  Command: ").strip()
    if not cmd:
        return
    data = cgi(cmd, config)
    if data:
        show_dict(data)
    else:
        print("  No response")


# ---------------------------------------------------------------------------
# Viewer HTML generation
# ---------------------------------------------------------------------------

def generate_viewer(config):
    """Generate nerdcam.html with credentials baked in."""
    cam = config["camera"]
    html_path = os.path.join(SCRIPT_DIR, "nerdcam.html")
    template_path = os.path.join(SCRIPT_DIR, "nerdcam_template.html")
    if not os.path.exists(template_path):
        print(f"  Template not found: {template_path}")
        return
    with open(template_path) as f:
        html = f.read()
    html = html.replace("__CAM_HOST__", cam["ip"])
    html = html.replace("__CAM_PORT__", str(cam["port"]))
    html = html.replace("__CAM_USER__", cam["username"])
    html = html.replace("__CAM_PASS__", cam["password"])
    with open(html_path, "w") as f:
        f.write(html)
    os.chmod(html_path, 0o600)
    print(f"  Generated: {html_path}")


def open_viewer(config, open_browser=True):
    """Start proxy server (and optionally open web viewer in browser)."""
    import http.server
    from urllib.parse import urlparse, parse_qs

    cam = config["camera"]
    cam_base = f"http://{cam['ip']}:{cam['port']}/cgi-bin/CGIProxy.fcgi"

    port = 8088

    class ProxyHandler(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=SCRIPT_DIR, **kwargs)

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
                        fid = _mjpeg_frame_id
                        frame = _mjpeg_frame
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
                            if no_frame_count >= 250:  # ~5s no frames
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
        generate_viewer(config)
        url = f"http://localhost:{port}/nerdcam.html"
        subprocess.Popen(["xdg-open", url],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        print(f"  Web viewer opened: {url}")


# ---------------------------------------------------------------------------
# Credentials
# ---------------------------------------------------------------------------

def update_credentials(config):
    print("\n--- Update Credentials ---")
    print("  Leave blank to keep current value.")
    new_ip = input(f"  Camera IP [{config['camera']['ip']}]: ").strip()
    if new_ip:
        config["camera"]["ip"] = new_ip
    new_user = input(f"  Camera username [{config['camera']['username']}]: ").strip()
    if new_user:
        config["camera"]["username"] = new_user
    new_cam_pwd = getpass.getpass("  Camera password [****]: ")
    if new_cam_pwd:
        config["camera"]["password"] = new_cam_pwd
    new_ssid = input(f"  WiFi SSID [{config['wifi']['ssid']}]: ").strip()
    if new_ssid:
        config["wifi"]["ssid"] = new_ssid
    new_wifi_pwd = getpass.getpass("  WiFi password [****]: ")
    if new_wifi_pwd:
        config["wifi"]["password"] = new_wifi_pwd
    save_config(config)


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
    global _viewer_server, _stream_quality

    print("=== NerdCam (Foscam R2) Setup Tool ===\n")
    log.info("=== NerdCam starting ===")
    print(f"  Log file: {LOG_PATH}")

    _check_dependencies()
    _detect_rec_codecs()

    config = load_config()
    cam = config["camera"]
    print(f"\nCamera: {cam['ip']}:{cam['port']} (user: {cam['username']})")

    connected = show_device_info(config)
    if not connected:
        print("\nCannot connect. Check IP, port, credentials.")

    # (no longer needed, quality is now 1-10 scale)

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
            show_stream_url(config)
        elif choice == "4":
            _advanced_menu(config)
        elif choice == "5":
            _stop_server()
        elif choice == "q":
            if _patrol_running:
                print("  Stopping patrol...")
                _stop_patrol()
            if _recording_proc and _recording_proc.poll() is None:
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
            image_menu(config)
        elif choice == "3":
            ir_menu(config)
        elif choice == "4":
            video_settings(config)
        elif choice == "5":
            motion_detection(config)
        elif choice == "6":
            audio_menu(config)
        elif choice == "7":
            _compression_menu()
        elif choice == "8":
            watch_stream(config)
        elif choice == "9":
            test_rtsp(config)
        elif choice == "0":
            take_snapshot(config)
        elif choice == "w":
            show_wifi_status(config)
        elif choice == "n":
            configure_wifi(config)
        elif choice == "p":
            show_ports(config)
        elif choice == "i":
            show_device_info(config)
        elif choice == "t":
            sync_time(config)
        elif choice == "r":
            reboot_camera(config)
        elif choice == "x":
            raw_command(config)
        elif choice == "m":
            _mic_gain_menu(config)
        elif choice == "o":
            osd_menu(config)
        elif choice == "e":
            _recording_menu(config)
        elif choice == "c":
            update_credentials(config)
        elif choice == "b":
            break
        else:
            print("  Unknown choice")


def _start_recording(config):
    """Start recording RTSP stream to local MP4 file."""
    import datetime as _dt
    global _recording_proc, _recording_info
    if _recording_proc and _recording_proc.poll() is None:
        print("  Already recording!")
        return False

    rec_dir = os.path.join(SCRIPT_DIR, "recordings")
    os.makedirs(rec_dir, exist_ok=True)
    filename = f"nerdcam_{_dt.datetime.now().strftime('%Y%m%d_%H%M%S')}.mp4"
    filepath = os.path.join(rec_dir, filename)

    video_args = _rec_video_args()

    rtsp_url = _rtsp_url(config)
    try:
        cmd = ["ffmpeg", "-y",
               "-rtsp_transport", _rtsp_transport,
               "-i", rtsp_url,
               *video_args,
               "-c:a", "aac", "-b:a", "128k",
               "-t", str(_max_record_seconds),
               filepath]
        _recording_proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE
        )
        # Check if ffmpeg crashed immediately (bad codec, bad input, etc.)
        time.sleep(1.5)
        if _recording_proc.poll() is not None:
            err = _recording_proc.stderr.read().decode(errors="replace")
            # Show last few lines of ffmpeg error
            err_lines = [l for l in err.strip().splitlines() if l.strip()]
            err_tail = "\n    ".join(err_lines[-5:]) if err_lines else "unknown error"
            print(f"  ERROR: ffmpeg exited immediately:\n    {err_tail}")
            _recording_proc = None
            _recording_info = None
            return False
        _recording_info = {"filename": filename, "started": time.time(),
                           "codec": _rec_codec, "compression": _rec_compression}
        log.info("Recording started: %s (codec=%s, compression=%d)", filename, _rec_codec, _rec_compression)
        print(f"  Recording started: {filename} ({_rec_codec}, compression {_rec_compression})")
        return True
    except FileNotFoundError:
        log.error("Recording failed: ffmpeg not found")
        print("  ERROR: ffmpeg not found.")
        return False


def _stop_recording():
    """Stop recording by sending 'q' to ffmpeg for clean MP4 finalization."""
    global _recording_proc, _recording_info
    if not _recording_proc or _recording_proc.poll() is not None:
        _recording_proc = None
        _recording_info = None
        print("  Not recording.")
        return False

    try:
        _recording_proc.stdin.write(b"q")
        _recording_proc.stdin.flush()
        _recording_proc.wait(timeout=10)
    except Exception:
        _recording_proc.kill()
    elapsed = time.time() - _recording_info["started"]
    log.info("Recording stopped: %s (%ds)", _recording_info['filename'], int(elapsed))
    print(f"  Recording stopped: {_recording_info['filename']} ({int(elapsed)}s)")
    _recording_proc = None
    _recording_info = None
    return True


def _recording_status():
    """Return current recording state as dict."""
    if _recording_proc and _recording_proc.poll() is None and _recording_info:
        elapsed = time.time() - _recording_info["started"]
        return {
            "recording": True,
            "filename": _recording_info["filename"],
            "elapsed": int(elapsed)
        }
    return {"recording": False, "filename": "", "elapsed": 0}


def _cleanup_recording():
    """Safety net: kill orphaned ffmpeg recording process."""
    global _recording_proc
    if _recording_proc and _recording_proc.poll() is None:
        try:
            _recording_proc.stdin.write(b"q")
            _recording_proc.stdin.flush()
            _recording_proc.wait(timeout=5)
        except Exception:
            try:
                _recording_proc.kill()
            except Exception:
                pass


atexit.register(_cleanup_recording)


# ---------------------------------------------------------------------------
# Patrol (automated PTZ position cycling)
# ---------------------------------------------------------------------------

_patrol_thread = None
_patrol_running = False
_patrol_status = {"running": False, "current_pos": "", "cycle": 0}
_patrol_config = None  # reference to main config for saving


def _patrol_loop(positions, repeat, config):
    """Daemon thread: cycle through PTZ positions with dwell times."""
    global _patrol_running, _patrol_status
    cycle = 0
    while _patrol_running:
        cycle += 1
        _patrol_status["cycle"] = cycle
        for pos in positions:
            if not _patrol_running:
                break
            name = pos["name"]
            dwell = pos["dwell"]
            if dwell <= 0:
                continue
            _patrol_status["current_pos"] = name
            cgi("ptzGotoPresetPoint", config, name=name)
            # Sleep in 100ms increments for fast stop response
            elapsed = 0.0
            while elapsed < dwell and _patrol_running:
                time.sleep(0.1)
                elapsed += 0.1
        if not repeat:
            break
    _patrol_running = False
    _patrol_status["running"] = False
    _patrol_status["current_pos"] = ""


def _start_patrol(config):
    """Start patrol with config from settings."""
    global _patrol_thread, _patrol_running, _patrol_status, _patrol_config
    if _patrol_running:
        return {"ok": False, "error": "Patrol already running"}
    _patrol_config = config
    settings = config.get("settings", {})
    patrol_cfg = settings.get("patrol", {})
    positions = patrol_cfg.get("positions", [])
    repeat = patrol_cfg.get("repeat", True)
    active = [p for p in positions if p.get("dwell", 0) > 0]
    if len(active) < 2:
        log.info("Patrol start rejected: only %d active positions", len(active))
        return {"ok": False, "error": "Need at least 2 positions with dwell > 0"}
    _patrol_running = True
    _patrol_status = {"running": True, "current_pos": "", "cycle": 0}
    _patrol_thread = threading.Thread(
        target=_patrol_loop, args=(positions, repeat, config), daemon=True)
    _patrol_thread.start()
    log.info("Patrol started: %d positions, repeat=%s", len(active), repeat)
    return {"ok": True}


def _stop_patrol():
    """Stop patrol loop."""
    global _patrol_running
    if not _patrol_running:
        return {"ok": False, "error": "Patrol not running"}
    _patrol_running = False
    log.info("Patrol stopped")
    return {"ok": True}


def _get_patrol_status():
    """Return current patrol state."""
    return {
        "running": _patrol_running,
        "current_pos": _patrol_status.get("current_pos", ""),
        "cycle": _patrol_status.get("cycle", 0),
    }


def _get_patrol_config(config):
    """Return patrol config from settings."""
    settings = config.get("settings", {})
    return settings.get("patrol", {
        "positions": [
            {"name": "pos1", "dwell": 0},
            {"name": "pos2", "dwell": 0},
            {"name": "pos3", "dwell": 0},
            {"name": "pos4", "dwell": 0},
        ],
        "repeat": True,
    })


def _save_patrol_config(config, patrol_cfg):
    """Save patrol config into encrypted settings."""
    if "settings" not in config:
        config["settings"] = {}
    config["settings"]["patrol"] = patrol_cfg
    save_config(config)


def _cleanup_patrol():
    """Safety net: stop patrol thread on exit."""
    global _patrol_running
    _patrol_running = False


atexit.register(_cleanup_patrol)


def _recording_menu(config):
    """CLI menu for local recording."""
    global _rec_codec, _rec_compression, _rec_gpu
    print("\n--- Local Recording ---")
    rec_dir = os.path.join(SCRIPT_DIR, "recordings")
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


def osd_menu(config):
    """Toggle OSD overlays (timestamp, device name) on camera stream."""
    print("\n--- OSD Overlay ---")
    data = cgi("getOSDSetting", config)
    if not ok(data, "getOSDSetting"):
        return

    ts_on = data.get("isEnableTimeStamp", "0") == "1"
    dn_on = data.get("isEnableDevName", "0") == "1"
    print(f"  Timestamp: {'ON' if ts_on else 'OFF'}")
    print(f"  Device name: {'ON' if dn_on else 'OFF'}")

    print("\n  Options:")
    print("  t=toggle timestamp  d=toggle device name  n=set device name")
    print("  q=back")

    while True:
        choice = input("  OSD> ").strip().lower()
        if choice == "q":
            break
        elif choice == "t":
            new_val = "0" if ts_on else "1"
            data = cgi("setOSDSetting", config, isEnableTimeStamp=new_val,
                        isEnableDevName="1" if dn_on else "0")
            if ok(data, "setOSDSetting"):
                ts_on = not ts_on
                print(f"  Timestamp: {'ON' if ts_on else 'OFF'}")
        elif choice == "d":
            new_val = "0" if dn_on else "1"
            data = cgi("setOSDSetting", config, isEnableDevName=new_val,
                        isEnableTimeStamp="1" if ts_on else "0")
            if ok(data, "setOSDSetting"):
                dn_on = not dn_on
                print(f"  Device name: {'ON' if dn_on else 'OFF'}")
        elif choice == "n":
            name = input("  New device name: ").strip()
            if name:
                data = cgi("setDevName", config, devName=name)
                ok(data, f"setDevName({name})")
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

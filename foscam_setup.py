#!/usr/bin/env python3
"""Foscam R2 (NerdCam) setup and control tool.

On first run, prompts for camera credentials and WiFi settings,
then encrypts everything into config.enc with a master password.
Run: python3 foscam_setup.py
"""

import base64
import hashlib


def cls():
    """Clear terminal screen."""
    os.system("clear" if os.name != "nt" else "cls")
import json
import os
import subprocess
import sys
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
import getpass
import time

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(SCRIPT_DIR, "config.enc")
CONFIG_PLAIN = os.path.join(SCRIPT_DIR, "config.json")

_viewer_server = None
_stream_quality = 7  # 1-10 scale: 10=best quality, 1=lowest. Maps to ffmpeg -q:v internally.

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
    global _stream_quality
    settings = config.get("settings", {})
    _stream_quality = settings.get("stream_quality", 7)


def _save_settings(config):
    """Save app settings into config and encrypt."""
    if "settings" not in config:
        config["settings"] = {}
    config["settings"]["stream_quality"] = _stream_quality
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
    print("  q=back")

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
            cgi(ptz_cmds[choice], config)
            time.sleep(0.5)
            cgi("ptzStopRun", config)
        elif choice == "s":
            ptz_set_speed(config)
        elif choice == "p":
            ptz_list_presets(config)
        elif choice == "g":
            ptz_goto_preset(config)
        elif choice == "a":
            ptz_add_preset(config)
        elif choice == "d":
            ptz_delete_preset(config)
        else:
            print("  Unknown PTZ command")


def ptz_set_speed(config):
    data = cgi("getPTZSpeed", config)
    current = data.get("speed", "?")
    print(f"  Current speed: {current} (0=slow, 1=normal, 2=fast, 3=very fast, 4=fastest)")
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
    import threading
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
            parsed = urlparse(self.path)

            # Proxy: /api/cam?cmd=XXX&param=val -> camera CGI
            if parsed.path == "/api/cam":
                qs = parse_qs(parsed.query)
                params = {k: v[0] for k, v in qs.items()}
                params["usr"] = cam["username"]
                params["pwd"] = cam["password"]
                cam_url = f"{cam_base}?{urllib.parse.urlencode(params)}"
                try:
                    with urllib.request.urlopen(cam_url, timeout=10) as resp:
                        data = resp.read()
                    self.send_response(200)
                    self.send_header("Content-Type", "text/xml")
                    self.send_header("Access-Control-Allow-Origin", "*")
                    self.end_headers()
                    self.wfile.write(data)
                except Exception as e:
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

            # MJPEG stream via ffmpeg (RTSP -> MJPEG conversion)
            # OpenCV can read this: cv2.VideoCapture("http://localhost:8088/api/mjpeg")
            if parsed.path == "/api/mjpeg":
                rtsp_port = cam.get("port", 88)
                rtsp_url = (f"rtsp://{cam['username']}:{cam['password']}"
                            f"@{cam['ip']}:{rtsp_port}/videoMain")
                self.send_response(200)
                self.send_header("Content-Type",
                                 "multipart/x-mixed-replace; boundary=ffmpeg")
                self.send_header("Cache-Control", "no-cache")
                self.end_headers()
                try:
                    proc = subprocess.Popen(
                        ["ffmpeg",
                         "-fflags", "+nobuffer+flush_packets",
                         "-flags", "low_delay",
                         "-probesize", "32",
                         "-analyzeduration", "0",
                         "-rtsp_transport", "udp",
                         "-rtsp_flags", "prefer_tcp",
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
                        stderr=subprocess.DEVNULL
                    )
                    # Read JPEG frames from ffmpeg stdout and wrap in MJPEG
                    buf = b""
                    while True:
                        chunk = proc.stdout.read(4096)
                        if not chunk:
                            break
                        buf += chunk
                        # Find JPEG boundaries (FFD8 = start, FFD9 = end)
                        while True:
                            start = buf.find(b"\xff\xd8")
                            end = buf.find(b"\xff\xd9", start + 2) if start >= 0 else -1
                            if start < 0 or end < 0:
                                break
                            jpeg = buf[start:end + 2]
                            buf = buf[end + 2:]
                            self.wfile.write(b"--ffmpeg\r\n")
                            self.wfile.write(b"Content-Type: image/jpeg\r\n")
                            self.wfile.write(f"Content-Length: {len(jpeg)}\r\n".encode())
                            self.wfile.write(b"\r\n")
                            self.wfile.write(jpeg)
                            self.wfile.write(b"\r\n")
                            self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError):
                    pass
                except Exception:
                    pass
                finally:
                    try:
                        proc.kill()
                    except Exception:
                        pass
                return

            # Full A/V stream via ffmpeg (RTSP -> MPEG-TS, for VLC/ffplay)
            # VLC: vlc http://localhost:8088/api/stream
            if parsed.path == "/api/stream":
                rtsp_port = cam.get("port", 88)
                rtsp_url = (f"rtsp://{cam['username']}:{cam['password']}"
                            f"@{cam['ip']}:{rtsp_port}/videoMain")
                self.send_response(200)
                self.send_header("Content-Type", "video/mp2t")
                self.send_header("Cache-Control", "no-cache")
                self.end_headers()
                try:
                    proc = subprocess.Popen(
                        ["ffmpeg",
                         "-fflags", "+nobuffer+flush_packets",
                         "-flags", "low_delay",
                         "-probesize", "32",
                         "-analyzeduration", "0",
                         "-rtsp_transport", "udp",
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
                        stderr=subprocess.DEVNULL
                    )
                    while True:
                        chunk = proc.stdout.read(4096)
                        if not chunk:
                            break
                        self.wfile.write(chunk)
                        self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError):
                    pass
                except Exception:
                    pass
                finally:
                    try:
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


# ---------------------------------------------------------------------------
# Server control helpers
# ---------------------------------------------------------------------------

def _start_server(config):
    """Start the proxy server if not already running."""
    global _viewer_server
    if _viewer_server is not None:
        return True  # already running
    open_viewer.__wrapped__(config)  # start server without opening browser
    return _viewer_server is not None


def _stop_server():
    global _viewer_server
    if _viewer_server:
        _viewer_server.shutdown()
        _viewer_server = None
        print("  Server stopped.")
        return True
    print("  No server running.")
    return False


def _ensure_server(config):
    """Start server if needed, return True if running."""
    global _viewer_server
    if _viewer_server is not None:
        return True
    # Start server without browser
    import http.server
    import threading

    generate_viewer(config)
    cam = config["camera"]
    cam_base = f"http://{cam['ip']}:{cam['port']}/cgi-bin/CGIProxy.fcgi"
    port = 8088

    # Re-use the ProxyHandler from open_viewer by calling it
    open_viewer(config, open_browser=False)
    return _viewer_server is not None


# ---------------------------------------------------------------------------
# Main menu
# ---------------------------------------------------------------------------

def main():
    global _viewer_server, _stream_quality

    print("=== NerdCam (Foscam R2) Setup Tool ===\n")

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
        print("  SYSTEM")
        print("    i. Device info")
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
        elif choice == "r":
            reboot_camera(config)
        elif choice == "x":
            raw_command(config)
        elif choice == "c":
            update_credentials(config)
        elif choice == "b":
            break
        else:
            print("  Unknown choice")


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

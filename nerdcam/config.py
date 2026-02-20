"""Configuration loading, saving, and onboarding for NerdCam.

All functions take an AppState instance instead of using globals.
"""

import getpass
import json
import os
import sys
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

from nerdcam.crypto import encrypt_config, decrypt_config
from nerdcam.state import CONFIG_PATH, CONFIG_PLAIN


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


def load_settings(state):
    """Restore app settings from config into AppState."""
    config = state.config
    settings = config.get("settings", {})
    state.stream_quality = settings.get("stream_quality", 7)
    state.mic_gain = settings.get("mic_gain", 3.0)
    rt = settings.get("rtsp_transport", "udp")
    state.rtsp_transport = rt if rt in ("udp", "tcp") else "udp"
    rc = settings.get("rec_codec", state.default_rec_codec)
    state.rec_codec = rc if rc in state.rec_codecs else state.default_rec_codec
    comp = settings.get("rec_compression", 5)
    state.rec_compression = max(1, min(10, int(comp)))
    gpu = settings.get("rec_gpu", "auto")
    valid_gpus = {"auto"} | {idx for idx, _ in state.available_gpus}
    state.rec_gpu = gpu if gpu in valid_gpus else "auto"


def save_settings(state):
    """Save app settings from AppState into config and encrypt."""
    config = state.config
    if "settings" not in config:
        config["settings"] = {}
    config["settings"]["stream_quality"] = state.stream_quality
    config["settings"]["mic_gain"] = state.mic_gain
    config["settings"]["rec_codec"] = state.rec_codec
    config["settings"]["rec_compression"] = state.rec_compression
    config["settings"]["rec_gpu"] = state.rec_gpu
    config["settings"]["rtsp_transport"] = state.rtsp_transport
    save_config(state)


def load_config(state) -> dict:
    """Load config: try encrypted first, then plaintext, then create new."""

    # Try encrypted config
    if os.path.exists(CONFIG_PATH):
        print("  Encrypted config found.")
        for attempt in range(3):
            state.master_pwd = getpass.getpass("  Master password: ")
            config = decrypt_config(state.master_pwd, CONFIG_PATH)
            if config is not None:
                print("  Config decrypted OK.")
                state.config = config
                load_settings(state)
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
        state.master_pwd = getpass.getpass("  New master password: ")
        confirm = getpass.getpass("  Confirm master password: ")
        if state.master_pwd == confirm:
            break
        print("  Passwords don't match, try again.")

    encrypt_config(config, state.master_pwd, CONFIG_PATH)
    # Remove plaintext config if it exists
    if os.path.exists(CONFIG_PLAIN):
        os.remove(CONFIG_PLAIN)
        print("  Removed plaintext config.json")
    print(f"  Encrypted config saved to {CONFIG_PATH}")
    state.config = config
    return config


def save_config(state) -> None:
    """Save config encrypted."""
    if state.master_pwd:
        encrypt_config(state.config, state.master_pwd, CONFIG_PATH)
    else:
        print("  ERROR: no master password set, cannot save.")

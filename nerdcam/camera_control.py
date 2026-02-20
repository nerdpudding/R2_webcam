"""Stateless camera control menus for NerdCam.

All functions take a config dict and communicate with the camera
via CGI commands. No server or streaming state dependencies.
"""

import getpass
import os
import subprocess
import time
import urllib.parse
import urllib.request

from nerdcam.camera_cgi import cgi, ok, show_dict
from nerdcam.state import PROJECT_DIR


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


def sync_time(config, quiet=False):
    """Sync camera time from this computer's clock.

    quiet: if True, only print errors (for auto-sync on startup).
    """
    import datetime as _dt
    import time as _time

    if not quiet:
        print("\n--- Sync Time ---")
    now = _dt.datetime.now()

    if not quiet:
        utc_offset = int(now.astimezone().utcoffset().total_seconds())
        sign = "+" if utc_offset >= 0 else ""
        dst_label = " (DST)" if _time.localtime().tm_isdst else ""
        print(f"  PC time: {now.strftime('%Y-%m-%d %H:%M:%S')} "
              f"(UTC{sign}{utc_offset // 3600}){dst_label}")

    # Send local time with timeZone=0 so the camera stores it as-is.
    # Foscam R2 subtracts timeZone from the provided time, so any
    # non-zero offset causes a mismatch.
    data = cgi("setSystemTime", config,
               timeSource="1",
               ntpServer="",
               dateFormat="0",
               timeFormat="1",
               timeZone="0",
               isDst="0",
               dst="0",
               year=str(now.year),
               mon=str(now.month),
               day=str(now.day),
               hour=str(now.hour),
               minute=str(now.minute),
               sec=str(now.second))
    if not quiet:
        if ok(data, "setSystemTime"):
            print("  Camera time synced.")
        # Verify
        data = cgi("getSystemTime", config)
        if data:
            print(f"  Camera reports: {data.get('year')}-{data.get('mon')}-{data.get('day')} "
                  f"{data.get('hour')}:{data.get('minute')}:{data.get('sec')}")
    else:
        rc = data.get("result", "-1")
        if rc != "0":
            print(f"  WARNING: Time sync failed (result={rc})")


def image_menu(config):
    print("\n--- Image Settings ---")
    data = cgi("getImageSetting", config)
    if ok(data, "getImageSetting"):
        show_dict(data)
    else:
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


def audio_menu(config):
    print("\n--- Audio Settings ---")
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


def _get_stream_params(config, stream=0):
    """Get current video stream parameters for a given stream index."""
    data = cgi("getVideoStreamParam", config)
    if data.get("result") != "0":
        return None
    s = str(stream)
    return {
        "streamType": s,
        "resolution": data.get(f"resolution{s}", "0"),
        "bitRate": data.get(f"bitRate{s}", "2097152"),
        "frameRate": data.get(f"frameRate{s}", "25"),
        "GOP": data.get(f"GOP{s}", "30"),
        "isVBR": data.get(f"isVBR{s}", "1"),
    }


def _set_stream_param(config, stream=0, **overrides):
    """Set video stream parameters. Reads current values first, applies overrides."""
    params = _get_stream_params(config, stream)
    if params is None:
        print("  ERROR: could not read current stream parameters")
        return False
    params.update(overrides)
    data = cgi("setVideoStreamParam", config, **params)
    return ok(data, "setVideoStreamParam")


def video_settings(config):
    print("\n--- Video Stream Settings ---")

    data = cgi("getVideoStreamParam", config)
    if not ok(data, "getVideoStreamParam"):
        return

    # Show main stream (index 0) settings clearly
    res_names = {"0": "720p", "1": "VGA", "3": "VGA 4:3", "7": "1080p", "9": "1536p"}
    res = data.get("resolution0", "?")
    br = int(data.get("bitRate0", "0"))
    fr = data.get("frameRate0", "?")
    gop = data.get("GOP0", "?")
    vbr = "VBR" if data.get("isVBR0") == "1" else "CBR"

    print(f"  Resolution:  {res_names.get(res, res)}")
    print(f"  Bitrate:     {br // 1024} kbps ({vbr})")
    print(f"  Framerate:   {fr} fps")
    print(f"  GOP:         {gop} frames (keyframe every {int(gop) / max(int(fr), 1):.1f}s)")

    print("\n  Options:")
    print("  r=resolution  f=framerate  b=bitrate  k=keyframe interval")
    print("  q=back")

    while True:
        choice = input("  Video> ").strip().lower()
        if choice == "q":
            break
        elif choice == "r":
            print("  Resolutions: 7=1080p, 0=720p, 1=VGA")
            val = input("  Resolution: ").strip()
            _set_stream_param(config, resolution=val)
        elif choice == "f":
            val = input("  Framerate (5-25): ").strip()
            _set_stream_param(config, frameRate=val)
        elif choice == "b":
            print("  Bitrate in kbps: 512, 1024, 2048, 4096, 6144, 8192")
            val = input("  Bitrate (kbps): ").strip()
            try:
                bits = int(val) * 1024
                _set_stream_param(config, bitRate=str(bits))
            except ValueError:
                print("  Invalid number")
        elif choice == "k":
            print("  GOP = frames between keyframes. Lower = better motion quality, more bandwidth.")
            print("  Suggested: 10, 15, 20, 30")
            val = input("  GOP: ").strip()
            _set_stream_param(config, GOP=val)
        else:
            print("  Unknown option")


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
        filename = os.path.join(PROJECT_DIR, f"snapshot_{int(time.time())}.jpg")
        with open(filename, "wb") as f:
            f.write(img_data)
        print(f"  Saved: {filename} ({len(img_data)} bytes)")
    except Exception as e:
        print(f"  ERROR: {e}")


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


def generate_viewer(config):
    """Generate nerdcam.html with credentials baked in."""
    cam = config["camera"]
    html_path = os.path.join(PROJECT_DIR, "nerdcam.html")
    template_path = os.path.join(PROJECT_DIR, "nerdcam_template.html")
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


def show_stream_url(config, viewer_server):
    print("\n--- Stream URLs (no credentials needed) ---")
    if viewer_server is None:
        print("  WARNING: Server not running! Start it first.\n")
    print("  VIDEO ONLY (lowest latency, ~1s):")
    print("    http://localhost:8088/api/mjpeg")
    print("    MJPEG — re-encoded from camera H.264, no audio")
    print("    For: NerdPudding, OpenCV, browser (mic off)")
    print()
    print("  SYNCED A/V (~3-3.5s latency):")
    print("    http://localhost:8088/api/fmp4")
    print("    fMP4 — H.264 copy + AAC audio")
    print("    For: browser (mic on), VLC, ffplay")
    print()
    print("  SNAPSHOT:")
    print("    http://localhost:8088/api/snap")
    print()
    print("  Examples:")
    print("    vlc http://localhost:8088/api/fmp4")
    print("    ffplay http://localhost:8088/api/mjpeg")
    print("    cv2.VideoCapture('http://localhost:8088/api/mjpeg')")


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


def update_credentials(config, save_config_fn):
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
    save_config_fn(config)


def _rtsp_url(config) -> str:
    cam = config["camera"]
    data = cgi("getPortInfo", config)
    rtsp_port = data.get("rtspPort", str(cam["port"]))
    return f"rtsp://{cam['username']}:{cam['password']}@{cam['ip']}:{rtsp_port}/videoMain"

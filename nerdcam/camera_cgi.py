"""CGI helpers for Foscam R2 camera communication.

Stateless functions that send CGI commands to the camera and parse
XML responses. Takes config dict with camera credentials.
"""

import logging
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

log = logging.getLogger("nerdcam")


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

#!/usr/bin/env python3
"""Probe camera ONVIF capabilities — no dependencies beyond stdlib.

Usage: python3 tools/onvif_probe.py <ip> <port> <username> <password>
       python3 tools/onvif_probe.py  (uses NerdCam encrypted config)
"""

import base64
import hashlib
import os
import sys
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone


def _ws_security_header(username, password):
    """Build WS-Security UsernameToken header with PasswordDigest."""
    nonce = os.urandom(16)
    created = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    # PasswordDigest = Base64(SHA1(nonce + created + password))
    digest_input = nonce + created.encode("utf-8") + password.encode("utf-8")
    digest = base64.b64encode(hashlib.sha1(digest_input).digest()).decode()
    nonce_b64 = base64.b64encode(nonce).decode()

    return f"""<s:Header>
    <Security xmlns="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-secext-1.0.xsd">
      <UsernameToken>
        <Username>{username}</Username>
        <Password Type="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-username-token-profile-1.0#PasswordDigest">{digest}</Password>
        <Nonce EncodingType="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-soap-message-security-1.0#Base64Binary">{nonce_b64}</Nonce>
        <Created xmlns="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-utility-1.0.xsd">{created}</Created>
      </UsernameToken>
    </Security>
  </s:Header>"""


# Module-level credentials, set in main()
_username = ""
_password = ""


def soap_request(url, action, body):
    """Send a SOAP request with WS-Security auth and return parsed XML."""
    header = _ws_security_header(_username, _password)
    envelope = f"""<?xml version="1.0" encoding="UTF-8"?>
<s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope"
            xmlns:trt="http://www.onvif.org/ver10/media/wsdl"
            xmlns:tt="http://www.onvif.org/ver10/schema">
  {header}
  <s:Body>
    {body}
  </s:Body>
</s:Envelope>"""

    req = urllib.request.Request(url, data=envelope.encode("utf-8"), headers={
        "Content-Type": 'application/soap+xml; charset=utf-8; action="' + action + '"',
    })
    with urllib.request.urlopen(req, timeout=10) as resp:
        return ET.fromstring(resp.read().decode())


def strip_ns(tag):
    """Remove XML namespace prefix."""
    return tag.split("}")[-1] if "}" in tag else tag


def xml_to_dict(elem, depth=0):
    """Recursively convert XML element to readable output."""
    lines = []
    tag = strip_ns(elem.tag)
    if elem.text and elem.text.strip():
        lines.append(f"{'  ' * depth}{tag}: {elem.text.strip()}")
    elif len(elem) > 0:
        lines.append(f"{'  ' * depth}{tag}:")
        for child in elem:
            lines.extend(xml_to_dict(child, depth + 1))
    else:
        lines.append(f"{'  ' * depth}{tag}: (empty)")
    return lines


def probe_camera(ip, port, username, password):
    """Query ONVIF capabilities from camera."""
    global _username, _password
    _username = username
    _password = password

    onvif_port = 888  # Foscam default ONVIF port
    media_url = f"http://{ip}:{onvif_port}/onvif/media"

    print(f"Camera: {ip}:{port}")
    print(f"ONVIF endpoint: {media_url}")
    print()

    # Step 1: Get profiles
    print("=== Video Profiles ===")
    try:
        root = soap_request(media_url,
            "http://www.onvif.org/ver10/media/wsdl/GetProfiles",
            "<trt:GetProfiles/>")

        profiles = []
        for elem in root.iter():
            if strip_ns(elem.tag) == "Profiles":
                token = elem.get("token", "?")
                name_el = elem.find(".//{http://www.onvif.org/ver10/schema}Name")
                name = name_el.text if name_el is not None else "?"
                profiles.append(token)
                print(f"  Profile: {name} (token={token})")

                # Show video encoder config
                for vec in elem.iter():
                    if strip_ns(vec.tag) == "VideoEncoderConfiguration":
                        for child in vec:
                            tag = strip_ns(child.tag)
                            if tag == "Resolution":
                                w = child.find(".//{http://www.onvif.org/ver10/schema}Width")
                                h = child.find(".//{http://www.onvif.org/ver10/schema}Height")
                                if w is not None and h is not None:
                                    print(f"    Resolution: {w.text}x{h.text}")
                            elif tag == "RateControl":
                                fr = child.find(".//{http://www.onvif.org/ver10/schema}FrameRateLimit")
                                br = child.find(".//{http://www.onvif.org/ver10/schema}BitrateLimit")
                                if fr is not None:
                                    print(f"    FrameRateLimit: {fr.text}")
                                if br is not None:
                                    print(f"    BitrateLimit: {br.text}")
                            elif tag == "H264" or tag == "Encoding":
                                for sub in child:
                                    st = strip_ns(sub.tag)
                                    if sub.text and sub.text.strip():
                                        print(f"    {st}: {sub.text.strip()}")
        print()
    except Exception as e:
        print(f"  ERROR getting profiles: {e}")
        profiles = []

    # Step 2: Get video encoder configuration options (the capabilities/limits)
    print("=== Video Encoder Options (supported ranges) ===")
    for token in profiles:
        print(f"\n  Profile: {token}")
        try:
            body = f"""<trt:GetVideoEncoderConfigurationOptions>
  <trt:ProfileToken>{token}</trt:ProfileToken>
</trt:GetVideoEncoderConfigurationOptions>"""

            root = soap_request(media_url,
                "http://www.onvif.org/ver10/media/wsdl/GetVideoEncoderConfigurationOptions",
                body)

            # Find the Options element
            for elem in root.iter():
                if strip_ns(elem.tag) == "Options":
                    for line in xml_to_dict(elem, 2):
                        print(line)
        except Exception as e:
            print(f"    ERROR: {e}")

    # Step 3: Get video sources (sensor capabilities)
    print("\n=== Video Source Configuration ===")
    try:
        root = soap_request(media_url,
            "http://www.onvif.org/ver10/media/wsdl/GetVideoSources",
            "<trt:GetVideoSources/>")

        for elem in root.iter():
            if strip_ns(elem.tag) == "VideoSources":
                for line in xml_to_dict(elem, 1):
                    print(line)
    except Exception as e:
        print(f"  ERROR: {e}")

    print("\nDone.")


def main():
    if len(sys.argv) == 5:
        ip, port, username, password = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4]
    elif len(sys.argv) == 1:
        # Try to load from NerdCam encrypted config
        try:
            import os
            sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
            from nerdcam.state import CONFIG_PATH
            from nerdcam.crypto import decrypt_config
            import getpass
            pwd = getpass.getpass("NerdCam master password: ")
            config = decrypt_config(pwd, CONFIG_PATH)
            if config is None:
                print("Wrong password.")
                sys.exit(1)
            cam = config["camera"]
            ip, port = cam["ip"], str(cam["port"])
            username, password = cam["username"], cam["password"]
        except Exception as e:
            print(f"Could not load config: {e}")
            print(f"Usage: {sys.argv[0]} <ip> <port> <username> <password>")
            sys.exit(1)
    else:
        print(f"Usage: {sys.argv[0]} <ip> <port> <username> <password>")
        print(f"       {sys.argv[0]}   (uses NerdCam config)")
        sys.exit(1)

    probe_camera(ip, port, username, password)


if __name__ == "__main__":
    main()

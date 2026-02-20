"""PTZ control, presets, and patrol config menu for NerdCam."""

import os
import time
import urllib.parse

from nerdcam.camera_cgi import cgi, ok
from nerdcam.patrol import get_patrol_config, save_patrol_config


def _cls():
    os.system("clear" if os.name != "nt" else "cls")


def ptz_menu(config, patrol, save_config_fn):
    """Interactive PTZ control menu.

    patrol: PatrolController instance
    save_config_fn: callable to persist config changes
    """
    _cls()
    print("--- PTZ Control ---")
    print("  Movement:  7=UL  8=U  9=UR")
    print("             4=L   5=H  6=R")
    print("             1=DL  2=D  3=DR")
    print("  Speed:     s=set speed")
    print("  Presets:   p=list  g=goto  a=add  d=delete")
    print("  Patrol:    t=start  x=stop  c=configure")
    print("  q=back")

    status = patrol.get_status()
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
            if patrol.running:
                patrol.stop()
                print("  Patrol auto-stopped (manual PTZ)")
            cgi(ptz_cmds[choice], config)
            time.sleep(0.5)
            cgi("ptzStopRun", config)
        elif choice == "s":
            set_speed(config)
        elif choice == "p":
            list_presets(config)
        elif choice == "g":
            if patrol.running:
                patrol.stop()
                print("  Patrol auto-stopped (manual preset)")
            goto_preset(config)
        elif choice == "a":
            add_preset(config)
        elif choice == "d":
            delete_preset(config)
        elif choice == "t":
            result = patrol.start(config)
            if result.get("ok"):
                print("  Patrol started")
            else:
                print(f"  {result.get('error', 'Failed')}")
        elif choice == "x":
            result = patrol.stop()
            if result.get("ok"):
                print("  Patrol stopped")
            else:
                print(f"  {result.get('error', 'Failed')}")
        elif choice == "c":
            patrol_config_menu(config, save_config_fn)
        else:
            print("  Unknown PTZ command")


def set_speed(config):
    data = cgi("getPTZSpeed", config)
    current = data.get("speed", "?")
    print(f"  Current speed: {current} (0=fastest, 1=fast, 2=normal, 3=slow, 4=slowest)")
    speed = input("  New speed (0-4): ").strip()
    if speed in ("0", "1", "2", "3", "4"):
        data = cgi("setPTZSpeed", config, speed=speed)
        ok(data, "setPTZSpeed")
    else:
        print("  Invalid speed")


def list_presets(config):
    data = cgi("getPTZPresetPointList", config)
    if ok(data, "getPTZPresetPointList"):
        presets = []
        for key, val in sorted(data.items()):
            if key.startswith("point") and key[5:].isdigit() and val:
                presets.append(urllib.parse.unquote(val))
        if presets:
            print(f"  Presets: {', '.join(presets)}")
        else:
            print("  No presets saved")


def goto_preset(config):
    name = input("  Preset name: ").strip()
    if name:
        data = cgi("ptzGotoPresetPoint", config, name=name)
        ok(data, f"ptzGotoPresetPoint({name})")


def add_preset(config):
    name = input("  New preset name: ").strip()
    if name:
        data = cgi("ptzAddPresetPoint", config, name=name)
        ok(data, f"ptzAddPresetPoint({name})")


def delete_preset(config):
    name = input("  Preset name to delete: ").strip()
    if name:
        data = cgi("ptzDeletePresetPoint", config, name=name)
        ok(data, f"ptzDeletePresetPoint({name})")


def patrol_config_menu(config, save_config_fn):
    """Configure patrol positions and dwell times."""
    _cls()
    patrol_cfg = get_patrol_config(config)
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
    save_patrol_config(config, patrol_cfg, save_config_fn)
    print("  Patrol config saved")
    for p in new_positions:
        print(f"    {p['name']}: dwell={p['dwell']}s")

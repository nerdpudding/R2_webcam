"""PTZ patrol controller for NerdCam.

Automated cycling through preset PTZ positions with configurable
dwell times. Runs as a daemon thread.
"""

import atexit
import logging
import threading
import time

from nerdcam.camera_cgi import cgi

log = logging.getLogger("nerdcam")

# Default patrol config when none is saved
_DEFAULT_PATROL_CONFIG = {
    "positions": [
        {"name": "pos1", "dwell": 0},
        {"name": "pos2", "dwell": 0},
        {"name": "pos3", "dwell": 0},
        {"name": "pos4", "dwell": 0},
    ],
    "repeat": True,
}


class PatrolController:
    """Manages automated PTZ position cycling."""

    def __init__(self):
        self._thread = None
        self.running = False
        self._status = {"running": False, "current_pos": "", "cycle": 0}

    def start(self, config):
        """Start patrol with config from settings. Returns result dict."""
        if self.running:
            return {"ok": False, "error": "Patrol already running"}
        settings = config.get("settings", {})
        patrol_cfg = settings.get("patrol", {})
        positions = patrol_cfg.get("positions", [])
        repeat = patrol_cfg.get("repeat", True)
        active = [p for p in positions if p.get("dwell", 0) > 0]
        if len(active) < 2:
            log.info("Patrol start rejected: only %d active positions", len(active))
            return {"ok": False, "error": "Need at least 2 positions with dwell > 0"}
        self.running = True
        self._status = {"running": True, "current_pos": "", "cycle": 0}
        self._thread = threading.Thread(
            target=self._loop, args=(positions, repeat, config), daemon=True)
        self._thread.start()
        log.info("Patrol started: %d positions, repeat=%s", len(active), repeat)
        return {"ok": True}

    def stop(self):
        """Stop patrol loop. Returns result dict."""
        if not self.running:
            return {"ok": False, "error": "Patrol not running"}
        self.running = False
        log.info("Patrol stopped")
        return {"ok": True}

    def get_status(self):
        """Return current patrol state."""
        return {
            "running": self.running,
            "current_pos": self._status.get("current_pos", ""),
            "cycle": self._status.get("cycle", 0),
        }

    def cleanup(self):
        """Safety net: stop patrol thread on exit."""
        self.running = False

    def _loop(self, positions, repeat, config):
        """Daemon thread: cycle through PTZ positions with dwell times."""
        cycle = 0
        while self.running:
            cycle += 1
            self._status["cycle"] = cycle
            for pos in positions:
                if not self.running:
                    break
                name = pos["name"]
                dwell = pos["dwell"]
                if dwell <= 0:
                    continue
                self._status["current_pos"] = name
                cgi("ptzGotoPresetPoint", config, name=name)
                # Sleep in 100ms increments for fast stop response
                elapsed = 0.0
                while elapsed < dwell and self.running:
                    time.sleep(0.1)
                    elapsed += 0.1
            if not repeat:
                break
        self.running = False
        self._status["running"] = False
        self._status["current_pos"] = ""


def get_patrol_config(config):
    """Return patrol config from settings."""
    settings = config.get("settings", {})
    return settings.get("patrol", dict(_DEFAULT_PATROL_CONFIG))


def save_patrol_config(config, patrol_cfg, save_fn):
    """Save patrol config into encrypted settings.

    save_fn: callable that persists the config (e.g. save_config).
    """
    if "settings" not in config:
        config["settings"] = {}
    config["settings"]["patrol"] = patrol_cfg
    save_fn(config)

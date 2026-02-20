"""Centralized application state for NerdCam.

Replaces all module-level global variables with a single AppState
dataclass. Passed to all functions instead of using 'global' statements.
"""

import os
from dataclasses import dataclass, field
from typing import Any, Optional


# Project root directory (parent of nerdcam/ package)
PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Fixed paths derived from project root
LOG_PATH = os.path.join(PROJECT_DIR, "nerdcam.log")
CONFIG_PATH = os.path.join(PROJECT_DIR, "config.enc")
CONFIG_PLAIN = os.path.join(PROJECT_DIR, "config.json")

# Constant: force-restart MJPEG if no frame for this long
MJPEG_STALE_SECONDS = 2

# Codec definitions: (key, encoder_name, description, required_ffmpeg_encoder)
# encoder_name=None means -c:v copy (no re-encode, compression level ignored)
ALL_REC_CODECS = [
    ("nvenc_av1",  "av1_nvenc",  "NVENC AV1 (GPU, best compression)", "av1_nvenc"),
    ("nvenc_h265", "hevc_nvenc", "NVENC H.265 (GPU, recommended)",    "hevc_nvenc"),
    ("nvenc_h264", "h264_nvenc", "NVENC H.264 (GPU, most compatible)","h264_nvenc"),
    ("sw_h265",    "libx265",    "Software H.265 (CPU)",              "libx265"),
    ("sw_h264",    "libx264",    "Software H.264 (CPU, compatible)",  "libx264"),
    ("original",   None,         "Original (no re-encode)",           None),
]

# Quality ranges per encoder: maps compression 1-10 to CQ/CRF values
QUALITY_RANGES = {
    "av1_nvenc":  (22, 48),
    "hevc_nvenc": (18, 42),
    "h264_nvenc": (16, 38),
    "libx265":    (18, 40),
    "libx264":    (16, 38),
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


@dataclass
class AppState:
    """All mutable application state in one place."""

    # Config & auth
    master_pwd: Optional[str] = None
    config: Optional[dict] = None

    # Server
    viewer_server: Any = None

    # Stream settings (persisted in config.enc)
    stream_quality: int = 7
    mic_gain: float = 3.0
    rtsp_transport: str = "tcp"

    # Shared MJPEG source state
    mjpeg_frame: Optional[bytes] = None
    mjpeg_frame_id: int = 0
    mjpeg_proc: Any = None
    mjpeg_quality: Optional[int] = None
    mjpeg_last_frame_time: float = 0.0

    # Recording state
    recording_proc: Any = None
    recording_info: Optional[dict] = None
    max_record_seconds: int = 3600
    rec_codec: Optional[str] = None
    rec_compression: int = 5
    rec_gpu: str = "auto"
    available_gpus: list = field(default_factory=list)
    rec_codecs: dict = field(default_factory=dict)
    default_rec_codec: str = "original"

    # Patrol state
    patrol_thread: Any = None
    patrol_running: bool = False
    patrol_status: dict = field(default_factory=lambda: {
        "running": False, "current_pos": "", "cycle": 0
    })

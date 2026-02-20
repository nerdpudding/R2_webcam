"""Recording management for NerdCam.

Handles codec detection, GPU enumeration, and ffmpeg-based recording
of RTSP streams to local MP4 files.
"""

import datetime
import logging
import os
import subprocess
import time

from nerdcam.state import PROJECT_DIR, ALL_REC_CODECS, QUALITY_RANGES

log = logging.getLogger("nerdcam")


def detect_codecs():
    """Probe ffmpeg for available encoders and GPUs. Called once at startup.

    Returns (codecs_dict, default_codec, gpus_list).
    """
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
    gpus = []
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=index,name", "--format=csv,noheader"],
            capture_output=True, text=True, timeout=5)
        for line in out.stdout.strip().splitlines():
            parts = [p.strip() for p in line.split(",", 1)]
            if len(parts) == 2:
                gpus.append((parts[0], parts[1]))
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass

    codecs = {}
    for key, encoder, desc, required in ALL_REC_CODECS:
        if required is None or required in available:
            codecs[key] = (encoder, desc)

    default_codec = "original"
    for pref in ["nvenc_h265", "nvenc_h264", "sw_h265", "sw_h264", "original"]:
        if pref in codecs:
            default_codec = pref
            break

    if not codecs:
        codecs["original"] = (None, "Original (no re-encode)")
        default_codec = "original"

    # Print summary
    gpu_count = sum(1 for k in codecs if k.startswith("nvenc_"))
    sw_count = sum(1 for k in codecs if k.startswith("sw_"))
    info = []
    if gpu_count:
        info.append(f"{gpu_count} GPU")
        if len(gpus) > 1:
            names = ", ".join(f"{i}:{n}" for i, n in gpus)
            info.append(f"GPUs: [{names}]")
    if sw_count:
        info.append(f"{sw_count} software")
    if "original" in codecs:
        info.append("passthrough")
    print(f"  Recording codecs: {', '.join(info)} (default: {default_codec})")

    return codecs, default_codec, gpus


def build_video_args(rec_codec, rec_compression, rec_gpu, rec_codecs, available_gpus):
    """Build ffmpeg video args from codec + compression level."""
    codec = rec_codecs.get(rec_codec)
    if not codec or codec[0] is None:
        return ["-c:v", "copy"]

    encoder = codec[0]
    lo, hi = QUALITY_RANGES.get(encoder, (18, 42))
    qval = int(lo + (rec_compression - 1) * (hi - lo) / 9)

    if encoder.endswith("_nvenc"):
        args = ["-c:v", encoder]
        if rec_gpu != "auto" and len(available_gpus) > 1:
            args += ["-gpu", rec_gpu]
        args += ["-cq", str(qval), "-preset", "p4"]
        return args
    else:
        return ["-c:v", encoder, "-crf", str(qval), "-preset", "fast"]


class Recorder:
    """Manages ffmpeg recording of RTSP stream to local MP4 files."""

    def __init__(self, output_dir=None, max_seconds=3600):
        self._proc = None
        self._info = None  # {"filename": str, "started": float, ...}
        self._max_seconds = max_seconds
        self.output_dir = output_dir or os.path.join(PROJECT_DIR, "recordings")

    def start(self, rtsp_url, rtsp_transport, rec_codec, rec_compression,
              rec_gpu, rec_codecs, available_gpus):
        """Start recording. Returns True on success."""
        if self._proc and self._proc.poll() is None:
            print("  Already recording!")
            return False

        os.makedirs(self.output_dir, exist_ok=True)
        filename = f"nerdcam_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.mp4"
        filepath = os.path.join(self.output_dir, filename)

        video_args = build_video_args(rec_codec, rec_compression, rec_gpu,
                                      rec_codecs, available_gpus)
        try:
            cmd = ["ffmpeg", "-y",
                   "-rtsp_transport", rtsp_transport,
                   "-i", rtsp_url,
                   *video_args,
                   "-c:a", "aac", "-b:a", "128k",
                   "-t", str(self._max_seconds),
                   filepath]
            self._proc = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE
            )
            # Check if ffmpeg crashed immediately
            time.sleep(1.5)
            if self._proc.poll() is not None:
                err = self._proc.stderr.read().decode(errors="replace")
                err_lines = [l for l in err.strip().splitlines() if l.strip()]
                err_tail = "\n    ".join(err_lines[-5:]) if err_lines else "unknown error"
                print(f"  ERROR: ffmpeg exited immediately:\n    {err_tail}")
                self._proc = None
                self._info = None
                return False
            self._info = {"filename": filename, "started": time.time(),
                          "codec": rec_codec, "compression": rec_compression}
            log.info("Recording started: %s (codec=%s, compression=%d)",
                     filename, rec_codec, rec_compression)
            print(f"  Recording started: {filename} ({rec_codec}, compression {rec_compression})")
            return True
        except FileNotFoundError:
            log.error("Recording failed: ffmpeg not found")
            print("  ERROR: ffmpeg not found.")
            return False

    def stop(self):
        """Stop recording by sending 'q' to ffmpeg for clean MP4 finalization."""
        if not self._proc or self._proc.poll() is not None:
            self._proc = None
            self._info = None
            print("  Not recording.")
            return False

        try:
            self._proc.stdin.write(b"q")
            self._proc.stdin.flush()
            self._proc.wait(timeout=10)
        except Exception:
            self._proc.kill()
        elapsed = time.time() - self._info["started"]
        log.info("Recording stopped: %s (%ds)", self._info['filename'], int(elapsed))
        print(f"  Recording stopped: {self._info['filename']} ({int(elapsed)}s)")
        self._proc = None
        self._info = None
        return True

    def status(self):
        """Return current recording state as dict."""
        if self._proc and self._proc.poll() is None and self._info:
            elapsed = time.time() - self._info["started"]
            return {
                "recording": True,
                "filename": self._info["filename"],
                "elapsed": int(elapsed)
            }
        return {"recording": False, "filename": "", "elapsed": 0}

    def cleanup(self):
        """Safety net: kill orphaned ffmpeg recording process."""
        if self._proc and self._proc.poll() is None:
            try:
                self._proc.stdin.write(b"q")
                self._proc.stdin.flush()
                self._proc.wait(timeout=5)
            except Exception:
                try:
                    self._proc.kill()
                except Exception:
                    pass

    @property
    def is_recording(self):
        return self._proc is not None and self._proc.poll() is None

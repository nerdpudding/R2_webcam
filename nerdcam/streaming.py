"""Shared MJPEG source for NerdCam.

One ffmpeg process reads the camera RTSP stream and decodes to MJPEG.
Multiple browser clients poll the latest frame from the shared buffer.
Uses simple polling — CPython's GIL makes single variable reads/writes
atomic, and there's only one writer thread.
"""

import logging
import subprocess
import threading
import time

from nerdcam.state import MJPEG_STALE_SECONDS

log = logging.getLogger("nerdcam")


class MjpegSource:
    """Shared MJPEG source: one ffmpeg process, multiple browser clients."""

    def __init__(self):
        self.frame = None          # latest JPEG frame bytes
        self.frame_id = 0          # incremented on each new frame
        self._proc = None          # ffmpeg subprocess
        self._quality = None       # quality level when source was started
        self._last_frame_time = 0  # time.time() of last frame

    def start(self, cam, stream_quality, rtsp_transport):
        """Start shared ffmpeg MJPEG source if not already running."""
        if self._proc:
            if self._proc.poll() is None:
                # Process alive — but is it actually producing frames?
                frame_age = time.time() - self._last_frame_time if self._last_frame_time else 0
                if self._quality == stream_quality and frame_age < MJPEG_STALE_SECONDS:
                    return  # alive and producing frames, nothing to do
                # Stale or quality changed — kill and restart
                log.warning("MJPEG source stale (%.1fs no frames), restarting ffmpeg", frame_age)
                self.stop()
                time.sleep(0.5)
            else:
                log.info("MJPEG ffmpeg process died (exit=%s), restarting", self._proc.returncode)
                try:
                    self._proc.kill()
                except Exception:
                    pass
                self._proc = None
                time.sleep(0.5)

        rtsp_port = cam.get("port", 88)
        rtsp_url = (f"rtsp://{cam['username']}:{cam['password']}"
                    f"@{cam['ip']}:{rtsp_port}/videoMain")
        self._quality = stream_quality
        # TCP needs larger probesize to find video track in interleaved data.
        probe = "500000" if rtsp_transport == "tcp" else "32768"
        analyze = "500000" if rtsp_transport == "tcp" else "0"
        log.info("Starting MJPEG source (quality=%d, transport=%s, rtsp=%s:%s)",
                 stream_quality, rtsp_transport, cam['ip'], rtsp_port)
        proc = subprocess.Popen(
            ["ffmpeg",
             "-fflags", "+nobuffer+flush_packets",
             "-flags", "low_delay",
             "-probesize", probe,
             "-analyzeduration", analyze,
             "-rtsp_transport", rtsp_transport,
             "-i", rtsp_url,
             "-f", "mjpeg",
             "-q:v", str(int(2 + (10 - stream_quality) * 29 / 9)),
             "-r", "25",
             "-an",
             "-threads", "1",
             "-flush_packets", "1",
             "pipe:1"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE
        )
        self._proc = proc
        self._last_frame_time = time.time()

        threading.Thread(target=self._reader, args=(proc,), daemon=True).start()

    def stop(self):
        """Stop the shared MJPEG source."""
        if self._proc:
            log.info("Stopping MJPEG source (pid=%s)", self._proc.pid)
            try:
                self._proc.kill()
            except Exception:
                pass
            self._proc = None
        self.frame = None

    def _reader(self, proc):
        """Read JPEG frames from ffmpeg stdout into shared buffer."""
        buf = b""
        frame_count = 0
        try:
            while True:
                chunk = proc.stdout.read(4096)
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
                    self.frame = jpeg
                    self.frame_id += 1
                    self._last_frame_time = time.time()
                    frame_count += 1
                    if frame_count == 1:
                        log.info("MJPEG source: first frame received (%d bytes)", len(jpeg))
        except Exception as e:
            log.error("MJPEG reader exception: %s", e)
        log.info("MJPEG reader stopped after %d frames", frame_count)
        if proc.poll() and proc.stderr:
            try:
                err = proc.stderr.read().decode(errors="replace").strip()
                if err:
                    lines = err.splitlines()[-5:]
                    log.warning("MJPEG ffmpeg stderr:\n  %s", "\n  ".join(lines))
            except Exception:
                pass

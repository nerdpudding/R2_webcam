"""HTTP proxy server for NerdCam.

Serves the web viewer, proxies camera CGI commands (hiding credentials),
and provides streaming endpoints (MJPEG, fMP4, MPEG-TS, audio).
"""

import http.server
import json
import logging
import subprocess
import threading
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from urllib.parse import urlparse, parse_qs

from nerdcam.state import PROJECT_DIR

log = logging.getLogger("nerdcam")


class NerdCamServer:
    """Manages the HTTP server lifecycle."""

    def __init__(self):
        self._server = None
        self.shutting_down = False
        self._active_procs = []  # track per-client ffmpeg processes

    @property
    def running(self):
        return self._server is not None

    def start(self, config, mjpeg, ctx, port=8088):
        """Start the server if not already running.

        config: camera config dict
        mjpeg: MjpegSource instance
        ctx: ServerContext with callbacks and state accessors
        """
        if self._server is not None:
            print(f"  Server already running on port {port}")
            return

        self.shutting_down = False
        cam = config["camera"]
        cam_base = f"http://{cam['ip']}:{cam['port']}/cgi-bin/CGIProxy.fcgi"

        handler = _make_handler(cam, cam_base, mjpeg, ctx, self)

        self._server = _ThreadedServer(("127.0.0.1", port), handler)
        thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        thread.start()
        log.info("Server started on port %d", port)
        print(f"  Server started on port {port}")

    def stop(self, mjpeg):
        """Stop the server, MJPEG source, and all active stream processes."""
        self.shutting_down = True
        mjpeg.stop()
        # Kill all active per-client ffmpeg processes
        for proc in self._active_procs:
            try:
                proc.kill()
            except Exception:
                pass
        self._active_procs.clear()
        if self._server:
            self._server.shutdown()
            self._server = None
            log.info("Server stopped")
            print("  Server stopped.")
            return True
        print("  No server running.")
        return False

    def register_proc(self, proc):
        """Track a per-client ffmpeg process for cleanup on stop."""
        self._active_procs.append(proc)

    def unregister_proc(self, proc):
        """Remove a finished per-client ffmpeg process."""
        try:
            self._active_procs.remove(proc)
        except ValueError:
            pass


class ServerContext:
    """Bundles all callbacks and state accessors the server handler needs.

    This avoids the handler reaching into module globals. Each accessor
    is a callable that returns the current value.
    """

    def __init__(self, *, get_stream_quality, get_mic_gain, set_mic_gain,
                 get_rtsp_transport, set_rtsp_transport,
                 get_rec_codec, set_rec_codec,
                 get_rec_compression, set_rec_compression,
                 get_rec_gpu, set_rec_gpu,
                 get_rec_codecs, get_available_gpus,
                 save_settings, start_recording, stop_recording,
                 recording_status, start_patrol, stop_patrol,
                 get_patrol_status, get_patrol_config,
                 save_patrol_config, stop_mjpeg, start_mjpeg):
        self.get_stream_quality = get_stream_quality
        self.get_mic_gain = get_mic_gain
        self.set_mic_gain = set_mic_gain
        self.get_rtsp_transport = get_rtsp_transport
        self.set_rtsp_transport = set_rtsp_transport
        self.get_rec_codec = get_rec_codec
        self.set_rec_codec = set_rec_codec
        self.get_rec_compression = get_rec_compression
        self.set_rec_compression = set_rec_compression
        self.get_rec_gpu = get_rec_gpu
        self.set_rec_gpu = set_rec_gpu
        self.get_rec_codecs = get_rec_codecs
        self.get_available_gpus = get_available_gpus
        self.save_settings = save_settings
        self.start_recording = start_recording
        self.stop_recording = stop_recording
        self.recording_status = recording_status
        self.start_patrol = start_patrol
        self.stop_patrol = stop_patrol
        self.get_patrol_status = get_patrol_status
        self.get_patrol_config = get_patrol_config
        self.save_patrol_config = save_patrol_config
        self.stop_mjpeg = stop_mjpeg
        self.start_mjpeg = start_mjpeg


class _ThreadedServer(http.server.ThreadingHTTPServer):
    daemon_threads = True


def _make_handler(cam, cam_base, mjpeg, ctx, server_instance):
    """Create a request handler class with access to server context."""

    class ProxyHandler(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=PROJECT_DIR, **kwargs)

        def log_message(self, format, *args):
            pass

        def _json_response(self, data, status=200):
            """Send a JSON response."""
            body = json.dumps(data).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(body)

        def _error_json(self, status, message):
            """Send a structured JSON error response."""
            self._json_response({"error": message, "status": status}, status)

        def do_GET(self):
            parsed = urlparse(self.path)

            # Proxy: /api/cam?cmd=XXX&param=val -> camera CGI
            if parsed.path == "/api/cam":
                self._handle_cam(parsed)
                return

            if parsed.path == "/api/snap":
                self._handle_snap()
                return

            if parsed.path == "/api/mjpeg":
                self._handle_mjpeg()
                return

            if parsed.path == "/api/audio":
                self._handle_audio()
                return

            if parsed.path == "/api/settings":
                self._handle_settings(parsed)
                return

            if parsed.path == "/api/record":
                self._handle_record(parsed)
                return

            if parsed.path == "/api/patrol":
                self._handle_patrol(parsed)
                return

            if parsed.path == "/api/fmp4":
                self._handle_fmp4()
                return

            # Default: serve static files
            super().do_GET()

        def _handle_cam(self, parsed):
            qs = parse_qs(parsed.query)
            params = {k: v[0] for k, v in qs.items()}
            params["usr"] = cam["username"]
            params["pwd"] = cam["password"]
            cam_url = f"{cam_base}?{urllib.parse.urlencode(params)}"
            cmd_name = params.get("cmd", "?")
            extra_params = {k: v for k, v in params.items() if k not in ("cmd", "usr", "pwd")}
            if extra_params:
                log.debug("CGI: %s %s", cmd_name, extra_params)
            else:
                log.debug("CGI: %s", cmd_name)
            try:
                with urllib.request.urlopen(cam_url, timeout=10) as resp:
                    data = resp.read()
                try:
                    _root = ET.fromstring(data.decode())
                    _result = {c.tag: c.text or "" for c in _root}
                    _rc = _result.get("result", "?")
                    if _rc != "0":
                        log.warning("CGI: %s returned result=%s", cmd_name, _rc)
                    elif cmd_name.startswith("ptz"):
                        log.info("CGI: %s OK %s", cmd_name, extra_params)
                except Exception:
                    pass
                self.send_response(200)
                self.send_header("Content-Type", "text/xml")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(data)
            except Exception as e:
                log.error("CGI proxy error (cmd=%s): %s", cmd_name, e)
                self._error_json(502, f"Camera error: {e}")

        def _handle_snap(self):
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
            except Exception:
                self._error_json(502, "Snapshot failed")

        def _handle_mjpeg(self):
            self.connection.settimeout(30)
            ctx.start_mjpeg(cam)
            self.send_response(200)
            self.send_header("Content-Type",
                             "multipart/x-mixed-replace; boundary=ffmpeg")
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            log.info("MJPEG client connected from %s", self.client_address[0])
            try:
                last_id = 0
                no_frame_count = 0
                while not server_instance.shutting_down:
                    fid = mjpeg.frame_id
                    frame = mjpeg.frame
                    if fid > last_id and frame is not None:
                        no_frame_count = 0
                        last_id = fid
                        self.wfile.write(b"--ffmpeg\r\n")
                        self.wfile.write(b"Content-Type: image/jpeg\r\n")
                        self.wfile.write(
                            f"Content-Length: {len(frame)}\r\n".encode())
                        self.wfile.write(b"\r\n")
                        self.wfile.write(frame)
                        self.wfile.write(b"\r\n")
                        self.wfile.flush()
                    else:
                        time.sleep(0.02)
                        no_frame_count += 1
                        if no_frame_count >= 100:
                            if server_instance.shutting_down:
                                break
                            log.warning("MJPEG client: %ds no frames, requesting source restart",
                                        no_frame_count // 50)
                            ctx.start_mjpeg(cam)
                            no_frame_count = 0
            except (BrokenPipeError, ConnectionResetError, OSError):
                log.info("MJPEG client disconnected from %s", self.client_address[0])

        def _handle_audio(self):
            self.connection.settimeout(30)
            rtsp_port = cam.get("port", 88)
            rtsp_url = (f"rtsp://{cam['username']}:{cam['password']}"
                        f"@{cam['ip']}:{rtsp_port}/videoMain")
            self.send_response(200)
            self.send_header("Content-Type", "audio/mpeg")
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            transport = ctx.get_rtsp_transport()
            gain = ctx.get_mic_gain()
            probe = "500000" if transport == "tcp" else "32768"
            analyze = "500000" if transport == "tcp" else "0"
            log.info("Audio stream starting (transport=%s, gain=%.1f)", transport, gain)
            proc = None
            try:
                proc = subprocess.Popen(
                    ["ffmpeg",
                     "-fflags", "+nobuffer+flush_packets",
                     "-flags", "low_delay",
                     "-probesize", probe,
                     "-analyzeduration", analyze,
                     "-rtsp_transport", transport,
                     "-i", rtsp_url,
                     "-vn",
                     "-af", f"volume={gain}",
                     "-c:a", "libmp3lame",
                     "-b:a", "128k",
                     "-f", "mp3",
                     "-flush_packets", "1",
                     "pipe:1"],
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL
                )
                server_instance.register_proc(proc)
                while not server_instance.shutting_down:
                    chunk = proc.stdout.read(4096)
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError, OSError):
                log.info("Audio stream disconnected")
            except Exception as e:
                log.error("Audio stream error: %s", e)
            finally:
                if proc:
                    server_instance.unregister_proc(proc)
                    try:
                        proc.kill()
                    except Exception:
                        pass

        def _handle_settings(self, parsed):
            qs = parse_qs(parsed.query)
            changed = False
            if "mic_gain" in qs:
                try:
                    val = float(qs["mic_gain"][0])
                    if 1.0 <= val <= 5.0:
                        ctx.set_mic_gain(round(val, 1))
                        changed = True
                except (ValueError, IndexError):
                    pass
            if "rec_codec" in qs:
                val = qs["rec_codec"][0]
                if val in ctx.get_rec_codecs():
                    ctx.set_rec_codec(val)
                    changed = True
            if "rec_compression" in qs:
                try:
                    val = int(qs["rec_compression"][0])
                    if 1 <= val <= 10:
                        ctx.set_rec_compression(val)
                        changed = True
                except (ValueError, IndexError):
                    pass
            if "rec_gpu" in qs:
                val = qs["rec_gpu"][0]
                valid = {"auto"} | {idx for idx, _ in ctx.get_available_gpus()}
                if val in valid:
                    ctx.set_rec_gpu(val)
                    changed = True
            if "rtsp_transport" in qs:
                val = qs["rtsp_transport"][0]
                if val in ("udp", "tcp"):
                    ctx.set_rtsp_transport(val)
                    changed = True
                    ctx.stop_mjpeg()
                    log.info("RTSP transport changed to %s, MJPEG source will restart on next request", val)
            if changed:
                ctx.save_settings()
            codecs_info = {k: {"desc": v[1]}
                           for k, v in ctx.get_rec_codecs().items()}
            gpus_info = [{"index": idx, "name": name}
                         for idx, name in ctx.get_available_gpus()]
            self._json_response({
                "mic_gain": ctx.get_mic_gain(),
                "stream_quality": ctx.get_stream_quality(),
                "rec_codec": ctx.get_rec_codec(),
                "rec_compression": ctx.get_rec_compression(),
                "rec_gpu": ctx.get_rec_gpu(),
                "rtsp_transport": ctx.get_rtsp_transport(),
                "rec_codecs": codecs_info,
                "gpus": gpus_info,
            })

        def _handle_record(self, parsed):
            qs = parse_qs(parsed.query)
            action = qs.get("action", ["status"])[0]
            if action == "start":
                ctx.start_recording()
            elif action == "stop":
                ctx.stop_recording()
            self._json_response(ctx.recording_status())

        def _handle_patrol(self, parsed):
            qs = parse_qs(parsed.query)
            action = qs.get("action", ["status"])[0]
            if action == "start":
                result = ctx.start_patrol()
            elif action == "stop":
                result = ctx.stop_patrol()
            elif action == "config":
                patrol_data = {}
                if "positions" in qs:
                    try:
                        patrol_data["positions"] = json.loads(qs["positions"][0])
                    except (ValueError, KeyError):
                        pass
                if "repeat" in qs:
                    patrol_data["repeat"] = qs["repeat"][0] == "true"
                if patrol_data:
                    current = ctx.get_patrol_config()
                    current.update(patrol_data)
                    ctx.save_patrol_config(current)
                result = ctx.get_patrol_config()
            else:
                result = ctx.get_patrol_status()
            result_with_status = dict(ctx.get_patrol_status())
            if isinstance(result, dict) and "ok" in result:
                result_with_status.update(result)
            elif action == "config":
                result_with_status["config"] = result
            self._json_response(result_with_status)

        def _handle_fmp4(self):
            self.connection.settimeout(30)
            rtsp_port = cam.get("port", 88)
            rtsp_url = (f"rtsp://{cam['username']}:{cam['password']}"
                        f"@{cam['ip']}:{rtsp_port}/videoMain")
            self.send_response(200)
            self.send_header("Content-Type", "video/mp4")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            transport = ctx.get_rtsp_transport()
            gain = ctx.get_mic_gain()
            probe = "500000" if transport == "tcp" else "32768"
            analyze = "500000" if transport == "tcp" else "0"
            gain_filter = f"volume={gain:.1f}" if gain != 1.0 else "volume=1.0"
            log.info("fMP4 stream starting (transport=%s, gain=%.1f, client=%s)",
                     transport, gain, self.client_address[0])
            proc = None
            try:
                proc = subprocess.Popen(
                    ["ffmpeg",
                     "-fflags", "+nobuffer+flush_packets+genpts",
                     "-flags", "low_delay",
                     "-probesize", probe,
                     "-analyzeduration", analyze,
                     "-rtsp_transport", transport,
                     "-i", rtsp_url,
                     "-c:v", "copy",
                     "-c:a", "aac", "-b:a", "128k",
                     "-af", gain_filter,
                     "-f", "mp4",
                     "-movflags", "frag_keyframe+empty_moov+default_base_moof",
                     "-frag_duration", "500000",
                     "-min_frag_duration", "250000",
                     "-flush_packets", "1",
                     "pipe:1"],
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE
                )
                server_instance.register_proc(proc)
                while not server_instance.shutting_down:
                    chunk = proc.stdout.read(4096)
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError, OSError):
                log.info("fMP4 stream disconnected")
            except Exception as e:
                log.error("fMP4 stream error: %s", e)
            finally:
                if proc:
                    server_instance.unregister_proc(proc)
                    try:
                        proc.kill()
                    except Exception:
                        pass

    return ProxyHandler

#!/data/data/com.termux/files/usr/bin/python3
"""
RTSP to HLS streaming server with automatic reconnection.
Camera file browser proxy — fetches from camera so browser can browse without CORS.
"""
import subprocess, os, signal, sys, re, time, threading
try:
    from http.client import HTTPConnection
except ImportError:
    from httplib import HTTPConnection
from http.server import HTTPServer, SimpleHTTPRequestHandler

STREAM_DIR  = "/data/data/com.termux/files/home/.stream"
HLS_URL     = "rtsp://192.168.167.40:554/live"
CAM_HOST    = "192.168.167.40"
CAM_PORT    = 80
PORT        = 8080
ASSETS_DIR  = os.path.join(os.path.dirname(os.path.dirname(__file__)), "assets")

os.makedirs(STREAM_DIR, exist_ok=True)

_cleaned = False
def clean_stream_dir():
    global _cleaned
    if _cleaned: return
    _cleaned = True
    for item in os.listdir(STREAM_DIR):
        p = os.path.join(STREAM_DIR, item)
        try:
            if os.path.isdir(p):
                for f in os.listdir(p): os.unlink(os.path.join(p, f))
                os.rmdir(p)
            else: os.unlink(p)
        except: pass

clean_stream_dir()
DEVNULL = open(os.devnull, 'wb')
proc = None

def start_ffmpeg():
    global proc
    ffmpeg_cmd = [
        "ffmpeg", "-rtsp_transport", "tcp", "-re", "-i", HLS_URL,
        "-c:v", "copy", "-f", "hls",
        "-hls_time", "2", "-hls_list_size", "30", "-hls_flags", "append_list",
        "-reconnect", "1", "-reconnect_streamed", "1", "-reconnect_delay_max", "5",
        os.path.join(STREAM_DIR, "stream.m3u8"),
    ]
    proc = subprocess.Popen(ffmpeg_cmd, stdout=DEVNULL.fileno(), stderr=DEVNULL.fileno())
    print(f"[{time.strftime('%H:%M:%S')}] ffmpeg started (pid={proc.pid})")

start_ffmpeg()

def watchdog():
    while True:
        time.sleep(5)
        if proc is None:
            start_ffmpeg()
        elif proc.poll() is not None:
            print(f"[{time.strftime('%H:%M:%S')}] ffmpeg died, restarting...")
            start_ffmpeg()

threading.Thread(target=watchdog, daemon=True).start()
print(f"Stream : {HLS_URL}")
print(f"Output : {STREAM_DIR}")
print(f"HTTP   : http://0.0.0.0:{PORT}/")

signal.signal(signal.SIGTERM, lambda s,f: (DEVNULL.close(), sys.exit(0)))
signal.signal(signal.SIGINT,  lambda s,f: (DEVNULL.close(), sys.exit(0)))


class HLSHandler(SimpleHTTPRequestHandler):

    def _proxy_camera(self, path):
        """Fetch a path from the camera HTTP server and relay to browser (streaming)."""
        try:
            conn = HTTPConnection(CAM_HOST, CAM_PORT, timeout=15)
            conn.request("GET", path, headers={"Host": CAM_HOST, "User-Agent": "IONIQ6-Proxy/1.0"})
            resp = conn.getresponse()
            self.send_response(resp.status)
            for h, v in resp.getheaders():
                if h.lower() not in ("transfer-encoding", "connection", "keep-alive"):
                    self.send_header(h, v)
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            # Stream in 64KB chunks instead of buffering whole file
            while True:
                chunk = resp.read(65536)
                if not chunk:
                    break
                self.wfile.write(chunk)
            conn.close()
        except Exception as e:
            self.send_error(500, f"Camera unreachable: {e}")

    def do_GET(self):
        # ── Camera file browser proxy ─────────────────────────────────
        if self.path.startswith("/cam/"):
            # /cam/DCIM/Movie/Parking → fetch from camera
            cam_path = self.path[4:]   # strip /cam
            if not cam_path.startswith("/"):
                cam_path = "/" + cam_path
            self._proxy_camera(cam_path)
            return

        # ── Stream playlist ────────────────────────────────────────────
        candidates = [
            (os.path.join(STREAM_DIR, "stream.m3u8"), ""),
            (os.path.join(STREAM_DIR, "ch1", "stream.m3u8"), "ch1/"),
        ]
        m3u8_path, ch1 = None, ""
        for p, sub in candidates:
            if os.path.exists(p):
                m3u8_path, ch1 = p, sub
                break

        if self.path.startswith("/stream.m3u8"):
            if m3u8_path and os.path.exists(m3u8_path):
                c = open(m3u8_path).read()
                if ch1:
                    def fix(m): return ch1 + m.group(1) + ".ts"
                    c = re.sub(r'(stream\d+\.ts)', fix, c)
                c = c.replace("#EXT-X-ENDLIST\n", "").replace("#EXT-X-ENDLIST", "")
                self.send_response(200)
                self.send_header("Content-Type", "application/vnd.apple.mpegurl")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Cache-Control", "no-cache, no-store")
                self.end_headers()
                self.wfile.write(c.encode())
            else:
                self.send_error(503, "Stream not ready")
            return

        # ── Segment files ─────────────────────────────────────────────
        if ".ts" in self.path and not self.path.startswith("/."):
            ts = self.path.split("?")[0].lstrip("/")
            ts_path = os.path.join(STREAM_DIR, ts)
            if os.path.exists(ts_path):
                self.send_response(200)
                self.send_header("Content-Type", "video/mp2t")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Cache-Control", "no-cache, max-age=0")
                self.end_headers()
                self.wfile.write(open(ts_path, 'rb').read())
            else:
                # Fallback: serve closest available segment
                m = re.search(r'stream(\d+)\.ts', ts)
                if m:
                    all_files = [(int(re.match(r'stream(\d+)\.ts', f).group(1)), f)
                                 for f in os.listdir(STREAM_DIR)
                                 if re.match(r'stream\d+\.ts', f)]
                    if all_files:
                        all_files.sort()
                        req_num = int(m.group(1))
                        closest = min(all_files, key=lambda x: abs(x[0] - req_num))
                        self.send_response(200)
                        self.send_header("Content-Type", "video/mp2t")
                        self.send_header("Access-Control-Allow-Origin", "*")
                        self.send_header("Cache-Control", "no-cache, max-age=0")
                        self.end_headers()
                        self.wfile.write(open(os.path.join(STREAM_DIR, closest[1]), 'rb').read())
                        return
                self.send_error(404, "not found")
            return

        # ── Static files ──────────────────────────────────────────────
        if self.path in ("/", "/index.html"):
            ipath = os.path.join(ASSETS_DIR, "index.html")
            if os.path.exists(ipath):
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.end_headers()
                self.wfile.write(open(ipath).read().encode())
            else:
                self.send_error(404, "index.html not found")
            return

        super().do_GET()

    def log_message(self, *args): pass


server = HTTPServer(("0.0.0.0", PORT), HLSHandler)
server.allow_reuse_address = True
server.serve_forever()

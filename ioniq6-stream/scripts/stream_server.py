#!/data/data/com.termux/files/usr/bin/python3
"""
RTSP to HLS streaming server with camera proxy and on-demand MP4 transcoding.
"""
import subprocess, os, signal, sys, re, time, threading, json
try:
    from http.client import HTTPConnection
except ImportError:
    from httplib import HTTPConnection
from http.server import HTTPServer, SimpleHTTPRequestHandler

STREAM_DIR   = "/data/data/com.termux/files/home/.stream"
TRANSCOD_DIR = "/data/data/com.termux/files/home/.transcode"
CAM_PORT     = 80
PORT         = 8080
ASSETS_DIR   = os.path.join(os.path.dirname(os.path.dirname(__file__)), "assets")
STATE_FILE   = os.path.expanduser("~/.camera_ip")

os.makedirs(STREAM_DIR,   exist_ok=True)
os.makedirs(TRANSCOD_DIR, exist_ok=True)

# ── Camera discovery ─────────────────────────────────────────────────────────
def _is_camera_http(ip, timeout=4):
    try:
        conn = HTTPConnection(ip, CAM_PORT, timeout=timeout)
        conn.request("GET", "/")
        resp = conn.getresponse()
        server = resp.getheader("Server", "").lower()
        conn.close()
        for sig in ("hfs", "busybox", "viofo"):
            if sig in server:
                return True
        if ip.startswith("192.168."):
            return True
    except Exception:
        pass
    return False

def _scan_subnet(subnet):
    try:
        result = subprocess.run(
            ["nmap", "-sn", "-PS80", "-T4", "--max-retries", "1",
             "--max-scan-delay", "100ms", "-oG", "-", subnet],
            capture_output=True, text=True, timeout=60
        )
        for line in result.stdout.splitlines():
            if "Host:" in line and "Status: Up" in line:
                for p in line.split():
                    if p[0].isdigit() and p.count(".") == 3:
                        if _is_camera_http(p):
                            return p
    except Exception:
        pass
    return None

def find_camera_ip():
    if os.path.exists(STATE_FILE):
        cached = open(STATE_FILE).read().strip()
        if cached and _is_camera_http(cached):
            return cached
    for subnet in ["192.168.109.0/24", "192.168.167.0/24"]:
        found = _scan_subnet(subnet)
        if found:
            open(STATE_FILE, "w").write(found)
            return found
    return "192.168.109.40"

CAM_HOST = find_camera_ip()
HLS_URL  = f"rtsp://{CAM_HOST}:554/live"
print(f"Camera : {CAM_HOST}")
print(f"Stream : {HLS_URL}")
print(f"Output : {STREAM_DIR}")
print(f"HTTP   : http://0.0.0.0:{PORT}/")

# ── FFmpeg RTSP→HLS watchdog ──────────────────────────────────────────────────
def start_ffmpeg():
    try:
        for f in os.listdir(STREAM_DIR):
            if f.endswith((".ts", ".m3u8")):
                os.unlink(os.path.join(STREAM_DIR, f))
    except Exception:
        pass
    cmd = [
        "ffmpeg", "-rtsp_transport", "tcp", "-re",
        "-i", HLS_URL,
        "-c:v", "copy",
        "-f", "hls",
        "-hls_time", "2",
        "-hls_list_size", "30",
        "-hls_flags", "append_list",
        "-reconnect", "1", "-reconnect_streamed", "1", "-reconnect_delay_max", "5",
        os.path.join(STREAM_DIR, "stream.m3u8")
    ]
    return subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

_ffmpeg_pid  = None
_ffmpeg_lock = threading.Lock()

def keep_ffmpeg():
    global _ffmpeg_pid
    with _ffmpeg_lock:
        if _ffmpeg_pid is None or _ffmpeg_pid.poll() is not None:
            _ffmpeg_pid = start_ffmpeg()
            print(f"[{time.strftime('%H:%M:%S')}] ffmpeg started (pid={_ffmpeg_pid.pid})")
            threading.Timer(5, keep_ffmpeg).start()
        else:
            threading.Timer(10, keep_ffmpeg).start()

def restart_ffmpeg():
    global _ffmpeg_pid
    with _ffmpeg_lock:
        if _ffmpeg_pid:
            try: _ffmpeg_pid.terminate()
            except Exception: pass
        _ffmpeg_pid = start_ffmpeg()
        print(f"[{time.strftime('%H:%M:%S')}] ffmpeg restarted (pid={_ffmpeg_pid.pid})")

threading.Timer(5, keep_ffmpeg).start()

# ── Transcode sessions ───────────────────────────────────────────────────────
# Key = safe session ID, Value = {path, proc, m3u8, dir, last_access}
transcode_sessions = {}
_xcode_lock = threading.Lock()
TRANSCODE_SEGMENTS  = 30
TRANSCODE_KEEPALIVE = 600  # 10 min idle = cleanup

def _make_session_id(cam_path):
    """Safe filename from a camera path like /DCIM/Movie/RO/ABC123.MP4"""
    safe = re.sub(r'[^a-zA-Z0-9_-]', '_', cam_path)
    return safe[:80]

def start_transcode(cam_path, session_id):
    out_dir = os.path.join(TRANSCOD_DIR, session_id)
    os.makedirs(out_dir, exist_ok=True)
    m3u8_path = os.path.join(out_dir, "trans.m3u8")

    cmd = [
        "ffmpeg", "-re",
        "-i", f"http://{CAM_HOST}/{cam_path.lstrip('/')}",
        "-vf", "scale=640:-2",
        "-c:v", "libx264",
        "-preset", "ultrafast",
        "-crf", "28",
        "-maxrate", "500k",
        "-bufsize", "1000k",
        "-g", "30",
        "-keyint_min", "30",
        "-sc_threshold", "0",
        "-an",
        "-f", "hls",
        "-hls_time", "3",
        "-hls_list_size", str(TRANSCODE_SEGMENTS),
        "-hls_flags", "append_list+delete_out_of_range",
        "-hls_segment_filename", os.path.join(out_dir, "seg_%03d.ts"),
        m3u8_path
    ]
    proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return proc, m3u8_path, out_dir

def get_transcode_url(cam_path):
    session_id = _make_session_id(cam_path)
    now = time.time()
    with _xcode_lock:
        if session_id in transcode_sessions:
            sess = transcode_sessions[session_id]
            sess["last_access"] = now
            if sess["proc"].poll() is not None:
                # Restart dead transcoder
                proc, m3u8, out_dir = start_transcode(cam_path, session_id)
                sess["proc"] = proc
                sess["m3u8"] = m3u8
                sess["dir"]  = out_dir
                sess["path"] = cam_path
            return f"/transcode/{session_id}/trans.m3u8"
        else:
            proc, m3u8, out_dir = start_transcode(cam_path, session_id)
            transcode_sessions[session_id] = {
                "path": cam_path, "proc": proc,
                "m3u8": m3u8, "dir": out_dir,
                "last_access": now
            }
            return f"/transcode/{session_id}/trans.m3u8"

def _cleanup_transcode():
    now = time.time()
    with _xcode_lock:
        for sid, sess in list(transcode_sessions.items()):
            if now - sess["last_access"] > TRANSCODE_KEEPALIVE:
                sess["proc"].terminate()
                try: sess["proc"].wait(2)
                except Exception: sess["proc"].kill()
                import shutil
                try: shutil.rmtree(sess["dir"])
                except Exception: pass
                del transcode_sessions[sid]

threading.Timer(60, lambda: threading.Thread(target=_cleanup_transcode, daemon=True).start()).start()

# ── HTTP Handler ─────────────────────────────────────────────────────────────
class HLSHandler(SimpleHTTPRequestHandler):

    def log_message(self, *args): pass

    def _proxy_camera(self, path):
        try:
            conn = HTTPConnection(CAM_HOST, CAM_PORT, timeout=20)
            conn.request("GET", path, headers={"Host": CAM_HOST})
            resp = conn.getresponse()
            self.send_response(resp.status)
            for h, v in resp.getheaders():
                if h.lower() not in ("transfer-encoding", "connection", "keep-alive"):
                    self.send_header(h, v)
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            while True:
                chunk = resp.read(65536)
                if not chunk: break
                self.wfile.write(chunk)
            conn.close()
        except Exception as e:
            self.send_error(500, f"Camera unreachable: {e}")

    def _delete_camera(self, path):
        try:
            conn = HTTPConnection(CAM_HOST, CAM_PORT, timeout=15)
            conn.request("DELETE", path, headers={"Host": CAM_HOST})
            resp = conn.getresponse()
            self.send_response(resp.status)
            for h, v in resp.getheaders():
                if h.lower() not in ("transfer-encoding", "connection", "keep-alive"):
                    self.send_header(h, v)
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            body = resp.read()
            self.wfile.write(body if body else b"")
            conn.close()
        except Exception as e:
            self.send_error(500, f"Camera delete failed: {e}")

    def do_DELETE(self):
        p = str(self.path)
        if p.startswith("/cam/"):
            cam_path = "/" + p[4:].lstrip("/")
            self._delete_camera(cam_path)
            return
        self.send_error(405, "Method not allowed")

    def do_GET(self):
        p = str(self.path)

        # Camera proxy
        if p.startswith("/cam/"):
            cam_path = "/" + p[4:].lstrip("/")
            self._proxy_camera(cam_path)
            return

        # Transcode session keepalive
        if p.startswith("/transcode/keepalive/"):
            sid = p.split("/", 3)[3]
            with _xcode_lock:
                if sid in transcode_sessions:
                    transcode_sessions[sid]["last_access"] = time.time()
                    self.send_response(204)
                    self.end_headers()
                    return
            self.send_error(404, "Session not found")
            return

        # Transcode HLS files
        if p.startswith("/transcode/"):
            parts = p.split("/", 3)
            if len(parts) >= 4:
                sid  = parts[2]
                rest = parts[3]
                with _xcode_lock:
                    valid = sid in transcode_sessions
                if not valid:
                    self.send_error(404, "Session expired")
                    return
                full = os.path.join(TRANSCOD_DIR, sid, rest)
                if os.path.isfile(full):
                    if rest.endswith(".m3u8"):
                        content = open(full).read()
                        content = re.sub(r"#EXT-X-ENDLIST.*", "", content)
                        self.send_response(200)
                        self.send_header("Content-Type", "application/vnd.apple.mpegurl")
                        self.send_header("Access-Control-Allow-Origin", "*")
                        self.send_header("Cache-Control", "no-cache")
                        self.end_headers()
                        self.wfile.write(content.encode())
                        # Touch keepalive
                        with _xcode_lock:
                            if sid in transcode_sessions:
                                transcode_sessions[sid]["last_access"] = time.time()
                    elif rest.endswith(".ts"):
                        self.send_response(200)
                        self.send_header("Content-Type", "video/mp2t")
                        self.send_header("Access-Control-Allow-Origin", "*")
                        self.send_header("Cache-Control", "max-age=3600")
                        self.end_headers()
                        self.wfile.write(open(full, "rb").read())
                    return
            self.send_error(404, "Transcode file not found")
            return

        # Stream playlist
        if ".m3u8" in p:
            m3u8_path = os.path.join(STREAM_DIR, "stream.m3u8")
            if os.path.exists(m3u8_path):
                content = open(m3u8_path).read()
                content = re.sub(r"#EXT-X-ENDLIST.*", "", content)
                self.send_response(200)
                self.send_header("Content-Type", "application/vnd.apple.mpegurl")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Cache-Control", "no-cache")
                self.end_headers()
                self.wfile.write(content.encode())
            else:
                self.send_error(503, "Stream not ready")
            return

        # Segment files
        ts_match = re.search(r"(stream\d+\.ts)", p)
        if ts_match:
            ts_file = ts_match.group(1)
            ts_path = os.path.join(STREAM_DIR, ts_file)
            if os.path.exists(ts_path):
                self.send_response(200)
                self.send_header("Content-Type", "video/mp2t")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Cache-Control", "max-age=0")
                self.end_headers()
                self.wfile.write(open(ts_path, "rb").read())
            else:
                files = sorted([f for f in os.listdir(STREAM_DIR) if f.endswith(".ts")],
                              key=lambda x: int(re.search(r"\d+", x).group(1)))
                if files:
                    fallback = files[-1]
                    self.send_response(200)
                    self.send_header("Content-Type", "video/mp2t")
                    self.send_header("Access-Control-Allow-Origin", "*")
                    self.send_header("Cache-Control", "no-cache")
                    self.end_headers()
                    self.wfile.write(open(os.path.join(STREAM_DIR, fallback), "rb").read())
                else:
                    self.send_error(503, "No segments available")
            return

        # Assets
        asset_path = os.path.join(ASSETS_DIR, p.lstrip("/"))
        if os.path.isfile(asset_path):
            return SimpleHTTPRequestHandler.do_GET(self)

        # Root → index.html
        if p in ("/", "/index.html"):
            idx = os.path.join(ASSETS_DIR, "index.html")
            if os.path.exists(idx):
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.end_headers()
                self.wfile.write(open(idx, "rb").read())
                return

        self.send_error(404, "File not found")

    def do_POST(self):
        """Start transcode: POST /transcode {path: '/DCIM/.../file.mp4'}"""
        if self.path == "/transcode":
            clen = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(clen) if clen > 0 else b"{}"
            try:
                data = json.loads(body.decode())
                cam_path = data.get("path", "")
                if not cam_path:
                    self.send_error(400, "path required")
                    return
                m3u8_url = get_transcode_url(cam_path)
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(json.dumps({"m3u8": m3u8_url}).encode())
            except Exception as e:
                self.send_error(500, str(e))
            return
        self.send_error(404, "Not found")

server = HTTPServer(("0.0.0.0", PORT), HLSHandler)
server.allow_reuse_address = True
print(f"[{time.strftime('%H:%M:%S')}] Server ready on :{PORT}")
server.serve_forever()
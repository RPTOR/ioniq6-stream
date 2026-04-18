import subprocess, os, re, time, threading, json, signal

STREAM_DIR   = "/data/data/com.termux/files/home/.stream"
TRANSCOD_DIR = "/data/data/com.termux/files/home/.transcode"
CAM_PORT     = 80
PORT         = 8080
ASSETS_DIR   = os.path.join(os.path.dirname(os.path.dirname(__file__)), "assets")

os.makedirs(STREAM_DIR,   exist_ok=True)
os.makedirs(TRANSCOD_DIR, exist_ok=True)

signal.signal(signal.SIGCHLD, signal.SIG_IGN)

def find_camera_ip():
    """Find camera IP: ARP cache first, then probe .40 on known subnets."""
    STATE_FILE = os.path.expanduser("~/.camera_ip")
    def _is(ip, timeout=4):
        try:
            from http.client import HTTPConnection
            c = HTTPConnection(ip, CAM_PORT, timeout=timeout)
            c.request("GET", "/")
            r = c.getresponse()
            srv = r.getheader("Server","").lower()
            c.close()
            return "hfs" in srv or "busybox" in srv
        except: return False
    # 1. Try ARP cache
    try:
        r = subprocess.run(["ip","neigh","show"], capture_output=True, text=True, timeout=10)
        for line in r.stdout.splitlines():
            if "50:41:1c" in line.lower():  # VIOFO MAC prefix
                ip = line.split()[0]
                if _is(ip):
                    open(STATE_FILE,"w").write(ip)
                    return ip
    except: pass
    # 2. Probe .40 on each known subnet (camera always at .40 on VIOFO AP)
    for subnet in ["192.168.177.0/24", "192.168.167.0/24", "192.168.109.0/24", "192.168.194.0/24"]:
        base = subnet.rsplit(".", 1)[0]
        ip = "%s.40" % base
        if _is(ip):
            open(STATE_FILE,"w").write(ip)
            return ip
    # 3. Nmap scan as last resort
    for subnet in ["192.168.177.0/24", "192.168.167.0/24", "192.168.109.0/24", "192.168.194.0/24"]:
        try:
            r = subprocess.run(["nmap","-sn","-PS80","-T4","-oG","-",subnet],
                               capture_output=True, text=True, timeout=60)
            for line in r.stdout.splitlines():
                if "Status: Up" in line:
                    for p in line.split():
                        if p[0].isdigit() and p.count(".")==3 and _is(p):
                            open(STATE_FILE,"w").write(p); return p
        except: pass
    # 4. Return cached stale IP (don't overwrite with wrong fallback)
    if os.path.exists(STATE_FILE):
        cached = open(STATE_FILE).read().strip()
        if cached: return cached
    return "192.168.194.40"  # final fallback (android tether subnet)

def _read_camera_ip():
    """Read current camera IP from state file; fall back to find_camera_ip()."""
    sf = os.path.expanduser("~/.camera_ip")
    if os.path.exists(sf):
        cached = open(sf).read().strip()
        if cached:
            try:
                from http.client import HTTPConnection
                c = HTTPConnection(cached, CAM_PORT, timeout=3)
                c.request("GET", "/")
                r = c.getresponse()
                srv = r.getheader("Server","").lower()
                c.close()
                if "hfs" in srv or "busybox" in srv:
                    return cached
            except: pass
    return find_camera_ip()

CAM_HOST = _read_camera_ip()
HLS_URL  = "rtsp://%s:554/live" % CAM_HOST

# ── RTSP ffmpeg watchdog ─────────────────────────────────────────────────────
_ffmpeg_proc = None
_ffmpeg_lock = threading.Lock()
MAX_STREAM_SEGMENTS = 35

def _cleanup_stream_segments():
    """Remove old stream segments, keeping only the most recent MAX_STREAM_SEGMENTS."""
    try:
        segs = [f for f in os.listdir(STREAM_DIR)
                if f.startswith("stream") and f.endswith(".ts")]
        if len(segs) <= MAX_STREAM_SEGMENTS:
            return
        def seq_num(f):
            try: return int(f.replace("stream","").replace(".ts",""))
            except: return 0
        segs.sort(key=seq_num)
        old = segs[:-MAX_STREAM_SEGMENTS]
        for f in old:
            try: os.unlink(os.path.join(STREAM_DIR, f))
            except: pass
        if old:
            print("[%s] cleaned %d stale stream segments" % (time.strftime("%H:%M:%S"), len(old)))
    except Exception as e:
        print("cleanup error: %s" % e)

def start_rtsp_ffmpeg():
    import subprocess as _subprocess
    # Re-resolve camera IP each time to handle IP changes
    cam_ip = _read_camera_ip()
    hls_url = "rtsp://%s:554/live" % cam_ip
    cmd = ["ffmpeg",
           "-rtsp_transport", "tcp", "-re", "-fflags", "+genpts", "-use_wallclock_as_timestamps", "1", "-i", hls_url,
           "-c:v", "copy", "-f", "hls",
           "-hls_time", "2", "-hls_list_size", "30",
           "-hls_flags", "append_list",
           "-reconnect", "1", "-reconnect_streamed", "1",
           "-reconnect_delay_max", "5",
           os.path.join(STREAM_DIR, "stream.m3u8")]
    stderr_file = open(os.path.join(STREAM_DIR, "ffmpeg.log"), "a")
    proc = _subprocess.Popen(cmd, stdout=_subprocess.DEVNULL, stderr=stderr_file,
                             start_new_session=True)
    stderr_file.close()
    return proc

def keep_ffmpeg():
    global _ffmpeg_proc
    with _ffmpeg_lock:
        if _ffmpeg_proc is not None and _ffmpeg_proc.poll() is not None:
            _ffmpeg_proc = None
        try:
            segs = [f for f in os.listdir(STREAM_DIR)
                    if f.startswith("stream") and f.endswith(".ts")]
            if segs:
                newest = max(os.path.getmtime(os.path.join(STREAM_DIR, f)) for f in segs)
                age = time.time() - newest
                if age > 20:
                    if _ffmpeg_proc is not None and _ffmpeg_proc.poll() is None:
                        _ffmpeg_proc.terminate()
                    _ffmpeg_proc = None
        except: pass
        if _ffmpeg_proc is None:
            _ffmpeg_proc = start_rtsp_ffmpeg()
            print("[%s] ffmpeg pid=%s started (ip=%s)" % (
                time.strftime("%H:%M:%S"), _ffmpeg_proc.pid, _read_camera_ip()))
        _cleanup_stream_segments()
        threading.Timer(10, keep_ffmpeg).start()

threading.Timer(5, keep_ffmpeg).start()

# ── Transcode sessions ─────────────────────────────────────────────────────────
transcode_sessions = {}
_xcode_lock = threading.Lock()
TRANSCODE_SEGMENTS  = 30
TRANSCODE_KEEPALIVE = 600

def _session_id(cam_path):
    return re.sub(r"[^a-zA-Z0-9_-]", "_", cam_path)[:80]

def start_transcode(cam_path, sid):
    out_dir = os.path.join(TRANSCOD_DIR, sid)
    os.makedirs(out_dir, exist_ok=True)
    m3u8_path = os.path.join(out_dir, "trans.m3u8")
    stderr_log = open(os.path.join(out_dir, "ffmpeg.log"), "a")
    cmd = [
        "ffmpeg",
        "-user_agent", "Mozilla/5.0 (compatible; DashcamClient/1.0)",
        "-re",
        "-i", "http://%s/%s" % (_read_camera_ip(), cam_path.lstrip("/")),
        "-vf", "scale=640:-2",
        "-c:v", "libx264",
        "-preset", "ultrafast",
        "-crf", "28",
        "-an",
        "-f", "hls",
        "-hls_time", "3",
        "-hls_list_size", str(TRANSCODE_SEGMENTS),
        "-hls_flags", "append_list",
        "-hls_segment_filename", os.path.join(out_dir, "seg_%03d.ts"),
        "-y",
        m3u8_path
    ]
    proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=stderr_log,
                             start_new_session=True)
    stderr_log.close()
    return proc, m3u8_path, out_dir

def get_transcode_url(cam_path):
    sid = _session_id(cam_path)
    now = time.time()
    with _xcode_lock:
        if sid in transcode_sessions:
            sess = transcode_sessions[sid]
            sess["last_access"] = now
            out_dir = sess["dir"]
            try:
                segs = [f for f in os.listdir(out_dir) if f.startswith("seg_") and f.endswith(".ts")]
                if segs:
                    newest = max(os.path.getmtime(os.path.join(out_dir, f)) for f in segs)
                    if now - newest > 30:
                        proc, m3u8, out_dir = start_transcode(cam_path, sid)
                        sess["proc"] = proc; sess["m3u8"] = m3u8
                        sess["dir"] = out_dir; sess["path"] = cam_path
            except: pass
            return "/transcode/%s/trans.m3u8" % sid
        else:
            proc, m3u8, out_dir = start_transcode(cam_path, sid)
            transcode_sessions[sid] = {
                "path": cam_path, "proc": proc,
                "m3u8": m3u8, "dir": out_dir,
                "last_access": now
            }
            return "/transcode/%s/trans.m3u8" % sid

def _cleanup():
    now = time.time()
    with _xcode_lock:
        for sid, sess in list(transcode_sessions.items()):
            if now - sess["last_access"] > TRANSCODE_KEEPALIVE:
                try: sess["proc"].terminate()
                except: pass
                try: sess["proc"].wait(2)
                except: sess["proc"].kill()
                import shutil
                try: shutil.rmtree(sess["dir"])
                except: pass
                del transcode_sessions[sid]

threading.Timer(60, lambda: threading.Thread(target=_cleanup, daemon=True).start()).start()

# ── HTTP Handler ─────────────────────────────────────────────────────────────
from http.server import HTTPServer, SimpleHTTPRequestHandler, ThreadingHTTPServer

class Handler(SimpleHTTPRequestHandler):
    protocol_version = "HTTP/1.0"
    def log_message(self, *args): pass

    def _proxy_camera(self, path):
        try:
            from http.client import HTTPConnection
            conn = HTTPConnection(_read_camera_ip(), CAM_PORT, timeout=20)
            conn.request("GET", path, headers={"Host": _read_camera_ip()})
            resp = conn.getresponse()
            self.send_response(resp.status)
            for h, v in resp.getheaders():
                if h.lower() not in ("transfer-encoding","connection","keep-alive"):
                    self.send_header(h, v)
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            while True:
                chunk = resp.read(65536)
                if not chunk: break
                self.wfile.write(chunk)
            conn.close()
        except Exception as e:
            self.send_error(500, "Camera unreachable: %s" % e)

    def _delete_camera(self, path):
        try:
            from http.client import HTTPConnection
            conn = HTTPConnection(_read_camera_ip(), CAM_PORT, timeout=15)
            conn.request("DELETE", path, headers={"Host": _read_camera_ip()})
            resp = conn.getresponse()
            self.send_response(resp.status)
            for h, v in resp.getheaders():
                if h.lower() not in ("transfer-encoding","connection","keep-alive"):
                    self.send_header(h, v)
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            body = resp.read()
            self.wfile.write(body if body else b"")
            conn.close()
        except Exception as e:
            self.send_error(500, "Camera delete failed: %s" % e)

    def do_DELETE(self):
        p = str(self.path)
        if p.startswith("/cam/"):
            cam_path = "/" + p[4:].lstrip("/")
            self._delete_camera(cam_path)
            return
        self.send_error(405, "Method not allowed")

    def do_GET(self):
        p = str(self.path)

        if p.startswith("/cam/"):
            cam_path = "/" + p[4:].lstrip("/")
            self._proxy_camera(cam_path)
            return

        if p.startswith("/transcode/keepalive/"):
            sid = p.split("/", 3)[3]
            with _xcode_lock:
                if sid in transcode_sessions:
                    transcode_sessions[sid]["last_access"] = time.time()
                    self.send_response(204); self.end_headers(); return
            self.send_error(404, "Session not found"); return

        if p.startswith("/transcode/"):
            parts = p.split("/", 3)
            if len(parts) >= 4:
                sid, rest = parts[2], parts[3]
                with _xcode_lock: valid = sid in transcode_sessions
                if not valid:
                    self.send_error(404, "Session expired"); return
                full = os.path.join(TRANSCOD_DIR, sid, rest)
                if os.path.isfile(full):
                    if rest.endswith(".m3u8"):
                        content = re.sub(r"#EXT-X-ENDLIST.*", "", open(full).read())
                        self.send_response(200)
                        self.send_header("Content-Type", "application/vnd.apple.mpegurl")
                        self.send_header("Access-Control-Allow-Origin", "*")
                        self.send_header("Cache-Control", "no-cache")
                        self.end_headers()
                        self.wfile.write(content.encode())
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
            self.send_error(404, "Transcode file not found"); return

        if ".m3u8" in p:
            mp = os.path.join(STREAM_DIR, "stream.m3u8")
            if os.path.exists(mp):
                content = re.sub(r"#EXT-X-ENDLIST.*", "", open(mp).read())
                self.send_response(200)
                self.send_header("Content-Type", "application/vnd.apple.mpegurl")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Cache-Control", "no-cache")
                self.end_headers()
                self.wfile.write(content.encode())
            else:
                self.send_error(503, "Stream not ready"); return

        ts_match = re.search(r"(stream\d+\.ts)", p)
        if ts_match:
            ts_path = os.path.join(STREAM_DIR, ts_match.group(1))
            if os.path.exists(ts_path):
                self.send_response(200)
                self.send_header("Content-Type", "video/mp2t")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Cache-Control", "no-cache")
                self.end_headers()
                self.wfile.write(open(ts_path, "rb").read())
            else:
                files = sorted([f for f in os.listdir(STREAM_DIR) if f.startswith("stream") and f.endswith(".ts")],
                              key=lambda x: int(re.search(r"\d+", x).group(1)) if re.search(r"\d+", x) else 0)
                if files:
                    fallback = files[-1]
                    self.send_response(200)
                    self.send_header("Content-Type", "video/mp2t")
                    self.send_header("Access-Control-Allow-Origin", "*")
                    self.send_header("Cache-Control", "no-cache")
                    self.end_headers()
                    self.wfile.write(open(os.path.join(STREAM_DIR, fallback), "rb").read())
                else:
                    self.send_error(503, "No segments")
            return

        ap = os.path.join(ASSETS_DIR, p.lstrip("/"))
        if os.path.isfile(ap):
            self.path = ap
            return SimpleHTTPRequestHandler.do_GET(self)

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
        if self.path == "/transcode":
            clen = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(clen) if clen > 0 else b"{}"
            try:
                data = json.loads(body.decode())
                path = data.get("path", "")
                if not path:
                    self.send_error(400, "path required"); return
                m3u8_url = get_transcode_url(path)
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(json.dumps({"m3u8": m3u8_url}).encode())
            except Exception as e:
                self.send_error(500, str(e))
            return
        self.send_error(404, "Not found")

server = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
server.allow_reuse_address = True
print("[%s] Server ready on :%s" % (time.strftime("%H:%M:%S"), PORT))
server.serve_forever()

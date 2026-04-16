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
CAM_PORT    = 80
PORT        = 8080
ASSETS_DIR  = os.path.join(os.path.dirname(os.path.dirname(__file__)), "assets")
STATE_FILE  = os.path.expanduser("~/.camera_ip")

os.makedirs(STREAM_DIR, exist_ok=True)

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

def _get_subnets():
    subnets = ["192.168.167.0/24", "192.168.1.0/24"]
    try:
        with open("/proc/net/route") as f:
            for line in f:
                parts = line.split()
                if len(parts) >= 3 and parts[1] == "00000000":
                    gw = parts[2]
                    if len(gw) == 8:
                        ip = ".".join([str(int(gw[i:i+2], 16)) for i in (6, 4, 2, 0)])
                        subnets.insert(0, ".".join(ip.split(".")[:3]) + ".0/24")
    except Exception:
        pass
    return list(dict.fromkeys(subnets))

def find_camera_ip():
    if os.path.exists(STATE_FILE):
        cached = open(STATE_FILE).read().strip()
        if cached and _is_camera_http(cached, timeout=3):
            return cached
    for subnet in _get_subnets():
        found = _scan_subnet(subnet)
        if found:
            open(STATE_FILE, "w").write(found)
            return found
    return "192.168.167.40"

CAM_HOST = find_camera_ip()
HLS_URL  = f"rtsp://{CAM_HOST}:554/live"
print(f"Camera : {CAM_HOST}")
print(f"Stream : {HLS_URL}")
print(f"Output : {STREAM_DIR}")
print(f"HTTP   : http://0.0.0.0:{PORT}/")

DEVNULL = open(os.devnull, 'wb')
proc    = None

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

signal.signal(signal.SIGTERM, lambda s,f: (DEVNULL.close(), sys.exit(0)))
signal.signal(signal.SIGINT,  lambda s,f: (DEVNULL.close(), sys.exit(0)))


class HLSHandler(SimpleHTTPRequestHandler):

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

    def _proxy_camera(self, path):
        try:
            conn = HTTPConnection(CAM_HOST, CAM_PORT, timeout=15)
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
                if not chunk:
                    break
                self.wfile.write(chunk)
            conn.close()
        except Exception as e:
            self.send_error(500, f"Camera unreachable: {e}")

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

        # Stream playlist
        if p.startswith("/stream.m3u8") or (p.startswith("/stream") and ".m3u8" in p):
            m3u8_path = os.path.join(STREAM_DIR, "stream.m3u8")
            if os.path.exists(m3u8_path):
                content = open(m3u8_path).read()
                content = content.replace("#EXT-X-ENDLIST\n", "").replace("#EXT-X-ENDLIST", "")
                self.send_response(200)
                self.send_header("Content-Type", "application/vnd.apple.mpegurl")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Cache-Control", "no-cache, no-store")
                self.end_headers()
                self.wfile.write(content.encode())
            else:
                self.send_error(503, "Stream not ready")
            return

        # Segment files
        ts_match = re.match(r"(/stream\d+\.ts)", p)
        if ts_match:
            ts_file = ts_match.group(1).lstrip("/")
            ts_path = os.path.join(STREAM_DIR, ts_file)
            if os.path.exists(ts_path):
                self.send_response(200)
                self.send_header("Content-Type", "video/mp2t")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Cache-Control", "no-cache, max-age=0")
                self.end_headers()
                self.wfile.write(open(ts_path, 'rb').read())
            else:
                # Fallback: serve closest segment
                all_files = []
                for f in os.listdir(STREAM_DIR):
                    m = re.match(r"stream(\d+)\.ts", f)
                    if m:
                        all_files.append((int(m.group(1)), f))
                if all_files:
                    all_files.sort()
                    req_num = int(re.match(r"stream(\d+)\.ts", ts_file).group(1))
                    closest = min(all_files, key=lambda x: abs(x[0] - req_num))
                    self.send_response(200)
                    self.send_header("Content-Type", "video/mp2t")
                    self.send_header("Access-Control-Allow-Origin", "*")
                    self.end_headers()
                    self.wfile.write(open(os.path.join(STREAM_DIR, closest[1]), 'rb').read())
                    return
                self.send_error(404, "not found")
            return

        # Root / index.html
        if p in ("/", "/index.html"):
            ipath = os.path.join(ASSETS_DIR, "index.html")
            if os.path.exists(ipath):
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.end_headers()
                self.wfile.write(open(ipath).read().encode())
            else:
                self.send_error(404, "index.html not found")
            return

        # Let SimpleHTTPRequestHandler handle everything else
        super().do_GET()

    def log_message(self, *args): pass


server = HTTPServer(("0.0.0.0", PORT), HLSHandler)
server.allow_reuse_address = True
server.serve_forever()
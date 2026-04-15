#!/data/data/com.termux/files/usr/bin/python3
"""RTSP to HLS streaming server."""
import subprocess, os, signal, sys, re
from http.server import HTTPServer, SimpleHTTPRequestHandler

STREAM_DIR = "/data/data/com.termux/files/home/.stream"
HLS_URL    = "rtsp://192.168.167.40:554/live"
PORT       = 8080
ASSETS_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "assets")

os.makedirs(STREAM_DIR, exist_ok=True)
for f in os.listdir(STREAM_DIR):
    try: os.unlink(os.path.join(STREAM_DIR, f))
    except: pass

ffmpeg_cmd = [
    "ffmpeg",
    "-rtsp_transport", "tcp",
    "-re",
    "-i",              HLS_URL,
    "-c:v",            "copy",
    "-f",              "hls",
    "-hls_time",       "2",
    "-hls_list_size",  "5",
    "-hls_flags",      "append_list+delete_segments",
    "-reconnect",      "1",
    "-reconnect_streamed", "1",
    "-reconnect_delay_max", "5",
    os.path.join(STREAM_DIR, "stream.m3u8"),
]

DEVNULL = open(os.devnull, 'wb')
proc = subprocess.Popen(ffmpeg_cmd, stdout=DEVNULL.fileno(), stderr=DEVNULL.fileno())

print(f"Stream : {HLS_URL}")
print(f"Output : {STREAM_DIR}")
print(f"HTTP   : http://0.0.0.0:{PORT}/")

signal.signal(signal.SIGTERM, lambda s,f: (proc.terminate(), DEVNULL.close(), sys.exit(0)))
signal.signal(signal.SIGINT,  lambda s,f: (proc.terminate(), DEVNULL.close(), sys.exit(0)))


class HLSHandler(SimpleHTTPRequestHandler):
    def do_GET(self):
        # Locate the actual m3u8 (root or ch1/ subdirectory)
        candidates = [
            (os.path.join(STREAM_DIR, "stream.m3u8"), ""),
            (os.path.join(STREAM_DIR, "ch1", "stream.m3u8"), "ch1/"),
        ]
        m3u8_path, ch1_subdir = None, ""
        for p, sub in candidates:
            if os.path.exists(p):
                m3u8_path, ch1_subdir = p, sub
                break

        if self.path == "/stream.m3u8" or self.path.startswith("/stream.m3u8?"):
            if m3u8_path and os.path.exists(m3u8_path):
                c = open(m3u8_path).read()
                # Rewrite streamNN.ts paths to include ch1/ prefix
                if ch1_subdir:
                    def fix(m): return ch1_subdir + m.group(1) + ".ts"
                    c = re.sub(r'(stream\d+\.ts)', fix, c)
                # Strip ENDLIST — prevents HLS.js from stopping playback on reconnect
                c = c.replace("#EXT-X-ENDLIST\n", "")
                c = c.replace("#EXT-X-ENDLIST", "")
                self.send_response(200)
                self.send_header("Content-Type", "application/vnd.apple.mpegurl")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Cache-Control", "no-cache, no-store")
                self.end_headers()
                self.wfile.write(c.encode())
            else:
                self.send_error(503, "Stream not ready")
            return

        # Serve .ts segment files
        # Use cache-busting query param so browser always fetches fresh content
        if (".ts" in self.path or "/ch1/" in self.path) and not self.path.startswith("/."):
            # Strip any cache-bust query param before mapping to file path
            ts_path = self.path.split("?")[0].lstrip("/")
            ts_path = os.path.join(STREAM_DIR, ts_path)
            if os.path.exists(ts_path):
                self.send_response(200)
                self.send_header("Content-Type", "video/mp2t")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Cache-Control", "no-cache, max-age=0")
                self.end_headers()
                self.wfile.write(open(ts_path, 'rb').read())
            else:
                self.send_error(404, f"not found: {self.path}")
            return

        # Serve index.html
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
print("Serving on http://0.0.0.0:{}/".format(PORT))
server.serve_forever()

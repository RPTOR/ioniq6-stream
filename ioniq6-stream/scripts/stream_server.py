#!/data/data/com.termux/files/usr/bin/python3
"""RTSP to HLS streaming server."""
import subprocess, threading, os, signal, sys

STREAM_DIR = "/data/data/com.termux/files/home/.stream"
HLS_URL    = "rtsp://192.168.167.40:554/live"
PORT       = 8080
ASSETS_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "assets")

os.makedirs(STREAM_DIR, exist_ok=True)
# Clean old files
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
    "-hls_flags",      "delete_segments",
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

from http.server import HTTPServer, SimpleHTTPRequestHandler

class HLSHandler(SimpleHTTPRequestHandler):
    def do_GET(self):
        # Find the actual m3u8 (may be in root or ch1/ subdirectory)
        candidates = [
            os.path.join(STREAM_DIR, "stream.m3u8"),
            os.path.join(STREAM_DIR, "ch1", "stream.m3u8"),
        ]
        m3u8_path = None
        ch1_subdir = ""
        for p in candidates:
            if os.path.exists(p):
                m3u8_path = p
                if "ch1" in p:
                    ch1_subdir = "ch1/"
                break

        if self.path == "/stream.m3u8" or self.path.startswith("/stream.m3u8"):
            if m3u8_path and os.path.exists(m3u8_path):
                c = open(m3u8_path).read()
                # If files are in ch1/ subdirectory, prefix each .ts entry with "ch1/"
                # so browser resolves them correctly as /ch1/stream23.ts
                if ch1_subdir:
                    def fix_ts(m):
                        return ch1_subdir + m.group(1) + ".ts"
                    c = re.sub(r'(stream\d+\.ts)', fix_ts, c)
                self.send_response(200)
                self.send_header("Content-Type", "application/vnd.apple.mpegurl")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(c.encode())
            else:
                self.send_error(503, "Stream not ready")
            return

        # Serve .ts files from ch1/ subdirectory
        # Browser requests e.g. /ch1/stream23.ts → STREAM_DIR/ch1/stream23.ts
        if ".ts" in self.path and not self.path.startswith("/."):
            ts = self.path.lstrip("/")
            ts_path = os.path.join(STREAM_DIR, ts)
            if os.path.exists(ts_path):
                self.send_response(200)
                self.send_header("Content-Type", "video/mp2t")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(open(ts_path, 'rb').read())
            else:
                self.send_error(404, f"File not found: {ts}")
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

import re
server = HTTPServer(("0.0.0.0", PORT), HLSHandler)
server.allow_reuse_address = True
print("Serving on http://0.0.0.0:{}/".format(PORT))
server.serve_forever()

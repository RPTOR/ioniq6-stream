#!/data/data/com.termux/files/usr/bin/python3
"""
RTSP to HLS streaming server.
Serves a live HLS stream from a VIOFO A139 PRO dashcam at rtsp://192.168.167.40:554/live
"""

import subprocess
import threading
import os
import signal
import sys

STREAM_DIR = "/data/data/com.termux/files/home/.stream"
HLS_URL = "rtsp://192.168.167.40:554/live"
PORT = 8080
ASSETS_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "assets")

os.makedirs(STREAM_DIR, exist_ok=True)

# Clean up old stream files
for f in os.listdir(STREAM_DIR):
    try: os.unlink(os.path.join(STREAM_DIR, f))
    except: pass

ffmpeg_cmd = [
    "ffmpeg",
    "-rtsp_transport", "tcp",
    "-i", HLS_URL,
    "-c:v", "copy",
    "-f", "hls",
    "-hls_time", "2",
    "-hls_list_size", "5",
    "-hls_flags", "delete_segments",
    "-hls_dir", STREAM_DIR,
    os.path.join(STREAM_DIR, "stream.m3u8")
]

print(f"Starting stream from {HLS_URL}")
print(f"HLS output: {STREAM_DIR}")
print(f"Serving on port {PORT}")

proc = subprocess.Popen(ffmpeg_cmd, stderr=subprocess.PIPE)

def log_stderr():
    for line in proc.stderr:
        try:
            print(line.decode('utf-8', errors='replace').strip())
        except:
            pass

t = threading.Thread(target=log_stderr, daemon=True)
t.start()

def handle_sigterm(signum, frame):
    proc.terminate()
    sys.exit(0)

signal.signal(signal.SIGTERM, handle_sigterm)
signal.signal(signal.SIGINT, handle_sigterm)

# Simple HTTP server for HLS
from http.server import HTTPServer, SimpleHTTPRequestHandler

class HLSHandler(SimpleHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/stream.m3u8" or self.path == "/stream.m3u8?":
            path = os.path.join(STREAM_DIR, "stream.m3u8")
            if os.path.exists(path):
                with open(path) as f:
                    content = f.read()
                # Fix m3u8 paths for HTTP delivery
                content = content.replace(".ts", f"/.stream/.ts")
                self.send_response(200)
                self.send_header("Content-Type", "application/vnd.apple.mpegurl")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(content.encode())
            else:
                self.send_error(404, "Stream not ready")
        elif self.path.startswith("/.stream/.ts"):
            ts_file = self.path.replace("/.stream/", STREAM_DIR + "/")
            if os.path.exists(ts_file):
                with open(ts_file, 'rb') as f:
                    self.send_response(200)
                    self.send_header("Content-Type", "video/mp2t")
                    self.send_header("Access-Control-Allow-Origin", "*")
                    self.end_headers()
                    self.wfile.write(f.read())
            else:
                self.send_error(404)
        elif self.path == "/" or self.path == "/index.html":
            index_path = os.path.join(ASSETS_DIR, "index.html")
            if os.path.exists(index_path):
                with open(index_path) as f:
                    content = f.read()
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.end_headers()
                self.wfile.write(content.encode())
            else:
                self.send_error(404, "index.html not found")
        else:
            super().do_GET()

    def log_message(self, format, *args):
        pass  # suppress logging

server = HTTPServer(("0.0.0.0", PORT), HLSHandler)
print(f"HTTP server on http://0.0.0.0:{PORT}/")
print("Open http://<device-ip>:8080/ in browser")

try:
    server.serve_forever()
except KeyboardInterrupt:
    proc.terminate()
    server.shutdown()

#!/data/data/com.termux/files/usr/bin/python3
"""
RTSP to HLS streaming server.
Serves a live HLS stream from a VIOFO A139 PRO dashcam.
"""
import subprocess, threading, os, signal, sys

STREAM_DIR = "/data/data/com.termux/files/home/.stream"
HLS_URL    = "rtsp://192.168.167.40:554/live"
PORT       = 8080
ASSETS_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "assets")

os.makedirs(STREAM_DIR, exist_ok=True)
for f in os.listdir(STREAM_DIR):
    try: os.unlink(os.path.join(STREAM_DIR, f))
    except: pass

# Use -hls_segment_filename so .ts files go into STREAM_DIR
ffmpeg_cmd = [
    "ffmpeg",
    "-rtsp_transport", "tcp",
    "-i",              HLS_URL,
    "-c:v",            "copy",
    "-f",              "hls",
    "-hls_time",       "2",
    "-hls_list_size",  "5",
    "-hls_flags",      "delete_segments",
    "-hls_segment_filename", os.path.join(STREAM_DIR, "%03d.ts"),
    os.path.join(STREAM_DIR, "stream.m3u8"),
]

print(f"Stream : {HLS_URL}")
print(f"Output : {STREAM_DIR}")
print(f"HTTP   : http://0.0.0.0:{PORT}/")

proc = subprocess.Popen(ffmpeg_cmd, stderr=subprocess.PIPE)
def log():
    for line in proc.stderr:
        try: print(line.decode('utf-8', errors='replace').strip())
        except: pass
threading.Thread(target=log, daemon=True).start()

signal.signal(signal.SIGTERM, lambda s,f: (proc.terminate(), sys.exit(0)))
signal.signal(signal.SIGINT,  lambda s,f: (proc.terminate(), sys.exit(0)))

from http.server import HTTPServer, SimpleHTTPRequestHandler

class HLSHandler(SimpleHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/stream.m3u8" or self.path == "/stream.m3u8?":
            p = os.path.join(STREAM_DIR, "stream.m3u8")
            if os.path.exists(p):
                # Rewrite .ts paths so they load from our /.stream/ path
                c = open(p).read().replace(".ts", "/.stream/.ts")
                self.send_response(200)
                self.send_header("Content-Type", "application/vnd.apple.mpegurl")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(c.encode())
            else:
                self.send_error(404, "Stream not ready")
        elif self.path.startswith("/.stream/.ts"):
            ts = self.path.replace("/.stream/", STREAM_DIR + "/")
            if os.path.exists(ts):
                self.send_response(200)
                self.send_header("Content-Type", "video/mp2t")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(open(ts, 'rb').read())
            else:
                self.send_error(404)
        elif self.path in ("/", "/index.html"):
            ipath = os.path.join(ASSETS_DIR, "index.html")
            if os.path.exists(ipath):
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.end_headers()
                self.wfile.write(open(ipath).read().encode())
            else:
                self.send_error(404, "index.html not found")
        else:
            super().do_GET()
    def log_message(self, *args): pass

server = HTTPServer(("0.0.0.0", PORT), HLSHandler)
server.allow_reuse_address = True
print("Serving on http://0.0.0.0:{}/".format(PORT))
server.serve_forever()

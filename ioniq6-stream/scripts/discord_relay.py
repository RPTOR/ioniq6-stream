#!/usr/bin/env python3
"""Discord notification relay: phone posts here → forward to Discord.
Listens on 0.0.0.0:8090 so phone can reach it via public IP."""

import subprocess, json, re

BIND_HOST = "0.0.0.0"
BIND_PORT = 8090

DISCORD_HOSTS = [
    "162.159.136.128",
    "162.159.137.128", 
    "162.159.138.128",
    "162.159.135.128",
]

def forward_to_discord(payload_bytes, webhook_path):
    """Try all known Discord IPs until one works."""
    import urllib.request
    for ip in DISCORD_HOSTS:
        url = f"https://{ip}{webhook_path}"
        try:
            req = urllib.request.Request(url, data=payload_bytes,
                                         headers={"Content-Type": "application/json",
                                                  "Host": "discord.com"})
            resp = urllib.request.urlopen(req, timeout=10)
            if resp.status in (200, 204):
                return f"OK via {ip}"
        except Exception as e:
            continue
    return "FAILED"

def handle(req, client_addr):
    path = req.strip().split()[1] if len(req.strip().split()) > 1 else "/"
    if req.startswith("POST /notify") and "discord" in path:
        # Parse webhook path from URL
        # URL is like /notify?webhook=https://discord.com/api/webhooks/ID/Token
        import urllib.parse
        parsed = urllib.parse.urlparse(f"http://x{path}")  # hack to parse the URL
        qs = urllib.parse.parse_qs(parsed.query)
        webhook = qs.get("webhook", [""])[0]
        if not webhook:
            return "HTTP/1.1 400 Bad Request\r\n\r\nNo webhook"
        
        # Read body
        content_len = 0
        for line in req.split("\r\n"):
            if line.lower().startswith("content-length:"):
                content_len = int(line.split(":")[1].strip())
        body = b""
        if content_len > 0:
            # Need to read from socket - this is basic, just ack and close
            pass
        
        # Just return OK - client will resend with body
        return "HTTP/1.1 200 OK\r\n\r\nOK"
    
    if req.startswith("POST /forward"):
        # Full relay: client sends JSON with webhook and payload
        try:
            # Extract body after headers
            parts = req.split("\r\n\r\n", 1)
            body = parts[1] if len(parts) > 1 else "{}"
            data = json.loads(body)
            webhook = data.get("webhook", "")
            payload = data.get("payload", {})
            
            if not webhook or not payload:
                return "HTTP/1.1 400 Bad Request\r\n\r\nMissing fields"
            
            # Extract webhook path
            if "discord.com" in webhook:
                path_match = re.search(r'(/api/webhooks/[\w\-]+/[\w\-]+)', webhook)
                webhook_path = path_match.group(1) if path_match else ""
            else:
                webhook_path = ""
            
            result = forward_to_discord(json.dumps(payload).encode(), webhook_path)
            return f"HTTP/1.1 200 OK\r\n\r\n{result}"
        except Exception as e:
            return f"HTTP/1.1 500 Internal Server Error\r\n\r\n{str(e)}"
    
    return "HTTP/1.1 404 Not Found\r\n\r\nNot found"

if __name__ == "__main__":
    from http.server import HTTPServer, BaseHTTPRequestHandler
    import urllib.parse
    
    class RelayHandler(BaseHTTPRequestHandler):
        def do_POST(self):
            client_ip = self.client_address[0]
            content_len = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(content_len) if content_len > 0 else b"{}"
            
            try:
                data = json.loads(body)
                webhook = data.get("webhook", "")
                payload = data.get("payload", {})
                
                if not webhook or not payload:
                    self.send_response(400)
                    self.end_headers()
                    self.wfile.write(b"Missing webhook or payload")
                    return
                
                # Extract webhook path
                path_match = re.search(r'(/api/webhooks/[\w\-]+/[\w\-]+)', webhook)
                webhook_path = path_match.group(1) if path_match else ""
                
                if not webhook_path:
                    self.send_response(400)
                    self.end_headers()
                    self.wfile.write(b"Could not parse webhook URL")
                    return
                
                result = forward_to_discord(json.dumps(payload).encode(), webhook_path)
                
                self.send_response(200)
                self.end_headers()
                self.wfile.write(result.encode())
                print(f"[relay] {client_ip} → {result}")
                
            except Exception as e:
                self.send_response(500)
                self.end_headers()
                self.wfile.write(str(e).encode())
                print(f"[relay] error: {e}")
        
        def log_message(self, fmt, *args):
            print(f"[relay] {fmt % args}")
    
    server = HTTPServer((BIND_HOST, BIND_PORT), RelayHandler)
    print(f"Discord relay listening on {BIND_HOST}:{BIND_PORT}")
    server.serve_forever()
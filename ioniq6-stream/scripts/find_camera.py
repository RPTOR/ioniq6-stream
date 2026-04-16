#!/data/data/com.termux/files/usr/bin/python3
"""
Scan local subnet for VIOFO dashcam HTTP server and return its IP.
Stores result in ~/.camera_ip for subsequent runs.
"""
import subprocess, os, time, socket

STATE_FILE = os.path.expanduser("~/.camera_ip")
CAMERA_PORT = 80
CAMERA_HTTP_SERVERS = ["hfs", "Busybox", "VIOFO", "HTTP"]

def is_camera_http(ip):
    """Check if IP runs an HTTP server that looks like the VIOFO camera."""
    try:
        import http.client
        conn = http.client.HTTPConnection(ip, CAMERA_PORT, timeout=4)
        conn.request("GET", "/")
        resp = conn.getresponse()
        server = resp.getheader("Server", "").lower()
        for sig in CAMERA_HTTP_SERVERS:
            if sig.lower() in server:
                return True
        # Also check if it's on subnet 192.168.167.x (typical VIOFO AP range)
        if ip.startswith("192.168.167."):
            return True
        conn.close()
    except Exception:
        pass
    return False

def scan_subnet():
    """Use nmap to find active hosts on the likely subnets."""
    # VIOFO cameras typically run AP at 192.168.167.x or 192.168.1.x
    # Also check the current default route subnet
    gw = None
    try:
        with open("/proc/net/route") as f:
            for line in f:
                parts = line.split()
                if len(parts) >= 3 and parts[1] == "00000000":
                    gw = parts[2]
                    break
    except Exception:
        pass

    # Convert gateway hex to IP
    if gw and len(gw) == 8:
        ip = ".".join([str(int(gw[i:i+2], 16)) for i in (6, 4, 2, 0)])
        subnet_base = ".".join(ip.split(".")[:3])
        subnets = [f"{subnet_base}.0/24", "192.168.167.0/24"]
    else:
        subnets = ["192.168.167.0/24", "192.168.1.0/24", "192.168.2.0/24"]

    subnets = list(dict.fromkeys(subnets))  # dedupe

    print(f"[find_camera] Scanning subnets: {subnets}")
    for subnet in subnets:
        try:
            result = subprocess.run(
                ["nmap", "-sn", "-PS80", "-T4", "--max-retries", "1",
                 "--max-scan-delay", "100ms", "-oG", "-", subnet],
                capture_output=True, text=True, timeout=60
            )
            for line in result.stdout.splitlines():
                if "Host:" in line and "Status: Up" in line:
                    # Extract IP: 192.168.167.40
                    parts = line.split()
                    for p in parts:
                        if p[0].isdigit() and "." in p and p.count(".") == 3:
                            ip = p
                            if is_camera_http(ip):
                                return ip
        except subprocess.TimeoutExpired:
            continue
        except Exception as e:
            print(f"[find_camera] nmap error on {subnet}: {e}")
            continue
    return None

def main():
    # Try cached IP first
    if os.path.exists(STATE_FILE):
        cached = open(STATE_FILE).read().strip()
        if cached:
            print(f"[find_camera] Trying cached IP: {cached}")
            if is_camera_http(cached):
                print(f"[find_camera] Cached IP {cached} is still valid")
                return cached
            else:
                print(f"[find_camera] Cached IP {cached} not reachable, rescanning")

    print("[find_camera] Scanning for VIOFO camera...")
    found = scan_subnet()
    if found:
        print(f"[find_camera] Found camera at {found}")
        open(STATE_FILE, "w").write(found)
        return found
    else:
        print("[find_camera] Camera not found on local subnets")
        return None

if __name__ == "__main__":
    ip = main()
    if ip:
        print(f"CAMERA_IP={ip}")
    else:
        print("CAMERA_IP=not_found")
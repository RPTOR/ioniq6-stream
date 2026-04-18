#!/data/data/com.termux/files/usr/bin/python3
"""
Find VIOFO dashcam IP and store it for other scripts to use.
Writes to ~/.camera_env — can be sourced as a shell script.
Run via cron to keep IP fresh: */5 * * * * python3 ~/ioniq6-stream/scripts/find_camera.py
"""
import os, re, sys

STATE_FILE  = os.path.expanduser("~/.camera_ip")
ENV_FILE    = os.path.expanduser("~/.camera_env")
CAMERA_LLADDRS = {"50:41:1c:03:96:e2"}  # known VIOFO MAC
CAMERA_MAC_OUI  = ("50:41:1c",)         # VIOFO OUI

def is_viofo_hw(hw):
    hw = hw.lower()
    if hw in CAMERA_LLADDRS: return True
    for p in CAMERA_MAC_OUI:
        if hw.startswith(p): return True
    return False

def find_via_arp():
    """Find camera IP from /proc/net/arp by MAC address."""
    try:
        with open("/proc/net/arp") as f:
            next(f)
            for line in f:
                parts = line.split()
                if len(parts) < 4: continue
                ip, hw, flags = parts[0], parts[3].lower(), parts[2]
                if is_viofo_hw(hw):
                    return ip
    except Exception as e:
        print(f"[find_camera] arp error: {e}", file=sys.stderr)
    return None

def find_via_probe(ip):
    """Check if an IP is a camera by probing its HTTP server."""
    try:
        import http.client
        conn = http.client.HTTPConnection(ip, 80, timeout=3)
        conn.request("GET", "/")
        resp = conn.getresponse()
        server = resp.getheader("Server", "").lower()
        conn.close()
        if "hfs" in server or "busybox" in server:
            return True
        # Also accept if it's in the typical camera subnet
        if ip.startswith("192.168."):
            return True
    except Exception:
        pass
    return False

def write_env(ip):
    """Write a shell-sourcable env file."""
    with open(ENV_FILE, "w") as f:
        f.write(f"export CAMERA_IP={ip}\n")
        f.write(f"export CAMERA_RTSP=rtsp://{ip}:554/live\n")
    os.chmod(ENV_FILE, 0o600)

def main():
    # 1. Try cached IP first (fast)
    cached = None
    if os.path.exists(STATE_FILE):
        cached = open(STATE_FILE).read().strip()
        if cached and find_via_probe(cached):
            print(f"[find_camera] cached OK: {cached}")
            write_env(cached)
            print(f"export CAMERA_IP={cached}")
            return

    # 2. Try ARP cache (instant, no network traffic)
    arp_ip = find_via_arp()
    if arp_ip:
        open(STATE_FILE, "w").write(arp_ip)
        write_env(arp_ip)
        print(f"[find_camera] arp found: {arp_ip}")
        print(f"export CAMERA_IP={arp_ip}")
        return

    # 3. Scan common subnets for .40 (the camera always gets .40 on VIOFO APs)
    subnets = [
        "192.168.194", "192.168.167", "192.168.109", "192.168.1",
        "192.168.2", "192.168.100", "192.168.200",
    ]
    found = None
    for subnet in subnets:
        ip = f"{subnet}.40"
        print(f"[find_camera] probing {ip}...", end=" ", flush=True)
        if find_via_probe(ip):
            print("OK")
            found = ip
            break
        print("no")

    if found:
        open(STATE_FILE, "w").write(found)
        write_env(found)
        print(f"export CAMERA_IP={found}")
    else:
        # Last resort: use last known
        last = cached or "192.168.109.40"
        open(STATE_FILE, "w").write(last)
        write_env(last)
        print(f"[find_camera] not found, using {last}")
        print(f"export CAMERA_IP={last}")

if __name__ == "__main__":
    main()
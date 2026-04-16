#!/data/data/com.termux/files/usr/bin/python3
"""
Parking folder watcher for VIOFO A139 PRO dashcam.
Monitors multiple HTTP folders (Parking, RO, etc.) for new files
and sends Discord notifications. Saves last report locally before notifying.
Auto-discovers camera IP via subnet scan on startup.

Usage:
    python3 parking_watcher.py \
        --urls http://192.168.167.40/DCIM/Movie/Parking \
               http://192.168.167.40/DCIM/Movie/RO \
        --interval 60
"""

import os, sys, time, re, json, argparse, socket, subprocess
try: import requests
except ImportError: requests = None
from datetime import datetime

DEFAULT_INTERVAL = 60
STATE_FILE = "/data/data/com.termux/files/home/.parking_state.json"
CAM_STATE_FILE = os.path.expanduser("~/.camera_ip")


def _is_camera_http(ip, timeout=4):
    try:
        import http.client
        conn = http.client.HTTPConnection(ip, 80, timeout=timeout)
        conn.request("GET", "/")
        resp = conn.getresponse()
        server = resp.getheader("Server", "").lower()
        conn.close()
        for sig in ("hfs", "busybox", "viofo"):
            if sig in server:
                return True
        if ip.startswith("192.168.167."):
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
    if os.path.exists(CAM_STATE_FILE):
        cached = open(CAM_STATE_FILE).read().strip()
        if cached and _is_camera_http(cached, timeout=3):
            return cached
    for subnet in _get_subnets():
        found = _scan_subnet(subnet)
        if found:
            open(CAM_STATE_FILE, "w").write(found)
            return found
    return None  # let caller handle fallback

CAMERA_IP = None  # resolved at startup

def resolve_camera_ip(urls):
    """Replace any URL that still has the hardcoded 192.168.167.40 placeholder."""
    global CAMERA_IP
    cam_ip = find_camera_ip()
    if cam_ip:
        CAMERA_IP = cam_ip
        print(f"Camera  : {cam_ip} (auto-discovered)")
        resolved = []
        for url in urls:
            if "192.168.167.40" in url:
                url = url.replace("192.168.167.40", cam_ip)
            resolved.append(url)
        return resolved
    print("Camera  : NOT FOUND on subnet")
    return urls

def get_file_list(parking_url: str) -> list[dict]:
    """Fetch the folder HTML and extract file entries."""
    try:
        import urllib.request
        with urllib.request.urlopen(parking_url, timeout=10) as r:
            html = r.read().decode("utf-8", errors="replace")
    except Exception as e:
        print(f"  [{datetime.now():%H:%M:%S}] ERROR fetching {e}")
        return []

    files = []
    # Parse rows like: <tr><td><a href="/DCIM/Movie/Parking/...MP4">...</a>...
    for raw in html.split("<tr>"):
        if not raw or "<a href=" not in raw:
            continue
        href_m = re.search(r'<a href="([^"]+)"><b>([^<]+)</b></a>', raw)
        if not href_m:
            continue
        href = href_m.group(1)
        name = href_m.group(2)
        vals = re.findall(r'>([^<]+)<', raw)
        is_folder = len(vals) >= 2 and vals[1] == "folder"
        if is_folder:
            continue
        size = vals[1].strip() if len(vals) >= 2 else ""
        ftime = vals[2].strip() if len(vals) >= 3 else ""
        if name not in ('.', '..'):
            files.append({'name': name, 'href': href, 'size': size, 'ftime': ftime})
    files.sort(key=lambda f: f['ftime'], reverse=True)
    return files


def load_state(state_file: str) -> dict:
    if not os.path.exists(state_file):
        return {"seen": {}, "last": {}}
    try:
        with open(state_file) as f:
            return json.load(f)
    except Exception:
        return {"seen": {}, "last": {}}


def save_state(state: dict, state_file: str):
    with open(state_file, 'w') as f:
        json.dump(state, f, indent=2)


def save_last_report(folder: str, file_info: dict, state_file: str):
    """Persist last reported file BEFORE Discord — survives failures."""
    state = load_state(state_file)
    state["last"][folder] = {
        "name":        file_info['name'],
        "ftime":       file_info['ftime'],
        "size":        file_info['size'],
        "href":        file_info['href'],
        "reported_at":  datetime.now().isoformat(),
    }
    save_state(state, state_file)
    print(f"  ✓ Saved [{folder}]: {file_info['name']} ({file_info['ftime']})")


def send_discord(webhook_url: str, folder: str, new_files: list[dict]):
    """Send a Discord embed for new files from a specific folder."""
    if not webhook_url:
        print(f"  No webhook — skipping Discord notification for [{folder}]")
        return
    if not requests:
        print(f"  No requests library — skipping Discord notification for [{folder}]")
        return

    preview = new_files[:10]
    lines = '\n'.join(
        f"**{f['name']}** ({f['size']}, {f['ftime']})"
        for f in preview
    )
    if len(new_files) > 10:
        lines += f"\n_...and {len(new_files) - 10} more_"

    embed = {
        "embeds": [{
            "title": f"🚗 [{folder}] — {len(new_files)} New File(s)",
            "color": 0xFF8C00,
            "description": lines,
            "footer": {"text": f"folder: {folder}"},
            "timestamp": datetime.now().isoformat(),
        }]
    }

    try:
        r = requests.post(webhook_url, json=embed, timeout=10)
        status = "✓" if r.status_code in (200, 204) else f"✗ {r.status_code}"
        print(f"  Discord [{folder}]: {status}")
    except Exception as e:
        print(f"  Discord [{folder}] ✗: {e}")


def main():
    ap = argparse.ArgumentParser(description="VIOFO multi-folder watcher")
    ap.add_argument("--urls", nargs="+",
                    default=[
                        "http://192.168.167.40/DCIM/Movie/Parking",
                        "http://192.168.167.40/DCIM/Movie/RO",
                    ],
                    help="Folder URLs to monitor (IP auto-resolved if placeholder used)")
    ap.add_argument("--interval", type=int, default=DEFAULT_INTERVAL)
    ap.add_argument("--webhook",  default=os.environ.get("DISCORD_WEBHOOK_URL", ""),
                    help="Discord webhook URL")
    ap.add_argument("--once",     action="store_true", help="Poll once and exit")
    ap.add_argument("--state",    default=STATE_FILE,  help="State file path")
    args = ap.parse_args()

    # Auto-resolve camera IP for any URL containing the placeholder
    args.urls = resolve_camera_ip(args.urls)

    state_file = args.state
    urls = args.urls

    print(f"Watching : {len(urls)} folder(s)")
    for u in urls:
        print(f"  • {u}")
    print(f"Interval : {args.interval}s")
    print(f"State    : {state_file}")
    print(f"Webhook  : {'set ✓' if args.webhook else 'NOT SET'}")
    print()

    state = load_state(state_file)

    # Restore seen sets from state
    seen: dict[str, set] = {}
    for folder in urls:
        folder_seen = state.get("seen", {}).get(folder, [])
        seen[folder] = set(folder_seen)

    last = state.get("last", {})
    if last:
        print("Last reports:")
        for folder, info in last.items():
            print(f"  [{folder}] {info['name']} at {info['ftime']} (saved {info['reported_at']})")
        print()

    while True:
        ts = datetime.now().strftime("%H:%M:%S")
        print(f"[{ts}] Checking {len(urls)} folder(s)...")

        for folder in urls:
            files = get_file_list(folder)
            if not files:
                print(f"  [{folder}] (fetch error or empty)")
                continue

            folder_seen = seen.get(folder, set())
            new_files = [f for f in files if f['name'] not in folder_seen]

            if new_files:
                print(f"  [{folder}] {len(new_files)} new: " + ", ".join(f['name'] for f in new_files))

                # Save LAST REPORT before Discord
                save_last_report(folder, new_files[0], state_file)

                # Send Discord notification
                send_discord(args.webhook, folder, new_files)

                # Update seen list
                folder_seen.update(f['name'] for f in new_files)
                seen[folder] = folder_seen
                state["seen"] = {f: list(s) for f, s in seen.items()}
                state["last"] = last
                save_state(state, state_file)
            else:
                print(f"  [{folder}] no new files ({len(files)} total)")

        if args.once:
            break
        time.sleep(args.interval)


if __name__ == "__main__":
    main()

#!/data/data/com.termux/files/usr/bin/python3
"""
Parking folder watcher for VIOFO A139 PRO dashcam.
Monitors multiple HTTP folders (Parking, RO, etc.) for new files
and sends Discord notifications. Saves last report locally before notifying.

Usage:
    python3 parking_watcher.py --interval 60 --webhook DISCORD_WEBHOOK_URL
"""
import os, sys, time, re, json, argparse
try: import requests
except ImportError: requests = None
from datetime import datetime

DEFAULT_INTERVAL = 60
STATE_FILE = "/data/data/com.termux/files/home/.parking_state.json"


def get_file_list(parking_url):
    try:
        import urllib.request
        with urllib.request.urlopen(parking_url, timeout=10) as r:
            html = r.read().decode("utf-8", errors="replace")
    except Exception as e:
        print(f"  [{datetime.now():%H:%M:%S}] ERROR fetching {e}")
        return []

    files = []
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
        size  = vals[1].strip() if len(vals) >= 2 else ""
        ftime = vals[2].strip() if len(vals) >= 3 else ""
        files.append({"name": name, "href": href, "size": size, "ftime": ftime})
    files.sort(key=lambda f: f["ftime"], reverse=True)
    return files


def load_state(state_file):
    if not os.path.exists(state_file):
        return {"seen": {}, "last": {}}
    try:
        with open(state_file) as f:
            return json.load(f)
    except Exception:
        return {"seen": {}, "last": {}}


def save_state(state, state_file):
    with open(state_file, "w") as f:
        json.dump(state, f, indent=2)


def save_last_report(folder, file_info, state_file):
    state = load_state(state_file)
    state["last"][folder] = {
        "name":        file_info["name"],
        "ftime":       file_info["ftime"],
        "size":        file_info["size"],
        "href":        file_info["href"],
        "reported_at": datetime.now().isoformat(),
    }
    save_state(state, state_file)
    print(f"  ✓ [{folder}] {file_info['name']}")


def send_discord(webhook_url, folder, new_files):
    if not webhook_url or not requests:
        print(f"  No webhook — skipping [{folder}]")
        return

    preview = new_files[:10]
    lines = "\n".join(f"**{f['name']}** ({f['size']}, {f['ftime']})" for f in preview)
    if len(new_files) > 10:
        lines += f"\n_...and {len(new_files) - 10} more_"

    embed = {
        "embeds": [{
            "title":       f"🚗 [{folder}] — {len(new_files)} New File(s)",
            "color":       0xFF8C00,
            "description": lines,
            "footer":      {"text": folder},
            "timestamp":   datetime.now().isoformat(),
        }]
    }

    try:
        r = requests.post(webhook_url, json=embed, timeout=10)
        status = "✓" if r.status_code in (200, 204) else f"✗ {r.status_code}"
        print(f"  Discord [{folder}]: {status}")
    except Exception as e:
        print(f"  Discord [{folder}] ✗: {e}")


def main():
    # Source camera env if available
    env_file = os.path.expanduser("~/.camera_env")
    if os.path.exists(env_file):
        with open(env_file) as f:
            for line in f:
                if line.startswith("export CAMERA_IP="):
                    os.environ["CAMERA_IP"] = line.split("=", 1)[1].strip()

    cam_ip = os.environ.get("CAMERA_IP", "192.168.167.40")

    default_urls = [
        f"http://{cam_ip}/DCIM/Movie/Parking",
        f"http://{cam_ip}/DCIM/Movie/RO",
    ]

    ap = argparse.ArgumentParser(description="VIOFO multi-folder watcher")
    ap.add_argument("--urls", nargs="+", default=default_urls,
                    help="Folder URLs to monitor")
    ap.add_argument("--interval", type=int, default=DEFAULT_INTERVAL)
    ap.add_argument("--webhook",  default=os.environ.get("DISCORD_WEBHOOK_URL", ""),
                    help="Discord webhook URL")
    ap.add_argument("--once",  action="store_true", help="Poll once and exit")
    ap.add_argument("--state", default=STATE_FILE, help="State file path")
    args = ap.parse_args()

    state_file = args.state
    urls = args.urls

    print(f"Camera  : {cam_ip}")
    print(f"Watching : {len(urls)} folder(s)")
    for u in urls:
        print(f"  • {u}")
    print(f"Interval : {args.interval}s")
    print(f"State    : {state_file}")
    print(f"Webhook  : {'set ✓' if args.webhook else 'NOT SET'}")
    print()

    state = load_state(state_file)
    seen  = {f: set(state.get("seen", {}).get(f, [])) for f in urls}
    last  = state.get("last", {})

    if last:
        print("Last reports:")
        for folder, info in last.items():
            print(f"  [{folder}] {info['name']} at {info['ftime']}")
        print()

    while True:
        ts = datetime.now().strftime("%H:%M:%S")
        print(f"[{ts}] Checking {len(urls)} folder(s)...")

        for folder in urls:
            files = get_file_list(folder)
            if not files:
                print(f"  [{folder}] (fetch error or empty)")
                continue

            new_files = [f for f in files if f["name"] not in seen.get(folder, set())]

            if new_files:
                print(f"  [{folder}] {len(new_files)} new: " + ", ".join(f["name"] for f in new_files))
                save_last_report(folder, new_files[0], state_file)
                send_discord(args.webhook, folder, new_files)
                seen.setdefault(folder, set()).update(f["name"] for f in new_files)
            else:
                print(f"  [{folder}] no new files ({len(files)} total)")

        seen_dict = {f: list(s) for f, s in seen.items()}
        save_state({"seen": seen_dict, "last": last}, state_file)

        if args.once:
            break
        time.sleep(args.interval)


if __name__ == "__main__":
    main()
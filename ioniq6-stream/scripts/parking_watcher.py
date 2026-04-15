#!/data/data/com.termux/files/usr/bin/python3
"""
Parking folder watcher for VIOFO A139 PRO dashcam.
Monitors multiple HTTP folders (Parking, RO, etc.) for new files
and sends Discord notifications. Saves last report locally before notifying.

Usage:
    python3 parking_watcher.py \
        --urls http://192.168.167.40/DCIM/Movie/Parking \
               http://192.168.167.40/DCIM/Movie/RO \
        --interval 60
"""

import os
import sys
import time
import re
import json
import argparse
import requests
from datetime import datetime

DEFAULT_INTERVAL = 60
STATE_FILE = "/data/data/com.termux/files/home/.parking_state.json"


def get_file_list(parking_url: str) -> list[dict]:
    """Fetch the folder HTML and extract file entries."""
    try:
        r = requests.get(parking_url, timeout=10)
        r.raise_for_status()
    except Exception as e:
        print(f"  [{datetime.now():%H:%M:%S}] ERROR fetching {e}")
        return []

    files = []
    rows = re.findall(
        r'<tr><td><a href="([^"]+)">([^<]+)</a>.*?<td[^>]*>([\d,]+)</td>.*?<td[^>]*>([\d/:\s]+)</td>',
        r.text, re.DOTALL
    )
    for href, name, size, ftime in rows:
        if name not in ('.', '..'):
            files.append({
                'name': name,
                'href':  href,
                'size':  size.strip(),
                'ftime': ftime.strip(),
            })
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
                    help="Folder URLs to monitor")
    ap.add_argument("--interval", type=int, default=DEFAULT_INTERVAL)
    ap.add_argument("--webhook",  default=os.environ.get("DISCORD_WEBHOOK_URL", ""),
                    help="Discord webhook URL")
    ap.add_argument("--once",     action="store_true", help="Poll once and exit")
    ap.add_argument("--state",    default=STATE_FILE,  help="State file path")
    args = ap.parse_args()

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

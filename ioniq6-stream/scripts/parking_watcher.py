#!/data/data/com.termux/files/usr/bin/python3
"""
Parking folder watcher for VIOFO A139 PRO dashcam.
Polls the HTTP parking folder for new files and sends Discord notifications.
Stores last reported file name + timestamp locally before sending.

Usage:
    python3 parking_watcher.py [--url http://192.168.167.40/DCIM/Movie/Parking] [--interval 60]
"""

import os
import sys
import time
import re
import json
import argparse
import requests
from datetime import datetime

DEFAULT_URL = "http://192.168.167.40/DCIM/Movie/Parking"
DEFAULT_INTERVAL = 60  # seconds
STATE_FILE = "/data/data/com.termux/files/home/.parking_state.json"


def get_file_list(parking_url: str) -> list[dict]:
    """Fetch the parking folder HTML and extract file entries."""
    try:
        r = requests.get(parking_url, timeout=10)
        r.raise_for_status()
    except Exception as e:
        print(f"[{datetime.now():%H:%M:%S}] ERROR fetching {parking_url}: {e}")
        return []

    files = []
    rows = re.findall(
        r'<tr><td><a href="([^"]+)">([^<]+)</a>.*?<td[^>]*>([\d,]+)</td>.*?<td[^>]*>([\d/:\s]+)</td>',
        r.text, re.DOTALL
    )
    for href, name, size, ftime in rows:
        if name in ('.', '..'):
            continue
        files.append({
            'name': name,
            'href': href,
            'size': size.strip(),
            'ftime': ftime.strip(),
        })
    # Newest first
    files.sort(key=lambda f: f['ftime'], reverse=True)
    return files


def load_state(state_file: str = STATE_FILE) -> dict:
    if not os.path.exists(state_file):
        return {"seen": [], "last": None}
    try:
        with open(state_file) as f:
            return json.load(f)
    except Exception:
        return {"seen": [], "last": None}


def save_state(state: dict, state_file: str = STATE_FILE):
    with open(state_file, 'w') as f:
        json.dump(state, f, indent=2)


def save_last_report(file_info: dict, state_file: str = STATE_FILE):
    """
    Persist the last reported file name + dashcam timestamp
    to local state BEFORE sending Discord.
    This survives even if Discord fails.
    """
    state = load_state(state_file)
    state["last"] = {
        "name":     file_info['name'],
        "ftime":    file_info['ftime'],
        "size":     file_info['size'],
        "href":     file_info['href'],
        "reported_at": datetime.now().isoformat(),
    }
    save_state(state)
    print(f"  ✓ Last report saved: {file_info['name']} ({file_info['ftime']})")


def send_discord(webhook_url: str, files: list[dict], parking_url: str):
    """Send a Discord embed notification for new files."""
    if not webhook_url:
        print("  No Discord webhook URL set — skipping notification")
        return

    preview = files[:10]
    file_lines = '\n'.join(
        f"**{f['name']}** ({f['size']}, {f['ftime']})"
        for f in preview
    )
    if len(files) > 10:
        file_lines += f"\n_...and {len(files) - 10} more_"

    base_name = parking_url.rstrip('/').split('/')[-1]
    embed = {
        "embeds": [{
            "title": "🚗 Parking Recording — New File(s)",
            "color": 0xFF8C00,
            "description": file_lines,
            "footer": {"text": f"Source: {base_name}"},
            "timestamp": datetime.now().isoformat(),
        }]
    }

    try:
        r = requests.post(webhook_url, json=embed, timeout=10)
        if r.status_code in (200, 204):
            print(f"  ✓ Discord notified ({len(files)} file(s))")
        else:
            print(f"  ✗ Discord error {r.status_code}: {r.text}")
    except Exception as e:
        print(f"  ✗ Discord exception: {e}")


def main():
    ap = argparse.ArgumentParser(description="VIOFO parking folder watcher")
    ap.add_argument("--url",      default=DEFAULT_URL, help="Parking folder URL")
    ap.add_argument("--interval", type=int, default=DEFAULT_INTERVAL, help="Poll interval (s)")
    ap.add_argument("--webhook",  default=os.environ.get("DISCORD_WEBHOOK_URL", ""),
                    help="Discord webhook URL")
    ap.add_argument("--once",     action="store_true", help="Poll once and exit")
    ap.add_argument("--state",    default=STATE_FILE,   help="State file path")
    args = ap.parse_args()

    state_file = args.state

    print(f"Watching : {args.url}")
    print(f"Interval : {args.interval}s")
    print(f"State    : {STATE_FILE}")
    print(f"Webhook  : {'set ✓' if args.webhook else 'NOT SET'}")
    print()

    state = load_state(state_file)
    seen: set = set(state.get("seen", []))
    last  = state.get("last")

    if last:
        print(f"Last report: {last['name']} at {last['ftime']} (saved at {last['reported_at']})")
        print()

    while True:
        ts = datetime.now().strftime("%H:%M:%S")
        print(f"[{ts}] Checking parking folder...")
        files = get_file_list(args.url)

        if not files:
            print("  (fetch error or folder empty)")
        else:
            new_files = [f for f in files if f['name'] not in seen]
            if new_files:
                print(f"  {len(new_files)} new file(s):")
                for f in new_files:
                    print(f"    + {f['name']} ({f['size']}, {f['ftime']})")

                # ── Save LAST REPORT before sending Discord ──────────
                # Save the single most recent new file as "the" last report
                save_last_report(new_files[0], state_file)

                # ── Send Discord notification ───────────────────────
                send_discord(args.webhook, new_files, args.url)

                # ── Update seen list ────────────────────────────────
                seen.update(f['name'] for f in new_files)
                state["seen"] = list(seen)
                save_state(state, state_file)
            else:
                print(f"  No new files ({len(files)} total in folder)")

        if args.once:
            break
        time.sleep(args.interval)


if __name__ == "__main__":
    main()

#!/data/data/com.termux/files/usr/bin/python3
"""
Parking folder watcher for VIOFO A139 PRO dashcam.
Polls the HTTP parking folder for new files and sends Discord notifications.

Usage:
    python3 parking_watcher.py [--url http://192.168.167.40/DCIM/Movie/Parking] [--interval 60]
"""

import os
import sys
import time
import re
import argparse
import requests
from datetime import datetime
from pathlib import Path

# ─── Discord webhook ─────────────────────────────────────────────────────────
# Set DISCORD_WEBHOOK_URL in environment or pass --webhook
# To get a webhook: Discord channel settings → Integrations → Webhooks

DEFAULT_URL = "http://192.168.167.40/DCIM/Movie/Parking"
DEFAULT_INTERVAL = 60  # seconds
SEEN_FILE = "/data/data/com.termux/files/home/.parking_seen.txt"


def get_file_list(parking_url: str) -> list[dict]:
    """Fetch the parking folder HTML and extract file entries."""
    try:
        r = requests.get(parking_url, timeout=10)
        r.raise_for_status()
    except Exception as e:
        print(f"[{datetime.now():%H:%M:%S}] ERROR fetching {parking_url}: {e}")
        return []

    files = []
    # Parse HTML table rows: <tr><td><a href="/DCIM/...">filename</a>...
    rows = re.findall(r'<tr><td><a href="([^"]+)">([^<]+)</a>.*?<td[^>]*>([\d,]+)</td>.*?<td[^>]*>([\d/:\s]+)</td>', r.text, re.DOTALL)
    for href, name, size, ftime in rows:
        if name in ('.', '..'):
            continue
        files.append({
            'name': name,
            'href': href,
            'size': size.strip(),
            'ftime': ftime.strip(),
        })
    return files


def load_seen() -> set:
    if not os.path.exists(SEEN_FILE):
        return set()
    with open(SEEN_FILE) as f:
        return set(line.strip() for line in f if line.strip())


def save_seen(names: set):
    with open(SEEN_FILE, 'w') as f:
        for n in sorted(names):
            f.write(n + '\n')


def send_discord(webhook_url: str, files: list[dict], parking_url: str):
    """Send a Discord embed notification for new files."""
    if not webhook_url:
        print("No Discord webhook URL set — skipping notification")
        return

    # Build file list text (max 10 files per message)
    preview = files[:10]
    file_lines = '\n'.join(
        f"**{f['name']}** ({f['size']}, {f['ftime']})"
        for f in preview
    )
    if len(files) > 10:
        file_lines += f"\n_...and {len(files) - 10} more_"

    base_url = parking_url.rstrip('/')
    embed = {
        "embeds": [{
            "title": "🚗 Parking Recording — New File(s)",
            "color": 0xFF8C00,  # orange
            "description": file_lines,
            "footer": {"text": f"Source: {base_url.split('/')[-1]}"},
            "timestamp": datetime.now().isoformat(),
        }]
    }

    try:
        r = requests.post(webhook_url, json=embed, timeout=10)
        if r.status_code in (200, 204):
            print(f"  ✓ Discord notification sent for {len(files)} file(s)")
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
    ap.add_argument("--once",      action="store_true", help="Poll once and exit")
    args = ap.parse_args()

    print(f"Watching: {args.url}")
    print(f"Interval: {args.interval}s")
    print(f"Webhook:  {'set' if args.webhook else 'NOT SET — set DISCORD_WEBHOOK_URL'}")
    print()

    seen = load_seen()

    while True:
        ts = datetime.now().strftime("%H:%M:%S")
        print(f"[{ts}] Checking parking folder...")
        files = get_file_list(args.url)

        if not files:
            print("  (no files found or fetch error)")
        else:
            new_files = [f for f in files if f['name'] not in seen]
            if new_files:
                print(f"  {len(new_files)} new file(s) detected:")
                for f in new_files:
                    print(f"    + {f['name']} ({f['size']})")
                send_discord(args.webhook, new_files, args.url)
                # Add new files to seen
                seen.update(f['name'] for f in new_files)
                save_seen(seen)
            else:
                print(f"  No new files ({len(files)} total)")

        if args.once:
            break
        time.sleep(args.interval)


if __name__ == "__main__":
    main()

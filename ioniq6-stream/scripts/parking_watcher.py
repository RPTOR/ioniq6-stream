#!/data/data/com.termux/files/usr/bin/python3
"""
Parking folder watcher for VIOFO A139 PRO dashcam.
Monitors multiple HTTP folders (Parking, RO, Photo) for new files
and sends Discord notifications. Saves last report locally before notifying.
Photo files are sent as Discord message attachments (images).

Usage:
    python3 parking_watcher.py --interval 60 --webhook DISCORD_WEBHOOK_URL
"""
import os, sys, time, re, json, argparse, subprocess
try: import requests
except ImportError: requests = None
from datetime import datetime
import shutil

def _curl_get(url, timeout=5):
    """Download a URL via curl (bypasses broken Python DNS on Termux/Android)."""
    curl = shutil.which("curl") or "curl"
    r = subprocess.run([curl, "-s", "--max-time", str(timeout), url, "-o", "-"],
                       capture_output=True, timeout=timeout+2)
    return r.stdout if r.returncode == 0 else None

def _curl_post_json(url, payload, timeout=15):
    """POST JSON via curl (bypasses broken Python DNS on Termux/Android)."""
    import tempfile
    data = json.dumps(payload).encode()
    with tempfile.NamedTemporaryFile(mode='wb', suffix='.json', delete=False) as f:
        f.write(data); tmp = f.name
    try:
        curl = shutil.which("curl") or "curl"
        r = subprocess.run([curl, "-s", "-X", "POST", url,
                          "-H", "Content-Type: application/json",
                          "--max-time", str(timeout),
                          "--data-binary", "@" + tmp],
                          capture_output=True, text=True, timeout=timeout+5)
        return 200 if r.returncode == 0 else r.returncode, r.stdout
    finally:
        os.unlink(tmp)

def _curl_post_multipart(url, payload_json, fname, img_data, ctype, timeout=20):
    """POST multipart/form-data with a file via curl."""
    import tempfile
    with tempfile.NamedTemporaryFile(mode='wb', suffix='.json', delete=False) as f:
        f.write(json.dumps(payload_json).encode()); pfile = f.name
    with tempfile.NamedTemporaryFile(mode='wb', suffix='.jpg', delete=False) as f:
        f.write(img_data); ifile = f.name
    try:
        curl = shutil.which("curl") or "curl"
        r = subprocess.run([curl, "-s", "-X", "POST", url,
                          "-F", f"payload_json=@{pfile}",
                          "-F", f"file=@{ifile};type={ctype}",
                          "--max-time", str(timeout)],
                          capture_output=True, text=True, timeout=timeout+5)
        return 200 if r.returncode == 0 else r.returncode, r.stdout
    finally:
        os.unlink(pfile); os.unlink(ifile)

DEFAULT_INTERVAL = 60
STATE_FILE  = "/data/data/com.termux/files/home/.parking_state.json"
CAM_PROXY  = "http://localhost:8080/cam"
DISCORD_MAX_PHOTOS = 3   # max JPG photos to attach per notification


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
    if not webhook_url:
        print(f"  No webhook — skipping [{folder}]")
        return

    # Separate JPG photos from other files
    mp4_files  = [f for f in new_files if f["name"].lower().endswith(".mp4")]
    jpg_files  = [f for f in new_files if f["name"].lower().endswith(".jpg")]
    other_files = [f for f in new_files if not f["name"].lower().endswith((".mp4", ".jpg"))]

    # ── Send text embed for non-JPG files ──
    if other_files:
        preview = other_files[:10]
        lines = "\n".join(f"**{f['name']}** ({f['size']}, {f['ftime']})" for f in preview)
        if len(other_files) > 10:
            lines += f"\n_...and {len(other_files) - 10} more_"
        embed = {
            "embeds": [{
                "title":     f"🚗 [{folder}] — {len(other_files)} New File(s)",
                "color":     0xFF8C00,
                "description": lines,
                "footer":    {"text": folder},
                "timestamp": datetime.now().isoformat(),
            }]
        }
        try:
            code, _ = _curl_post_json(webhook_url, embed, timeout=10)
            status = "✓" if code == 200 else f"✗ {code}"
            print(f"  Discord [{folder}]: {status}")
        except Exception as e:
            print(f"  Discord [{folder}] ✗: {e}")

    # ── Send MP4 files with extracted screenshot ──
    if mp4_files:
        for mp4 in mp4_files:
            try:
                file_url = CAM_PROXY + mp4["href"]
                temp_thumb = f"/tmp/{mp4['name']}.jpg"
                cmd = [
                    "ffmpeg", "-y", "-ss", "00:00:01", "-i", file_url,
                    "-vframes", "1", "-q:v", "2", temp_thumb
                ]
                subprocess.run(cmd, capture_output=True, check=True)
                with open(temp_thumb, "rb") as f:
                    img_data = f.read()
                payload = {
                    "embeds": [{
                        "title":       f"🚗 [{folder}] — New Video",
                        "description": f"**{mp4['name']}**\n{mp4['ftime']} · {mp4['size']}",
                        "color":       0x32CD32,
                        "footer":      {"text": folder},
                        "timestamp":   datetime.now().isoformat(),
                    }]
                }
                code, _ = _curl_post_multipart(webhook_url, payload, f"{mp4['name']}.jpg", img_data, "image/jpeg", timeout=20)
                status = "✓" if code == 200 else f"✗ {code}"
                print(f"  Discord MP4 [{folder}] {mp4['name']}: {status}")
                os.unlink(temp_thumb)
            except Exception as e:
                print(f"  Discord MP4 [{folder}] {mp4['name']} ✗: {e}")

    # ── Send JPG photos as Discord attachments ──
    if jpg_files:
        photos_to_send = jpg_files[:DISCORD_MAX_PHOTOS]
        remaining = len(jpg_files) - DISCORD_MAX_PHOTOS

        for photo in photos_to_send:
            file_url = CAM_PROXY + photo["href"]
            img_data = _curl_get(file_url, timeout=5)  # use curl for local proxy too
            fname = photo["name"]
            try:
                if img_data:
                    payload = {
                        "embeds": [{
                            "title":       f"📷 [{folder}] — New Photo",
                            "description": f"**{fname}**\n{photo['ftime']} · {photo['size']}",
                            "color":       0x4FC3F7,
                            "footer":      {"text": folder},
                            "timestamp":   datetime.now().isoformat(),
                        }]
                    }
                    code, _ = _curl_post_multipart(webhook_url, payload, fname, img_data, "image/jpeg", timeout=20)
                else:
                    # Server down — send embed without attachment
                    payload = {"embeds": [{
                        "title":       f"📷 [{folder}] — New Photo",
                        "description": f"**{fname}**\n{photo['ftime']} · {photo['size']}\n_(thumbnail unavailable)_",
                        "color":       0x4FC3F7,
                        "footer":      {"text": folder},
                        "timestamp":   datetime.now().isoformat(),
                    }]}
                    code, _ = _curl_post_json(webhook_url, payload, timeout=10)
                status = "✓" if code == 200 else f"✗ {code}"
                print(f"  Discord photo [{folder}] {fname}: {status}")
            except Exception as e:
                print(f"  Discord photo [{folder}] {photo['name']} ✗: {e}")

        if remaining > 0:
            embed = {
                "embeds": [{
                    "title":     f"📷 [{folder}] — {remaining} more photo(s)",
                    "color":     0xFF8C00,
                    "description": "\n".join(f"**{f['name']}** ({f['ftime']})" for f in jpg_files[DISCORD_MAX_PHOTOS:]),
                    "footer":    {"text": folder},
                    "timestamp": datetime.now().isoformat(),
                }]
            }
            try:
                code, _ = _curl_post_json(webhook_url, embed, timeout=10)
                status = "✓" if code == 200 else f"✗ {code}"
                print(f"  Discord [{folder}] remaining: {status}")
            except Exception as e:
                print(f"  Discord [{folder}] remaining ✗: {e}")


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
        f"http://{cam_ip}/DCIM/Photo",
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
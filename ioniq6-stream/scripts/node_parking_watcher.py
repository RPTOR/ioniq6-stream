#!/usr/bin/env python3
"""
Parking watcher running on the node (not on the phone).
Polls the stream server (reachable via Tailscale) for new parking/RO/Photo files
and sends Discord notifications directly.
"""
import os, sys, time, re, json, subprocess, datetime, shutil
from urllib.request import urlopen, Request
from urllib.error import URLError

STATE_FILE   = "/home/node/.parking_state.json"
STREAM_BASE  = "http://100.127.189.53:8080/cam"
DISCORD_WH   = "https://discord.com/api/webhooks/1493959527073317135/97HadaLPZRy5Khz9acsc3ZhYDzKVLc9Qm1lqQRiiHjJH7SCOd8Y38l85GxbTYRfXzB66"
CAM_PROXY    = "http://100.127.189.53:8080/cam"
MAX_PHOTOS   = 3

def get(url, timeout=10):
    try:
        req = Request(url, headers={"User-Agent": "ioniq6-watcher/1.0"})
        with urlopen(req, timeout=timeout) as r:
            return r.read().decode("utf-8", errors="replace")
    except Exception as e:
        print(f"  GET error {url}: {e}")
        return ""

def curl_get(url, timeout=5):
    curl = shutil.which("curl") or "curl"
    r = subprocess.run([curl, "-s", "--max-time", str(timeout), url, "-o", "-"],
                       capture_output=True, timeout=timeout+2)
    return r.stdout if r.returncode == 0 else None

def curl_post_json(url, payload, timeout=15):
    import tempfile
    data = json.dumps(payload).encode()
    with tempfile.NamedTemporaryFile(mode='wb', suffix='.json', delete=False) as f:
        f.write(data); tmp = f.name
    try:
        curl = shutil.which("curl") or "curl"
        r = subprocess.run([curl, "-s", "-X", "POST", url,
                          "-H", "Content-Type: application/json",
                          "--max-time", str(timeout), "--data-binary", "@" + tmp],
                          capture_output=True, text=True, timeout=timeout+5)
        return 200 if r.returncode == 0 else r.returncode, r.stdout
    finally:
        os.unlink(tmp)

def curl_post_multipart(url, payload_json, fname, img_data, ctype, timeout=20):
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

def parse_files(html, folder):
    """Extract file name/size/ftime from folder listing HTML."""
    files = []
    for m in re.finditer(r'<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>', html, re.DOTALL):
        name = re.sub(r'<[^>]+>', '', m.group(2)).strip()
        href = m.group(1).strip()
        if name in (".", "..", ""): continue
        if not re.search(r'\.(mp4|jpg)$', name, re.I): continue
        # get size/ftime from same row
        size = ftime = "?"
        row = m.group(0)
        sm = re.search(r'(\d+[\d.]+[KM]?)', row)
        if sm: size = sm.group(1)
        fm = re.search(r'\d{4}[-/]\d{2}[-/]\d{2}\s+\d{2}:\d{2}', row)
        if fm: ftime = fm.group(0).replace("/", "-")
        files.append({"name": name, "href": href, "size": size, "ftime": ftime})
    return files

def load_state():
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except: return {"folders": {}}

def save_state(state):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f)

def send_discord(folder, new_files):
    if not new_files: return
    mp4_files  = [f for f in new_files if f["name"].lower().endswith(".mp4")]
    jpg_files  = [f for f in new_files if f["name"].lower().endswith(".jpg")]
    other_files = [f for f in new_files if not f["name"].lower().endswith((".mp4", ".jpg"))]

    ts = datetime.datetime.now().isoformat()

    # Text embed for non-JPG
    if other_files:
        lines = "\n".join(f"**{f['name']}** ({f['size']}, {f['ftime']})" for f in other_files[:10])
        embed = {"embeds": [{"title": f"🚗 [{folder}] — {len(other_files)} New File(s)",
                             "description": lines, "color": 0xFF8C00,
                             "footer": {"text": folder}, "timestamp": ts}]}
        code, _ = curl_post_json(DISCORD_WH, embed, timeout=10)
        print(f"  Discord [{folder}]: {'✓' if code == 200 else f'✗ {code}'}")

    # MP4 with thumbnail
    for mp4 in mp4_files:
        try:
            file_url = CAM_PROXY + mp4["href"]
            tmp_mp4 = f"/tmp/{mp4['name']}"
            tmp_thumb = f"/tmp/{mp4['name']}.jpg"
            # Download MP4 to /tmp first (moov atom may be at end - ffmpeg needs it)
            curl = shutil.which("curl") or "curl"
            r = subprocess.run([curl, "-s", "--max-time", "120", "-o", tmp_mp4, file_url],
                              capture_output=True, timeout=130)
            if r.returncode != 0:
                print(f"  MP4 download failed for {mp4['name']}: curl rc={r.returncode}")
                # Send text-only notification
                payload = {"embeds": [{"title": f"🚗 [{folder}] — New Video (no thumb)",
                                       "description": f"**{mp4['name']}**\n{mp4['ftime']} · {mp4['size']}",
                                       "color": 0x32CD32, "footer": {"text": folder}, "timestamp": ts}]}
                code, _ = curl_post_json(DISCORD_WH, payload, timeout=15)
                print(f"  Discord MP4 (text) [{folder}] {mp4['name']}: {'✓' if code == 200 else f'✗ {code}'}")
                continue
            # Extract thumbnail from local file
            r2 = subprocess.run(["/opt/ffmpeg_bin", "-y", "-ss", "00:00:01", "-i", tmp_mp4,
                                 "-vframes", "1", "-q:v", "2", tmp_thumb],
                                 capture_output=True, timeout=30)
            os.unlink(tmp_mp4)
            if r2.returncode != 0:
                print(f"  ffmpeg failed for {mp4['name']}: {r2.stderr.decode()[:100]}")
                payload = {"embeds": [{"title": f"🚗 [{folder}] — New Video",
                                       "description": f"**{mp4['name']}**\n{mp4['ftime']} · {mp4['size']}",
                                       "color": 0x32CD32, "footer": {"text": folder}, "timestamp": ts}]}
                code, _ = curl_post_json(DISCORD_WH, payload, timeout=15)
                print(f"  Discord MP4 (text) [{folder}] {mp4['name']}: {'✓' if code == 200 else f'✗ {code}'}")
                continue
            with open(tmp_thumb, "rb") as f:
                img_data = f.read()
            os.unlink(tmp_thumb)
            payload = {"embeds": [{"title": f"🚗 [{folder}] — New Video",
                                   "description": f"**{mp4['name']}**\n{mp4['ftime']} · {mp4['size']}",
                                   "color": 0x32CD32, "footer": {"text": folder}, "timestamp": ts}]}
            code, _ = curl_post_multipart(DISCORD_WH, payload, f"{mp4['name']}.jpg", img_data, "image/jpeg")
            print(f"  Discord MP4 [{folder}] {mp4['name']}: {'✓' if code == 200 else f'✗ {code}'}")
        except Exception as e:
            print(f"  Discord MP4 [{folder}] {mp4['name']} ✗: {e}")

    # JPG photos
    if jpg_files:
        for photo in jpg_files[:MAX_PHOTOS]:
            file_url = CAM_PROXY + photo["href"]
            img_data = curl_get(file_url, timeout=5)
            fname = photo["name"]
            if img_data:
                payload = {"embeds": [{"title": f"📷 [{folder}] — New Photo",
                                       "description": f"**{fname}**\n{photo['ftime']} · {photo['size']}",
                                       "color": 0x4FC3F7, "footer": {"text": folder}, "timestamp": ts}]}
                code, _ = curl_post_multipart(DISCORD_WH, payload, fname, img_data, "image/jpeg")
            else:
                payload = {"embeds": [{"title": f"📷 [{folder}] — New Photo",
                                       "description": f"**{fname}**\n{photo['ftime']} · {photo['size']}\n_(unavailable)_",
                                       "color": 0x4FC3F7, "footer": {"text": folder}, "timestamp": ts}]}
                code, _ = curl_post_json(DISCORD_WH, payload, timeout=10)
            print(f"  Discord photo [{folder}] {fname}: {'✓' if code == 200 else f'✗ {code}'}")

        if len(jpg_files) > MAX_PHOTOS:
            remaining = jpg_files[MAX_PHOTOS:]
            embed = {"embeds": [{"title": f"📷 [{folder}] — {len(remaining)} more photo(s)",
                                  "description": "\n".join(f"**{f['name']}** ({f['ftime']})" for f in remaining),
                                  "color": 0xFF8C00, "footer": {"text": folder}, "timestamp": ts}]}
            code, _ = curl_post_json(DISCORD_WH, embed, timeout=10)
            print(f"  Discord [{folder}] remaining: {'✓' if code == 200 else f'✗ {code}'}")

def check_folder(folder, url):
    html = get(url)
    if not html: return []
    files = parse_files(html, folder)
    if not files: return []

    state = load_state()
    folder_state = state.get("folders", {}).get(folder, {})
    seen = set(folder_state.get("seen", []))
    last = folder_state.get("last", "")

    # Find new files (not in seen, after last)
    new_files = []
    for f in sorted(files, key=lambda x: x["name"]):
        if f["name"] not in seen:
            if f["name"] > last or not last:
                new_files.append(f)
                seen.add(f["name"])

    if new_files:
        folder_state["seen"] = list(seen)
        folder_state["last"] = max(f["name"] for f in new_files)
        state.setdefault("folders", {})[folder] = folder_state
        save_state(state)
        print(f"  [{folder}] {len(new_files)} new file(s): {', '.join(f['name'] for f in new_files)}")
        send_discord(folder, new_files)
    else:
        print(f"  [{folder}] no new files ({len(files)} known)")

def main():
    folders = [
        ("Parking", STREAM_BASE + "/DCIM/Movie/Parking/"),
        ("RO",      STREAM_BASE + "/DCIM/Movie/RO/"),
        ("Photo",   STREAM_BASE + "/DCIM/Photo/"),
    ]
    print(f"[{datetime.datetime.now():%H:%M:%S}] Parking check...")
    for folder, url in folders:
        check_folder(folder, url)

if __name__ == "__main__":
    main()
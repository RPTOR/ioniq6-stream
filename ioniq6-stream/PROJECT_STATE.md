# IONIQ6 Dashcam Project — State File
# Updated: 2026-04-18 08:36 UTC

## What This Is
This file captures the complete state of the IONIQ6 dashcam tunnel + notification system.
It is the authoritative source of truth if session context is lost.

---

## Network Topology

| Component | Details |
|---|---|
| **Phone** | Android (Termux), Tailscale IP: `100.127.189.53`, SSH port: `8022` |
| **Camera** | VIOFO A139 Pro, RTSP at `rtsp://192.168.194.40:554/live`, dynamic IP |
| **Camera subnet** | Phone connects to camera via WiFi STA on `192.168.194.0/24` |
| **Stream server** | `rtsp_proxy.py` → proxies camera RTSP; `stream_server.py` → HTTP gateway |
| **Stream server port** | `8080` on phone, reachable at `http://100.127.189.53:8080/` |
| **RTSP proxy port** | `5544` on phone, reachable at `rtsp://100.127.189.53:5544/live` |
| **Tailscale** | Phone is on Tailscale (system level). Node cannot reach phone via Tailscale tunnel. |

## Node → Phone Connectivity
- **Node can reach phone** via Tailscale IP: `curl http://100.127.189.53:8080/` ✅ works
- **Phone cannot reach node** via node's public IP `115.164.33.137` — all ports blocked from phone's perspective
- **Phone cannot reach Discord** — DNS resolution broken in Termux (no default route)
- **Node has full internet** — can reach Discord directly

---

## Key Files

### On Phone (`100.127.189.53:8022`)

| File | Purpose |
|---|---|
| `/data/data/com.termux/files/home/ioniq6-stream/scripts/stream_server.py` | HTTP server: HLS proxy + camera HTTP proxy + web UI. **Single-threaded HTTPServer — should be ThreadingHTTPServer** |
| `/data/data/com.termux/files/home/ioniq6-stream/scripts/rtsp_proxy.py` | RTSP proxy: forwards `0.0.0.0:5544` → `192.168.194.40:554` |
| `/data/data/com.termux/files/home/ioniq6-stream/scripts/find_camera.py` | ARP cache → nmap scan → probe `.40` on known subnets. Writes `~/.camera_ip` |
| `/data/data/com.termux/files/home/ioniq6-stream/scripts/parking_watcher.py` | **OLD** — phone-side watcher, broken due to DNS. Replaced by node_parking_watcher.py |
| `/data/data/com.termux/files/home/.camera_ip` | Current camera IP (e.g. `192.168.194.40`) |
| `/data/data/com.termux/files/home/.parking_state.json` | OLD state file — NO LONGER USED (node has its own) |
| `/data/data/com.termux/files/home/.service/rtsp_proxy/run` | Runit service for rtsp_proxy |

### On Node

| File | Purpose |
|---|---|
| `/home/node/.openclaw/workspace-developer/ioniq6-stream/scripts/node_parking_watcher.py` | **ACTIVE** — runs on node, polls stream server via Tailscale, sends Discord notifications |
| `/home/node/.openclaw/workspace-developer/ioniq6-stream/scripts/node_parking_watcher.py` | Also handles MP4 thumbnail extraction (downloads MP4 to /tmp, runs /opt/ffmpeg_bin, then posts to Discord) |
| `/home/node/.parking_state.json` | **ACTIVE** state file — seeded with all known files |
| `/opt/ffmpeg_bin` | Static ffmpeg 7.0.2 — used for MP4 thumbnail extraction |
| `/home/node/.openclaw/workspace-developer/ioniq6-stream/logs/node_parking.log` | Watcher log (when run manually) |

### Web UI
| File | Purpose |
|---|---|
| `/home/node/.openclaw/workspace-developer/ioniq6-stream/assets/index.html` | Live stream (HLS.js) + file browser + download/delete |

---

## Discord Notifications

- **Webhook**: `https://discord.com/api/webhooks/1493959527073317135/97HadaLPZRy5Khz9acsc3ZhYDzKVLc9Qm1lqQRiiHjJH7SCOd8Y38l85GxbTYRfXzB66`
- **Notification types**:
  - **Photo (`.jpg`)**: Embed + thumbnail attachment via `curl_post_multipart()` ✅
  - **RO/Parking (`.mp4`)**: Embed + extracted thumbnail via `/opt/ffmpeg_bin` (download full MP4 to /tmp, extract frame, post) ✅
  - **Other files**: Text embed only

---

## Cron Jobs

### OpenClaw Cron — ACTIVE

**`ioniq6-parking-watcher`** (id: `4faca81c-c4df-4a23-a367-f146b713a5c0`)
- Schedule: `* * * * *` (every minute)
- Session target: `isolated`
- Command: `python3 /home/node/.openclaw/workspace-developer/ioniq6-stream/scripts/node_parking_watcher.py`
- Delivery: `none` (logs to cron run history)
- Status: ✅ enabled, last run OK

### Old Phone Crons (DEFUNCT — phone watcher broken)

- `*/5 * * * *` → `find_camera.py` (still runs on phone, keeps `~/.camera_ip` fresh)
- `*/1 * * * *` → `parking_watcher.py --once` (BROKEN — DNS fails)

---

## Watched Folders & State

State file: `/home/node/.parking_state.json`

| Folder | Camera Path | Files Known | Last File |
|---|---|---|---|
| Parking | `DCIM/Movie/Parking/` | 255 | `2026_0418_144931_PR.MP4` |
| RO | `DCIM/Movie/RO/` | 99 | `2026_0418_162102_R.MP4` |
| Photo | `DCIM/Photo/` | 370 | `2026_0418_162135_R.JPG` |

---

## Current Issues / Notes

1. **MP4 thumbnail takes ~60s per file** — must download full MP4 (moov atom at end prevents seeking), then extract frame. Large parking videos (50MB+) take time.
2. **No `ffmpeg` on node initially** — found static build at `/opt/ffmpeg_bin` (ffmpeg 7.0.2).
3. **Phone DNS broken** — Termux has no default route. All Discord calls must go through node.
4. **`stream_server.py` uses single-threaded HTTPServer** — should be `ThreadingHTTPServer` to avoid blocking on slow camera responses. The phone version may still have this issue.
5. **Camera IP** — currently `192.168.194.40`. `find_camera.py` on phone keeps this fresh.

---

## How to Restart Services

### Phone (if stream server dies)
```bash
ssh -p 8022 100.127.189.53
# Restart rtsp_proxy
sv restart rtsp_proxy
# Check stream_server is running
ps aux | grep stream_server | grep -v grep
# Restart stream_server if needed
cd /data/data/com.termux/files/home/ioniq6-stream/scripts && nohup python3 stream_server.py > /data/data/com.termux/files/home/.stream/server.log 2>&1 &
```

### Node watcher (if cron stops working)
```bash
# Manual test
python3 /home/node/.openclaw/workspace-developer/ioniq6-stream/scripts/node_parking_watcher.py

# Check cron status
openclaw cron list | grep ioniq6

# Re-add cron if needed
openclaw cron add ...
```

---

## URLs

- **Stream server**: `http://100.127.189.53:8080/`
- **RTSP (via Tailscale)**: `rtsp://100.127.189.53:5544/live`
- **Web UI**: `http://100.127.189.53:8080/` (live stream + file browser)
- **Camera HTTP**: `http://100.127.189.53:8080/cam/` (proxies camera folder listings)
- **MP4 files**: `http://100.127.189.53:8080/cam/DCIM/Movie/Parking/<file>`
- **Photo files**: `http://100.127.189.53:8080/cam/DCIM/Photo/<file>`

---

_Last updated: 2026-04-18 08:36 UTC_

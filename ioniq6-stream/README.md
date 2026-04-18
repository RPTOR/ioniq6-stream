# IONIQ6 VIOFO A139 Pro Dashcam Stream Server

HTTP/HLS live streaming + file browser + Discord parking notifications for the VIOFO A139 Pro dashcam, running on an Android phone (Termux) with a Tailscale VPN tunnel to this node.

## System Overview

```
VIOFO A139 Pro (camera)
    │ RTSP rtsp://192.168.194.40:554/live
    ▼
Android Phone (Termux)
    ├── rtsp_proxy.py     → listens on 0.0.0.0:5544 (RTSP tunnel)
    ├── stream_server.py  → HTTP server on :8080
    │   ├── RTSP→HLS live transcoding (ffmpeg)
    │   ├── /cam/*        → proxies camera's built-in HTTP server
    │   ├── /stream.m3u8  → HLS stream for browsers
    │   └── /transcode/*  → on-demand MP4 transcoding
    │
    └── find_camera.py    → discovers camera IP via ARP/nmap
                              (camera IP is dynamic)
        ▼
Tailscale VPN (100.127.189.53)
    │
    ├── HTTP  :8080  → accessible from internet (carrier NAT)
    │                 web UI: http://100.127.189.53:8080/
    │
    └── RTSP  :5544  → Tailscale only
                       rtsp://100.127.189.53:5544/live
```

## Components

### Phone (Android + Termux)

| File | Purpose |
|------|---------|
| `stream_server.py` | Main HTTP server — HLS proxy, camera HTTP proxy, web UI |
| `rtsp_proxy.py` | RTSP forwarder: phone:5544 → camera:554 |
| `find_camera.py` | Camera IP discovery via ARP cache + nmap fallback |
| `index.html` | Web UI — live HLS player + file browser + download |
| `parking_watcher.py` | Original Discord parking watcher (**defunct** — phone has no DNS) |

### Node (Linux server)

| File | Purpose |
|------|---------|
| `node_parking_watcher.py` | Polls phone's camera folders every 60s, sends Discord notifications |

## URLs

| Service | URL | Notes |
|---------|-----|-------|
| Web UI | `http://100.127.189.53:8080/` | Full UI + live stream + file browser |
| HLS stream | `http://100.127.189.53:8080/stream.m3u8` | For external players / smart TVs |
| RTSP direct | `rtsp://100.127.189.53:5544/live` | Native RTSP — best quality, requires Tailscale |
| Camera browse | `http://100.127.189.53:8080/cam/DCIM/Photo/` | Direct camera file access |

## Setup

### 1. Phone Dependencies (Termux)

```bash
pkg install python ffmpeg nmap openssh
pip install paho-mqtt
```

### 2. Start RTSP Proxy

```bash
cd ~/ioniq6-stream/scripts
bash rtsp_tunnel.sh   # finds camera IP, starts rtsp_proxy.py
```

Or manually:
```bash
# Find camera IP
python find_camera.py   # writes to ~/.camera_ip

# Start RTSP proxy (listens on 0.0.0.0:5544)
python rtsp_proxy.py &

# Start HTTP stream server
python stream_server.py &
```

### 3. Start Stream Server

```bash
sv restart stream_server   # if runit service is set up
# or
python stream_server.py &
```

### 4. Set Up Runit Services (optional)

**RTSP Proxy service** at `~/.service/rtsp_proxy/run`:
```bash
#!/data/data/com.termux/files/usr/bin/sh
cd /data/data/com.termux/files/home/ioniq6-stream/scripts
exec ./rtsp_tunnel.sh
```

**Stream Server service** at `~/.service/stream_server/run`:
```bash
#!/data/data/com.termux/files/usr/bin/sh
cd /data/data/com.termux/files/home/ioniq6-stream/scripts
exec python stream_server.py
```

### 5. Node Parking Watcher (this node)

The phone cannot make outbound HTTPS connections (Termux routing table has no default route — DNS is broken). The parking watcher therefore runs on this node instead, polling the phone's stream server.

```bash
# Create cron job via OpenClaw:
# Every minute: python /path/to/node_parking_watcher.py
```

The watcher monitors:
- `DCIM/Movie/Parking/` — parking events
- `DCIM/Movie/RO/` — continuous loop recordings
- `DCIM/Photo/` — photos

State is stored in `/home/node/.parking_state.json`.

## Network Constraints

**Phone (Termux) routing table:**
```
10.81.241.0/24   (Tailscale)
10.133.77.248/30 (Tailscale)
192.168.194.0/24 (Android WiFi tether to camera)
```

No default route — Termux processes cannot reach the internet via DNS or most ports.

**Phone internet access:**
- Inbound: HTTP (80/8080) via carrier NAT
- Outbound: limited; Tailscale runs at Android system level, not accessible to Termux

**Solution:** All Discord/HTTPS outbound calls run from this node, not the phone.

## Camera IP

Camera IP changes dynamically after each drive. `find_camera.py` keeps it updated:

1. Checks `~/.camera_ip` for last-known IP
2. Verifies reachability via HTTP probe
3. Falls back to `arp -a` scan of known subnets
4. Falls back to nmap ping scan of subnets

Known VIOFO AP subnets: `192.168.109.0/24`, `192.168.167.0/24`, `192.168.177.0/24`, `192.168.194.0/24`

Camera always at `.40` on these subnets.

## Discord Notifications

The `node_parking_watcher.py` sends Discord embeds via webhook:

- **Parking event**: front/rear clip filenames, timestamp
- **RO event**: continuous recording clip
- **Photo event**: photo filename

Thumbnail extraction for MP4 files requires local ffmpeg (`/opt/ffmpeg_bin` on this node). MP4 files have the moov atom at the end, preventing HTTP seeking — files are downloaded to `/tmp/` before thumbnail extraction.

## Web UI Features

- **Live Stream tab**: HLS.js player connecting to `stream.m3u8`
- **Quick links**: 🔴 HLS direct URL, 📹 RTSP direct URL
- **Browse Files tab**: Navigate camera SD card, download files
  - Folders: `DCIM/Photo/`, `DCIM/Movie/Parking/`, `DCIM/Movie/RO/`
  - Click file → downloads (JPG, MP4, all formats)
  - ⬆ / 🏠 navigation buttons
- **Snapshot**: capture current video frame as JPG

## Key Files

| Path | Description |
|------|-------------|
| `~/.camera_ip` | Current camera IP (updated by `find_camera.py`) |
| `~/.parking_state.json` | Parking watcher state on node |
| `~/.stream/` | HLS segment files (stream.m3u8 + stream*.ts) |
| `~/.transcode/<session>/` | On-demand MP4 transcode output |

## SSH Access to Phone

```bash
ssh -p 8022 100.127.189.53
# from this node:
ssh -o ConnectTimeout=5 -o StrictHostKeyChecking=no -p 8022 100.127.189.53
```

## Troubleshooting

**Stream not loading:**
```bash
# Check camera IP is fresh
ssh phone "cat ~/.camera_ip"

# Check rtsp_proxy is running
ssh phone "ps aux | grep rtsp_proxy | grep -v grep"

# Check port 5544 listening
ssh phone "netstat -tlnp | grep 5544"

# Check ffmpeg is running
ssh phone "ps aux | grep ffmpeg | grep -v grep"
```

**Browse Files 404:** Camera may have disconnected from WiFi. Restart the camera's AP and re-run `find_camera.py`.

**Discord notifications not sending:** Check node's `node_parking_watcher.py` logs. Ensure webhook URL is valid.

**Phone SSH timeout:** Phone is likely asleep. Wake it up (unlock screen), then retry.

## GitHub

Repo: `https://github.com/RPTOR/ioniq6-stream`

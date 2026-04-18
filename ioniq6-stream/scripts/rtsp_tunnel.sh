#!/data/data/com.termux/files/usr/bin/bash
# Dynamic RTSP Tunnel: Detect IP -> Resolve IP -> Forward traffic
# Usage: ./rtsp_tunnel.sh

# 1. Force a quick re-scan to ensure we have the absolute latest IP
echo "Checking camera network..."
python3 /home/node/.openclaw/workspace-developer/ioniq6-stream/scripts/find_camera.py

# 2. Get the currently detected IP from the state file
CAMERA_IP=$(cat ~/.camera_ip)
echo "Tunneling RTSP from camera at $CAMERA_IP to port 5544..."

# 3. Use python proxy to forward local port 5544 to camera RTSP port
exec python3 /home/node/.openclaw/workspace-developer/ioniq6-stream/scripts/rtsp_proxy.py

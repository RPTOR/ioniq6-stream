#!/bin/bash
# Keep node_parking_watcher running as a daemon
while true; do
    python3 /home/node/.openclaw/workspace-developer/ioniq6-stream/scripts/node_parking_watcher.py >> /home/node/.openclaw/workspace-developer/ioniq6-stream/logs/node_parking.log 2>&1
    sleep 60
done
#!/data/data/com.termux/files/usr/bin/sh
cd /data/data/com.termux/files/home
exec python3 -u ioniq6-stream/scripts/stream_server.py >> .stream/server.log 2>&1
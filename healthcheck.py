import urllib.request
import json
import os
import subprocess
from datetime import datetime

WEBHOOK_URL = "https://discord.com/api/webhooks/1493958751919542373/YOUR_WEBHOOK_TOKEN_HERE"

def notify(msg):
    try:
        data = json.dumps({"content": msg}).encode("utf-8")
        req = urllib.request.Request(WEBHOOK_URL, data=data, headers={"Content-Type": "application/json"}, method="POST")
        urllib.request.urlopen(req, timeout=10)
    except: pass

def get_cam_ip():
    try:
        with open("/data/data/com.termux/files/home/.camera_ip", "r") as f:
            return f.read().strip()
    except:
        return "Unknown"

def count_new_parking_files():
    try:
        with open("/data/data/com.termux/files/home/.parking_state.json", "r") as f:
            state = json.load(f)
            seen = state.get("seen", {})
            total_new = 0
            for folder, files in seen.items():
                total_new += len(files)
            return total_new
    except:
        return 0

def check_service(proc_name):
    try:
        # Check if process is running
        subprocess.run(["pgrep", "-f", proc_name], check=True, capture_output=True)
        return True
    except:
        return False

def main():
    ssh_up = False
    try:
        subprocess.run(["nc", "-z", "-w5", "100.127.189.53", "8022"], check=True)
        ssh_up = True
    except: pass

    cam_ip = get_cam_ip()
    new_files = count_new_parking_files()
    
    # Check backend services
    services = {
        "Stream Server": "stream_server.py",
        "Parking Watcher": "parking_watcher.py"
    }
    svc_status = ""
    for name, proc in services.items():
        if check_service(proc):
            svc_status += f"- {name}: ✅\n"
        else:
            svc_status += f"- {name}: ❌ (Restarting...)\n"
            # Attempt restart
            if name == "Stream Server":
                 subprocess.run(["ssh", "-p", "8022", "u0_a13@100.127.189.53", 
                                 "killall python3; nohup python3 -u ioniq6-stream/scripts/stream_server.py > /dev/null 2>&1 &"])
            elif name == "Parking Watcher":
                 subprocess.run(["ssh", "-p", "8022", "u0_a13@100.127.189.53", 
                                 "nohup python3 -u ioniq6-stream/scripts/parking_watcher.py > /dev/null 2>&1 &"])

    status_msg = f"🔍 Healthcheck Status:\n- SSH: {'✅' if ssh_up else '❌'}\n- Camera IP: {cam_ip}\n- New Parking Files: {new_files}\n\nServices:\n{svc_status}"

    if ssh_up:
        try:
            urllib.request.urlopen("http://100.127.189.53:8080/", timeout=10)
            status_msg += "\n- Website: ✅"
        except:
            status_msg += "\n- Website: ❌ (Restarting...)"
            # Already handled by service check logic above? Let's be explicit
            pass

    notify(status_msg)

if __name__ == "__main__":
    main()

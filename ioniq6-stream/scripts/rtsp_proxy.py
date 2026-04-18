import socket
import select
import sys
import os

def tunnel(local_port, remote_ip, remote_port):
    # Setup listener
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind(('0.0.0.0', local_port))
    server.listen(5)
    print(f"Tunneling 0.0.0.0:{local_port} -> {remote_ip}:{remote_port}")

    while True:
        client, addr = server.accept()
        print(f"Connection from {addr}")
        target = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        target.connect((remote_ip, remote_port))
        
        # Forward data
        inputs = [client, target]
        try:
            while inputs:
                readable, _, _ = select.select(inputs, [], [])
                for s in readable:
                    data = s.recv(4096)
                    if not data:
                        inputs = []
                        break
                    if s is client:
                        target.send(data)
                    else:
                        client.send(data)
        except Exception as e:
            print(f"Tunnel error: {e}")
        finally:
            client.close()
            target.close()

if __name__ == "__main__":
    # Get current camera IP
    try:
        with open("/data/data/com.termux/files/home/.camera_ip", "r") as f:
            cam_ip = f.read().strip()
    except:
        cam_ip = "192.168.194.40"
    
    tunnel(5544, cam_ip, 554)

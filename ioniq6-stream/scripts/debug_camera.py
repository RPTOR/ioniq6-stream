#!/data/data/com.termux/files/usr/bin/python3
import subprocess, os, time
from http.client import HTTPConnection

t0 = time.time()
print('Starting find_camera_ip...')

# Quick check: read cached IP
sf = os.path.expanduser('~/.camera_ip')
print('Cached IP file exists:', os.path.exists(sf))
if os.path.exists(sf):
    cached = open(sf).read().strip()
    print('Cached IP:', cached)
    try:
        c = HTTPConnection(cached, 80, timeout=3)
        c.request("GET", "/")
        r = c.getresponse()
        print('Connection success:', r.getheader("Server",""))
        c.close()
    except Exception as e:
        print('Connection failed:', e)

print('Total time:', time.time()-t0)

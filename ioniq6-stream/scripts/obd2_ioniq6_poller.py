#!/data/data/com.termux/files/usr/bin/python3
"""
OBD2 poller for Hyundai IONIQ 5/6 EV via USB OBD2 adapter.
Loads PIDs from a CSV mapping file and polls live data.

Usage:
    python3 obd2_ioniq6_poller.py [--port /dev/ttyUSB0] [--csv PIDS.csv] [--interval 1]

Requirements (Termux):
    pip install pyserial

    pkg install python  (if not already installed)
    pip install pyserial
"""

import serial
import time
import csv
import re
import sys
import argparse
import os
from collections import OrderedDict
from datetime import datetime

# ─── USB OBD2 device ────────────────────────────────────────────────────────
# On Termux/Linux, plug in OBD2 USB adapter and check with:
#   ls /dev/serial/by-id/       ← recommended
#   ls /dev/ttyUSB*             ← fallback
# Typical ELM327 USB: /dev/ttyUSB0
DEFAULT_PORT = "/dev/ttyUSB0"
DEFAULT_BAUD = 38400   # ELM327 default (some use 9600, many need 38400)
DEFAULT_CSV = "ioniq6_pids.csv"
DEFAULT_INTERVAL = 1.0

# ─── Helpers ─────────────────────────────────────────────────────────────────

def sign_byte(b):
    return b if b < 128 else b - 256

def sign_word(w):
    return w if w < 32768 else w - 65536

def parse_equation(eq: str, raw: dict, computed: dict | None = None) -> float | None:
    """
    Evaluate a CSV equation against a dict of raw byte labels -> int values.

    Labels: a-t = bytes 0-19, aa/ab = bytes 20-51 in response data.
    Pattern X<NN  → byte X right-shifted by NN bits, masked to 0xFF.
    Pattern {J:N} → bit N of byte J.
    SIGNED(X)     → signed 8-bit interpretation.
    Int24(a:b:c)  → 24-bit big-endian from three byte labels.
    val{Name}     → previously computed PID value.
    """
    if not eq or eq.strip() == "" or eq.startswith("~") or eq.startswith("!"):
        return None
    if computed is None:
        computed = {}

    expr = eq.strip()

    # Substitute val{} references
    for match in re.findall(r"val\{([^}]+)\}", expr):
        expr = expr.replace(f"val{{{match}}}", str(computed.get(match, 0)))

    # Byte-shift: LETTER<NN  (e.g. q<8 means (q >> 8) & 0xFF)
    def byte_shift(m):
        key, shift = m.group(1), int(m.group(2))
        return str((raw.get(key, 0) >> shift) & 0xFF)
    expr = re.sub(r"([a-z]{1,2})<(\d+)", byte_shift, expr)

    # Bit-test: {J:N}
    def bit_test(m):
        parts = m.group(1).split(":")
        val = raw.get(parts[0], 0)
        bit_pos = int(parts[1]) if len(parts) > 1 else 0
        return str((val >> bit_pos) & 1)
    expr = re.sub(r"\{([^}]+)\}", bit_test, expr)

    # SIGNED(X)
    def signed_byte(m):
        return str(sign_byte(raw.get(m.group(1), 0)))
    expr = re.sub(r"SIGNED\(([a-z]{1,2})\)", signed_byte, expr)

    # Int24(a:b:c)
    def int24_conv(m):
        parts = [x.strip() for x in m.group(1).split(":")]
        total = 0
        for p in parts:
            total = (total << 8) | raw.get(p, 0)
        return str(total)
    expr = re.sub(r"Int24\(([^)]+)\)", int24_conv, expr)

    # Substitute remaining letter labels (longest first to avoid partial matches)
    for key in sorted(raw.keys(), key=lambda x: -len(x)):
        expr = re.sub(rf"\b{key}\b", str(raw[key]), expr)

    try:
        return float(eval(expr))
    except Exception:
        return None


# ─── ELM327 interface ─────────────────────────────────────────────────────────

class ELM327:
    def __init__(self, port: str, baud: int = DEFAULT_BAUD, timeout: float = 3.0):
        self.ser = serial.Serial(port, baud, timeout=int(timeout))
        self._protocol = None

    def _send(self, cmd: bytes) -> str:
        self.ser.write(cmd)
        self.ser.flush()
        buf = b""
        deadline = time.time() + self.ser.timeout * 4
        while time.time() < deadline:
            ch = self.ser.read(1)
            if not ch:
                continue
            if ch == b">":
                break
            buf += ch
        return buf.decode("ascii", errors="replace").strip()

    def init(self) -> bool:
        """Initialize ELM327 with EV-appropriate settings."""
        time.sleep(1.5)   # ATZ reset needs time

        for cmd, expect in [
            (b"ATZ\r",         None),
            (b"ATE0\r",        None),
            (b"ATL0\r",        None),
            (b"ATS0\r",        None),
            (b"ATH1\r",        None),   # headers on so we see ECU addresses
            (b"ATSP0\r",       None),   # auto protocol
            (b"ATAT1\r",       None),   # adaptive timing on
            (b"ATST32\r",      None),   # timeout 50ms (0x32 = 50 decimal)
        ]:
            time.sleep(0.2)
            r = self._send(cmd)
            if "?" in r and cmd[:2] != b"AT":
                pass  # tolerate INIT echoes

        r = self._send(b"ATI\r")
        print(f"  ELM327 ID: {r}")

        # Detect protocol
        r = self._send(b"ATDPN\r")
        self._protocol = r.strip()
        print(f"  Protocol: {self._protocol}")

        return True

    def set_header(self, header: str) -> str:
        """Set the CAN TX header (ECU address)."""
        cmd = b"ATSH" + bytes.fromhex(header) + b"\r"
        return self._send(cmd)

    def query_raw(self, frame_hex: str) -> str:
        """Send a raw CAN frame and return the raw response string."""
        cmd = frame_hex.encode() + b"\r"
        return self._send(cmd)

    def poll_pid(self, header: str, mode: int, pid_bytes: bytes) -> bytes | None:
        """
        Send a mode/PID request to a specific ECU header.
        Returns the 8-byte data payload on success, None on failure.
        """
        # Set TX header
        self.set_header(header)
        time.sleep(0.03)

        # Build TX frame: <mode> <pid bytes> [pad to 8]
        data = bytes([mode]) + pid_bytes
        data += b"\x00" * (8 - len(data))
        frame_hex = " ".join(f"{b:02X}" for b in data)

        resp = self.query_raw(frame_hex)
        if not resp or "NO DATA" in resp or "ERROR" in resp:
            return None

        # Parse response: "7E8 03 62 01 01 XX YY ..."
        # Find 8 consecutive data bytes after the length byte
        try:
            parts = resp.replace(">", "").split()
            # Find the length byte (first byte after ID that indicates data length)
            data_len = None
            data_start = None
            for i, p in enumerate(parts):
                if len(p) == 2:
                    try:
                        v = int(p, 16)
                        if 2 <= v <= 8 and i + v + 1 <= len(parts):
                            # This might be the length byte
                            data_start = i + 1
                            data_len = v
                            break
                    except ValueError:
                        pass

            if data_start and data_len:
                data_hex = "".join(parts[data_start:data_start + data_len])
                return bytes.fromhex(data_hex)
        except Exception:
            pass

        return None

    def close(self):
        self.ser.close()


# ─── PID Loader ──────────────────────────────────────────────────────────────

class OBD2PID:
    def __init__(self, name: str, short: str, mode_pid: str, equation: str,
                 min_val: str, max_val: str, unit: str, header: str):
        self.name = name
        self.short = short
        # ModeAndPID e.g. "0x220101" → mode=0x22, pid_bytes="0101"
        mode_pid = mode_pid.strip()
        self.mode = int(mode_pid[:2], 16) if mode_pid else 0x22
        self.pid_bytes = bytes.fromhex(mode_pid[2:]) if mode_pid else b""
        self.equation = equation
        self.min = float(min_val) if min_val else 0.0
        self.max = float(max_val) if max_val else 0.0
        self.unit = unit
        self.header = (header or "7E4").strip()
        self.last_value: float | None = None
        self.last_raw: dict = {}

    def parse_response(self, rx_data: bytes) -> float | None:
        """
        Parse an 8-byte CAN data payload.
        For mode 0x22 response: [mode+0x40] [pid bytes] [data...]
        Letter labels a,b,c... map to rx_data[2] onwards.
        """
        if len(rx_data) < 3:
            return None

        # Confirm response mode = request mode + 0x40
        if rx_data[0] != (self.mode + 0x40):
            return None

        # Map bytes to letter labels
        raw = {}
        for i in range(2, len(rx_data)):
            idx = i - 2   # a=0, b=1, ... t=19, then aa=20, ab=21, ...
            if idx < 20:
                raw[chr(ord('a') + idx)] = rx_data[i]
            else:
                rem = idx - 20
                raw['a' + chr(ord('a') + rem // 26 + 1) + chr(ord('a') + rem % 26)]

        self.last_raw = raw
        return parse_equation(self.equation, raw)


def load_pids(csv_path: str):
    """
    Load PIDs from the IONIQ 6 CSV file.
    Returns (pids_by_header, calc_pids).
    pids_by_header: OrderedDict[header_str, list[OBD2PID]]
    calc_pids: list of OBD2PID with no raw PID (computed values)
    """
    pids_by_header: dict = OrderedDict()
    calc_pids: list = []

    with open(csv_path, newline="", encoding="utf-8") as f:
        reader = csv.reader(f)
        next(reader)  # skip column names

        for row in reader:
            if len(row) < 8:
                continue
            name   = row[0].strip()
            short  = row[1].strip()
            mp     = row[2].strip()
            eq     = row[3].strip()
            min_v  = row[4].strip()
            max_v  = row[5].strip()
            unit   = row[6].strip()
            header = row[7].strip() if len(row) > 7 else "7E4"

            if not name or name.startswith("~") or name.startswith("!"):
                continue

            # Computed PIDs (no raw ModeAndPID)
            if name.startswith("004_CALC") or not mp:
                p = OBD2PID(name, short, "", eq, min_v, max_v, unit, header)
                calc_pids.append(p)
                continue

            p = OBD2PID(name, short, mp, eq, min_v, max_v, unit, header)
            pids_by_header.setdefault(header, []).append(p)

    return pids_by_header, calc_pids


# ─── Poller ───────────────────────────────────────────────────────────────────

def poll_ecu(elm: ELM327, pids: list[OBD2PID], header: str, computed: dict) -> dict:
    """
    Poll all raw PIDs for one ECU header.
    Returns {short_name: computed_value}.
    """
    results = {}
    for pid in pids:
        rx = elm.poll_pid(header, pid.mode, pid.pid_bytes)
        if rx is None:
            continue
        val = pid.parse_response(rx)
        if val is not None:
            results[pid.short] = val
            computed[pid.short] = val   # store for CALC PIDs that may reference it
    return results


def main():
    ap = argparse.ArgumentParser(description="IONIQ 5/6 OBD2 poller")
    ap.add_argument("--port",     default=DEFAULT_PORT, help="Serial port (e.g. /dev/ttyUSB0)")
    ap.add_argument("--csv",      default=DEFAULT_CSV, help="PID CSV file")
    ap.add_argument("--interval", type=float, default=DEFAULT_INTERVAL, help="Poll interval (s)")
    ap.add_argument("--once",      action="store_true", help="Poll once and exit")
    ap.add_argument("--baud",      type=int, default=DEFAULT_BAUD, help="Baud rate (default 38400)")
    args = ap.parse_args()

    # Resolve CSV
    csv_path = args.csv
    if not os.path.exists(csv_path):
        alt = os.path.join(os.path.dirname(__file__), args.csv)
        if os.path.exists(alt):
            csv_path = alt
        else:
            print(f"ERROR: CSV not found: {args.csv}")
            sys.exit(1)

    print(f"Loading PIDs from: {csv_path}")
    pids_by_header, calc_pids = load_pids(csv_path)

    print(f"Port: {args.port} @ {args.baud} baud")
    print(f"ECUs : {list(pids_by_header.keys())}")
    total = sum(len(v) for v in pids_by_header.values())
    print(f"Raw PIDs : {total}")
    print(f"Calc PIDs: {len(calc_pids)}")
    print()

    # Check port exists
    if not os.path.exists(args.port):
        print(f"WARNING: {args.port} not found.")
        print("  Check with: ls /dev/serial/by-id/  or  ls /dev/ttyUSB*")
        print("  You may need to run as root or add user to 'dialout' group:")
        print("    sudo usermod -a -G dialout $USER")
        print()

    try:
        elm = ELM327(args.port, args.baud)
        elm.init()
    except serial.SerialException as e:
        print(f"ERROR: Cannot open {args.port}: {e}")
        sys.exit(1)

    try:
        while True:
            ts = datetime.now().strftime("%H:%M:%S")
            print(f"[{ts}]")
            computed: dict = {}

            for header, pids in pids_by_header.items():
                results = poll_ecu(elm, pids, header, computed)
                for short, val in results.items():
                    pid = next((p for p in pids if p.short == short), None)
                    unit = pid.unit if pid else ""
                    print(f"  {short:20s}: {val:10.3f} {unit}")

            # Evaluate CALC PIDs
            for cp in calc_pids:
                val = parse_equation(cp.equation, {}, computed)
                if val is not None:
                    print(f"  {cp.short:20s}: {val:10.3f} {cp.unit}")

            print()
            if args.once:
                break
            time.sleep(args.interval)

    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        elm.close()


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Read LTE signal from an Android phone over USB (adb), no root, no app.

For a quick signal survey while the phone is tethered to this PC: it parses
`adb shell dumpsys telephony.registry` and prints RSRP / RSRQ / SINR with the
same quality verdict used elsewhere in the project.

    python3 adb_signal.py              # one reading
    python3 adb_signal.py --loop       # keep reading every 3s
    python3 adb_signal.py --loop --interval 5 --csv

Note: on MIUI (and some other skins) the adb dump exposes the signal level but
NOT the band/EARFCN or operator — for those use an on-phone app (NetMonster /
G-NetTrack Lite) or Termux:API. This tool is the PC-side, cabled counterpart to
phone_agent.py; it is read-only toward the phone.
"""
from __future__ import annotations

import argparse
import csv
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from bands import classify_signal

DATA_DIR = Path(__file__).parent / "data"
UNAVAIL = "2147483647"  # Android's Integer.MAX_VALUE sentinel for "no value"

# mLte=CellSignalStrengthLte: rssi=-77 rsrp=-86 rsrq=-10 rssnr=4 ... cqi=0 ta=0
LTE_RE = re.compile(
    r"CellSignalStrengthLte:\s*rssi=(-?\d+)\s+rsrp=(-?\d+)\s+rsrq=(-?\d+)\s+rssnr=(-?\d+)")


def _num(s):
    return None if s is None or s == UNAVAIL else int(s)


def read_once() -> dict | None:
    """Return the best LTE signal sample from the phone, or None if adb/the
    phone/the LTE fields are unavailable."""
    try:
        out = subprocess.run(["adb", "shell", "dumpsys", "telephony.registry"],
                             capture_output=True, text=True, timeout=10).stdout
    except (subprocess.SubprocessError, FileNotFoundError):
        return None
    best = None
    for m in LTE_RE.finditer(out):
        rssi, rsrp, rsrq, sinr = (_num(m.group(i)) for i in range(1, 5))
        if rsrp is None:
            continue
        cand = {"rssi": rssi, "rsrp": rsrp, "rsrq": rsrq, "sinr": sinr}
        # Prefer the strongest RSRP among reported cells (the serving one).
        if best is None or rsrp > best["rsrp"]:
            best = cand
    return best


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0],
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--loop", action="store_true", help="keep reading until Ctrl-C")
    ap.add_argument("--interval", type=int, default=3)
    ap.add_argument("--csv", action="store_true", help="also append to data/adb_signal_<day>.csv")
    args = ap.parse_args()

    writer = None
    if args.csv:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        day = datetime.now(timezone.utc).strftime("%Y%m%d")
        path = DATA_DIR / f"adb_signal_{day}.csv"
        new = not path.exists()
        fh = path.open("a", newline="")
        writer = csv.writer(fh)
        if new:
            writer.writerow(["timestamp", "rsrp", "rsrq", "sinr", "rssi", "quality"])

    def show():
        s = read_once()
        ts = datetime.now().strftime("%H:%M:%S")
        if not s:
            print(f"[{ts}] nessun dato LTE (telefono collegato e autorizzato? adb devices)")
            return
        q = classify_signal(s["rsrp"], s["sinr"])
        print(f"[{ts}] RSRP {s['rsrp']} | RSRQ {s['rsrq']} | SINR {s['sinr']} | "
              f"RSSI {s['rssi']} | -> {q}")
        if writer:
            writer.writerow([datetime.now(timezone.utc).isoformat(),
                             s["rsrp"], s["rsrq"], s["sinr"], s["rssi"], q])

    if not args.loop:
        show()
        return 0
    try:
        while True:
            show()
            time.sleep(max(1, args.interval))
    except KeyboardInterrupt:
        print("\nstop.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

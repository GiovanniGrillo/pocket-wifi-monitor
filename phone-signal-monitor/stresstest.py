#!/usr/bin/env python3
"""Load test a mobile link via a phone's USB tethering, with a hard data budget.

The point is NOT the headline peak Mbps (capped by the phone/modem and its
WiFi, not the SIM). The point is the two numbers that predict whether a real
household works on this connection:

  1. Sustained aggregate throughput under load.
  2. Latency UNDER LOAD vs idle (bufferbloat) — if latency explodes when the
     link is busy, video calls and streaming stutter even with "enough" Mbps.

It ramps through several concurrency levels (default 1 / 3 / 7 parallel
streams, to mirror a handful of household devices), saturating the link at
each level while sampling latency.

Downloads use Cloudflare's sizing endpoint (speed.cloudflare.com/__down),
which lets the script spend an EXACT, bounded amount of data. It stops the
moment the budget is reached. All traffic is bound to the chosen interface.

Setup: connect the phone by USB and turn on "USB tethering", then:

    python3 stresstest.py                       # default ramp, 300 MB budget
    python3 stresstest.py --budget-mb 100       # tighter data cap
    python3 stresstest.py --ramp 1,3,7 --phase 20
    python3 stresstest.py --iface enp0s20u1     # pick the tether interface by hand

Read-only: it only *uses* the data link, changes nothing. For the signal
(RSRP/band) use the phone itself (phone_agent.py, or NetMonster).
"""
from __future__ import annotations

import argparse
import os
import statistics
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone

DOWN_URL = "https://speed.cloudflare.com/__down?bytes={n}"
LAT_URL = "http://connectivitycheck.gstatic.com/generate_204"


def find_tether_iface():
    """First USB-attached mobile interface: a phone sharing data over USB
    tethering shows up under rndis_host / cdc_ether / cdc_ncm / cdc_mbim."""
    net = "/sys/class/net"
    try:
        names = sorted(os.listdir(net))
    except OSError:
        return None
    for name in names:
        try:
            drv = os.path.basename(os.path.realpath(
                os.path.join(net, name, "device", "driver")))
        except OSError:
            continue
        if drv in ("rndis_host", "cdc_ether", "cdc_ncm", "cdc_mbim"):
            return name
    return None


def now() -> str:
    return datetime.now(timezone.utc).strftime("%H:%M:%S")


def one_download(iface: str, nbytes: int, max_time: int) -> tuple[int, float]:
    """Download nbytes through iface. Returns (bytes_received, seconds)."""
    try:
        p = subprocess.run(
            ["curl", "-s", "--interface", iface, "-o", "/dev/null",
             "-w", "%{size_download} %{time_total}", "--max-time", str(max_time),
             DOWN_URL.format(n=nbytes)],
            capture_output=True, text=True, timeout=max_time + 5,
        )
    except subprocess.TimeoutExpired:
        return 0, float(max_time)
    parts = (p.stdout or "").split()
    if len(parts) == 2:
        try:
            return int(parts[0]), float(parts[1])
        except ValueError:
            return 0, 0.0
    return 0, 0.0


class LatencySampler(threading.Thread):
    """Times a tiny HTTP request every ~1s; used to compare idle vs loaded."""

    def __init__(self, iface: str, timeout_s: int = 5):
        super().__init__(daemon=True)
        self.iface, self.timeout_s = iface, timeout_s
        self._run = False
        self.samples: list[float] = []

    def _once(self):
        try:
            p = subprocess.run(
                ["curl", "-s", "--interface", self.iface, "-o", "/dev/null",
                 "-w", "%{time_total}", "--max-time", str(self.timeout_s), LAT_URL],
                capture_output=True, text=True, timeout=self.timeout_s + 3,
            )
            return float(p.stdout.strip()) * 1000
        except (subprocess.SubprocessError, ValueError):
            return None

    def collect_window(self):
        """Return (samples_taken) while active; clears between phases."""
        self.samples = []

    def run(self):
        self._run = True
        while self._run:
            ms = self._once()
            if ms is not None:
                self.samples.append(ms)
            time.sleep(1.0)

    def stop(self):
        self._run = False


def run_phase(iface: str, streams: int, phase_s: int, chunk_mb: int,
              lat: LatencySampler, budget_left_bytes: int) -> dict:
    """Saturate the link with `streams` parallel downloads for ~phase_s,
    not exceeding budget_left_bytes. Returns throughput + latency stats."""
    chunk = chunk_mb * 1024 * 1024
    lat.samples = []  # fresh latency window for this phase
    start = time.monotonic()
    total_bytes = 0
    results: list[tuple[int, float]] = []
    lock = threading.Lock()

    def worker():
        nonlocal total_bytes
        while time.monotonic() - start < phase_s:
            with lock:
                if total_bytes >= budget_left_bytes:
                    return
            b, t = one_download(iface, chunk, max_time=max(5, phase_s))
            with lock:
                total_bytes += b
                results.append((b, t))
            if b == 0:
                return

    threads = [threading.Thread(target=worker, daemon=True) for _ in range(streams)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=phase_s + 15)
    elapsed = max(0.001, time.monotonic() - start)
    mbps = total_bytes * 8 / elapsed / 1e6
    lw = lat.samples[:]
    return {
        "streams": streams, "bytes": total_bytes, "elapsed": elapsed,
        "mbps": round(mbps, 2),
        "lat_med": round(statistics.median(lw), 1) if lw else None,
        "lat_p95": round(sorted(lw)[int(len(lw) * 0.95) - 1], 1) if len(lw) >= 3 else None,
        "lat_max": round(max(lw), 1) if lw else None,
    }


def verdict(peak_mbps: float, idle_lat: float | None, loaded_lat: float | None) -> list[str]:
    out = []
    # Household scenario: 2 HD Netflix (~10 Mbps) + smart-home (~1-2 Mbps).
    if peak_mbps >= 25:
        out.append(f"Banda {peak_mbps:.0f} Mbps: regge 2 stream Netflix in 4K + domotica.")
    elif peak_mbps >= 12:
        out.append(f"Banda {peak_mbps:.0f} Mbps: ok 2 stream HD + domotica; 4K a fatica.")
    elif peak_mbps >= 6:
        out.append(f"Banda {peak_mbps:.0f} Mbps: giusta per 1 stream HD; 2 contemporanei rischiano.")
    else:
        out.append(f"Banda {peak_mbps:.0f} Mbps: stretta per lo streaming multiplo.")
    if idle_lat and loaded_lat:
        delta = loaded_lat - idle_lat
        if delta < 50:
            out.append(f"Latenza sotto carico stabile (+{delta:.0f} ms): buono.")
        elif delta < 200:
            out.append(f"Latenza sotto carico sale di {delta:.0f} ms: accettabile, qualche lag.")
        else:
            out.append(f"Latenza sotto carico esplode (+{delta:.0f} ms, bufferbloat): "
                       f"chiamate/giochi soffrono anche con banda sufficiente.")
    out.append("NB: il tetto dipende dal device usato per il test (telefono/modem) e dal "
               "suo WiFi; a parità di segnale un router migliore fa di più. La cella/segnale "
               "resta comunque il vero limite in un posto con poca copertura.")
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0],
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--ramp", default="1,3,7",
                    help="concurrency levels to test, comma-separated (default 1,3,7)")
    ap.add_argument("--phase", type=int, default=20, help="seconds per level (default 20)")
    ap.add_argument("--chunk-mb", type=int, default=10, help="download size per request (default 10)")
    ap.add_argument("--budget-mb", type=int, default=300,
                    help="HARD cap on total data spent; stops when reached (default 300)")
    ap.add_argument("--iface", help="network interface to test through "
                    "(default: auto-detect a phone USB tether)")
    args = ap.parse_args()

    iface = args.iface or find_tether_iface()
    if not iface:
        print("ERRORE: nessuna interfaccia mobile trovata.")
        print("  Collega il telefono via USB e attiva il 'Tethering USB',")
        print("  poi rilancia (eventualmente con --iface <nome>).")
        print("  (vedi le interfacce con:  ip -br addr)")
        return 1
    levels = [int(x) for x in args.ramp.split(",") if x.strip()]
    budget_bytes = args.budget_mb * 1024 * 1024

    print(f"[{now()}] stress test via {iface} | ramp={levels} | "
          f"phase={args.phase}s | budget={args.budget_mb} MB")
    print("  (Ctrl-C per fermare; il budget dati è un tetto rigido)\n")

    lat = LatencySampler(iface)
    lat.start()

    # Idle latency baseline: ~6 s of quiet before loading the link.
    time.sleep(6)
    idle_lat = round(statistics.median(lat.samples), 1) if lat.samples else None
    print(f"[{now()}] latenza a vuoto (mediana): {idle_lat} ms\n")

    spent = 0
    phases = []
    try:
        for c in levels:
            if spent >= budget_bytes:
                print(f"[{now()}] budget dati esaurito, stop.")
                break
            left = budget_bytes - spent
            r = run_phase(iface, c, args.phase, args.chunk_mb, lat, left)
            spent += r["bytes"]
            phases.append(r)
            print(f"[{now()}] {c:>2} stream | {r['mbps']:>6.2f} Mbps | "
                  f"lat med {r['lat_med']} / p95 {r['lat_p95']} / max {r['lat_max']} ms | "
                  f"dati finora {spent/1024/1024:.0f} MB")
    except KeyboardInterrupt:
        print(f"\n[{now()}] interrotto.")
    finally:
        lat.stop()
        time.sleep(0.2)

    print()
    print(f"  dati totali spesi: {spent/1024/1024:.0f} MB")
    if phases:
        peak = max(p["mbps"] for p in phases)
        loaded = max((p["lat_med"] for p in phases if p["lat_med"]), default=None)
        print("\n=== Verdetto ===")
        for line in verdict(peak, idle_lat, loaded):
            print("  • " + line)
    return 0


if __name__ == "__main__":
    sys.exit(main())

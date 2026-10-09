#!/usr/bin/env python3
"""Autonomous mobile-signal sensor that runs ON an Android phone (Termux).

Leave the phone (with the SIM) at the mountain spot. Each cycle this reads the
serving-cell signal via Termux:API, runs a tiny data-works check, and POSTs the
sample to collector.py on your home PC (over Tailscale), so you watch the
mountain connection live from home. It also keeps a local CSV/JSONL backup in
case the uplink drops.

Why a phone: its modem is far better than the cheap pocket-wifi — it supports
all the bands (incl. B20 800 MHz), reads RSRP/RSRQ/SINR accurately, has a
battery, and has no broken SIM tray.

Setup on the phone (no root):
  1. Install Termux and Termux:API (from F-Droid).
  2. In Termux:  pkg install python termux-api
  3. Copy this folder over (at least phone_agent.py + bands.py).
  4. Install Tailscale on phone and PC; get the PC's Tailscale IP (100.x.y.z).
  5. Run:
       python phone_agent.py --collector http://100.x.y.z:8099 --token SECRET --interval 30

Runs until Ctrl-C. Stdlib + Termux:API only — no pip packages needed.
"""
from __future__ import annotations

import argparse
import json
import socket
import subprocess
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from bands import band_label, classify_signal, decode_earfcn

LAT_URL = "http://connectivitycheck.gstatic.com/generate_204"
BASE = Path(__file__).parent
DATA_DIR = BASE / "data"


def _run_json(cmd: list[str], timeout: int = 10):
    """Run a termux-api command and parse its JSON, or return None."""
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except (subprocess.SubprocessError, FileNotFoundError):
        return None
    out = (p.stdout or "").strip()
    if not out:
        return None
    try:
        return json.loads(out)
    except ValueError:
        return None


def _num(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        try:
            return int(float(v))
        except (TypeError, ValueError):
            return None


def _first(d: dict, *keys):
    """First present, non-empty value among keys (cellinfo field names vary)."""
    for k in keys:
        if k in d and d[k] not in (None, "", 2147483647):  # 2147483647 = 'unavailable'
            return d[k]
    return None


def bars_from_rsrp(rsrp):
    if rsrp is None:
        return ""
    for thr, b in ((-85, 5), (-95, 4), (-105, 3), (-115, 2)):
        if rsrp >= thr:
            return b
    return 1


def read_signal() -> dict:
    """Parse the registered LTE cell from termux-telephony-cellinfo, plus
    operator/network type from termux-telephony-deviceinfo. Best-effort:
    returns {} silently if Termux:API or the fields are unavailable."""
    s: dict = {}
    dev = _run_json(["termux-telephony-deviceinfo"]) or {}
    if isinstance(dev, dict):
        op = dev.get("network_operator_name") or dev.get("sim_operator_name")
        if op:
            s["operator"] = op
        nt = dev.get("data_network_type") or dev.get("network_type")
        if nt:
            s["network_type"] = str(nt).upper()

    cells = _run_json(["termux-telephony-cellinfo"])
    cell = None
    if isinstance(cells, list):
        lte = [c for c in cells if isinstance(c, dict) and str(c.get("type", "")).lower() == "lte"]
        cell = next((c for c in lte if c.get("registered")), None) or (lte[0] if lte else None)
    if not isinstance(cell, dict):
        return s

    rsrp = _num(_first(cell, "rsrp", "dbm"))
    rsrq = _num(_first(cell, "rsrq"))
    sinr = _num(_first(cell, "rssnr", "sinr", "rssi_snr"))  # absent on some skins (e.g. MIUI)
    earfcn = _num(_first(cell, "earfcn", "arfcn"))
    ci = _num(_first(cell, "ci", "cid", "cell_identity"))
    pci = _num(_first(cell, "pci", "physical_cell_id"))
    tac = _num(_first(cell, "tac"))
    mcc = _first(cell, "mcc")
    mnc = _first(cell, "mnc")

    s["sig_rsrp"] = "" if rsrp is None else rsrp
    s["sig_rsrq"] = "" if rsrq is None else rsrq
    s["sig_sinr"] = "" if sinr is None else sinr
    s["sig_quality"] = classify_signal(rsrp, sinr)
    s["signal_bars"] = bars_from_rsrp(rsrp)
    # Band: prefer the direct "bands" list (Termux gives e.g. [3]); fall back to
    # decoding an EARFCN on phones that report that instead.
    band_list = cell.get("bands") if isinstance(cell.get("bands"), list) else None
    if band_list:
        bn = _num(band_list[0])
        label, low = band_label(bn)
        s["sig_band"] = label
        s["sig_earfcn"] = ""
    else:
        earfcn2, _, label, low = decode_earfcn(earfcn)
        s["sig_band"] = label
        s["sig_earfcn"] = "" if earfcn2 is None else earfcn2
    s["sig_band_low"] = "" if low is None else ("1" if low else "0")
    if ci is not None:
        s["sig_cell_id"] = ci
        if ci > 255:
            s["sig_enbid"] = ci >> 8  # ECI = eNB<<8 | sector
    if pci is not None:
        s["sig_pci"] = pci
    if tac is not None:
        s["sig_tac_lac"] = tac
    if mcc and mnc:
        s["operator_numeric"] = f"{mcc}{int(mnc):02d}" if str(mnc).isdigit() else f"{mcc}{mnc}"
    return s


def http_check(timeout_s: int = 8) -> dict:
    r: dict = {}
    t0 = time.monotonic()
    try:
        req = urllib.request.Request(LAT_URL, method="GET")
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            r["http_code"] = resp.status
            r["http_ms"] = round((time.monotonic() - t0) * 1000, 1)
            r["http_ok"] = resp.status in (200, 204, 301, 302)
    except Exception as e:  # noqa: BLE001 - any failure means link down
        r["http_ok"] = False
        r["http_error"] = str(e)[:120]
    return r


# Durable outbox: every sample is appended here first (this is also the full
# local backup of the whole run). A byte cursor remembers how far the collector
# has acked, so after any gap — PC off for a day, phone reboot — the next flush
# re-sends exactly what was missed, in batches, and nothing is lost.
OUTBOX = DATA_DIR / "outbox.jsonl"
CURSOR = DATA_DIR / "outbox.cursor"
BATCH = 400


def read_battery() -> dict:
    """Battery % / temperature / health via Termux:API (best-effort)."""
    d = _run_json(["termux-battery-status"]) or {}
    out = {}
    if isinstance(d, dict):
        if d.get("percentage") is not None:
            out["batt_pct"] = d["percentage"]
        t = d.get("temperature")
        if isinstance(t, (int, float)):
            out["batt_temp"] = round(float(t), 1)
        if d.get("health"):
            out["batt_health"] = d["health"]
        if d.get("plugged"):
            out["batt_plugged"] = d["plugged"]
    return out


def post(collector: str, token: str, payload, timeout_s: int = 20):
    """POST a dict (one sample) or a list (batch) to /ingest.
    Returns (ok, response_dict). response_dict may carry a remote 'stop' flag."""
    data = json.dumps(payload).encode()
    req = urllib.request.Request(collector.rstrip("/") + "/ingest", data=data,
                                 method="POST",
                                 headers={"Content-Type": "application/json",
                                          "X-Token": token})
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            if resp.status != 200:
                return False, None
            try:
                return True, json.loads(resp.read() or b"{}")
            except ValueError:
                return True, {}
    except Exception:  # noqa: BLE001
        return False, None


def append_outbox(sample: dict):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with OUTBOX.open("a") as f:
        f.write(json.dumps(sample, ensure_ascii=False) + "\n")
        f.flush()


def _read_cursor() -> int:
    try:
        return int(CURSOR.read_text().strip())
    except (OSError, ValueError):
        return 0


def flush_outbox(collector: str, token: str):
    """Send everything after the cursor, in batches, advancing the (persisted)
    byte cursor on each ack. Returns (sent, caught_up, backlog_bytes)."""
    try:
        size = OUTBOX.stat().st_size
    except OSError:
        return 0, True, 0
    off = _read_cursor()
    if off > size:          # file rotated/truncated -> restart from top
        off = 0
    sent = 0
    stop = False
    with OUTBOX.open("rb") as f:
        f.seek(off)
        while True:
            batch, consumed = [], 0
            while len(batch) < BATCH:
                line = f.readline()
                if not line:
                    break
                if not line.endswith(b"\n"):   # partial trailing write: stop here
                    break
                consumed += len(line)
                try:
                    batch.append(json.loads(line))
                except ValueError:
                    pass  # skip a corrupt line but still advance past it
            if not batch and consumed == 0:
                break
            if batch:
                ok, resp = post(collector, token, batch)
                if not ok:
                    break      # leave cursor put; retry next cycle
                if isinstance(resp, dict) and resp.get("stop"):
                    stop = True
            off += consumed
            try:
                CURSOR.write_text(str(off))
            except OSError:
                pass
            sent += len(batch)
            if consumed == 0 or f.tell() >= size:
                break
    return sent, off >= size, max(0, size - off), stop


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0],
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--collector", required=True,
                    help="base URL of collector.py, e.g. http://100.x.y.z:8099")
    ap.add_argument("--token", default="", help="shared secret (must match collector)")
    ap.add_argument("--interval", type=int, default=30, help="seconds between samples")
    ap.add_argument("--name", default=socket.gethostname() or "phone",
                    help="label for this sensor shown on the dashboard")
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--max-temp", type=float, default=44.0,
                    help="°C batteria oltre cui fermarsi per sicurezza (default 44)")
    ap.add_argument("--temp-strikes", type=int, default=3,
                    help="letture consecutive sopra soglia prima di fermarsi (default 3)")
    args = ap.parse_args()

    print(f"phone_agent -> {args.collector} | ogni {args.interval}s | name={args.name}")
    if not read_signal():
        print("ATTENZIONE: nessun dato segnale da Termux:API. Controlla di aver installato")
        print("  'termux-api' (pkg install termux-api) e l'app Termux:API, e dato i permessi")
        print("  Telefono/Posizione. Si continua comunque loggando solo la connettivita'.")

    print(f"outbox: {OUTBOX}  (backlog non consegnato: {max(0, OUTBOX.stat().st_size - _read_cursor()) if OUTBOX.exists() else 0} byte)")
    print(f"sicurezza: stop se batteria ≥ {args.max_temp}°C per {args.temp_strikes} letture; "
          f"stop remoto via collector.")

    over = 0
    while True:
        sample = {"timestamp": datetime.now(timezone.utc).isoformat(), "source": args.name,
                  "sim_status": "ready"}
        sample.update(read_signal())
        sample.update(http_check())
        sample.update(read_battery())
        sample["connection_status"] = "connected" if sample.get("http_ok") else "disconnected"
        append_outbox(sample)

        # Deliver this sample plus anything missed since last time (PC off,
        # reboot, ...): the file + byte cursor make it survive restarts.
        sent, caught_up, backlog, stop_remote = flush_outbox(args.collector, args.token)

        temp = sample.get("batt_temp")
        over = over + 1 if isinstance(temp, (int, float)) and temp >= args.max_temp else 0

        q = sample.get("sig_quality") or "?"
        band = sample.get("sig_band") or "?"
        rsrp = sample.get("sig_rsrp", "?")
        http = sample.get("http_code") or ("FAIL" if not sample.get("http_ok") else "?")
        uplink = ("ok" + (f" (recuperati {sent})" if sent > 1 else "")) if caught_up \
            else f"NO (arretrato {backlog // 1024} KB)"
        tstr = f" | {temp}°C" if isinstance(temp, (int, float)) else ""
        print(f"[{datetime.now().strftime('%H:%M:%S')}] {band} RSRP {rsrp} ({q}) "
              f"| http {http} | uplink {uplink}{tstr}")

        reason = None
        if stop_remote:
            reason = "stop remoto"
        elif over >= args.temp_strikes:
            reason = f"temperatura batteria {temp}°C ≥ {args.max_temp}"
        if reason:
            final = {"timestamp": datetime.now(timezone.utc).isoformat(), "source": args.name,
                     "agent_status": "stopped", "stop_reason": reason}
            final.update(read_battery())
            append_outbox(final)
            flush_outbox(args.collector, args.token)   # prova a consegnare l'avviso
            print(f">> STOP: {reason}. Agente terminato.")
            return 0

        if args.once:
            return 0
        time.sleep(max(5, args.interval))


if __name__ == "__main__":
    sys.exit(main())

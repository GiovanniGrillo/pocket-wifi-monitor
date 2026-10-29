#!/usr/bin/env python3
"""Home-PC receiver for the remote phone sensor.

Runs on your computer at home. The phone in the mountains (phone_agent.py)
POSTs one JSON sample per cycle to this server; here each sample is appended to
data/remote_YYYYMMDD.csv + .jsonl and kept as "latest" so the existing
dashboard shows the mountain signal live in your browser.

Reachability: the phone is behind mobile CGNAT and this PC behind a home
router, so neither is directly reachable. Run **Tailscale** on both and point
the phone at this PC's Tailscale IP — it traverses NAT/CGNAT with no port
forwarding. Then:

    python3 collector.py --token SECRET            # listens on 0.0.0.0:8099
    # open http://127.0.0.1:8099  (or the Tailscale IP from another device)

A shared --token must match the phone agent's, so only your phone can post.
"""
import argparse
import csv
import glob
import json
import shutil
import statistics
import subprocess
import sys
import threading
import time
import urllib.request
from datetime import datetime, timedelta, timezone
from functools import partial
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

BASE = Path(__file__).parent
DATA_DIR = BASE / "data"
HTML = BASE / "dashboard.html"
STATS_HTML = BASE / "stats.html"
EXPLORE_HTML = BASE / "explore.html"

QUALITY_ORDER = ["very_poor", "poor", "fair", "good", "excellent"]

_lock = threading.Lock()
_latest = {"ts": None, "fields": {}, "source": ""}

# Push notifications (optional): POST the message body to NOTIFY_URL, e.g. an
# ntfy.sh topic (https://ntfy.sh/your-topic). Fired on sensor offline, overheat/
# stop and SIM loss. Keeps the 30-day unattended run from failing silently.
NOTIFY_URL = ""
OFFLINE_MIN = 60
_notify_state = {"offline": False, "sim_ready": True}


def _notify(title: str, msg: str):
    if not NOTIFY_URL:
        return
    def _send():
        try:
            req = urllib.request.Request(
                NOTIFY_URL, data=msg.encode("utf-8"), method="POST",
                headers={"Title": title, "Priority": "high", "Tags": "warning"})
            urllib.request.urlopen(req, timeout=10)
        except Exception:  # noqa: BLE001
            pass
    threading.Thread(target=_send, daemon=True).start()


def _check_alerts(items):
    """Fire notifications from freshly ingested samples (best-effort)."""
    if not NOTIFY_URL:
        return
    for it in items:
        src = it.get("source", "phone")
        st = it.get("agent_status")
        if st == "thermal_shutdown":
            _notify("🌡️ Sensore: SURRISCALDAMENTO",
                    f"{src}: batteria {it.get('batt_temp','?')}°C — agente fermato e messo a raffreddare.")
        elif st == "stopped":
            _notify("⏹️ Sensore fermato", f"{src}: stop remoto.")
        sim = it.get("sim_status")
        if sim is not None:
            ready = sim in ("ready", "valid")
            if _notify_state["sim_ready"] and not ready:
                _notify("📵 SIM persa", f"{src}: sim_status={sim}")
            _notify_state["sim_ready"] = ready
    if _notify_state["offline"]:
        _notify("✅ Sensore di nuovo online", "I dati stanno di nuovo arrivando.")
        _notify_state["offline"] = False


def _offline_monitor():
    """Background: warn once when no sample has arrived for OFFLINE_MIN minutes."""
    while True:
        time.sleep(60)
        with _lock:
            ts = _latest["ts"]
        if ts is None:
            continue
        age = (datetime.now(timezone.utc) - ts).total_seconds()
        if age > OFFLINE_MIN * 60 and not _notify_state["offline"]:
            _notify("⚠️ Sensore OFFLINE", f"Nessun dato da ~{int(age/60)} minuti.")
            _notify_state["offline"] = True


def _auto_stress_scheduler(hours, budget_mb):
    """Background: at the top of each configured LOCAL hour, queue one stress
    test (the phone runs it at its next poll). Once per hour per day."""
    done = set()
    while True:
        now = datetime.now()
        key = (now.date().isoformat(), now.hour)
        if now.hour in hours and key not in done:
            with _lock:
                if not _control.get("stress"):
                    _control["stress"] = {"id": int(datetime.now(timezone.utc).timestamp()),
                                          "budget_mb": budget_mb, "streams": 3, "secs": 15}
                    _save_control()
            done.add(key)
            if len(done) > 100:
                done = set(sorted(done)[-50:])
        time.sleep(60)


CONTROL_FILE = DATA_DIR / "control.json"
# Remote control the phone reads on each POST: a kill-switch and an optional
# one-shot stress-test command. `stress` is null, or {id, budget_mb, streams, secs}.
_control = {"stop": False, "stress": None}
_last_stress = {}  # most recent stress-test result reported by the phone


def _load_control():
    try:
        _control.update(json.loads(CONTROL_FILE.read_text()))
    except (OSError, ValueError):
        pass


def _save_control():
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        CONTROL_FILE.write_text(json.dumps(_control))
    except OSError:
        pass


def _append(fields: dict):
    """Persist one sample. JSONL keeps the full schema; CSV uses the column
    order fixed by the first sample of the day (extra keys still go to JSONL)."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    day = datetime.now(timezone.utc).strftime("%Y%m%d")
    jsonl = DATA_DIR / f"remote_{day}.jsonl"
    csv_path = DATA_DIR / f"remote_{day}.csv"
    with jsonl.open("a") as f:
        f.write(json.dumps(fields, ensure_ascii=False) + "\n")
    new = not csv_path.exists()
    if new:
        columns = ["timestamp"] + [k for k in fields if k != "timestamp"]
        csv_path.with_suffix(".columns").write_text(json.dumps(columns))
    else:
        try:
            columns = json.loads(csv_path.with_suffix(".columns").read_text())
        except OSError:
            columns = ["timestamp"] + [k for k in fields if k != "timestamp"]
    with csv_path.open("a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=columns, extrasaction="ignore")
        if new:
            w.writeheader()
        w.writerow(fields)


def _parse_ts(s):
    try:
        dt = datetime.fromisoformat(str(s))
    except (ValueError, TypeError):
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _load_samples(days: int):
    """All stored samples within the last `days`, oldest first, de-duplicated
    by (source, timestamp) so the phone's catch-up re-sends never double-count."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    out = []
    seen = set()
    for path in sorted(glob.glob(str(DATA_DIR / "remote_*.jsonl"))):
        try:
            with open(path) as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        d = json.loads(line)
                    except ValueError:
                        continue
                    dt = _parse_ts(d.get("timestamp"))
                    if dt is None or dt < cutoff:
                        continue
                    key = (d.get("source"), d.get("timestamp"))
                    if key in seen:
                        continue
                    seen.add(key)
                    out.append((dt, d))
        except OSError:
            continue
    out.sort(key=lambda x: x[0])
    return out


def _seed_latest():
    """After a restart, remember the last stored sample so the dashboard can
    say how long ago the phone was last heard from instead of showing '—'."""
    files = sorted(glob.glob(str(DATA_DIR / "remote_*.jsonl")))
    for path in reversed(files):
        try:
            with open(path) as f:
                lines = [ln for ln in f if ln.strip()]
        except OSError:
            continue
        for line in reversed(lines):
            try:
                d = json.loads(line)
            except ValueError:
                continue
            ts = _parse_ts(d.get("timestamp"))
            if ts is None:
                continue
            _latest["ts"], _latest["fields"] = ts, d
            _latest["source"] = str(d.get("source", "phone"))
            return


def _recent(n: int, days: int = 2) -> list:
    """The last `n` stored samples, newest first, compacted for the UI table."""
    rows = []
    for _dt, d in _load_samples(days):
        rows.append({
            "t": d.get("timestamp"),
            "operator": d.get("operator"),
            "net": d.get("network_type"),
            "band": d.get("sig_band"),
            "rsrp": d.get("sig_rsrp"),
            "rsrq": d.get("sig_rsrq"),
            "sinr": d.get("sig_sinr"),
            "http": d.get("http_code"),
            "http_ms": d.get("http_ms"),
            "http_ok": d.get("http_ok"),
            "batt": d.get("batt_pct"),
            "temp": d.get("batt_temp"),
        })
    rows.reverse()
    return rows[:n]


def _conclusions(h: dict) -> list:
    """Plain-language takeaways from the aggregates (rule-based, Italian)."""
    c = []
    n = h["n"]
    if n == 0:
        return ["Ancora nessun dato: appena il telefono inizia a inviare, qui compaiono le statistiche."]
    med = h["rsrp"].get("median")
    if med is not None:
        if med >= -90:
            c.append(f"Segnale mediano RSRP {med} dBm: **buono**, presa solida.")
        elif med >= -100:
            c.append(f"Segnale mediano RSRP {med} dBm: **discreto**, utilizzabile.")
        elif med >= -110:
            c.append(f"Segnale mediano RSRP {med} dBm: **scarso**, al limite — rischioso per usi pesanti.")
        else:
            c.append(f"Segnale mediano RSRP {med} dBm: **molto scarso**, presa critica.")
    okq = h.get("pct_ok_quality")
    if okq is not None:
        c.append(f"Qualità ≥ discreta per il **{okq}%** del tempo → "
                 + ("stabile." if okq >= 80 else "ballerina, con cali frequenti." if okq < 50 else "accettabile ma con cali."))
    up = h.get("uplink_ok_pct")
    if up is not None:
        c.append(f"Dato realmente funzionante (http 204) nel **{up}%** dei campioni"
                 + (" → link affidabile." if up >= 95 else " → ci sono buchi di connettività."))
    if h.get("band_main"):
        low = h.get("low_band_pct", 0)
        c.append(f"Banda prevalente **{h['band_main']}**"
                 + (f"; banda bassa (sub-GHz, buona portata) nel {low}% del tempo." if low else
                    "; nessuna banda bassa (sub-GHz) → portata/penetrazione muri limitata."))
    if h.get("trend"):
        c.append(h["trend"])
    c.append("Per sapere quanta banda regge (es. 2 Netflix + domotica) fai un `stresstest.py` "
             "via tethering: queste statistiche misurano la presa, non la velocità sotto carico.")
    return c


def _history(days: int) -> dict:
    samples = _load_samples(days)
    h = {"window_days": days, "n": len(samples)}
    if not samples:
        h.update({"rsrp": {}, "quality_pct": {}, "series": [], "conclusions": _conclusions(h)})
        return h

    first, last = samples[0][0], samples[-1][0]
    span_h = (last - first).total_seconds() / 3600
    h["first"], h["last"] = first.isoformat(), last.isoformat()
    h["span_hours"] = round(span_h, 1)
    h["days_covered"] = round(span_h / 24, 1)

    rsrps = [int(d["sig_rsrp"]) for _, d in samples if str(d.get("sig_rsrp", "")).lstrip("-").isdigit()]
    if rsrps:
        h["rsrp"] = {"min": min(rsrps), "max": max(rsrps),
                     "median": round(statistics.median(rsrps)),
                     "mean": round(statistics.mean(rsrps))}
    else:
        h["rsrp"] = {}

    qcount = {q: 0 for q in QUALITY_ORDER}
    for _, d in samples:
        q = d.get("sig_quality")
        if q in qcount:
            qcount[q] += 1
    qtot = sum(qcount.values()) or 1
    h["quality_counts"] = qcount
    h["quality_pct"] = {q: round(100 * qcount[q] / qtot) for q in QUALITY_ORDER}
    h["pct_ok_quality"] = round(100 * (qcount["fair"] + qcount["good"] + qcount["excellent"]) / qtot)

    oks = sum(1 for _, d in samples if d.get("http_ok") is True)
    h["uplink_ok_pct"] = round(100 * oks / len(samples))
    httpms = [float(d["http_ms"]) for _, d in samples
              if isinstance(d.get("http_ms"), (int, float)) and d.get("http_ok") is True]
    h["http_ms_median"] = round(statistics.median(httpms), 1) if httpms else None

    bands = {}
    low_n = 0
    for _, d in samples:
        b = d.get("sig_band")
        if b:
            bands[b] = bands.get(b, 0) + 1
        if str(d.get("sig_band_low")) == "1":
            low_n += 1
    h["bands"] = bands
    h["band_main"] = max(bands, key=bands.get) if bands else ""
    h["low_band_pct"] = round(100 * low_n / len(samples))
    ops = sorted({d.get("operator") for _, d in samples if d.get("operator")})
    h["operators"] = ops
    net_types = {}
    for _, d in samples:
        nt = d.get("network_type")
        if nt:
            net_types[nt] = net_types.get(nt, 0) + 1
    h["net_types"] = net_types
    h["seen_5g"] = any("NR" in k.upper() or "5G" in k.upper() for k in net_types)

    # Time series, bucketed by hour (<=3 days) or by day (longer), for charting.
    bucket = "hour" if span_h <= 72 else "day"
    buckets = {}
    for dt, d in samples:
        key = dt.replace(minute=0, second=0, microsecond=0) if bucket == "hour" \
            else dt.replace(hour=0, minute=0, second=0, microsecond=0)
        buckets.setdefault(key, []).append(d)
    series = []
    for key in sorted(buckets):
        grp = buckets[key]
        rs = [int(x["sig_rsrp"]) for x in grp if str(x.get("sig_rsrp", "")).lstrip("-").isdigit()]
        ok = sum(1 for x in grp if x.get("http_ok") is True)
        series.append({
            "t": key.isoformat(), "n": len(grp),
            "rsrp_median": round(statistics.median(rs)) if rs else None,
            "ok_rate": round(100 * ok / len(grp)),
        })
    h["bucket"] = bucket
    h["series"] = series

    # Simple trend: median RSRP of first third vs last third of the window.
    if len(rsrps) >= 12:
        k = len(rsrps) // 3
        early = statistics.median(rsrps[:k])
        late = statistics.median(rsrps[-k:])
        diff = round(late - early)
        # Structured trend so the UI can localise it; the Italian sentence
        # below stays only for the server-side _conclusions list.
        h["trend_db"] = abs(diff)
        if abs(diff) >= 3:
            h["trend_dir"] = "up" if diff > 0 else "down"
            h["trend"] = (f"Andamento: il segnale è {'migliorato' if diff > 0 else 'peggiorato'} "
                          f"di ~{abs(diff)} dB dall'inizio del periodo.")
        else:
            h["trend_dir"] = "flat"
            h["trend"] = "Andamento: segnale sostanzialmente stabile nel periodo."

    # Breakdown by hour-of-day (LOCAL time) — reveals peak-hour congestion,
    # e.g. whether evenings (streaming time) are the weak spot.
    by_hour = {h24: {"rsrp": [], "okq": 0, "up": 0, "n": 0} for h24 in range(24)}
    for dt, d in samples:
        b = by_hour[dt.astimezone().hour]
        b["n"] += 1
        if str(d.get("sig_rsrp", "")).lstrip("-").isdigit():
            b["rsrp"].append(int(d["sig_rsrp"]))
        if d.get("sig_quality") in ("fair", "good", "excellent"):
            b["okq"] += 1
        if d.get("http_ok") is True:
            b["up"] += 1
    h["by_hour"] = [{
        "hour": hh, "n": b["n"],
        "rsrp_median": round(statistics.median(b["rsrp"])) if b["rsrp"] else None,
        "ok_quality_pct": round(100 * b["okq"] / b["n"]) if b["n"] else None,
        "uplink_ok_pct": round(100 * b["up"] / b["n"]) if b["n"] else None,
    } for hh, b in sorted(by_hour.items())]

    # Per-day aggregates (LOCAL date), each with its own 24h quality profile —
    # powers the scrollable "day by day" view on the stats page.
    bd = {}
    for dt, d in samples:
        loc = dt.astimezone()
        day = loc.date().isoformat()
        e = bd.setdefault(day, {"rsrp": [], "okq": 0, "up": 0, "n": 0, "hours": {}})
        e["n"] += 1
        if str(d.get("sig_rsrp", "")).lstrip("-").isdigit():
            e["rsrp"].append(int(d["sig_rsrp"]))
        q_ok = d.get("sig_quality") in ("fair", "good", "excellent")
        if q_ok:
            e["okq"] += 1
        if d.get("http_ok") is True:
            e["up"] += 1
        he = e["hours"].setdefault(loc.hour, {"okq": 0, "n": 0})
        he["n"] += 1
        if q_ok:
            he["okq"] += 1
    h["by_day"] = [{
        "date": day,
        "n": e["n"],
        "rsrp_median": round(statistics.median(e["rsrp"])) if e["rsrp"] else None,
        "ok_quality_pct": round(100 * e["okq"] / e["n"]) if e["n"] else None,
        "uplink_ok_pct": round(100 * e["up"] / e["n"]) if e["n"] else None,
        "hours": [round(100 * e["hours"][hh]["okq"] / e["hours"][hh]["n"]) if hh in e["hours"] and e["hours"][hh]["n"] else None
                  for hh in range(24)],
    } for day, e in sorted(bd.items())]

    # Rough data-usage estimate for the window: stress tests (the big, known
    # cost) + a small per-sample overhead for the continuous monitoring.
    stress_mb = sum(float(d["data_mb"]) for _, d in samples
                    if d.get("kind") == "stress_result" and isinstance(d.get("data_mb"), (int, float)))
    agent_mb = len(samples) * 0.003   # ~3 KB per cycle (POST + 204 check), estimate
    h["data_stress_mb"] = round(stress_mb, 1)
    h["data_used_mb"] = round(stress_mb + agent_mb, 1)

    h["conclusions"] = _conclusions(h)
    h["last_stress"] = dict(_last_stress)
    return h


class Handler(BaseHTTPRequestHandler):
    token = ""
    stale_s = 120

    def log_message(self, *a):
        pass

    def _send(self, code, body: bytes, ctype: str):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _authorized(self):
        if not self.token:
            return True
        if self.headers.get("X-Token") == self.token:
            return True
        # the PC's own browser (localhost) may control without the token
        return bool(self.client_address) and self.client_address[0] in ("127.0.0.1", "::1")

    def do_POST(self):
        global _last_stress
        if self.path == "/control":
            if not self._authorized():
                self._send(403, b"bad token", "text/plain")
                return
            try:
                n = int(self.headers.get("Content-Length", 0))
                body = json.loads(self.rfile.read(n) or b"{}")
            except (ValueError, TypeError):
                self._send(400, b"bad json", "text/plain")
                return
            with _lock:
                if isinstance(body, dict) and "stop" in body:
                    _control["stop"] = bool(body["stop"])
                if isinstance(body, dict) and "stress" in body:
                    req = body["stress"]
                    if isinstance(req, dict):
                        _control["stress"] = {
                            "id": int(datetime.now(timezone.utc).timestamp()),
                            "budget_mb": max(1, min(int(req.get("budget_mb", 50)), 1000)),
                            "streams": max(1, min(int(req.get("streams", 3)), 16)),
                            "secs": max(5, min(int(req.get("secs", 15)), 120)),
                        }
                    else:
                        _control["stress"] = None  # cancel a pending test
                _save_control()
                state = dict(_control)
                state["last_stress"] = _last_stress
            self._send(200, json.dumps(state).encode(), "application/json")
            return
        if self.path != "/ingest":
            self._send(404, b"not found", "text/plain")
            return
        if not self._authorized():
            self._send(403, b"bad token", "text/plain")
            return
        try:
            n = int(self.headers.get("Content-Length", 0))
            payload = json.loads(self.rfile.read(n) or b"{}")
        except (ValueError, TypeError):
            self._send(400, b"bad json", "text/plain")
            return
        # Accept one sample (dict) or a batch (list of dicts, oldest first).
        items = payload if isinstance(payload, list) else [payload]
        items = [x for x in items if isinstance(x, dict)]
        if not items:
            self._send(400, b"want object or array", "text/plain")
            return
        with _lock:
            try:
                for it in items:
                    it.setdefault("timestamp", datetime.now(timezone.utc).isoformat())
                    _append(it)
                    if it.get("kind") == "stress_result":
                        _last_stress = it
                        pend = _control.get("stress")
                        if pend and it.get("stress_id") == pend.get("id"):
                            _control["stress"] = None  # one-shot: command fulfilled
                            _save_control()
            except OSError as e:
                self._send(500, f"store error: {e}".encode(), "text/plain")
                return
            last = items[-1]
            _latest["ts"] = _parse_ts(last.get("timestamp")) or datetime.now(timezone.utc)
            _latest["fields"] = last
            _latest["source"] = str(last.get("source", "phone"))
            resp = {"ok": True, "n": len(items), "stop": bool(_control.get("stop")),
                    "stress": _control.get("stress")}
        _check_alerts(items)
        self._send(200, json.dumps(resp).encode(), "application/json")

    def do_GET(self):
        route = urlparse(self.path)
        path = route.path
        if path in ("/", "/index.html"):
            try:
                self._send(200, HTML.read_bytes(), "text/html; charset=utf-8")
            except OSError:
                self._send(500, b"dashboard.html missing", "text/plain")
            return
        if path == "/stats":
            try:
                self._send(200, STATS_HTML.read_bytes(), "text/html; charset=utf-8")
            except OSError:
                self._send(500, b"stats.html missing", "text/plain")
            return
        if path == "/explore":
            try:
                self._send(200, EXPLORE_HTML.read_bytes(), "text/html; charset=utf-8")
            except OSError:
                self._send(500, b"explore.html missing", "text/plain")
            return
        if path == "/history":
            if not self._authorized():
                self._send(403, b"bad token", "text/plain")
                return
            try:
                days = int(parse_qs(route.query).get("days", ["30"])[0])
            except ValueError:
                days = 30
            days = max(1, min(days, 365))
            with _lock:
                data = _history(days)
            self._send(200, json.dumps(data, ensure_ascii=False).encode(),
                       "application/json; charset=utf-8")
            return
        if path == "/recent":
            if not self._authorized():
                self._send(403, b"bad token", "text/plain")
                return
            q = parse_qs(route.query)
            try:
                n = int(q.get("n", ["200"])[0])
            except ValueError:
                n = 200
            n = max(1, min(n, 2000))
            try:
                days = int(q.get("days", ["2"])[0])
            except ValueError:
                days = 2
            days = max(1, min(days, 365))
            with _lock:
                rows = _recent(n, days)
            self._send(200, json.dumps(rows, ensure_ascii=False).encode(),
                       "application/json; charset=utf-8")
            return
        if path == "/control":
            if not self._authorized():
                self._send(403, b"bad token", "text/plain")
                return
            with _lock:
                state = dict(_control)
                state["last_stress"] = _last_stress
            self._send(200, json.dumps(state).encode(), "application/json")
            return
        if self.path == "/data":
            if not self._authorized():
                self._send(403, b"bad token", "text/plain")
                return
            with _lock:
                ts, fields, src = _latest["ts"], dict(_latest["fields"]), _latest["source"]
            out = {"timestamp": datetime.now(timezone.utc).isoformat(), "reachable": False}
            if ts is not None:
                age = (datetime.now(timezone.utc) - ts).total_seconds()
                out["reachable"] = age <= self.stale_s
                out["age_s"] = round(age)
                out["label"] = f"{src} · remoto" + ("" if out["reachable"] else " (fermo)")
                out["fields"] = fields
                out["errors"] = {}
            self._send(200, json.dumps(out, ensure_ascii=False).encode(),
                       "application/json; charset=utf-8")
            return
        self._send(404, b"not found", "text/plain")


def _tailscale_reminder():
    """Remind to turn Tailscale on. Tailscale is kept off by default on this PC
    (no 24/7 background use); without it the phone can't deliver from outside."""
    if not shutil.which("tailscale"):
        return
    up = False
    ip = ""
    try:
        r = subprocess.run(["tailscale", "status"], capture_output=True,
                           text=True, timeout=5)
        up = r.returncode == 0
        if up:
            ipr = subprocess.run(["tailscale", "ip", "-4"], capture_output=True,
                                text=True, timeout=5)
            ip = ipr.stdout.strip().splitlines()[0] if ipr.stdout.strip() else ""
    except (subprocess.SubprocessError, OSError):
        return
    print("-" * 60)
    if up:
        print(f"✓ Tailscale ATTIVO" + (f"  (IP: {ip})" if ip else ""))
        print(f"  Il telefono ti raggiunge da fuori su http://{ip or '<IP>'}:{{porta}}")
    else:
        print("⚠  Tailscale è SPENTO — il telefono NON potrà mandarti i dati da fuori.")
        print("   Accendilo prima:")
        print("     sudo systemctl start tailscaled   (poi, se serve: sudo tailscale up)")
        print("   In LAN/localhost il collector funziona comunque.")
    print("-" * 60)


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--port", type=int, default=8099)
    ap.add_argument("--bind", default="0.0.0.0",
                    help="0.0.0.0 so the phone can reach it over Tailscale/LAN")
    ap.add_argument("--token", default="", help="shared secret the phone must send (X-Token)")
    ap.add_argument("--stale", type=int, default=120,
                    help="seconds without a sample before the phone is shown as 'fermo'")
    ap.add_argument("--notify-url", default="",
                    help="URL per notifiche push (es. https://ntfy.sh/tuo-topic-segreto)")
    ap.add_argument("--offline-min", type=int, default=60,
                    help="minuti senza dati prima dell'avviso OFFLINE (default 60)")
    ap.add_argument("--auto-stress", default="",
                    help="ore locali per stress test automatici, es. '13,21' (vuoto = off)")
    ap.add_argument("--auto-stress-mb", type=int, default=50,
                    help="budget MB per ogni stress test automatico (default 50)")
    args = ap.parse_args()

    if args.bind not in ("127.0.0.1", "::1", "localhost") and not args.token:
        ap.error("--token è obbligatorio quando --bind espone il collector alla rete")

    global NOTIFY_URL, OFFLINE_MIN
    NOTIFY_URL = args.notify_url
    OFFLINE_MIN = args.offline_min

    _load_control()
    if _control.get("stop"):
        print("NB: flag STOP attivo → il sensore si fermerà/resterà fermo. "
              "Per riattivarlo: dashboard 'Riprendi' o POST /control {\"stop\":false}.")
    _tailscale_reminder()

    Handler.token = args.token
    Handler.stale_s = args.stale
    server = ThreadingHTTPServer((args.bind, args.port), Handler)
    print(f"Collector in ascolto su {args.bind}:{args.port}  "
          f"(token {'ON' if args.token else 'OFF'})")
    print(f"Dashboard live:  http://127.0.0.1:{args.port}")
    print(f"Statistiche:     http://127.0.0.1:{args.port}/stats")
    print(f"(da remoto, via Tailscale: http://<tuo-IP-tailscale>:{args.port})")
    if NOTIFY_URL:
        threading.Thread(target=_offline_monitor, daemon=True).start()
        print(f"Notifiche: ON (offline > {OFFLINE_MIN} min, surriscaldamento, SIM persa)")
    if args.auto_stress:
        hrs = [int(x) for x in args.auto_stress.split(",") if x.strip().isdigit()]
        if hrs:
            threading.Thread(target=_auto_stress_scheduler, args=(hrs, args.auto_stress_mb), daemon=True).start()
            print(f"Stress test automatici: ore {hrs} ({args.auto_stress_mb} MB l'uno)")
    print("Ctrl-C per fermare.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nchiuso.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

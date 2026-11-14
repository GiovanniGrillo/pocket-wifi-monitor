# Usage

[Back to the README](../README.md)

## Running the collector

`collector/start.sh` reads `POCKET_WIFI_TOKEN` and starts the collector on port
8099. To run it by hand:

```bash
python3 collector/collector.py --bind 0.0.0.0 --port 8099 --token "$POCKET_WIFI_TOKEN"
```

The collector refuses to listen on a non-local address without `--token`. It
speaks plain HTTP and relies on Tailscale's WireGuard encryption in transit, so
never expose it on a public interface without a TLS reverse proxy. For defence
in depth, bind it to the Tailscale address only:

```bash
python3 collector/collector.py --bind "$(tailscale ip -4 | head -1)" --token "$POCKET_WIFI_TOKEN"
```

Find the PC's Tailscale address with `tailscale ip -4`.

## API

All endpoints need the token in the `X-Token` header (requests from the PC
itself are allowed without it).

| Endpoint | Method | What it does |
| --- | --- | --- |
| `/ingest` | POST | one sample (object) or a batch (array, oldest first) |
| `/data` | GET | the latest sample and how old it is |
| `/history?days=N` | GET | aggregates, series and verdict inputs for the last N days |
| `/recent?n=&days=&meta=1` | GET | the last samples, compacted for the tables |
| `/control` | GET / POST | remote stop flag and stress-test command |

Pages: `/` (Overview), `/stats` (Analysis), `/explore` (Data).

## Remote control

From the dashboard, or with `POST /control`, even from outside the house:

- **Stop / resume the sensor.** The phone reads the flag on its next cycle and
  exits cleanly. A stopped agent cannot be restarted remotely: clear the flag
  with *Resume sensor*, then start `run_agent.sh` again in Termux.
- **On-demand stress test.** Multi-stream download plus latency under load,
  with a data budget chosen at the time. The result comes back on the
  dashboard.

## Thermal safety

The agent watches the battery temperature in two stages:

- **Warning** (`--max-temp`, default 44 °C): keeps measuring and marks the
  sample as hot.
- **Emergency** (`--shutdown-temp`, default 46 °C, for a few readings in a
  row): raises an alert, releases the wake-lock so the phone can sleep and cool
  down and, only on a rooted device, powers it off.

A non-rooted Android cannot power itself off from an app. There the agent does
what it can (stop, sleep, alert) and the last line of defence is the phone's own
hardware protection.

## Optional tools

Signal over USB/ADB, without Termux:

```bash
python3 tools/adb_signal.py --loop --interval 5 --csv
```

Throughput and bufferbloat over USB tethering. The defaults can use up to about
300 MB; the `--budget-mb` cap is approximate to the size of each downloaded
chunk:

```bash
python3 tools/stresstest.py --budget-mb 100 --phase 20
```

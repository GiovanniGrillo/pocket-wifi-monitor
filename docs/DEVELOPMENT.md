# Development

[Back to the README](../README.md)

## Tests

```bash
python3 -m unittest discover -s tests
```

The tests use synthetic data only: band decoding, signal classification and the
collector's aggregation (malformed records, duplicate re-sends, event records).

## Sample format

Each sample is a JSON object. The collector needs only `timestamp` (ISO 8601
with offset) and `source`; it stores any other field as is, and the dashboard
uses the ones it knows:

```json
{
  "timestamp": "2026-01-01T12:00:00+00:00",
  "source": "phone",
  "sig_rsrp": -97,
  "sig_rsrq": -11,
  "sig_band": "B20 (800)",
  "http_ok": true,
  "http_ms": 84,
  "batt_pct": 88
}
```

Samples are de-duplicated on `(source, timestamp)`, so an agent can safely
re-send its outbox.

## Adding a sensor

Keep the JSON contract above and keep the transport separate from collection,
so the outbox keeps working offline. Never put real data in fixtures: use
synthetic carriers, cell IDs and timestamps.

## Private data

`collector/data/` is git-ignored and ships only a `.gitkeep`. Do not commit
tokens, Tailscale IPs, carriers or real logs in scripts, issues or commits.

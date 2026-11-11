import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "collector"))
import collector


class CollectorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old_dir = collector.DATA_DIR
        collector.DATA_DIR = Path(self.tmp.name)

    def tearDown(self):
        collector.DATA_DIR = self.old_dir
        self.tmp.cleanup()

    def write(self, records):
        day = datetime.now(timezone.utc).strftime("%Y%m%d")
        with open(collector.DATA_DIR / f"remote_{day}.jsonl", "w") as f:
            for r in records:
                f.write((r if isinstance(r, str) else json.dumps(r)) + "\n")

    def ts(self, minutes_ago):
        return (datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)).isoformat()

    def test_clean_coerces_and_drops_bad_fields(self):
        d = collector._clean({"sig_rsrp": "--90", "sig_rsrq": "-11", "source": ["x"],
                              "operator": {"a": 1}, "network_type": 5})
        self.assertNotIn("sig_rsrp", d)
        self.assertEqual(d["sig_rsrq"], -11)
        self.assertNotIn("source", d)
        self.assertNotIn("operator", d)
        self.assertEqual(d["network_type"], "5")

    def test_malformed_records_do_not_break_history(self):
        self.write([
            {"source": "p", "timestamp": self.ts(3), "sig_rsrp": -100, "http_ok": True},
            {"source": "p", "timestamp": self.ts(2), "sig_rsrp": "--90"},
            {"source": ["x"], "timestamp": self.ts(1), "sig_band": ["a"]},
            "[1, 2, 3]",
            "not json",
        ])
        h = collector._history(1)
        self.assertEqual(h["n"], 3)
        self.assertEqual(h["rsrp"]["median"], -100)
        self.assertEqual(len(collector._recent(10, 1)), 3)

    def test_events_are_not_counted_as_samples(self):
        self.write([
            {"source": "p", "timestamp": self.ts(3), "sig_rsrp": -95, "http_ok": True},
            {"source": "p", "timestamp": self.ts(2), "kind": "stress_result", "stress_id": 1},
            {"source": "p", "timestamp": self.ts(1), "agent_status": "stopped"},
        ])
        self.assertEqual(collector._history(1)["n"], 1)
        rows, total = collector._recent(10, 1, with_total=True)
        self.assertEqual((len(rows), total), (1, 1))

    def test_duplicate_resends_are_counted_once(self):
        t = self.ts(1)
        self.write([{"source": "p", "timestamp": t, "sig_rsrp": -95}] * 3)
        self.assertEqual(collector._history(1)["n"], 1)


if __name__ == "__main__":
    unittest.main()

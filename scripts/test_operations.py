"""Small deterministic checks for operational tooling (no running services)."""
import importlib.util
from pathlib import Path
import unittest

from benchmark import latency_report, percentile, sample_events

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("health_router", ROOT / "deploy/health_router.py")
router = importlib.util.module_from_spec(spec)
spec.loader.exec_module(router)


class OperationsTests(unittest.TestCase):
    def test_health_routing_uses_only_explicit_validated_addresses(self):
        config = router.upstream_config(["127.0.0.1:8002", "127.0.0.1:8001", "127.0.0.1:8001"])
        self.assertEqual(config.count("server 127.0.0.1:8001 "), 1)
        self.assertLess(config.index(":8001"), config.index(":8002"))
        with self.assertRaises(ValueError):
            router.upstream_config(["127.0.0.1; include /tmp/attack;"])
        with self.assertRaises(ValueError):
            router.upstream_config(["127.0.0.1:70000"])

    def test_no_ready_backends_fails_closed(self):
        config = router.upstream_config([])
        self.assertIn("server 127.0.0.1:65535 down;", config)
        self.assertNotIn("server backend", config)

    def test_percentiles_and_empty_samples(self):
        self.assertEqual(percentile([100, 0], 95), 95)
        self.assertEqual(percentile([5], 99), 5)
        self.assertIsNone(latency_report([])["p99_ms"])

    def test_workload_samples_have_unique_evidence(self):
        events = sample_events(100, "run-a")
        self.assertEqual(len({event["raw"] for event in events}), 100)
        self.assertTrue(all("run-a" in event["raw"] for event in events))
        self.assertTrue(any(event["raw"].startswith("LEEF:") for event in events))
        self.assertTrue(any(event["raw"].startswith("<event>") for event in events))


if __name__ == "__main__":
    unittest.main()

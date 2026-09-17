#!/usr/bin/env python3
"""Unit tests for scenario fault injection - no cluster required (kubectl is mocked)."""

import json
import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import fault_injection as fi


def sample_config():
    """A trimmed copy of the real demo.flagd.json shape."""
    return {
        "$schema": "https://flagd.dev/schema/v0/flags.json",
        "flags": {
            "postgresFailure": {"state": "ENABLED", "variants": {"on": True, "off": False}, "defaultVariant": "off"},
            "postgresSlow": {"state": "ENABLED", "variants": {"off": 0, "1sec": 1000, "3sec": 3000, "6sec": 6000}, "defaultVariant": "off"},
            "emailMemoryLeak": {"state": "ENABLED", "variants": {"off": 0, "1x": 1, "10x": 10, "100x": 100, "1000x": 1000, "10000x": 10000}, "defaultVariant": "off"},
            "cartFailure": {"state": "ENABLED", "variants": {"on": True, "off": False}, "defaultVariant": "off"},
            "loadGeneratorTraffic": {"state": "ENABLED", "variants": {"on": True, "off": False}, "defaultVariant": "on"},
        },
    }


class FakeCluster:
    """Stands in for kubectl: records calls and holds the ConfigMap state."""

    def __init__(self, config=None, forbid_patch=False):
        self.config = config if config is not None else sample_config()
        self.forbid_patch = forbid_patch
        self.calls = []

    def run_cmd(self, args, timeout=10):
        self.calls.append(args)
        if "get" in args and "cm" in args:
            # Mirror the real `kubectl get cm -o json` shape: the flag document is a
            # JSON *string* under .data["demo.flagd.json"], not a nested object.
            return 0, json.dumps({"data": {fi.CM_KEY: json.dumps(self.config)}}), ""
        if "patch" in args:
            if self.forbid_patch:
                return 1, "", 'configmaps "flagd-config" is forbidden: User cannot patch resource'
            self.config = json.loads(json.loads(args[args.index("-p") + 1])["data"][fi.CM_KEY])
            return 0, "configmap/flagd-config patched", ""
        if "rollout" in args:
            return 0, "deployment.apps/flagd restarted", ""
        return 0, "", ""

    def active(self):
        return {
            n: s["defaultVariant"]
            for n, s in self.config["flags"].items()
            if not n.startswith("loadGenerator") and s["defaultVariant"] not in ("off", 0, False)
        }


class TestScenarioMapping(unittest.TestCase):
    def test_every_workshop_scenario_is_mapped(self):
        """A scenario missing from the map would silently skip injection."""
        expected = {
            "badDeploy1405", "episodicRecurrence", "postgresFailure",
            "emailMemoryLeak", "postgresSlow", "poisonedEntity",
        }
        self.assertEqual(expected, set(fi.SCENARIO_FAULTS))

    def test_mutually_exclusive_faults_are_both_mapped(self):
        """postgresFailure fails fast and postgresSlow adds delay - they cannot coexist."""
        self.assertIsNotNone(fi.SCENARIO_FAULTS["postgresFailure"])
        self.assertIsNotNone(fi.SCENARIO_FAULTS["postgresSlow"])
        self.assertNotEqual(fi.SCENARIO_FAULTS["postgresFailure"], fi.SCENARIO_FAULTS["postgresSlow"])


class TestInjection(unittest.TestCase):
    def test_injects_only_the_requested_fault(self):
        fake = FakeCluster()
        with patch.object(fi, "run_cmd", fake.run_cmd):
            fi.inject_scenario("group-1", "postgresFailure", warmup_s=0)
        self.assertEqual({"postgresFailure": "on"}, fake.active())

    def test_previous_fault_is_cleared_before_the_next(self):
        """Without this, postgresFailure would mask postgresSlow on the following run."""
        fake = FakeCluster()
        with patch.object(fi, "run_cmd", fake.run_cmd):
            fi.inject_scenario("group-1", "postgresFailure", warmup_s=0)
            fi.inject_scenario("group-1", "postgresSlow", warmup_s=0)
        self.assertEqual({"postgresSlow": "3sec"}, fake.active())

    def test_scenarios_needing_no_fault_leave_namespace_clean(self):
        fake = FakeCluster()
        with patch.object(fi, "run_cmd", fake.run_cmd):
            fi.inject_scenario("group-1", "postgresFailure", warmup_s=0)
            result = fi.inject_scenario("group-1", "poisonedEntity", warmup_s=0)
        self.assertIsNone(result)
        self.assertEqual({}, fake.active())

    def test_flagd_is_restarted_so_the_change_takes_effect(self):
        """flagd reads from an emptyDir; a ConfigMap patch alone changes nothing."""
        fake = FakeCluster()
        with patch.object(fi, "run_cmd", fake.run_cmd):
            fi.inject_scenario("group-1", "emailMemoryLeak", warmup_s=0)
        restarts = [c for c in fake.calls if "rollout" in c and "restart" in c]
        self.assertTrue(restarts, "flagd was never restarted - the fault would not apply")

    def test_load_generator_traffic_is_never_disabled(self):
        """With no traffic, even a real fault produces no observable symptoms."""
        fake = FakeCluster()
        with patch.object(fi, "run_cmd", fake.run_cmd):
            fi.inject_scenario("group-1", "postgresFailure", warmup_s=0)
        self.assertEqual("on", fake.config["flags"]["loadGeneratorTraffic"]["defaultVariant"])

    def test_clear_faults_restores_health(self):
        fake = FakeCluster()
        with patch.object(fi, "run_cmd", fake.run_cmd):
            fi.inject_scenario("group-1", "postgresSlow", warmup_s=0)
            fi.clear_faults("group-1")
        self.assertEqual({}, fake.active())


class TestFailureModes(unittest.TestCase):
    def test_unknown_scenario_is_rejected(self):
        with self.assertRaises(fi.InjectionError):
            fi.inject_scenario("group-1", "notAScenario", warmup_s=0)

    def test_injection_rejects_malicious_namespace(self):
        with self.assertRaises(ValueError):
            fi.inject_scenario("group-1; rm -rf /", "postgresFailure", warmup_s=0)

    def test_missing_variant_is_rejected(self):
        cfg = sample_config()
        cfg["flags"]["postgresSlow"]["variants"] = {"off": 0}  # 3sec no longer offered
        fake = FakeCluster(cfg)
        with patch.object(fi, "run_cmd", fake.run_cmd):
            with self.assertRaises(fi.InjectionError) as ctx:
                fi.inject_scenario("group-1", "postgresSlow", warmup_s=0)
        self.assertIn("not valid", str(ctx.exception))

    def test_rbac_denial_explains_the_fix(self):
        """Attendee service accounts may lack configmaps:patch - the error must say so."""
        fake = FakeCluster(forbid_patch=True)
        with patch.object(fi, "run_cmd", fake.run_cmd):
            with self.assertRaises(fi.InjectionError) as ctx:
                fi.inject_scenario("group-1", "postgresFailure", warmup_s=0)
        message = str(ctx.exception)
        self.assertIn("configmaps: patch", message)
        self.assertIn("--no-inject", message)

    def test_active_faults_reports_state(self):
        fake = FakeCluster()
        with patch.object(fi, "run_cmd", fake.run_cmd):
            fi.inject_scenario("group-1", "emailMemoryLeak", warmup_s=0)
            self.assertEqual({"emailMemoryLeak": "10000x"}, fi.active_faults("group-1"))


if __name__ == "__main__":
    unittest.main(verbosity=2)

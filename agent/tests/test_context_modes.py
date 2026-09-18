#!/usr/bin/env python3
"""Context-mode tests for Module 4 - no cluster required (kubectl is mocked).

Module 4 contrasts a curated context against an uncurated one. If every mode returns
the same volume the module demonstrates nothing, which is exactly what happened when
raw_80k was capped at the same 200 lines as the other modes.
"""

import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import tools


def fake_cluster(log_lines):
    """Return a run_cmd stand-in serving `log_lines`, honouring kubectl's --tail."""
    def run_cmd(args, timeout=10):
        if "get" in args and "pods" in args:
            return 0, "frontend-abc123 email-def456", ""
        if "logs" in args:
            tail = next((int(a.split("=")[1]) for a in args if a.startswith("--tail=")), len(log_lines))
            return 0, "\n".join(log_lines[-tail:]), ""
        return 0, "", ""
    return run_cmd


# A chatty pod: mostly routine lines, a few errors - the realistic shape.
CHATTY = [f"2026-09-18T10:00:{i % 60:02d}Z INFO handled request id={i} latency=12ms" for i in range(3000)]
CHATTY[500] = "2026-09-18T10:00:30Z ERROR failed to load products: PostgreSQL unavailable"
CHATTY[2999] = "2026-09-18T10:09:59Z ERROR connection refused talking to product-catalog"


class TestContextModes(unittest.TestCase):
    def _read(self, mode, lines=CHATTY):
        with patch.object(tools, "run_cmd", fake_cluster(lines)):
            return tools.query_pod_logs("frontend", "group-test", tail=30, context_mode=mode)

    def test_raw_mode_returns_far_more_than_curated_modes(self):
        """The whole point of Module 4 - if these are equal, the lesson is invisible."""
        summary = self._read("structured_summary")
        filtered = self._read("filtered_regex")
        raw = self._read("raw_80k")
        self.assertGreater(len(raw), len(filtered) * 10,
                           "raw_80k must be dramatically larger than filtered_regex")
        self.assertGreater(len(raw), len(summary) * 10)

    def test_raw_mode_ignores_the_small_tail_cap(self):
        """safe_tail caps at 200; raw mode must fetch its own, much larger budget."""
        raw = self._read("raw_80k")
        self.assertGreater(len(raw.splitlines()), 200,
                           "raw_80k is still limited by the 200-line cap")

    def test_raw_mode_is_bounded(self):
        """Uncurated must not mean unbounded - cost has to stay predictable."""
        raw = self._read("raw_80k")
        self.assertLessEqual(len(raw), tools.RAW_MODE_CHAR_CAP + 200,
                             "raw_80k exceeded its character cap")

    def test_raw_mode_says_when_it_truncated(self):
        huge = [f"line {i} " + "x" * 200 for i in range(5000)]
        raw = self._read("raw_80k", huge)
        self.assertIn("truncated", raw.lower())

    def test_filtered_mode_keeps_only_error_lines(self):
        filtered = self._read("filtered_regex")
        self.assertIn("ERROR", filtered)
        self.assertNotIn("handled request", filtered,
                         "filtered_regex leaked routine lines")

    def test_summary_mode_reports_counts_not_content(self):
        summary = self._read("structured_summary")
        self.assertIn("Structured Summary", summary)
        self.assertIn("lines scanned", summary)

    def test_every_mode_survives_an_empty_log(self):
        for mode in ("structured_summary", "filtered_regex", "raw_80k"):
            out = self._read(mode, [])
            self.assertTrue(out, f"{mode} returned nothing for an empty log")


if __name__ == "__main__":
    unittest.main(verbosity=2)

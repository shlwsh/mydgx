#!/usr/bin/env python3
"""T76-02: negative scenarios for number ledger ↔ brief mapping checks."""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
LEDGER = HERE / "number_ledger.py"


def run_check(source: Path, ledger: Path, brief: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(LEDGER), "check", str(source), str(ledger), str(brief)],
        capture_output=True,
        text=True,
    )


def write_pair(tmpdir: Path, source: str, items: list[dict], brief: str, digest: str | None = None):
    src = tmpdir / "source.txt"
    led = tmpdir / "ledger.json"
    br = tmpdir / "brief.md"
    src.write_text(source, encoding="utf-8")
    import hashlib

    sha = hashlib.sha256(src.read_bytes()).hexdigest()
    payload = {
        "version": 1,
        "source_name": src.name,
        "scope": "unit-test fixture",
        "source_sha256": digest or sha,
        "items": items,
    }
    led.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    br.write_text(brief, encoding="utf-8")
    return src, led, br


class NumberLedgerNegatives(unittest.TestCase):
    def test_pass_mapped_primary(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            source = "相对静态 −13.54% 达标。\n"
            items = [{
                "id": "N0001",
                "line": 1,
                "column": 5,
                "text": "−13.54%",
                "locator": "表2",
                "meaning": "相对静态变化率",
                "claim": "重压更低",
                "use": {"quote": "相对静态 −13.54% 达标。", "text": "−13.54%"},
            }]
            # Find actual column for −13.54%
            col = source.index("−13.54%") + 1
            items[0]["column"] = col
            src, led, br = write_pair(tmp, source, items, "相对静态 −13.54% 达标。\n")
            res = run_check(src, led, br)
            self.assertEqual(res.returncode, 0, res.stderr)
            self.assertIn("PASS", res.stdout)

    def test_missing_source_token(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            source = "A 10 和 B 20。\n"
            # Only map 10; 20 missing from ledger → fail
            items = [{
                "id": "N0001",
                "line": 1,
                "column": source.index("10") + 1,
                "text": "10",
                "locator": "p1",
                "meaning": "A",
                "claim": "c",
                "use": {"quote": "A 10 和", "text": "10"},
            }]
            src, led, br = write_pair(tmp, source, items, "A 10 和 B。\n")
            res = run_check(src, led, br)
            self.assertNotEqual(res.returncode, 0)
            self.assertIn("Missing source tokens", res.stderr)

    def test_changed_value(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            source = "效果 −13.54%。\n"
            col = source.index("−13.54%") + 1
            items = [{
                "id": "N0001",
                "line": 1,
                "column": col,
                "text": "−13.54%",
                "locator": "p1",
                "meaning": "delta",
                "claim": "c",
                "use": {"quote": "效果 −10%。", "text": "−10%"},
            }]
            src, led, br = write_pair(tmp, source, items, "效果 −10%。\n")
            res = run_check(src, led, br)
            self.assertNotEqual(res.returncode, 0)
            self.assertIn("numeric value changed", res.stderr)

    def test_same_value_wrong_claim_collision(self):
        """Two independent same literals must not share one brief span."""
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            source = "场景甲 5% ；场景乙 5%。\n"
            c1 = source.index("5%") + 1
            c2 = source.rindex("5%") + 1
            quote = "场景甲 5% ；场景乙 5%。"
            items = [
                {
                    "id": "N0001",
                    "line": 1,
                    "column": c1,
                    "text": "5%",
                    "locator": "甲",
                    "meaning": "甲效果",
                    "claim": "甲",
                    "use": {"quote": quote, "text": "5%", "occurrence": 1},
                },
                {
                    "id": "N0002",
                    "line": 1,
                    "column": c2,
                    "text": "5%",
                    "locator": "乙",
                    "meaning": "乙效果",
                    "claim": "乙",
                    # Wrongly reuse occurrence 1 → span collision
                    "use": {"quote": quote, "text": "5%", "occurrence": 1},
                },
            ]
            src, led, br = write_pair(tmp, source, items, quote + "\n")
            res = run_check(src, led, br)
            self.assertNotEqual(res.returncode, 0)
            self.assertIn("already uses this brief span", res.stderr)

    def test_duplicate_alias_ok(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            source = "先写 12，再写 12。\n"
            c1 = source.index("12") + 1
            c2 = source.rindex("12") + 1
            items = [
                {
                    "id": "N0001",
                    "line": 1,
                    "column": c1,
                    "text": "12",
                    "locator": "first",
                    "meaning": "count",
                    "claim": "c",
                    "use": {"quote": "先写 12，", "text": "12"},
                },
                {
                    "id": "N0002",
                    "line": 1,
                    "column": c2,
                    "text": "12",
                    "locator": "second",
                    "meaning": "count",
                    "claim": "c",
                    "alias_of": "N0001",
                    "use": None,
                },
            ]
            src, led, br = write_pair(tmp, source, items, "先写 12，再复述。\n")
            res = run_check(src, led, br)
            self.assertEqual(res.returncode, 0, res.stderr)
            self.assertIn("verified repeat occurrences", res.stdout)

    def test_review_fields_partial_fail(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            source = "相对静态 −13.54%。\n"
            col = source.index("−13.54%") + 1
            items = [{
                "id": "N0001",
                "line": 1,
                "column": col,
                "text": "−13.54%",
                "locator": "表2",
                "meaning": "相对静态变化率",
                "claim": "重压更低",
                "evidence_type": "derived_estimate",
                "check_status": "",
                "basis": "",
                "use": {"quote": "相对静态 −13.54%。", "text": "−13.54%"},
            }]
            src, led, br = write_pair(tmp, source, items, "相对静态 −13.54%。\n")
            res = run_check(src, led, br)
            self.assertNotEqual(res.returncode, 0)
            self.assertTrue(
                "evidence_type" in res.stderr or "check_status" in res.stderr or "basis" in res.stderr,
                res.stderr,
            )

    def test_review_fields_ok_and_summarize(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            source = "相对静态 −13.54%。\n"
            col = source.index("−13.54%") + 1
            items = [{
                "id": "N0001",
                "line": 1,
                "column": col,
                "text": "−13.54%",
                "locator": "表2",
                "meaning": "相对静态变化率",
                "claim": "重压更低",
                "evidence_type": "derived_estimate",
                "check_status": "consistent",
                "basis": "recompute_t59 --no-write matches table",
                "use": {"quote": "相对静态 −13.54%。", "text": "−13.54%"},
            }]
            src, led, br = write_pair(tmp, source, items, "相对静态 −13.54%。\n")
            res = run_check(src, led, br)
            self.assertEqual(res.returncode, 0, res.stderr)
            self.assertIn("REVIEW:", res.stdout)
            rev = subprocess.run(
                [sys.executable, str(LEDGER), "review", str(led)],
                capture_output=True, text=True,
            )
            self.assertEqual(rev.returncode, 0, rev.stderr)
            self.assertIn("consistent", rev.stdout)


def main() -> int:
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(NumberLedgerNegatives)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())

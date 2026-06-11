"""Cycle 2 audit probes — sibling-class scan.

C2-P1: rule_provider._load silently swallows ValueError (config errors).
       After fix: re-raise ValueError on size cap rejection and schema errors.
       OSError (file missing, permission) continues to fall back to empty ruleset.

C2-P2: lifecycle.evaluate cyclomatic complexity smoke check
       (informational, documents that the function is complex).
"""

from __future__ import annotations

import json
import logging
import sqlite3
from pathlib import Path

import pytest


# ──────────────────────────────────────────────────────────────────────────
# C2-P1: FileRuleProvider must surface config errors (RED → GREEN after fix)
# ──────────────────────────────────────────────────────────────────────────


class TestRuleProviderErrorPropagation:
    """File size cap rejections and schema errors must be visible to the caller.

    The bug: rule_provider.py:47-49 wraps the entire load in
    `except Exception` and silently sets `self._ruleset = RuleSet()`.
    This masks BOTH the file size cap rejection (P3 fix) AND any
    schema validation error. The caller sees "0 rules loaded" with
    no indication that the config is broken.

    The fix: separate OSError (tolerated — use empty ruleset) from
    ValueError (config error — re-raise so caller knows to fix the file).
    """

    def test_c2p1_too_large_file_raises_value_error(self, tmp_path: Path, caplog):
        """A file > RULES_FILE_MAX_BYTES must raise ValueError from constructor.

        Current bug: FileRuleProvider(big_path) returns silently with
        0 rules. The cap rejection is logged at ERROR but the caller
        has no way to know the file was rejected.
        """
        from correlation_lib.rule_provider import FileRuleProvider
        from correlation_lib.rules import RULES_FILE_MAX_BYTES

        # 12 MB file > 10 MB cap
        big_path = tmp_path / "big.json"
        rule_tmpl = (
            '{"id": "r%d", "trigger_context": "test", '
            '"trigger_keywords": ["foo", "bar"], "must_also_fetch": ["x"], '
            '"relationship_type": "related_to", "confidence": 0.5},'
        )
        big_path.write_text("[" + rule_tmpl % 0 * 80000 + "{}]")
        assert big_path.stat().st_size > RULES_FILE_MAX_BYTES

        caplog.set_level(logging.ERROR)
        with pytest.raises(ValueError, match=r"(?i)(size|large|cap|limit)"):
            FileRuleProvider(big_path, watch_enabled=False)

    def test_c2p1_schema_error_raises_value_error(self, tmp_path: Path, caplog):
        """A rule with a schema error (missing field) must raise ValueError from constructor.

        Current bug: the schema error is caught and silently logged.
        """
        from correlation_lib.rule_provider import FileRuleProvider

        # One bad rule (missing trigger_context)
        bad_path = tmp_path / "bad.json"
        bad_path.write_text(
            json.dumps([{
                "id": "r0",
                "trigger_keywords": ["foo"],
                "must_also_fetch": ["x"],
                "relationship_type": "related_to",
                "confidence": 0.5,
                # trigger_context missing
            }])
        )

        caplog.set_level(logging.ERROR)
        with pytest.raises(ValueError, match=r"(?i)(missing|required|trigger_context)"):
            FileRuleProvider(bad_path, watch_enabled=False)

    def test_c2p1_missing_file_does_not_crash(self, tmp_path: Path, caplog):
        """A missing rules file should NOT crash — use empty ruleset.

        Rationale: a missing rules file is a deployment / first-run state,
        not a config error. The engine should run in degraded mode.
        """
        from correlation_lib.rule_provider import FileRuleProvider

        missing = tmp_path / "nonexistent.json"
        assert not missing.exists()

        caplog.set_level(logging.WARNING)
        provider = FileRuleProvider(missing, watch_enabled=False)
        rules = provider.get_rules().rules
        assert rules == [], f"expected empty ruleset, got {len(rules)} rules"
        # And a warning was emitted (not silent)
        assert any(
            "cannot" in str(rec.message).lower() or "missing" in str(rec.message).lower()
            or "not found" in str(rec.message).lower()
            for rec in caplog.records if rec.levelno >= logging.WARNING
        ), f"expected a WARNING log about missing file, got: {[r.message for r in caplog.records]}"


# ──────────────────────────────────────────────────────────────────────────
# C2-P2: lifecycle.evaluate complexity smoke check (informational)
# ──────────────────────────────────────────────────────────────────────────


class TestLifecycleComplexity:
    """Informational check that LifecycleEvaluator.evaluate is complex.

    From OCR: cyclomatic 21 in `lifecycle.py:52 evaluate`.
    This is a code smell, not a bug. The probe confirms the function
    is still complex so future refactors can target it.
    """

    def test_c2p2_evaluate_is_complex(self):
        from correlation_lib.lifecycle import LifecycleManager
        import inspect
        source = inspect.getsource(LifecycleManager.evaluate)
        # Count branch points: if/elif/for/except/while/and/or (rough proxy)
        branch_count = (
            source.count("if ") + source.count("elif ") +
            source.count("for ") + source.count("except ") +
            source.count(" and ") + source.count(" or ")
        )
        # Document the current complexity
        # (assertion is informational — passes if complexity is still high)
        assert branch_count >= 10, (
            f"lifecycle.evaluate has only {branch_count} branches — "
            f"complexity may have improved"
        )


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])

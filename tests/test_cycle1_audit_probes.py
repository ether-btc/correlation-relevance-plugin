"""Cycle 1 empirical probes for correlation-relevance-plugin.

Each probe targets a specific bug claim. Probes MUST be run BEFORE the fix
to confirm the bug exists (red), then AFTER the fix to confirm the fix works (green).

Probes:
  P1: engine.py:105-111 — lifecycle log records NEW state as from_state (real bug)
  P2: enricher.py / matcher.py — task_text has no length cap (DoS by large message)
  P3: rule_provider.py:39-49 — no file size cap on rules JSON (DoS by big/JSON-bomb)
  P4: tracker.py:91-93 — dead defensive allowlist check (cosmetic, observe-only)

Run from the project root:
    python3 -m pytest tests/test_cycle1_audit_probes.py -v --no-header --tb=short
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

# Add project root to sys.path so `correlation_lib` is importable when running
# from any directory (CI, dev shell, IDE).
PROJECT_ROOT = Path(__file__).parent.parent.resolve()
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from correlation_lib.engine import CorrelationEngine
from correlation_lib.rules import (
    CorrelationRule,
    LifecycleState,
    RuleSet,
)
from correlation_lib.tracker import (
    EffectivenessTracker,
    SQLiteEffectivenessStore,
)


# ──────────────────────────────────────────────────────────────────────────
# P1: lifecycle log records the WRONG from_state
# ──────────────────────────────────────────────────────────────────────────
class TestLifecycleLogFromState:
    """Bug claim: engine.py:105 mutates rule.lifecycle_state to new_state
    BEFORE log_lifecycle is called on line 109-114, so the from_state
    recorded in the lifecycle log equals the to_state (both new_state).
    """

    def test_p1_lifecycle_log_records_correct_from_state(self, tmp_path: Path):
        """When a rule transitions PROPOSAL → TESTING, the lifecycle_log
        must record from_state=PROPOSAL, to_state=TESTING — NOT from=TESTING.
        """
        import sqlite3
        from correlation_lib.enricher import Enricher

        # Build a minimal ruleset with a TESTING rule (PROPOSAL is blocked by can_advance)
        rule = CorrelationRule(
            id="r1",
            trigger_context="test",
            trigger_keywords=("foo", "bar"),
            must_also_fetch=(),
            relationship_type="related_to",
            confidence=0.7,
            lifecycle_state=LifecycleState.TESTING,
        )
        ruleset = RuleSet(rules=[rule])

        # Use a real SQLite store in tmp_path
        db_path = tmp_path / "test.db"
        store = SQLiteEffectivenessStore(db_path=db_path)

        # Mock backends
        class _MockRecall:
            def fetch(self, path):
                return None

        class _MockContext:
            def inject(self, content, **kwargs):
                return None

        # Build engine WITHOUT rule file (we'll inject ruleset below)
        engine = CorrelationEngine(
            rule_file=None,
            db_path=db_path,
            recall_backend=_MockRecall(),
            context_backend=_MockContext(),
        )
        # Manually wire enricher with our ruleset
        engine._enricher = Enricher(
            ruleset=ruleset,
            recall_backend=_MockRecall(),
            context_backend=_MockContext(),
            tracker=EffectivenessTracker(store),
        )

        # Record enough fires to meet TESTING→VALIDATED (firing_count >= 30 AND eff_ratio >= 0.80)
        for _ in range(30):
            store.record_fire("r1")
        # 5/6 relevant = 0.833
        for _ in range(5):
            store.record_relevance("r1", True)
        store.record_relevance("r1", False)

        # Now run the lifecycle evaluation
        engine.evaluate_lifecycles(ruleset)

        # Inspect the lifecycle_log table directly
        with sqlite3.connect(db_path) as conn:
            rows = conn.execute(
                "SELECT rule_id, from_state, to_state, triggered_by FROM lifecycle_log"
            ).fetchall()
        assert len(rows) == 1, f"expected 1 log row, got {len(rows)}: {rows}"
        rule_id, from_state, to_state, triggered_by = rows[0]
        assert rule_id == "r1"
        # THIS IS THE BUG: from_state should be "testing" but is "validated"
        assert from_state == "testing", (
            f"BUG CONFIRMED: from_state should be 'testing' but is {from_state!r} "
            f"(to_state was {to_state!r}). Engine.py:105 mutates rule.lifecycle_state "
            f"BEFORE log_lifecycle reads it on line 111."
        )
        assert to_state == "validated"


# ──────────────────────────────────────────────────────────────────────────
# P2: task_text has no length cap (DoS by large message)
# ──────────────────────────────────────────────────────────────────────────
class TestTaskTextLengthCap:
    """Bug claim: a user can send a multi-MB message that flows into
    enricher.py:89 → matcher.match() → re.findall + per-rule re.search.
    The empty-task guard in enricher.py:89 catches None/empty, but a large
    string passes the guard and triggers O(n) work + O(n) memory.
    """

    def test_p2_large_task_text_is_capped(self, tmp_path: Path):
        """Sending a large task_text (>64KB) should be truncated or rejected.
        Currently: silently accepted and processes the whole string.
        """
        # Build a simple engine with one rule
        rule = CorrelationRule(
            id="r1",
            trigger_context="test",
            trigger_keywords=("foo",),
            must_also_fetch=(),
            relationship_type="related_to",
            confidence=0.9,
            lifecycle_state=LifecycleState.TESTING,
        )
        ruleset = RuleSet(rules=[rule])

        # Mock backends
        class _MockRecall:
            def fetch(self, path):
                return None

        class _MockContext:
            def inject(self, content, **kwargs):
                return None

        from correlation_lib.enricher import Enricher
        enricher = Enricher(
            ruleset=ruleset,
            recall_backend=_MockRecall(),
            context_backend=_MockContext(),
            tracker=EffectivenessTracker(SQLiteEffectivenessStore(tmp_path / "test.db")),
        )

        # 200KB of "x" with a target keyword
        # (200KB chosen as "moderately large" — would still cause O(n) regex work
        #  and O(n) memory for the lowercase copy + word set)
        large = "x" * (200 * 1024) + " foo " + "x" * (200 * 1024)
        result = enricher.on_task_start(large)
        # After fix: should be capped or truncated to <64KB
        # Currently: full 400KB passes through
        assert len(result.task_text) <= 65536, (
            f"BUG: task_text of {len(result.task_text):,} bytes was accepted "
            f"without truncation. Memory exhaustion is possible."
        )


# ──────────────────────────────────────────────────────────────────────────
# P3: rules file has no size cap (DoS by big file)
# ──────────────────────────────────────────────────────────────────────────
class TestRuleFileSizeCap:
    """Bug claim: load_rules_from_file reads the entire JSON with no cap.
    A large JSON file (or a JSON-bomb with deep nesting) will OOM the process.
    """

    def test_p3_large_rules_file_is_rejected(self, tmp_path: Path, monkeypatch):
        """A rules file larger than the cap should be rejected BEFORE json.load."""
        from correlation_lib.rules import RULES_FILE_MAX_BYTES, load_rules_from_file

        # Create a file slightly larger than the cap (12 MB > 10 MB)
        big_path = tmp_path / "rules.json"
        rule_template = (
            '{"id": "r%d", "trigger_context": "test", '
            '"trigger_keywords": ["foo", "bar"], "must_also_fetch": ["x"], '
            '"relationship_type": "related_to", "confidence": 0.5},'
        )
        # Each template is ~150 bytes; need ~80,000 to hit 12 MB
        big_path.write_text("[" + rule_template % 0 * 80000 + "{}]")

        # Sanity: file exceeds the cap
        assert big_path.stat().st_size > RULES_FILE_MAX_BYTES, (
            f"Test setup error: file {big_path.stat().st_size:,} bytes did not "
            f"exceed cap {RULES_FILE_MAX_BYTES:,}"
        )

        # Direct call to load_rules_from_file: must raise ValueError mentioning the cap
        with pytest.raises(ValueError) as exc_info:
            load_rules_from_file(big_path)
        msg = str(exc_info.value).lower()
        assert "size" in msg or "large" in msg or "limit" in msg or "cap" in msg, (
            f"ValueError raised but message {str(exc_info.value)!r} doesn't mention size cap. "
            f"Was the error from a different validation?"
        )


# ──────────────────────────────────────────────────────────────────────────
# P4: tracker.py:91-93 — dead defensive check (LOW, observe-only)
# ──────────────────────────────────────────────────────────────────────────
class TestTrackerDeadCode:
    """Observation: defensive check at tracker.py:91-93 is unreachable
    (col is derived from a bool). Not a bug — observe only.
    """

    def test_p4_record_relevance_both_branches(self, tmp_path: Path):
        """Sanity check that both branches work."""
        store = SQLiteEffectivenessStore(tmp_path / "test.db")
        store.record_relevance("r1", True)
        store.record_relevance("r1", False)
        store.record_relevance("r1", True)
        stats = store.get_stats("r1")
        assert stats["relevance_count"] == 2
        assert stats["irrelevance_count"] == 1

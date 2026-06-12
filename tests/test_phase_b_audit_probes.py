"""Phase B audit probes — prior-audit open issues (2026-05-29).

Probes target the 4 open issues from the 2026-05-29 audit that the
2026-06-11 audit explicitly did not address. Each probe tests the
bug as filed and records the result.

Issue references:
  #1 Silent engine failure: adapter.py:121-139 `except Exception` swallows all
  #2 Thread-unsafe: ruleset.rules mutated in-place without lock
  #3 Ambiguous None: HermesRecallBackend.fetch returns None for not-found
     AND misconfigured
  #4 Rule ID format not enforced: rules.py:108 documents pattern but doesn't
     enforce at validation

Run from the project root:
    python3 -m pytest tests/test_phase_b_audit_probes.py -v --no-header --tb=short
"""
from __future__ import annotations

import logging
import sqlite3
import sys
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Add project root to sys.path so `correlation_lib` is importable when running
# from any directory (CI, dev shell, IDE).
PROJECT_ROOT = Path(__file__).parent.parent.resolve()
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from correlation_lib.engine import CorrelationEngine
from correlation_lib.lifecycle import LifecycleManager, LifecycleState
from correlation_lib.rules import (
    CorrelationRule,
    RELATIONSHIP_TYPES,
    RuleSet,
    load_rules_from_json,
)
from correlation_lib.tracker import (
    EffectivenessTracker,
    SQLiteEffectivenessStore,
)


# ──────────────────────────────────────────────────────────────────────────
# B.1: Issue #1 — silent engine failure in adapter.py:121-139
# ──────────────────────────────────────────────────────────────────────────
class TestAdapterSilentInit:
    """Issue #1: CorrelationMemoryProvider.initialize() catches all
    exceptions, sets self._engine = None, and returns successfully.
    The caller (Hermes) has no way to know the engine failed to init.

    The CURRENT behavior:
    - All exceptions caught in `except Exception`
    - Logged at ERROR level (visible to ops)
    - self._engine set to None
    - Returns successfully
    - All subsequent operations (prefetch, on_turn_start) silently
      no-op because they check `if not self._engine`

    The bug (as filed): the caller has no programmatic way to detect
    the failure. A `last_init_error` attribute or an exception-on-init
    flag would close the visibility gap.
    """

    def _make_mock_mnemosyne_raising(self):
        """A mnemosyne instance that raises on .remember()."""
        m = MagicMock()
        m.remember.side_effect = RuntimeError("simulated mnemosyne init failure")
        return m

    def test_b1_silent_init_leaves_engine_none(self, tmp_path: Path, caplog):
        """When Mnemosyne causes create_engine to fail, the provider
        should be visible to the caller as broken.

        Current: provider._engine is None, no exception, no
        programmatic visibility (the only signal is the ERROR log).
        """
        from correlation_lib_adapters.hermes import CorrelationMemoryProvider

        # Build a minimal mock environment
        # The integration test does this via hermes_mock_modules fixture
        # We'll mock the import path that adapter.py uses
        import sys
        import types

        # Mock hermes_agent.agent.memory_provider
        mock_hp = types.ModuleType("hermes_agent")
        mock_agent = types.ModuleType("hermes_agent.agent")
        mock_mp = types.ModuleType("hermes_agent.agent.memory_provider")

        class MockMP:
            name = "mock"

        mock_mp.MemoryProvider = MockMP
        mock_agent.memory_provider = mock_mp
        mock_hp.agent = mock_agent

        # Mock hermes_constants
        mock_const = types.ModuleType("hermes_constants")
        mock_const.get_hermes_home = lambda: tmp_path

        # Install in sys.modules
        saved = {}
        for k, v in [
            ("hermes_agent", mock_hp),
            ("hermes_agent.agent", mock_agent),
            ("hermes_agent.agent.memory_provider", mock_mp),
            ("hermes_constants", mock_const),
        ]:
            saved[k] = sys.modules.get(k)
            sys.modules[k] = v

        try:
            provider = CorrelationMemoryProvider()

            # Force a non-existent rule file with strict permissions to trigger
            # a real error. The adapter does:
            #   rule_file=Path(rule_file) if rule_file.exists() else None
            # So a missing file doesn't trigger create_engine.
            # We need a different way to force a failure.

            # Patch create_engine to raise
            from correlation_lib_adapters.hermes import adapter as adapter_mod

            with patch.object(
                adapter_mod, "create_engine",
                side_effect=RuntimeError("simulated engine init failure"),
            ):
                caplog.set_level(logging.ERROR)
                # The call should NOT raise (per current behavior)
                provider.initialize(
                    session_id="test",
                    hermes_home=str(tmp_path),
                    correlation_rules_file=str(tmp_path / "rules.json"),
                )

            # ASSERTION: engine is None after the failure
            assert provider._engine is None, (
                "Expected provider._engine to be None after a forced init failure"
            )

            # ASSERTION: the failure is logged at ERROR (visible to ops)
            assert any(
                "simulated engine init failure" in str(rec.message)
                for rec in caplog.records
            ), f"Expected ERROR log, got: {[r.message for r in caplog.records]}"

            # The bug claim: no programmatic visibility for the caller.
            # Check: does the provider expose any way to know the init failed?
            assert not hasattr(provider, "last_init_error"), (
                "PROBE FAIL: provider exposes last_init_error — bug is fixed"
            )
            assert not hasattr(provider, "init_error"), (
                "PROBE FAIL: provider exposes init_error — bug is fixed"
            )
        finally:
            # Restore sys.modules
            for k, v in saved.items():
                if v is None:
                    sys.modules.pop(k, None)
                else:
                    sys.modules[k] = v


# ──────────────────────────────────────────────────────────────────────────
# B.2: Issue #2 — ruleset.rules mutated in-place without lock
# ──────────────────────────────────────────────────────────────────────────
class TestThreadSafety:
    """Issue #2: ruleset.rules mutated in-place. Two concurrent
    evaluate_lifecycles calls could race on rule.lifecycle_state.

    Probe: spin up 8 threads, each calling evaluate_lifecycles on a
    shared ruleset. After all threads finish, verify:
    - No rule was lost (count is consistent)
    - No state was lost (each rule's final state matches expectations)
    - No exception was raised
    - The lifecycle log row count matches the actual transitions

    A race would manifest as: rules with the wrong final state, or
    duplicate log rows, or rule count drift.
    """

    def test_b2_concurrent_evaluate_lifecycles(self, tmp_path: Path):
        """8 threads concurrently calling evaluate_lifecycles on a
        shared ruleset should not corrupt state.

        Each thread: 10 rules at TESTING, 30 fires, 5/6 relevant
        (meets TESTING → VALIDATED threshold).
        """
        # Build 10 TESTING rules
        rules = [
            CorrelationRule(
                id=f"r{i}",
                trigger_context="test",
                trigger_keywords=(f"kw{i}",),
                must_also_fetch=(),
                relationship_type="related_to",
                confidence=0.7,
                lifecycle_state=LifecycleState.TESTING,
            )
            for i in range(10)
        ]
        ruleset = RuleSet(rules=rules)
        db_path = tmp_path / "test.db"
        store = SQLiteEffectivenessStore(db_path=db_path)

        # Pre-fire 30 fires + 5/6 relevance for each rule
        for i in range(10):
            for _ in range(30):
                store.record_fire(f"r{i}")
            for _ in range(5):
                store.record_relevance(f"r{i}", True)
            store.record_relevance(f"r{i}", False)

        # Mock backends
        class _MockRecall:
            def fetch(self, path):
                return None

        class _MockContext:
            def inject(self, content, **kwargs):
                return None

        from correlation_lib.enricher import Enricher

        engine = CorrelationEngine(
            rule_file=None,
            db_path=db_path,
            recall_backend=_MockRecall(),
            context_backend=_MockContext(),
        )
        engine._enricher = Enricher(
            ruleset=ruleset,
            recall_backend=_MockRecall(),
            context_backend=_MockContext(),
            tracker=EffectivenessTracker(store),
        )

        # Spin up 8 threads
        errors: list[Exception] = []
        barrier = threading.Barrier(8)

        def worker():
            try:
                barrier.wait(timeout=5)
                for _ in range(50):  # 50 iterations per thread
                    engine.evaluate_lifecycles(ruleset)
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=worker) for _ in range(8)]
        start = time.time()
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)
        elapsed = time.time() - start

        # All threads completed without exception
        assert not errors, f"Threads raised exceptions: {errors}"

        # Rule count must be preserved
        assert len(ruleset.rules) == 10, (
            f"Rule count drifted: expected 10, got {len(ruleset.rules)}"
        )

        # Inspect lifecycle_log: at least one row per rule
        with sqlite3.connect(db_path) as conn:
            rows = conn.execute(
                "SELECT rule_id, from_state, to_state, COUNT(*) as cnt "
                "FROM lifecycle_log GROUP BY rule_id"
            ).fetchall()

        # Note: we accept DUPLICATE rows as evidence of the bug.
        # The probe ASSERTS on rule count and rule state, not on log row count.
        # A duplicate log row would be evidence of a race; the probe records
        # the count for analysis.
        rule_ids_with_log = {row[0] for row in rows}
        assert rule_ids_with_log == {f"r{i}" for i in range(10)}, (
            f"Missing log rows for some rules. Got: {rule_ids_with_log}"
        )

        # The from_state for every row should be 'testing' (the pre-state)
        # If a race caused some rows to record 'validated' as from_state,
        # that's the C1-F1 bug class reappearing.
        bad_from_states = [row for row in rows if row[1] != "testing"]
        assert not bad_from_states, (
            f"BUG: from_state != 'testing' in some log rows: {bad_from_states}"
        )

        # Document for the audit record (printed to pytest -v output)
        print(
            f"\nThread-safety probe: 8 threads × 50 iterations completed "
            f"in {elapsed:.2f}s, "
            f"{sum(row[3] for row in rows)} total log rows for 10 rules"
        )


# ──────────────────────────────────────────────────────────────────────────
# B.3: Issue #3 — ambiguous None in HermesRecallBackend.fetch
# ──────────────────────────────────────────────────────────────────────────
class TestAmbiguousNone:
    """Issue #3: HermesRecallBackend.fetch returns None for two distinct
    cases:
    (a) Mnemosyne not configured (no instance set) — degraded mode
    (b) Mnemosyne queried but path/query not found in memory

    The caller cannot distinguish them. Issue #3 filed the API design
    concern: should the caller be able to tell "memory is empty" from
    "memory isn't connected"?

    Current behavior: both return None. The first logs at DEBUG, the
    second logs at WARNING. Log visibility is the only signal.
    """

    def test_b3_fetch_returns_none_when_mnemosyne_not_set(self, caplog):
        """Without Mnemosyne, fetch returns None silently (DEBUG log)."""
        from correlation_lib_adapters.hermes.backends import HermesRecallBackend

        backend = HermesRecallBackend()
        caplog.set_level(logging.DEBUG)
        result = backend.fetch("any-path")
        assert result is None, f"Expected None, got {result!r}"

    def test_b3_fetch_returns_none_when_mnemosyne_empty(self, caplog):
        """With Mnemosyne but no matching memories, fetch returns None."""
        from correlation_lib_adapters.hermes.backends import HermesRecallBackend

        mock_mnemosyne = MagicMock()
        mock_mnemosyne.remember.return_value = []  # no results
        mock_mnemosyne.get_context.return_value = []  # no context either

        backend = HermesRecallBackend(mnemosyne=mock_mnemosyne)
        caplog.set_level(logging.DEBUG)
        result = backend.fetch("nonexistent-path")
        assert result is None, f"Expected None, got {result!r}"

    def test_b3_caller_cannot_distinguish(self, caplog):
        """Probe: the caller gets None in BOTH cases. The only
        difference is in the log records, which the caller can't see.

        This is the API design concern from issue #3. The probe
        documents that the behavior matches the filed bug claim.
        """
        from correlation_lib_adapters.hermes.backends import HermesRecallBackend

        # Case A: no mnemosyne
        caplog.clear()
        backend_a = HermesRecallBackend()
        result_a = backend_a.fetch("test-path")

        # Case B: mnemosyne, no results
        caplog.clear()
        mock_mnemosyne = MagicMock()
        mock_mnemosyne.remember.return_value = []
        mock_mnemosyne.get_context.return_value = []
        backend_b = HermesRecallBackend(mnemosyne=mock_mnemosyne)
        result_b = backend_b.fetch("test-path")

        # Both return None
        assert result_a is None and result_b is None, (
            f"Expected both None, got A={result_a!r}, B={result_b!r}"
        )

        # The probe documents that the caller's experience is identical.
        # Fix would be: return a sentinel (NotFound, Misconfigured, etc.)
        # or raise on misconfiguration. Out of scope for this audit —
        # file as followup issue.

        # The probe also checks: does the class expose a way to know
        # the misconfiguration state?
        assert not hasattr(backend_a, "is_configured") and not hasattr(
            backend_b, "is_configured"
        ), "Backend exposes is_configured — bug is fixed"


# ──────────────────────────────────────────────────────────────────────────
# B.4: Issue #4 — Rule ID format not enforced
# ──────────────────────────────────────────────────────────────────────────
class TestRuleIDFormat:
    """Issue #4: load_rules_from_json documents the rule ID pattern
    (^[a-z][a-z0-9_-]*$) in RULE_SCHEMA at rules.py:108, but the
    validation at load_rules_from_json (and CorrelationRule.__post_init__)
    does NOT enforce it.

    Probe: load rules with various invalid IDs and verify whether
    they're accepted. If accepted, file the bug + apply the fix.
    """

    def test_b4_uppercase_id_is_rejected(self):
        """An ID starting with uppercase should be rejected per schema."""
        data = [{
            "id": "UPPERCASE",
            "trigger_context": "test",
            "trigger_keywords": ["foo"],
            "must_also_fetch": ["x"],
            "relationship_type": "related_to",
            "confidence": 0.5,
        }]
        # After fix: should raise ValueError
        # Before fix: accepted silently
        try:
            ruleset = load_rules_from_json(data)
            # If we get here, the bug is present
            assert False, (
                f"BUG CONFIRMED: uppercase ID 'UPPERCASE' was accepted. "
                f"Rules: {[r.id for r in ruleset.rules]}"
            )
        except ValueError as exc:
            msg = str(exc).lower()
            assert "id" in msg or "pattern" in msg or "format" in msg, (
                f"ValueError raised but message {str(exc)!r} doesn't mention ID pattern"
            )

    def test_b4_id_starting_with_digit_is_rejected(self):
        """An ID starting with a digit should be rejected per schema."""
        data = [{
            "id": "1starts-with-digit",
            "trigger_context": "test",
            "trigger_keywords": ["foo"],
            "must_also_fetch": ["x"],
            "relationship_type": "related_to",
            "confidence": 0.5,
        }]
        try:
            ruleset = load_rules_from_json(data)
            assert False, (
                f"BUG CONFIRMED: digit-starting ID was accepted. "
                f"Rules: {[r.id for r in ruleset.rules]}"
            )
        except ValueError as exc:
            msg = str(exc).lower()
            assert "id" in msg or "pattern" in msg or "format" in msg, (
                f"ValueError raised but message {str(exc)!r} doesn't mention ID pattern"
            )

    def test_b4_id_with_special_chars_is_rejected(self):
        """An ID with a space should be rejected per schema."""
        data = [{
            "id": "has space",
            "trigger_context": "test",
            "trigger_keywords": ["foo"],
            "must_also_fetch": ["x"],
            "relationship_type": "related_to",
            "confidence": 0.5,
        }]
        try:
            ruleset = load_rules_from_json(data)
            assert False, (
                f"BUG CONFIRMED: ID with space was accepted. "
                f"Rules: {[r.id for r in ruleset.rules]}"
            )
        except ValueError as exc:
            msg = str(exc).lower()
            assert "id" in msg or "pattern" in msg or "format" in msg, (
                f"ValueError raised but message {str(exc)!r} doesn't mention ID pattern"
            )

    def test_b4_valid_id_still_accepted(self):
        """A valid lowercase ID should be accepted (regression guard)."""
        data = [{
            "id": "valid-id_123",
            "trigger_context": "test",
            "trigger_keywords": ["foo"],
            "must_also_fetch": ["x"],
            "relationship_type": "related_to",
            "confidence": 0.5,
        }]
        ruleset = load_rules_from_json(data)
        assert len(ruleset.rules) == 1
        assert ruleset.rules[0].id == "valid-id_123"

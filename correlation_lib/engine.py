"""Thin facade/factory for the correlation engine.

Engine is the single entry point — creates and wires all components.
Target: <100 LoC.
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path

from correlation_lib.enricher import Enricher
from correlation_lib.interfaces import ContextBackend, EffectivenessStore, RecallBackend
from correlation_lib.lifecycle import LifecycleManager
from correlation_lib.rule_provider import FileRuleProvider
from correlation_lib.rules import RuleSet
from correlation_lib.tracker import EffectivenessTracker, SQLiteEffectivenessStore
from correlation_lib.lifecycle import LifecycleManager, LifecycleState

logger = logging.getLogger(__name__)


class CorrelationEngine:
    """Wires together rules, matching, tracking, and enrichment.

    Single entry point for the correlation engine.
    All configuration flows through here.
    """

    def __init__(
        self,
        rule_file: str | Path | None = None,
        watch_enabled: bool = False,
        db_path: str | Path | None = None,
        recall_backend: RecallBackend | None = None,
        context_backend: ContextBackend | None = None,
    ) -> None:
        # Rule provider
        if rule_file:
            self._rule_provider = FileRuleProvider(rule_file, watch_enabled=watch_enabled)
        else:
            self._rule_provider = None
            logger.warning("No rule_file provided — engine will run with empty rule set")

        # Stores
        store: EffectivenessStore = SQLiteEffectivenessStore(db_path=db_path)
        self._tracker = EffectivenessTracker(store)
        self._lifecycle_manager = LifecycleManager()

        # Backends (require concrete implementations)
        self._recall_backend = recall_backend
        self._context_backend = context_backend

        # Enricher
        self._enricher: Enricher | None = None
        if self._rule_provider and self._recall_backend and self._context_backend:
            ruleset = self._rule_provider.get_rules()
            self._enricher = Enricher(ruleset, self._recall_backend, self._context_backend, self._tracker)
        else:
            logger.warning("Engine initialized without backends — enrichment disabled")

        # Lifecycle lock — evaluate_lifecycles mutates ruleset.rules[*]
        # .lifecycle_state in-place and writes to the SQLite lifecycle log.
        # Concurrent calls (e.g., from multiple threads/tasks) race on the
        # in-place state mutation: another thread's update can be observed
        # as the "from_state" in the log row, producing corrupted audit
        # trail entries. The C1-F1 fix (capture prev_state before setattr)
        # is correct for single-threaded use; this lock makes the
        # mutation + log write atomic across threads.
        # See: tests/test_phase_b_audit_probes.py::TestThreadSafety
        #      and issue #2 from 2026-05-29 prior audit.
        self._lifecycle_lock = threading.RLock()

    @property
    def enricher(self) -> Enricher | None:
        return self._enricher

    @property
    def tracker(self) -> EffectivenessTracker:
        return self._tracker

    @property
    def lifecycle_manager(self) -> LifecycleManager:
        return self._lifecycle_manager

    @property
    def rule_provider(self) -> FileRuleProvider | None:
        return self._rule_provider

    def reload_rules(self) -> None:
        """Reload rules from file."""
        if self._rule_provider:
            self._rule_provider.reload()

    def evaluate_lifecycles(self, ruleset: RuleSet) -> None:
        """Run lifecycle evaluation on all tracked rules.

        Called periodically or after significant firing_count changes.
        Q1=A: fully automated — no human intervention required.

        Thread-safety: this method mutates rule.lifecycle_state in-place
        and writes to the SQLite lifecycle log. A threading.RLock
        (self._lifecycle_lock) serializes the capture-from-state /
        mutate / write-log sequence so concurrent calls produce
        consistent audit trail entries. Single-threaded callers are
        unaffected (lock acquisition is uncontended).
        """
        with self._lifecycle_lock:
            all_stats = self._tracker.get_all_stats()
            for rule in ruleset.get_active_rules():
                if not self._lifecycle_manager.can_advance(rule):
                    continue
                stats = all_stats.get(rule.id)
                if not stats:
                    continue
                new_state = self._lifecycle_manager.evaluate(
                    rule,
                    firing_count=stats.firing_count,
                    effectiveness_ratio=stats.effectiveness_ratio,
                )
                if new_state:
                    # Capture the pre-mutation state — the in-place setattr on the
                    # next line mutates rule.lifecycle_state, so we must read it
                    # before that mutation or log_lifecycle records the wrong
                    # from_state (which equals the to_state).
                    prev_state = rule.lifecycle_state
                    # Use the manager's specific reason if available (e.g.,
                    # "hard demote: firing_count(100) >= 90 AND
                    # effectiveness_ratio(0.10) < 0.20"). Fall back to a
                    # generic reason if the manager did not record one
                    # (defense-in-depth; the manager always records a
                    # reason on a successful transition, but a future
                    # refactor could change that).
                    reason = self._lifecycle_manager.last_reason_for(rule.id)
                    if reason is None:
                        reason = (
                            f"auto: firing_count={stats.firing_count}, "
                            f"eff_ratio={stats.effectiveness_ratio:.3f}"
                        )
                    # Update rule in ruleset
                    for r in ruleset.rules:
                        if r.id == rule.id:
                            object.__setattr__(r, "lifecycle_state", new_state)
                    # Update store
                    self._tracker._store.update_state(rule.id, new_state)  # type: ignore
                    # Log to lifecycle log
                    self._tracker._store.log_lifecycle(  # type: ignore
                        rule.id,
                        prev_state,
                        new_state,
                        reason,
                        "auto",
                    )


def create_engine(
    rule_file: str | Path,
    watch_enabled: bool = False,
    db_path: str | Path | None = None,
    recall_backend: RecallBackend | None = None,
    context_backend: ContextBackend | None = None,
) -> CorrelationEngine:
    """Factory function to create a configured engine."""
    return CorrelationEngine(
        rule_file=rule_file,
        watch_enabled=watch_enabled,
        db_path=db_path,
        recall_backend=recall_backend,
        context_backend=context_backend,
    )

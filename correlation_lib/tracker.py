"""Effectiveness tracking with SQLite persistence.

Q4=A confirmed: SQLite standalone at ~/.hermes/correlation-effectiveness.db
Q1=A confirmed: fully automated lifecycle advancement.
"""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from correlation_lib.interfaces import EffectivenessStore
from correlation_lib.rules import LifecycleState

logger = logging.getLogger(__name__)

DB_PATH = Path.home() / ".hermes" / "correlation-effectiveness.db"


@dataclass
class RuleStats:
    """Aggregated effectiveness stats for a rule."""

    rule_id: str
    firing_count: int
    relevance_count: int
    irrelevance_count: int
    effectiveness_ratio: float
    last_fired: str | None
    last_relevance_recorded: str | None
    current_state: LifecycleState


class SQLiteEffectivenessStore(EffectivenessStore):
    """SQLite-backed effectiveness store (Q4=A)."""

    def __init__(self, db_path: Path | str | None = None) -> None:
        self._db_path = Path(db_path) if db_path else DB_PATH
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _init_db(self) -> None:
        with sqlite3.connect(self._db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS rule_effectiveness (
                    rule_id TEXT PRIMARY KEY,
                    firing_count INTEGER DEFAULT 0,
                    relevance_count INTEGER DEFAULT 0,
                    irrelevance_count INTEGER DEFAULT 0,
                    last_fired TEXT,
                    last_relevance_recorded TEXT,
                    current_state TEXT DEFAULT 'proposal'
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS lifecycle_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    rule_id TEXT NOT NULL,
                    from_state TEXT NOT NULL,
                    to_state TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    triggered_by TEXT NOT NULL,
                    timestamp TEXT NOT NULL
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_lifecycle_rule
                ON lifecycle_log(rule_id, timestamp)
            """)

    def record_fire(self, rule_id: str) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with sqlite3.connect(self._db_path) as conn:
            conn.execute(
                """
                INSERT INTO rule_effectiveness (rule_id, firing_count, last_fired)
                VALUES (?, 1, ?)
                ON CONFLICT(rule_id) DO UPDATE SET
                    firing_count = firing_count + 1,
                    last_fired = excluded.last_fired
                """,
                (rule_id, now),
            )

    def record_relevance(self, rule_id: str, is_relevant: bool) -> None:
        now = datetime.now(timezone.utc).isoformat()
        col = "relevance_count" if is_relevant else "irrelevance_count"
        # Defensive: col is constrained to only these two; validate to prevent injection
        if col not in ("relevance_count", "irrelevance_count"):
            raise ValueError(f"Invalid column name: {col}")
        with sqlite3.connect(self._db_path) as conn:
            conn.execute(
                f"""
                INSERT INTO rule_effectiveness (rule_id, {col}, last_relevance_recorded, current_state)
                VALUES (?, 1, ?, 'proposal')
                ON CONFLICT(rule_id) DO UPDATE SET
                    {col} = {col} + 1,
                    last_relevance_recorded = excluded.last_relevance_recorded
                """,
                (rule_id, now),
            )

    def get_stats(self, rule_id: str) -> dict:
        with sqlite3.connect(self._db_path) as conn:
            row = conn.execute(
                "SELECT * FROM rule_effectiveness WHERE rule_id = ?", (rule_id,)
            ).fetchone()
            if not row:
                return {}
            cols = ["rule_id", "firing_count", "relevance_count", "irrelevance_count",
                    "last_fired", "last_relevance_recorded", "current_state"]
            return dict(zip(cols, row))

    def get_all_stats(self) -> dict[str, dict]:
        with sqlite3.connect(self._db_path) as conn:
            rows = conn.execute("SELECT * FROM rule_effectiveness").fetchall()
            cols = ["rule_id", "firing_count", "relevance_count", "irrelevance_count",
                    "last_fired", "last_relevance_recorded", "current_state"]
            return {row[0]: dict(zip(cols, row)) for row in rows}

    def update_state(self, rule_id: str, state: LifecycleState) -> None:
        with sqlite3.connect(self._db_path) as conn:
            conn.execute(
                "UPDATE rule_effectiveness SET current_state = ? WHERE rule_id = ?",
                (state.value, rule_id),
            )

    def log_lifecycle(
        self,
        rule_id: str,
        from_state: LifecycleState,
        to_state: LifecycleState,
        reason: str,
        triggered_by: str,
    ) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with sqlite3.connect(self._db_path) as conn:
            conn.execute(
                """
                INSERT INTO lifecycle_log (rule_id, from_state, to_state, reason, triggered_by, timestamp)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (rule_id, from_state.value, to_state.value, reason, triggered_by, now),
            )


class EffectivenessTracker:
    """Tracks rule effectiveness and drives automated lifecycle transitions.

    Q1=A confirmed: fully automated — auto-promote AND auto-demote.
    """

    def __init__(self, store: EffectivenessStore) -> None:
        self._store = store

    def record(self, rule_id: str, was_relevant: bool | None = None) -> None:
        """Record a rule firing, optionally with relevance feedback.

        Args:
            rule_id: The rule that fired.
            was_relevant: If None, fire is recorded but no relevance judgment made.
                         If True/False, both fire and relevance are recorded.
        """
        self._store.record_fire(rule_id)
        if was_relevant is not None:
            self._store.record_relevance(rule_id, was_relevant)

    def get_effectiveness_ratio(self, rule_id: str) -> float:
        """Compute effectiveness ratio: relevant / (relevant + irrelevant)."""
        stats = self._store.get_stats(rule_id)
        rel = stats.get("relevance_count", 0)
        irel = stats.get("irrelevance_count", 0)
        total = rel + irel
        return rel / total if total > 0 else 0.0

    def get_stats(self, rule_id: str) -> RuleStats:
        """Get comprehensive stats for a rule."""
        raw = self._store.get_stats(rule_id)
        if not raw:
            raise KeyError(f"No stats found for rule {rule_id}")
        rel = raw.get("relevance_count", 0)
        irel = raw.get("irrelevance_count", 0)
        total = rel + irel
        eff_ratio = rel / total if total > 0 else 0.0
        return RuleStats(
            rule_id=rule_id,
            firing_count=raw.get("firing_count", 0),
            relevance_count=rel,
            irrelevance_count=irel,
            effectiveness_ratio=eff_ratio,
            last_fired=raw.get("last_fired"),
            last_relevance_recorded=raw.get("last_relevance_recorded"),
            current_state=LifecycleState(raw.get("current_state", "proposal")),
        )

    def get_all_stats(self) -> dict[str, RuleStats]:
        """Get stats for all rules."""
        result = {}
        for rule_id, raw in self._store.get_all_stats().items():
            rel = raw.get("relevance_count", 0)
            irel = raw.get("irrelevance_count", 0)
            total = rel + irel
            eff_ratio = rel / total if total > 0 else 0.0
            result[rule_id] = RuleStats(
                rule_id=rule_id,
                firing_count=raw.get("firing_count", 0),
                relevance_count=rel,
                irrelevance_count=irel,
                effectiveness_ratio=eff_ratio,
                last_fired=raw.get("last_fired"),
                last_relevance_recorded=raw.get("last_relevance_recorded"),
                current_state=LifecycleState(raw.get("current_state", "proposal")),
            )
        return result

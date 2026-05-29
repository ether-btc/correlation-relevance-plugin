"""Tests for correlation_lib.tracker."""

import tempfile
from pathlib import Path

import pytest

from correlation_lib.rules import LifecycleState
from correlation_lib.tracker import (
    EffectivenessTracker,
    RuleStats,
    SQLiteEffectivenessStore,
)


class TestSQLiteEffectivenessStore:
    def test_record_fire(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.db"
            store = SQLiteEffectivenessStore(db_path=db_path)
            store.record_fire("cr-001")
            stats = store.get_stats("cr-001")
            assert stats["rule_id"] == "cr-001"
            assert stats["firing_count"] == 1

    def test_record_fire_twice(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.db"
            store = SQLiteEffectivenessStore(db_path=db_path)
            store.record_fire("cr-001")
            store.record_fire("cr-001")
            stats = store.get_stats("cr-001")
            assert stats["firing_count"] == 2

    def test_record_relevance(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.db"
            store = SQLiteEffectivenessStore(db_path=db_path)
            store.record_fire("cr-001")
            store.record_relevance("cr-001", True)
            store.record_relevance("cr-001", False)
            stats = store.get_stats("cr-001")
            assert stats["relevance_count"] == 1
            assert stats["irrelevance_count"] == 1

    def test_get_all_stats(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.db"
            store = SQLiteEffectivenessStore(db_path=db_path)
            store.record_fire("cr-001")
            store.record_fire("cr-002")
            all_stats = store.get_all_stats()
            assert "cr-001" in all_stats
            assert "cr-002" in all_stats

    def test_update_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.db"
            store = SQLiteEffectivenessStore(db_path=db_path)
            store.record_fire("cr-001")
            store.update_state("cr-001", LifecycleState.PROMOTED)
            stats = store.get_stats("cr-001")
            assert stats["current_state"] == "promoted"


class TestEffectivenessTracker:
    def test_record(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.db"
            store = SQLiteEffectivenessStore(db_path=db_path)
            tracker = EffectivenessTracker(store)
            tracker.record("cr-001")
            stats = tracker.get_stats("cr-001")
            assert stats.firing_count == 1

    def test_record_with_relevance(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.db"
            store = SQLiteEffectivenessStore(db_path=db_path)
            tracker = EffectivenessTracker(store)
            tracker.record("cr-001", was_relevant=True)
            tracker.record("cr-001", was_relevant=True)
            tracker.record("cr-001", was_relevant=False)
            ratio = tracker.get_effectiveness_ratio("cr-001")
            assert ratio == pytest.approx(2/3, rel=0.01)

    def test_effectiveness_ratio_zero(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.db"
            store = SQLiteEffectivenessStore(db_path=db_path)
            tracker = EffectivenessTracker(store)
            tracker.record("cr-001")
            ratio = tracker.get_effectiveness_ratio("cr-001")
            assert ratio == 0.0

    def test_get_stats(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.db"
            store = SQLiteEffectivenessStore(db_path=db_path)
            tracker = EffectivenessTracker(store)
            tracker.record("cr-001", was_relevant=True)
            tracker.record("cr-001", was_relevant=False)
            stats = tracker.get_stats("cr-001")
            assert isinstance(stats, RuleStats)
            # record() always records a fire AND relevance, so two calls = two fires
            assert stats.firing_count == 2
            assert stats.relevance_count == 1
            assert stats.irrelevance_count == 1
            assert stats.effectiveness_ratio == pytest.approx(0.5, rel=0.01)

    def test_get_all_stats(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.db"
            store = SQLiteEffectivenessStore(db_path=db_path)
            tracker = EffectivenessTracker(store)
            tracker.record("cr-001")
            tracker.record("cr-002")
            all_stats = tracker.get_all_stats()
            assert "cr-001" in all_stats
            assert "cr-002" in all_stats

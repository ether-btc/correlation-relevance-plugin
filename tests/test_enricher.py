"""Tests for correlation_lib.enricher."""

import tempfile
from pathlib import Path

from correlation_lib.enricher import Enricher
from correlation_lib.rules import CorrelationRule, RuleSet
from correlation_lib.tracker import EffectivenessTracker, SQLiteEffectivenessStore


class MockRecall:
    def __init__(self) -> None:
        self.fetched: list[str] = []

    def fetch(self, path: str) -> str | None:
        self.fetched.append(path)
        return f"content-for-{path}"


class MockContext:
    def __init__(self) -> None:
        self.injected: list[tuple[str, str, str]] = []

    def inject(self, content: str, source_rule: str, relationship: str) -> None:
        self.injected.append((content, source_rule, relationship))


class TestEnricherIsNewTask:
    """Test the new-task detection heuristic (Q2=B)."""

    def test_verb_at_start(self) -> None:
        assert Enricher.is_new_task("Fix the memory leak in the cache")
        assert Enricher.is_new_task("Deploy to production")
        assert Enricher.is_new_task("Analyze the CI failure")

    def test_short_command(self) -> None:
        # Under 15 words + verb + object
        assert Enricher.is_new_task("check the gateway config")

    def test_long_message_not_task(self) -> None:
        # Over 15 words — not a new task
        assert not Enricher.is_new_task(
            "I have been trying to fix the memory leak in the cache for the past hour and it is still not working properly and the logs show nothing useful"
        )

    def test_non_directive(self) -> None:
        assert not Enricher.is_new_task("Can you help me understand the memory leak?")
        assert not Enricher.is_new_task("The gateway config is broken")

    def test_empty(self) -> None:
        assert not Enricher.is_new_task("")
        assert not Enricher.is_new_task("   ")


class TestEnricherOnTaskStart:
    def test_enricher_basic(self) -> None:
        rule = CorrelationRule(
            id="cr-001",
            trigger_context="config-change",
            trigger_keywords=("config", "setting"),
            must_also_fetch=("backup-location", "rollback-instructions"),
            relationship_type="constrains",
            confidence=0.95,
        )
        ruleset = RuleSet()
        ruleset.add(rule)

        recall = MockRecall()
        context = MockContext()
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.db"
            store = SQLiteEffectivenessStore(db_path=db_path)
            tracker = EffectivenessTracker(store)
            enricher = Enricher(ruleset, recall, context, tracker)
            result = enricher.on_task_start("I need to modify the config setting")

            assert result.injected_count == 2
            assert len(result.fired_rules) == 1
            assert result.fired_rules[0][0].id == "cr-001"

    def test_enricher_no_match(self) -> None:
        rule = CorrelationRule(
            id="cr-001",
            trigger_context="config-change",
            trigger_keywords=("config",),
            must_also_fetch=("backup-location",),
            relationship_type="constrains",
            confidence=0.95,
        )
        ruleset = RuleSet()
        ruleset.add(rule)

        recall = MockRecall()
        context = MockContext()
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.db"
            store = SQLiteEffectivenessStore(db_path=db_path)
            tracker = EffectivenessTracker(store)
            enricher = Enricher(ruleset, recall, context, tracker)
            result = enricher.on_task_start("debug the memory issue")

            assert result.injected_count == 0

    def test_enricher_missing_context(self) -> None:
        class FailingRecall:
            def fetch(self, path: str) -> str | None:
                return None  # Context not found

        rule = CorrelationRule(
            id="cr-001",
            trigger_context="config-change",
            trigger_keywords=("config",),
            must_also_fetch=("missing-context",),
            relationship_type="constrains",
            confidence=0.95,
        )
        ruleset = RuleSet()
        ruleset.add(rule)

        recall = FailingRecall()
        context = MockContext()
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.db"
            store = SQLiteEffectivenessStore(db_path=db_path)
            tracker = EffectivenessTracker(store)
            enricher = Enricher(ruleset, recall, context, tracker)
            result = enricher.on_task_start("modify the config")

            assert result.injected_count == 0
            assert len(result.errors) == 1
            assert "not found" in result.errors[0]


class TestEnricherOnPrefetch:
    def test_prefetch_high_confidence_only(self) -> None:
        rules = [
            CorrelationRule(
                id="cr-high",
                trigger_context="test",
                trigger_keywords=("test",),
                must_also_fetch=("ctx-high",),
                relationship_type="constrains",
                confidence=0.95,
            ),
            CorrelationRule(
                id="cr-low",
                trigger_context="test",
                trigger_keywords=("test",),
                must_also_fetch=("ctx-low",),
                relationship_type="constrains",
                confidence=0.60,
            ),
        ]
        ruleset = RuleSet()
        for r in rules:
            ruleset.add(r)

        recall = MockRecall()
        context = MockContext()
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.db"
            store = SQLiteEffectivenessStore(db_path=db_path)
            tracker = EffectivenessTracker(store)
            enricher = Enricher(ruleset, recall, context, tracker)
            result = enricher.on_prefetch("test task")

            # Only high-confidence (>=0.9) should fire in prefetch
            assert result.injected_count == 1
            assert recall.fetched == ["ctx-high"]

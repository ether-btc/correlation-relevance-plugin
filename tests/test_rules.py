"""Tests for correlation_lib.rules."""

import pytest

from correlation_lib.rules import (
    CorrelationRule,
    LifecycleState,
    MatchMode,
    RuleSet,
    load_rules_from_json,
)


class TestCorrelationRule:
    def test_valid_rule(self) -> None:
        rule = CorrelationRule(
            id="cr-001",
            trigger_context="config-change",
            trigger_keywords=("config", "setting"),
            must_also_fetch=("backup-location",),
            relationship_type="constrains",
            confidence=0.95,
        )
        assert rule.id == "cr-001"
        assert rule.trigger_context == "config-change"
        assert rule.lifecycle_state == LifecycleState.PROPOSAL

    def test_invalid_relationship_type(self) -> None:
        with pytest.raises(ValueError, match="Invalid relationship_type"):
            CorrelationRule(
                id="cr-002",
                trigger_context="test",
                trigger_keywords=("test",),
                must_also_fetch=("ctx",),
                relationship_type="invalid_rel",
                confidence=0.5,
            )

    def test_invalid_confidence_too_high(self) -> None:
        with pytest.raises(ValueError, match="confidence must be 0.0-1.0"):
            CorrelationRule(
                id="cr-003",
                trigger_context="test",
                trigger_keywords=("test",),
                must_also_fetch=("ctx",),
                relationship_type="constrains",
                confidence=1.5,
            )

    def test_invalid_confidence_negative(self) -> None:
        with pytest.raises(ValueError, match="confidence must be 0.0-1.0"):
            CorrelationRule(
                id="cr-004",
                trigger_context="test",
                trigger_keywords=("test",),
                must_also_fetch=("ctx",),
                relationship_type="constrains",
                confidence=-0.1,
            )

    def test_validate_keywords_short(self) -> None:
        rule = CorrelationRule(
            id="cr-005",
            trigger_context="test",
            trigger_keywords=("a",),  # single short keyword
            must_also_fetch=("ctx",),
            relationship_type="constrains",
            confidence=0.5,
        )
        warnings = rule.validate_keywords()
        assert any("single short keyword" in w for w in warnings)

    def test_validate_keywords_common_words(self) -> None:
        rule = CorrelationRule(
            id="cr-006",
            trigger_context="test",
            trigger_keywords=("the", "check"),
            must_also_fetch=("ctx",),
            relationship_type="constrains",
            confidence=0.5,
        )
        warnings = rule.validate_keywords()
        assert any("common words" in w for w in warnings)


class TestLifecycleState:
    def test_allowed_transitions(self) -> None:
        assert LifecycleState.PROPOSAL.can_transition_to(LifecycleState.TESTING)
        assert LifecycleState.TESTING.can_transition_to(LifecycleState.VALIDATED)
        assert LifecycleState.VALIDATED.can_transition_to(LifecycleState.PROMOTED)
        assert LifecycleState.PROMOTED.can_transition_to(LifecycleState.RETIRED)

    def test_disallowed_transitions(self) -> None:
        assert not LifecycleState.PROPOSAL.can_transition_to(LifecycleState.PROMOTED)
        assert not LifecycleState.RETIRED.can_transition_to(LifecycleState.PROMOTED)


class TestRuleSet:
    def test_add_rule(self) -> None:
        ruleset = RuleSet()
        rule = CorrelationRule(
            id="cr-001",
            trigger_context="config-change",
            trigger_keywords=("config",),
            must_also_fetch=("backup",),
            relationship_type="constrains",
            confidence=0.95,
        )
        warnings = ruleset.add(rule)
        assert len(ruleset.rules) == 1
        assert warnings == []

    def test_get_by_id(self) -> None:
        ruleset = RuleSet()
        rule = CorrelationRule(
            id="cr-001",
            trigger_context="test",
            trigger_keywords=("test",),
            must_also_fetch=("ctx",),
            relationship_type="constrains",
            confidence=0.5,
        )
        ruleset.add(rule)
        found = ruleset.get_by_id("cr-001")
        assert found is not None
        assert found.id == "cr-001"
        assert ruleset.get_by_id("nonexistent") is None

    def test_get_active_rules(self) -> None:
        ruleset = RuleSet()
        rules = [
            CorrelationRule(
                id=f"cr-{i:03d}",
                trigger_context="test",
                trigger_keywords=("test",),
                must_also_fetch=("ctx",),
                relationship_type="constrains",
                confidence=0.5,
                lifecycle_state=LifecycleState.PROMOTED if i % 2 == 0 else LifecycleState.RETIRED,
            )
            for i in range(4)
        ]
        for r in rules:
            ruleset.add(r)
        active = ruleset.get_active_rules()
        assert len(active) == 2
        assert all(r.lifecycle_state != LifecycleState.RETIRED for r in active)

    def test_keyword_index(self) -> None:
        ruleset = RuleSet()
        rules = [
            CorrelationRule(
                id=f"cr-{i}",
                trigger_context="test",
                trigger_keywords=(f"kw{i}",),
                must_also_fetch=("ctx",),
                relationship_type="constrains",
                confidence=0.5,
            )
            for i in range(3)
        ]
        for r in rules:
            ruleset.add(r)
        # Index should contain all keywords
        index = ruleset._keyword_index
        assert "kw0" in index
        assert "kw1" in index
        assert "kw2" in index


class TestLoadRulesFromJson:
    def test_load_valid_rules(self) -> None:
        data = [
            {
                "id": "cr-001",
                "trigger_context": "config-change",
                "trigger_keywords": ["config", "setting"],
                "must_also_fetch": ["backup-location"],
                "relationship_type": "constrains",
                "confidence": 0.95,
            },
            {
                "id": "cr-002",
                "trigger_context": "error-debug",
                "trigger_keywords": ["error", "crash"],
                "must_also_fetch": ["log-file"],
                "relationship_type": "diagnosed_by",
                "confidence": 0.85,
            },
        ]
        ruleset = load_rules_from_json(data)
        assert len(ruleset.rules) == 2
        assert ruleset.get_by_id("cr-001").confidence == 0.95
        assert ruleset.get_by_id("cr-002").trigger_context == "error-debug"

    def test_load_with_lifecycle(self) -> None:
        data = [
            {
                "id": "cr-001",
                "trigger_context": "test",
                "trigger_keywords": ["test"],
                "must_also_fetch": ["ctx"],
                "relationship_type": "constrains",
                "confidence": 0.5,
                "lifecycle": {"state": "promoted"},
            }
        ]
        ruleset = load_rules_from_json(data)
        assert ruleset.get_by_id("cr-001").lifecycle_state == LifecycleState.PROMOTED

    def test_load_with_match_mode(self) -> None:
        data = [
            {
                "id": "cr-001",
                "trigger_context": "test",
                "trigger_keywords": ["test"],
                "must_also_fetch": ["ctx"],
                "relationship_type": "constrains",
                "confidence": 0.5,
                "match_mode": "strict",
            }
        ]
        ruleset = load_rules_from_json(data)
        assert ruleset.get_by_id("cr-001").match_mode == MatchMode.STRICT

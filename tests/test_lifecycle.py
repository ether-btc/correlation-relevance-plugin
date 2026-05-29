"""Tests for correlation_lib.lifecycle."""


from correlation_lib.lifecycle import (
    AUTO_DEMOTE_FIRE_THRESHOLD,
    AUTO_PROMOTE_EFFECTIVENESS_RATIO,
    AUTO_PROMOTE_FIRE_THRESHOLD,
    LifecycleManager,
)
from correlation_lib.rules import CorrelationRule, LifecycleState


def make_rule(id: str, state: LifecycleState, confidence: float = 0.95) -> CorrelationRule:
    return CorrelationRule(
        id=id,
        trigger_context="test",
        trigger_keywords=("test",),
        must_also_fetch=("ctx",),
        relationship_type="constrains",
        confidence=confidence,
        lifecycle_state=state,
    )


class TestLifecycleManager:
    def test_proposal_to_testing(self) -> None:
        manager = LifecycleManager()
        rule = make_rule("cr-001", LifecycleState.PROPOSAL)
        new_state = manager.evaluate(rule, firing_count=5, effectiveness_ratio=0.0)
        assert new_state == LifecycleState.TESTING

    def test_proposal_stays_without_fires(self) -> None:
        manager = LifecycleManager()
        rule = make_rule("cr-001", LifecycleState.PROPOSAL)
        new_state = manager.evaluate(rule, firing_count=4, effectiveness_ratio=0.0)
        assert new_state is None

    def test_testing_to_validated_auto(self) -> None:
        manager = LifecycleManager()
        rule = make_rule("cr-001", LifecycleState.TESTING)
        new_state = manager.evaluate(
            rule,
            firing_count=AUTO_PROMOTE_FIRE_THRESHOLD + 1,
            effectiveness_ratio=0.85,
        )
        # TESTING -> VALIDATED is the correct promotion path (not directly to PROMOTED)
        assert new_state == LifecycleState.VALIDATED

    def test_validated_to_promoted_auto(self) -> None:
        manager = LifecycleManager()
        rule = make_rule("cr-001", LifecycleState.VALIDATED)
        new_state = manager.evaluate(
            rule,
            firing_count=AUTO_PROMOTE_FIRE_THRESHOLD,
            effectiveness_ratio=AUTO_PROMOTE_EFFECTIVENESS_RATIO,
        )
        assert new_state == LifecycleState.PROMOTED

    def test_auto_demote_testing(self) -> None:
        manager = LifecycleManager()
        rule = make_rule("cr-001", LifecycleState.TESTING)
        new_state = manager.evaluate(
            rule,
            firing_count=AUTO_DEMOTE_FIRE_THRESHOLD + 1,
            effectiveness_ratio=0.20,  # below threshold
        )
        assert new_state == LifecycleState.PROPOSAL

    def test_auto_demote_promoted(self) -> None:
        manager = LifecycleManager()
        rule = make_rule("cr-001", LifecycleState.PROMOTED)
        new_state = manager.evaluate(
            rule,
            firing_count=AUTO_DEMOTE_FIRE_THRESHOLD + 1,
            effectiveness_ratio=0.20,
        )
        assert new_state == LifecycleState.VALIDATED

    def test_hard_demote(self) -> None:
        manager = LifecycleManager()
        rule = make_rule("cr-001", LifecycleState.PROMOTED)
        new_state = manager.evaluate(
            rule,
            firing_count=AUTO_PROMOTE_FIRE_THRESHOLD * 3,
            effectiveness_ratio=0.15,
        )
        # Hard demote: PROMOTED -> PROPOSAL when high fire count + low effectiveness
        assert new_state == LifecycleState.PROPOSAL

    def test_disallowed_transition(self) -> None:
        manager = LifecycleManager()
        rule = make_rule("cr-001", LifecycleState.RETIRED)
        new_state = manager.evaluate(rule, firing_count=1000, effectiveness_ratio=0.99)
        assert new_state is None

    def test_can_advance_retired(self) -> None:
        manager = LifecycleManager()
        rule = make_rule("cr-001", LifecycleState.RETIRED)
        assert not manager.can_advance(rule)

    def test_transition_history(self) -> None:
        manager = LifecycleManager()
        rule = make_rule("cr-001", LifecycleState.PROPOSAL)
        new_state = manager.evaluate(rule, firing_count=5, effectiveness_ratio=0.0)
        assert new_state is not None
        assert len(manager.history) == 1
        assert manager.history[0].rule_id == "cr-001"
        assert manager.history[0].to_state == LifecycleState.TESTING

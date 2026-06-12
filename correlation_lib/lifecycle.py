"""Lifecycle state machine for correlation rules.

Implements the lifecycle state transitions defined in rules.py.
Automated lifecycle advancement based on EffectivenessTracker stats (Q1=A confirmed).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone

from correlation_lib.rules import CorrelationRule, LifecycleState

logger = logging.getLogger(__name__)


# Automated lifecycle thresholds (Q1=A: fully automated)
AUTO_PROMOTE_FIRE_THRESHOLD = 30
AUTO_PROMOTE_EFFECTIVENESS_RATIO = 0.80
AUTO_DEMOTE_FIRE_THRESHOLD = 10
AUTO_DEMOTE_EFFECTIVENESS_RATIO = 0.30


@dataclass
class LifecycleTransition:
    """Record of a lifecycle state transition."""

    rule_id: str
    from_state: LifecycleState
    to_state: LifecycleState
    reason: str
    triggered_by: str  # 'auto' or 'human'
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class LifecycleManager:
    """Manages rule lifecycle state transitions.

    Implements automated advancement per Q1=A:
    - Auto-promote: firing_count > 30 AND effectiveness_ratio > 0.80
    - Auto-demote: firing_count > 10 AND effectiveness_ratio < 0.30
    """

    def __init__(self) -> None:
        self._transitions: list[LifecycleTransition] = []

    @property
    def history(self) -> list[LifecycleTransition]:
        return list(self._transitions)

    def evaluate(
        self,
        rule: CorrelationRule,
        firing_count: int,
        effectiveness_ratio: float,
    ) -> LifecycleState | None:
        """Evaluate whether a rule should transition lifecycle state.

        Returns the new state if transition occurred, None otherwise.
        """
        current = rule.lifecycle_state
        new_state: LifecycleState | None = None
        reason = ""
        triggered_by = "auto"

        # Promotion path: proposal -> testing -> validated -> promoted
        if current == LifecycleState.PROPOSAL and firing_count >= 5:
            new_state = LifecycleState.TESTING
            reason = f"firing_count({firing_count}) >= 5"
        elif current == LifecycleState.TESTING:
            # TESTING can only go to VALIDATED (not directly to PROMOTED)
            if firing_count >= AUTO_PROMOTE_FIRE_THRESHOLD and effectiveness_ratio >= AUTO_PROMOTE_EFFECTIVENESS_RATIO:
                new_state = LifecycleState.VALIDATED
                reason = f"auto: firing_count({firing_count}) >= {AUTO_PROMOTE_FIRE_THRESHOLD} AND effectiveness_ratio({effectiveness_ratio:.2f}) >= {AUTO_PROMOTE_EFFECTIVENESS_RATIO}"
            elif firing_count >= AUTO_PROMOTE_FIRE_THRESHOLD * 2 and effectiveness_ratio >= 0.70:
                new_state = LifecycleState.VALIDATED
                reason = f"auto: firing_count({firing_count}) >= {AUTO_PROMOTE_FIRE_THRESHOLD * 2} AND effectiveness_ratio({effectiveness_ratio:.2f}) >= 0.70"
        elif current == LifecycleState.VALIDATED:
            if firing_count >= AUTO_PROMOTE_FIRE_THRESHOLD and effectiveness_ratio >= AUTO_PROMOTE_EFFECTIVENESS_RATIO:
                new_state = LifecycleState.PROMOTED
                reason = f"auto: firing_count({firing_count}) >= {AUTO_PROMOTE_FIRE_THRESHOLD} AND effectiveness_ratio({effectiveness_ratio:.2f}) >= {AUTO_PROMOTE_EFFECTIVENESS_RATIO}"

        # Demotion path (checked independently, not in the same elif chain)
        # Demotion: step demote OR hard demote (both as independent if-branches,
        # not elif — when both conditions are met, hard demote takes precedence)
        if (
            current in (LifecycleState.TESTING, LifecycleState.VALIDATED, LifecycleState.PROMOTED)
            and new_state is None
        ):
            if firing_count >= AUTO_DEMOTE_FIRE_THRESHOLD and effectiveness_ratio < AUTO_DEMOTE_EFFECTIVENESS_RATIO:
                demote_map = {
                    LifecycleState.PROMOTED: LifecycleState.VALIDATED,
                    LifecycleState.VALIDATED: LifecycleState.TESTING,
                    LifecycleState.TESTING: LifecycleState.PROPOSAL,
                }
                new_state = demote_map.get(current)
                reason = f"auto: firing_count({firing_count}) >= {AUTO_DEMOTE_FIRE_THRESHOLD} AND effectiveness_ratio({effectiveness_ratio:.2f}) < {AUTO_DEMOTE_EFFECTIVENESS_RATIO}"

        # Hard demote: separate if (not elif) so it overrides step demote when both fire
        if (
            current in (LifecycleState.TESTING, LifecycleState.VALIDATED, LifecycleState.PROMOTED)
            and firing_count >= AUTO_PROMOTE_FIRE_THRESHOLD * 3
            and effectiveness_ratio < 0.20
        ):
            new_state = LifecycleState.PROPOSAL
            reason = f"auto: hard demote — firing_count({firing_count}) >= {AUTO_PROMOTE_FIRE_THRESHOLD * 3} AND effectiveness_ratio({effectiveness_ratio:.2f}) < 0.20"

        if new_state is not None and new_state != current:
            if not current.can_transition_to(new_state):
                logger.warning("Rule %s: disallowed transition %s -> %s", rule.id, current.value, new_state.value)
                return None

            transition = LifecycleTransition(
                rule_id=rule.id,
                from_state=current,
                to_state=new_state,
                reason=reason,
                triggered_by=triggered_by,
            )
            self._transitions.append(transition)
            logger.info(
                "Rule %s lifecycle transition: %s -> %s (%s)",
                rule.id, current.value, new_state.value, reason,
            )
            return new_state

        return None

    def can_advance(self, rule: CorrelationRule) -> bool:
        """Check if rule is eligible for lifecycle evaluation."""
        return rule.lifecycle_state not in (
            LifecycleState.RETIRED,
            LifecycleState.PROPOSAL,  # proposals need manual activation
        )

    def last_reason_for(self, rule_id: str) -> str | None:
        """Return the reason recorded for the most recent transition
        of the given rule_id, or None if no transition has been recorded.

        The engine calls this after a successful evaluate() to preserve
        the manager's specific reason string ('hard demote ...',
        'auto: firing_count ... >= ... AND effectiveness_ratio ...',
        etc.) in the SQLite lifecycle_log table. Without this, the
        engine's generic "firing_count=X, eff_ratio=Y" reason overwrites
        the manager's specific reason in the audit trail.
        """
        for transition in reversed(self._transitions):
            if transition.rule_id == rule_id:
                return transition.reason
        return None

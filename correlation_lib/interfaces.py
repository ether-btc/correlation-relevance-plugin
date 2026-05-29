"""Protocol definitions for correlation engine adapters."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from correlation_lib.rules import CorrelationRule
    from correlation_lib.lifecycle import LifecycleState


@runtime_checkable
class RecallBackend(Protocol):
    """Backend for retrieving correlated context by path/key."""

    def fetch(self, path: str) -> str | None:
        """Fetch context for a given path or key.

        Returns the context content as a string, or None if not found.
        """
        ...


@runtime_checkable
class ContextBackend(Protocol):
    """Backend for injecting correlated context into the agent."""

    def inject(self, content: str, source_rule: str, relationship: str) -> None:
        """Inject correlated context into the agent's context stream.

        Args:
            content: The correlated context content to inject.
            source_rule: The rule ID that triggered this injection.
            relationship: The relationship type (e.g., 'constrains', 'supports').
        """
        ...


@runtime_checkable
class RuleProvider(Protocol):
    """Provider that supplies correlation rules."""

    def get_rules(self) -> list["CorrelationRule"]:
        """Return all active correlation rules."""
        ...

    def reload(self) -> None:
        """Reload rules from source (for hot-reload support)."""
        ...


@runtime_checkable
class EffectivenessStore(Protocol):
    """Store for rule effectiveness metrics."""

    def record_fire(self, rule_id: str) -> None:
        """Record that a rule fired."""
        ...

    def record_relevance(self, rule_id: str, is_relevant: bool) -> None:
        """Record whether a rule's correlation was relevant."""
        ...

    def get_stats(self, rule_id: str) -> dict:
        """Return effectiveness stats for a rule."""
        ...

    def get_all_stats(self) -> dict[str, dict]:
        """Return effectiveness stats for all rules."""
        ...

    def update_state(self, rule_id: str, state: "LifecycleState") -> None:
        """Update the tracked lifecycle state for a rule.

        Used by the lifecycle autoloop to persist state transitions.
        """
        ...

    def log_lifecycle(
        self,
        rule_id: str,
        from_state: "LifecycleState",
        to_state: "LifecycleState",
        reason: str,
        triggered_by: str,
    ) -> None:
        """Log a lifecycle state transition."""
        ...

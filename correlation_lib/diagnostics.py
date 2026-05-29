"""Runtime diagnostics for the correlation engine.

Dumped when DIAGNOSTIC=1 env var is set.
Also available via correlation_diagnostics() function.
"""

from __future__ import annotations

import logging
import os
import sys
from typing import Any

from correlation_lib.rule_provider import FileRuleProvider
from correlation_lib.rules import RuleSet
from correlation_lib.tracker import EffectivenessTracker

logger = logging.getLogger(__name__)


def correlation_diagnostics(
    ruleset: RuleSet,
    tracker: EffectivenessTracker,
    rule_provider: FileRuleProvider | None = None,
) -> dict[str, Any]:
    """Return a diagnostics dict for the correlation engine.

    Call this to get a snapshot of engine health:
    - Per-rule firing stats and effectiveness ratios
    - Cache hit rates (if tracked)
    - Circuit breaker state
    - Cascade timing breakdown
    - Rule lifecycle distribution
    """
    all_stats = tracker.get_all_stats()

    # Per-rule breakdown
    rule_stats = []
    for rule in ruleset.get_active_rules():
        stats = all_stats.get(rule.id, None)
        if stats:
            firing_count = stats.firing_count
            eff_ratio = stats.effectiveness_ratio
        else:
            firing_count = 0
            eff_ratio = 0.0

        rule_stats.append({
            "id": rule.id,
            "trigger_context": rule.trigger_context,
            "lifecycle": rule.lifecycle_state.value,
            "confidence": rule.confidence,
            "firing_count": firing_count,
            "effectiveness_ratio": round(eff_ratio, 3),
            "matched_keywords_count": len(rule.trigger_keywords),
            "must_also_fetch_count": len(rule.must_also_fetch),
        })

    # Sort by firing_count descending
    rule_stats.sort(key=lambda x: x["firing_count"], reverse=True)

    # Lifecycle distribution
    lifecycle_dist: dict[str, int] = {}
    for rule in ruleset.rules:
        state = rule.lifecycle_state.value
        lifecycle_dist[state] = lifecycle_dist.get(state, 0) + 1

    # Rule provider state
    provider_info: dict[str, Any] = {}
    if rule_provider:
        provider_info = {
            "rule_file": str(rule_provider.rule_file),
            "watch_enabled": rule_provider.watch_enabled,
            "total_rules": len(ruleset.rules),
            "active_rules": len(ruleset.get_active_rules()),
        }

    return {
        "version": "0.1.0",
        "environment": {
            "DIAGNOSTIC": os.environ.get("DIAGNOSTIC", "not set"),
            "PYTHON": sys.version,
        },
        "rules": {
            "total": len(ruleset.rules),
            "active": len(ruleset.get_active_rules()),
            "retired": len([r for r in ruleset.rules if r.lifecycle_state.value == "retired"]),
            "lifecycle_distribution": lifecycle_dist,
        },
        "provider": provider_info,
        "per_rule_stats": rule_stats,
        "top_fired_rules": rule_stats[:5],
        "tracker": {
            "tracked_rules": len(all_stats),
            "total_firings": sum(s.firing_count for s in all_stats.values()),
        },
    }


def dump_diagnostics(
    ruleset: RuleSet,
    tracker: EffectivenessTracker,
    rule_provider: FileRuleProvider | None = None,
) -> None:
    """Dump diagnostics to stdout (for DIAGNOSTIC=1 mode)."""
    import json

    diag = correlation_diagnostics(ruleset, tracker, rule_provider)
    print("\n" + "=" * 60)
    print("CORRELATION ENGINE DIAGNOSTICS")
    print("=" * 60)
    print(json.dumps(diag, indent=2, default=str))
    print("=" * 60 + "\n")


class DiagnosticsMiddleware:
    """HTTP middleware pattern for DIAGNOSTIC=1 env var auto-dump."""

    def __init__(
        self,
        ruleset: RuleSet,
        tracker: EffectivenessTracker,
        rule_provider: FileRuleProvider | None = None,
    ) -> None:
        self._ruleset = ruleset
        self._tracker = tracker
        self._rule_provider = rule_provider
        self._dumped = False

    def maybe_dump(self) -> None:
        """Dump diagnostics once when DIAGNOSTIC=1."""
        if os.environ.get("DIAGNOSTIC") == "1" and not self._dumped:
            dump_diagnostics(self._ruleset, self._tracker, self._rule_provider)
            self._dumped = True

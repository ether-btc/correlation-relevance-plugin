"""Correlation-lib: agent-agnostic rule-based context enrichment engine.

Q1=A: Fully automated lifecycle advancement
Q2=B: on_task_start (new task detection heuristic)
Q3=C: Configurable hot-reload (default: restart-only)
Q4=A: SQLite standalone effectiveness store

Example:
    from correlation_lib.engine import create_engine
    from correlation_lib_adapters.hermes import HermesRecallBackend, HermesContextBackend

    recall = HermesRecallBackend()
    context = HermesContextBackend()
    engine = create_engine("rules.json", recall_backend=recall, context_backend=context)

    if engine.enricher.is_new_task(user_message):
        result = engine.enricher.on_task_start(user_message)
"""

from correlation_lib.diagnostics import correlation_diagnostics, dump_diagnostics
from correlation_lib.engine import CorrelationEngine, create_engine
from correlation_lib.enricher import Enricher, EnrichmentResult
from correlation_lib.interfaces import (
    ContextBackend,
    EffectivenessStore,
    RecallBackend,
    RuleProvider,
)
from correlation_lib.lifecycle import LifecycleManager, LifecycleTransition
from correlation_lib.matcher import Matcher, MatchResult, get_fired_rules
from correlation_lib.rule_provider import FileRuleProvider
from correlation_lib.rules import (
    CorrelationRule,
    LifecycleState,
    MatchMode,
    RuleSet,
    load_rules_from_file,
    load_rules_from_json,
)
from correlation_lib.tracker import (
    EffectivenessTracker,
    RuleStats,
    SQLiteEffectivenessStore,
)

__all__ = [
    # Engine
    "CorrelationEngine",
    "create_engine",
    # Rules
    "CorrelationRule",
    "RuleSet",
    "LifecycleState",
    "MatchMode",
    "load_rules_from_file",
    "load_rules_from_json",
    # Matching
    "Matcher",
    "MatchResult",
    "get_fired_rules",
    # Enrichment
    "Enricher",
    "EnrichmentResult",
    # Tracking
    "EffectivenessTracker",
    "SQLiteEffectivenessStore",
    "RuleStats",
    # Lifecycle
    "LifecycleManager",
    "LifecycleTransition",
    # Providers
    "FileRuleProvider",
    # Protocols
    "RecallBackend",
    "ContextBackend",
    "RuleProvider",
    "EffectivenessStore",
    # Diagnostics
    "correlation_diagnostics",
    "dump_diagnostics",
]

__version__ = "0.2.0"

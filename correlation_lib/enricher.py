"""Context enrichment orchestrator — coordinates match, recall, and inject.

Q2=B: Fires on on_task_start (new task detection). Prefetch fallback for confidence >= 0.9.
Q3=C: Configurable hot-reload via FileRuleProvider.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

from correlation_lib.interfaces import ContextBackend, RecallBackend
from correlation_lib.matcher import Matcher, MatchResult
from correlation_lib.rules import CorrelationRule, RuleSet
from correlation_lib.tracker import EffectivenessTracker

if TYPE_CHECKING:
    pass
logger = logging.getLogger(__name__)

# Maximum length of task_text accepted by the enrichment pipeline. A user-supplied
# message larger than this is truncated with a warning to prevent DoS via
# O(n) regex work and O(n) memory allocation in the matcher.
TASK_TEXT_MAX_LEN = 65536  # 64 KiB


@dataclass
class EnrichmentResult:
    """Result of context enrichment for a task."""

    task_text: str
    fired_rules: list[tuple[CorrelationRule, MatchResult]]
    injected_count: int
    skipped_count: int
    errors: list[str]

    @property
    def had_errors(self) -> bool:
        return bool(self.errors)


class Enricher:
    """Orchestrates correlation match → recall → inject pipeline.

    Q2=B: on_task_start new-task detection heuristic:
      - User message starts with a verb (first word is verb-like), OR
      - Message is under 15 words AND looks like a command (contains verb + object pattern)
    """

    def __init__(
        self,
        ruleset: RuleSet,
        recall_backend: RecallBackend,
        context_backend: ContextBackend,
        tracker: EffectivenessTracker,
    ) -> None:
        self._ruleset = ruleset
        self._recall = recall_backend
        self._context = context_backend
        self._tracker = tracker
        self._matcher = Matcher(ruleset)

    def on_task_start(
        self,
        task_text: str,
        trigger_context: str | None = None,
    ) -> EnrichmentResult:
        """Enrich context for a new task (Q2=B primary trigger).

        Returns enrichment result with injected contexts and any errors.
        """
        return self._enrich(task_text, trigger_context, min_confidence=0.5)

    def on_prefetch(
        self,
        task_text: str,
        trigger_context: str | None = None,
    ) -> EnrichmentResult:
        """Lightweight enrichment for prefetch fallback (Q2=B fallback, Q2=C lightweight).

        Only fires rules with confidence >= 0.9 — high-stakes correlations only.
        """
        return self._enrich(task_text, trigger_context, min_confidence=0.9)

    def _enrich(
        self,
        task_text: str,
        trigger_context: str | None,
        min_confidence: float,
    ) -> EnrichmentResult:
        """Internal enrichment pipeline."""
        if not task_text or not task_text.strip():
            return EnrichmentResult(
                task_text=task_text or "",
                fired_rules=[],
                injected_count=0,
                skipped_count=0,
                errors=["task_text is empty or None — skipping enrichment"],
            )
        if len(task_text) > TASK_TEXT_MAX_LEN:
            logger.warning(
                "task_text length %d exceeds cap %d — truncating",
                len(task_text), TASK_TEXT_MAX_LEN,
            )
            task_text = task_text[:TASK_TEXT_MAX_LEN]
        errors: list[str] = []
        injected_count = 0
        skipped_count = 0

        # Match task against rules
        results = self._matcher.match(task_text, trigger_context)
        fired: list[tuple[CorrelationRule, MatchResult]] = []

        for result in results:
            if not result.is_match:
                continue
            # Apply minimum confidence threshold
            if result.rule.confidence < min_confidence:
                skipped_count += 1
                continue
            fired.append((result.rule, result))

        # Record firing for all matched rules
        for rule, result in fired:
            self._tracker.record(rule.id)
            logger.debug(
                "Rule %s fired for task (score=%.3f, matched_kws=%s)",
                rule.id, result.combined_score, result.matched_keywords,
            )

        # Recall and inject for each fired rule
        for rule, result in fired:
            for context_path in rule.must_also_fetch:
                content = self._recall.fetch(context_path)
                if content is None:
                    errors.append(f"Rule {rule.id}: context {context_path!r} not found (silently skipped)")
                    logger.warning("Rule %s: must_also_fetch %r not found", rule.id, context_path)
                    continue
                try:
                    self._context.inject(content, source_rule=rule.id, relationship=rule.relationship_type)
                    injected_count += 1
                    logger.debug(
                        "Injected context from rule %s (%s) for path %s",
                        rule.id, rule.relationship_type, context_path,
                    )
                except Exception as exc:
                    errors.append(f"Rule {rule.id}: inject failed for {context_path!r}: {exc}")
                    logger.error("Rule %s: inject failed for %s: %s", rule.id, context_path, exc)

        return EnrichmentResult(
            task_text=task_text,
            fired_rules=fired,
            injected_count=injected_count,
            skipped_count=skipped_count,
            errors=errors,
        )

    @staticmethod
    def is_new_task(message: str) -> bool:
        """Heuristic: is this message a new task directive?

        Q2=B: new task detection heuristic:
        - Message starts with a verb (first word is verb-like), OR
        - Message is under 15 words AND contains verb + object pattern
        """
        if not message or not message.strip():
            return False

        words = message.strip().split()
        if len(words) > 15:
            return False

        # Common task verbs (imperative, with common prefixes)
        task_verbs = {
            "build", "create", "fix", "debug", "analyze", "review", "audit",
            "check", "test", "deploy", "migrate", "setup", "configure",
            "add", "remove", "update", "upgrade", "install", "run", "execute",
            "implement", "design", "refactor", "optimize", "document",
            "investigate", "diagnose", "trace", "profile", "benchmark",
            "reconfigure", "remigrate", "readjust", "reinstall",
        }

        first_word = words[0].lower().rstrip(".,!?")
        if first_word in task_verbs:
            return True

        # Pattern: verb + object (2+ words, first is verb-like, second is a noun)
        noun_suffixes = ("tion", "ment", "ness", "er", "or", "ry", "ity")
        if len(words) >= 2:
            second_word = words[1].lower().rstrip(".,!?")
            if first_word.endswith(("e", "es", "ing")) and len(first_word) > 3:
                # Looks like a verb (e.g., "running", "fixes", "made")
                if second_word.endswith(noun_suffixes) or (second_word and second_word[0].islower()):
                    return True

        return False

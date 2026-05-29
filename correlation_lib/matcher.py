"""Keyword/context matching engine for correlation rules."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

from correlation_lib.rules import CorrelationRule, MatchMode, RuleSet

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class MatchResult:
    """Result of matching a task against a rule."""

    rule: CorrelationRule
    matched_keywords: tuple[str, ...]
    keyword_coverage: float  # 0.0-1.0 fraction of keywords matched
    context_score: float     # 0.0-1.0 trigger_context similarity
    combined_score: float    # 0.0-1.0 weighted combination

    @property
    def is_match(self) -> bool:
        return self.combined_score >= 0.48 and len(self.matched_keywords) > 0


class Matcher:
    """Matches task text against correlation rules."""

    def __init__(self, ruleset: RuleSet) -> None:
        self._ruleset = ruleset

    def match(self, task_text: str, trigger_context: str | None = None) -> list[MatchResult]:
        """Match task text against active rules.

        Args:
            task_text: The task description or user message.
            trigger_context: Optional semantic context hint (e.g., 'config-change').
                           If None, matches against all contexts.

        Returns:
            List of MatchResult sorted by combined_score descending.
        """
        task_lower = task_text.lower()
        task_words = set(re.findall(r'\b\w+\b', task_lower))
        results: list[MatchResult] = []

        for rule in self._ruleset.get_active_rules():
            # Skip if context hint provided and doesn't match
            if trigger_context and rule.trigger_context != trigger_context:
                continue

            matched_kws, kw_coverage = self._match_keywords(rule, task_words, task_lower, task_text)

            # Compute context similarity
            ctx_score = self._context_score(rule.trigger_context, task_lower, trigger_context)

    # Combined score: weighted by rule confidence
            # keyword_coverage contributes 65%, context_score 35%
            combined = (kw_coverage * 0.65 + ctx_score * 0.35) * rule.confidence

            result = MatchResult(
                rule=rule,
                matched_keywords=tuple(matched_kws),
                keyword_coverage=kw_coverage,
                context_score=ctx_score,
                combined_score=combined,
            )
            results.append(result)

        # Sort by combined score descending
        results.sort(key=lambda r: r.combined_score, reverse=True)
        return results

    def _match_keywords(
        self,
        rule: CorrelationRule,
        task_words: set[str],
        task_lower: str,
        task_text: str,
    ) -> tuple[list[str], float]:
        """Match keywords against task. Returns (matched_keywords, coverage)."""
        matched: list[str] = []

        if rule.match_mode == MatchMode.STRICT:
            # Word-boundary matching
            for kw in rule.trigger_keywords:
                pattern = r'\b' + re.escape(kw.lower()) + r'\b'
                if re.search(pattern, task_lower):
                    matched.append(kw)
        elif rule.match_mode == MatchMode.LENIENT:
            # Substring matching
            for kw in rule.trigger_keywords:
                if kw.lower() in task_lower:
                    matched.append(kw)
        else:
            # AUTO: word-boundary first, substring fallback
            for kw in rule.trigger_keywords:
                pattern = r'\b' + re.escape(kw.lower()) + r'\b'
                if re.search(pattern, task_lower):
                    matched.append(kw)
                elif kw.lower() in task_lower:
                    matched.append(kw)

        coverage = len(matched) / len(rule.trigger_keywords) if rule.trigger_keywords else 0.0
        return matched, coverage

    def _context_score(
        self,
        rule_context: str,
        task_lower: str,
        trigger_hint: str | None,
    ) -> float:
        """Compute context similarity score 0.0-1.0.

        Returns 1.0 if:
        - trigger_hint exactly matches rule_context, OR
        - task_lower contains significant words from rule_context

        Returns 0.0 if:
        - rule_context is in a different semantic domain (e.g., 'config-change' vs 'error-debug')
        """
        # Exact hint match — highest confidence
        if trigger_hint and trigger_hint == rule_context:
            return 1.0

        # Semantic overlap: count shared significant words
        ctx_words = set(re.findall(r'\b\w+\b', rule_context.lower()))
        # Filter out very short/common words
        ctx_words -= {"a", "an", "the", "to", "of", "in", "on", "at", "for", "by"}
        if not ctx_words:
            return 0.5  # Neutral for empty context

        overlap = ctx_words & set(task_lower.split())
        if overlap:
            return 0.5 + 0.5 * (len(overlap) / len(ctx_words))
        return 0.5  # Neutral when no semantic overlap — don't penalize cross-domain rules


def filter_high_confidence(results: list[MatchResult], threshold: float = 0.7) -> list[MatchResult]:
    """Filter results to only those above a confidence threshold."""
    return [r for r in results if r.is_match and r.combined_score >= threshold]


def get_fired_rules(
    task_text: str,
    ruleset: RuleSet,
    trigger_context: str | None = None,
    min_score: float = 0.5,
) -> list[tuple[CorrelationRule, MatchResult]]:
    """Convenience function: get rules that fire for a task.

    Returns (rule, match_result) pairs for rules that match above min_score.
    """
    matcher = Matcher(ruleset)
    results = matcher.match(task_text, trigger_context)
    return [(r.rule, r) for r in results if r.is_match and r.combined_score >= min_score]

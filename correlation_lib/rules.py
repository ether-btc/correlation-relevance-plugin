"""Correlation rule schema, validation, and lifecycle management."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any


class LifecycleState(Enum):
    """Rule lifecycle states, ordered from proposal to retirement."""

    PROPOSAL = "proposal"
    TESTING = "testing"
    VALIDATED = "validated"
    PROMOTED = "promoted"
    RETIRED = "retired"

    def can_transition_to(self, next_state: LifecycleState) -> bool:
        """Validate allowed lifecycle transitions."""
        allowed = {
            LifecycleState.PROPOSAL: {LifecycleState.TESTING},
            LifecycleState.TESTING: {LifecycleState.VALIDATED, LifecycleState.PROPOSAL},
            LifecycleState.VALIDATED: {LifecycleState.PROMOTED, LifecycleState.TESTING},
            LifecycleState.PROMOTED: {LifecycleState.RETIRED, LifecycleState.VALIDATED, LifecycleState.PROPOSAL},
            LifecycleState.RETIRED: set(),
        }
        return next_state in allowed.get(self, set())


class MatchMode(Enum):
    """Rule matching mode."""

    AUTO = "auto"      # keyword + context coverage
    STRICT = "strict"  # word-boundary keyword
    LENIENT = "lenient"  # fuzzy fallback


RELATIONSHIP_TYPES = {
    "constrains",
    "supports",
    "diagnosed_by",
    "requires",
    "conflicts_with",
    "supersedes",
    "related_to",
}

# Confidence calibration guidelines (from OpenClaw production)
CONFIDENCE_CALIBRATION = {
    (0.95, 0.99): "Catastrophic cost if wrong — config changes, gateway restarts",
    (0.85, 0.90): "Reliable patterns — backup ops, error debugging",
    (0.70, 0.80): "Useful but some false-positive risk — session recovery, git ops",
}


@dataclass(frozen=True)
class CorrelationRule:
    """A correlation rule — defines when to fetch additional context."""

    id: str
    trigger_context: str
    trigger_keywords: tuple[str, ...]
    must_also_fetch: tuple[str, ...]
    relationship_type: str
    confidence: float
    match_mode: MatchMode = MatchMode.AUTO
    lifecycle_state: LifecycleState = LifecycleState.PROPOSAL
    learned_from: str | None = None
    description: str | None = None

    def __post_init__(self) -> None:
        if self.relationship_type not in RELATIONSHIP_TYPES:
            raise ValueError(
                f"Invalid relationship_type {self.relationship_type!r}. "
                f"Must be one of: {RELATIONSHIP_TYPES}"
            )
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError(f"confidence must be 0.0-1.0, got {self.confidence}")

    def validate_keywords(self) -> list[str]:
        """Check for problematic keyword patterns. Returns list of warnings."""
        warnings: list[str] = []
        if len(self.trigger_keywords) == 1:
            word = self.trigger_keywords[0].lower()
            if len(word) < 4:
                warnings.append(
                    f"Rule {self.id}: single short keyword {word!r} may cause over-correlation"
                )
        common_words = {"the", "a", "an", "is", "are", "to", "in", "on", "at", "for", "by"}
        overlap = set(self.trigger_keywords) & common_words
        if overlap:
            warnings.append(
                f"Rule {self.id}: common words in keywords: {overlap} — may fire on everything"
            )
        return warnings


# JSON schema for reference (not enforced via jsonschema library to keep zero deps)
RULE_SCHEMA = {
    "$schema": "http://json-schema.org/draft-07/schema#",
    "type": "object",
    "required": ["id", "trigger_context", "trigger_keywords", "must_also_fetch", "relationship_type", "confidence"],
    "additionalProperties": False,
    "properties": {
        "id": {"type": "string", "pattern": "^[a-z][a-z0-9_-]*$"},
        "trigger_context": {"type": "string"},
        "trigger_keywords": {"type": "array", "items": {"type": "string"}, "minItems": 1},
        "must_also_fetch": {"type": "array", "items": {"type": "string"}, "minItems": 1},
        "relationship_type": {"type": "string", "enum": list(RELATIONSHIP_TYPES)},
        "confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
        "match_mode": {"type": "string", "enum": [m.value for m in MatchMode]},
        "lifecycle": {
            "type": "object",
            "properties": {"state": {"type": "string", "enum": [s.value for s in LifecycleState]}},
            "required": ["state"],
        },
        "learned_from": {"type": "string"},
        "description": {"type": "string"},
    },
}


@dataclass
class RuleSet:
    """A collection of correlation rules with validation."""

    rules: list[CorrelationRule] = field(default_factory=list)
    _keyword_index: dict[str, set[int]] = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        # Build keyword index for fast lookup
        kw_index: dict[str, set[int]] = {}
        for i, rule in enumerate(self.rules):
            for kw in rule.trigger_keywords:
                kw_index.setdefault(kw.lower(), set()).add(i)
        object.__setattr__(self, "_keyword_index", kw_index)

    def add(self, rule: CorrelationRule) -> list[str]:
        """Add a rule, returning validation warnings."""
        warnings = rule.validate_keywords()
        rules = self.rules.copy()
        rules.append(rule)
        object.__setattr__(self, "rules", rules)
        # Rebuild index
        kw_index: dict[str, set[int]] = {}
        for i, r in enumerate(self.rules):
            for kw in r.trigger_keywords:
                kw_index.setdefault(kw.lower(), set()).add(i)
        object.__setattr__(self, "_keyword_index", kw_index)
        return warnings

    def get_by_id(self, rule_id: str) -> CorrelationRule | None:
        for rule in self.rules:
            if rule.id == rule_id:
                return rule
        return None

    def get_for_context(self, context: str) -> list[CorrelationRule]:
        """Get rules whose trigger_context matches the given context."""
        return [r for r in self.rules if r.trigger_context == context]

    def get_active_rules(self) -> list[CorrelationRule]:
        """Return rules not in retired state."""
        return [r for r in self.rules if r.lifecycle_state != LifecycleState.RETIRED]


def load_rules_from_json(data: list[dict[str, Any]]) -> RuleSet:
    """Load rules from JSON-serializable list of dicts."""
    required_fields = ("id", "trigger_context", "trigger_keywords",
                       "must_also_fetch", "relationship_type", "confidence")
    rules: list[CorrelationRule] = []
    for idx, item in enumerate(data):
        rule_id = item.get("id", f"<item {idx}>")
        for field in required_fields:
            if field not in item:
                raise ValueError(
                    f"Rule '{rule_id}' (index {idx}): missing required field '{field}'. "
                    f"Required fields: {required_fields}"
                )

        # Validate keyword arrays are non-empty
        for field in ("trigger_keywords", "must_also_fetch"):
            val = item[field]
            if not isinstance(val, (list, tuple)) or len(val) == 0:
                raise ValueError(
                    f"Rule '{rule_id}': '{field}' must be a non-empty list; "
                    f"got {type(val).__name__} of length {len(val) if isinstance(val, (list, tuple)) else 'N/A'}"
                )

        # Clamp and validate confidence
        try:
            confidence = float(item["confidence"])
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"Rule '{rule_id}': 'confidence' must be a number; got {type(item['confidence']).__name__}: {exc}"
            ) from exc
        confidence = max(0.0, min(1.0, confidence))

        # Validate relationship_type
        rel_type = str(item["relationship_type"])
        if rel_type not in RELATIONSHIP_TYPES:
            raise ValueError(
                f"Rule '{rule_id}': 'relationship_type' must be one of {list(RELATIONSHIP_TYPES)}; "
                f"got '{rel_type}'"
            )

        state_str = (item.get("lifecycle") or {}).get("state", "proposal")
        try:
            lifecycle_state = LifecycleState(state_str)
        except ValueError as exc:
            valid_states = [s.value for s in LifecycleState]
            raise ValueError(
                f"Rule '{rule_id}': 'lifecycle.state' must be one of {valid_states}; got '{state_str}'"
            ) from exc

        match_mode_str = item.get("match_mode", "auto")
        try:
            match_mode = MatchMode(match_mode_str)
        except ValueError:
            raise ValueError(
                f"Rule '{rule_id}': 'match_mode' must be one of {[m.value for m in MatchMode]}; "
                f"got '{match_mode_str}'"
            ) from exc

        rules.append(
            CorrelationRule(
                id=str(item["id"]),
                trigger_context=str(item["trigger_context"]),
                trigger_keywords=tuple(item["trigger_keywords"]),
                must_also_fetch=tuple(item["must_also_fetch"]),
                relationship_type=rel_type,
                confidence=confidence,
                match_mode=match_mode,
                lifecycle_state=lifecycle_state,
                learned_from=item.get("learned_from"),
                description=item.get("description"),
            )
        )
    return RuleSet(rules)


def load_rules_from_file(path: str | Path) -> RuleSet:
    """Load rules from a JSON file."""
    with open(path) as f:
        data = json.load(f)
    if not isinstance(data, list):
        raise ValueError(f"Rule file must contain a JSON array, got {type(data).__name__}")
    return load_rules_from_json(data)

"""correlation_lookup — surface the context you forgot to fetch, before you act.

Wraps the existing correlation-lib engine as ONE Hermes tool. A rule file lists triggers
(keywords + context) and what must also be fetched when a trigger fires; the tool scores the
task the agent is about to do against those rules and returns the hints.

Rules file: $HERMES_CORRELATION_RULES, else ~/.hermes/correlation-rules.json (optional).
Read-only: nothing is written, no network, no model call.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

DEFAULT_RULES = Path.home() / ".hermes" / "correlation-rules.json"
MAX_LIMIT = 50


def rules_path() -> Path:
    """Rule file location, resolved per call so a config change needs no restart."""
    return Path(os.environ.get("HERMES_CORRELATION_RULES") or DEFAULT_RULES).expanduser()


def correlation_lookup(args: Any, **kwargs) -> str:
    """Match a task against the correlation rules. Always returns a JSON string."""
    try:
        args = args or {}
        task_text = str(args.get("task_text") or "").strip()
        if not task_text:
            return json.dumps({"error": "task_text is required", "success": False})

        try:
            limit = int(args.get("limit") or 5)
        except (TypeError, ValueError):
            limit = 5
        limit = max(1, min(limit, MAX_LIMIT))
        trigger_context = args.get("trigger_context") or None

        path = rules_path()
        if not path.exists():
            # A fresh install with no rules file is a normal state, not a failure.
            return json.dumps({"rules_configured": False, "matched": [], "count": 0})

        from correlation_lib import Matcher, load_rules_from_file

        matcher = Matcher(load_rules_from_file(path))
        hits = [r for r in matcher.match(task_text, trigger_context) if r.is_match][:limit]

        return json.dumps(
            {
                "rules_configured": True,
                "matched": [
                    {
                        "rule_id": r.rule.id,
                        "relationship_type": r.rule.relationship_type,
                        "must_also_fetch": list(r.rule.must_also_fetch),
                        "confidence": r.rule.confidence,
                        "score": round(r.combined_score, 3),
                        "matched_keywords": list(r.matched_keywords),
                    }
                    for r in hits
                ],
                "count": len(hits),
            }
        )
    except Exception as exc:  # noqa: BLE001 - a tool call must never raise
        return json.dumps({"error": f"{type(exc).__name__}: {exc}", "success": False})


def register(ctx) -> None:
    """Register the single tool. No hooks, no commands."""
    ctx.register_tool(
        name="correlation_lookup",
        toolset="correlation",
        schema={
            "type": "object",
            "properties": {
                "task_text": {
                    "type": "string",
                    "description": "What you are about to do, in your own words (e.g. 'modify the gateway config and restart it').",
                },
                "trigger_context": {
                    "type": "string",
                    "description": "Optional context hint that must equal a rule's trigger_context (e.g. 'config-change'). Omit to match every context.",
                },
                "limit": {
                    "type": "integer",
                    "description": "Max hints to return (default 5, max 50).",
                    "default": 5,
                },
            },
            "required": ["task_text"],
        },
        handler=correlation_lookup,
        check_fn=lambda: True,
        description=(
            "Look up your correlation rules for the task you are about to do and return the "
            "context you should also fetch (e.g. before a config change: the backup location "
            "and the rollback runbook). Rules live in ~/.hermes/correlation-rules.json."
        ),
        emoji="🔗",
    )

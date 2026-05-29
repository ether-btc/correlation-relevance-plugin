"""Hermes adapter — wires correlation-lib into Hermes Agent.

Provides:
- HermesRecallBackend: fetches context paths from Mnemosyne memory
- HermesContextBackend: injects correlated context into agent context
- CorrelationMemoryProvider: MemoryProvider plugin for Hermes
"""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING, Any

from correlation_lib.interfaces import ContextBackend, RecallBackend

if TYPE_CHECKING:
    from mnemosyne import Mnemosyne


logger = logging.getLogger(__name__)


# Context paths used in must_also_fetch are stored as memory search queries.
# This pattern maps path-like strings to Mnemosyne recall queries.
def _path_to_query(path: str) -> str:
    """Convert a context path to a Mnemosyne recall query.

    Paths are dot-separated or slash-separated identifiers.
    Examples:
        'backup-location' -> 'backup location'
        'rollback-instructions' -> 'rollback instructions'
        'config/backup' -> 'config backup'
    """
    return re.sub(r'[-_/]', ' ', path)


class HermesRecallBackend(RecallBackend):
    """Recall backend that fetches context paths via Mnemosyne search.

    must_also_fetch entries are treated as search queries against Mnemosyne's
    working memory and episodic memory.
    """

    def __init__(self, mnemosyne: "Mnemosyne | None" = None) -> None:
        self._mnemosyne = mnemosyne

    def set_mnemosyne(self, mnemosyne: "Mnemosyne") -> None:
        object.__setattr__(self, "_mnemosyne", mnemosyne)

    def fetch(self, path: str) -> str | None:
        """Fetch context for a path by querying Mnemosyne memory.

        Returns the best recall result as a formatted string, or None.
        """
        if self._mnemosyne is None:
            logger.debug("HermesRecallBackend: no Mnemosyne instance — skipping fetch %r", path)
            return None

        query = _path_to_query(path)

        try:
            # Use recall to get relevant memories
            results = self._mnemosyne.remember(query, limit=3)
            if not results:
                # Try get_context for recent memories as fallback
                ctx_results = self._mnemosyne.get_context(limit=5)
                # Check if any context entries match the path
                for entry in ctx_results:
                    if path.lower() in entry.lower() or query.lower() in entry.lower():
                        return entry
                return None

            # Format results as context
            formatted = []
            for i, result in enumerate(results, 1):
                content = result.get("content", "")
                if content:
                    formatted.append(f"[{i}] {content}")

            if formatted:
                return "\n\n".join(formatted)
            return None
        except Exception as exc:
            logger.error("HermesRecallBackend: recall failed for %r: %s", path, exc)
            return None


class HermesContextBackend(ContextBackend):
    """Context backend that injects correlated context into Hermes.

    In Hermes, context injection is done by appending to the agent's
    injected context list, which gets prepended to the system prompt
    or inserted as a context block before the next LLM call.
    """

    def __init__(self) -> None:
        self._injected: list[dict[str, Any]] = []

    def inject(self, content: str, source_rule: str, relationship: str) -> None:
        """Append correlated context to the injection buffer."""
        entry = {
            "content": content,
            "source_rule": source_rule,
            "relationship": relationship,
        }
        self._injected.append(entry)
        logger.debug(
            "Injected context from rule %s (relationship=%s): %s",
            source_rule, relationship, content[:100] + "..." if len(content) > 100 else content,
        )

    def get_injected(self) -> list[dict[str, Any]]:
        """Return all injected context entries."""
        return list(self._injected)

    def clear(self) -> None:
        """Clear the injection buffer (called after each turn)."""
        self._injected.clear()

    def format_injected(self) -> str:
        """Format injected context as a readable string for system prompt."""
        if not self._injected:
            return ""
        lines = ["## Correlated Context"]
        for entry in self._injected:
            lines.append(f"\n**Rule:** `{entry['source_rule']}`")
            lines.append(f"**Relationship:** {entry['relationship']}")
            lines.append(f"\n{entry['content']}")
        return "\n".join(lines)

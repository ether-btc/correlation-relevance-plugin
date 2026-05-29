"""CorrelationMemoryProvider — wires correlation-lib into Hermes Agent memory system.

Q2=B: on_task_start fires correlation on new task detection (Enricher.is_new_task).
Q2 fallback: prefetch fires only high-confidence (>=0.9) correlations.

Usage in ~/.hermes/config.yaml:
  memory:
    provider: correlation
    correlation:
      rule_file: ~/.hermes/correlation-rules.json
      watch_enabled: false
      db_path: ~/.hermes/correlation-effectiveness.db
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from agent.memory_provider import MemoryProvider
else:
    # Runtime import — 'agent' is available when Hermes loads this as a plugin.
    # Tests that import without hermes-agent must mock MemoryProvider directly.
    try:
        from agent.memory_provider import MemoryProvider
    except ImportError:
        # hermes-agent not installed — use a stub base for type-checking only.
        # Subclass must override all abstract methods in this case.
        class MemoryProvider:  # type: ignore[no-redef]
            pass

from correlation_lib import (
    Enricher,
    create_engine,
)
from correlation_lib_adapters.hermes.backends import (
    HermesContextBackend,
    HermesRecallBackend,
)

logger = logging.getLogger(__name__)

# Config keys
CONFIG_RULE_FILE = "rule_file"
CONFIG_WATCH_ENABLED = "watch_enabled"
CONFIG_DB_PATH = "db_path"


class CorrelationMemoryProvider(MemoryProvider):
    """Memory provider that adds rule-based correlation enrichment.

    Integrates into Hermes via the MemoryProvider plugin system.
    Fires on new-task detection (Q2=B primary) and prefetch (Q2 fallback).

    Config (in memory.provider.config):
      rule_file: str           # Path to JSON rule file
      watch_enabled: bool      # Hot-reload rules on file change (default: false)
      db_path: str            # SQLite effectiveness DB path (default: ~/.hermes/correlation-effectiveness.db)
    """

    @property
    def name(self) -> str:
        return "correlation"

    def __init__(self) -> None:
        self._engine = None
        self._recall = HermesRecallBackend()
        self._context = HermesContextBackend()
        self._turn_count = 0

    # -- MemoryProvider implementation -----------------------------------------

    def is_available(self) -> bool:
        """Check if correlation rules are configured."""
        # Provider is available if config has a rule file
        return True  # Always available; engine handles missing rule file gracefully

    def initialize(self, session_id: str, **kwargs) -> None:
        """Initialize the correlation engine.

        Reads config from memory.provider.correlation in ~/.hermes/config.yaml.
        """
        try:
            from hermes_constants import get_hermes_home
        except ImportError:
            def get_hermes_home() -> Path:
                return Path.home()

        hermes_home = kwargs.get("hermes_home") or str(get_hermes_home())

        # Check for correlation_rules_file kwarg override
        correlation_rules_file = kwargs.get("correlation_rules_file")

        # Load correlation config
        import yaml
        config_path = Path(hermes_home) / "config.yaml"
        rule_file = Path(hermes_home) / "correlation-rules.json"
        watch_enabled = False
        db_path = Path(hermes_home) / "correlation-effectiveness.db"

        if config_path.exists():
            with open(config_path) as f:
                config = yaml.safe_load(f) or {}
            corr_cfg = (config.get("memory", {}).get("provider", {}).get("correlation") or {})
            rule_file_str = corr_cfg.get(CONFIG_RULE_FILE)
            if rule_file_str:
                rule_file = Path(rule_file_str).expanduser()
            watch_enabled = bool(corr_cfg.get(CONFIG_WATCH_ENABLED, False))
            db_path_str = corr_cfg.get(CONFIG_DB_PATH)
            if db_path_str:
                db_path = Path(db_path_str).expanduser()

        # Apply correlation_rules_file kwarg override if provided
        if correlation_rules_file:
            rule_file = Path(correlation_rules_file).expanduser()

        # Create engine
        try:
            self._engine = create_engine(
                rule_file=str(rule_file) if rule_file.exists() else None,
                watch_enabled=watch_enabled,
                db_path=str(db_path),
                recall_backend=self._recall,
                context_backend=self._context,
            )
            # Wire Mnemosyne if available
            if "mnemosyne" in kwargs:
                self._recall.set_mnemosyne(kwargs["mnemosyne"])
            logger.info(
                "CorrelationMemoryProvider initialized: rule_file=%s watch=%s db=%s",
                rule_file, watch_enabled, db_path,
            )
        except Exception as exc:
            logger.error("Failed to initialize correlation engine: %s", exc)
            logger.debug("Correlation engine init details", exc_info=True)
            self._engine = None

    def system_prompt_block(self) -> str:
        """Return static info about the correlation engine for system prompt."""
        if not self._engine or not self._engine.enricher:
            return ""
        # Inject correlated context from previous turns
        return self._context.format_injected()

    def prefetch(self, query: str, *, session_id: str = "") -> str:
        """Q2 fallback: lightweight correlation for prefetch.

        Only fires high-confidence (>=0.9) rules — high-stakes correlations only.
        Runs on every LLM call as fallback.
        """
        if not self._engine or not self._engine.enricher:
            return ""
        if not query or not query.strip():
            return ""

        try:
            result = self._engine.enricher.on_prefetch(query)
            if result.had_errors:
                for err in result.errors:
                    logger.warning("Correlation prefetch error: %s", err)
            return self._context.format_injected()
        except Exception as exc:
            logger.error("Correlation prefetch failed: %s", exc)
            return ""

    def on_turn_start(self, turn_number: int, message: str, **kwargs) -> None:
        """Q2=B primary trigger: new-task correlation.

        Fires only when message looks like a new task directive.
        Clears injection buffer from previous turn.
        """
        if not self._engine or not self._engine.enricher:
            return

        self._turn_count += 1
        self._context.clear()  # Clear previous turn's injections

        if not Enricher.is_new_task(message):
            return

        try:
            result = self._engine.enricher.on_task_start(message)
            if result.had_errors:
                for err in result.errors:
                    logger.warning("Correlation turn error: %s", err)
            if result.injected_count > 0:
                logger.info(
                    "Correlation fired on turn %d: %d rules, %d injections",
                    self._turn_count, len(result.fired_rules), result.injected_count,
                )
        except Exception as exc:
            logger.error("Correlation on_turn_start failed: %s", exc)

        # Maybe dump diagnostics
        if os.environ.get("DIAGNOSTIC") == "1":
            from correlation_lib.diagnostics import dump_diagnostics
            if self._engine.rule_provider:
                ruleset = self._engine.rule_provider.get_rules()
                dump_diagnostics(ruleset, self._engine.tracker, self._engine.rule_provider)

    def sync_turn(self, user_content: str, assistant_content: str, *, session_id: str = "") -> None:
        """After each turn: record effectiveness feedback (Q1=A: automated).

        For now, we record the fire without relevance judgment.
        Relevance feedback comes later via explicit tool call or signal.
        """
        pass  # Fire events already recorded in on_turn_start / prefetch

    def shutdown(self) -> None:
        """Clean shutdown."""
        self._context.clear()
        self._engine = None

    def get_tool_schemas(self) -> list:
        """Return no tool schemas — correlation is a context-only provider."""
        return []

    def handle_tool_call(self, tool_name: str, args, **kwargs):
        """No tools to handle."""
        return None

    def get_config_schema(self) -> list[dict[str, Any]]:
        return [
            {
                "key": CONFIG_RULE_FILE,
                "description": "Path to JSON rule file",
                "required": False,
                "default": "~/.hermes/correlation-rules.json",
            },
            {
                "key": CONFIG_WATCH_ENABLED,
                "description": "Hot-reload rules when file changes (default: false)",
                "required": False,
                "default": False,
            },
            {
                "key": CONFIG_DB_PATH,
                "description": "Path to SQLite effectiveness DB",
                "required": False,
                "default": "~/.hermes/correlation-effectiveness.db",
            },
        ]

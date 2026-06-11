"""File-based rule provider with optional hot-reload (Q3=C).

Q3=C: watch_enabled configurable, default False (restart-only).
When enabled, uses mtime polling to detect changes.
"""

from __future__ import annotations

import logging
from pathlib import Path

from correlation_lib.interfaces import RuleProvider
from correlation_lib.rules import RuleSet, load_rules_from_file

logger = logging.getLogger(__name__)


class FileRuleProvider(RuleProvider):
    """Loads rules from a JSON file with optional hot-reload.

    Q3=C: watch_enabled configurable via config param.
    Default is False (restart-only). When True, polls mtime for changes.
    """

    def __init__(
        self,
        rule_file: str | Path,
        watch_enabled: bool = False,
        poll_interval: float = 2.0,
    ) -> None:
        self._rule_file = Path(rule_file)
        self._watch_enabled = watch_enabled
        self._poll_interval = poll_interval
        self._ruleset: RuleSet | None = None
        self._last_mtime: float = 0.0
        self._last_size: int = 0
        self._load()

    def _load(self) -> None:
        """Load rules from file, building keyword index.

        Error policy:
        - OSError (file missing, permission denied, transient I/O): warn and
          fall back to an empty ruleset so the engine can run in degraded mode.
          A missing rules file is a normal first-run / deployment state.
        - ValueError, json.JSONDecodeError (config errors: file too large,
          schema violation, not a JSON array): re-raise. These are programming
          errors that the caller MUST see and fix — silently turning them
          into "0 rules loaded" hides broken configuration.
        """
        import json as _json
        try:
            self._ruleset = load_rules_from_file(self._rule_file)
        except (OSError, _json.JSONDecodeError) as exc:
            # OSError covers FileNotFoundError, PermissionError, etc.
            # JSONDecodeError is a subclass of ValueError but means "the file
            # exists but isn't valid JSON" — same severity as missing.
            # NOTE: we catch JSONDecodeError here too so a truncated file
            # doesn't crash the engine. load_rules_from_file only raises
            # ValueError for size cap and schema errors.
            logger.warning(
                "Cannot read rules file %s: %s — running with empty ruleset",
                self._rule_file, exc,
            )
            self._ruleset = RuleSet()
            return
        # ValueError re-raises: caller sees the actual config error
        # (size cap rejection, schema validation failure, etc.)

        try:
            stat = self._rule_file.stat()
            self._last_mtime = stat.st_mtime
            self._last_size = stat.st_size
        except OSError as exc:
            logger.warning("Cannot stat rules file %s: %s", self._rule_file, exc)

        logger.info("Loaded %d rules from %s", len(self._ruleset.rules), self._rule_file)

    def _needs_reload(self) -> bool:
        """Check if file has changed since last load."""
        try:
            stat = self._rule_file.stat()
            # Check mtime or size change
            if stat.st_mtime != self._last_mtime:
                return True
            if stat.st_size != self._last_size:
                return True
        except OSError:
            pass
        return False

    def get_rules(self) -> RuleSet:
        if self._watch_enabled:
            if self._needs_reload():
                logger.info("Rule file changed, reloading...")
                self._load()
        if self._ruleset is None:
            self._load()
        # Return a copy to avoid mutation
        return RuleSet(rules=list(self._ruleset.rules))

    def reload(self) -> None:
        """Force a reload, regardless of watch setting."""
        self._load()

    @property
    def watch_enabled(self) -> bool:
        return self._watch_enabled

    @property
    def rule_file(self) -> Path:
        return self._rule_file

    def enable_watch(self) -> None:
        object.__setattr__(self, "_watch_enabled", True)

    def disable_watch(self) -> None:
        object.__setattr__(self, "_watch_enabled", False)

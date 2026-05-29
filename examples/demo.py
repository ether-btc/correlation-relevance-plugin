#!/usr/bin/env python3
"""Demo: correlation-lib engine with mock backends.

This demonstrates how the correlation engine works in isolation,
without requiring a live Hermes Agent or Mnemosyne instance.

Run: python examples/demo.py
"""

from pathlib import Path

from correlation_lib import (
    Enricher,
    create_engine,
)
from correlation_lib.interfaces import ContextBackend, RecallBackend

# --------------------------------------------------------------------------- #
# Mock backends (replace with HermesRecallBackend / HermesContextBackend)
# --------------------------------------------------------------------------- #

class MockRecallBackend(RecallBackend):
    """Returns canned context for demo purposes."""

    _contexts: dict[str, str] = {
        "backup-location": (
            "Backup is stored at: /backup/daily on USB drive (mounted at /mnt/usb-backup). "
            "Last successful backup: 2026-05-16 23:00 UTC. Retention: 30 days."
        ),
        "rollback-instructions": (
            "To rollback: (1) stop service: sudo systemctl stop hermes "
            "(2) restore: cp /backup/daily/config.yaml ~/.hermes/config.yaml "
            "(3) restart: sudo systemctl start hermes"
        ),
        "recent-incidents": (
            "2026-05-15: Database lock contention caused 2s latency spike. "
            "Fixed by adding index on sessions(updated_at). "
            "2026-05-14: Memory leak in mnemosyne consolidation. "
            "Fixed by upgrading to v2.8.1."
        ),
        "error-patterns": (
            "Common patterns: (1) PermissionError on ~/.hermes paths — "
            "hermes runs as different user than file owner. "
            "(2) sqlite3.OperationalError: database is locked — "
            "concurrent writes from multiple processes."
        ),
        "backup-verification": (
            "Before migration: run 'SELECT COUNT(*) FROM users' to get row count. "
            "After migration: verify count matches. "
            "Check for orphaned records in related tables."
        ),
        "rollback-runbook": (
            "Rollback procedure: (1) Stop service: sudo systemctl stop hermes "
            "(2) Restore last backup: cp /backup/daily/users_backup.sql ~/.hermes/ "
            "(3) Run: psql hermes < ~/.hermes/users_backup.sql "
            "(4) Restart: sudo systemctl start hermes"
        ),
        "memory-history": (
            "Memory usage history (last 7 days): "
            "2026-05-16: 68% used, 2026-05-15: 72% used (peak), "
            "2026-05-14: 45% used (after restart). "
            "Baseline: 55-65% during idle."
        ),
        "gc-logs": (
            "GC log summary: No Full GC events in 30 days. "
            "Minor GC runs every ~45min, avg 12ms pause. "
            "Last OOM: 2026-05-14 (mnemosyne consolidation, fixed in v2.8.1)."
        ),
    }

    def fetch(self, path: str) -> str | None:
        return self._contexts.get(path)


class MockContextBackend(ContextBackend):
    """Collects injections for display."""

    def __init__(self) -> None:
        self._injected: list[dict] = []

    def inject(self, content: str, source_rule: str, relationship: str) -> None:
        self._injected.append({
            "content": content,
            "source_rule": source_rule,
            "relationship": relationship,
        })

    def get_injected(self) -> list[dict]:
        return list(self._injected)

    def clear(self) -> None:
        self._injected.clear()

    def format_injected(self) -> str:
        if not self._injected:
            return ""
        lines = ["## Correlated Context\n"]
        for entry in self._injected:
            lines.append(f"**Rule:** `{entry['source_rule']}`")
            lines.append(f"**Relationship:** {entry['relationship']}")
            lines.append(f"\n{entry['content']}\n")
        return "\n".join(lines)


# --------------------------------------------------------------------------- #
# Demo scenarios
# --------------------------------------------------------------------------- #

def main() -> None:
    example_rules_path = Path(__file__).parent / "example_rules.json"

    recall = MockRecallBackend()
    context = MockContextBackend()

    engine = create_engine(
        rule_file=str(example_rules_path),
        recall_backend=recall,
        context_backend=context,
    )

    scenarios = [
        "Reconfigure the gateway settings",
        "Debug the database error crash",
        "Migrate the users table schema",
        "Check current memory usage",
    ]

    for msg in scenarios:
        context.clear()
        print(f"\n{'='*60}")
        print(f"USER: {msg}")
        print("-" * 60)

        if not Enricher.is_new_task(msg):
            print("(Not detected as new task — skipping)")
            continue

        result = engine.enricher.on_task_start(msg)
        if result.had_errors:
            for err in result.errors:
                print(f"ERROR: {err}")
        if result.injected_count == 0:
            print("(No rules fired)")
        else:
            fired_ids = [r.id for r, _ in result.fired_rules]
            print(f"RULES FIRED: {fired_ids}")
            print(f"INJECTIONS: {result.injected_count}")
            print(context.format_injected())

    # Summary
    print(f"\n{'='*60}")
    print("ENGINE STATS:")
    all_stats = engine.tracker.get_all_stats()
    for rule_id, stats in all_stats.items():
        print(f"  {rule_id}: fires={stats.firing_count}, "
              f"eff_ratio={stats.effectiveness_ratio:.2f}, "
              f"state={stats.current_state.value}")


if __name__ == "__main__":
    main()

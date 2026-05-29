# Correlation Relevance Plugin

Agent-agnostic rule-based context enrichment engine for Hermes Agent.

**What it does:** When you execute a task, correlation rules automatically surface related memories (`must_also_fetch`) before task execution — so decisions are made with full context rather than in isolation.

**Reference upstream:** `ether-btc/openclaw-correlation-plugin` (MIT, v2.1.0) — TypeScript plugin for OpenClaw.

## Quick Start

```bash
# Install
pip install correlation-lib

# Create rules file at ~/.hermes/correlation-rules.json
```

```json
[
  {
    "id": "cr-001",
    "trigger_context": "config-change",
    "trigger_keywords": ["config", "setting", "modify"],
    "must_also_fetch": ["backup-location", "rollback-instructions"],
    "relationship_type": "constrains",
    "confidence": 0.95
  }
]
```

```python
from correlation_lib import create_engine

# With Hermes Agent (optional — requires hermes-agent package):
try:
    from correlation_lib_adapters.hermes import HermesRecallBackend, HermesContextBackend
    recall = HermesRecallBackend()
    context = HermesContextBackend()
except ImportError:
    # Standalone mode: provide your own backends
    # (see examples/demo.py for a MockBackend implementation)
    raise ImportError(
        "hermes-agent not installed. Install it, or provide your own "
        "RecallBackend/ContextBackend implementations."
    )

engine = create_engine(
    "rules.json",
    recall_backend=recall,
    context_backend=context,
)

# In your agent loop:
if engine.enricher.is_new_task(user_message):
    result = engine.enricher.on_task_start(user_message)
    print(context.format_injected())
```

## Architecture

```
correlation-lib/         # Pure Python, zero framework deps
├── engine.py            # Thin facade/factory
├── rules.py             # Schema + validation
├── matcher.py           # Keyword/context/confidence matching
├── lifecycle.py         # State machine (proposal → testing → validated → promoted → retired)
├── enricher.py          # Orchestrates match→recall→inject
├── tracker.py           # EffectivenessTracker (self-improvement)
├── interfaces.py        # Protocol definitions
├── diagnostics.py        # Runtime diagnostics
└── rule_provider.py     # File-based rule loader (hot-reload optional)

correlation_lib_adapters/
└── hermes/              # Hermes Agent adapter
    ├── adapter.py       # CorrelationMemoryProvider
    └── backends.py      # HermesRecallBackend, HermesContextBackend
```

## Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Q1 — Self-improvement | **A: Fully automated** | Auto-promote and auto-demote based on firing_count + effectiveness_ratio |
| Q2 — Trigger point | **B: on_task_start** | New task detection heuristic; prefetch as fallback for high-confidence rules |
| Q3 — Hot-reload | **C: Configurable** | `watch_enabled: false` default; power users can enable |
| Q4 — Effectiveness store | **A: SQLite standalone** | `~/.hermes/correlation-effectiveness.db` — independent of Mnemosyne |

## Lifecycle States

```
proposal → testing → validated → promoted → retired
     ↑__________↓___________↓____________↓
           auto-demote on low effectiveness
```

- **Auto-promote:** `firing_count >= 30 AND effectiveness_ratio >= 0.8`
- **Auto-demote:** `firing_count >= 10 AND effectiveness_ratio < 0.3`
- **Hard demote:** `firing_count >= 90 AND effectiveness_ratio < 0.20` → back to PROPOSAL

## Configuration

In `~/.hermes/config.yaml`:

```yaml
memory:
  provider: correlation
  correlation:
    rule_file: ~/.hermes/correlation-rules.json
    watch_enabled: false
    db_path: ~/.hermes/correlation-effectiveness.db
```

## Rule Schema

```json
{
  "id": "cr-001",
  "trigger_context": "config-change",
  "trigger_keywords": ["config", "setting", "modify"],
  "must_also_fetch": ["backup-location", "rollback-instructions"],
  "relationship_type": "constrains",
  "confidence": 0.95,
  "lifecycle": { "state": "promoted" },
  "learned_from": "config-misconfiguration-leads-to-service-outage"
}
```

| Field | Type | Description |
|-------|------|-------------|
| `id` | string | Unique rule identifier |
| `trigger_context` | string | Semantic domain (e.g., `config-change`, `error-debugging`) |
| `trigger_keywords` | string[] | Keywords that activate this rule |
| `must_also_fetch` | string[] | Context paths to retrieve when rule fires |
| `relationship_type` | string | `constrains`, `supports`, `diagnosed_by`, etc. |
| `confidence` | float | 0.0–1.0 |
| `lifecycle.state` | string | `proposal`, `testing`, `validated`, `promoted`, `retired` |

## Development

```bash
# Install with dev dependencies (ruff + pytest)
pip install -e ".[dev]"

# Run tests
python -m pytest tests/ -v

# Lint
ruff check .

# Install with Hermes support (for live integration)
pip install -e ".[hermes]"
```

## Deployment — Hermes Agent

Deploy this as a Hermes memory provider plugin:

```bash
# Link the plugin into Hermes
ln -sf /path/to/correlation-relevance-plugin/correlation_lib_adapters/hermes \
  ~/.hermes/plugins/correlation

# Add to ~/.hermes/config.yaml:
# memory:
#   provider: correlation
#   correlation:
#     rule_file: ~/.hermes/correlation-rules.json
#     watch_enabled: false
#     db_path: ~/.hermes/correlation-effectiveness.db
```

Restart Hermes Agent — the correlation engine will prefetch related context automatically on matched tasks.

## Migrating from OpenClaw

See [UPSTREAM.md](./UPSTREAM.md) for the OpenClaw→Hermes porting guide.

## License

MIT
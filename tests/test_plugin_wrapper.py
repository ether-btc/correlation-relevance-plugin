"""Tests for the correlation_lookup plugin wrapper (one tool, one manifest)."""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

import correlation_plugin as plugin

RULES = [
    {
        "id": "cr-001",
        "trigger_context": "config-change",
        "trigger_keywords": ["config", "setting"],
        "must_also_fetch": ["backup-location", "rollback-instructions"],
        "relationship_type": "constrains",
        "confidence": 0.95,
        "lifecycle": {"state": "promoted"},
    },
    {
        "id": "cr-002",
        "trigger_context": "error-debugging",
        "trigger_keywords": ["crash", "exception"],
        "must_also_fetch": ["recent-incidents"],
        "relationship_type": "diagnosed_by",
        "confidence": 0.90,
        "lifecycle": {"state": "validated"},
    },
    {
        "id": "cr-003",
        "trigger_context": "database-ops",
        "trigger_keywords": ["migration"],
        "must_also_fetch": ["backup-verification"],
        "relationship_type": "constrains",
        "confidence": 0.98,
        "lifecycle": {"state": "promoted"},
    },
]


@pytest.fixture
def rules_file(tmp_path, monkeypatch):
    path = tmp_path / "rules.json"
    path.write_text(json.dumps(RULES))
    monkeypatch.setenv("HERMES_CORRELATION_RULES", str(path))
    return path


def test_missing_rules_file_is_not_an_error(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_CORRELATION_RULES", str(tmp_path / "absent.json"))
    out = json.loads(plugin.correlation_lookup({"task_text": "modify the config"}))
    assert out["rules_configured"] is False
    assert out["matched"] == []
    assert out["count"] == 0
    assert "error" not in out


def test_matching_task_returns_hint(rules_file):
    out = json.loads(plugin.correlation_lookup({"task_text": "modify the config setting"}))
    assert out["rules_configured"] is True
    assert out["count"] >= 1
    top = out["matched"][0]
    assert top["rule_id"] == "cr-001"
    assert top["must_also_fetch"] == ["backup-location", "rollback-instructions"]
    assert top["relationship_type"] == "constrains"


def test_non_matching_task_returns_nothing(rules_file):
    out = json.loads(plugin.correlation_lookup({"task_text": "write a haiku about the sea"}))
    assert out["count"] == 0
    assert out["matched"] == []


def test_limit_truncates(rules_file):
    out = json.loads(
        plugin.correlation_lookup({"task_text": "config crash migration", "limit": 2})
    )
    assert out["count"] <= 2
    assert len(out["matched"]) <= 2


def test_malformed_rules_file_returns_error_json(tmp_path, monkeypatch):
    bad = tmp_path / "bad.json"
    bad.write_text("{not json")
    monkeypatch.setenv("HERMES_CORRELATION_RULES", str(bad))
    out = json.loads(plugin.correlation_lookup({"task_text": "modify the config"}))
    assert out["success"] is False
    assert "error" in out


def test_register_registers_exactly_one_tool():
    ctx = MagicMock()
    plugin.register(ctx)
    assert ctx.register_tool.call_count == 1
    assert ctx.register_tool.call_args.kwargs["name"] == "correlation_lookup"


def test_handler_always_returns_json_string(rules_file):
    for args in (
        {"task_text": "modify the config setting"},
        {"task_text": "nothing to see"},
        {},
        {"task_text": "config", "limit": "not-a-number"},
    ):
        out = plugin.correlation_lookup(args)
        assert isinstance(out, str)
        json.loads(out)

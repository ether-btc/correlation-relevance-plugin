#!/usr/bin/env python3
"""Integration test for CorrelationMemoryProvider with mock Hermes Agent.

This test validates that the Hermes adapter works correctly without requiring
a full Hermes Agent installation. It mocks the MemoryProvider interface and
simulates the Hermes lifecycle hooks.
"""
from __future__ import annotations

import json  # noqa: E402
import tempfile
from pathlib import Path

import yaml

# Import from fixture - sets up mock modules before CorrelationMemoryProvider
from correlation_lib_adapters.hermes import CorrelationMemoryProvider  # noqa: E402


def test_memory_provider_basic(hermes_mock_modules):
    """Test basic MemoryProvider lifecycle."""
    print("TEST: Memory Provider Basic Lifecycle")

    provider = CorrelationMemoryProvider()
    assert provider.name == "correlation", "Provider name should be 'correlation'"
    assert provider.is_available(), "Provider should be available"

    # Create temporary config and rules
    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir = Path(tmpdir)

        # Create rule file
        rules = [
            {
                "id": "test-001",
                "trigger_context": "test",
                "trigger_keywords": ["test", "verify"],
                "must_also_fetch": ["test-instructions"],
                "relationship_type": "related_to",
                "confidence": 0.90,
                "lifecycle": {"state": "promoted"}
            }
        ]
        rule_file = tmpdir / "rules.json"
        rule_file.write_text(json.dumps(rules))

        # Initialize with config
        provider.initialize(
            session_id="test-session",
            hermes_home=str(tmpdir),
            correlation_rules_file=str(rule_file)
        )

        assert provider._engine is not None, "Engine should be initialized"

        # Test prefetch
        result = provider.prefetch("Run the test suite", session_id="test-session")
        print(f"  Prefetch result: {repr(result[:50])}...")

        # Test on_turn_start
        provider.on_turn_start(1, "Verify the test results", session_id="test-session")

        # Test system_prompt_block
        block = provider.system_prompt_block()
        print(f"  System prompt block: {repr(block[:50])}...")

        # Shutdown
        provider.shutdown()

    print("  PASSED\n")


def test_memory_provider_config(hermes_mock_modules):
    """Test configuration loading."""
    print("TEST: Memory Provider Configuration")

    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir = Path(tmpdir)

        # Create config.yaml
        config = {
            "memory": {
                "provider": {
                    "correlation": {
                        "rule_file": str(tmpdir / "rules.json"),
                        "watch_enabled": False,
                        "db_path": str(tmpdir / "effectiveness.db")
                    }
                }
            }
        }
        config_file = tmpdir / "config.yaml"
        config_file.write_text(yaml.dump(config))

        # Create rule file
        rules = []
        rule_file = tmpdir / "rules.json"
        rule_file.write_text(json.dumps(rules))

        provider = CorrelationMemoryProvider()
        provider.initialize(
            session_id="test-session",
            hermes_home=str(tmpdir)
        )

        assert provider._engine is not None, "Engine should initialize from config"

        provider.shutdown()

    print("  PASSED\n")


def test_config_schema(hermes_mock_modules):
    """Test config schema."""
    print("TEST: Config Schema")

    provider = CorrelationMemoryProvider()
    schema = provider.get_config_schema()

    assert isinstance(schema, list), "Schema should be a list"
    assert len(schema) >= 3, "Schema should have at least 3 entries"

    # Check for required fields
    keys = [entry.get("key") for entry in schema]
    assert "rule_file" in keys, "Schema should have 'rule_file'"
    assert "watch_enabled" in keys, "Schema should have 'watch_enabled'"
    assert "db_path" in keys, "Schema should have 'db_path'"

    print(f"  Schema has {len(schema)} entries")
    print("  PASSED\n")


def test_error_handling(hermes_mock_modules):
    """Test error handling."""
    print("TEST: Error Handling")

    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir = Path(tmpdir)

        provider = CorrelationMemoryProvider()

        # Initialize with isolated tempdir as hermes_home
        provider.initialize(session_id="test-session", hermes_home=str(tmpdir))

        # Operations should be safe even without engine
        provider.prefetch("test", session_id="test-session")
        provider.on_turn_start(1, "test", session_id="test-session")
        block = provider.system_prompt_block()
        assert block == "", "System prompt should be empty without engine"

        provider.shutdown()

    print("  PASSED\n")


def main():
    print("="*60)
    print("CorrelationMemoryProvider Integration Tests")
    print("="*60)
    print()

    try:
        test_memory_provider_basic(hermes_mock_modules=None)
        test_memory_provider_config(hermes_mock_modules=None)
        test_config_schema(hermes_mock_modules=None)
        test_error_handling(hermes_mock_modules=None)

        print("="*60)
        print("ALL TESTS PASSED")
        print("="*60)
        return 0

    except AssertionError as e:
        print(f"\nFAILED: {e}")
        return 1
    except Exception as e:
        print(f"\nERROR: {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    exit(main())
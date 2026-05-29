"""Pytest configuration and shared fixtures."""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest


class MockMemoryProvider:
    """Mock base class for MemoryProvider."""
    name = "mock"

    def is_available(self) -> bool:
        return True

    def initialize(self, session_id: str, **kwargs) -> None:
        pass

    def system_prompt_block(self) -> str:
        return ""

    def prefetch(self, query: str, *, session_id: str = "") -> str:
        return ""

    def on_turn_start(self, turn_number: int, message: str, **kwargs) -> None:
        pass

    def sync_turn(self, user_content: str, assistant_content: str, *, session_id: str = "") -> None:
        pass

    def shutdown(self) -> None:
        pass

    def get_config_schema(self) -> list:
        return []


@pytest.fixture(autouse=True)
def preserve_sys_modules():
    """Save and restore sys.modules state after each test.

    This prevents sys.modules pollution from tests that modify
    sys.modules at runtime (e.g., by injecting mock modules).
    """
    # Save keys and values before test
    before_keys = set(sys.modules.keys())
    before_values = {k: sys.modules[k] for k in before_keys}

    yield

    # Remove any new modules that were added
    after_keys = set(sys.modules.keys())
    new_keys = after_keys - before_keys

    for key in new_keys:
        if key in sys.modules:
            del sys.modules[key]

    # Restore any modules that were removed or replaced
    for key in before_keys:
        if key not in sys.modules:
            sys.modules[key] = before_values[key]
        elif sys.modules[key] is not before_values[key]:
            sys.modules[key] = before_values[key]


@pytest.fixture
def hermes_mock_modules():
    """Set up mock hermes_agent modules for testing.

    This fixture creates a minimal mock hierarchy that mimics the
    hermes_agent package structure required by the CorrelationMemoryProvider.
    """
    # Create module hierarchy
    hermes_agent = type(sys)('hermes_agent')
    hermes_constants = type(sys)('hermes_constants')

    hermes_constants.get_hermes_home = lambda: Path.home()

    agent = type(sys)('agent')
    memory_provider_module = type(sys)('memory_provider')

    memory_provider_module.MemoryProvider = MockMemoryProvider
    agent.memory_provider = memory_provider_module
    hermes_agent.agent = agent
    hermes_agent.hermes_constants = hermes_constants

    # Store in sys.modules
    sys.modules['hermes_agent'] = hermes_agent
    sys.modules['hermes_agent.agent'] = agent
    sys.modules['hermes_agent.agent.memory_provider'] = memory_provider_module
    sys.modules['hermes_agent.hermes_constants'] = hermes_constants

    yield

    # Cleanup happens via preserve_sys_modules fixture
    pass
"""Adapters for correlation-lib —Hermes, LangChain, AutoGen, etc.

The hermes adapter requires the hermes-agent package. Import it explicitly:
    from correlation_lib_adapters.hermes import CorrelationMemoryProvider

Importing this package directly without hermes-agent installed will fail
on any adapter submodule access. The core correlation_lib works fine without it.
"""

__all__ = []  # No default adapter exports — import explicitly by adapter name

"""Base AI layer: providers, per-role config, structured schemas, and tools."""

from .providers import ModelSpec, ProviderError, available, build, build_structured
from .config import spec_for, describe

__all__ = ["ModelSpec", "ProviderError", "available", "build",
           "build_structured", "spec_for", "describe"]

"""LLM provider adapters."""

from .openai_compatible import FakeProviderModel, OpenAICompatibleModelAdapter

__all__ = ["FakeProviderModel", "OpenAICompatibleModelAdapter"]

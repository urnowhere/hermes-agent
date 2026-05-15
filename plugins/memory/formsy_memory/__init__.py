"""FormSy Memory Provider for Hermes."""

from .provider import FormSyMemoryProvider

__all__ = ["FormSyMemoryProvider"]


def register(ctx) -> None:
    """Register FormSy as a memory provider plugin."""
    ctx.register_memory_provider(FormSyMemoryProvider())

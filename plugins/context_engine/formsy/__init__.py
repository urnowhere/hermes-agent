"""Formsy context engine for Hermes."""

from .engine import FormsyContextEngine

__all__ = ["FormsyContextEngine"]


def register(ctx) -> None:
    """Register the Formsy context engine with Hermes."""
    ctx.register_context_engine(FormsyContextEngine())

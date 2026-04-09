"""Workspace plugin manager.

Discovers plugins, resolves one active plugin per category, falls back
to built-in defaults, computes signature bundles for index invalidation,
and exposes status/doctor diagnostics.
"""

from __future__ import annotations

import hashlib
import json
import logging
from typing import Any

from agent.workspace_contracts import (
    WorkspaceChunkerPlugin,
    WorkspaceEmbedderPlugin,
    WorkspaceIndexStorePlugin,
    WorkspaceParserPlugin,
    WorkspacePlugin,
    WorkspaceRerankerPlugin,
    WorkspaceRetrieverPlugin,
)
from agent.workspace_types import PluginHealth, WorkspacePluginContext

logger = logging.getLogger(__name__)

# Built-in default plugin names per category
_BUILTIN_DEFAULTS: dict[str, str] = {
    "parsers": "builtin_text",
    "chunkers": "builtin_structural",
    "embedders": "builtin_hash",
    "rerankers": "disabled",
    "retrievers": "builtin_hybrid_rrf",
    "index_stores": "builtin_sqlite",
}

# Config key -> category mapping
_CONFIG_CATEGORIES = (
    "parsers",
    "chunkers",
    "embedders",
    "rerankers",
    "retrievers",
    "index_stores",
)


class WorkspacePluginManager:
    """Discovers and resolves workspace pipeline plugins."""

    def __init__(self, config: dict[str, Any], context: WorkspacePluginContext) -> None:
        self._config = config
        self._context = context
        self._kb_cfg = config.get("knowledgebase", {}) or {}
        self._resolved: dict[str, WorkspacePlugin] = {}
        self._resolved_ids: dict[str, str] = {}
        self._warnings: list[str] = []

    @staticmethod
    def _normalize_plugin_id(name: str) -> str:
        return str(name or "").strip().replace("-", "_")

    def _category_config(self, category: str) -> dict[str, Any]:
        """Get the config dict for a category (e.g. knowledgebase.parsers)."""
        return self._kb_cfg.get(category, {}) or {}

    def _active_name(self, category: str) -> str:
        """Get the configured active plugin name for a category."""
        cat_cfg = self._category_config(category)
        active = str(cat_cfg.get("active", _BUILTIN_DEFAULTS[category]) or _BUILTIN_DEFAULTS[category])
        return self._normalize_plugin_id(active)

    def _plugin_config(self, category: str, name: str) -> dict[str, Any]:
        """Get the plugin-specific config block."""
        cat_cfg = self._category_config(category)
        for key in (name, self._normalize_plugin_id(name), str(name).replace("_", "-")):
            if key in cat_cfg and isinstance(cat_cfg.get(key), dict):
                return cat_cfg.get(key, {}) or {}
        return {}

    def _resolve(self, category: str) -> WorkspacePlugin | None:
        """Resolve the active plugin for a category with fallback."""
        if category in self._resolved:
            return self._resolved[category]

        from plugins.workspace import load_workspace_plugin

        active_name = self._active_name(category)
        plugin_cfg = self._plugin_config(category, active_name)

        plugin = load_workspace_plugin(category, active_name)
        if plugin is not None:
            if plugin.is_available(plugin_cfg, self._context):
                self._resolved[category] = plugin
                self._resolved_ids[category] = active_name
                self._context.resolved_plugins[category] = active_name
                return plugin
            self._warnings.append(
                f"{category}: configured plugin '{active_name}' is not available, "
                f"falling back to '{_BUILTIN_DEFAULTS[category]}'"
            )
        elif active_name != _BUILTIN_DEFAULTS[category]:
            self._warnings.append(
                f"{category}: configured plugin '{active_name}' not found, "
                f"falling back to '{_BUILTIN_DEFAULTS[category]}'"
            )

        fallback_name = _BUILTIN_DEFAULTS[category]
        fallback_cfg = self._plugin_config(category, fallback_name)
        fallback = load_workspace_plugin(category, fallback_name)
        if fallback is not None and fallback.is_available(fallback_cfg, self._context):
            self._resolved[category] = fallback
            self._resolved_ids[category] = fallback_name
            self._context.resolved_plugins[category] = fallback_name
            return fallback

        logger.error(f"No available plugin for workspace category '{category}'")
        return None

    # -----------------------------------------------------------------------
    # Public resolve methods
    # -----------------------------------------------------------------------

    def resolve_parser(self) -> WorkspaceParserPlugin | None:
        plugin = self._resolve("parsers")
        if plugin is not None and not isinstance(plugin, WorkspaceParserPlugin):
            logger.error(f"Plugin '{plugin.name}' does not implement WorkspaceParserPlugin")
            return None
        return plugin  # type: ignore[return-value]

    def resolve_chunker(self) -> WorkspaceChunkerPlugin | None:
        plugin = self._resolve("chunkers")
        if plugin is not None and not isinstance(plugin, WorkspaceChunkerPlugin):
            logger.error(f"Plugin '{plugin.name}' does not implement WorkspaceChunkerPlugin")
            return None
        return plugin  # type: ignore[return-value]

    def resolve_embedder(self) -> WorkspaceEmbedderPlugin | None:
        plugin = self._resolve("embedders")
        if plugin is not None and not isinstance(plugin, WorkspaceEmbedderPlugin):
            logger.error(f"Plugin '{plugin.name}' does not implement WorkspaceEmbedderPlugin")
            return None
        return plugin  # type: ignore[return-value]

    def resolve_reranker(self) -> WorkspaceRerankerPlugin | None:
        plugin = self._resolve("rerankers")
        if plugin is not None and not isinstance(plugin, WorkspaceRerankerPlugin):
            logger.error(f"Plugin '{plugin.name}' does not implement WorkspaceRerankerPlugin")
            return None
        return plugin  # type: ignore[return-value]

    def resolve_retriever(self) -> WorkspaceRetrieverPlugin | None:
        plugin = self._resolve("retrievers")
        if plugin is not None and not isinstance(plugin, WorkspaceRetrieverPlugin):
            logger.error(f"Plugin '{plugin.name}' does not implement WorkspaceRetrieverPlugin")
            return None
        return plugin  # type: ignore[return-value]

    def resolve_index_store(self) -> WorkspaceIndexStorePlugin | None:
        plugin = self._resolve("index_stores")
        if plugin is not None and not isinstance(plugin, WorkspaceIndexStorePlugin):
            logger.error(f"Plugin '{plugin.name}' does not implement WorkspaceIndexStorePlugin")
            return None
        return plugin  # type: ignore[return-value]


    def resolved_id(self, category: str) -> str | None:
        self._resolve(category)
        return self._resolved_ids.get(category)

    def resolved_config(self, category: str) -> dict[str, Any]:
        resolved_id = self.resolved_id(category)
        if not resolved_id:
            return {}
        return self._plugin_config(category, resolved_id)

    # -----------------------------------------------------------------------
    # Diagnostics
    # -----------------------------------------------------------------------

    def status_report(self) -> dict[str, Any]:
        """Return category-aware status for CLI and tools."""
        report: dict[str, Any] = {"categories": {}}
        for category in _CONFIG_CATEGORIES:
            active_name = self._active_name(category)
            plugin = self._resolve(category)
            resolved_id = self._resolved_ids.get(category)
            report["categories"][category] = {
                "configured": active_name,
                "resolved": resolved_id,
                "plugin_name": plugin.name if plugin else None,
                "source": "builtin" if resolved_id and resolved_id.startswith("builtin") else "external",
                "available": plugin is not None,
                "fallback": resolved_id != active_name if plugin else True,
            }
        report["warnings"] = list(self._warnings)
        return report

    def doctor_report(self) -> dict[str, Any]:
        """Return health diagnostics for all resolved plugins."""
        report: dict[str, Any] = {"categories": {}, "warnings": list(self._warnings), "healthy": True}
        for category in _CONFIG_CATEGORIES:
            active_name = self._active_name(category)
            plugin = self._resolve(category)
            if plugin is None:
                report["categories"][category] = {
                    "configured": active_name,
                    "resolved": None,
                    "healthy": False,
                    "message": "No available plugin",
                }
                report["healthy"] = False
                continue

            resolved_id = self._resolved_ids.get(category, active_name)
            plugin_cfg = self._plugin_config(category, resolved_id)
            health = plugin.healthcheck(plugin_cfg, self._context)
            report["categories"][category] = {
                "configured": active_name,
                "resolved": resolved_id,
                "plugin_name": plugin.name,
                "healthy": health.healthy,
                "message": health.message,
                "details": health.details,
            }
            if not health.healthy:
                report["healthy"] = False

        report["warnings"] = list(self._warnings)
        return report

    def signature_bundle(self) -> str:
        """Compute a signature bundle for index invalidation.

        Includes parser, chunker, embedder, retriever, index-store signatures
        plus schema version.  Reranker is excluded (does not invalidate stored data).
        """
        from agent.workspace import _INDEX_SCHEMA_VERSION

        parts: dict[str, str] = {"schema_version": str(_INDEX_SCHEMA_VERSION)}
        for category in ("parsers", "chunkers", "embedders", "retrievers", "index_stores"):
            plugin = self._resolve(category)
            if plugin is not None:
                resolved_id = self._resolved_ids.get(category, self._active_name(category))
                plugin_cfg = self._plugin_config(category, resolved_id)
                parts[category] = f"{resolved_id}:{plugin.signature(plugin_cfg)}"
            else:
                parts[category] = f"{_BUILTIN_DEFAULTS[category]}:unavailable"

        return hashlib.sha256(
            json.dumps(parts, sort_keys=True).encode("utf-8")
        ).hexdigest()

    def warm_up_all(self) -> None:
        """Warm up all resolved plugins."""
        for category in _CONFIG_CATEGORIES:
            plugin = self._resolve(category)
            if plugin is not None:
                resolved_id = self._resolved_ids.get(category, self._active_name(category))
                plugin_cfg = self._plugin_config(category, resolved_id)
                try:
                    plugin.warm_up(plugin_cfg, self._context)
                except Exception as e:
                    logger.warning(f"warm_up failed for {category}/{plugin.name}: {e}")

    @property
    def context(self) -> WorkspacePluginContext:
        return self._context

    @property
    def warnings(self) -> list[str]:
        return list(self._warnings)

"""Workspace plugin discovery and loading.

Scans ``plugins/workspace/<category>/<name>/`` directories for workspace
pipeline plugins.  Each category (parsers, chunkers, embedders, rerankers,
retrievers, index_stores) has its own registration method.

Modeled on ``plugins/memory/__init__.py`` but supports one active plugin
per category rather than one external plugin total.

Usage:
    from plugins.workspace import discover_workspace_plugins, load_workspace_plugin

    available = discover_workspace_plugins("parsers")
    plugin = load_workspace_plugin("parsers", "builtin_text")
"""

from __future__ import annotations

import importlib
import importlib.util
import logging
import sys
from pathlib import Path
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

logger = logging.getLogger(__name__)

_WORKSPACE_PLUGINS_DIR = Path(__file__).parent

CATEGORIES = (
    "parsers",
    "chunkers",
    "embedders",
    "rerankers",
    "retrievers",
    "index_stores",
)

_CATEGORY_CONTRACT_MAP: dict[str, type[WorkspacePlugin]] = {
    "parsers": WorkspaceParserPlugin,
    "chunkers": WorkspaceChunkerPlugin,
    "embedders": WorkspaceEmbedderPlugin,
    "rerankers": WorkspaceRerankerPlugin,
    "retrievers": WorkspaceRetrieverPlugin,
    "index_stores": WorkspaceIndexStorePlugin,
}


def discover_workspace_plugins(
    category: str,
) -> list[tuple[str, str, bool]]:
    """Scan plugins/workspace/<category>/ for available plugins.

    Returns list of (name, description, is_available) tuples.
    """
    if category not in CATEGORIES:
        logger.warning(f"Unknown workspace plugin category: {category}")
        return []

    category_dir = _WORKSPACE_PLUGINS_DIR / category
    if not category_dir.is_dir():
        return []

    results: list[tuple[str, str, bool]] = []
    for child in sorted(category_dir.iterdir()):
        if not child.is_dir() or child.name.startswith(("_", ".")):
            continue
        init_file = child / "__init__.py"
        if not init_file.exists():
            continue

        desc = ""
        yaml_file = child / "plugin.yaml"
        if yaml_file.exists():
            try:
                import yaml

                with open(yaml_file) as f:
                    meta = yaml.safe_load(f) or {}
                desc = meta.get("description", "")
            except Exception:
                pass

        available = True
        try:
            plugin = _load_plugin_from_dir(category, child)
            if plugin is None:
                available = False
        except Exception:
            available = False

        results.append((child.name, desc, available))

    return results


def load_workspace_plugin(
    category: str, name: str
) -> WorkspacePlugin | None:
    """Load and return a workspace plugin instance by category and name.

    Returns None if the plugin is not found or fails to load.
    """
    if category not in CATEGORIES:
        logger.warning(f"Unknown workspace plugin category: {category}")
        return None

    plugin_dir = _WORKSPACE_PLUGINS_DIR / category / name
    if not plugin_dir.is_dir():
        logger.debug(
            f"Workspace plugin '{name}' not found in {category}"
        )
        return None

    try:
        plugin = _load_plugin_from_dir(category, plugin_dir)
        if plugin is not None:
            return plugin
        logger.warning(
            f"Workspace plugin '{name}' loaded but no instance found"
        )
        return None
    except Exception as e:
        logger.warning(f"Failed to load workspace plugin '{name}': {e}")
        return None


def _load_plugin_from_dir(
    category: str, plugin_dir: Path
) -> WorkspacePlugin | None:
    """Import a workspace plugin module and extract the plugin instance."""
    name = plugin_dir.name
    module_name = f"plugins.workspace.{category}.{name}"
    init_file = plugin_dir / "__init__.py"

    if not init_file.exists():
        return None

    if module_name in sys.modules:
        mod = sys.modules[module_name]
    else:
        _ensure_parent_packages(category)

        spec = importlib.util.spec_from_file_location(
            module_name,
            str(init_file),
            submodule_search_locations=[str(plugin_dir)],
        )
        if not spec:
            return None

        mod = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = mod

        # Register submodules for relative imports
        for sub_file in plugin_dir.glob("*.py"):
            if sub_file.name == "__init__.py":
                continue
            sub_name = sub_file.stem
            full_sub_name = f"{module_name}.{sub_name}"
            if full_sub_name not in sys.modules:
                sub_spec = importlib.util.spec_from_file_location(
                    full_sub_name, str(sub_file)
                )
                if sub_spec:
                    sub_mod = importlib.util.module_from_spec(sub_spec)
                    sys.modules[full_sub_name] = sub_mod
                    try:
                        sub_spec.loader.exec_module(sub_mod)
                    except Exception as e:
                        logger.debug(
                            f"Failed to load submodule {full_sub_name}: {e}"
                        )

        try:
            spec.loader.exec_module(mod)
        except Exception as e:
            logger.debug(f"Failed to exec_module {module_name}: {e}")
            sys.modules.pop(module_name, None)
            return None

    # Try register(ctx) pattern first
    if hasattr(mod, "register"):
        collector = _WorkspacePluginCollector()
        try:
            mod.register(collector)
            if collector.plugin is not None:
                return collector.plugin
        except Exception as e:
            logger.debug(f"register() failed for {name}: {e}")

    # Fallback: find a subclass of the expected contract
    expected_base = _CATEGORY_CONTRACT_MAP.get(category, WorkspacePlugin)
    for attr_name in dir(mod):
        attr = getattr(mod, attr_name, None)
        if (
            isinstance(attr, type)
            and issubclass(attr, expected_base)
            and attr is not expected_base
            and not getattr(attr, "__abstractmethods__", None)
        ):
            try:
                return attr()
            except Exception:
                pass

    return None


def _ensure_parent_packages(category: str) -> None:
    """Register parent packages so relative imports work."""
    parents = [
        ("plugins", _WORKSPACE_PLUGINS_DIR.parent),
        ("plugins.workspace", _WORKSPACE_PLUGINS_DIR),
        (f"plugins.workspace.{category}", _WORKSPACE_PLUGINS_DIR / category),
    ]
    for pkg_name, pkg_path in parents:
        if pkg_name in sys.modules:
            continue
        init_file = pkg_path / "__init__.py"
        if not init_file.exists():
            # Create a namespace package stub
            import types

            ns_mod = types.ModuleType(pkg_name)
            ns_mod.__path__ = [str(pkg_path)]
            sys.modules[pkg_name] = ns_mod
            continue
        spec = importlib.util.spec_from_file_location(
            pkg_name,
            str(init_file),
            submodule_search_locations=[str(pkg_path)],
        )
        if spec:
            parent_mod = importlib.util.module_from_spec(spec)
            sys.modules[pkg_name] = parent_mod
            try:
                spec.loader.exec_module(parent_mod)
            except Exception:
                pass


class _WorkspacePluginCollector:
    """Fake context that captures workspace plugin registration calls."""

    def __init__(self) -> None:
        self.plugin: WorkspacePlugin | None = None

    def register_workspace_parser(self, plugin: Any) -> None:
        self.plugin = plugin

    def register_workspace_chunker(self, plugin: Any) -> None:
        self.plugin = plugin

    def register_workspace_embedder(self, plugin: Any) -> None:
        self.plugin = plugin

    def register_workspace_reranker(self, plugin: Any) -> None:
        self.plugin = plugin

    def register_workspace_retriever(self, plugin: Any) -> None:
        self.plugin = plugin

    def register_workspace_index_store(self, plugin: Any) -> None:
        self.plugin = plugin

    # No-ops for other registration methods
    def register_tool(self, *args: Any, **kwargs: Any) -> None:
        pass

    def register_hook(self, *args: Any, **kwargs: Any) -> None:
        pass

    def register_cli_command(self, *args: Any, **kwargs: Any) -> None:
        pass

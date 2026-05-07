"""Advanced diagnostic CLI commands for formalcc-memory provider."""

import asyncio
import logging
import time
from pathlib import Path
from typing import Optional
from datetime import datetime

from .config import ConfigManager
from plugins.formsy import RuntimeClient
from plugins.formsy.config_validator import ConfigValidator
from plugins.formsy.resilience import CircuitBreaker

logger = logging.getLogger("formalcc.memory.diagnostics")


class DiagnosticRunner:
    """Runs diagnostic checks for the memory provider."""

    def __init__(self, hermes_home: Path):
        self.hermes_home = hermes_home
        self.config_manager = ConfigManager(hermes_home)
        self.config = self.config_manager.load_config()

    async def run_connectivity_check(self) -> dict:
        """Test connectivity to Runtime API."""
        result = {
            "success": False,
            "latency_ms": None,
            "error": None,
        }

        try:
            start_time = time.time()

            async with RuntimeClient(
                base_url=self.config.base_url,
                api_key_env=self.config.api_key_env,
                timeout_s=self.config.timeout_s,
            ) as client:
                from shared.models import MemoryPrefetchRequest

                request = MemoryPrefetchRequest(
                    workspace_id=self.config.workspace_id,
                    session_id="diagnostic_session",
                    turn_id="diagnostic_turn_001",
                    query="diagnostic test",
                )

                await client.memory_prefetch(request)

            elapsed_ms = int((time.time() - start_time) * 1000)
            result["success"] = True
            result["latency_ms"] = elapsed_ms

        except Exception as e:
            result["error"] = str(e)

        return result

    async def run_config_validation(self) -> dict:
        """Validate configuration."""
        config_dict = self.config.to_dict()
        validation_result = ConfigValidator.validate_config(config_dict)

        return {
            "valid": validation_result.valid,
            "errors": validation_result.errors,
            "warnings": validation_result.warnings,
        }

    async def run_memory_prefetch_test(self) -> dict:
        """Test memory prefetch functionality."""
        result = {
            "success": False,
            "retrieved_count": 0,
            "latency_ms": None,
            "error": None,
        }

        try:
            start_time = time.time()

            async with RuntimeClient(
                base_url=self.config.base_url,
                api_key_env=self.config.api_key_env,
                timeout_s=self.config.timeout_s,
            ) as client:
                from shared.models import MemoryPrefetchRequest

                request = MemoryPrefetchRequest(
                    workspace_id=self.config.workspace_id,
                    session_id="test_session",
                    turn_id="test_turn_001",
                    query="test authentication patterns",
                    budget={"top_k": 5},
                )

                response = await client.memory_prefetch(request)

            elapsed_ms = int((time.time() - start_time) * 1000)
            result["success"] = True
            result["retrieved_count"] = response.retrieved_count
            result["latency_ms"] = elapsed_ms

        except Exception as e:
            result["error"] = str(e)

        return result

    async def run_full_diagnostics(self) -> dict:
        """Run all diagnostic checks."""
        return {
            "timestamp": datetime.now().isoformat(),
            "config_validation": await self.run_config_validation(),
            "connectivity": await self.run_connectivity_check(),
            "memory_prefetch": await self.run_memory_prefetch_test(),
        }


def format_status_output(config, diagnostics: Optional[dict] = None) -> str:
    """Format status output for display."""
    lines = []
    lines.append("=" * 50)
    lines.append("FormalCC Memory Provider Status")
    lines.append("=" * 50)
    lines.append(f"Provider: formalcc-memory")
    lines.append(f"Status: {'✓ Available' if config else '✗ Not configured'}")
    lines.append("")

    if config:
        lines.append("Configuration:")
        lines.append(f"  Base URL: {config.base_url}")
        lines.append(f"  Workspace: {config.workspace_id}")
        lines.append(f"  Tenant: {config.tenant_id or 'N/A'}")
        lines.append(f"  Timeout: {config.timeout_s}s")
        lines.append(f"  Max Retries: {config.max_retries}")
        lines.append(f"  Memory Tools: {'✓ Enabled' if config.enable_memory_tools else '✗ Disabled'}")
        lines.append(f"  Diagnostics: {'✓ Enabled' if config.enable_diagnostics else '✗ Disabled'}")
        lines.append("")

    if diagnostics:
        lines.append("Diagnostics:")

        # Config validation
        config_val = diagnostics.get("config_validation", {})
        if config_val.get("valid"):
            lines.append("  Config: ✓ Valid")
        else:
            lines.append("  Config: ✗ Invalid")
            for error in config_val.get("errors", []):
                lines.append(f"    - {error}")

        if config_val.get("warnings"):
            for warning in config_val["warnings"]:
                lines.append(f"    ⚠ {warning}")

        # Connectivity
        connectivity = diagnostics.get("connectivity", {})
        if connectivity.get("success"):
            latency = connectivity.get("latency_ms", "N/A")
            lines.append(f"  Connectivity: ✓ OK ({latency}ms)")
        else:
            lines.append(f"  Connectivity: ✗ Failed")
            if connectivity.get("error"):
                lines.append(f"    Error: {connectivity['error']}")

        # Memory prefetch
        prefetch = diagnostics.get("memory_prefetch", {})
        if prefetch.get("success"):
            count = prefetch.get("retrieved_count", 0)
            latency = prefetch.get("latency_ms", "N/A")
            lines.append(f"  Memory Prefetch: ✓ OK ({count} items, {latency}ms)")
        else:
            lines.append(f"  Memory Prefetch: ✗ Failed")
            if prefetch.get("error"):
                lines.append(f"    Error: {prefetch['error']}")

    lines.append("=" * 50)
    return "\n".join(lines)


async def doctor_command(hermes_home: Path) -> None:
    """Run comprehensive diagnostics."""
    print("Running FormalCC Memory Provider diagnostics...\n")

    runner = DiagnosticRunner(hermes_home)
    diagnostics = await runner.run_full_diagnostics()

    output = format_status_output(runner.config, diagnostics)
    print(output)

    # Summary
    all_passed = (
        diagnostics["config_validation"]["valid"]
        and diagnostics["connectivity"]["success"]
        and diagnostics["memory_prefetch"]["success"]
    )

    print()
    if all_passed:
        print("✓ All diagnostics passed")
    else:
        print("✗ Some diagnostics failed - see details above")


async def status_command(hermes_home: Path, verbose: bool = False) -> None:
    """Show provider status."""
    config_manager = ConfigManager(hermes_home)
    config = config_manager.load_config()

    diagnostics = None
    if verbose:
        runner = DiagnosticRunner(hermes_home)
        diagnostics = await runner.run_full_diagnostics()

    output = format_status_output(config, diagnostics)
    print(output)

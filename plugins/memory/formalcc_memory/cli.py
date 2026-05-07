"""CLI commands for formalcc-memory provider."""

import asyncio
import logging
import sys
from pathlib import Path
from typing import Optional

from .config import ConfigManager
from .diagnostics import doctor_command, status_command as status_cmd_enhanced
from plugins.formsy import RuntimeClient
from plugins.formsy.config_validator import ConfigValidator, ConfigGenerator

logger = logging.getLogger("formalcc.memory.cli")


async def status_command(hermes_home: Path, verbose: bool = False) -> None:
    """Show provider status."""
    await status_cmd_enhanced(hermes_home, verbose)


async def test_command(hermes_home: Path) -> None:
    """Test connectivity to Runtime API."""
    config_manager = ConfigManager(hermes_home)
    config = config_manager.load_config()

    print("Testing FormalCC Runtime API connectivity...")
    print(f"Base URL: {config.base_url}")

    try:
        async with RuntimeClient(
            base_url=config.base_url,
            api_key_env=config.api_key_env,
            timeout_s=config.timeout_s,
        ) as client:
            # Test memory prefetch
            from shared.models import MemoryPrefetchRequest

            request = MemoryPrefetchRequest(
                workspace_id=config.workspace_id,
                session_id="test_session",
                turn_id="test_turn_001",
                query="test query",
            )

            response = await client.memory_prefetch(request)
            print(f"✓ Memory prefetch successful ({response.elapsed_ms}ms)")
            print(f"  Retrieved: {response.retrieved_count} items")

    except Exception as e:
        print(f"✗ Test failed: {e}")
        return

    print("\n✓ All tests passed")


async def validate_command(hermes_home: Path) -> None:
    """Validate configuration."""
    config_manager = ConfigManager(hermes_home)
    config = config_manager.load_config()

    print("Validating FormalCC configuration...\n")

    config_dict = config.to_dict()
    result = ConfigValidator.validate_config(config_dict)

    if result.valid:
        print("✓ Configuration is valid")
    else:
        print("✗ Configuration has errors:")
        for error in result.errors:
            print(f"  - {error}")

    if result.warnings:
        print("\n⚠ Warnings:")
        for warning in result.warnings:
            print(f"  - {warning}")

    sys.exit(0 if result.valid else 1)


async def init_command(hermes_home: Path) -> None:
    """Initialize configuration."""
    config_file = hermes_home / "formalcc-config.json"
    env_file = hermes_home / ".env.formalcc"

    if config_file.exists():
        print(f"Config file already exists: {config_file}")
        response = input("Overwrite? (y/N): ")
        if response.lower() != 'y':
            print("Aborted")
            return

    # Generate config file
    ConfigGenerator.generate_config_file(config_file)
    print(f"✓ Created config file: {config_file}")

    # Generate .env template
    ConfigGenerator.generate_env_template(env_file)
    print(f"✓ Created .env template: {env_file}")

    print("\nNext steps:")
    print(f"1. Edit {env_file} and set your FORMALCC_API_KEY")
    print(f"2. Optionally edit {config_file} to customize settings")
    print("3. Run 'hermes formalcc-memory validate' to check configuration")
    print("4. Run 'hermes formalcc-memory test' to verify connectivity")


def main():
    """CLI entry point."""
    if len(sys.argv) < 2:
        print("Usage: hermes formalcc-memory <command> [options]")
        print("\nCommands:")
        print("  status      Show provider status")
        print("  status -v   Show detailed status with diagnostics")
        print("  test        Test connectivity to Runtime API")
        print("  doctor      Run comprehensive diagnostics")
        print("  validate    Validate configuration")
        print("  config      Show current configuration")
        print("  init        Initialize configuration files")
        sys.exit(1)

    command = sys.argv[1]
    hermes_home = Path.home() / ".hermes"

    if command == "status":
        verbose = "-v" in sys.argv or "--verbose" in sys.argv
        asyncio.run(status_command(hermes_home, verbose))
    elif command == "test":
        asyncio.run(test_command(hermes_home))
    elif command == "doctor":
        asyncio.run(doctor_command(hermes_home))
    elif command == "validate":
        asyncio.run(validate_command(hermes_home))
    elif command == "config":
        config_manager = ConfigManager(hermes_home)
        config = config_manager.load_config()
        import json
        print(json.dumps(config.to_dict(), indent=2))
    elif command == "init":
        asyncio.run(init_command(hermes_home))
    else:
        print(f"Unknown command: {command}")
        print("Run 'hermes formalcc-memory' for usage")
        sys.exit(1)

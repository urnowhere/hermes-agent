"""Sampling command plugin.

Registers a CLI-only /sampling slash command that can inspect and mutate
session sampling controls (temperature/top_p) without patching core CLI code.
"""

from __future__ import annotations

from typing import Optional, Tuple

_RESET_TOKENS = {"default", "none", "off", "auto", "null", "reset"}


def _display(value: Optional[float]) -> str:
    return "default" if value is None else str(value)


def _parse_temperature(raw: str) -> Optional[float]:
    token = (raw or "").strip().lower()
    if token in _RESET_TOKENS:
        return None
    value = float(token)
    if value < 0:
        raise ValueError("temperature must be >= 0")
    return value


def _parse_top_p(raw: str) -> Optional[float]:
    token = (raw or "").strip().lower()
    if token in _RESET_TOKENS:
        return None
    value = float(token)
    if value <= 0 or value > 1:
        raise ValueError("top_p must be in (0, 1]")
    return value


def _resolve_cli(context: dict) -> Tuple[object | None, str | None]:
    surface = (context or {}).get("surface")
    if surface != "cli":
        return None, "This command is CLI-only."
    cli = (context or {}).get("cli")
    if cli is None:
        return None, "Missing CLI context."
    return cli, None


def _apply_sampling(
    cli: object,
    *,
    temperature: Optional[float] = None,
    top_p: Optional[float] = None,
    set_temperature: bool = False,
    set_top_p: bool = False,
) -> None:
    if set_temperature:
        setattr(cli, "temperature", temperature)
        agent = getattr(cli, "agent", None)
        if agent is not None:
            setattr(agent, "temperature", temperature)

    if set_top_p:
        setattr(cli, "top_p", top_p)
        agent = getattr(cli, "agent", None)
        if agent is not None:
            setattr(agent, "top_p", top_p)


def _save_sampling(cli: object) -> tuple[bool, str]:
    try:
        from cli import save_config_value  # Lazy import to avoid heavy import at plugin load.
    except Exception as exc:
        return False, f"Could not import save_config_value: {exc}"

    temp_value = getattr(cli, "temperature", None)
    top_p_value = getattr(cli, "top_p", None)
    ok_temp = save_config_value("agent.temperature", temp_value)
    ok_top_p = save_config_value("agent.top_p", top_p_value)
    if ok_temp and ok_top_p:
        return True, f"Sampling saved: temperature={_display(temp_value)}, top_p={_display(top_p_value)}"
    return False, "Failed to save sampling config (session values are still active)."


def _sampling_status(cli: object) -> str:
    temp = getattr(cli, "temperature", None)
    top_p = getattr(cli, "top_p", None)
    return (
        "Sampling settings\n"
        f"  temperature: {_display(temp)}\n"
        f"  top_p:       {_display(top_p)}\n"
        "\n"
        "Usage:\n"
        "  /sampling\n"
        "  /sampling <temperature|default> <top_p|default>\n"
        "  /sampling set <temperature|default> <top_p|default>\n"
        "  /sampling temperature <value|default>\n"
        "  /sampling top_p <value|default>\n"
        "  /sampling reset\n"
        "  /sampling save"
    )


def sampling_command(args: str, context: dict | None = None) -> str:
    context = context or {}
    cli, err = _resolve_cli(context)
    if err:
        return err

    tokens = (args or "").strip().split()
    if not tokens or tokens[0].lower() in {"view", "show", "status"}:
        return _sampling_status(cli)

    cmd = tokens[0].lower()

    if cmd == "save":
        ok, message = _save_sampling(cli)
        return f"✅ {message}" if ok else f"❌ {message}"

    if cmd in _RESET_TOKENS:
        _apply_sampling(cli, temperature=None, top_p=None, set_temperature=True, set_top_p=True)
        return "Sampling reset: temperature=default, top_p=default"

    if cmd == "set":
        if len(tokens) != 3:
            return "Usage: /sampling set <temperature|default> <top_p|default>"
        temp_token, top_p_token = tokens[1], tokens[2]
    elif cmd in {"temperature", "temp"}:
        if len(tokens) == 1:
            return f"temperature={_display(getattr(cli, 'temperature', None))}"
        if len(tokens) != 2:
            return "Usage: /sampling temperature <value|default>"
        try:
            parsed = _parse_temperature(tokens[1])
        except Exception as exc:
            return f"Invalid temperature: {exc}"
        _apply_sampling(cli, temperature=parsed, set_temperature=True)
        return f"temperature set to {_display(parsed)}"
    elif cmd in {"top_p", "top-p", "topp"}:
        if len(tokens) == 1:
            return f"top_p={_display(getattr(cli, 'top_p', None))}"
        if len(tokens) != 2:
            return "Usage: /sampling top_p <value|default>"
        try:
            parsed = _parse_top_p(tokens[1])
        except Exception as exc:
            return f"Invalid top_p: {exc}"
        _apply_sampling(cli, top_p=parsed, set_top_p=True)
        return f"top_p set to {_display(parsed)}"
    else:
        # Shorthand: /sampling <temperature> <top_p>
        if len(tokens) == 2:
            temp_token, top_p_token = tokens[0], tokens[1]
        else:
            return (
                "Usage: /sampling, /sampling set <temp> <top_p>, "
                "/sampling <temp> <top_p>, /sampling temperature <v>, /sampling top_p <v>, /sampling save"
            )

    try:
        parsed_temp = _parse_temperature(temp_token)
        parsed_top_p = _parse_top_p(top_p_token)
    except Exception as exc:
        return f"Invalid sampling values: {exc}"

    _apply_sampling(
        cli,
        temperature=parsed_temp,
        top_p=parsed_top_p,
        set_temperature=True,
        set_top_p=True,
    )
    return f"Sampling updated: temperature={_display(parsed_temp)}, top_p={_display(parsed_top_p)}"


def register(ctx):
    ctx.register_command(
        name="sampling",
        handler=sampling_command,
        description="Show or change session sampling (temperature/top_p)",
        args_hint="[view|set|temperature|top_p|reset|save] ...",
        aliases=("sample",),
        cli_only=True,
    )

from __future__ import annotations


def _coerce_timeout(raw: object) -> float | None:
    try:
        timeout = float(raw)
    except (TypeError, ValueError):
        return None
    if timeout <= 0:
        return None
    return timeout


def _coerce_int(raw: object) -> int | None:
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return None
    if value < 0:
        return None
    return value


def _coerce_bool(raw: object) -> bool | None:
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, str):
        value = raw.strip().lower()
        if value in {"1", "true", "yes", "on"}:
            return True
        if value in {"0", "false", "no", "off"}:
            return False
    return None


def _load_provider_config(provider_id: str) -> dict[str, object] | None:
    if not provider_id:
        return None

    try:
        from hermes_cli.config import load_config
        config = load_config()
    except Exception:
        return None

    providers = config.get("providers", {}) if isinstance(config, dict) else {}
    provider_config = (
        providers.get(provider_id, {}) if isinstance(providers, dict) else {}
    )
    if isinstance(provider_config, dict):
        return provider_config
    return None


def get_provider_request_timeout(
    provider_id: str, model: str | None = None
) -> float | None:
    """Return a configured provider request timeout in seconds, if any."""
    provider_config = _load_provider_config(provider_id)
    if provider_config is None:
        return None

    model_config = _get_model_config(provider_config, model)
    if model_config is not None:
        timeout = _coerce_timeout(model_config.get("timeout_seconds"))
        if timeout is not None:
            return timeout

    return _coerce_timeout(provider_config.get("request_timeout_seconds"))


def get_provider_stale_timeout(
    provider_id: str, model: str | None = None
) -> float | None:
    """Return a configured non-stream stale timeout in seconds, if any."""
    provider_config = _load_provider_config(provider_id)
    if provider_config is None:
        return None

    model_config = _get_model_config(provider_config, model)
    if model_config is not None:
        timeout = _coerce_timeout(model_config.get("stale_timeout_seconds"))
        if timeout is not None:
            return timeout

    return _coerce_timeout(provider_config.get("stale_timeout_seconds"))


def get_provider_retry_attempts(
    provider_id: str, model: str | None = None
) -> int | None:
    """Return a configured API retry count, if any."""
    provider_config = _load_provider_config(provider_id)
    if provider_config is None:
        return None

    model_config = _get_model_config(provider_config, model)
    if model_config is not None:
        attempts = _coerce_int(model_config.get("retry_attempts"))
        if attempts is not None:
            return attempts

    return _coerce_int(provider_config.get("retry_attempts"))


def get_provider_retry_backoff_seconds(
    provider_id: str, model: str | None = None
) -> tuple[float, float] | None:
    """Return configured retry backoff ``(base_seconds, max_seconds)``, if any."""
    provider_config = _load_provider_config(provider_id)
    if provider_config is None:
        return None

    model_config = _get_model_config(provider_config, model)
    candidates = [model_config, provider_config]
    for cfg in candidates:
        if not isinstance(cfg, dict):
            continue
        base = _coerce_timeout(cfg.get("retry_backoff_seconds"))
        max_delay = _coerce_timeout(cfg.get("retry_backoff_max_seconds"))
        if base is None and max_delay is None:
            continue
        resolved_base = base if base is not None else 2.0
        resolved_max = max_delay if max_delay is not None else 60.0
        if resolved_max < resolved_base:
            resolved_max = resolved_base
        return resolved_base, resolved_max

    return None


def get_provider_ignore_env_proxy(
    provider_id: str, model: str | None = None
) -> bool | None:
    """Return whether env proxy vars should be ignored for this provider/model."""
    provider_config = _load_provider_config(provider_id)
    if provider_config is None:
        return None

    model_config = _get_model_config(provider_config, model)
    if model_config is not None:
        ignore = _coerce_bool(model_config.get("ignore_env_proxy"))
        if ignore is not None:
            return ignore

    return _coerce_bool(provider_config.get("ignore_env_proxy"))


def _get_model_config(
    provider_config: dict[str, object], model: str | None
) -> dict[str, object] | None:
    if not model:
        return None

    models = provider_config.get("models", {})
    model_config = models.get(model, {}) if isinstance(models, dict) else {}
    if isinstance(model_config, dict):
        return model_config
    return None

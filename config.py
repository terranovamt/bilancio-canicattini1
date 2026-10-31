from __future__ import annotations

import os
import re
from collections.abc import Mapping
from dataclasses import dataclass, field


class ConfigurationError(ValueError):
    """Raised when environment configuration is missing or invalid."""


def _required_text(environ: Mapping[str, str], name: str) -> str:
    value = environ.get(name, "").strip()
    if not value:
        raise ConfigurationError(f"Missing required setting: {name}")
    return value


def _optional_text(environ: Mapping[str, str], name: str) -> str | None:
    value = environ.get(name, "").strip()
    return value or None


def _positive_integer(value: str, name: str) -> int:
    try:
        parsed = int(value)
    except ValueError as error:
        raise ConfigurationError(f"Setting {name} must be a positive integer") from error
    if parsed <= 0:
        raise ConfigurationError(f"Setting {name} must be a positive integer")
    return parsed


def _optional_non_negative_integer(
    environ: Mapping[str, str], name: str
) -> int | None:
    value = _optional_text(environ, name)
    if value is None:
        return None
    try:
        parsed = int(value)
    except ValueError as error:
        raise ConfigurationError(f"Setting {name} must be a non-negative integer") from error
    if parsed < 0:
        raise ConfigurationError(f"Setting {name} must be a non-negative integer")
    return parsed


def _parse_allowed_user_ids(value: str) -> frozenset[int]:
    parts = [part.strip() for part in value.split(",")]
    if not parts or any(not part for part in parts):
        raise ConfigurationError("Setting ALLOWED_USER_IDS must be a comma-separated list")
    return frozenset(_positive_integer(part, "ALLOWED_USER_IDS") for part in parts)


def _parse_boolean(value: str, name: str, default: bool) -> bool:
    normalized = value.strip().lower()
    if not normalized:
        return default
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ConfigurationError(f"Setting {name} must be true or false")


@dataclass(frozen=True, slots=True)
class Settings:
    bot_token: str = field(repr=False)
    allowed_user_ids: frozenset[int]
    admin_user_id: int
    anthropic_api_key: str | None = field(repr=False)
    anthropic_model: str | None
    bilancio_url: str | None
    bilancio_user: str | None = field(repr=False)
    bilancio_password: str | None = field(repr=False)
    gemini_api_key: str | None = field(repr=False)
    gemini_model: str | None
    playwright_browser_channel: str | None
    data_path: str
    storage_path: str
    timezone: str
    dry_run: bool
    puid: int | None
    pgid: int | None
    ai_timeout_seconds: int = 600
    gemini_models: tuple[str, ...] = ()
    gemini_rpm_limits: tuple[int, ...] = ()
    gemini_rpd_limits: tuple[int, ...] = ()
    gemini_quota_warn_percent: int = 80

    @classmethod
    def from_env(
        cls, environ: Mapping[str, str] | None = None
    ) -> Settings:
        source = os.environ if environ is None else environ

        bot_token = _required_text(source, "BOT_TOKEN")
        allowed_user_ids = _parse_allowed_user_ids(
            _required_text(source, "ALLOWED_USER_IDS")
        )
        admin_user_id = _positive_integer(
            _required_text(source, "ADMIN_USER_ID"), "ADMIN_USER_ID"
        )
        if admin_user_id not in allowed_user_ids:
            raise ConfigurationError("ADMIN_USER_ID must also be in ALLOWED_USER_IDS")

        anthropic_api_key = _optional_text(source, "ANTHROPIC_API_KEY")
        anthropic_model = _optional_text(source, "ANTHROPIC_MODEL")
        if bool(anthropic_api_key) != bool(anthropic_model):
            raise ConfigurationError(
                "ANTHROPIC_API_KEY and ANTHROPIC_MODEL must both be set or both be empty"
            )

        bilancio_url = _optional_text(source, "BILANCIO_URL")
        bilancio_user = _optional_text(source, "BILANCIO_USER")
        bilancio_password = _optional_text(source, "BILANCIO_PASSWORD")
        bilancio_values = (bilancio_url, bilancio_user, bilancio_password)
        if any(bilancio_values) and not all(bilancio_values):
            raise ConfigurationError(
                "BILANCIO_URL, BILANCIO_USER, and BILANCIO_PASSWORD must all be set together"
            )
        playwright_browser_channel = _optional_text(
            source,
            "PLAYWRIGHT_BROWSER_CHANNEL",
        )
        if playwright_browser_channel not in {None, "chrome", "msedge"}:
            raise ConfigurationError(
                "Setting PLAYWRIGHT_BROWSER_CHANNEL must be chrome, msedge, or empty"
            )
        gemini_api_key = _optional_text(source, "GEMINI_API_KEY")
        gemini_model = _optional_text(source, "GEMINI_MODEL")
        if gemini_model is not None and gemini_api_key is None:
            raise ConfigurationError(
                "Setting GEMINI_MODEL requires GEMINI_API_KEY"
            )
        chain_text = _optional_text(source, "GEMINI_MODELS")
        models = tuple(part.strip() for part in chain_text.split(",")) if chain_text else ()
        if models and (
            not gemini_api_key or len(set(models)) != len(models)
            or any(not re.fullmatch(r"[a-zA-Z0-9._-]{1,100}", model) for model in models)
        ):
            raise ConfigurationError("GEMINI_MODELS requires a key and unique valid model IDs")
        limits: dict[str, tuple[int, ...]] = {}
        for name in ("GEMINI_RPM_LIMITS", "GEMINI_RPD_LIMITS"):
            text = _optional_text(source, name)
            values = tuple(_positive_integer(part.strip(), name) for part in text.split(",")) if text else ()
            if (models and len(values) != len(models)) or (values and not models):
                raise ConfigurationError(f"{name} must have one limit for each GEMINI_MODELS entry")
            limits[name] = values
        warn_percent = _positive_integer(source.get("GEMINI_QUOTA_WARN_PERCENT", "80"), "GEMINI_QUOTA_WARN_PERCENT")
        if warn_percent > 100:
            raise ConfigurationError("GEMINI_QUOTA_WARN_PERCENT must not exceed 100")

        return cls(
            bot_token=bot_token,
            allowed_user_ids=allowed_user_ids,
            admin_user_id=admin_user_id,
            anthropic_api_key=anthropic_api_key,
            anthropic_model=anthropic_model,
            bilancio_url=bilancio_url,
            bilancio_user=bilancio_user,
            bilancio_password=bilancio_password,
            gemini_api_key=gemini_api_key,
            gemini_model=gemini_model or ("gemini-1.5-flash" if gemini_api_key else None),
            gemini_models=models,
            gemini_rpm_limits=limits["GEMINI_RPM_LIMITS"],
            gemini_rpd_limits=limits["GEMINI_RPD_LIMITS"],
            gemini_quota_warn_percent=warn_percent,
            playwright_browser_channel=playwright_browser_channel,
            data_path=_optional_text(source, "DATA_PATH") or "/data",
            storage_path=_optional_text(source, "STORAGE_PATH") or "/scontrini",
            timezone=_optional_text(source, "TZ") or "Europe/Rome",
            dry_run=_parse_boolean(source.get("DRY_RUN", ""), "DRY_RUN", False),
            puid=_optional_non_negative_integer(source, "PUID"),
            pgid=_optional_non_negative_integer(source, "PGID"),
            ai_timeout_seconds=_positive_integer(
                _optional_text(source, "GEMINI_TIMEOUT_SECONDS")
                or _optional_text(source, "LOCAL_VISION_TIMEOUT_SECONDS")
                or "600",
                "GEMINI_TIMEOUT_SECONDS",
            ),
        )

"""Build notifier instances from configuration (+ environment variables)."""

from __future__ import annotations

import os
from typing import Any, Iterable, Mapping, TextIO

import httpx

from ..config import Config, ConfigError, NotifierSpec, substitute_env
from .base import Notifier
from .email import EmailNotifier, SmtpFactory
from .stdout import StdoutNotifier
from .telegram import TelegramNotifier
from .webhook import WebhookNotifier

# Defaults let a bare ``type: telegram`` work with nothing but environment variables.
_DEFAULT_OPTIONS: dict[str, dict[str, str]] = {
    "telegram": {"token": "${TELEGRAM_BOT_TOKEN}", "chat_id": "${TELEGRAM_CHAT_ID}"},
    "email": {
        "host": "${SMTP_HOST}",
        "port": "${SMTP_PORT:-587}",
        "username": "${SMTP_USER:-}",
        "password": "${SMTP_PASSWORD:-}",
        "sender": "${MAIL_FROM}",
        "recipients": "${MAIL_TO}",
        "starttls": "true",
    },
    "webhook": {"url": "${WEBHOOK_URL}"},
    "stdout": {},
}


def build_notifiers(
    config: Config,
    names: Iterable[str] | None = None,
    *,
    client: httpx.Client | None = None,
    env: Mapping[str, str] | None = None,
    smtp_factory: SmtpFactory | None = None,
    stream: TextIO | None = None,
) -> dict[str, Notifier]:
    """Instantiate the notifiers called ``names`` (all configured by default).

    Environment references in options are resolved here; a missing variable
    raises :class:`ConfigError` naming the notifier and the variable.
    """
    env = os.environ if env is None else env
    wanted = set(names) if names is not None else {spec.name for spec in config.notifiers}
    client = client or httpx.Client(timeout=config.timeout, headers={"User-Agent": config.user_agent})
    built: dict[str, Notifier] = {}
    for spec in config.notifiers:
        if spec.name not in wanted:
            continue
        options = _resolve(spec, env)
        built[spec.name] = _instantiate(spec, options, client, smtp_factory, stream)
    missing = wanted - set(built)
    if missing:
        raise ConfigError(f"unknown notifier(s): {', '.join(sorted(missing))}")
    return built


def _resolve(spec: NotifierSpec, env: Mapping[str, str]) -> dict[str, str]:
    merged: dict[str, str] = {**_DEFAULT_OPTIONS.get(spec.type, {}), **spec.options}
    resolved: dict[str, str] = {}
    for key, value in merged.items():
        try:
            resolved[key] = substitute_env(value, env)
        except ConfigError as exc:
            raise ConfigError(f"notifier {spec.name!r} ({spec.type}): option {key!r}: {exc}") from exc
    return resolved


def _instantiate(
    spec: NotifierSpec,
    options: dict[str, str],
    client: httpx.Client,
    smtp_factory: SmtpFactory | None,
    stream: TextIO | None,
) -> Notifier:
    if spec.type == "stdout":
        return StdoutNotifier(spec.name, stream=stream)
    if spec.type == "webhook":
        headers = {k[len("header_") :]: v for k, v in options.items() if k.startswith("header_")}
        return WebhookNotifier(spec.name, _require(spec, options, "url"), client, headers=headers)
    if spec.type == "telegram":
        return TelegramNotifier(
            spec.name,
            _require(spec, options, "token"),
            _require(spec, options, "chat_id"),
            client,
            api_base=options.get("api_base", "https://api.telegram.org"),
        )
    if spec.type == "email":
        kwargs: dict[str, Any] = {
            "host": _require(spec, options, "host"),
            "port": int(options.get("port") or 587),
            "sender": _require(spec, options, "sender"),
            "recipients": [r for r in _require(spec, options, "recipients").split(",")],
            "username": options.get("username") or None,
            "password": options.get("password") or None,
            "starttls": options.get("starttls", "true").lower() not in ("false", "0", "no"),
        }
        if smtp_factory is not None:
            kwargs["smtp_factory"] = smtp_factory
        return EmailNotifier(spec.name, **kwargs)
    raise ConfigError(f"notifier {spec.name!r}: unsupported type {spec.type!r}")


def _require(spec: NotifierSpec, options: Mapping[str, str], key: str) -> str:
    value = options.get(key, "").strip()
    if not value:
        raise ConfigError(f"notifier {spec.name!r} ({spec.type}): option {key!r} is required")
    return value

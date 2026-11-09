"""Configuration: YAML loading, validation and environment substitution.

Secrets never live in the YAML file. Notifier options may reference
environment variables as ``${NAME}`` or ``${NAME:-default}``; they are
resolved when the notifier is built, so commands that do not send anything
(``history``, ``export``) work without the variables being set.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

import yaml

DEFAULT_USER_AGENT = "change-monitor/0.1 (+https://github.com/Feldwebelkuchen/change-monitor)"

CONDITION_KINDS = ("any_change", "below", "above", "contains", "not_contains", "regex")
EXTRACT_MODES = ("text", "attr", "html")
NOTIFIER_TYPES = ("stdout", "webhook", "telegram", "email")

_ENV_PATTERN = re.compile(r"\$\{(?P<name>[A-Za-z_][A-Za-z0-9_]*)(?::-(?P<default>[^}]*))?\}")
_WATCH_KEYS = {
    "name",
    "url",
    "selector",
    "extract",
    "attr",
    "regex",
    "number",
    "condition",
    "notify",
    "interval",
    "failures_before_alert",
}
_TOP_KEYS = {"database", "user_agent", "timeout", "interval", "failures_before_alert", "notifiers", "watches"}


class ConfigError(ValueError):
    """Raised for unreadable or invalid configuration."""


@dataclass(frozen=True)
class NumberSpec:
    """How to turn the extracted text into a number.

    ``decimal`` is ``"auto"`` (detect), ``","`` or ``"."``.
    """

    decimal: str = "auto"


@dataclass(frozen=True)
class Condition:
    """When to alert.

    * ``any_change``: the extracted value differs from the previous one.
    * ``below`` / ``above``: numeric value crosses ``threshold``.
    * ``contains`` / ``not_contains``: case-insensitive substring test.
    * ``regex``: pattern found in the value.

    Threshold and text conditions alert when they *start* to hold and again
    whenever the value changes while they still hold - never on every run.
    """

    kind: str = "any_change"
    threshold: float | None = None
    pattern: str | None = None

    def describe(self) -> str:
        if self.kind == "any_change":
            return "any change"
        if self.kind in ("below", "above"):
            return f"{self.kind} {self.threshold:g}"
        return f"{self.kind} {self.pattern!r}"


@dataclass(frozen=True)
class Watch:
    """One monitored value."""

    name: str
    url: str
    selector: str
    extract: str = "text"
    attr: str | None = None
    regex: re.Pattern[str] | None = None
    number: NumberSpec | None = None
    condition: Condition = field(default_factory=Condition)
    notify: tuple[str, ...] = ()
    interval: int | None = None
    failures_before_alert: int | None = None


@dataclass(frozen=True)
class NotifierSpec:
    """A notification channel; ``options`` may contain ``${ENV}`` references."""

    name: str
    type: str
    options: Mapping[str, str]


@dataclass(frozen=True)
class Config:
    """Validated monitor configuration."""

    watches: tuple[Watch, ...]
    notifiers: tuple[NotifierSpec, ...]
    database: Path
    user_agent: str = DEFAULT_USER_AGENT
    timeout: float = 15.0
    interval: int = 0
    failures_before_alert: int = 3
    source: Path | None = None

    def watch(self, name: str) -> Watch:
        for watch in self.watches:
            if watch.name == name:
                return watch
        raise ConfigError(f"unknown watch {name!r}; defined: {', '.join(w.name for w in self.watches)}")

    def notifier(self, name: str) -> NotifierSpec:
        for spec in self.notifiers:
            if spec.name == name:
                return spec
        raise ConfigError(f"unknown notifier {name!r}; defined: {', '.join(n.name for n in self.notifiers)}")

    def notifiers_for(self, watch: Watch) -> tuple[NotifierSpec, ...]:
        """Notifiers a watch alerts through (all configured ones by default)."""
        if not watch.notify:
            return self.notifiers
        return tuple(self.notifier(name) for name in watch.notify)

    def failures_threshold(self, watch: Watch) -> int:
        return watch.failures_before_alert or self.failures_before_alert


# --------------------------------------------------------------------------- #
# Environment substitution
# --------------------------------------------------------------------------- #


def substitute_env(value: str, env: Mapping[str, str] | None = None) -> str:
    """Replace ``${NAME}`` / ``${NAME:-default}`` with environment values."""
    env = os.environ if env is None else env

    def replace(match: re.Match[str]) -> str:
        name, default = match.group("name"), match.group("default")
        if name in env and env[name] != "":
            return env[name]
        if default is not None:
            return default
        raise ConfigError(f"environment variable {name} is not set")

    return _ENV_PATTERN.sub(replace, value)


def missing_env(value: str, env: Mapping[str, str] | None = None) -> list[str]:
    """Names of referenced variables that are unset and have no default."""
    env = os.environ if env is None else env
    return [
        m.group("name")
        for m in _ENV_PATTERN.finditer(value)
        if m.group("default") is None and not env.get(m.group("name"))
    ]


# --------------------------------------------------------------------------- #
# Loading
# --------------------------------------------------------------------------- #


def load_config(path: str | Path) -> Config:
    """Read and validate a YAML configuration file."""
    file = Path(path)
    if not file.is_file():
        raise ConfigError(f"config file not found: {file}")
    try:
        data = yaml.safe_load(file.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ConfigError(f"{file}: invalid YAML: {exc}") from exc
    return parse_config(data, source=file)


def parse_config(data: Any, *, source: Path | None = None) -> Config:
    """Validate a parsed YAML mapping; all problems are reported together."""
    label = str(source) if source else "<config>"
    if not isinstance(data, dict):
        raise ConfigError(f"{label}: configuration must be a mapping")
    errors: list[str] = []
    _check_keys(data, _TOP_KEYS, "config", errors)

    base_dir = source.parent if source else Path.cwd()
    database = data.get("database", "change-monitor.db")
    if not isinstance(database, str) or not database.strip():
        errors.append("'database' must be a file path")
        database = "change-monitor.db"
    db_path = Path(database)
    if not db_path.is_absolute():
        db_path = base_dir / db_path

    user_agent = data.get("user_agent", DEFAULT_USER_AGENT)
    if not isinstance(user_agent, str) or not user_agent.strip():
        errors.append("'user_agent' must be a non-empty string")
        user_agent = DEFAULT_USER_AGENT

    timeout = _number(data, "timeout", 15.0, errors, minimum=0.1)
    interval = int(_number(data, "interval", 0, errors))
    failures = int(_number(data, "failures_before_alert", 3, errors, minimum=1))

    notifiers = _parse_notifiers(data.get("notifiers"), errors)
    watches = _parse_watches(data.get("watches"), {n.name for n in notifiers}, errors)

    if errors:
        raise ConfigError(f"{label}: invalid configuration:\n  - " + "\n  - ".join(errors))
    return Config(
        watches=watches,
        notifiers=notifiers,
        database=db_path,
        user_agent=user_agent.strip(),
        timeout=timeout,
        interval=interval,
        failures_before_alert=failures,
        source=source,
    )


# --------------------------------------------------------------------------- #
# Section parsers
# --------------------------------------------------------------------------- #


def _check_keys(section: Mapping[str, Any], allowed: set[str], label: str, errors: list[str]) -> None:
    unknown = sorted(str(k) for k in section if k not in allowed)
    if unknown:
        errors.append(f"{label}: unknown option(s): {', '.join(unknown)}")


def _number(section: Mapping[str, Any], key: str, default: float, errors: list[str], minimum: float = 0.0) -> float:
    value = section.get(key, default)
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value < minimum:
        errors.append(f"'{key}' must be a number >= {minimum:g}")
        return default
    return value


def _parse_notifiers(raw: Any, errors: list[str]) -> tuple[NotifierSpec, ...]:
    if raw is None:
        return (NotifierSpec("stdout", "stdout", {}),)
    if not isinstance(raw, dict):
        errors.append("'notifiers' must be a mapping of name -> settings")
        return ()
    specs: list[NotifierSpec] = []
    for name, settings in raw.items():
        if not isinstance(settings, dict):
            errors.append(f"notifiers.{name}: must be a mapping with a 'type'")
            continue
        type_ = settings.get("type")
        if type_ not in NOTIFIER_TYPES:
            errors.append(f"notifiers.{name}: 'type' must be one of {', '.join(NOTIFIER_TYPES)}")
            continue
        options = {str(k): str(v) for k, v in settings.items() if k != "type" and v is not None}
        specs.append(NotifierSpec(str(name), str(type_), options))
    return tuple(specs)


def _parse_watches(raw: Any, notifier_names: set[str], errors: list[str]) -> tuple[Watch, ...]:
    if not isinstance(raw, list) or not raw:
        errors.append("'watches' must be a non-empty list")
        return ()
    watches: list[Watch] = []
    seen: set[str] = set()
    for index, item in enumerate(raw):
        label = f"watches[{index}]"
        if not isinstance(item, dict):
            errors.append(f"{label}: must be a mapping")
            continue
        _check_keys(item, _WATCH_KEYS, label, errors)
        name = item.get("name")
        if not isinstance(name, str) or not name.strip():
            errors.append(f"{label}: 'name' is required")
            continue
        name = name.strip()
        label = f"watch {name!r}"
        if name in seen:
            errors.append(f"{label}: duplicate name")
        seen.add(name)

        url = item.get("url")
        if not isinstance(url, str) or not re.match(r"^https?://", url.strip()):
            errors.append(f"{label}: 'url' must start with http:// or https://")
            url = ""
        selector = item.get("selector")
        if not isinstance(selector, str) or not selector.strip():
            errors.append(f"{label}: 'selector' (CSS) is required")
            selector = ""

        extract = str(item.get("extract", "text"))
        if extract not in EXTRACT_MODES:
            errors.append(f"{label}: 'extract' must be one of {', '.join(EXTRACT_MODES)}")
        attr = item.get("attr")
        if extract == "attr" and not attr:
            errors.append(f"{label}: 'attr' is required when extract is 'attr'")

        regex = None
        if item.get("regex") is not None:
            try:
                regex = re.compile(str(item["regex"]), re.DOTALL)
            except re.error as exc:
                errors.append(f"{label}: invalid regex: {exc}")

        number = _parse_number_spec(item.get("number"), label, errors)
        condition = _parse_condition(item.get("condition"), label, errors)
        if condition.kind in ("below", "above") and number is None:
            number = NumberSpec()  # numeric conditions imply number parsing

        notify_raw = item.get("notify", [])
        if isinstance(notify_raw, str):
            notify_raw = [notify_raw]
        if not isinstance(notify_raw, list) or not all(isinstance(n, str) for n in notify_raw):
            errors.append(f"{label}: 'notify' must be a list of notifier names")
            notify_raw = []
        for n in notify_raw:
            if n not in notifier_names:
                errors.append(f"{label}: unknown notifier {n!r}")

        interval = item.get("interval")
        if interval is not None and (isinstance(interval, bool) or not isinstance(interval, int) or interval < 0):
            errors.append(f"{label}: 'interval' must be a non-negative number of seconds")
            interval = None
        failures = item.get("failures_before_alert")
        if failures is not None and (isinstance(failures, bool) or not isinstance(failures, int) or failures < 1):
            errors.append(f"{label}: 'failures_before_alert' must be a positive integer")
            failures = None

        watches.append(
            Watch(
                name=name,
                url=url.strip(),
                selector=selector.strip(),
                extract=extract,
                attr=str(attr) if attr else None,
                regex=regex,
                number=number,
                condition=condition,
                notify=tuple(notify_raw),
                interval=interval,
                failures_before_alert=failures,
            )
        )
    return tuple(watches)


def _parse_number_spec(raw: Any, label: str, errors: list[str]) -> NumberSpec | None:
    if raw is None or raw is False:
        return None
    if raw is True:
        return NumberSpec()
    if not isinstance(raw, dict):
        errors.append(f"{label}: 'number' must be true or a mapping like {{decimal: ','}}")
        return None
    decimal = str(raw.get("decimal", "auto"))
    if decimal not in ("auto", ",", "."):
        errors.append(f"{label}: number.decimal must be 'auto', ',' or '.'")
        decimal = "auto"
    return NumberSpec(decimal=decimal)


def _parse_condition(raw: Any, label: str, errors: list[str]) -> Condition:
    if raw is None or raw == "any_change":
        return Condition()
    if isinstance(raw, str):
        errors.append(f"{label}: condition {raw!r} is not valid; use any_change or a mapping like {{below: 50}}")
        return Condition()
    if not isinstance(raw, dict) or len(raw) != 1:
        errors.append(f"{label}: 'condition' must be any_change or a single-key mapping (below/above/contains/not_contains/regex)")
        return Condition()
    kind, value = next(iter(raw.items()))
    if kind not in CONDITION_KINDS:
        errors.append(f"{label}: unknown condition {kind!r}; valid: {', '.join(CONDITION_KINDS)}")
        return Condition()
    if kind == "any_change":
        return Condition()
    if kind in ("below", "above"):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            errors.append(f"{label}: condition.{kind} needs a numeric threshold")
            return Condition()
        return Condition(kind=kind, threshold=float(value))
    if not isinstance(value, str) or value == "":
        errors.append(f"{label}: condition.{kind} needs a non-empty string")
        return Condition()
    if kind == "regex":
        try:
            re.compile(value)
        except re.error as exc:
            errors.append(f"{label}: condition.regex is invalid: {exc}")
            return Condition()
    return Condition(kind=kind, pattern=value)

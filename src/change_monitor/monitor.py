"""The check cycle: fetch -> extract -> compare -> store -> notify."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field, replace
from datetime import datetime
from typing import Callable, Iterable, Mapping

import httpx

from .conditions import evaluate
from .config import Config, Watch
from .extract import ExtractionError, extract_value
from .fetch import FetchError, fetch_text, make_client
from .notify import Notifier, build_notifiers, dispatch
from .numbers import NumberParseError, parse_number
from .store import Observation, Store, utcnow
from .templates import change_message, failing_message, recovered_message

log = logging.getLogger(__name__)

OUTCOMES = ("baseline", "unchanged", "changed", "alert", "skipped", "error", "failing")


@dataclass
class WatchResult:
    """What happened to one watch during a run."""

    watch: str
    outcome: str
    value: str | None = None
    note: str = ""
    alerted: bool = False
    notify_failures: list[str] = field(default_factory=list)


@dataclass
class RunReport:
    """Results of one ``run_once`` call."""

    started: datetime
    results: list[WatchResult] = field(default_factory=list)

    @property
    def errors(self) -> int:
        return sum(1 for r in self.results if r.outcome in ("error", "failing"))

    @property
    def alerts(self) -> int:
        return sum(1 for r in self.results if r.alerted)

    @property
    def notify_failures(self) -> list[str]:
        return [f for r in self.results for f in r.notify_failures]

    def table(self) -> str:
        """Aligned plain-text summary for the console."""
        rows = [(r.watch, r.outcome, (r.value or "")[:40], r.note) for r in self.results]
        header = ("watch", "outcome", "value", "note")
        widths = [max(len(str(row[i])) for row in rows + [header]) for i in range(4)]
        fmt = "  ".join("{:<" + str(w) + "}" for w in widths)
        lines = [fmt.format(*header), fmt.format(*("-" * w for w in widths))]
        lines += [fmt.format(*row) for row in rows]
        return "\n".join(line.rstrip() for line in lines)


def run_once(
    config: Config,
    store: Store,
    *,
    client: httpx.Client | None = None,
    notifiers: Mapping[str, Notifier] | None = None,
    now: datetime | None = None,
    force: bool = False,
    only: Iterable[str] | None = None,
) -> RunReport:
    """Check every watch once and return a :class:`RunReport`.

    Args:
        config: Validated configuration.
        store: Open SQLite store.
        client: HTTP client (``make_client`` by default; inject for tests).
        notifiers: Prebuilt notifiers by name (built from ``config`` by default).
        now: Timestamp for this run (defaults to the current UTC time).
        force: Ignore per-watch intervals and check everything.
        only: Restrict the run to these watch names.
    """
    now = now or utcnow()
    report = RunReport(started=now)
    selected = [w for w in config.watches if only is None or w.name in set(only)]
    own_client = client is None
    client = client or make_client(config.user_agent, config.timeout)
    if notifiers is None:
        needed = {spec.name for w in selected for spec in config.notifiers_for(w)}
        notifiers = build_notifiers(config, needed, client=client)
    try:
        for watch in selected:
            channels = [notifiers[spec.name] for spec in config.notifiers_for(watch) if spec.name in notifiers]
            result = check_watch(watch, config, store, client, channels, now=now, force=force)
            report.results.append(result)
            log.info("%s: %s %s", watch.name, result.outcome, result.note)
    finally:
        if own_client:
            client.close()
    return report


def check_watch(
    watch: Watch,
    config: Config,
    store: Store,
    client: httpx.Client,
    channels: list[Notifier],
    *,
    now: datetime,
    force: bool = False,
) -> WatchResult:
    """Run the full cycle for a single watch."""
    state = store.state(watch.name)
    interval = watch.interval if watch.interval is not None else config.interval
    if not force and state.last_checked is not None and interval > 0:
        elapsed = (now - state.last_checked).total_seconds()
        if elapsed < interval:
            return WatchResult(watch.name, "skipped", note=f"checked {int(elapsed)}s ago, interval {interval}s")

    try:
        html = fetch_text(client, watch.url)
        value = extract_value(html, watch)
        number = parse_number(value, watch.number.decimal) if watch.number else None
    except (FetchError, ExtractionError, NumberParseError) as exc:
        return _handle_failure(watch, config, store, channels, state, str(exc), now)

    previous = store.last_ok(watch.name)
    current = Observation.ok(watch.name, now, value, number)
    decision = evaluate(watch.condition, previous, current, previously_triggered=state.triggered)

    result = WatchResult(watch.name, "unchanged", value=value, note=decision.reason)
    if previous is None:
        result.outcome = "baseline"
    elif decision.alert:
        result.outcome = "alert"
        result.notify_failures += dispatch(channels, change_message(watch, previous, current, decision.reason))
        result.alerted = True
        current = replace(current, alerted=True)
        state.last_alert = now
    elif previous.hash != current.hash:
        result.outcome = "changed"

    if state.failing_alerted:
        result.notify_failures += dispatch(channels, recovered_message(watch, current, state.consecutive_failures))
        result.note = f"recovered; {result.note}"

    store.record(current)
    state.consecutive_failures = 0
    state.failing_alerted = False
    state.triggered = decision.triggered
    state.last_checked = now
    store.save_state(state)
    return result


def _handle_failure(watch, config, store, channels, state, error: str, now: datetime) -> WatchResult:
    state.consecutive_failures += 1
    state.last_checked = now
    store.record(Observation.failure(watch.name, now, error))
    result = WatchResult(watch.name, "error", note=f"{error} ({state.consecutive_failures} in a row)")
    threshold = config.failures_threshold(watch)
    if state.consecutive_failures >= threshold and not state.failing_alerted:
        result.outcome = "failing"
        result.notify_failures += dispatch(channels, failing_message(watch, state.consecutive_failures, error, now))
        result.alerted = True
        state.failing_alerted = True
        state.last_alert = now
    store.save_state(state)
    return result


def run_loop(
    config: Config,
    store: Store,
    *,
    interval: float,
    client: httpx.Client | None = None,
    notifiers: Mapping[str, Notifier] | None = None,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], datetime] = utcnow,
    max_iterations: int | None = None,
    on_report: Callable[[RunReport], None] | None = None,
) -> int:
    """Run ``run_once`` every ``interval`` seconds until interrupted.

    Returns the number of completed iterations. ``max_iterations`` and the
    injectable ``sleep``/``clock`` exist for tests.
    """
    own_client = client is None
    client = client or make_client(config.user_agent, config.timeout)
    if notifiers is None:
        notifiers = build_notifiers(config, client=client)
    iterations = 0
    try:
        while max_iterations is None or iterations < max_iterations:
            report = run_once(config, store, client=client, notifiers=notifiers, now=clock())
            iterations += 1
            if on_report:
                on_report(report)
            if max_iterations is not None and iterations >= max_iterations:
                break
            sleep(interval)
    except KeyboardInterrupt:
        log.info("interrupted after %d iteration(s)", iterations)
    finally:
        if own_client:
            client.close()
    return iterations

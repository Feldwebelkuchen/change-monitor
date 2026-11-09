"""Command-line interface ``cmon``.

Exit codes: ``0`` all good, ``1`` at least one watch failed or a notification
could not be delivered, ``2`` configuration error, ``130`` interrupted.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path
from typing import Sequence

from . import __version__
from .config import Config, ConfigError, load_config, missing_env
from .fetch import make_client
from .monitor import RunReport, run_loop, run_once
from .notify import build_notifiers, dispatch
from .store import Store, utcnow
from .templates import test_message

EXIT_OK = 0
EXIT_PROBLEMS = 1
EXIT_CONFIG = 2
EXIT_INTERRUPTED = 130

log = logging.getLogger("change_monitor")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="cmon", description="Monitor values on web pages and alert on changes.")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    def common(p: argparse.ArgumentParser, with_db: bool = True) -> None:
        p.add_argument("config", help="path to the YAML configuration")
        if with_db:
            p.add_argument("--db", help="override the SQLite database path from the config")
        group = p.add_mutually_exclusive_group()
        group.add_argument("-v", "--verbose", action="store_true", help="debug logging")
        group.add_argument("-q", "--quiet", action="store_true", help="warnings and errors only")

    run = sub.add_parser("run", help="check all watches (once by default)")
    common(run)
    mode = run.add_mutually_exclusive_group()
    mode.add_argument("--once", action="store_true", help="single pass (default)")
    mode.add_argument("--loop", action="store_true", help="keep running; see --interval")
    run.add_argument("--interval", type=float, default=300.0, help="seconds between loop passes (default 300)")
    run.add_argument("--force", action="store_true", help="ignore per-watch intervals")
    run.add_argument("--watch", action="append", dest="watches", help="only this watch (repeatable)")

    history = sub.add_parser("history", help="show recent observations of a watch")
    common(history)
    history.add_argument("--watch", required=True, help="watch name")
    history.add_argument("--last", type=int, default=20, help="number of rows (default 20)")

    export = sub.add_parser("export", help="export the observation history as CSV")
    common(export)
    export.add_argument("--csv", required=True, help="output file")
    export.add_argument("--watch", help="only this watch")

    test = sub.add_parser("test-notify", help="send a test message through the configured channels")
    common(test, with_db=False)
    test.add_argument("--notifier", action="append", dest="notifiers", help="only this notifier (repeatable)")

    validate = sub.add_parser("validate", help="check the configuration and list watches")
    common(validate, with_db=False)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    _configure_logging(args)
    try:
        config = load_config(args.config)
    except ConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_CONFIG
    try:
        if args.command == "validate":
            return _cmd_validate(config, args)
        if args.command == "test-notify":
            return _cmd_test_notify(config, args)
        db_path = Path(args.db) if args.db else config.database
        with Store(db_path) as store:
            if args.command == "run":
                return _cmd_run(config, store, args)
            if args.command == "history":
                return _cmd_history(config, store, args)
            return _cmd_export(config, store, args)
    except ConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_CONFIG
    except KeyboardInterrupt:
        print("\ninterrupted", file=sys.stderr)
        return EXIT_INTERRUPTED


# --------------------------------------------------------------------------- #
# Commands
# --------------------------------------------------------------------------- #


def _cmd_validate(config: Config, args: argparse.Namespace) -> int:
    print(f"OK: {args.config}")
    print(f"  database:  {config.database}")
    print(f"  interval:  {config.interval}s per watch (0 = every run), alert after {config.failures_before_alert} failures")
    print(f"  notifiers: {', '.join(f'{n.name} ({n.type})' for n in config.notifiers)}")
    for spec in config.notifiers:
        unresolved = sorted({var for value in spec.options.values() for var in missing_env(value)})
        if unresolved:
            print(f"    warning: {spec.name}: environment variable(s) not set: {', '.join(unresolved)}")
    print(f"  watches ({len(config.watches)}):")
    for watch in config.watches:
        extra = f", interval {watch.interval}s" if watch.interval is not None else ""
        print(f"    - {watch.name}: {watch.condition.describe()} | {watch.selector} | {watch.url}{extra}")
    return EXIT_OK


def _cmd_run(config: Config, store: Store, args: argparse.Namespace) -> int:
    if args.loop:
        if args.interval <= 0:
            print("error: --interval must be > 0", file=sys.stderr)
            return EXIT_CONFIG
        log.info("loop mode: every %.0fs, Ctrl+C to stop", args.interval)
        problems = {"count": 0}

        def on_report(report: RunReport) -> None:
            print(report.table())
            print()
            problems["count"] += report.errors + len(report.notify_failures)

        run_loop(config, store, interval=args.interval, on_report=on_report)
        return EXIT_PROBLEMS if problems["count"] else EXIT_OK

    report = run_once(config, store, force=args.force, only=args.watches)
    print(report.table())
    summary = f"{len(report.results)} watches, {report.alerts} alerts, {report.errors} errors"
    if report.notify_failures:
        summary += f", {len(report.notify_failures)} notification failures"
    print(f"\n{summary}", file=sys.stderr)
    return EXIT_PROBLEMS if report.errors or report.notify_failures else EXIT_OK


def _cmd_history(config: Config, store: Store, args: argparse.Namespace) -> int:
    config.watch(args.watch)  # validates the name
    rows = store.history(args.watch, max(1, args.last))
    if not rows:
        print(f"no observations yet for {args.watch!r}")
        return EXIT_OK
    print(f"{'timestamp (UTC)':<20} {'status':<7} {'alert':<5} value / error")
    for obs in rows:
        text = obs.value if obs.status == "ok" else f"ERROR: {obs.error}"
        print(f"{obs.ts.strftime('%Y-%m-%d %H:%M:%S'):<20} {obs.status:<7} {'yes' if obs.alerted else '':<5} {text}")
    return EXIT_OK


def _cmd_export(config: Config, store: Store, args: argparse.Namespace) -> int:
    if args.watch:
        config.watch(args.watch)
    count = store.export_csv(args.csv, args.watch)
    print(f"wrote {count} rows to {args.csv}", file=sys.stderr)
    return EXIT_OK


def _cmd_test_notify(config: Config, args: argparse.Namespace) -> int:
    with make_client(config.user_agent, config.timeout) as client:
        notifiers = build_notifiers(config, args.notifiers, client=client)
        message = test_message(args.config, utcnow())
        failures = dispatch(notifiers.values(), message)
    sent = len(notifiers) - len(failures)
    print(f"sent test message via {sent}/{len(notifiers)} notifier(s)", file=sys.stderr)
    for failure in failures:
        print(f"  failed: {failure}", file=sys.stderr)
    return EXIT_PROBLEMS if failures else EXIT_OK


def _configure_logging(args: argparse.Namespace) -> None:
    level = logging.INFO
    if getattr(args, "verbose", False):
        level = logging.DEBUG
    elif getattr(args, "quiet", False):
        level = logging.WARNING
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)-7s %(message)s",
        datefmt="%H:%M:%S",
        stream=sys.stderr,
        force=True,
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())

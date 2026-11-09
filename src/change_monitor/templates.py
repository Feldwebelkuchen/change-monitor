"""Plain-text message templates for every event type."""

from __future__ import annotations

from datetime import datetime, timezone

from .config import Watch
from .notify.base import Message
from .numbers import format_number
from .store import Observation

PREFIX = "[change-monitor]"


def _stamp(ts: datetime) -> str:
    return ts.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def change_message(watch: Watch, previous: Observation, current: Observation, reason: str) -> Message:
    diff = diff_percent = None
    change_line = ""
    if previous.value_num is not None and current.value_num is not None:
        diff = current.value_num - previous.value_num
        if previous.value_num != 0:
            diff_percent = diff / abs(previous.value_num) * 100
        sign = "+" if diff > 0 else ""
        change_line = f"Change:  {sign}{format_number(diff)}"
        if diff_percent is not None:
            change_line += f" ({sign}{diff_percent:.1f} %)"
    lines = [
        f"Watch:   {watch.name}",
        f"URL:     {watch.url}",
        f"Old:     {previous.value}",
        f"New:     {current.value}",
    ]
    if change_line:
        lines.append(change_line)
    lines += [f"Reason:  {reason}", f"Checked: {_stamp(current.ts)}"]
    return Message(
        event="change",
        watch=watch.name,
        subject=f"{PREFIX} {watch.name}: {reason}",
        body="\n".join(lines),
        url=watch.url,
        old=previous.value,
        new=current.value,
        diff=diff,
        diff_percent=diff_percent,
        reason=reason,
        timestamp=current.ts.isoformat(),
    )


def failing_message(watch: Watch, failures: int, error: str, ts: datetime) -> Message:
    body = "\n".join(
        [
            f"Watch:   {watch.name}",
            f"URL:     {watch.url}",
            f"Status:  {failures} consecutive failed checks",
            f"Error:   {error}",
            f"Checked: {_stamp(ts)}",
            "",
            "The page may be down or its layout changed (selector needs repair).",
        ]
    )
    return Message(
        event="failing",
        watch=watch.name,
        subject=f"{PREFIX} {watch.name}: watch failing ({failures} errors in a row)",
        body=body,
        url=watch.url,
        reason=error,
        timestamp=ts.isoformat(),
    )


def recovered_message(watch: Watch, current: Observation, failures: int) -> Message:
    body = "\n".join(
        [
            f"Watch:   {watch.name}",
            f"URL:     {watch.url}",
            f"Value:   {current.value}",
            f"Status:  OK again after {failures} failed check(s)",
            f"Checked: {_stamp(current.ts)}",
        ]
    )
    return Message(
        event="recovered",
        watch=watch.name,
        subject=f"{PREFIX} {watch.name}: recovered",
        body=body,
        url=watch.url,
        new=current.value,
        reason="recovered",
        timestamp=current.ts.isoformat(),
    )


def test_message(source: str, ts: datetime) -> Message:
    body = "\n".join(
        [
            "This is a test notification from change-monitor.",
            f"Config:  {source}",
            f"Sent:    {_stamp(ts)}",
        ]
    )
    return Message(
        event="test",
        watch="-",
        subject=f"{PREFIX} test notification",
        body=body,
        reason="test",
        timestamp=ts.isoformat(),
    )

"""Common message type, notifier protocol and fault-tolerant dispatch."""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass
from typing import Any, Iterable, Protocol

log = logging.getLogger(__name__)


class NotifyError(RuntimeError):
    """A channel failed to deliver a message."""


@dataclass(frozen=True)
class Message:
    """Channel-independent notification content.

    ``subject`` and ``body`` are plain text (no Markdown) so that every
    channel can show them verbatim. Structured fields are included for
    webhooks and tests.
    """

    event: str  # change | failing | recovered | test
    watch: str
    subject: str
    body: str
    url: str = ""
    old: str | None = None
    new: str | None = None
    diff: float | None = None
    diff_percent: float | None = None
    reason: str = ""
    timestamp: str = ""

    def as_payload(self) -> dict[str, Any]:
        """JSON-serialisable representation (used by the webhook channel)."""
        return asdict(self)

    @property
    def text(self) -> str:
        return f"{self.subject}\n\n{self.body}"


class Notifier(Protocol):
    """Anything with a ``name`` that can ``send`` a :class:`Message`."""

    name: str

    def send(self, message: Message) -> None: ...


def dispatch(notifiers: Iterable[Notifier], message: Message) -> list[str]:
    """Send ``message`` through every notifier; never raises.

    Returns a list of ``"<notifier>: <error>"`` strings for channels that
    failed, so a broken channel cannot stop the monitoring run.
    """
    failures: list[str] = []
    for notifier in notifiers:
        try:
            notifier.send(message)
            log.info("notified %s (%s: %s)", notifier.name, message.event, message.watch)
        except Exception as exc:  # noqa: BLE001 - any channel error must be contained
            failures.append(f"{notifier.name}: {exc}")
            log.error("notifier %s failed: %s", notifier.name, exc)
    return failures

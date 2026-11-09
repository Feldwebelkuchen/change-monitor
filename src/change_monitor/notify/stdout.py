"""Print notifications to the console (useful for cron logs and testing)."""

from __future__ import annotations

import sys
from typing import TextIO

from .base import Message


class StdoutNotifier:
    def __init__(self, name: str = "stdout", stream: TextIO | None = None) -> None:
        self.name = name
        self._stream = stream

    def send(self, message: Message) -> None:
        stream = self._stream or sys.stdout
        print(message.text, file=stream)
        print("-" * 40, file=stream)
        stream.flush()

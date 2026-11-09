"""Notification channels (plugins) and the dispatcher."""

from .base import Message, Notifier, NotifyError, dispatch
from .email import EmailNotifier
from .registry import build_notifiers
from .stdout import StdoutNotifier
from .telegram import TelegramNotifier
from .webhook import WebhookNotifier

__all__ = [
    "EmailNotifier",
    "Message",
    "Notifier",
    "NotifyError",
    "StdoutNotifier",
    "TelegramNotifier",
    "WebhookNotifier",
    "build_notifiers",
    "dispatch",
]

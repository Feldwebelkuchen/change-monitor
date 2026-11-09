"""Telegram Bot API channel (``sendMessage``), plain text."""

from __future__ import annotations

import httpx

from .base import Message, NotifyError

TELEGRAM_API = "https://api.telegram.org"
_MAX_LENGTH = 4000  # Telegram allows 4096 characters per message


class TelegramNotifier:
    def __init__(
        self,
        name: str,
        token: str,
        chat_id: str,
        client: httpx.Client,
        api_base: str = TELEGRAM_API,
    ) -> None:
        self.name = name
        self.chat_id = chat_id
        self._client = client
        self._endpoint = f"{api_base.rstrip('/')}/bot{token}/sendMessage"

    def send(self, message: Message) -> None:
        text = message.text
        if len(text) > _MAX_LENGTH:
            text = text[: _MAX_LENGTH - 1] + "…"
        try:
            response = self._client.post(
                self._endpoint,
                json={"chat_id": self.chat_id, "text": text, "disable_web_page_preview": True},
            )
        except httpx.HTTPError as exc:
            raise NotifyError(f"telegram request failed: {exc.__class__.__name__}: {exc}") from exc
        if response.status_code >= 400:
            raise NotifyError(f"telegram returned HTTP {response.status_code}: {response.text[:200]}")
        try:
            ok = bool(response.json().get("ok"))
        except ValueError:
            ok = False
        if not ok:
            raise NotifyError(f"telegram rejected the message: {response.text[:200]}")

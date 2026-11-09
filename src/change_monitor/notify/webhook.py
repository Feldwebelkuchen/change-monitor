"""Generic webhook: POST the message as JSON (Slack/Discord/n8n/Zapier/your API)."""

from __future__ import annotations

from typing import Mapping

import httpx

from .base import Message, NotifyError


class WebhookNotifier:
    def __init__(
        self,
        name: str,
        url: str,
        client: httpx.Client,
        headers: Mapping[str, str] | None = None,
    ) -> None:
        self.name = name
        self.url = url
        self._client = client
        self._headers = dict(headers or {})

    def send(self, message: Message) -> None:
        payload = message.as_payload()
        payload["text"] = message.text  # convenience for Slack-style receivers
        try:
            response = self._client.post(self.url, json=payload, headers=self._headers)
        except httpx.HTTPError as exc:
            raise NotifyError(f"webhook request failed: {exc.__class__.__name__}: {exc}") from exc
        if response.status_code >= 400:
            raise NotifyError(f"webhook returned HTTP {response.status_code}: {response.text[:200]}")

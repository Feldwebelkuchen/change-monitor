"""Minimal HTTP layer. One request per watch and run; failures are reported
to the monitor, which counts them and alerts after repeated failures."""

from __future__ import annotations

import httpx


class FetchError(RuntimeError):
    """The page could not be retrieved."""


def make_client(user_agent: str, timeout: float, transport: httpx.BaseTransport | None = None) -> httpx.Client:
    """Create the HTTP client used for all watches (``transport`` for tests)."""
    return httpx.Client(
        headers={
            "User-Agent": user_agent,
            "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.8",
        },
        timeout=timeout,
        follow_redirects=True,
        transport=transport,
    )


def fetch_text(client: httpx.Client, url: str) -> str:
    """Return the response body or raise :class:`FetchError`."""
    try:
        response = client.get(url)
    except httpx.HTTPError as exc:
        raise FetchError(f"request failed: {exc.__class__.__name__}: {exc}") from exc
    if response.status_code >= 400:
        raise FetchError(f"HTTP {response.status_code}")
    return response.text

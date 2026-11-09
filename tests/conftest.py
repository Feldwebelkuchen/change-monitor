"""Offline fixtures: a fake shop served via ``httpx.MockTransport``, an
in-memory store and a recording notifier. No test touches the network."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from change_monitor.config import Config, parse_config
from change_monitor.notify.base import Message, NotifyError
from change_monitor.store import Store

BASE = "https://shop.test"
PRODUCT = f"{BASE}/product/1"
T0 = datetime(2026, 11, 9, 12, 0, tzinfo=timezone.utc)


def product_page(price: str = "£51.77", stock: str = "In stock (22 available)") -> str:
    return (
        "<html><body><div class='product_main'><h1>A Light in the Attic</h1>"
        f"<p class='price_color'>{price}</p>"
        f"<p class='instock availability'><i class='icon-ok'></i>\n   {stock}\n</p>"
        "<a class='buy' href='/cart?add=1' data-sku='SKU-1'>Add to basket</a>"
        "</div></body></html>"
    )


class FakeSite:
    """Mutable in-memory website; ``set`` changes what a URL returns."""

    def __init__(self) -> None:
        self.pages: dict[str, tuple[int, str]] = {PRODUCT: (200, product_page())}
        self.requests: list[str] = []

    def set(self, url: str, html: str = "", status: int = 200) -> None:
        self.pages[url] = (status, html)

    def transport(self) -> httpx.MockTransport:
        def handler(request: httpx.Request) -> httpx.Response:
            url = str(request.url)
            self.requests.append(url)
            if url not in self.pages:
                return httpx.Response(404, text="not found")
            status, body = self.pages[url]
            return httpx.Response(status, text=body)

        return httpx.MockTransport(handler)


class FakeNotifier:
    """Records messages; optionally fails to simulate a broken channel."""

    def __init__(self, name: str = "console", fail: bool = False) -> None:
        self.name = name
        self.fail = fail
        self.sent: list[Message] = []

    def send(self, message: Message) -> None:
        if self.fail:
            raise NotifyError("simulated outage")
        self.sent.append(message)


def at(minutes: float) -> datetime:
    return T0 + timedelta(minutes=minutes)


@pytest.fixture(autouse=True)
def _reset_logging():
    root = logging.getLogger()
    handlers, level = list(root.handlers), root.level
    yield
    root.handlers[:] = handlers
    root.setLevel(level)


@pytest.fixture
def site() -> FakeSite:
    return FakeSite()


@pytest.fixture
def client(site: FakeSite) -> httpx.Client:
    with httpx.Client(transport=site.transport()) as c:
        yield c


@pytest.fixture
def store() -> Store:
    with Store(":memory:") as s:
        yield s


@pytest.fixture
def notifier() -> FakeNotifier:
    return FakeNotifier("console")


@pytest.fixture
def config_dict() -> dict:
    return {
        "database": "data/test.db",
        "interval": 0,
        "failures_before_alert": 3,
        "notifiers": {"console": {"type": "stdout"}},
        "watches": [
            {
                "name": "price",
                "url": PRODUCT,
                "selector": ".product_main .price_color",
                "number": {"decimal": "."},
                "condition": {"below": 50},
                "notify": ["console"],
            },
            {
                "name": "stock",
                "url": PRODUCT,
                "selector": ".product_main .availability",
                "regex": r"\((\d+) available\)",
                "condition": {"below": 5},
            },
            {
                "name": "availability-text",
                "url": PRODUCT,
                "selector": ".product_main .availability",
                "condition": "any_change",
            },
        ],
    }


@pytest.fixture
def config(config_dict: dict) -> Config:
    return parse_config(config_dict)

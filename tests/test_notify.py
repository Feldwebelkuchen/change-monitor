import io
import json
import smtplib

import httpx
import pytest

from change_monitor.config import ConfigError, parse_config
from change_monitor.notify import (
    EmailNotifier,
    Message,
    NotifyError,
    StdoutNotifier,
    TelegramNotifier,
    WebhookNotifier,
    build_notifiers,
    dispatch,
)

from conftest import FakeNotifier

MESSAGE = Message(
    event="change",
    watch="price",
    subject="[change-monitor] price: dropped below 50 (now 49)",
    body="Old: 51.77\nNew: 49.00",
    url="https://shop.test/product/1",
    old="51.77",
    new="49.00",
    diff=-2.77,
    diff_percent=-5.4,
    reason="dropped below 50 (now 49)",
    timestamp="2026-11-09T12:00:00+00:00",
)


class Capture:
    """Mock transport that records the last request and returns a canned response."""

    def __init__(self, status: int = 200, body: dict | None = None):
        self.status = status
        self.body = body if body is not None else {"ok": True}
        self.requests: list[httpx.Request] = []

    def transport(self) -> httpx.MockTransport:
        def handler(request: httpx.Request) -> httpx.Response:
            self.requests.append(request)
            return httpx.Response(self.status, json=self.body)

        return httpx.MockTransport(handler)


def test_webhook_posts_json_payload_with_headers():
    capture = Capture()
    client = httpx.Client(transport=capture.transport())
    WebhookNotifier("hook", "https://hooks.test/in", client, headers={"Authorization": "Bearer x"}).send(MESSAGE)
    request = capture.requests[0]
    assert request.method == "POST" and str(request.url) == "https://hooks.test/in"
    assert request.headers["authorization"] == "Bearer x"
    payload = json.loads(request.content)
    assert payload["event"] == "change" and payload["watch"] == "price"
    assert payload["old"] == "51.77" and payload["new"] == "49.00" and payload["diff"] == -2.77
    assert payload["text"].startswith("[change-monitor] price")


def test_webhook_http_error_raises():
    client = httpx.Client(transport=Capture(status=500, body={"error": "x"}).transport())
    with pytest.raises(NotifyError, match="HTTP 500"):
        WebhookNotifier("hook", "https://hooks.test/in", client).send(MESSAGE)


def test_telegram_uses_bot_endpoint_and_plain_text():
    capture = Capture()
    client = httpx.Client(transport=capture.transport())
    TelegramNotifier("tg", "123:ABC", "4242", client).send(MESSAGE)
    request = capture.requests[0]
    assert str(request.url) == "https://api.telegram.org/bot123:ABC/sendMessage"
    payload = json.loads(request.content)
    assert payload["chat_id"] == "4242" and payload["disable_web_page_preview"] is True
    assert payload["text"] == MESSAGE.subject + "\n\n" + MESSAGE.body
    assert "parse_mode" not in payload


def test_telegram_rejection_raises():
    client = httpx.Client(transport=Capture(status=200, body={"ok": False, "description": "chat not found"}).transport())
    with pytest.raises(NotifyError, match="rejected"):
        TelegramNotifier("tg", "123:ABC", "4242", client).send(MESSAGE)


class FakeSMTP:
    instances: list["FakeSMTP"] = []

    def __init__(self, host: str, port: int):
        self.host, self.port = host, port
        self.calls: list[tuple] = []
        FakeSMTP.instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.calls.append(("quit",))

    def starttls(self):
        self.calls.append(("starttls",))

    def login(self, user, password):
        self.calls.append(("login", user, password))

    def send_message(self, mail):
        self.calls.append(("send", mail))


def test_email_builds_and_sends_message():
    FakeSMTP.instances.clear()
    notifier = EmailNotifier(
        "mail", "smtp.test", 587, "alerts@test", ["a@test", " b@test "], username="u", password="p", smtp_factory=FakeSMTP
    )
    notifier.send(MESSAGE)
    smtp = FakeSMTP.instances[0]
    assert (smtp.host, smtp.port) == ("smtp.test", 587)
    assert smtp.calls[0] == ("starttls",) and smtp.calls[1] == ("login", "u", "p")
    mail = smtp.calls[2][1]
    assert mail["Subject"] == MESSAGE.subject and mail["From"] == "alerts@test" and mail["To"] == "a@test, b@test"
    assert mail.get_content().strip() == MESSAGE.body


def test_email_smtp_errors_become_notify_errors():
    class Exploding(FakeSMTP):
        def send_message(self, mail):
            raise smtplib.SMTPRecipientsRefused({})

    notifier = EmailNotifier("mail", "smtp.test", 25, "a@test", ["b@test"], starttls=False, smtp_factory=Exploding)
    with pytest.raises(NotifyError, match="smtp failed"):
        notifier.send(MESSAGE)
    with pytest.raises(ValueError):
        EmailNotifier("mail", "smtp.test", 25, "a@test", [" "], smtp_factory=FakeSMTP)


def test_stdout_notifier_writes_to_stream():
    stream = io.StringIO()
    StdoutNotifier("console", stream=stream).send(MESSAGE)
    assert MESSAGE.subject in stream.getvalue() and "Old: 51.77" in stream.getvalue()


def test_dispatch_isolates_failures():
    good, bad = FakeNotifier("good"), FakeNotifier("bad", fail=True)
    failures = dispatch([bad, good], MESSAGE)
    assert failures == ["bad: simulated outage"]
    assert good.sent == [MESSAGE]


def test_build_notifiers_resolves_env_and_defaults(config_dict):
    config_dict["notifiers"] = {
        "console": {"type": "stdout"},
        "tg": {"type": "telegram"},
        "hook": {"type": "webhook", "url": "${HOOK}", "header_X-Token": "${HOOK_TOKEN:-none}"},
        "mail": {"type": "email", "recipients": "x@test,y@test"},
    }
    config = parse_config(config_dict)
    env = {
        "TELEGRAM_BOT_TOKEN": "1:A",
        "TELEGRAM_CHAT_ID": "7",
        "HOOK": "https://hooks.test/x",
        "SMTP_HOST": "smtp.test",
        "MAIL_FROM": "alerts@test",
    }
    client = httpx.Client(transport=Capture().transport())
    built = build_notifiers(config, client=client, env=env, smtp_factory=FakeSMTP)
    assert set(built) == {"console", "tg", "hook", "mail"}
    assert isinstance(built["tg"], TelegramNotifier) and built["tg"].chat_id == "7"
    assert isinstance(built["hook"], WebhookNotifier) and built["hook"]._headers == {"X-Token": "none"}
    mail = built["mail"]
    assert isinstance(mail, EmailNotifier) and mail.recipients == ["x@test", "y@test"] and mail.port == 587

    subset = build_notifiers(config, ["console"], client=client, env=env)
    assert list(subset) == ["console"]


def test_build_notifiers_reports_missing_env_and_unknown_names(config_dict):
    config_dict["notifiers"] = {"console": {"type": "stdout"}, "tg": {"type": "telegram"}}
    config = parse_config(config_dict)
    client = httpx.Client(transport=Capture().transport())
    with pytest.raises(ConfigError, match="notifier 'tg' \\(telegram\\): option 'token': environment variable TELEGRAM_BOT_TOKEN"):
        build_notifiers(config, client=client, env={})
    with pytest.raises(ConfigError, match="unknown notifier"):
        build_notifiers(config, ["pager"], client=client, env={})

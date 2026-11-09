import csv

import httpx
import pytest
import yaml

from change_monitor import cli, monitor

from conftest import PRODUCT, FakeNotifier, FakeSite, product_page


@pytest.fixture
def config_file(tmp_path, config_dict):
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(config_dict, sort_keys=False), encoding="utf-8")
    return path


@pytest.fixture
def offline(monkeypatch, site: FakeSite):
    """Point the monitor's HTTP client at the fake site."""
    monkeypatch.setattr(monitor, "make_client", lambda ua, timeout: httpx.Client(transport=site.transport()))
    return site


def test_validate_lists_watches_and_warns_about_env(config_file, config_dict, capsys, monkeypatch):
    config_dict["notifiers"]["tg"] = {"type": "telegram", "token": "${TELEGRAM_BOT_TOKEN}"}
    config_file.write_text(yaml.safe_dump(config_dict, sort_keys=False), encoding="utf-8")
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    assert cli.main(["validate", str(config_file)]) == cli.EXIT_OK
    out = capsys.readouterr().out
    assert "OK:" in out and "- price: below 50" in out and "- availability-text: any change" in out
    assert "warning: tg: environment variable(s) not set: TELEGRAM_BOT_TOKEN" in out


def test_config_error_exit_code(tmp_path, capsys):
    bad = tmp_path / "bad.yaml"
    bad.write_text("watches: []\n", encoding="utf-8")
    assert cli.main(["validate", str(bad)]) == cli.EXIT_CONFIG
    assert "invalid configuration" in capsys.readouterr().err


def test_run_once_baseline_then_history_and_export(config_file, offline, tmp_path, capsys):
    assert cli.main(["run", str(config_file), "--once", "-q"]) == cli.EXIT_OK
    out = capsys.readouterr().out
    assert "baseline" in out and "£51.77" in out
    assert (tmp_path / "data" / "test.db").exists()

    offline.set(PRODUCT, product_page(price="£40.00"))
    assert cli.main(["run", str(config_file), "-q"]) == cli.EXIT_OK
    out = capsys.readouterr().out
    assert "alert" in out and "dropped below 50 (now 40)" in out  # stdout notifier prints the alert

    assert cli.main(["history", str(config_file), "--watch", "price", "--last", "5"]) == cli.EXIT_OK
    lines = capsys.readouterr().out.splitlines()
    assert lines[0].startswith("timestamp") and len(lines) == 3
    assert "£40.00" in lines[1] and "yes" in lines[1] and "£51.77" in lines[2]

    csv_path = tmp_path / "out" / "history.csv"
    assert cli.main(["export", str(config_file), "--csv", str(csv_path), "--watch", "price"]) == cli.EXIT_OK
    with csv_path.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    assert [r["value"] for r in rows] == ["£51.77", "£40.00"]


def test_run_reports_problems_with_exit_code_1(config_file, offline, capsys):
    offline.set(PRODUCT, "oops", status=500)
    assert cli.main(["run", str(config_file), "--once", "-q"]) == cli.EXIT_PROBLEMS
    assert "3 errors" in capsys.readouterr().err


def test_run_with_db_override_and_watch_filter(config_file, offline, tmp_path, capsys):
    db = tmp_path / "elsewhere.db"
    assert cli.main(["run", str(config_file), "--db", str(db), "--watch", "stock", "-q"]) == cli.EXIT_OK
    assert db.exists()
    out = capsys.readouterr().out
    assert "stock" in out and "price" not in out


def test_history_unknown_watch_is_config_error(config_file, capsys):
    assert cli.main(["history", str(config_file), "--watch", "nope"]) == cli.EXIT_CONFIG
    assert "unknown watch 'nope'" in capsys.readouterr().err


def test_test_notify_uses_built_notifiers(config_file, monkeypatch, capsys):
    fake = FakeNotifier("console")
    monkeypatch.setattr(cli, "build_notifiers", lambda config, names=None, **kw: {"console": fake})
    assert cli.main(["test-notify", str(config_file)]) == cli.EXIT_OK
    assert len(fake.sent) == 1 and fake.sent[0].event == "test" and "test notification" in fake.sent[0].subject
    assert "sent test message via 1/1" in capsys.readouterr().err


def test_test_notify_reports_failures(config_file, monkeypatch, capsys):
    broken = FakeNotifier("console", fail=True)
    monkeypatch.setattr(cli, "build_notifiers", lambda config, names=None, **kw: {"console": broken})
    assert cli.main(["test-notify", str(config_file)]) == cli.EXIT_PROBLEMS
    assert "failed: console: simulated outage" in capsys.readouterr().err

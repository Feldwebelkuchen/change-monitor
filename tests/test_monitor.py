import pytest

from change_monitor.monitor import run_loop, run_once

from conftest import PRODUCT, FakeNotifier, at, product_page


def run(config, store, client, notifier, minutes=0, **kwargs):
    return run_once(config, store, client=client, notifiers={"console": notifier}, now=at(minutes), **kwargs)


def outcome(report, name):
    return next(r for r in report.results if r.watch == name)


def test_first_run_is_a_silent_baseline(config, store, client, notifier):
    report = run(config, store, client, notifier)
    assert [r.outcome for r in report.results] == ["baseline", "baseline", "baseline"]
    assert notifier.sent == [] and report.alerts == 0 and report.errors == 0
    price = store.last_ok("price")
    assert price.value == "£51.77" and price.value_num == 51.77
    assert store.last_ok("stock").value == "22" and store.last_ok("stock").value_num == 22.0


def test_unchanged_then_any_change_alert(config, store, client, notifier, site):
    run(config, store, client, notifier, 0)
    report = run(config, store, client, notifier, 5)
    assert outcome(report, "availability-text").outcome == "unchanged"
    assert notifier.sent == []

    site.set(PRODUCT, product_page(stock="In stock (3 available)"))
    report = run(config, store, client, notifier, 10)
    result = outcome(report, "availability-text")
    assert result.outcome == "alert" and result.alerted
    # stock dropped to 3 -> "below 5" also fires; price unchanged
    assert outcome(report, "stock").outcome == "alert"
    assert outcome(report, "price").outcome == "unchanged"
    events = {(m.watch, m.event) for m in notifier.sent}
    assert events == {("availability-text", "change"), ("stock", "change")}
    text_alert = next(m for m in notifier.sent if m.watch == "availability-text")
    assert text_alert.old == "In stock (22 available)" and text_alert.new == "In stock (3 available)"
    assert "Old:     In stock (22 available)" in text_alert.body and PRODUCT in text_alert.body
    assert store.history("availability-text", 1)[0].alerted is True


def test_threshold_alerts_on_transition_and_further_change(config, store, client, notifier, site):
    run(config, store, client, notifier, 0)
    site.set(PRODUCT, product_page(price="£49.00"))
    report = run(config, store, client, notifier, 1)
    assert outcome(report, "price").outcome == "alert"
    msg = notifier.sent[-1]
    assert msg.reason == "dropped below 50 (now 49)"
    assert msg.diff == pytest.approx(-2.77) and msg.diff_percent == pytest.approx(-5.35, abs=0.01)
    assert "Change:  -2.77 (-5.4 %)" in msg.body

    report = run(config, store, client, notifier, 2)  # still 49 -> no new alert
    assert outcome(report, "price").outcome == "unchanged" and len(notifier.sent) == 1

    site.set(PRODUCT, product_page(price="£45.00"))
    report = run(config, store, client, notifier, 3)
    assert outcome(report, "price").outcome == "alert" and notifier.sent[-1].reason == "value changed, still below 50"

    site.set(PRODUCT, product_page(price="£55.00"))
    report = run(config, store, client, notifier, 4)
    assert outcome(report, "price").outcome == "changed" and len(notifier.sent) == 2


def test_failures_alert_after_threshold_then_recover(config, store, client, notifier, site):
    run(config, store, client, notifier, 0)
    site.set(PRODUCT, "down", status=503)
    for minute in (1, 2):
        report = run(config, store, client, notifier, minute)
        assert all(r.outcome == "error" for r in report.results)
    assert notifier.sent == []

    report = run(config, store, client, notifier, 3)
    assert all(r.outcome == "failing" and r.alerted for r in report.results)
    failing = [m for m in notifier.sent if m.event == "failing"]
    assert len(failing) == 3 and "3 consecutive failed checks" in failing[0].body and "HTTP 503" in failing[0].body

    report = run(config, store, client, notifier, 4)  # still down: no repeated alert
    assert all(r.outcome == "error" for r in report.results) and len(notifier.sent) == 3

    site.set(PRODUCT, product_page())
    report = run(config, store, client, notifier, 5)
    result = outcome(report, "price")
    assert result.outcome == "unchanged" and result.note.startswith("recovered;")
    recovered = [m for m in notifier.sent if m.event == "recovered"]
    assert len(recovered) == 3 and "after 4 failed check(s)" in recovered[0].body
    assert store.state("price").consecutive_failures == 0 and store.state("price").failing_alerted is False


def test_broken_selector_counts_as_failure(config, store, client, notifier, site):
    site.set(PRODUCT, "<html><body><p>layout changed</p></body></html>")
    report = run(config, store, client, notifier)
    result = outcome(report, "price")
    assert result.outcome == "error" and "matched nothing" in result.note
    assert store.history("price", 1)[0].status == "error"


def test_number_parse_failure_is_an_error(config, store, client, notifier, site):
    site.set(PRODUCT, product_page(price="Call for price"))
    report = run(config, store, client, notifier)
    assert outcome(report, "price").outcome == "error"
    assert "no number found" in outcome(report, "price").note


def test_per_watch_interval_rate_limits(config_dict, store, client, notifier):
    from change_monitor.config import parse_config

    config_dict["watches"][0]["interval"] = 3600
    config = parse_config(config_dict)
    run(config, store, client, notifier, 0)
    report = run(config, store, client, notifier, 10)
    assert outcome(report, "price").outcome == "skipped" and "interval 3600s" in outcome(report, "price").note
    assert outcome(report, "stock").outcome == "unchanged"
    report = run(config, store, client, notifier, 20, force=True)
    assert outcome(report, "price").outcome == "unchanged"
    report = run(config, store, client, notifier, 20 + 61)
    assert outcome(report, "price").outcome == "unchanged"


def test_notifier_failure_does_not_stop_the_run(config, store, client, site):
    broken = FakeNotifier("console", fail=True)
    run(config, store, client, broken, 0)
    site.set(PRODUCT, product_page(stock="Sold out (0 available)"))
    report = run(config, store, client, broken, 1)
    result = outcome(report, "availability-text")
    assert result.outcome == "alert" and result.notify_failures == ["console: simulated outage"]
    assert report.notify_failures == ["console: simulated outage"] * 2  # text watch + stock watch
    assert store.history("availability-text", 1)[0].value == "Sold out (0 available)"


def test_only_restricts_watches(config, store, client, notifier):
    report = run(config, store, client, notifier, only=["stock"])
    assert [r.watch for r in report.results] == ["stock"]


def test_run_loop_iterates_and_sleeps(config, store, client, notifier):
    sleeps: list[float] = []
    reports = []
    clock_values = iter([at(0), at(5), at(10)])
    count = run_loop(
        config,
        store,
        interval=300,
        client=client,
        notifiers={"console": notifier},
        sleep=sleeps.append,
        clock=lambda: next(clock_values),
        max_iterations=3,
        on_report=reports.append,
    )
    assert count == 3 and sleeps == [300, 300]
    assert [r.results[0].outcome for r in reports] == ["baseline", "unchanged", "unchanged"]


def test_report_table_lists_every_watch(config, store, client, notifier):
    report = run(config, store, client, notifier)
    table = report.table()
    assert table.splitlines()[0].startswith("watch")
    assert "price" in table and "baseline" in table and "£51.77" in table

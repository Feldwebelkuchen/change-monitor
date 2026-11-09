import csv

from change_monitor.store import Observation, Store, WatchState

from conftest import at


def test_record_last_ok_and_history_order(store: Store):
    store.record(Observation.ok("w", at(0), "A", 1.0))
    store.record(Observation.failure("w", at(1), "HTTP 500"))
    store.record(Observation.ok("w", at(2), "B", 2.0))
    last = store.last_ok("w")
    assert last is not None and last.value == "B" and last.value_num == 2.0 and last.hash
    history = store.history("w", limit=10)
    assert [o.status for o in history] == ["ok", "error", "ok"]
    assert history[0].value == "B" and history[1].error == "HTTP 500"
    assert history[0].ts == at(2)
    assert store.history("w", limit=1)[0].value == "B"
    assert store.last_ok("unknown") is None


def test_state_roundtrip(store: Store):
    assert store.state("w") == WatchState(watch="w")
    state = WatchState("w", consecutive_failures=2, failing_alerted=True, triggered=True, last_checked=at(5), last_alert=at(4))
    store.save_state(state)
    assert store.state("w") == state
    state.consecutive_failures = 0
    store.save_state(state)
    assert store.state("w").consecutive_failures == 0


def test_export_csv_and_watch_names(store: Store, tmp_path):
    store.record(Observation.ok("a", at(0), "1", 1.0))
    store.record(Observation.ok("b", at(0), "x"))
    store.record(Observation(watch="a", ts=at(1), status="ok", value="2", value_num=2.0, hash="h", alerted=True))
    assert store.watch_names() == ["a", "b"]
    out = tmp_path / "hist.csv"
    assert store.export_csv(out) == 3
    with out.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    assert rows[0]["watch"] == "a" and rows[0]["ts"] == "2026-11-09T12:00:00Z"
    assert rows[2]["alerted"] == "1" and rows[2]["value_num"] == "2.0"
    assert store.export_csv(tmp_path / "a.csv", watch="a") == 2


def test_store_creates_parent_directory(tmp_path):
    path = tmp_path / "nested" / "dir" / "m.db"
    with Store(path) as store:
        store.record(Observation.ok("w", at(0), "v"))
    assert path.exists()
    with Store(path) as store:
        assert store.last_ok("w").value == "v"

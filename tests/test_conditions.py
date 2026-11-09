from change_monitor.conditions import evaluate, holds
from change_monitor.config import Condition
from change_monitor.store import Observation

from conftest import at


def obs(value: str, num: float | None = None) -> Observation:
    return Observation.ok("w", at(0), value, num)


def test_baseline_never_alerts():
    for condition in (Condition(), Condition("below", threshold=100), Condition("contains", pattern="stock")):
        decision = evaluate(condition, None, obs("In stock", 10))
        assert decision.alert is False and decision.reason == "baseline recorded"
    # the baseline remembers whether the condition already holds
    assert evaluate(Condition("below", threshold=100), None, obs("10", 10)).triggered is True


def test_any_change_alerts_only_on_difference():
    same = evaluate(Condition(), obs("A"), obs("A"))
    assert same.alert is False and same.reason == "unchanged"
    changed = evaluate(Condition(), obs("A"), obs("B"))
    assert changed.alert is True and changed.reason == "value changed"


def test_threshold_transition_and_repeat_rules():
    below = Condition("below", threshold=50)
    entered = evaluate(below, obs("51.77", 51.77), obs("49.00", 49.0), previously_triggered=False)
    assert entered.alert and entered.triggered and entered.reason == "dropped below 50 (now 49)"
    still = evaluate(below, obs("49.00", 49.0), obs("49.00", 49.0), previously_triggered=True)
    assert not still.alert and still.triggered and still.reason == "still below 50"
    moved = evaluate(below, obs("49.00", 49.0), obs("45.00", 45.0), previously_triggered=True)
    assert moved.alert and moved.reason == "value changed, still below 50"
    left = evaluate(below, obs("45.00", 45.0), obs("55.00", 55.0), previously_triggered=True)
    assert not left.alert and not left.triggered and left.reason == "condition no longer holds"


def test_above_contains_not_contains_regex():
    assert holds(Condition("above", threshold=10), obs("11", 11)) is True
    assert holds(Condition("above", threshold=10), obs("10", 10)) is False
    assert holds(Condition("contains", pattern="in stock"), obs("In Stock (3)")) is True
    assert holds(Condition("not_contains", pattern="in stock"), obs("Sold out")) is True
    assert holds(Condition("regex", pattern=r"\(\d+ available\)"), obs("In stock (3 available)")) is True
    assert holds(Condition("below", threshold=10), obs("n/a", None)) is False  # no number -> cannot hold


def test_entered_reasons_for_text_conditions():
    gone = evaluate(Condition("not_contains", pattern="In stock"), obs("In stock"), obs("Sold out"))
    assert gone.alert and gone.reason == "no longer contains 'In stock'"
    found = evaluate(Condition("contains", pattern="sale"), obs("regular"), obs("On SALE now"))
    assert found.alert and found.reason == "now contains 'sale'"
    rx = evaluate(Condition("regex", pattern=r"\d+ left"), obs("plenty"), obs("only 2 left"))
    assert rx.alert and rx.reason == r"now matches /\d+ left/"

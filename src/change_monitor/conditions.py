"""Decide whether an observation should trigger an alert."""

from __future__ import annotations

import re
from dataclasses import dataclass

from .config import Condition
from .numbers import format_number
from .store import Observation


@dataclass(frozen=True)
class Decision:
    """Outcome of evaluating a condition.

    Attributes:
        alert: Send a notification now.
        triggered: Whether the condition currently holds (persisted so the
            next run can detect transitions).
        reason: Short human-readable explanation used in messages.
    """

    alert: bool
    triggered: bool
    reason: str


def holds(condition: Condition, observation: Observation) -> bool:
    """Return whether ``condition`` is satisfied by ``observation`` alone."""
    kind = condition.kind
    if kind == "below":
        return observation.value_num is not None and condition.threshold is not None and observation.value_num < condition.threshold
    if kind == "above":
        return observation.value_num is not None and condition.threshold is not None and observation.value_num > condition.threshold
    if kind == "contains":
        return (condition.pattern or "").lower() in (observation.value or "").lower()
    if kind == "not_contains":
        return (condition.pattern or "").lower() not in (observation.value or "").lower()
    if kind == "regex":
        return re.search(condition.pattern or "", observation.value or "") is not None
    return False  # any_change is relative, handled in evaluate()


def evaluate(
    condition: Condition,
    previous: Observation | None,
    current: Observation,
    previously_triggered: bool = False,
) -> Decision:
    """Compare ``current`` with the last good observation.

    Rules:

    * No previous observation: baseline, never alert.
    * ``any_change``: alert whenever the value hash differs.
    * Other conditions: alert when the condition starts to hold, and again
      when the value changes while it still holds. A condition that keeps
      holding with an unchanged value does not alert repeatedly.
    """
    if condition.kind == "any_change":
        if previous is None:
            return Decision(alert=False, triggered=False, reason="baseline recorded")
        changed = current.hash != previous.hash
        return Decision(alert=changed, triggered=changed, reason="value changed" if changed else "unchanged")

    satisfied = holds(condition, current)
    if previous is None:
        return Decision(alert=False, triggered=satisfied, reason="baseline recorded")
    changed = current.hash != previous.hash
    if not satisfied:
        reason = "condition no longer holds" if previously_triggered else ("value changed" if changed else "unchanged")
        return Decision(alert=False, triggered=False, reason=reason)
    if not previously_triggered:
        return Decision(alert=True, triggered=True, reason=_entered(condition, current))
    if changed:
        return Decision(alert=True, triggered=True, reason=f"value changed, still {condition.describe()}")
    return Decision(alert=False, triggered=True, reason=f"still {condition.describe()}")


def _entered(condition: Condition, current: Observation) -> str:
    if condition.kind in ("below", "above") and current.value_num is not None and condition.threshold is not None:
        verb = "dropped below" if condition.kind == "below" else "rose above"
        return f"{verb} {format_number(condition.threshold)} (now {format_number(current.value_num)})"
    if condition.kind == "contains":
        return f"now contains {condition.pattern!r}"
    if condition.kind == "not_contains":
        return f"no longer contains {condition.pattern!r}"
    return f"now matches /{condition.pattern}/"

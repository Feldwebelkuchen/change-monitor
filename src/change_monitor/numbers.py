"""Locale-tolerant number parsing for prices and counts.

Examples (``decimal="auto"``)::

    "1.299,00 €"   -> 1299.0      "$1,299.00"  -> 1299.0
    "£51.77"       -> 51.77       "1 299,50"   -> 1299.5
    "51,77"        -> 51.77       "12.500"     -> 12500.0  (3 digits after a single separator = thousands)
    "In stock (22 available)" -> 22.0

The only truly ambiguous case is a single separator followed by exactly
three digits (``1,299`` vs. ``1.299``); it is read as a thousands separator.
Set ``decimal: ","`` or ``decimal: "."`` on the watch when you know the
site's locale.
"""

from __future__ import annotations

import re

_NUMBER_RUN = re.compile(r"[-+]?\d[\d.,   '’]*")


class NumberParseError(ValueError):
    """No number could be found in the text."""


def parse_number(text: str, decimal: str = "auto") -> float:
    """Extract the first number from ``text`` and return it as ``float``."""
    match = _NUMBER_RUN.search(text)
    if not match:
        raise NumberParseError(f"no number found in {text[:80]!r}")
    token = match.group(0)
    sign = -1.0 if token.startswith("-") else 1.0
    token = token.lstrip("+-")
    # spaces / apostrophes only ever group thousands
    token = re.sub(r"[   '’]", "", token).rstrip(".,")
    if not token:
        raise NumberParseError(f"no digits found in {text[:80]!r}")

    if decimal == ",":
        normalised = token.replace(".", "").replace(",", ".")
    elif decimal == ".":
        normalised = token.replace(",", "")
    else:
        normalised = _auto_normalise(token)

    try:
        return sign * float(normalised)
    except ValueError as exc:
        raise NumberParseError(f"cannot parse {token!r} as a number") from exc


def _auto_normalise(token: str) -> str:
    has_dot, has_comma = "." in token, "," in token
    if has_dot and has_comma:
        decimal_sep = "." if token.rfind(".") > token.rfind(",") else ","
        thousands_sep = "," if decimal_sep == "." else "."
        return token.replace(thousands_sep, "").replace(decimal_sep, ".")
    sep = "." if has_dot else "," if has_comma else None
    if sep is None:
        return token
    if token.count(sep) > 1:
        return token.replace(sep, "")
    head, tail = token.split(sep)
    if len(tail) == 3 and head != "":
        return head + tail  # 1.299 / 1,299 -> thousands
    return head + "." + tail


def format_number(value: float) -> str:
    """Compact human-readable formatting (``1299.0 -> '1299'``, ``51.7 -> '51.70'``)."""
    if value == int(value):
        return str(int(value))
    return f"{value:.2f}"

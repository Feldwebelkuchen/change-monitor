"""Pull the watched value out of an HTML document."""

from __future__ import annotations

import re

from selectolax.lexbor import LexborHTMLParser

from .config import Watch

_WHITESPACE = re.compile(r"\s+")


class ExtractionError(RuntimeError):
    """Selector or regex did not match - usually the page layout changed."""


def extract_value(html: str, watch: Watch) -> str:
    """Return the normalised value for ``watch`` or raise :class:`ExtractionError`."""
    tree = LexborHTMLParser(html)
    node = tree.css_first(watch.selector)
    if node is None:
        raise ExtractionError(f"selector {watch.selector!r} matched nothing")

    if watch.extract == "attr":
        raw = node.attributes.get(watch.attr or "")
        if raw is None:
            raise ExtractionError(f"attribute {watch.attr!r} missing on matched element")
    elif watch.extract == "html":
        raw = node.html or ""
    else:
        raw = node.text(separator=" ", strip=True)
    value = _WHITESPACE.sub(" ", raw).strip()

    if watch.regex is not None:
        match = watch.regex.search(value)
        if not match:
            raise ExtractionError(f"regex {watch.regex.pattern!r} did not match {value[:80]!r}")
        value = (match.group(1) if match.groups() else match.group(0)).strip()
    if value == "":
        raise ExtractionError("extracted value is empty")
    return value

import re

import pytest

from change_monitor.config import Watch
from change_monitor.extract import ExtractionError, extract_value
from change_monitor.numbers import NumberParseError, format_number, parse_number

from conftest import PRODUCT, product_page


def watch(**overrides) -> Watch:
    base = {"name": "w", "url": PRODUCT, "selector": ".price_color"}
    base.update(overrides)
    return Watch(**base)


def test_text_extraction_normalises_whitespace():
    value = extract_value(product_page(), watch(selector=".availability"))
    assert value == "In stock (22 available)"


def test_attr_and_html_extraction():
    assert extract_value(product_page(), watch(selector="a.buy", extract="attr", attr="data-sku")) == "SKU-1"
    html = extract_value(product_page(), watch(selector="h1", extract="html"))
    assert html == "<h1>A Light in the Attic</h1>"


def test_regex_group_is_applied():
    value = extract_value(product_page(), watch(selector=".availability", regex=re.compile(r"\((\d+) available\)")))
    assert value == "22"


def test_missing_selector_attr_or_regex_raise():
    with pytest.raises(ExtractionError, match="matched nothing"):
        extract_value(product_page(), watch(selector=".nope"))
    with pytest.raises(ExtractionError, match="attribute 'data-x' missing"):
        extract_value(product_page(), watch(selector="a.buy", extract="attr", attr="data-x"))
    with pytest.raises(ExtractionError, match="did not match"):
        extract_value(product_page(), watch(selector=".availability", regex=re.compile(r"\d{5}")))


@pytest.mark.parametrize(
    "text, expected",
    [
        ("1.299,00 €", 1299.0),
        ("$1,299.00", 1299.0),
        ("£51.77", 51.77),
        ("1 299,50", 1299.5),
        ("1 299,50 €", 1299.5),
        ("51,77", 51.77),
        ("12.500", 12500.0),
        ("1,299", 1299.0),
        ("CHF 1'299.50", 1299.5),
        ("In stock (22 available)", 22.0),
        ("-3.5 %", -3.5),
        ("Price: 7", 7.0),
        ("1.234.567,89", 1234567.89),
        ("1,234,567.89", 1234567.89),
    ],
)
def test_parse_number_auto(text, expected):
    assert parse_number(text) == pytest.approx(expected)


def test_parse_number_explicit_decimal():
    assert parse_number("1,299", decimal=",") == pytest.approx(1.299)
    assert parse_number("1.299", decimal=".") == pytest.approx(1.299)
    assert parse_number("1.299,00", decimal=",") == pytest.approx(1299.0)


def test_parse_number_without_digits_raises():
    with pytest.raises(NumberParseError):
        parse_number("Sold out")


def test_format_number():
    assert format_number(1299.0) == "1299"
    assert format_number(51.7) == "51.70"
    assert format_number(-6.77) == "-6.77"

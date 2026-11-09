from pathlib import Path

import pytest
import yaml

from change_monitor.config import ConfigError, load_config, missing_env, parse_config, substitute_env


def test_parse_valid_config(config):
    assert [w.name for w in config.watches] == ["price", "stock", "availability-text"]
    price = config.watch("price")
    assert price.condition.kind == "below" and price.condition.threshold == 50.0
    assert price.number is not None and price.number.decimal == "."
    stock = config.watch("stock")
    assert stock.number is not None, "numeric conditions imply number parsing"
    assert stock.regex is not None and stock.regex.pattern == r"\((\d+) available\)"
    assert config.watch("availability-text").condition.kind == "any_change"


def test_notifiers_for_defaults_to_all(config):
    assert [n.name for n in config.notifiers_for(config.watch("stock"))] == ["console"]
    assert [n.name for n in config.notifiers_for(config.watch("price"))] == ["console"]


def test_database_path_is_relative_to_config_file(tmp_path, config_dict):
    path = tmp_path / "cfg" / "config.yaml"
    path.parent.mkdir()
    path.write_text(yaml.safe_dump(config_dict, sort_keys=False), encoding="utf-8")
    config = load_config(path)
    assert config.database == tmp_path / "cfg" / "data" / "test.db"
    assert config.source == path


def test_errors_are_collected(config_dict):
    config_dict["watches"][0]["url"] = "ftp://nope"
    config_dict["watches"][1]["condition"] = {"sideways": 1}
    config_dict["watches"][2]["notify"] = ["pager"]
    config_dict["watches"].append({"name": "price", "url": "https://x.test", "selector": "p"})
    config_dict["watches"].append({"url": "https://x.test", "selector": "p"})
    with pytest.raises(ConfigError) as info:
        parse_config(config_dict)
    text = str(info.value)
    for fragment in ("http:// or https://", "unknown condition 'sideways'", "unknown notifier 'pager'", "duplicate name", "'name' is required"):
        assert fragment in text


@pytest.mark.parametrize(
    "condition, kind, threshold, pattern",
    [
        (None, "any_change", None, None),
        ("any_change", "any_change", None, None),
        ({"above": 100}, "above", 100.0, None),
        ({"contains": "In stock"}, "contains", None, "In stock"),
        ({"not_contains": "sold out"}, "not_contains", None, "sold out"),
        ({"regex": r"\d+ available"}, "regex", None, r"\d+ available"),
    ],
)
def test_condition_forms(config_dict, condition, kind, threshold, pattern):
    config_dict["watches"][2]["condition"] = condition
    watch = parse_config(config_dict).watch("availability-text")
    assert (watch.condition.kind, watch.condition.threshold, watch.condition.pattern) == (kind, threshold, pattern)


def test_invalid_condition_values(config_dict):
    config_dict["watches"][0]["condition"] = {"below": "cheap"}
    with pytest.raises(ConfigError, match="numeric threshold"):
        parse_config(config_dict)
    config_dict["watches"][0]["condition"] = {"regex": "(["}
    with pytest.raises(ConfigError, match="condition.regex is invalid"):
        parse_config(config_dict)


def test_unknown_notifier_type_and_missing_watches(config_dict):
    config_dict["notifiers"]["x"] = {"type": "carrier-pigeon"}
    config_dict["watches"] = []
    with pytest.raises(ConfigError) as info:
        parse_config(config_dict)
    assert "'type' must be one of" in str(info.value) and "'watches' must be a non-empty list" in str(info.value)


def test_missing_file_and_invalid_yaml(tmp_path):
    with pytest.raises(ConfigError, match="not found"):
        load_config(tmp_path / "nope.yaml")
    bad = tmp_path / "bad.yaml"
    bad.write_text("watches: [", encoding="utf-8")
    with pytest.raises(ConfigError, match="invalid YAML"):
        load_config(bad)


def test_substitute_env_and_defaults():
    env = {"TOKEN": "abc", "EMPTY": ""}
    assert substitute_env("bot${TOKEN}/x", env) == "botabc/x"
    assert substitute_env("${PORT:-587}", env) == "587"
    assert substitute_env("${EMPTY:-fallback}", env) == "fallback"
    assert substitute_env("plain", env) == "plain"
    with pytest.raises(ConfigError, match="MISSING is not set"):
        substitute_env("${MISSING}", env)
    assert missing_env("${A} ${B:-x} ${TOKEN}", env) == ["A"]


def test_default_notifier_is_stdout(config_dict):
    del config_dict["notifiers"]
    for watch in config_dict["watches"]:
        watch.pop("notify", None)
    config = parse_config(config_dict)
    assert [(n.name, n.type) for n in config.notifiers] == [("stdout", "stdout")]
    assert isinstance(config.database, Path)

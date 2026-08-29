from __future__ import annotations

from pathlib import Path

import pytest

from file_organiser.categories import OTHER, ConfigError, Ruleset, load_exclusions_from_toml


def test_default_ruleset_maps_known_extensions() -> None:
    rules = Ruleset.default()
    assert rules.category_for(Path("a.jpg")) == "images"
    assert rules.category_for(Path("a.PDF")) == "documents"
    assert rules.category_for(Path("a.py")) == "code"
    assert rules.category_for(Path("a.mp4")) == "videos"


def test_unknown_and_extensionless_files_fall_back_to_other() -> None:
    rules = Ruleset.default()
    assert rules.category_for(Path("mystery.qqq")) == OTHER
    assert rules.category_for(Path("README")) == OTHER
    assert rules.category_for(Path(".gitignore")) == OTHER


def test_extension_matching_is_case_insensitive() -> None:
    rules = Ruleset.default()
    assert rules.category_for(Path("A.JpEg")) == "images"


def test_config_merges_over_defaults(tmp_path: Path) -> None:
    config = tmp_path / "rules.toml"
    config.write_text('[categories]\nphotos = ["jpg"]\n', encoding="utf-8")

    rules = Ruleset.from_toml(config)
    # Overridden.
    assert rules.category_for(Path("a.jpg")) == "photos"
    # Untouched defaults survive.
    assert rules.category_for(Path("a.pdf")) == "documents"
    assert rules.category_for(Path("a.png")) == "images"


def test_config_rejects_extension_claimed_by_two_categories(tmp_path: Path) -> None:
    config = tmp_path / "rules.toml"
    config.write_text('[categories]\nphotos = ["jpg"]\nsnaps = ["jpg"]\n', encoding="utf-8")

    with pytest.raises(ConfigError, match="claimed by both"):
        Ruleset.from_toml(config)


def test_config_rejects_malformed_toml(tmp_path: Path) -> None:
    config = tmp_path / "rules.toml"
    config.write_text("[categories\nbroken", encoding="utf-8")

    with pytest.raises(ConfigError, match="not valid TOML"):
        Ruleset.from_toml(config)


def test_config_rejects_non_list_category(tmp_path: Path) -> None:
    config = tmp_path / "rules.toml"
    config.write_text('[categories]\nimages = "jpg"\n', encoding="utf-8")

    with pytest.raises(ConfigError, match="must be a list"):
        Ruleset.from_toml(config)


def test_missing_config_reports_clearly(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="cannot read"):
        Ruleset.from_toml(tmp_path / "nope.toml")


def test_extensions_may_be_written_with_a_leading_dot(tmp_path: Path) -> None:
    config = tmp_path / "rules.toml"
    config.write_text('[categories]\nphotos = [".JPG"]\n', encoding="utf-8")

    assert Ruleset.from_toml(config).category_for(Path("a.jpg")) == "photos"


def test_exclusions_load_from_settings_table(tmp_path: Path) -> None:
    config = tmp_path / "rules.toml"
    config.write_text('[settings]\nexclude = ["Work", "*.tmp"]\n', encoding="utf-8")

    assert load_exclusions_from_toml(config) == ("Work", "*.tmp")


def test_exclusions_default_to_empty_when_absent(tmp_path: Path) -> None:
    config = tmp_path / "rules.toml"
    config.write_text('[categories]\nimages = ["jpg"]\n', encoding="utf-8")

    assert load_exclusions_from_toml(config) == ()

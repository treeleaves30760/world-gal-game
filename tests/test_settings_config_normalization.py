"""Strict trust-boundary tests for player-owned ``settings.json`` values."""
from __future__ import annotations

import json
import math
from pathlib import Path

import pytest

from world_gal_game import config as config_module
from world_gal_game.config import EngineConfig


_BOOLEAN_FIELDS = (
    "fullscreen",
    "touch_mode",
    "auto_play_wait_voice",
    "skip_unread_only",
    "nvl_mode",
    "rollback_enabled",
    "show_status_hud",
    "show_affection_feedback",
    "dim_inactive_speakers",
    "auto_emote_on_emotion",
    "reduce_motion",
    "show_sound_captions",
    "typewriter_sound",
    "ui_sound_enabled",
    "autosave_enabled",
    "seen_intro",
)

_NUMERIC_FIELDS = (
    ("bgm_volume", 0.0, 1.0, 0.4),
    ("sfx_volume", 0.0, 1.0, 0.4),
    ("voice_volume", 0.0, 1.0, 0.4),
    ("text_speed", 0.0, 1000.0, 80.0),
    ("auto_play_delay", 0.0, 60.0, 1.5),
    ("auto_play_speed", 0.1, 4.0, 1.5),
    ("text_scale", 1.0, 1.4, 1.2),
)


def _config(root: Path, **overrides) -> EngineConfig:
    return EngineConfig(writable_root_override=root, **overrides)


def _write(root: Path, payload: object) -> None:
    (root / "settings.json").write_text(
        json.dumps(payload),
        encoding="utf-8",
    )


def test_normalization_defaults_remain_in_sync_with_engine_config(
    tmp_path: Path,
) -> None:
    defaults = _config(tmp_path)

    assert set(_BOOLEAN_FIELDS) == config_module._BOOLEAN_SETTING_FIELDS
    assert {
        name for name, _lower, _upper, _current in _NUMERIC_FIELDS
    } == set(config_module._NUMERIC_SETTING_BOUNDS)
    for name in (*_BOOLEAN_FIELDS, *(row[0] for row in _NUMERIC_FIELDS)):
        assert config_module._SETTING_DEFAULTS[name] == getattr(defaults, name)


@pytest.mark.parametrize(
    "malformed",
    ["false", "true", 0, 1, None, [], {}],
)
def test_load_never_truth_coerces_persisted_boolean_fields(
    tmp_path: Path,
    malformed: object,
) -> None:
    config = _config(tmp_path)
    for index, name in enumerate(_BOOLEAN_FIELDS):
        setattr(config, name, index % 2 == 0)
    before = {name: getattr(config, name) for name in _BOOLEAN_FIELDS}
    _write(tmp_path, {name: malformed for name in _BOOLEAN_FIELDS})

    config.load_from_disk()

    assert {name: getattr(config, name) for name in _BOOLEAN_FIELDS} == before
    assert all(type(getattr(config, name)) is bool for name in _BOOLEAN_FIELDS)


def test_load_accepts_exact_json_booleans_for_every_boolean_field(
    tmp_path: Path,
) -> None:
    defaults = _config(tmp_path)
    expected = {
        name: not getattr(defaults, name)
        for name in _BOOLEAN_FIELDS
    }
    _write(tmp_path, expected)
    config = _config(tmp_path)

    config.load_from_disk()

    assert {name: getattr(config, name) for name in _BOOLEAN_FIELDS} == expected


def _invalid_numeric(kind: str, lower: float, upper: float) -> object:
    return {
        "numeric_string": str((lower + upper) / 2.0),
        "boolean": True,
        "null": None,
        "nan": float("nan"),
        "positive_infinity": float("inf"),
        "negative_infinity": float("-inf"),
        "below_range": lower - max(0.01, abs(lower) * 0.1 + 0.01),
        "above_range": upper + max(0.01, abs(upper) * 0.1 + 0.01),
    }[kind]


@pytest.mark.parametrize(
    ("name", "lower", "upper", "current"),
    _NUMERIC_FIELDS,
)
@pytest.mark.parametrize(
    "kind",
    [
        "numeric_string",
        "boolean",
        "null",
        "nan",
        "positive_infinity",
        "negative_infinity",
        "below_range",
        "above_range",
    ],
)
def test_load_rejects_non_numeric_non_finite_and_out_of_range_values(
    tmp_path: Path,
    name: str,
    lower: float,
    upper: float,
    current: float,
    kind: str,
) -> None:
    config = _config(tmp_path)
    setattr(config, name, current)
    _write(tmp_path, {name: _invalid_numeric(kind, lower, upper)})

    config.load_from_disk()

    assert getattr(config, name) == current
    assert type(getattr(config, name)) is float
    assert math.isfinite(getattr(config, name))


@pytest.mark.parametrize(
    ("name", "lower", "upper", "_current"),
    _NUMERIC_FIELDS,
)
@pytest.mark.parametrize("endpoint", [0, 1], ids=["lower", "upper"])
def test_load_accepts_numeric_range_endpoints_and_canonicalizes_to_float(
    tmp_path: Path,
    name: str,
    lower: float,
    upper: float,
    _current: float,
    endpoint: int,
) -> None:
    value: int | float = lower if endpoint == 0 else upper
    if float(value).is_integer():
        value = int(value)
    _write(tmp_path, {name: value})
    config = _config(tmp_path)

    config.load_from_disk()

    assert getattr(config, name) == float(value)
    assert type(getattr(config, name)) is float


def test_load_filters_per_character_voice_map_by_key_and_value(
    tmp_path: Path,
) -> None:
    overlong = "x" * (config_module._MAX_CHARACTER_ID_LENGTH + 1)
    _write(tmp_path, {
        "per_character_voice_volume": {
            "heroine_yuki": 0,
            "白若雪": 1,
            "narrator.main": 0.45,
            "": 0.5,
            " padded ": 0.5,
            "line\nbreak": 0.5,
            overlong: 0.5,
            "numeric_string": "0.5",
            "boolean": True,
            "nan": float("nan"),
            "below": -0.01,
            "above": 1.01,
        },
    })
    config = _config(tmp_path)

    config.load_from_disk()

    assert config.per_character_voice_volume == {
        "heroine_yuki": 0.0,
        "白若雪": 1.0,
        "narrator.main": 0.45,
    }
    assert all(
        type(value) is float and math.isfinite(value)
        for value in config.per_character_voice_volume.values()
    )


@pytest.mark.parametrize("malformed", [None, "mapping", [], 1])
def test_load_rejects_non_mapping_character_voice_payload_without_erasing_state(
    tmp_path: Path,
    malformed: object,
) -> None:
    _write(tmp_path, {"per_character_voice_volume": malformed})
    config = _config(
        tmp_path,
        per_character_voice_volume={"keep": 0.35},
    )

    config.load_from_disk()

    assert config.per_character_voice_volume == {"keep": 0.35}


def test_load_rejects_unbounded_character_voice_payload(
    tmp_path: Path,
) -> None:
    oversized = {
        f"speaker_{index}": 0.5
        for index in range(
            config_module._MAX_PER_CHARACTER_VOICE_OVERRIDES + 1,
        )
    }
    _write(tmp_path, {"per_character_voice_volume": oversized})
    config = _config(
        tmp_path,
        per_character_voice_volume={"keep": 0.35},
    )

    config.load_from_disk()

    assert config.per_character_voice_volume == {"keep": 0.35}


def test_save_canonicalizes_poisoned_embedded_settings_before_serializing(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    defaults = _config(tmp_path / "defaults")
    for name in _BOOLEAN_FIELDS:
        setattr(config, name, "false")
    for name, _lower, _upper, _current in _NUMERIC_FIELDS:
        setattr(config, name, float("nan"))
    config.per_character_voice_volume = {  # type: ignore[dict-item]
        "valid": 0.25,
        "numeric_string": "0.5",
        "boolean": True,
        "infinite": float("inf"),
        "": 0.5,
        7: 0.75,
    }

    config.save_to_disk()

    payload = json.loads(config.settings_path().read_text(encoding="utf-8"))
    for name in _BOOLEAN_FIELDS:
        assert getattr(config, name) == getattr(defaults, name)
        assert payload[name] == getattr(defaults, name)
        assert type(payload[name]) is bool
    for name, _lower, _upper, _current in _NUMERIC_FIELDS:
        assert getattr(config, name) == getattr(defaults, name)
        assert payload[name] == getattr(defaults, name)
        assert type(payload[name]) is float
        assert math.isfinite(payload[name])
    assert config.per_character_voice_volume == {"valid": 0.25}
    assert payload["per_character_voice_volume"] == {"valid": 0.25}

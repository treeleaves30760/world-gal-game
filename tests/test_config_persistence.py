"""Tests for EngineConfig settings persistence (WP-F1).

``save_to_disk()`` / ``load_from_disk()`` round-trip only the user-tunable
settings to ``settings.json``. Loading must be robust: a missing file is a
no-op, a corrupt file is ignored (defaults kept), and unknown keys are
skipped — none of these may raise.

In dev mode ``writable_root()`` ignores ``app_data_name`` and returns the
project root, so we monkeypatch the module-level ``writable_root`` to point
at ``tmp_path``. ``settings_path`` / ``save_to_disk`` / ``load_from_disk``
all resolve through that one function, so this keeps the repo clean.
"""
from __future__ import annotations

import json

import pytest

from world_gal_game import config as cfg
from world_gal_game.config import EngineConfig
from world_gal_game.ui.visual import VisualMode


@pytest.fixture
def tmp_settings(monkeypatch, tmp_path):
    """Redirect writable_root() to a temp dir for the duration of a test."""
    monkeypatch.setattr(cfg, "writable_root", lambda *_a, **_k: tmp_path)
    return tmp_path


def test_settings_path_under_writable_root(tmp_settings):
    config = EngineConfig()
    assert config.settings_path() == tmp_settings / "settings.json"


def test_round_trip_preserves_values(tmp_settings):
    config = EngineConfig()
    # Touch a representative subset across types: float, bool, int, str, dict.
    config.bgm_volume = 0.25
    config.voice_volume = 0.5
    config.text_speed = 80.0
    config.auto_play_delay = 1.5
    config.touch_mode = True
    config.auto_play_speed = 1.5
    config.auto_play_wait_voice = False
    config.skip_unread_only = False
    config.nvl_mode = True
    config.per_character_voice_volume = {"yuki": 0.8, "rin": 0.3}
    config.autosave_enabled = False
    config.autosave_slot_count = 5
    config.quicksave_slot = "qs_main"
    config.save_to_disk()

    assert config.settings_path().exists()

    fresh = EngineConfig()
    fresh.load_from_disk()
    assert fresh.bgm_volume == 0.25
    assert fresh.voice_volume == 0.5
    assert fresh.text_speed == 80.0
    assert fresh.auto_play_delay == 1.5
    assert fresh.touch_mode is True
    assert fresh.auto_play_speed == 1.5
    assert fresh.auto_play_wait_voice is False
    assert fresh.skip_unread_only is False
    assert fresh.nvl_mode is True
    assert fresh.per_character_voice_volume == {"yuki": 0.8, "rin": 0.3}
    assert fresh.autosave_enabled is False
    assert fresh.autosave_slot_count == 5
    assert fresh.quicksave_slot == "qs_main"


def test_only_user_settings_are_serialized(tmp_settings):
    config = EngineConfig()
    config.save_to_disk()
    data = json.loads(config.settings_path().read_text(encoding="utf-8"))
    # User-tunable fields are present.
    assert "bgm_volume" in data
    assert "nvl_mode" in data
    assert "per_character_voice_volume" in data
    # Pack/path/dev fields are intentionally excluded.
    for excluded in ("default_pack", "save_subdir", "dev_mode",
                     "screen_size", "title", "app_data_name",
                     "extra_pack_dirs", "game_pack_dir",
                     "max_image_cache_mb", "max_sound_cache_mb"):
        assert excluded not in data


def test_missing_file_keeps_defaults(tmp_settings):
    # No settings.json written -> load is a silent no-op, defaults intact.
    assert not (tmp_settings / "settings.json").exists()
    config = EngineConfig()
    config.load_from_disk()
    defaults = EngineConfig()
    assert config.nvl_mode == defaults.nvl_mode is False
    assert config.auto_play_speed == defaults.auto_play_speed == 1.0
    assert config.quicksave_slot == defaults.quicksave_slot == "quicksave"


def test_corrupt_file_keeps_defaults(tmp_settings):
    # Unparseable JSON must be ignored without raising.
    (tmp_settings / "settings.json").write_text("{not valid json,,,",
                                                encoding="utf-8")
    config = EngineConfig()
    config.load_from_disk()  # must not raise
    assert config.auto_play_speed == 1.0
    assert config.nvl_mode is False
    assert config.autosave_slot_count == 3


def test_non_object_json_keeps_defaults(tmp_settings):
    # Valid JSON but not an object (e.g. a list) -> ignored, no crash.
    (tmp_settings / "settings.json").write_text("[1, 2, 3]", encoding="utf-8")
    config = EngineConfig()
    config.load_from_disk()
    assert config.text_speed == EngineConfig().text_speed


def test_unknown_key_is_ignored(tmp_settings):
    payload = {
        "bgm_volume": 0.42,
        "totally_unknown_setting": "ignore me",
        "default_pack": "should_not_be_applied",
    }
    (tmp_settings / "settings.json").write_text(json.dumps(payload),
                                                encoding="utf-8")
    config = EngineConfig()
    config.load_from_disk()
    assert config.bgm_volume == 0.42
    # Unknown key did not become an attribute.
    assert not hasattr(config, "totally_unknown_setting")
    # default_pack is not a persisted setting, so it stays at its default
    # even though the file contained it.
    assert config.default_pack == EngineConfig().default_pack


def test_partial_file_only_overwrites_present_keys(tmp_settings):
    # A file with a subset of keys leaves the rest at their defaults.
    (tmp_settings / "settings.json").write_text(
        json.dumps({"nvl_mode": True}), encoding="utf-8")
    config = EngineConfig()
    config.text_speed = 12.0  # locally tweaked, absent from file
    config.load_from_disk()
    assert config.nvl_mode is True            # came from file
    assert config.auto_play_speed == 1.0      # default, untouched
    # Key absent from file is left as-is on the instance (not reset).
    assert config.text_speed == 12.0


def test_input_bindings_round_trip_in_canonical_form(tmp_settings):
    config = EngineConfig()
    config.input_bindings = {
        "confirm_advance": ["C", "c"],
        "open_history": [],
        "open_map": ["f5"],
        "unknown_action": ["q"],
    }
    config.save_to_disk()

    data = json.loads(config.settings_path().read_text(encoding="utf-8"))
    assert data["input_bindings"] == {
        "confirm_advance": ["c"],
        "open_history": [],
    }

    fresh = EngineConfig()
    fresh.load_from_disk()
    assert fresh.input_bindings == data["input_bindings"]


@pytest.mark.parametrize(
    "malformed",
    [None, "not-an-object", ["m"], 42],
)
def test_malformed_input_binding_setting_falls_back_safely(
    tmp_settings,
    malformed,
):
    (tmp_settings / "settings.json").write_text(
        json.dumps({"input_bindings": malformed}),
        encoding="utf-8",
    )
    config = EngineConfig()
    config.load_from_disk()
    assert config.input_bindings == {}


def test_pack_name_variants_are_deterministic_and_exact_first():
    variants = cfg._name_variants("demo_pack")
    assert variants[0] == "demo_pack"
    assert len(variants) == len(set(variants))


def test_sound_caption_preference_round_trips_and_legacy_missing_is_on(
    tmp_settings,
):
    config = EngineConfig()
    assert config.show_sound_captions is True
    config.show_sound_captions = False
    config.save_to_disk()

    fresh = EngineConfig()
    fresh.load_from_disk()
    assert fresh.show_sound_captions is False

    (tmp_settings / "settings.json").write_text(
        json.dumps({"text_speed": 20.0}),
        encoding="utf-8",
    )
    legacy = EngineConfig()
    legacy.load_from_disk()
    assert legacy.show_sound_captions is True


@pytest.mark.parametrize(
    "malformed",
    [None, 0, 1, "false", "true", [], {}],
)
def test_malformed_sound_caption_preference_is_not_truth_coerced(
    tmp_settings,
    malformed,
):
    (tmp_settings / "settings.json").write_text(
        json.dumps({"show_sound_captions": malformed}),
        encoding="utf-8",
    )
    config = EngineConfig(show_sound_captions=False)

    config.load_from_disk()

    assert config.show_sound_captions is False


def test_save_normalizes_malformed_embedded_sound_caption_preference(
    tmp_settings,
):
    config = EngineConfig()
    config.show_sound_captions = "false"  # type: ignore[assignment]

    config.save_to_disk()

    payload = json.loads(
        config.settings_path().read_text(encoding="utf-8"),
    )
    assert config.show_sound_captions is True
    assert payload["show_sound_captions"] is True


@pytest.mark.parametrize("mode", ["standard", "high_contrast"])
def test_visual_accessibility_mode_round_trips_as_an_exact_string(
    tmp_settings,
    mode,
):
    config = EngineConfig(visual_accessibility_mode=mode)

    config.save_to_disk()

    payload = json.loads(
        config.settings_path().read_text(encoding="utf-8"),
    )
    assert payload["visual_accessibility_mode"] == mode
    assert type(payload["visual_accessibility_mode"]) is str

    fresh = EngineConfig()
    fresh.load_from_disk()
    assert fresh.visual_accessibility_mode == mode


def test_legacy_settings_without_visual_mode_keep_standard_default(
    tmp_settings,
):
    (tmp_settings / "settings.json").write_text(
        json.dumps({"text_speed": 20.0}),
        encoding="utf-8",
    )

    config = EngineConfig()
    config.load_from_disk()

    assert config.visual_accessibility_mode == "standard"


@pytest.mark.parametrize(
    "malformed",
    [
        None,
        False,
        True,
        0,
        1,
        "",
        "HIGH_CONTRAST",
        " high_contrast",
        "high_contrast ",
        [],
        {},
    ],
)
def test_malformed_visual_mode_loads_as_canonical_standard(
    tmp_settings,
    malformed,
):
    (tmp_settings / "settings.json").write_text(
        json.dumps({"visual_accessibility_mode": malformed}),
        encoding="utf-8",
    )
    config = EngineConfig(visual_accessibility_mode="high_contrast")

    config.load_from_disk()

    assert config.visual_accessibility_mode == "standard"


@pytest.mark.parametrize(
    ("embedded", "expected"),
    [
        (VisualMode.HIGH_CONTRAST, "high_contrast"),
        ("high_contrast", "high_contrast"),
        ("standard", "standard"),
        (None, "standard"),
        (False, "standard"),
        (True, "standard"),
        (1, "standard"),
        ("HIGH_CONTRAST", "standard"),
        (" high_contrast", "standard"),
    ],
)
def test_save_normalizes_visual_mode_without_truth_coercion(
    tmp_settings,
    embedded,
    expected,
):
    config = EngineConfig()
    config.visual_accessibility_mode = embedded  # type: ignore[assignment]

    config.save_to_disk()

    payload = json.loads(
        config.settings_path().read_text(encoding="utf-8"),
    )
    assert config.visual_accessibility_mode == expected
    assert payload["visual_accessibility_mode"] == expected
    assert type(payload["visual_accessibility_mode"]) is str


def test_locale_preferences_round_trip_per_pack(tmp_settings) -> None:
    config = EngineConfig()
    config.pack_id = "pack-one"

    assert config.set_preferred_locale("en-US") is True
    config.pack_id = "pack-two"
    assert config.set_preferred_locale("ja") is True
    config.save_to_disk()

    fresh = EngineConfig()
    fresh.load_from_disk()
    assert fresh.preferred_locale("pack-one") == "en-US"
    assert fresh.preferred_locale("pack-two") == "ja"
    assert fresh.preferred_locale("pack-three") == ""


def test_runtime_locale_override_is_not_persisted(tmp_settings) -> None:
    config = EngineConfig(locale="en")
    config.pack_id = "demo"
    assert config.set_preferred_locale("zh-Hant") is True
    config.save_to_disk()

    payload = json.loads(config.settings_path().read_text(encoding="utf-8"))
    assert "locale" not in payload
    assert payload["locale_preferences"] == {"demo": "zh-Hant"}

    fresh = EngineConfig()
    fresh.load_from_disk()
    assert fresh.locale == ""
    assert fresh.preferred_locale("demo") == "zh-Hant"


@pytest.mark.parametrize(
    "bad_value",
    [
        None,
        [],
        {"demo": True},
        {"demo": "../en"},
        {"demo": "https://example.test/en"},
        {"../": "en"},
    ],
)
def test_locale_preferences_reject_untrusted_shapes(
    tmp_settings,
    bad_value,
) -> None:
    (tmp_settings / "settings.json").write_text(
        json.dumps({"locale_preferences": bad_value}),
        encoding="utf-8",
    )
    config = EngineConfig()
    config.load_from_disk()

    assert config.locale_preferences == {}
    assert config.preferred_locale("demo") == ""


def test_set_preferred_locale_rejects_path_or_unknown_shape(
    tmp_settings,
) -> None:
    config = EngineConfig(pack_id="demo")

    assert config.set_preferred_locale("../en") is False
    assert config.set_preferred_locale("en_US") is False
    assert config.set_preferred_locale("") is False
    assert config.locale_preferences == {}

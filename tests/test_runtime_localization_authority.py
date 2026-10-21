from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from world_gal_game.core.localization import (
    LocaleManifestError,
    Localization,
    LocaleOption,
    message_table_digest,
)


def _write_catalog(
    path: Path,
    *,
    language: str,
    source_messages: dict[str, str],
    messages: dict[str, str],
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.safe_dump(
            {
                "format_version": 1,
                "language": language,
                "source_language": "zh-Hant",
                "source_digest": message_table_digest(source_messages),
                "messages": messages,
            },
            allow_unicode=True,
            sort_keys=False,
        ),
        encoding="utf-8",
    )


def _manifest(*, catalog: str = "content/i18n/en.yaml") -> dict:
    return {
        "locale": {
            # Legacy source overrides remain authoritative fallbacks.
            "ui": {"new_game": "開始", "day_format": "第 {day} 天"},
            "source": "zh-Hant",
            "default": "zh-Hant",
            "languages": {
                "zh-Hant": {
                    "label": "繁體中文",
                    "status": "complete",
                    "font": {
                        "bundled": "assets/fonts/source.ttf",
                        "candidates": ["Source Han Sans TC"],
                    },
                    "story_scenes": "all",
                },
                "en": {
                    "label": "English Preview",
                    "status": "preview",
                    "catalog": catalog,
                    "font": {"candidates": ["Noto Sans", "Arial"]},
                    "story_scenes": ["intro"],
                },
            },
        }
    }


def test_legacy_locale_merge_is_unchanged_and_has_no_selector() -> None:
    localization = Localization.from_meta({
        "locale": {
            "ui": {"new_game": "Go"},
            "time_of_day": {"morning": "AM"},
        }
    })

    assert localization.t("new_game") == "Go"
    assert localization.time_label("morning") == "AM"
    assert localization.options == ()
    assert localization.source == localization.default == ""
    assert localization.requested == localization.effective == localization.active == ""


def test_manifest_catalog_activation_story_scope_and_font_fallback(
    tmp_path: Path,
) -> None:
    (tmp_path / "assets" / "fonts").mkdir(parents=True)
    (tmp_path / "assets" / "fonts" / "source.ttf").write_bytes(b"font")
    source = {
        "ui.new_game": "開始",
        "intro.line.welcome": "你好，{player_name}",
        "other.line.0": "來源",
        "meta.title": "來源標題",
        "music_room.library_theme.usage": "圖書館深夜",
    }
    _write_catalog(
        tmp_path / "content" / "i18n" / "en.yaml",
        language="en",
        source_messages=source,
        messages={
            "ui.new_game": "New Game",
            "intro.line.welcome": "Hello, {player_name}",
            "other.line.0": "Target outside preview",
            "meta.title": "Localized title",
            "music_room.library_theme.usage": "Library at night",
        },
    )

    localization = Localization.from_meta(
        _manifest(),
        pack_root=tmp_path,
        requested="EN",
    )

    assert localization.source == "zh-Hant"
    assert localization.default == "zh-Hant"
    assert localization.requested == localization.effective == localization.active == "en"
    assert all(isinstance(option, LocaleOption) for option in localization.options)
    assert [option.model_dump() for option in localization.options] == [
        {
            "id": "zh-Hant",
            "label": "繁體中文",
            "status": "complete",
            "selected": False,
        },
        {
            "id": "en",
            "label": "English Preview",
            "status": "preview",
            "selected": True,
        },
    ]
    assert localization.t("new_game") == "New Game"
    assert localization.story(
        "intro.line.welcome",
        "你好，{player_name}",
    ) == "Hello, {player_name}"
    assert localization.story("other.line.0", "來源") == "來源"
    assert localization.story("meta.title", "來源標題") == "Localized title"
    assert localization.story(
        "music_room.library_theme.usage",
        "圖書館深夜",
    ) == "Library at night"
    assert localization.font_sources.model_dump() == {
        "bundled": "assets/fonts/source.ttf",
        "candidates": ("Noto Sans", "Arial", "Source Han Sans TC"),
    }

    # Unsupported/path-like input performs no I/O and never leaves the previous
    # target half-active: it atomically resolves to the declared fallback.
    assert localization.activate("../../en") == "zh-Hant"
    assert localization.requested == ""
    assert localization.t("new_game") == "開始"
    assert [option.selected for option in localization.options] == [True, False]


def test_bad_target_catalog_is_excluded_as_one_atomic_option(tmp_path: Path) -> None:
    bad = tmp_path / "i18n" / "en.yaml"
    bad.parent.mkdir(parents=True)
    bad.write_text(
        """format_version: 1
language: en
source_language: zh-Hant
source_digest: aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
messages:
  ui.new_game: New Game
  ui.new_game: Duplicate
""",
        encoding="utf-8",
    )
    meta = {
        "locale": {
            "source": "zh-Hant",
            "default": "en",
            "languages": {
                "zh-Hant": {"label": "繁體中文", "status": "complete"},
                "en": {
                    "label": "English",
                    "status": "complete",
                    # Explicit legacy pack-root catalog location remains valid.
                    "catalog": "i18n/en.yaml",
                },
            },
        }
    }

    localization = Localization.from_meta(meta, pack_root=tmp_path, requested="en")
    assert localization.active == "zh-Hant"
    assert [option.id for option in localization.options] == ["zh-Hant"]
    assert localization.errors and "duplicate key" in localization.errors[0]
    assert localization.activate("en") == "zh-Hant"

    with pytest.raises(LocaleManifestError):
        Localization.from_meta(
            meta,
            pack_root=tmp_path,
            requested="en",
            strict=True,
        )


def test_catalog_cannot_escape_pack_and_bad_placeholders_fall_back(
    tmp_path: Path,
) -> None:
    (tmp_path / "assets" / "fonts").mkdir(parents=True)
    (tmp_path / "assets" / "fonts" / "source.ttf").write_bytes(b"font")
    source = {"intro.line.0": "Hi {player_name}"}
    _write_catalog(
        tmp_path / "content" / "i18n" / "en.yaml",
        language="en",
        source_messages=source,
        messages={"intro.line.0": "Hello {wrong_name}"},
    )
    meta = _manifest()
    localization = Localization.from_meta(meta, pack_root=tmp_path, requested="en")
    assert localization.story("intro.line.0", "Hi {player_name}") == "Hi {player_name}"

    escaped = _manifest(catalog="../outside.yaml")
    unsafe = Localization.from_meta(escaped, pack_root=tmp_path, requested="en")
    assert unsafe.active == "zh-Hant"
    assert [option.id for option in unsafe.options] == ["zh-Hant"]
    assert any("escape" in error for error in unsafe.errors)


def test_declared_source_catalog_blocks_stale_target_per_key(tmp_path: Path) -> None:
    source = {
        "ui.new_game": "開始",
        "intro.line.0": "原始台詞",
    }
    _write_catalog(
        tmp_path / "content" / "i18n" / "messages.zh-Hant.yaml",
        language="zh-Hant",
        source_messages=source,
        messages=source,
    )
    _write_catalog(
        tmp_path / "content" / "i18n" / "en.yaml",
        language="en",
        source_messages=source,
        messages={
            "ui.new_game": "New Game",
            "intro.line.0": "Translated line",
        },
    )
    meta = {
        "locale": {
            "ui": {"new_game": "開始"},
            "source": "zh-Hant",
            "default": "en",
            "languages": {
                "zh-Hant": {
                    "label": "繁體中文",
                    "status": "complete",
                    "catalog": "content/i18n/messages.zh-Hant.yaml",
                },
                "en": {
                    "label": "English",
                    "status": "complete",
                    "catalog": "content/i18n/en.yaml",
                },
            },
        }
    }
    localization = Localization.from_meta(
        meta,
        pack_root=tmp_path,
        requested="en",
    )

    assert localization.t("new_game") == "New Game"
    assert localization.story("intro.line.0", "原始台詞") == "Translated line"

    # Catalogs are internally digest-consistent, but live authored/default copy
    # changed after extraction. Only that stale key falls back; no target string
    # from the old source is applied.
    localization.ui["new_game"] = "開始遊戲"
    assert localization.t("new_game") == "開始遊戲"
    assert localization.story("intro.line.0", "改寫後台詞") == "改寫後台詞"

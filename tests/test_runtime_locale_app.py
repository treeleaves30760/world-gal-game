"""End-to-end runtime locale wiring at the title-safe boundary."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

import pygame  # noqa: E402
import yaml  # noqa: E402

from world_gal_game.config import EngineConfig  # noqa: E402
from world_gal_game.core.localization import message_table_digest  # noqa: E402


_REPO = Path(__file__).resolve().parents[1]


def test_cli_accepts_an_explicit_runtime_locale() -> None:
    from world_gal_game.cli import build_parser

    args = build_parser().parse_args(["--locale", "EN-us"])
    assert args.locale == "EN-us"


def _write_catalog(
    path: Path,
    *,
    language: str,
    source: dict[str, str],
    messages: dict[str, str],
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.safe_dump(
            {
                "format_version": 1,
                "language": language,
                "source_language": "zh-Hant-TW",
                "source_digest": message_table_digest(source),
                "messages": messages,
            },
            allow_unicode=True,
            sort_keys=False,
            width=1000,
        ),
        encoding="utf-8",
    )


def _localized_demo(tmp_path: Path) -> Path:
    pack = tmp_path / "pack"
    shutil.copytree(_REPO / "games" / "demo_pack", pack)
    meta_path = pack / "content" / "meta.yaml"
    meta = yaml.safe_load(meta_path.read_text(encoding="utf-8"))
    legacy_font = pack / "assets" / "fonts" / "legacy.ttf"
    legacy_font.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(
        Path(pygame.__file__).parent / pygame.font.get_default_font(),
        legacy_font,
    )
    meta["bundled_font"] = "assets/fonts/legacy.ttf"
    source = {
        "meta.title": meta["title"],
        "meta.subtitle": meta["subtitle"],
        "ui.new_game": "新遊戲",
        "ui.settings": "設定",
        "ui.language": "語言",
        "ui.language_title_only_help": (
            "為保留目前台詞與存檔狀態，請回到標題畫面切換語言。"
        ),
        "prologue.title": "序章 · 搬家當天",
        "prologue.line.2": "「總之，先出去走走吧。」",
        "prologue.line.2.speaker": "玩家",
    }
    translated = {
        "meta.title": "An Afternoon in Town",
        "meta.subtitle": "Runtime Locale Preview",
        "ui.new_game": "New Game",
        "ui.settings": "Settings",
        "ui.language": "Language",
        "ui.language_title_only_help": (
            "Return to the title screen before changing language."
        ),
        "prologue.title": "Prologue · Moving Day",
        "prologue.line.2": "“For now, I should go take a look around.”",
        "prologue.line.2.speaker": "Player",
    }
    _write_catalog(
        pack / "content" / "i18n" / "messages.zh-Hant-TW.yaml",
        language="zh-Hant-TW",
        source=source,
        messages=source,
    )
    _write_catalog(
        pack / "content" / "i18n" / "en.yaml",
        language="en",
        source=source,
        messages=translated,
    )
    meta["locale"] = {
        "source": "zh-Hant-TW",
        "default": "zh-Hant-TW",
        "languages": {
            "zh-Hant-TW": {
                "label": "繁體中文",
                "status": "complete",
                "catalog": "content/i18n/messages.zh-Hant-TW.yaml",
                "font": {"candidates": ["Noto Sans TC"]},
                "story_scenes": "all",
            },
            "en": {
                "label": "English Preview",
                "status": "preview",
                "catalog": "content/i18n/en.yaml",
                "font": {"candidates": ["Arial"]},
                "story_scenes": ["prologue"],
            },
        },
    }
    meta_path.write_text(
        yaml.safe_dump(meta, allow_unicode=True, sort_keys=False, width=1000),
        encoding="utf-8",
    )
    return pack


def test_app_applies_saved_pack_locale_and_switches_only_at_title(
    tmp_path: Path,
) -> None:
    from world_gal_game.app import GalGameApp
    from world_gal_game.scenes.settings_scene import SettingsScene
    from world_gal_game.scenes.title import TitleScene

    pack = _localized_demo(tmp_path)
    config = EngineConfig(
        default_pack=str(pack),
        writable_root_override=tmp_path / "user",
        locale_preferences={"pack": "en"},
        seen_intro=True,
    )
    app = GalGameApp(config=config, pack=str(pack), headless=True)
    app.manager.commit_pending()
    try:
        title = app.manager.current
        assert isinstance(title, TitleScene)
        assert app.localization.effective == "en"
        assert title.title_text == "An Afternoon in Town"
        assert title.subtitle_text == "Runtime Locale Preview"
        assert app.fonts.candidates[:1] == ("Arial",)
        assert app.fonts.resolved_path != pack / "assets" / "fonts" / "legacy.ttf"

        # Unsupported embedding/callback input is an atomic no-op; it cannot
        # leave authority on the fallback while title/font copy stays English.
        assert app._change_locale_from_settings("ja") == "en"
        assert app.localization.effective == "en"
        assert title.title_text == "An Afternoon in Town"

        app._open_settings()
        app.manager.commit_pending()
        settings = app.manager.current
        assert isinstance(settings, SettingsScene)
        assert settings.describe()["locale_change_allowed"] is True
        settings._set_locale("zh-hant-tw")

        assert app.localization.effective == "zh-Hant-TW"
        assert title.title_text == "小鎮的午後"
        assert title.subtitle_text == "Demo Pack — Engine Showcase"
        saved = json.loads(config.settings_path().read_text(encoding="utf-8"))
        assert saved["locale_preferences"]["pack"] == "zh-Hant-TW"
    finally:
        pygame.quit()


def test_headless_story_locales_are_isolated_and_change_is_boundary_safe(
    tmp_path: Path,
) -> None:
    from world_gal_game.headless import HeadlessSession

    pack = _localized_demo(tmp_path)
    english = HeadlessSession.open(
        EngineConfig(locale_preferences={"pack": "en"}),
        pack=str(pack),
    )
    source = HeadlessSession.open(EngineConfig(), pack=str(pack))

    english_inspect = english.inspect()
    assert english_inspect["locale"]["effective"] == "en"
    assert english_inspect["title"] == "An Afternoon in Town"
    assert english_inspect["subtitle"] == "Runtime Locale Preview"
    assert source.inspect()["locale"]["effective"] == "zh-Hant-TW"
    assert english.start_scene("prologue")["presentation"]["title"] == (
        "Prologue · Moving Day"
    )
    assert source.start_scene("prologue")["presentation"]["title"] == (
        "序章 · 搬家當天"
    )

    for _ in range(2):
        english.next_line()
        source.next_line()
    assert english.last_presentation["line"]["speaker"] == "Player"
    assert english.last_presentation["line"]["text"] == (
        "“For now, I should go take a look around.”"
    )
    assert source.last_presentation["line"]["speaker"] == "玩家"

    blocked = english.set_locale("zh-Hant-TW")
    assert blocked["ok"] is False
    assert blocked["effective"] == "en"
    english.next_line()
    english.next_line()
    switched = english.set_locale("ZH-hant-tw")
    assert switched == {
        "ok": True,
        "requested": "ZH-hant-tw",
        "effective": "zh-Hant-TW",
    }
    assert english.run_script([{"op": "locale", "locale": "EN"}]) == [{
        "ok": True,
        "requested": "EN",
        "effective": "en",
        "op": "locale",
    }]

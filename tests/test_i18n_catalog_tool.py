from __future__ import annotations

from pathlib import Path
import sys

import pytest
import yaml

import world_gal_game
from world_gal_game.core.localization import (
    load_translation_catalog,
    message_table_digest,
)

_REPO = Path(world_gal_game.__file__).parent.parent
sys.path.insert(0, str(_REPO / "tools"))
import i18n_extract  # noqa: E402


def _pack(
    tmp_path: Path,
    *,
    stable: bool = True,
    loc_id: str = "welcome",
) -> Path:
    root = tmp_path / "pack"
    meta_path = root / "content" / "meta.yaml"
    meta_path.parent.mkdir(parents=True, exist_ok=True)
    meta_path.write_text(
        yaml.safe_dump(
            {
                "title": "測試標題",
                "subtitle": "測試副標",
                "title_screen": {
                    "tagline": "標題標語",
                    "footer_text": "頁尾文案",
                },
                "locale": {"ui": {"new_game": "自訂開始"}},
            },
            allow_unicode=True,
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    line = {
        "speaker": "嚮導",
        "text": "[b]你好，{player_name}[/b][[ping:x]]",
        "sound_captions": [{"kind": "ambient", "text": "風吹過樹葉"}],
    }
    if stable:
        line["loc_id"] = loc_id
    scenes = [{
        "id": "intro",
        "title": "序章",
        "lines": [line],
        "choices": [{
            "id": "yes",
            "text": "答應",
            "card": {
                "title": "前往",
                "subtitle": "現在",
                "badge": "推薦",
                "image_alt": "校門",
                "action": "確認",
                "details": [{"label": "時間", "text": "上午"}],
                "tags": ["主線"],
            },
        }],
    }]
    path = root / "content" / "scenes" / "intro.yaml"
    path.parent.mkdir(parents=True)
    path.write_text(
        yaml.safe_dump({"scenes": scenes}, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    return root


def _translated_catalog(
    path: Path,
    source: dict[str, str],
    messages: dict[str, str],
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.safe_dump(
            {
                "format_version": 1,
                "language": "en",
                "source_language": "zh-Hant",
                "source_digest": message_table_digest(source),
                "messages": messages,
            },
            allow_unicode=True,
            sort_keys=False,
        ),
        encoding="utf-8",
    )


def test_extracts_stable_line_speaker_caption_and_all_card_copy(
    tmp_path: Path,
) -> None:
    report = i18n_extract.extract_pack_report(_pack(tmp_path))
    assert report.unstable_lines == ()
    expected = {
        "meta.title": "測試標題",
        "meta.subtitle": "測試副標",
        "meta.title_screen.tagline": "標題標語",
        "meta.title_screen.footer_text": "頁尾文案",
        "ui.new_game": "自訂開始",
        "intro.title": "序章",
        "intro.line.welcome": "[b]你好，{player_name}[/b][[ping:x]]",
        "intro.line.welcome.speaker": "嚮導",
        "intro.line.welcome.sound_caption.0": "風吹過樹葉",
        "intro.choice.yes": "答應",
        "intro.choice.yes.card.title": "前往",
        "intro.choice.yes.card.subtitle": "現在",
        "intro.choice.yes.card.badge": "推薦",
        "intro.choice.yes.card.image_alt": "校門",
        "intro.choice.yes.card.action": "確認",
        "intro.choice.yes.card.detail.0.label": "時間",
        "intro.choice.yes.card.detail.0.text": "上午",
        "intro.choice.yes.card.tag.0": "主線",
    }
    assert {
        key: report.messages[key]
        for key in expected
    } == expected
    # Engine UI keys not explicitly overridden by the pack still participate in
    # source digest/coverage under the same ui.* namespace.
    assert report.messages["ui.settings"] == "設定"


def test_extracts_music_room_copy_with_stable_and_fallback_keys(
    tmp_path: Path,
) -> None:
    from world_gal_game.core.music_room_spec import (
        MusicRoomEntrySpec,
        music_room_localization_id,
    )

    pack = _pack(tmp_path)
    music_path = pack / "content" / "music_room.yaml"
    music_path.write_text(
        "entries:\n"
        "  - asset: assets/bgm/library.ogg\n"
        "    loc_id: library_theme\n"
        "    title: 書庫之夜\n"
        "    artist: 作曲者\n"
        "    usage: 圖書館\n"
        "  - asset: assets/bgm/legacy.ogg\n"
        "    title: 舊曲目\n",
        encoding="utf-8",
    )

    messages = i18n_extract.extract_pack(pack)
    fallback = music_room_localization_id(MusicRoomEntrySpec(
        asset="assets/bgm/legacy.ogg",
        title="舊曲目",
    ))

    assert messages["music_room.library_theme.title"] == "書庫之夜"
    assert messages["music_room.library_theme.artist"] == "作曲者"
    assert messages["music_room.library_theme.usage"] == "圖書館"
    assert messages[f"music_room.{fallback}.title"] == "舊曲目"
    assert f"music_room.{fallback}.artist" not in messages


def test_preview_scope_always_requires_static_music_room_metadata() -> None:
    messages = {
        "ui.music_room": "音樂室",
        "meta.title": "測試標題",
        "music_room.library_theme.title": "書庫之夜",
        "music_room.library_theme.usage": "圖書館",
        "intro.line.welcome": "你好",
        "other.line.hidden": "不在預覽範圍",
    }

    assert i18n_extract._keys_for_scenes(messages, ("intro",)) == {
        "ui.music_room",
        "meta.title",
        "music_room.library_theme.title",
        "music_room.library_theme.usage",
        "intro.line.welcome",
    }


def test_output_is_versioned_digest_bound_and_uses_content_i18n(
    tmp_path: Path,
) -> None:
    pack = _pack(tmp_path)
    source = i18n_extract.extract_pack(pack)

    assert i18n_extract.main([str(pack), "--out", "--src-lang", "zh-Hant"]) == 0
    source_path = pack / "content" / "i18n" / "messages.zh-Hant.yaml"
    catalog = load_translation_catalog(source_path)
    assert catalog.language == catalog.source_language == "zh-Hant"
    assert catalog.source_digest == message_table_digest(source)
    assert catalog.messages == source

    assert i18n_extract.main([
        str(pack), "--template", "en", "--src-lang", "zh-Hant"
    ]) == 0
    target = load_translation_catalog(pack / "content" / "i18n" / "en.yaml")
    assert target.language == "en"
    assert set(target.messages) == set(source)
    assert not any(target.messages.values())

    # A locale filename is created only after strict tag validation.
    assert i18n_extract.main([
        str(pack), "--template", "../../escape", "--src-lang", "zh-Hant"
    ]) == 1
    assert not (tmp_path / "escape.yaml").exists()


def test_versioned_check_rejects_extra_empty_and_structural_drift(
    tmp_path: Path,
) -> None:
    pack = _pack(tmp_path)
    source = i18n_extract.extract_pack(pack)
    translated = dict(source)
    translated["intro.line.welcome"] = (
        "[b]Hello, {player_name}[/b][[ping:x]]"
    )
    path = pack / "content" / "i18n" / "en.yaml"
    _translated_catalog(path, source, translated)
    assert i18n_extract.main([
        str(pack), "--check", str(path), "--src-lang", "zh-Hant",
        "--status", "complete",
    ]) == 0

    translated["intro.line.welcome"] = "Hello, {wrong}"
    _translated_catalog(path, source, translated)
    assert i18n_extract.main([
        str(pack), "--check", str(path), "--src-lang", "zh-Hant"
    ]) == 1

    translated = dict(source)
    translated["extra.key"] = "extra"
    _translated_catalog(path, source, translated)
    assert i18n_extract.main([
        str(pack), "--check", str(path), "--src-lang", "zh-Hant"
    ]) == 1

    translated = dict(source)
    translated["intro.title"] = ""
    _translated_catalog(path, source, translated)
    assert i18n_extract.main([
        str(pack), "--check", str(path), "--src-lang", "zh-Hant"
    ]) == 1


def test_complete_check_can_require_loc_id_and_duplicates_fail_closed(
    tmp_path: Path,
) -> None:
    pack = _pack(tmp_path, stable=False)
    source = i18n_extract.extract_pack(pack)
    path = pack / "content" / "i18n" / "en.yaml"
    _translated_catalog(path, source, source)
    assert i18n_extract.main([
        str(pack), "--check", str(path), "--src-lang", "zh-Hant",
        "--status", "complete", "--require-stable-line-ids",
    ]) == 1

    duplicate = pack / "content" / "scenes" / "duplicate.yaml"
    duplicate.write_text(
        """scenes:
  - id: duplicate
    title: First
    title: Second
    lines: []
""",
        encoding="utf-8",
    )
    with pytest.raises(i18n_extract.ExtractionError, match="duplicate key"):
        i18n_extract.extract_pack(pack)


@pytest.mark.parametrize("bad", ["_leading", "has.dot", "含中文", "a" * 65])
def test_loc_id_uses_one_unambiguous_64_char_ascii_segment(
    tmp_path: Path,
    bad: str,
) -> None:
    with pytest.raises(i18n_extract.ExtractionError, match="loc_id must match"):
        i18n_extract.extract_pack(_pack(tmp_path, loc_id=bad))

    # Boundary proof is kept in the same test family: exactly 64 characters is
    # valid and becomes the key segment verbatim.
    valid_root = _pack(tmp_path / "valid", loc_id="a" * 64)
    assert f"intro.line.{'a' * 64}" in i18n_extract.extract_pack(valid_root)

#!/usr/bin/env python3
"""Extract and verify versioned runtime-localization catalogs.

The canonical output directory is ``<pack>/content/i18n``.  Catalog paths used
at runtime still come exclusively from ``meta.locale.languages.*.catalog``;
the locale id is never a runtime path selector.  This tool validates a locale id
before using it as an authoring-time output filename.

The source authority includes runtime ``meta.*`` title copy, all canonical
``ui.*`` defaults/pack overrides, scene titles, dialogue text/speakers, authored
sound captions, choice labels, and every player-facing choice-card string. A
line may declare ``loc_id`` for a stable key; old packs retain numeric indexes.

Usage::

    # Print the source messages as a flat YAML table.
    uv run python tools/i18n_extract.py games/demo_pack

    # Write content/i18n/messages.zh-Hant.yaml (a v1 catalog envelope).
    uv run python tools/i18n_extract.py games/demo_pack --out --src-lang zh-Hant

    # Write an empty, digest-bound target catalog.
    uv run python tools/i18n_extract.py games/demo_pack --template en \
        --src-lang zh-Hant

    # Check coverage and placeholder/markup/dialogue-op parity.
    uv run python tools/i18n_extract.py games/demo_pack \
        --check games/demo_pack/content/i18n/en.yaml --src-lang zh-Hant
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path
import re
import sys
from typing import Any, Mapping

import yaml

# ``python tools/i18n_extract.py`` starts with tools/ on sys.path.  Make the
# source checkout importable without requiring installation; an installed
# console environment already resolves this normally.
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from world_gal_game.core.localization import (  # noqa: E402
    DEFAULT_AFFECTION_LEVELS,
    DEFAULT_DAY_OF_WEEK,
    DEFAULT_TIME_OF_DAY,
    DEFAULT_UI,
    MAX_CATALOG_MESSAGES,
    LocaleManifestError,
    TranslationCatalog,
    TranslationCatalogError,
    load_yaml_strict,
    message_parity_issues,
    message_table_digest,
    normalize_locale_tag,
    parse_translation_catalog,
)


class ExtractionError(ValueError):
    """Source YAML cannot produce a unique, trustworthy message table."""


@dataclass(frozen=True)
class ExtractionReport:
    messages: dict[str, str]
    unstable_lines: tuple[str, ...]


_TEXT_FIELDS = ("title", "subtitle", "badge", "image_alt", "action")
_LOC_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")


def _scene_list(data: Any) -> list[dict[str, Any]]:
    """Normalize a scenes YAML doc (list or ``{scenes: [...]}``)."""

    if isinstance(data, dict):
        data = data.get("scenes", [])
    if data is None:
        return []
    if not isinstance(data, list):
        raise ExtractionError("scene YAML must be a list or a {scenes: [...]} mapping")
    if any(not isinstance(scene, dict) for scene in data):
        raise ExtractionError("every scene entry must be a mapping")
    return list(data)


def _add_message(
    table: dict[str, str],
    owners: dict[str, str],
    *,
    key: str,
    value: object,
    owner: str,
    optional: bool = False,
) -> None:
    if value is None and optional:
        return
    if not isinstance(value, str):
        raise ExtractionError(f"{owner} must be a string")
    if not value.strip():
        if optional:
            return
        raise ExtractionError(f"{owner} must not be empty")
    if key in table:
        raise ExtractionError(
            f"duplicate localization key {key!r}: {owners[key]} and {owner}"
        )
    table[key] = value.rstrip("\n")
    owners[key] = owner


def _line_selector(
    line: object,
    *,
    index: int,
    owner: str,
) -> tuple[str, bool]:
    if not isinstance(line, dict) or "loc_id" not in line:
        return str(index), False
    loc_id = line.get("loc_id")
    if not isinstance(loc_id, str) or _LOC_ID_RE.fullmatch(loc_id) is None:
        raise ExtractionError(
            f"{owner}.loc_id must match [A-Za-z0-9][A-Za-z0-9_-]{{0,63}}"
        )
    return loc_id, True


def _extract_card(
    card: object,
    *,
    prefix: str,
    owner: str,
    table: dict[str, str],
    owners: dict[str, str],
) -> None:
    if card is None:
        return
    if not isinstance(card, dict):
        raise ExtractionError(f"{owner} must be a mapping")
    for field in _TEXT_FIELDS:
        if field in card:
            _add_message(
                table,
                owners,
                key=f"{prefix}.card.{field}",
                value=card[field],
                owner=f"{owner}.{field}",
                optional=field != "title",
            )

    details = card.get("details", []) or []
    if not isinstance(details, list):
        raise ExtractionError(f"{owner}.details must be a list")
    for index, detail in enumerate(details):
        if not isinstance(detail, dict):
            raise ExtractionError(f"{owner}.details[{index}] must be a mapping")
        for field in ("label", "text"):
            _add_message(
                table,
                owners,
                key=f"{prefix}.card.detail.{index}.{field}",
                value=detail.get(field),
                owner=f"{owner}.details[{index}].{field}",
            )

    tags = card.get("tags", []) or []
    if not isinstance(tags, list):
        raise ExtractionError(f"{owner}.tags must be a list")
    for index, tag in enumerate(tags):
        _add_message(
            table,
            owners,
            key=f"{prefix}.card.tag.{index}",
            value=tag,
            owner=f"{owner}.tags[{index}]",
        )


def extract_pack_report(pack_root: Path) -> ExtractionReport:
    """Extract messages plus the legacy lines that still use index keys."""

    root = Path(pack_root)
    scenes_dir = root / "content" / "scenes"
    if not scenes_dir.is_dir():
        scenes_dir = root / "scenes"  # tolerate a content-less layout
    table: dict[str, str] = {}
    owners: dict[str, str] = {}
    unstable: list[str] = []
    scene_ids: dict[str, str] = {}

    # Runtime resolves these metadata fields through Localization.story().
    # They must therefore participate in the same digest/coverage authority as
    # scene copy instead of being treated as arbitrary target-only extras.
    content_root = root / "content" if (root / "content").is_dir() else root
    meta_path = content_root / "meta.yaml"
    meta: dict[str, Any] = {}
    if meta_path.is_file():
        try:
            raw_meta = load_yaml_strict(meta_path)
        except TranslationCatalogError as exc:
            raise ExtractionError(str(exc)) from exc
        if raw_meta is not None and not isinstance(raw_meta, dict):
            raise ExtractionError(f"{meta_path} must be a mapping")
        meta = raw_meta or {}
    for field in ("title", "subtitle"):
        if field in meta:
            _add_message(
                table,
                owners,
                key=f"meta.{field}",
                value=meta[field],
                owner=f"{meta_path}:{field}",
                optional=True,
            )
    title_screen = meta.get("title_screen")
    if title_screen is not None:
        if not isinstance(title_screen, dict):
            raise ExtractionError(f"{meta_path}:title_screen must be a mapping")
        for field in ("tagline", "footer_text"):
            if field in title_screen:
                _add_message(
                    table,
                    owners,
                    key=f"meta.title_screen.{field}",
                    value=title_screen[field],
                    owner=f"{meta_path}:title_screen.{field}",
                    optional=True,
                )

    # Static Music Room copy is rendered through Localization.story() just
    # like scene text. A pack-supplied loc_id is the preferred durable key;
    # older manifests receive a deterministic asset-digest key.
    music_room_path = content_root / "music_room.yaml"
    if music_room_path.is_file():
        from world_gal_game.core.music_room_spec import (
            MusicRoomManifest,
            music_room_localization_id,
        )

        try:
            raw_music_room = load_yaml_strict(music_room_path)
            if raw_music_room is None:
                music_manifest = MusicRoomManifest()
            elif (
                isinstance(raw_music_room, dict)
                and "entries" in raw_music_room
            ):
                music_manifest = MusicRoomManifest.model_validate(
                    raw_music_room,
                )
            else:
                music_manifest = MusicRoomManifest.from_items(raw_music_room)
        except (TranslationCatalogError, TypeError, ValueError) as exc:
            raise ExtractionError(
                f"{music_room_path}: invalid Music Room manifest: {exc}"
            ) from exc
        for index, entry in enumerate(music_manifest.entries):
            selector = music_room_localization_id(entry)
            prefix = f"music_room.{selector}"
            owner = f"{music_room_path}:entries[{index}]"
            for field in ("title", "artist", "usage"):
                _add_message(
                    table,
                    owners,
                    key=f"{prefix}.{field}",
                    value=getattr(entry, field),
                    owner=f"{owner}.{field}",
                    optional=field != "title",
                )

    # System UI has one canonical source inventory. Pack legacy overrides alter
    # that source copy, while new target catalogs use the namespaced `ui.<key>`
    # convention consumed by Localization.t().
    source_ui = dict(DEFAULT_UI)
    raw_locale = meta.get("locale")
    if raw_locale is not None and not isinstance(raw_locale, dict):
        raise ExtractionError(f"{meta_path}:locale must be a mapping")
    raw_ui = raw_locale.get("ui") if isinstance(raw_locale, dict) else None
    if raw_ui is not None:
        if not isinstance(raw_ui, dict):
            raise ExtractionError(f"{meta_path}:locale.ui must be a mapping")
        for key, value in raw_ui.items():
            if not isinstance(key, str) or not key or key != key.strip():
                raise ExtractionError(
                    f"{meta_path}:locale.ui contains an invalid key"
                )
            source_ui[key] = value
    for key, value in source_ui.items():
        _add_message(
            table,
            owners,
            key=f"ui.{key}",
            value=value,
            owner=f"{meta_path}:locale.ui.{key}",
            optional=True,
        )

    source_time = dict(DEFAULT_TIME_OF_DAY)
    source_days = dict(DEFAULT_DAY_OF_WEEK)
    if isinstance(raw_locale, dict):
        for block_name, destination in (
            ("time_of_day", source_time),
            ("day_of_week", source_days),
        ):
            block = raw_locale.get(block_name)
            if block is not None:
                if not isinstance(block, dict):
                    raise ExtractionError(
                        f"{meta_path}:locale.{block_name} must be a mapping"
                    )
                destination.update(block)
    for key, value in source_time.items():
        _add_message(
            table,
            owners,
            key=f"ui.time_of_day.{key}",
            value=value,
            owner=f"{meta_path}:locale.time_of_day.{key}",
        )
    for key, value in source_days.items():
        _add_message(
            table,
            owners,
            key=f"ui.day_of_week.{key}",
            value=value,
            owner=f"{meta_path}:locale.day_of_week.{key}",
        )

    raw_levels = (
        raw_locale.get("affection_levels")
        if isinstance(raw_locale, dict)
        else None
    )
    levels = raw_levels if raw_levels else DEFAULT_AFFECTION_LEVELS
    if not isinstance(levels, list):
        raise ExtractionError(f"{meta_path}:locale.affection_levels must be a list")
    for index, level in enumerate(levels):
        if not isinstance(level, dict) or set(level) - {"min", "label"}:
            raise ExtractionError(
                f"{meta_path}:locale.affection_levels[{index}] is invalid"
            )
        minimum = level.get("min")
        if type(minimum) not in (int, float):
            raise ExtractionError(
                f"{meta_path}:locale.affection_levels[{index}].min is invalid"
            )
        _add_message(
            table,
            owners,
            key=f"ui.affection_level.{minimum}",
            value=level.get("label"),
            owner=f"{meta_path}:locale.affection_levels[{index}].label",
        )

    for path in sorted(scenes_dir.rglob("*.yaml")):
        try:
            data = load_yaml_strict(path)
        except TranslationCatalogError as exc:
            raise ExtractionError(str(exc)) from exc
        for scene_index, scene in enumerate(_scene_list(data)):
            owner = f"{path}:scenes[{scene_index}]"
            sid = scene.get("id")
            if not isinstance(sid, str) or not sid.strip() or sid != sid.strip():
                raise ExtractionError(f"{owner}.id must be a non-empty string")
            if sid in scene_ids:
                raise ExtractionError(
                    f"duplicate scene id {sid!r}: {scene_ids[sid]} and {owner}"
                )
            scene_ids[sid] = owner
            title = scene.get("title")
            if title is not None:
                _add_message(
                    table,
                    owners,
                    key=f"{sid}.title",
                    value=title,
                    owner=f"{owner}.title",
                    optional=True,
                )

            lines = scene.get("lines", []) or []
            if not isinstance(lines, list):
                raise ExtractionError(f"{owner}.lines must be a list")
            for line_index, line in enumerate(lines):
                line_owner = f"{owner}.lines[{line_index}]"
                selector, stable = _line_selector(
                    line,
                    index=line_index,
                    owner=line_owner,
                )
                prefix = f"{sid}.line.{selector}"
                if not stable:
                    unstable.append(prefix)
                if isinstance(line, str):
                    _add_message(
                        table,
                        owners,
                        key=prefix,
                        value=line,
                        owner=line_owner,
                    )
                    continue
                if not isinstance(line, dict):
                    raise ExtractionError(f"{line_owner} must be a string or mapping")
                _add_message(
                    table,
                    owners,
                    key=prefix,
                    value=line.get("text"),
                    owner=f"{line_owner}.text",
                )
                if "speaker" in line:
                    _add_message(
                        table,
                        owners,
                        key=f"{prefix}.speaker",
                        value=line.get("speaker"),
                        owner=f"{line_owner}.speaker",
                        optional=True,
                    )
                captions = line.get("sound_captions", []) or []
                if not isinstance(captions, list):
                    raise ExtractionError(f"{line_owner}.sound_captions must be a list")
                for caption_index, caption in enumerate(captions):
                    caption_owner = (
                        f"{line_owner}.sound_captions[{caption_index}]"
                    )
                    if not isinstance(caption, dict):
                        raise ExtractionError(f"{caption_owner} must be a mapping")
                    _add_message(
                        table,
                        owners,
                        key=f"{prefix}.sound_caption.{caption_index}",
                        value=caption.get("text"),
                        owner=f"{caption_owner}.text",
                    )

            choices = scene.get("choices", []) or []
            if not isinstance(choices, list):
                raise ExtractionError(f"{owner}.choices must be a list")
            for choice_index, choice in enumerate(choices):
                choice_owner = f"{owner}.choices[{choice_index}]"
                if not isinstance(choice, dict):
                    raise ExtractionError(f"{choice_owner} must be a mapping")
                cid = choice.get("id")
                if not isinstance(cid, str) or not cid.strip() or cid != cid.strip():
                    raise ExtractionError(f"{choice_owner}.id must be a non-empty string")
                prefix = f"{sid}.choice.{cid}"
                _add_message(
                    table,
                    owners,
                    key=prefix,
                    value=choice.get("text"),
                    owner=f"{choice_owner}.text",
                )
                _extract_card(
                    choice.get("card"),
                    prefix=prefix,
                    owner=f"{choice_owner}.card",
                    table=table,
                    owners=owners,
                )

    if len(table) > MAX_CATALOG_MESSAGES:
        raise ExtractionError(
            f"source table exceeds {MAX_CATALOG_MESSAGES} messages"
        )
    return ExtractionReport(table, tuple(unstable))


def extract_pack(pack_root: Path) -> dict[str, str]:
    """Return an ordered source ``{message_id: copy}`` table."""

    return extract_pack_report(pack_root).messages


def _dump_yaml(table: dict[str, str]) -> str:
    """Legacy flat-table formatter retained for stdout/API compatibility."""

    return yaml.safe_dump(
        table,
        allow_unicode=True,
        sort_keys=False,
        default_flow_style=False,
        width=1000,
    )


def _catalog_document(
    *,
    language: str,
    source_language: str,
    source_messages: Mapping[str, str],
    messages: Mapping[str, str],
) -> dict[str, Any]:
    return {
        "format_version": 1,
        "language": language,
        "source_language": source_language,
        "source_digest": message_table_digest(source_messages),
        "messages": dict(messages),
    }


def _dump_catalog(document: Mapping[str, Any]) -> str:
    return yaml.safe_dump(
        dict(document),
        allow_unicode=True,
        sort_keys=False,
        default_flow_style=False,
        width=1000,
    )


def _legacy_messages(data: Any, *, origin: Path) -> dict[str, str]:
    if not isinstance(data, dict):
        raise TranslationCatalogError(f"{origin} must be a mapping")
    if len(data) > MAX_CATALOG_MESSAGES:
        raise TranslationCatalogError(
            f"{origin} exceeds {MAX_CATALOG_MESSAGES} entries"
        )
    messages: dict[str, str] = {}
    for key, value in data.items():
        if not isinstance(key, str) or not isinstance(value, str):
            raise TranslationCatalogError(
                f"legacy table {origin} requires string keys and values"
            )
        messages[key] = value
    return messages


def _load_check_target(path: Path) -> tuple[dict[str, str], TranslationCatalog | None]:
    data = load_yaml_strict(path)
    envelope_markers = {
        "format_version", "language", "source_language", "source_digest", "messages"
    }
    if isinstance(data, dict) and envelope_markers & set(data):
        catalog = parse_translation_catalog(data, origin=str(path))
        return dict(catalog.messages), catalog
    return _legacy_messages(data or {}, origin=path), None


def _declared_policy(
    pack_root: Path,
    language: str,
) -> tuple[str | None, tuple[str, ...] | None]:
    meta_path = Path(pack_root) / "content" / "meta.yaml"
    if not meta_path.is_file():
        meta_path = Path(pack_root) / "meta.yaml"
    if not meta_path.is_file():
        return None, None
    try:
        meta = load_yaml_strict(meta_path)
    except TranslationCatalogError:
        return None, None
    if not isinstance(meta, dict) or not isinstance(meta.get("locale"), dict):
        return None, None
    languages = meta["locale"].get("languages")
    if not isinstance(languages, dict):
        return None, None
    for raw_id, raw_entry in languages.items():
        try:
            locale_id = normalize_locale_tag(raw_id)
        except LocaleManifestError:
            continue
        if locale_id != language or not isinstance(raw_entry, dict):
            continue
        status = raw_entry.get("status")
        if status not in ("complete", "preview"):
            status = None
        raw_scenes = raw_entry.get("story_scenes")
        if raw_scenes in ("*", "all"):
            scenes = None
        elif isinstance(raw_scenes, list) and all(
            isinstance(value, str) and value for value in raw_scenes
        ):
            scenes = tuple(raw_scenes)
        else:
            scenes = None
        return status, scenes
    return None, None


def _keys_for_scenes(
    messages: Mapping[str, str],
    scene_ids: tuple[str, ...] | None,
) -> set[str]:
    if scene_ids is None:
        return set(messages)
    return {
        key
        for key in messages
        if key.startswith(("ui.", "meta.", "music_room."))
        or any(key == scene_id or key.startswith(f"{scene_id}.") for scene_id in scene_ids)
    }


def _safe_output_language(value: object, *, option: str) -> str:
    try:
        return normalize_locale_tag(value)
    except LocaleManifestError as exc:
        raise ExtractionError(f"{option}: {exc}") from exc


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("pack", help="path to the pack directory")
    parser.add_argument(
        "--src-lang",
        default="src",
        help="source language tag stored in generated catalogs (default: src)",
    )
    parser.add_argument(
        "--out",
        action="store_true",
        help="write <pack>/content/i18n/messages.<src-lang>.yaml",
    )
    parser.add_argument(
        "--template",
        metavar="LANG",
        help="write an empty <pack>/content/i18n/<LANG>.yaml catalog",
    )
    parser.add_argument(
        "--check",
        metavar="FILE",
        help="check a v1 catalog (or legacy flat table) against source",
    )
    parser.add_argument(
        "--language",
        help="target locale for a legacy flat --check file",
    )
    parser.add_argument(
        "--status",
        choices=("complete", "preview"),
        help="override the locale manifest status during --check",
    )
    parser.add_argument(
        "--require-stable-line-ids",
        action="store_true",
        help="fail when any source line relies on its numeric index key",
    )
    args = parser.parse_args(argv)

    pack_root = Path(args.pack)
    if not pack_root.is_dir():
        print(f"[error] pack directory not found: {pack_root}", file=sys.stderr)
        return 1
    try:
        source_language = _safe_output_language(args.src_lang, option="--src-lang")
        report = extract_pack_report(pack_root)
    except (ExtractionError, TranslationCatalogError) as exc:
        print(f"[error] {exc}", file=sys.stderr)
        return 1
    table = report.messages

    if args.check:
        check_path = Path(args.check)
        try:
            translated, catalog = _load_check_target(check_path)
        except (OSError, TranslationCatalogError, LocaleManifestError) as exc:
            print(f"[error] cannot validate {check_path}: {exc}", file=sys.stderr)
            return 1

        target_language = ""
        is_versioned = catalog is not None
        if catalog is not None:
            target_language = catalog.language
            if catalog.source_language != source_language:
                print(
                    f"[error] source_language is {catalog.source_language!r}; "
                    f"expected {source_language!r}",
                    file=sys.stderr,
                )
                return 1
            expected_digest = message_table_digest(table)
            if catalog.source_digest != expected_digest:
                print(
                    "[error] source_digest does not match current extracted source",
                    file=sys.stderr,
                )
                return 1
        elif args.language:
            try:
                target_language = _safe_output_language(
                    args.language,
                    option="--language",
                )
            except ExtractionError as exc:
                print(f"[error] {exc}", file=sys.stderr)
                return 1

        declared_status: str | None = None
        declared_scenes: tuple[str, ...] | None = None
        if target_language:
            declared_status, declared_scenes = _declared_policy(
                pack_root,
                target_language,
            )
        status = args.status or declared_status
        required_keys = _keys_for_scenes(
            table,
            declared_scenes if status == "preview" else None,
        )
        source_keys, translated_keys = set(table), set(translated)
        missing = sorted(required_keys - translated_keys)
        extra = sorted(translated_keys - source_keys)
        empty = sorted(
            key
            for key in required_keys & translated_keys
            if not translated[key].strip()
        )
        parity: dict[str, tuple[str, ...]] = {}
        if is_versioned:
            for key in sorted(required_keys & translated_keys):
                value = translated[key]
                if value.strip():
                    issues = message_parity_issues(table[key], value)
                    if issues:
                        parity[key] = issues

        covered = len(required_keys) - len(missing) - len(empty) - len(parity)
        pct = (covered / len(required_keys) * 100) if required_keys else 100.0
        print(f"coverage: {covered}/{len(required_keys)} ({pct:.0f}%)")
        for key in missing:
            print(f"  missing: {key}")
        for key in empty:
            print(f"  untranslated: {key}")
        for key in extra:
            print(f"  extra (not in source): {key}")
        for key, issues in parity.items():
            print(f"  unsafe: {key}: {'; '.join(issues)}")

        # loc_id is deliberately additive: complete catalogs still work with
        # legacy index keys unless CI explicitly opts into the stronger churn
        # guard. This lets a production checker require stability without
        # making a pack-format migration mandatory for every existing line.
        require_stable = args.require_stable_line_ids
        if require_stable:
            for key in report.unstable_lines:
                print(f"  unstable line id: {key}")

        # Preview catalogs may intentionally omit untranslated scenes; all
        # other structural defects still fail. Legacy/no-status behaviour keeps
        # the historical full-coverage requirement.
        missing_fails = status != "preview"
        failed = bool(
            (missing and missing_fails)
            or empty
            or extra
            or parity
            or (require_stable and report.unstable_lines)
        )
        return 1 if failed else 0

    try:
        if args.template:
            target_language = _safe_output_language(
                args.template,
                option="--template",
            )
            out_path = (
                pack_root / "content" / "i18n" / f"{target_language}.yaml"
            )
            out_path.parent.mkdir(parents=True, exist_ok=True)
            document = _catalog_document(
                language=target_language,
                source_language=source_language,
                source_messages=table,
                messages={key: "" for key in table},
            )
            out_path.write_text(_dump_catalog(document), encoding="utf-8")
            print(f"wrote {out_path} ({len(table)} keys)")
            return 0

        if args.out:
            out_path = (
                pack_root
                / "content"
                / "i18n"
                / f"messages.{source_language}.yaml"
            )
            out_path.parent.mkdir(parents=True, exist_ok=True)
            document = _catalog_document(
                language=source_language,
                source_language=source_language,
                source_messages=table,
                messages=table,
            )
            out_path.write_text(_dump_catalog(document), encoding="utf-8")
            print(f"wrote {out_path} ({len(table)} keys)")
            return 0
    except (ExtractionError, OSError) as exc:
        print(f"[error] {exc}", file=sys.stderr)
        return 1

    sys.stdout.write(_dump_yaml(table))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

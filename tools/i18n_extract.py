#!/usr/bin/env python3
"""Extract translatable strings from a pack's scenes into a message table.

Walks ``<pack>/content/scenes/**/*.yaml`` and pulls every translatable string —
scene titles, dialogue line text, and choice text — keyed by a stable id
(``<scene_id>.title`` / ``<scene_id>.line.<index>`` / ``<scene_id>.choice.<id>``).
This is the first half of an i18n workflow: ship the table to translators, then
keep their files honest with ``--check``.

Usage::

    # Print the source message table (id -> source string) as YAML.
    uv run python tools/i18n_extract.py games/demo_pack

    # Write it to <pack>/i18n/messages.<srclang>.yaml.
    uv run python tools/i18n_extract.py games/demo_pack --out --src-lang zh

    # Emit an empty template for a target language (id -> "").
    uv run python tools/i18n_extract.py games/demo_pack --template ja

    # Coverage check: which keys a translation file is missing / has extra.
    uv run python tools/i18n_extract.py games/demo_pack --check games/demo_pack/i18n/ja.yaml

Note: this tool produces/validates the tables. Applying them at runtime (a
translation lookup keyed by these ids) is a separate, still-open engine feature.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

import yaml


def _scene_list(data: Any) -> list[dict]:
    """Normalize a scenes YAML doc (list, or {scenes: [...]}) to a scene list."""
    if isinstance(data, dict):
        data = data.get("scenes", [])
    return [s for s in (data or []) if isinstance(s, dict)]


def _line_text(line: Any) -> str | None:
    """Translatable text of a line (dict with ``text``, or a bare string)."""
    if isinstance(line, str):
        return line
    if isinstance(line, dict) and isinstance(line.get("text"), str):
        return line["text"]
    return None


def extract_pack(pack_root: Path) -> dict[str, str]:
    """Return an ordered ``{key: source_string}`` table for the pack."""
    root = Path(pack_root)
    scenes_dir = root / "content" / "scenes"
    if not scenes_dir.is_dir():
        scenes_dir = root / "scenes"  # tolerate content-less layout
    table: dict[str, str] = {}
    for path in sorted(scenes_dir.rglob("*.yaml")):
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
        except yaml.YAMLError:
            continue
        for scene in _scene_list(data):
            sid = scene.get("id")
            if not sid:
                continue
            title = scene.get("title")
            if isinstance(title, str) and title.strip():
                table[f"{sid}.title"] = title.rstrip("\n")
            for i, line in enumerate(scene.get("lines", []) or []):
                text = _line_text(line)
                if text and text.strip():
                    table[f"{sid}.line.{i}"] = text.rstrip("\n")
            for choice in scene.get("choices", []) or []:
                if isinstance(choice, dict) and isinstance(choice.get("text"), str):
                    cid = choice.get("id", "")
                    table[f"{sid}.choice.{cid}"] = choice["text"].rstrip("\n")
    return table


def _dump_yaml(table: dict[str, str]) -> str:
    return yaml.safe_dump(table, allow_unicode=True, sort_keys=False,
                          default_flow_style=False, width=1000)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("pack", help="path to the pack directory")
    p.add_argument("--src-lang", default="src",
                   help="source language tag for --out filename (default: src)")
    p.add_argument("--out", action="store_true",
                   help="write <pack>/i18n/messages.<src-lang>.yaml")
    p.add_argument("--template", metavar="LANG",
                   help="write an empty <pack>/i18n/<LANG>.yaml template")
    p.add_argument("--check", metavar="FILE",
                   help="report keys a translation FILE is missing / has extra")
    args = p.parse_args(argv)

    pack_root = Path(args.pack)
    if not pack_root.is_dir():
        print(f"[error] pack directory not found: {pack_root}", file=sys.stderr)
        return 1
    table = extract_pack(pack_root)

    if args.check:
        try:
            trans = yaml.safe_load(Path(args.check).read_text(encoding="utf-8")) or {}
        except (OSError, yaml.YAMLError) as exc:
            print(f"[error] cannot read {args.check}: {exc}", file=sys.stderr)
            return 1
        src_keys, tr_keys = set(table), set(trans)
        missing = sorted(src_keys - tr_keys)
        extra = sorted(tr_keys - src_keys)
        empty = sorted(k for k in src_keys & tr_keys if not str(trans.get(k, "")).strip())
        covered = len(src_keys) - len(missing) - len(empty)
        pct = (covered / len(src_keys) * 100) if src_keys else 100.0
        print(f"coverage: {covered}/{len(src_keys)} ({pct:.0f}%)")
        for k in missing:
            print(f"  missing: {k}")
        for k in empty:
            print(f"  untranslated: {k}")
        for k in extra:
            print(f"  extra (not in source): {k}")
        return 1 if (missing or empty) else 0

    if args.template:
        out_path = pack_root / "i18n" / f"{args.template}.yaml"
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(_dump_yaml({k: "" for k in table}), encoding="utf-8")
        print(f"wrote {out_path} ({len(table)} keys)")
        return 0

    if args.out:
        out_path = pack_root / "i18n" / f"messages.{args.src_lang}.yaml"
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(_dump_yaml(table), encoding="utf-8")
        print(f"wrote {out_path} ({len(table)} keys)")
        return 0

    sys.stdout.write(_dump_yaml(table))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

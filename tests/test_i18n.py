"""i18n translation-extraction tool (tools/i18n_extract.py)."""
from __future__ import annotations

import pathlib
import sys

import yaml

import world_gal_game

_REPO = pathlib.Path(world_gal_game.__file__).parent.parent
sys.path.insert(0, str(_REPO / "tools"))
import i18n_extract  # noqa: E402

DEMO = _REPO / "games" / "demo_pack"


def test_extract_pulls_titles_lines_choices():
    t = i18n_extract.extract_pack(DEMO)
    assert t, "expected a non-empty message table"
    assert t["ending_lover.title"] == "結局 · 戀人"
    assert any(k.startswith("ending_lover.line.") for k in t)


def test_extract_is_deterministic_with_unique_keys():
    t1 = i18n_extract.extract_pack(DEMO)
    t2 = i18n_extract.extract_pack(DEMO)
    assert list(t1) == list(t2)            # stable order
    assert len(t1) == len(set(t1))         # unique keys


def test_check_flags_untranslated(tmp_path):
    table = i18n_extract.extract_pack(DEMO)
    f = tmp_path / "empty.yaml"
    f.write_text(yaml.safe_dump({k: "" for k in table}, allow_unicode=True),
                 encoding="utf-8")
    assert i18n_extract.main([str(DEMO), "--check", str(f)]) == 1


def test_check_passes_for_full_translation(tmp_path):
    table = i18n_extract.extract_pack(DEMO)
    f = tmp_path / "full.yaml"
    f.write_text(yaml.safe_dump({k: "X" for k in table}, allow_unicode=True),
                 encoding="utf-8")
    assert i18n_extract.main([str(DEMO), "--check", str(f)]) == 0

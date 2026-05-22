"""Tests for world_gal_game.build_web.stage_web_build.

These exercise the staging logic only — they assert on the staged directory
contents and NEVER invoke pygbag (which isn't installed in CI). A tiny pack
is written under tmp_path, staged, and the layout + templated main.py are
verified.
"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from world_gal_game.build_web import stage_web_build, _render_web_main


def _write(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        yaml.dump(data, fh, allow_unicode=True, default_flow_style=False)


@pytest.fixture
def tiny_pack(tmp_path: Path) -> Path:
    """A minimal but valid pack with a content/ and an assets/ tree."""
    root = tmp_path / "my_web_pack"
    _write(root / "content" / "meta.yaml", {
        "title": "Web Demo",
        "pack_format_version": "0.1",
        "bundled_font": "assets/fonts/font.ttf",
        "start_location": "loc_a",
    })
    _write(root / "content" / "locations.yaml", {"locations": [
        {"id": "loc_a", "name": "A", "exits": []},
    ]})
    # An asset file to confirm assets/ is mirrored.
    afont = root / "assets" / "fonts" / "font.ttf"
    afont.parent.mkdir(parents=True, exist_ok=True)
    afont.write_bytes(b"FAKEFONT")
    return root


def test_render_web_main_substitutes_pack_name() -> None:
    src = _render_web_main("cool_pack")
    assert '_PACK = "cool_pack"' in src or "_PACK = 'cool_pack'" in src
    # Relative imports rewritten to absolute for a top-level main.py.
    assert "from world_gal_game.app import" in src
    assert "from world_gal_game.config import" in src
    # Sanity: it still defines an async main + the asyncio.run footer.
    assert "async def main" in src
    assert "asyncio.run(main())" in src


def test_stage_copies_engine_pack_and_writes_main(tiny_pack: Path,
                                                   tmp_path: Path) -> None:
    staging = tmp_path / "staging"
    out = stage_web_build(tiny_pack, staging, pack_name="my_web_pack")
    assert out == staging.resolve()

    # 1) engine package copied, mirroring source layout.
    assert (staging / "world_gal_game" / "__init__.py").exists()
    assert (staging / "world_gal_game" / "app.py").exists()
    # web_main template travels with the engine too.
    assert (staging / "world_gal_game" / "web_main.py").exists()

    # 2) pack content + assets land under games/<name>/ so resource_root
    #    math (root = parent of world_gal_game) resolves the pack.
    assert (staging / "games" / "my_web_pack" / "content" / "meta.yaml").exists()
    assert (staging / "games" / "my_web_pack" / "assets" / "fonts"
            / "font.ttf").exists()

    # 3) templated entry point at the staging root names the pack.
    main_py = (staging / "main.py").read_text(encoding="utf-8")
    assert "my_web_pack" in main_py
    assert "async def main" in main_py


def test_stage_defaults_pack_name_to_dir_name(tiny_pack: Path,
                                               tmp_path: Path) -> None:
    staging = tmp_path / "staging2"
    stage_web_build(tiny_pack, staging)  # no explicit pack_name
    assert (staging / "games" / "my_web_pack" / "content" / "meta.yaml").exists()
    main_py = (staging / "main.py").read_text(encoding="utf-8")
    assert "my_web_pack" in main_py


def test_stage_no_pycache_in_engine_copy(tiny_pack: Path, tmp_path: Path) -> None:
    staging = tmp_path / "staging3"
    stage_web_build(tiny_pack, staging, pack_name="my_web_pack")
    # __pycache__ dirs must be excluded by the ignore patterns.
    pycaches = list((staging / "world_gal_game").rglob("__pycache__"))
    assert pycaches == []


def test_stage_missing_meta_raises(tmp_path: Path) -> None:
    empty = tmp_path / "not_a_pack"
    empty.mkdir()
    with pytest.raises(FileNotFoundError):
        stage_web_build(empty, tmp_path / "s")


def test_stage_repeat_is_clean(tiny_pack: Path, tmp_path: Path) -> None:
    """Re-staging into the same dir doesn't error and re-mirrors cleanly."""
    staging = tmp_path / "staging4"
    stage_web_build(tiny_pack, staging, pack_name="my_web_pack")
    # Plant a stale file under the engine dst; a re-stage should remove it.
    stale = staging / "world_gal_game" / "STALE_marker.txt"
    stale.write_text("x", encoding="utf-8")
    stage_web_build(tiny_pack, staging, pack_name="my_web_pack")
    assert not stale.exists()

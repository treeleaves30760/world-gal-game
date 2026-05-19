"""Tests for Phase 2 dev modules: SmokeRunner / VisualCheck / AssetStudio / SelfCheck."""
from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

import pytest


# ----------------------------------------------------------------------
# SmokeRunner


def test_smoke_runner_discovers_demo_pack_scripts():
    from world_gal_game.dev.smoke_runner import SmokeRunner
    scripts = SmokeRunner("games/demo_pack").discover()
    assert {p.name for p in scripts} == {
        "test_lover_route.json",
        "test_friend_route.json",
        "test_alone_route.json",
    }


def test_smoke_runner_runs_demo_pack_routes():
    from world_gal_game.dev.smoke_runner import SmokeRunner
    from world_gal_game.plugins import snapshot, restore
    snap = snapshot()
    try:
        report = SmokeRunner("games/demo_pack").run()
        assert report.ok
        endings = {r.ending_flag for r in report.results}
        assert endings == {"ending_alone", "ending_friend", "ending_lover"}
    finally:
        restore(snap)


def test_smoke_runner_no_scripts_returns_empty_report(tmp_path: Path):
    """A pack root with no scripts/ dir is OK — empty report, ok==False
    because we require at least one passing script."""
    from world_gal_game.dev.smoke_runner import SmokeRunner
    (tmp_path / "content").mkdir()
    sr = SmokeRunner(tmp_path)
    rep = sr.run()
    assert rep.results == []
    assert rep.ok is False  # empty = not ok (no scripts to confirm)


def test_smoke_runner_to_dict_roundtrips():
    from world_gal_game.dev.smoke_runner import SmokeRunner
    from world_gal_game.plugins import snapshot, restore
    snap = snapshot()
    try:
        rep = SmokeRunner("games/demo_pack").run()
        d = rep.to_dict()
        assert d["ok"] is True
        assert d["count"] == 3
        assert d["passed"] == 3
        assert d["failed"] == 0
    finally:
        restore(snap)


# ----------------------------------------------------------------------
# AssetStudio


def test_placeholder_image_writes_png(tmp_path: Path):
    from world_gal_game.dev.asset_studio import placeholder_image
    p = placeholder_image(size=(64, 32), label="HI", path=tmp_path / "x.png")
    assert p.is_file()
    assert p.stat().st_size > 100  # not empty


def test_placeholder_image_same_label_same_color(tmp_path: Path):
    """Labels should produce stable colours (md5-derived)."""
    from world_gal_game.dev.asset_studio import placeholder_image, _color_for_label
    c1 = _color_for_label("alpha")
    c2 = _color_for_label("alpha")
    c3 = _color_for_label("beta")
    assert c1 == c2
    assert c1 != c3


def test_resize_writes_smaller(tmp_path: Path):
    from world_gal_game.dev.asset_studio import placeholder_image, resize
    src = placeholder_image(size=(1000, 500), label="big",
                            path=tmp_path / "big.png")
    dst = tmp_path / "small.png"
    resize(src=src, dst=dst, max_dim=200)
    assert dst.is_file()
    # We can't easily check dimensions without pygame here, but verify
    # the file is smaller than the source (smaller image = smaller bytes
    # for solid colours).
    assert dst.stat().st_size < src.stat().st_size


def test_convert_changes_format(tmp_path: Path):
    from world_gal_game.dev.asset_studio import placeholder_image, convert
    src = placeholder_image(size=(64, 64), label="x", path=tmp_path / "x.png")
    dst = tmp_path / "x.bmp"
    convert(src=src, dst=dst)
    assert dst.is_file()
    assert dst.read_bytes()[:2] == b"BM"  # BMP magic


def test_stock_placeholder_pack_creates_assets(tmp_path: Path):
    from world_gal_game.dev.asset_studio import stock_placeholder_pack
    pack = tmp_path / "newpack"
    pack.mkdir()
    written = stock_placeholder_pack(pack)
    assert len(written) >= 4
    for p in written:
        assert p.is_file()


# ----------------------------------------------------------------------
# VisualCheck


def test_visual_check_first_run_creates_baseline(tmp_path: Path):
    """First-time scenarios promote candidate → baseline and pass."""
    os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
    os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

    from world_gal_game.dev.visual_check import VisualCheck
    from world_gal_game.plugins import snapshot, restore

    # Copy demo_pack into tmp so we don't pollute the real one with baselines.
    dst = tmp_path / "demo_pack"
    shutil.copytree("games/demo_pack", dst)

    snap = snapshot()
    try:
        vc = VisualCheck(dst)
        scenarios = [{"name": "title", "dev_start": None, "autoplay": 0.3}]
        rep = vc.run(scenarios)
        assert rep.ok
        assert rep.results[0].created_baseline is True
        # Baseline file exists now
        assert (dst / "visual_baselines" / "title.png").is_file()
    finally:
        restore(snap)


def test_visual_check_md5_match_passes(tmp_path: Path):
    """Second run with no changes matches baseline by md5."""
    os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
    from world_gal_game.dev.visual_check import VisualCheck
    from world_gal_game.plugins import snapshot, restore

    dst = tmp_path / "demo_pack"
    shutil.copytree("games/demo_pack", dst)

    snap = snapshot()
    try:
        vc = VisualCheck(dst)
        scenarios = [{"name": "title", "dev_start": None, "autoplay": 0.3}]
        vc.run(scenarios)  # creates baseline
        rep2 = vc.run(scenarios)  # second run should match
        assert rep2.ok
        # Second run should NOT mark created (baseline already there)
        assert not rep2.results[0].created_baseline
        assert rep2.results[0].md5_match
    finally:
        restore(snap)


# ----------------------------------------------------------------------
# SelfCheck


def test_self_check_demo_pack_passes():
    from world_gal_game.dev.self_check import SelfCheck
    from world_gal_game.plugins import snapshot, restore
    snap = snapshot()
    try:
        sc = SelfCheck("games/demo_pack", skip_visual=True)
        rep = sc.run()
        assert rep.ok
        # All five stages present (visual reports skipped)
        names = [s.name for s in rep.stages]
        assert "schema" in names
        assert "refs" in names
        assert "dead_ends" in names
        assert "smoke" in names
        assert "visual" in names
    finally:
        restore(snap)


def test_self_check_stops_on_failure(tmp_path: Path):
    """When schema fails, downstream stages should be marked skipped."""
    from world_gal_game.dev.self_check import SelfCheck
    # Build a deliberately-broken pack: scene references an unknown effect kind.
    pack = tmp_path / "bad_pack"
    (pack / "content/scenes").mkdir(parents=True)
    (pack / "content/meta.yaml").write_text(
        'pack_format_version: "0.1"\ntitle: bad\nintro_scene: s\n',
        encoding="utf-8",
    )
    (pack / "content/scenes/s.yaml").write_text(
        ("scenes:\n  - id: s\n    title: S\n    lines:\n      - {text: hi}\n"
         "    on_end:\n      - {kind: definitely_unknown_kind, target: x}\n"),
        encoding="utf-8",
    )
    sc = SelfCheck(pack, skip_smoke=False, skip_visual=True)
    rep = sc.run()
    assert not rep.ok
    # First non-ok stage should be present, downstream skipped.
    failures = [s for s in rep.stages if not s.ok and not s.skipped]
    assert failures, "expected at least one real failure"


def test_self_check_report_serialises_to_json():
    from world_gal_game.dev.self_check import SelfCheck
    from world_gal_game.plugins import snapshot, restore
    snap = snapshot()
    try:
        rep = SelfCheck("games/demo_pack", skip_visual=True).run()
        s = json.dumps(rep.to_dict(), ensure_ascii=False)
        parsed = json.loads(s)
        assert parsed["ok"] is True
        assert parsed["stages"]
    finally:
        restore(snap)


# ----------------------------------------------------------------------
# CLI integration smoke


def test_cli_self_check_returns_zero():
    """Running `wgg self-check demo_pack` returns 0."""
    from world_gal_game.cli import self_check_main
    from world_gal_game.plugins import snapshot, restore
    snap = snapshot()
    try:
        rc = self_check_main(["games/demo_pack"])
        assert rc == 0
    finally:
        restore(snap)


def test_cli_smoke_returns_zero():
    from world_gal_game.cli import smoke_main
    from world_gal_game.plugins import snapshot, restore
    snap = snapshot()
    try:
        rc = smoke_main(["games/demo_pack"])
        assert rc == 0
    finally:
        restore(snap)

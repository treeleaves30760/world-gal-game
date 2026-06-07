"""Credits / 鳴謝 overlay + its pack-supplied, data-driven credits sourcing.

Fix 3 (ship-readiness): a themed in-game credits screen, reachable from the
title's extras menu and the in-game menu, whose content comes entirely from the
PACK (``content/credits.yaml`` → ``meta.yaml`` ``credits:`` / ``attribution:``
→ a bundled ``CREDITS.md`` → engine defaults). This satisfies CC-BY / Steam
attribution obligations from data alone, with no game-specific strings in the
engine.

Covers the loader's precedence + normalization (pygame-free) and the scene's
open/describe/close + title/menu wiring (via GameDriver).
"""
import os
from pathlib import Path

import pytest

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

from world_gal_game.core.credits import Credits, load_credits


# ---------------------------------------------------------------------------
# Loader: precedence + normalization (no pygame needed)
# ---------------------------------------------------------------------------

def test_credits_yaml_takes_precedence(tmp_path: Path):
    """A pack's ``content/credits.yaml`` is the first-class source and wins over
    meta fields / markdown."""
    content = tmp_path / "content"
    content.mkdir()
    (content / "credits.yaml").write_text(
        "title: 製作群\n"
        "sections:\n"
        "  - heading: 企劃\n"
        "    lines: [導演 Alice, 編劇 Bob]\n"
        "  - heading: 音樂\n"
        "    lines:\n"
        "      - Music by Kevin MacLeod (CC-BY 4.0)\n",
        encoding="utf-8")
    # Even with competing meta credits, credits.yaml wins.
    cr = load_credits({"credits": "ignored"}, tmp_path)
    assert cr.source == "credits.yaml"
    assert cr.title == "製作群"
    assert [s.heading for s in cr.sections] == ["企劃", "音樂"]
    assert "導演 Alice" in cr.sections[0].body
    assert any("MacLeod" in ln for ln in cr.sections[1].body)


def test_meta_credits_and_attribution(tmp_path: Path):
    """With no credits.yaml, ``meta.credits`` + ``meta.attribution`` are used;
    a bare attribution block gets a sensible default heading."""
    cr = load_credits(
        {"credits": ["Lead: Alice", "Art: Bob"],
         "attribution": "Music by X — Creative Commons By Attribution 4.0"},
        tmp_path)
    assert cr.source == "meta"
    # credits -> one untitled section; attribution -> a headed section.
    headings = [s.heading for s in cr.sections]
    assert None in headings
    assert any(h and "授權" in h for h in headings)
    flat = cr.plain_lines()
    assert "Lead: Alice" in flat
    assert any("Attribution" in ln for ln in flat)


def test_markdown_fallback_finds_bundled_credits(tmp_path: Path):
    """A bundled plain-text ``CREDITS.md`` (incl. the ``assets/bgm`` BGM-
    attribution convention) is parsed into sections when no structured source
    exists. ``#`` lines become section headings."""
    bgm = tmp_path / "assets" / "bgm"
    bgm.mkdir(parents=True)
    (bgm / "CREDITS.md").write_text(
        "# 授權\n"
        "Music by Kevin MacLeod (incompetech.com)\n"
        "Licensed under Creative Commons: By Attribution 4.0\n"
        "\n"
        "# 對應表\n"
        "library_night.ogg <- Myst on the Moor\n",
        encoding="utf-8")
    cr = load_credits({}, tmp_path)
    assert cr.source == "CREDITS.md"
    assert [s.heading for s in cr.sections] == ["授權", "對應表"]
    assert any("MacLeod" in ln for ln in cr.sections[0].body)


def test_engine_default_when_pack_supplies_nothing(tmp_path: Path):
    """A pack with no credits data at all still yields a graceful, non-empty
    engine-default block (never an empty screen)."""
    cr = load_credits({}, tmp_path)        # empty pack dir
    assert cr.source == "engine-default"
    assert not cr.is_empty()
    assert any("World Gal-Game" in ln for ln in cr.plain_lines())


def test_loader_isolates_malformed_credits_yaml(tmp_path: Path):
    """A malformed credits.yaml is skipped (not fatal); resolution falls through
    to the next source (here, engine default)."""
    content = tmp_path / "content"
    content.mkdir()
    (content / "credits.yaml").write_text("::: not : valid : yaml :::\n  - [",
                                          encoding="utf-8")
    cr = load_credits({}, tmp_path)
    assert isinstance(cr, Credits)
    assert cr.source in ("engine-default", "credits.yaml")
    assert not cr.is_empty()


def test_real_tsinghua_pack_credits_carry_attribution():
    """If the sibling Tsing-Hua pack is checked out, its CC-BY BGM attribution
    surfaces in the resolved credits (the real ship-readiness case)."""
    pack = Path(__file__).resolve().parents[2] / "Tsing-Hua-Strange-Tales"
    if not pack.exists():
        pytest.skip("Tsing-Hua pack not checked out")
    cr = load_credits({}, pack)
    assert not cr.is_empty()
    flat = " ".join(cr.plain_lines())
    # The CC-BY composer attribution must be present somewhere in the screen.
    assert "MacLeod" in flat or "Creative Commons" in flat


# ---------------------------------------------------------------------------
# Scene + wiring (via GameDriver)
# ---------------------------------------------------------------------------

@pytest.fixture
def driver():
    from world_gal_game.dev.driver import GameDriver
    d = GameDriver(pack="demo_pack")
    d.app.manager.transitions_enabled = False
    yield d
    d.quit()


def test_credits_scene_opens_describes_and_closes(driver):
    """``_open_credits`` pushes a CreditsScene overlay that describes its
    resolved credits and pops cleanly."""
    driver.app._open_credits()
    driver.advance_frames(3)
    top = driver.app.manager.current
    assert type(top).__name__ == "CreditsScene"
    assert top.is_overlay is True
    desc = top.describe()
    assert desc["scene"] == "CreditsScene"
    assert "sections" in desc and "source" in desc and "title" in desc
    # Drawing repeatedly must not raise.
    driver.advance_frames(3)
    driver.app.manager.pop()
    driver.advance_frames(2)
    assert type(driver.app.manager.current).__name__ != "CreditsScene"


def test_credits_reachable_from_title_extras(driver):
    """The title's 鑑賞模式 submenu carries a 鳴謝 entry that opens the scene."""
    title = driver.app.manager.current
    assert type(title).__name__ == "TitleScene"
    assert title.on_credits is not None
    title._open_extras()
    driver.advance_frames(2)
    assert title._extras_mode is True
    title.on_credits()
    driver.advance_frames(3)
    assert type(driver.app.manager.current).__name__ == "CreditsScene"


def test_credits_reachable_from_ingame_menu(driver):
    """The in-game menu threads ``on_credits`` and firing it (after the menu's
    from_menu wrapper closes the menu) lands on the CreditsScene."""
    driver.new_game()
    driver.skip_dialogue(max_frames=800)
    driver.advance_frames(5)
    driver.app._open_menu()
    driver.advance_frames(2)
    menu = driver.app.manager.current
    assert type(menu).__name__ == "MenuScene"
    assert menu.on_credits is not None
    menu.on_credits()
    driver.advance_frames(3)
    assert type(driver.app.manager.current).__name__ == "CreditsScene"


def test_credits_scene_reads_pack_supplied_meta_credits(driver):
    """The scene sources its content from the PACK: inject meta credits and the
    overlay's describe() reflects them (source == 'meta')."""
    driver.app.ctx.meta = {"credits": ["Director: Carol", "Music: Dave"]}
    driver.app._open_credits()
    driver.advance_frames(3)
    desc = driver.app.manager.current.describe()
    assert desc["source"] == "meta"
    flat = []
    for s in desc["sections"]:
        flat.extend(s["body"])
    assert "Director: Carol" in flat and "Music: Dave" in flat

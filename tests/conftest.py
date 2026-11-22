"""Suite-wide fixtures.

Layout tests measure text, and text is as wide and as tall as the host's font
makes it. Left alone, the suite grades whichever font the machine happens to
have: a size-28 line is 28 px tall in one CJK font and 41 px in another, and a
machine with no CJK font at all falls back to pygame's Latin default. So the
suite pins one reference font (``tests/fonts/``, SIL OFL 1.1): every family
name the engine probes resolves to it, on every host. A registry built with no
candidates still gets pygame's bundled default, which is also the same bytes
everywhere.

This fixes what the suite measures, not what a player's machine has. To hold
layouts to a font with taller lines (Noto Sans CJK, which Linux desktops and
Android ship, is about 1.45 em), point ``WGG_TEST_FONT`` at that font file and
run the suite again; CI does this in its ``layout-tall-font`` job.

The same goes for player data. Run from a checkout, the engine keeps
``settings.json`` and ``saves/`` next to the project, so the suite used to
start from whatever the developer's last session left there: on a machine that
had already dismissed the first-run onboarding, tests saw one opening scene,
and on a fresh runner another. The suite now gets a data root of its own,
holding only the fact that the onboarding has been seen, and never reads or
writes the developer's. A test about the first run says so itself
(``config.seen_intro = False``).
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pygame
import pytest

REFERENCE_FONT = Path(
    os.environ.get("WGG_TEST_FONT")
    or Path(__file__).parent / "fonts" / "jf-openhuninn-2.1.ttf"
)
if not REFERENCE_FONT.is_file():
    raise RuntimeError(f"WGG_TEST_FONT does not name a font file: {REFERENCE_FONT}")


def _reference_match(name, bold=False, italic=False):
    return str(REFERENCE_FONT)


@pytest.fixture(autouse=True, scope="session")
def reference_font():
    """Resolve every font family to the reference font for the whole run."""
    original = pygame.font.match_font
    pygame.font.match_font = _reference_match
    try:
        yield REFERENCE_FONT
    finally:
        pygame.font.match_font = original


@pytest.fixture(autouse=True, scope="session")
def private_user_data(tmp_path_factory):
    """Give the run its own player-data root instead of the checkout's."""
    import world_gal_game.app as app_module
    import world_gal_game.config as config_module

    root = tmp_path_factory.mktemp("user-data")
    (root / "settings.json").write_text(
        json.dumps({"seen_intro": True}), encoding="utf-8")

    def _root(app_name="WorldGalGame"):
        return root

    originals = (config_module.writable_root, app_module.writable_root)
    config_module.writable_root = _root
    app_module.writable_root = _root
    try:
        yield root
    finally:
        config_module.writable_root, app_module.writable_root = originals

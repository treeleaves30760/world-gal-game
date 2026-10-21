"""Shared plain/rich line breaking keeps words and punctuation intact."""
from __future__ import annotations

import os

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

import pygame  # noqa: E402
import pytest  # noqa: E402

from world_gal_game.ui.fonts import FontRegistry  # noqa: E402
from world_gal_game.ui.widgets.label import _wrap_lines  # noqa: E402


@pytest.fixture(scope="module")
def font():
    pygame.font.init()
    registry = FontRegistry(("Arial", "DejaVu Sans"))
    yield registry.get(24)
    pygame.quit()


def test_plain_wrapper_moves_latin_word_to_next_line(font) -> None:
    prefix = "A long "
    word = "volunteers"

    lines = _wrap_lines(prefix + word, font, font.size(word)[0])

    assert lines == [prefix, word]
    assert "".join(lines) == prefix + word


def test_plain_wrapper_retains_cjk_and_closing_punctuation(font) -> None:
    source = "甲乙丙。丁"

    lines = _wrap_lines(source, font, font.size("甲乙丙")[0])

    assert "".join(lines) == source
    assert all(not line.startswith("。") for line in lines)


def test_plain_wrapper_hard_breaks_one_overlong_token(font) -> None:
    source = "averylongunspacedtoken"

    lines = _wrap_lines(source, font, font.size("avery")[0])

    assert len(lines) > 1
    assert "".join(lines) == source
    assert all(font.size(line)[0] <= font.size("avery")[0] for line in lines)

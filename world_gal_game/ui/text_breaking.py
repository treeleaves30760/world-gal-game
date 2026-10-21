"""Small, deterministic line-break rules shared by UI text widgets."""
from __future__ import annotations

import unicodedata


OPENING_PUNCTUATION = frozenset("([{（［｛〈《「『【〔〖〘〚")
CLOSING_PUNCTUATION = frozenset(
    ")]},.!?:;%）］｝〉》」』】〕〗〙〛，。！？：；、％"
)
BREAK_AFTER = frozenset("-‐–—")
WORD_APOSTROPHES = frozenset("'’")


def is_cjk_character(char: str) -> bool:
    """Return whether a glyph participates in ordinary CJK line breaking."""
    if not char or char in OPENING_PUNCTUATION | CLOSING_PUNCTUATION:
        return False
    codepoint = ord(char)
    return (
        0x2E80 <= codepoint <= 0x9FFF
        or 0xAC00 <= codepoint <= 0xD7AF
        or 0xF900 <= codepoint <= 0xFAFF
        or 0x20000 <= codepoint <= 0x323AF
    )


def is_word_character(char: str) -> bool:
    if char in WORD_APOSTROPHES:
        return True
    return bool(char) and unicodedata.category(char)[0] in {"L", "M", "N"}


def legal_soft_break(left: str, right: str) -> bool:
    """Whether a visual row may end between two authored glyphs.

    This is the practical intersection needed by mixed CJK and Latin VN prose,
    not a claim to implement the complete Unicode line-breaking algorithm.
    """
    if not left or not right:
        return False
    if right in CLOSING_PUNCTUATION or left in OPENING_PUNCTUATION:
        return False
    if left.isspace() or left in BREAK_AFTER:
        return True
    if is_cjk_character(left) or is_cjk_character(right):
        return True
    if is_word_character(left) and is_word_character(right):
        return False
    return False


def latest_soft_break(text: str) -> int | None:
    """Return the latest legal interior boundary in ``text``."""
    for index in range(len(text) - 1, 0, -1):
        if legal_soft_break(text[index - 1], text[index]):
            return index
    return None


def hard_break_before_latest(text: str) -> int:
    """Lossless fallback that avoids stranding paired punctuation when able."""
    boundary = max(1, len(text) - 1)
    if len(text) > 2 and (
        text[-1] in CLOSING_PUNCTUATION
        or text[-2] in OPENING_PUNCTUATION
    ):
        boundary -= 1
    return max(1, boundary)

"""Session-scoped display-only text policies.

Localization resolves canonical player-facing copy.  Development layout
audits may then expand that copy before a widget measures it, but the expanded
form must never enter GameState, history, saves, effects, hooks, or locale
preferences.  This small immutable policy is the boundary between those two
responsibilities.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
import re


_PROTECTED_TOKEN_RE = re.compile(
    r"(?:"
    r"\[\[[^\]\r\n]*\]\]"
    r"|\[/?(?:b|color|size|w|s|ruby|fx)(?:=[^\]\r\n]*)?\]"
    r"|\{[A-Za-z_][A-Za-z0-9_]*(?:![rsa])?(?::[^{}\r\n]+)?\}"
    r"|\\."
    r")",
    re.IGNORECASE,
)
_ACCENT_TRANSLATION = str.maketrans({
    # Stay inside widely-supported Latin-1 glyphs.  The layout-expansion gate
    # should expose geometry failures without turning every unsupported
    # extended consonant into a tofu box; per-glyph fallback has its own gate.
    "A": "Å", "C": "Ç", "E": "Ë", "I": "Ï", "N": "Ñ",
    "O": "Ö", "U": "Ü", "Y": "Ý", "a": "å", "c": "ç",
    "e": "ë", "i": "ï", "n": "ñ", "o": "ö", "u": "ü",
    "y": "ý",
})
_FILLER = "langbreitertext"


def _transform_visible(text: str) -> tuple[str, int]:
    """Accent visible characters while retaining control syntax verbatim."""
    pieces: list[str] = []
    visible = 0
    cursor = 0
    for match in _PROTECTED_TOKEN_RE.finditer(text):
        plain = text[cursor:match.start()]
        pieces.append(plain.translate(_ACCENT_TRANSLATION))
        visible += sum(char.isalnum() for char in plain)
        pieces.append(match.group(0))
        cursor = match.end()
    plain = text[cursor:]
    pieces.append(plain.translate(_ACCENT_TRANSLATION))
    visible += sum(char.isalnum() for char in plain)
    return "".join(pieces), visible


def _expand_line(text: str, factor: float) -> str:
    core = text.rstrip(" \t")
    trailing = text[len(core):]
    transformed, visible = _transform_visible(core)
    if visible <= 0:
        return transformed + trailing
    target = math.ceil(visible * factor)
    extra = max(0, target - visible)
    if extra:
        repeats = (extra + len(_FILLER) - 1) // len(_FILLER)
        transformed += " " + (_FILLER * repeats)[:extra]
    return transformed + trailing


@dataclass(frozen=True)
class PresentationTextPolicy:
    """Immutable policy applied only to text entering visual widgets."""

    profile_id: str = "identity-v1"
    expansion_factor: float = 1.0

    @classmethod
    def pseudo_expand(cls, factor: float = 1.35) -> "PresentationTextPolicy":
        value = float(factor)
        if not math.isfinite(value) or not 1.0 <= value <= 2.0:
            raise ValueError("pseudo text expansion factor must be between 1.0 and 2.0")
        return cls(
            profile_id="pseudo-expand-v1",
            expansion_factor=value,
        )

    @property
    def active(self) -> bool:
        return self.profile_id != "identity-v1"

    def apply(self, text: str) -> str:
        if not isinstance(text, str):
            raise TypeError("presentation text must be a string")
        if not self.active or not text:
            return text
        output: list[str] = []
        for line in text.splitlines(keepends=True):
            if line.endswith("\r\n"):
                body, ending = line[:-2], "\r\n"
            elif line.endswith(("\n", "\r")):
                body, ending = line[:-1], line[-1]
            else:
                body, ending = line, ""
            output.append(_expand_line(body, self.expansion_factor) + ending)
        # splitlines() returns [] for the empty string (handled above) and
        # retains every authored terminal newline for non-empty input.
        return "".join(output)

    def describe(self) -> dict[str, object]:
        return {
            "profile": self.profile_id,
            "factor": self.expansion_factor,
        }

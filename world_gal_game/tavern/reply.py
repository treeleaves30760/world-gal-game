"""Parse a model's reply into visual-novel lines.

Models write prose; a visual novel needs *lines* — who speaks, what is said,
which expression is on screen, and when the background, music or event CG
changes. This module is the bridge. It reads a reply that may mix quoted
speech, narration, parenthetical actions and directive tags, and returns a
list of :class:`ParsedLine`.

Directive tags sit on their own, in front of the line they apply to::

    <sprite:害羞>        expression for the following speech (free emotion word)
    <bg:圖書館>          background / location change
    <bgm:雨天>           music change
    <cg:窗邊>  <cg:off>  show / hide an event CG
    <sfx:敲門>           one-shot sound

Full-width brackets and the Chinese / Japanese tag names are accepted too
(``＜表情：害羞＞``), because models drift between them.

Speech is whatever sits inside the style guide's quotation marks; text outside
them is narration, and a parenthesised line is an action (kept as narration
with the brackets removed). A ``Name：`` prefix assigns the speaker when the
name is one the caller knows. Private reasoning blocks (``<think>…</think>``)
are removed and returned separately, never shown.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Iterable

from .style import StyleGuide

_THINK_RE = re.compile(
    r"<\s*(think|thinking|想|心聲|心声)\s*>(.*?)<\s*/\s*\1\s*>", re.DOTALL | re.IGNORECASE)
_FENCE_RE = re.compile(r"^```[\w-]*\s*$", re.MULTILINE)

_TAG_NAMES: dict[str, str] = {
    "sprite": "sprite", "表情": "sprite", "立繪": "sprite", "立绘": "sprite",
    "emotion": "sprite", "expression": "sprite", "face": "sprite",
    "bg": "bg", "scene": "bg", "場景": "bg", "场景": "bg", "背景": "bg",
    "background": "bg", "location": "bg",
    "bgm": "bgm", "music": "bgm", "音樂": "bgm", "音乐": "bgm",
    "cg": "cg",
    "sfx": "sfx", "音效": "sfx", "se": "sfx",
}
_TAG_RE = re.compile(
    r"[<＜\[【]\s*(" + "|".join(sorted(map(re.escape, _TAG_NAMES), key=len, reverse=True))
    + r")\s*[:：]\s*([^>＞\]】\n]{0,48}?)\s*[>＞\]】]",
    re.IGNORECASE)
_CG_OFF = {"off", "none", "hide", "關", "关", "關閉", "关闭", "無", "无", "-"}

_QUOTE_PAIRS = (("「", "」"), ("『", "』"), ("“", "”"), ('"', '"'))
_PAREN_RE = re.compile(r"^[（(]\s*(.+?)\s*[）)]$", re.DOTALL)
_STAR_RE = re.compile(r"^\*\s*(.+?)\s*\*$", re.DOTALL)
_PREFIX_RE = re.compile(r"^\s*\**\s*([^\s:：「『\"“*]{1,16})\s*\**\s*[:：]\s*\**\s*(.*)$",
                        re.DOTALL)


@dataclass
class ParsedLine:
    kind: str                    # "dialogue" | "narration"
    text: str                    # spoken words without quotes, or narration text
    speaker: str | None = None   # display name; None for narration
    sprite: str = ""             # emotion word in effect
    bg: str = ""
    bgm: str = ""
    cg: str = ""                 # label, or "off"
    sfx: str = ""

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"kind": self.kind, "text": self.text}
        for key in ("speaker", "sprite", "bg", "bgm", "cg", "sfx"):
            value = getattr(self, key)
            if value:
                out[key] = value
        return out


@dataclass
class ParsedReply:
    lines: list[ParsedLine] = field(default_factory=list)
    thoughts: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def spoken(self) -> list[ParsedLine]:
        return [line for line in self.lines if line.kind == "dialogue"]

    def text(self) -> str:
        return "\n".join(line.text for line in self.lines)


def strip_private(text: str) -> tuple[str, list[str]]:
    """Remove private reasoning blocks; return ``(public text, thoughts)``."""
    thoughts = [m.group(2).strip() for m in _THINK_RE.finditer(text)]
    return _THINK_RE.sub("", text), [t for t in thoughts if t]


def _split_quotes(paragraph: str, pairs: tuple[tuple[str, str], ...]
                  ) -> list[tuple[str, str]]:
    """Split a paragraph into ``("say" | "tell", text)`` segments."""
    segments: list[tuple[str, str]] = []
    pos = 0
    size = len(paragraph)
    while pos < size:
        best: tuple[int, str, str] | None = None
        for open_q, close_q in pairs:
            at = paragraph.find(open_q, pos)
            if at != -1 and (best is None or at < best[0]):
                best = (at, open_q, close_q)
        if best is None:
            segments.append(("tell", paragraph[pos:]))
            break
        at, open_q, close_q = best
        end = paragraph.find(close_q, at + len(open_q))
        if end == -1:
            # Unclosed quote: treat the rest as speech rather than lose it.
            if paragraph[pos:at].strip():
                segments.append(("tell", paragraph[pos:at]))
            segments.append(("say", paragraph[at + len(open_q):]))
            break
        if paragraph[pos:at].strip():
            segments.append(("tell", paragraph[pos:at]))
        segments.append(("say", paragraph[at + len(open_q):end]))
        pos = end + len(close_q)
    return [(kind, text.strip()) for kind, text in segments if text.strip()]


def parse_reply(
    text: str,
    *,
    default_speaker: str | None = None,
    known_speakers: Iterable[str] = (),
    style: StyleGuide | None = None,
    bare_is_speech: bool | None = None,
) -> ParsedReply:
    """Parse one reply.

    ``default_speaker`` is who is talking when no ``Name：`` prefix says
    otherwise — set it for a character's turn, leave it ``None`` for a
    narrator. ``bare_is_speech`` decides what an unquoted, unbracketed line is:
    speech (the default when there is a default speaker) or narration.
    """
    style = style or StyleGuide.builtin()
    reply = ParsedReply()
    if not text or not text.strip():
        return reply
    if bare_is_speech is None:
        bare_is_speech = default_speaker is not None
    body, reply.thoughts = strip_private(text.replace("\r\n", "\n").replace("\r", "\n"))
    body = _FENCE_RE.sub("", body)

    pairs = tuple(dict.fromkeys(
        [(style.quote_open, style.quote_close), *_QUOTE_PAIRS]))
    known = {name for name in known_speakers if name}
    if default_speaker:
        known.add(default_speaker)

    state = {"sprite": ""}
    pending: dict[str, str] = {}

    def take_tags(chunk: str) -> str:
        def swallow(match: re.Match[str]) -> str:
            kind = _TAG_NAMES.get(match.group(1).casefold(), "")
            value = match.group(2).strip()
            if kind == "sprite":
                state["sprite"] = value
            elif kind == "cg":
                pending["cg"] = "off" if value.casefold() in _CG_OFF else value
            elif kind:
                pending[kind] = value
            return ""
        return _TAG_RE.sub(swallow, chunk)

    def emit(kind: str, content: str, speaker: str | None) -> None:
        content = content.strip()
        if not content:
            return
        line = ParsedLine(kind=kind, text=content,
                          speaker=speaker if kind == "dialogue" else None,
                          sprite=state["sprite"] if kind == "dialogue" else "")
        for key in ("bg", "bgm", "cg", "sfx"):
            if key in pending:
                setattr(line, key, pending.pop(key))
        reply.lines.append(line)

    for raw in body.split("\n"):
        paragraph = take_tags(raw).strip()
        if not paragraph:
            continue
        speaker = default_speaker
        explicit = False
        match = _PREFIX_RE.match(paragraph)
        if match and match.group(1) in known and match.group(2).strip():
            speaker, paragraph, explicit = match.group(1), match.group(2).strip(), True

        action = _PAREN_RE.match(paragraph) or _STAR_RE.match(paragraph)
        if action and not any(q in action.group(1) for q, _c in pairs):
            emit("narration", action.group(1), None)
            continue

        segments = _split_quotes(paragraph, pairs)
        if not any(kind == "say" for kind, _text in segments):
            if explicit or bare_is_speech:
                emit("dialogue", paragraph, speaker)
            else:
                emit("narration", paragraph, None)
            continue
        for kind, content in segments:
            if kind == "say":
                if speaker is None:
                    reply.warnings.append("quoted speech with no speaker")
                    emit("narration", f"{pairs[0][0]}{content}{pairs[0][1]}", None)
                else:
                    emit("dialogue", content, speaker)
            else:
                inner = _PAREN_RE.match(content) or _STAR_RE.match(content)
                emit("narration", inner.group(1) if inner else content, None)

    if pending:
        # Tags after the last line have nothing to attach to; keep them on a
        # trailing empty carrier so the caller can still apply them.
        reply.warnings.append("directive tag with no following line: "
                              + ", ".join(f"{k}:{v}" for k, v in pending.items()))
    return reply


def render_turn(line_text: str, *, sprite: str = "", style: StyleGuide | None = None
                ) -> str:
    """Render a spoken line back in the format actors are asked to write."""
    style = style or StyleGuide.builtin()
    said = f"{style.quote_open}{line_text}{style.quote_close}"
    return f"<sprite:{sprite}>\n{said}" if sprite else said

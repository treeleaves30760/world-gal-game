"""The de-slop linter: measure how machine-written a scene sounds.

"It reads like an AI wrote it" is usually said about a handful of concrete
things, and most of them can be counted:

- stock phrases and rhetorical templates (the patterns in ``tavern.style``);
- emotions named instead of shown, and counsellor-style reassurance;
- rhythm: every line the same length, every line a complete sentence, no
  one-word replies;
- tics: every line opens the same way, or addresses the listener by name;
- voices that cannot be told apart once the name plates are covered;
- the wrong regional vocabulary, or the wrong script mixed in.

:class:`SlopLinter` reports each as a :class:`SlopHit` with the line it is on
and what to do instead, and rolls them into a score (weighted hits per 100
lines; lower is better) so a pack can track it or gate on it.

The same linter runs in two places. Over a finished scene (``lint``) it is an
advisory review tool — ``world-gal-game tavern lint``. Over a single freshly
generated turn (``lint_turn``) it is the stage's retake trigger: a hard hit
sends the line back to the agent that wrote it, with the hits as the note.

The numbers are heuristics. They are good at finding the lines worth a second
look and bad at judging whether a scene is moving; treat a clean report as
"no known tells", not as "well written".
"""
from __future__ import annotations

import re
import statistics
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Iterable

from .style import SlopPattern, StyleGuide

_DIRECTIVE_RE = re.compile(r"\[\[[^\]\n]*\]\]")
_TOKEN_RE = re.compile(r"\{[^{}\n]{1,40}\}")
_ELLIPSIS_RE = re.compile(r"…|\.{3,}|。{3,}")
_SENT_END = "。！？!?…～~.」』”\"' \u3000"
_CLOSERS = "」』”\"'）)"
_SIMPLIFIED_ONLY = set("们这说为个来时么对还发现过样会话让经问觉边东车长门间马鸟鱼饭饮钱银"
                       "红绿黄蓝亲爱谢请该谁当应虽难离欢见观认识读书写记许诉语调")
_TRADITIONAL_ONLY = set("們這說為個來時麼對還發現過樣會話讓經問覺邊東車長門間馬鳥魚飯飲錢銀"
                        "紅綠黃藍親愛謝請該誰當應雖難離歡見觀認識讀書寫記許訴語調")
_POLITE_RE = re.compile(r"[謝谢][謝谢]|[對对]不起|抱歉|不好意思|(?i:\bthank(?:s| you)\b)|(?i:\bsorry\b)")

_MESSAGES: dict[str, dict[str, tuple[str, str]]] = {
    "zh-Hant": {
        "rhythm.uniform": ("台詞長度太平均（變異係數 {cv:.2f}）",
                           "真人講話長短差很多：加幾句很短的，把長句拆開。"),
        "rhythm.no_short": ("幾乎沒有短回應（{ratio:.0%} 的台詞在 {n} 字以內）",
                            "讓人只回「嗯。」「蛤？」「……喔。」。"),
        "rhythm.long_line": ("單句太長（{length} 字，上限 {limit}）",
                             "拆成兩個對話框，或砍掉一半。"),
        "punct.full_stops": ("{ratio:.0%} 的台詞都收在句號",
                             "口語會斷在一半、拖長、或沒有收尾。"),
        "punct.ellipsis": ("{ratio:.0%} 的台詞有刪節號",
                           "停頓改用短句和換行表現，刪節號留給真的說不下去的地方。"),
        "punct.ellipsis_lead": ("{ratio:.0%} 的台詞用刪節號開頭",
                                "不要每句都先沉默一下。"),
        "tic.vocative": ("{ratio:.0%} 的台詞在叫對方的名字",
                         "兩個人面對面講話很少一直叫名字。"),
        "tic.opener": ("「{who}」有 {ratio:.0%} 的台詞用「{opener}」開頭",
                       "換掉大部分。"),
        "tic.polite": ("道謝／道歉太密（每 {per:.1f} 句一次）",
                       "留一次就好，其餘換成行動或沉默。"),
        "voice.indistinct": ("「{a}」和「{b}」的說話方式分不出來",
                             "遮住名字讀一次；給其中一人不同的句長、語氣詞或迴避方式。"),
        "script.mixed": ("混入簡體字：{chars}", "統一成繁體。"),
        "script.mixed_hans": ("混入繁體字：{chars}", "統一成簡體。"),
        "regional.term": ("用語不是這個地區的說法：「{term}」", "改成「{prefer}」。"),
        "card.never_says": ("「{who}」不會說的話：「{phrase}」", "照角色卡改。"),
        "banned.phrase": ("禁用句：「{phrase}」", "換一個說法，不是換同義詞。"),
        "fmt.quote": ("引號沒有成對", "補上或刪掉。"),
        "fmt.speaks_for_other": ("替「{who}」說話或行動", "只寫自己這個角色。"),
        "fmt.too_many_lines": ("一次說了 {count} 句", "這一輪只留最要緊的一兩句。"),
        "fmt.empty": ("沒有內容", "至少給一句話或一個動作。"),
    },
    "zh-Hans": {
        "rhythm.uniform": ("台词长度太平均（变异系数 {cv:.2f}）",
                           "真人讲话长短差很多：加几句很短的，把长句拆开。"),
        "rhythm.no_short": ("几乎没有短回应（{ratio:.0%} 的台词在 {n} 字以内）",
                            "让人只回「嗯。」「啊？」「……哦。」。"),
        "rhythm.long_line": ("单句太长（{length} 字，上限 {limit}）",
                             "拆成两个对话框，或砍掉一半。"),
        "punct.full_stops": ("{ratio:.0%} 的台词都收在句号",
                             "口语会断在一半、拖长、或没有收尾。"),
        "punct.ellipsis": ("{ratio:.0%} 的台词有省略号",
                           "停顿改用短句和换行表现，省略号留给真的说不下去的地方。"),
        "punct.ellipsis_lead": ("{ratio:.0%} 的台词用省略号开头",
                                "不要每句都先沉默一下。"),
        "tic.vocative": ("{ratio:.0%} 的台词在叫对方的名字",
                         "两个人面对面讲话很少一直叫名字。"),
        "tic.opener": ("「{who}」有 {ratio:.0%} 的台词用「{opener}」开头",
                       "换掉大部分。"),
        "tic.polite": ("道谢／道歉太密（每 {per:.1f} 句一次）",
                       "留一次就好，其余换成行动或沉默。"),
        "voice.indistinct": ("「{a}」和「{b}」的说话方式分不出来",
                             "遮住名字读一次；给其中一人不同的句长、语气词或回避方式。"),
        "script.mixed": ("混入简体字：{chars}", "统一成繁体。"),
        "script.mixed_hans": ("混入繁体字：{chars}", "统一成简体。"),
        "regional.term": ("用语不是这个地区的说法：「{term}」", "改成「{prefer}」。"),
        "card.never_says": ("「{who}」不会说的话：「{phrase}」", "照角色卡改。"),
        "banned.phrase": ("禁用句：「{phrase}」", "换一个说法，不是换同义词。"),
        "fmt.quote": ("引号没有成对", "补上或删掉。"),
        "fmt.speaks_for_other": ("替「{who}」说话或行动", "只写自己这个角色。"),
        "fmt.too_many_lines": ("一次说了 {count} 句", "这一轮只留最要紧的一两句。"),
        "fmt.empty": ("没有内容", "至少给一句话或一个动作。"),
    },
    "en": {
        "rhythm.uniform": ("line lengths are too even (CV {cv:.2f})",
                           "Real talk varies a lot: add very short replies, split long ones."),
        "rhythm.no_short": ("almost no short replies ({ratio:.0%} of lines are within {n} chars)",
                            "Let someone answer with one word."),
        "rhythm.long_line": ("line too long ({length} chars, limit {limit})",
                             "Split it across two boxes or cut half."),
        "punct.full_stops": ("{ratio:.0%} of lines end in a full stop",
                             "Speech breaks off, trails, or goes unfinished."),
        "punct.ellipsis": ("{ratio:.0%} of lines use an ellipsis",
                           "Show the pause with a shorter line instead."),
        "punct.ellipsis_lead": ("{ratio:.0%} of lines open with an ellipsis",
                                "Not every line needs a silence first."),
        "tic.vocative": ("{ratio:.0%} of lines address the listener by name",
                         "People facing each other rarely do."),
        "tic.opener": ("{ratio:.0%} of {who}'s lines open with '{opener}'",
                       "Change most of them."),
        "tic.polite": ("thanks / apologies are too frequent (one every {per:.1f} lines)",
                       "Keep one; turn the rest into action or silence."),
        "voice.indistinct": ("{a} and {b} sound the same",
                             "Read it with names covered; give one a different length, "
                             "habit or way of dodging."),
        "script.mixed": ("simplified characters mixed in: {chars}", "Use one script."),
        "script.mixed_hans": ("traditional characters mixed in: {chars}", "Use one script."),
        "regional.term": ("wrong regional word: '{term}'", "Use '{prefer}'."),
        "card.never_says": ("{who} would never say: '{phrase}'", "Follow the card."),
        "banned.phrase": ("banned phrase: '{phrase}'", "Say something else, not a synonym."),
        "fmt.quote": ("unbalanced quotation marks", "Close or remove them."),
        "fmt.speaks_for_other": ("speaks or acts for {who}", "Write only your own character."),
        "fmt.too_many_lines": ("{count} lines in one turn", "Keep the one or two that matter."),
        "fmt.empty": ("empty turn", "Give at least a word or an action."),
    },
}
_MESSAGES["ja"] = _MESSAGES["en"]

_WEIGHTS = {
    "rhythm.uniform": 4.0, "rhythm.no_short": 3.0, "rhythm.long_line": 1.0,
    "punct.full_stops": 2.0, "punct.ellipsis": 3.0, "punct.ellipsis_lead": 2.0,
    "tic.vocative": 3.0, "tic.opener": 2.0, "tic.polite": 2.0,
    "voice.indistinct": 2.0, "script.mixed": 3.0, "script.mixed_hans": 3.0,
    "regional.term": 1.5, "card.never_says": 4.0, "banned.phrase": 4.0,
    "fmt.quote": 2.0, "fmt.speaks_for_other": 6.0, "fmt.too_many_lines": 3.0,
    "fmt.empty": 6.0,
}
_HARD = {"card.never_says", "banned.phrase", "fmt.speaks_for_other",
         "fmt.too_many_lines", "fmt.empty"}

#: Grade boundaries on the score (weighted hits per 100 lines).
GRADES = ((10.0, "A"), (25.0, "B"), (50.0, "C"))


@dataclass
class TextLine:
    """One line of a scene as the linter sees it."""

    index: int
    text: str
    speaker: str | None = None
    scene_id: str = ""


@dataclass
class SlopHit:
    rule: str
    label: str
    hint: str = ""
    weight: float = 1.0
    hard: bool = False
    line: int | None = None
    scene_id: str = ""
    speaker: str | None = None
    snippet: str = ""

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"rule": self.rule, "label": self.label,
                               "weight": round(self.weight, 2)}
        if self.hint:
            out["hint"] = self.hint
        if self.hard:
            out["hard"] = True
        if self.line is not None:
            out["line"] = self.line
        if self.scene_id:
            out["scene"] = self.scene_id
        if self.speaker:
            out["speaker"] = self.speaker
        if self.snippet:
            out["snippet"] = self.snippet
        return out


@dataclass
class SlopReport:
    hits: list[SlopHit] = field(default_factory=list)
    lines: int = 0
    dialogue_lines: int = 0
    score: float = 0.0
    grade: str = "A"
    fingerprints: dict[str, dict[str, Any]] = field(default_factory=dict)

    @property
    def hard_hits(self) -> list[SlopHit]:
        return [hit for hit in self.hits if hit.hard]

    def by_rule(self) -> dict[str, int]:
        return dict(Counter(hit.rule for hit in self.hits).most_common())

    def to_dict(self) -> dict[str, Any]:
        return {
            "score": round(self.score, 1),
            "grade": self.grade,
            "lines": self.lines,
            "dialogue_lines": self.dialogue_lines,
            "by_rule": self.by_rule(),
            "hits": [hit.to_dict() for hit in self.hits],
            "fingerprints": self.fingerprints,
        }

    def render(self, limit: int = 40) -> str:
        """A plain-text review, worst hits first."""
        head = (f"slop score {self.score:.1f} (grade {self.grade}) — "
                f"{len(self.hits)} hits over {self.lines} lines "
                f"({self.dialogue_lines} spoken)")
        out = [head]
        ordered = sorted(self.hits, key=lambda h: (-h.weight, h.line or -1))
        for hit in ordered[:limit]:
            where = "" if hit.line is None else f"L{hit.line}"
            if hit.scene_id:
                where = f"{hit.scene_id}:{where}" if where else hit.scene_id
            who = f" [{hit.speaker}]" if hit.speaker else ""
            mark = "!" if hit.hard else "-"
            out.append(f" {mark} {where:<18} {hit.rule:<22} {hit.label}{who}")
            if hit.snippet:
                out.append(f"     > {hit.snippet}")
            if hit.hint:
                out.append(f"     = {hit.hint}")
        if len(ordered) > limit:
            out.append(f"   … {len(ordered) - limit} more")
        for name, fp in self.fingerprints.items():
            particles = "".join(fp.get("particles", [])) or "-"
            out.append(f"   voice {name}: {fp['lines']} lines, mean {fp['mean_len']:.0f} "
                       f"chars, short {fp['short_ratio']:.0%}, ? {fp['question_ratio']:.0%}, "
                       f"… {fp['ellipsis_ratio']:.0%}, particles {particles}")
        return "\n".join(out)


def grade_for(score: float) -> str:
    for limit, grade in GRADES:
        if score < limit:
            return grade
    return "D"


def clean_text(text: str) -> str:
    """Strip engine markup so only what the player reads is measured."""
    if not text:
        return ""
    text = _DIRECTIVE_RE.sub("", text)
    if "[" in text:
        try:
            from ..dialogue.richtext import strip_markup
            text = strip_markup(text)
        except Exception:
            pass
    return text.strip()


class SlopLinter:
    """Detect the tells described in :mod:`tavern.style` plus structural ones."""

    def __init__(self, style: StyleGuide | None = None, *,
                 never_says: dict[str, list[str]] | None = None,
                 names: Iterable[str] = (), player_name: str = "") -> None:
        self.style = style or StyleGuide.builtin()
        self._patterns: list[tuple[SlopPattern, re.Pattern[str]]] = [
            (pattern, re.compile(pattern.pattern))
            for pattern in self.style.active_patterns()
        ]
        self.never_says = {k: list(v) for k, v in (never_says or {}).items()}
        self.names = {n for n in names if n}
        self.player_name = player_name
        locale = self.style.locale
        self._msg = _MESSAGES.get(locale) or _MESSAGES["en"]
        self._cjk = locale.startswith("zh") or locale == "ja"
        self._short_len = 6 if self._cjk else 16

    # ------------------------------------------------------------------
    # helpers

    def _hit(self, rule: str, *, line: int | None = None, scene_id: str = "",
             speaker: str | None = None, snippet: str = "", weight: float | None = None,
             **fmt: Any) -> SlopHit:
        label, hint = self._msg.get(rule, (rule, ""))
        try:
            label = label.format(**fmt)
            hint = hint.format(**fmt)
        except (KeyError, IndexError, ValueError):
            pass
        return SlopHit(rule=rule, label=label, hint=hint,
                       weight=_WEIGHTS.get(rule, 1.0) if weight is None else weight,
                       hard=rule in _HARD, line=line, scene_id=scene_id,
                       speaker=speaker, snippet=snippet)

    def spoken(self, text: str) -> str:
        """The part of a dialogue line that is actually said aloud."""
        style = self.style
        if style.quote_open and style.quote_open in text:
            parts = re.findall(
                re.escape(style.quote_open) + r"(.*?)" + re.escape(style.quote_close),
                text, re.DOTALL)
            if parts:
                return "".join(parts).strip()
        return text.strip().strip(style.quote_open + style.quote_close)

    @staticmethod
    def _snippet(text: str, start: int, end: int, pad: int = 8) -> str:
        left = max(0, start - pad)
        right = min(len(text), end + pad)
        snippet = text[left:right].replace("\n", " ")
        return ("…" if left else "") + snippet + ("…" if right < len(text) else "")

    def _pattern_hits(self, line: TextLine, text: str, *,
                      density: dict[str, list[tuple[TextLine, str]]] | None,
                      close_zone: bool) -> list[SlopHit]:
        hits: list[SlopHit] = []
        is_dialogue = line.speaker is not None
        for pattern, compiled in self._patterns:
            if pattern.scope == "dialogue" and not is_dialogue:
                continue
            if pattern.scope == "narration" and is_dialogue:
                continue
            if pattern.id.startswith("close.") and not close_zone:
                continue
            matches = list(compiled.finditer(text))
            if not matches:
                continue
            if pattern.max_per_100 is not None:
                if density is not None:
                    bucket = density.setdefault(pattern.id, [])
                    bucket.extend((line, self._snippet(text, m.start(), m.end()))
                                  for m in matches)
                elif len(matches) >= 2:
                    # A single turn cannot have a density, but twice in one
                    # breath is already the habit showing.
                    hits.append(SlopHit(
                        rule=pattern.id, label=pattern.label, hint=pattern.hint,
                        weight=1.0, line=line.index, scene_id=line.scene_id,
                        speaker=line.speaker,
                        snippet=self._snippet(text, matches[0].start(), matches[-1].end())))
                continue
            for match in matches[:3]:
                hits.append(SlopHit(
                    rule=pattern.id, label=pattern.label, hint=pattern.hint,
                    weight=pattern.weight, hard=pattern.hard, line=line.index,
                    scene_id=line.scene_id, speaker=line.speaker,
                    snippet=self._snippet(text, match.start(), match.end())))
        return hits

    def _lexical_hits(self, line: TextLine, text: str) -> list[SlopHit]:
        """Banned phrases, card ``never_says``, regional words, script mixing."""
        hits: list[SlopHit] = []
        common = dict(line=line.index, scene_id=line.scene_id, speaker=line.speaker)
        for phrase in self.style.banned_phrases:
            at = text.find(phrase)
            if phrase and at >= 0:
                hits.append(self._hit("banned.phrase", phrase=phrase,
                                      snippet=self._snippet(text, at, at + len(phrase)),
                                      **common))
        if line.speaker:
            for phrase in self.never_says.get(line.speaker, []):
                at = text.find(phrase)
                if phrase and at >= 0:
                    hits.append(self._hit("card.never_says", who=line.speaker,
                                          phrase=phrase,
                                          snippet=self._snippet(text, at, at + len(phrase)),
                                          **common))
        if self.style.region:
            for term, prefer in self.style.regional_terms.items():
                at = text.find(term)
                if at >= 0:
                    hits.append(self._hit("regional.term", term=term, prefer=prefer,
                                          snippet=self._snippet(text, at, at + len(term)),
                                          **common))
        locale = self.style.locale
        if locale == "zh-Hant":
            stray = sorted({ch for ch in text if ch in _SIMPLIFIED_ONLY})
            if stray:
                hits.append(self._hit("script.mixed", chars="".join(stray[:8]), **common))
        elif locale == "zh-Hans":
            stray = sorted({ch for ch in text if ch in _TRADITIONAL_ONLY})
            if stray:
                hits.append(self._hit("script.mixed_hans", chars="".join(stray[:8]),
                                      **common))
        return hits

    # ------------------------------------------------------------------
    # one generated turn (the stage's retake check)

    def lint_turn(self, text: str, *, speaker: str | None = None,
                  others: Iterable[str] = (), max_lines: int = 3,
                  is_last: bool = False) -> list[SlopHit]:
        """Check one freshly written turn.

        ``speaker`` is the display name of the character whose turn it is
        (``None`` for narration). ``others`` are the names this turn must not
        speak for — the other cast members and the player.
        """
        line = TextLine(index=0, text=text, speaker=speaker)
        body = clean_text(text)
        if not body:
            return [self._hit("fmt.empty", speaker=speaker)]
        hits = self._pattern_hits(line, body, density=None, close_zone=is_last)
        hits += self._lexical_hits(line, body)
        style = self.style
        if body.count(style.quote_open) != body.count(style.quote_close):
            hits.append(self._hit("fmt.quote", speaker=speaker))
        if speaker is not None:
            spoken_parts = [part.strip() for part in re.findall(
                re.escape(style.quote_open) + r"(.*?)" + re.escape(style.quote_close),
                body, re.DOTALL)] or [self.spoken(body)]
            if len(spoken_parts) > max_lines:
                hits.append(self._hit("fmt.too_many_lines", count=len(spoken_parts),
                                      speaker=speaker))
            for part in spoken_parts:
                if len(part) > style.line_max_chars:
                    hits.append(self._hit(
                        "rhythm.long_line", length=len(part),
                        limit=style.line_max_chars, speaker=speaker,
                        snippet=part[:24] + "…",
                        weight=2.0 if len(part) > style.line_max_chars * 2 else 1.0))
        else:
            for part in [p for p in body.splitlines() if p.strip()]:
                if len(part) > style.narration_max_chars:
                    hits.append(self._hit(
                        "rhythm.long_line", length=len(part),
                        limit=style.narration_max_chars, snippet=part[:24] + "…"))
        for other in others:
            if not other or other == speaker:
                continue
            # "Name：「…」" or "Name: …" at the start of a line = speaking for them.
            if re.search(r"(?m)^\s*" + re.escape(other) + r"\s*[:：]", body):
                hits.append(self._hit("fmt.speaks_for_other", who=other, speaker=speaker))
        return hits

    @staticmethod
    def needs_retake(hits: list[SlopHit], soft_threshold: float = 5.0) -> bool:
        """Whether a turn's hits are bad enough to send it back."""
        if any(hit.hard for hit in hits):
            return True
        return sum(hit.weight for hit in hits) >= soft_threshold

    def retake_note(self, hits: list[SlopHit], limit: int = 5) -> str:
        """Turn hits into the note handed back to the agent."""
        ordered = sorted(hits, key=lambda h: (not h.hard, -h.weight))[:limit]
        lines = []
        for hit in ordered:
            bit = hit.label
            if hit.snippet:
                bit += f"（{hit.snippet}）" if self._cjk else f" ({hit.snippet})"
            if hit.hint:
                bit += f" → {hit.hint}"
            lines.append(f"- {bit}")
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # a whole scene

    def fingerprint(self, lines: list[str]) -> dict[str, Any]:
        """A speaker's measurable voice: length, rhythm, punctuation, particles."""
        spoken = [self.spoken(clean_text(text)) for text in lines]
        spoken = [s for s in spoken if s]
        lengths = [len(s) for s in spoken] or [0]
        mean = statistics.fmean(lengths)
        particles: Counter[str] = Counter()
        for text in spoken:
            core = text.rstrip(_SENT_END)
            if core and core[-1] in self.style.particles:
                particles[core[-1]] += 1
        openers = Counter(s[:2] for s in spoken if len(s) >= 2)
        total = len(spoken) or 1
        return {
            "lines": len(spoken),
            "mean_len": mean,
            "cv": (statistics.pstdev(lengths) / mean) if mean and len(lengths) > 1 else 0.0,
            "short_ratio": sum(1 for n in lengths if n <= self._short_len) / total,
            "question_ratio": sum(1 for s in spoken if s.rstrip(_CLOSERS).endswith(
                ("？", "?"))) / total,
            "exclaim_ratio": sum(1 for s in spoken if s.rstrip(_CLOSERS).endswith(
                ("！", "!"))) / total,
            "ellipsis_ratio": sum(1 for s in spoken if _ELLIPSIS_RE.search(s)) / total,
            "particles": [p for p, _n in particles.most_common(3)],
            "opener": openers.most_common(1)[0] if openers else ("", 0),
        }

    def lint(self, lines: list[TextLine]) -> SlopReport:
        """Review a whole scene (or several — set ``scene_id`` on the lines)."""
        report = SlopReport()
        cleaned = [(line, clean_text(line.text)) for line in lines]
        cleaned = [(line, text) for line, text in cleaned if text]
        report.lines = len(cleaned)
        if not cleaned:
            return report

        last_by_scene: dict[str, int] = {}
        for position, (line, _text) in enumerate(cleaned):
            last_by_scene[line.scene_id] = position
        scene_sizes = Counter(line.scene_id for line, _ in cleaned)

        density: dict[str, list[tuple[TextLine, str]]] = {}
        names = set(self.names) | {line.speaker for line, _ in cleaned if line.speaker}
        if self.player_name:
            names.add(self.player_name)

        dialogue: list[tuple[TextLine, str]] = []
        by_speaker: dict[str, list[str]] = {}
        for position, (line, text) in enumerate(cleaned):
            close_zone = (last_by_scene[line.scene_id] - position < 2
                          and scene_sizes[line.scene_id] >= 4)
            report.hits += self._pattern_hits(line, text, density=density,
                                              close_zone=close_zone)
            report.hits += self._lexical_hits(line, text)
            if text.count(self.style.quote_open) != text.count(self.style.quote_close):
                report.hits.append(self._hit("fmt.quote", line=line.index,
                                             scene_id=line.scene_id, speaker=line.speaker))
            if line.speaker is not None:
                said = self.spoken(text)
                if said:
                    dialogue.append((line, said))
                    by_speaker.setdefault(line.speaker, []).append(text)
                    if len(said) > self.style.line_max_chars:
                        report.hits.append(self._hit(
                            "rhythm.long_line", length=len(said),
                            limit=self.style.line_max_chars, line=line.index,
                            scene_id=line.scene_id, speaker=line.speaker,
                            snippet=said[:24] + "…",
                            weight=2.0 if len(said) > self.style.line_max_chars * 2 else 1.0))
        report.dialogue_lines = len(dialogue)

        # Density patterns: one aggregated hit when over the allowed rate.
        for pattern, _compiled in self._patterns:
            found = density.get(pattern.id)
            if not found or pattern.max_per_100 is None:
                continue
            rate = len(found) / report.lines * 100
            if rate <= pattern.max_per_100 or len(found) < 3:
                continue
            first_line, _snip = found[0]
            report.hits.append(SlopHit(
                rule=pattern.id, hint=pattern.hint,
                label=f"{pattern.label} ({len(found)}×, {rate:.0f}/100)",
                weight=pattern.weight * min(3.0, rate / pattern.max_per_100),
                line=first_line.index, scene_id=first_line.scene_id,
                snippet=" / ".join(snip for _line, snip in found[:3])))

        self._structure_hits(report, dialogue, names)
        for speaker, texts in by_speaker.items():
            report.fingerprints[speaker] = self.fingerprint(texts)
        self._voice_hits(report)

        total = sum(hit.weight for hit in report.hits)
        report.score = 100.0 * total / max(report.lines, 12)
        report.grade = grade_for(report.score)
        return report

    def _structure_hits(self, report: SlopReport,
                        dialogue: list[tuple[TextLine, str]], names: set[str]) -> None:
        count = len(dialogue)
        if count < 8:
            return
        said = [text for _line, text in dialogue]
        lengths = [len(text) for text in said]
        mean = statistics.fmean(lengths)
        if mean:
            cv = statistics.pstdev(lengths) / mean
            if cv < 0.33:
                report.hits.append(self._hit("rhythm.uniform", cv=cv))
        short_ratio = sum(1 for n in lengths if n <= self._short_len) / count
        if short_ratio < 0.08:
            report.hits.append(self._hit("rhythm.no_short", ratio=short_ratio,
                                         n=self._short_len))
        full_stop = sum(1 for text in said
                        if text.rstrip(_CLOSERS).endswith(("。", "."))) / count
        if full_stop > 0.75:
            report.hits.append(self._hit("punct.full_stops", ratio=full_stop))
        ellipsis = sum(1 for text in said if _ELLIPSIS_RE.search(text)) / count
        if ellipsis > 0.35:
            report.hits.append(self._hit("punct.ellipsis", ratio=ellipsis))
        lead = sum(1 for text in said if _ELLIPSIS_RE.match(text)) / count
        if lead > 0.2:
            report.hits.append(self._hit("punct.ellipsis_lead", ratio=lead))

        vocative = 0
        for line, text in dialogue:
            for name in names:
                if not name or name == line.speaker or len(name) < 2:
                    continue
                esc = re.escape(name)
                if re.search(esc + r"[，,、！？!?…～~]|[，,、]\s*" + esc + r"[。！？!?]?$",
                             text):
                    vocative += 1
                    break
        if vocative / count > 0.25:
            report.hits.append(self._hit("tic.vocative", ratio=vocative / count))

        polite = sum(len(_POLITE_RE.findall(text)) for text in said)
        if polite >= 3 and polite / count > 0.2:
            report.hits.append(self._hit("tic.polite", per=count / polite))

        by_speaker: dict[str, list[str]] = {}
        for line, text in dialogue:
            by_speaker.setdefault(line.speaker or "", []).append(text)
        for speaker, texts in by_speaker.items():
            if len(texts) < 5:
                continue
            openers = Counter(text[:2] for text in texts if len(text) >= 2)
            if not openers:
                continue
            opener, n = openers.most_common(1)[0]
            if n >= 3 and n / len(texts) > 0.4:
                report.hits.append(self._hit("tic.opener", who=speaker, opener=opener,
                                             ratio=n / len(texts), speaker=speaker))

    def _voice_hits(self, report: SlopReport) -> None:
        speakers = [(name, fp) for name, fp in report.fingerprints.items()
                    if fp["lines"] >= 6]
        for i, (name_a, a) in enumerate(speakers):
            for name_b, b in speakers[i + 1:]:
                longest = max(a["mean_len"], b["mean_len"]) or 1.0
                if abs(a["mean_len"] - b["mean_len"]) / longest > 0.15:
                    continue
                if a["particles"][:2] != b["particles"][:2]:
                    continue
                if abs(a["question_ratio"] - b["question_ratio"]) > 0.12:
                    continue
                if abs(a["ellipsis_ratio"] - b["ellipsis_ratio"]) > 0.12:
                    continue
                if abs(a["short_ratio"] - b["short_ratio"]) > 0.12:
                    continue
                report.hits.append(self._hit("voice.indistinct", a=name_a, b=name_b))


def lines_from_scene(scene: Any) -> list[TextLine]:
    """Adapt an engine ``Scene`` (or a scene dict) to linter lines."""
    scene_id = getattr(scene, "id", None) or (scene.get("id", "") if isinstance(scene, dict)
                                              else "")
    raw_lines = getattr(scene, "lines", None)
    if raw_lines is None and isinstance(scene, dict):
        raw_lines = scene.get("lines") or []
    out: list[TextLine] = []
    for index, line in enumerate(raw_lines or []):
        if isinstance(line, dict):
            text, speaker = line.get("text", ""), line.get("speaker")
        else:
            text, speaker = getattr(line, "text", ""), getattr(line, "speaker", None)
        out.append(TextLine(index=index, text=str(text or ""), speaker=speaker,
                            scene_id=str(scene_id)))
    return out

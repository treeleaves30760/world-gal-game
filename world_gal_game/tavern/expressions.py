"""Resolve free-form emotion words to the portrait expressions a pack has.

A writer (human or model) says ``<sprite:難為情>``; the character only has
``smile`` / ``shy`` / ``sad`` art. Forcing writers to pick from a fixed list
makes them stop thinking about the emotion and start thinking about the list,
so the stage lets them write the word that fits and resolves it afterwards:

1. the word is an expression key, or one of its aliases on the card;
2. the word was resolved before — the memo (認詞記錄) remembers, so the same
   word always lands on the same portrait and a human can correct it once;
3. an alias or key is contained in the word (``有點害羞`` contains ``害羞``);
4. a built-in emotion lexicon maps the word to an emotion family, and the
   family to whichever key the character has for it;
5. a fuzzy string match, as a last resort;
6. otherwise unresolved: the caller keeps the previous expression.

Each result says how it was reached and how sure it is, so a review can show
"≈ guessed" next to anything that was not an exact hit.
"""
from __future__ import annotations

import difflib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from .card import dump_yaml

_STRIP_RE = re.compile(r"[\s　，。、！？!?,.~～…「」『』（）()<>＜＞:：]+")

#: Emotion families -> words that name them (zh-Hant, zh-Hans, en, ja).
LEXICON: dict[str, tuple[str, ...]] = {
    "neutral": ("平靜", "平静", "普通", "沒表情", "没表情", "面無表情", "面无表情", "冷靜",
                "冷静", "淡然", "淡定", "正常", "normal", "neutral", "default", "calm",
                "idle", "base", "plain", "通常", "真顔"),
    "smile": ("微笑", "淺笑", "浅笑", "溫柔", "温柔", "笑容", "輕笑", "轻笑", "笑",
              "smile", "gentle", "soft", "warm", "微笑み", "笑顔"),
    "happy": ("開心", "开心", "高興", "高兴", "喜悅", "喜悦", "興奮", "兴奋", "雀躍", "雀跃",
              "歡喜", "欢喜", "大笑", "happy", "joy", "glad", "delight", "excited", "grin",
              "laugh", "嬉しい", "喜び"),
    "shy": ("害羞", "臉紅", "脸红", "難為情", "难为情", "不好意思", "嬌羞", "娇羞", "靦腆",
            "腼腆", "扭捏", "羞", "shy", "blush", "embarrassed", "bashful", "flustered",
            "照れ", "恥ずかしい"),
    "sad": ("難過", "难过", "悲傷", "悲伤", "傷心", "伤心", "失落", "沮喪", "沮丧", "落寞",
            "寂寞", "低落", "哀傷", "哀伤", "sad", "down", "sorrow", "lonely", "depressed",
            "gloomy", "悲しい", "寂しい"),
    "cry": ("流淚", "流泪", "哽咽", "泫然", "眼眶", "哭", "crying", "cry", "tears",
            "tearful", "泣く", "涙"),
    "angry": ("生氣", "生气", "憤怒", "愤怒", "不爽", "火大", "惱火", "恼火", "不悅", "不悦",
              "怒", "angry", "mad", "annoyed", "irritated", "怒り", "怒る"),
    "pout": ("鬧彆扭", "闹别扭", "嘟嘴", "賭氣", "赌气", "不服氣", "不服气", "pout", "sulk",
             "拗ねる"),
    "surprised": ("驚訝", "惊讶", "吃驚", "吃惊", "愣住", "錯愕", "错愕", "嚇到", "吓到",
                  "意外", "愣", "surprised", "shock", "shocked", "startled", "驚き",
                  "びっくり"),
    "scared": ("害怕", "恐懼", "恐惧", "驚恐", "惊恐", "不安", "緊張", "紧张", "慌張", "慌张",
               "慌", "scared", "afraid", "fear", "nervous", "anxious", "panic", "怖い"),
    "worried": ("擔心", "担心", "憂慮", "忧虑", "為難", "为难", "困擾", "困扰", "遲疑", "迟疑",
                "猶豫", "犹豫", "worried", "troubled", "concern", "hesitant", "困る",
                "心配"),
    "serious": ("認真", "认真", "嚴肅", "严肃", "凝重", "堅定", "坚定", "serious",
                "determined", "stern", "真剣"),
    "smug": ("得意", "壞笑", "坏笑", "調皮", "调皮", "捉弄", "smug", "smirk", "teasing",
             "mischief", "playful", "いたずら"),
    "tired": ("疲倦", "疲憊", "疲惫", "困倦", "無力", "无力", "累", "tired", "sleepy",
              "exhausted", "眠い", "疲れ"),
    "thinking": ("思考", "沉思", "若有所思", "thinking", "pensive", "考える"),
    "awkward": ("尷尬", "尴尬", "苦笑", "無奈", "无奈", "awkward", "wry", "苦笑い"),
}

#: When a character has no art for a family, try these neighbours in order.
NEAR: dict[str, tuple[str, ...]] = {
    "smile": ("happy",), "happy": ("smile",), "cry": ("sad",),
    "sad": ("cry", "worried"), "pout": ("angry", "awkward"),
    "angry": ("pout", "serious"), "scared": ("worried", "surprised", "shy", "sad"),
    "worried": ("sad", "scared", "awkward"), "awkward": ("shy", "worried", "smile"),
    "shy": ("awkward", "smile"), "serious": ("neutral",),
    "thinking": ("serious", "neutral"), "tired": ("sad", "neutral"),
    "smug": ("smile", "happy"), "surprised": ("scared", "shy"), "neutral": (),
}

#: Returned as the key when the word means "no particular expression".
DEFAULT_KEY = ""


def normalize(word: str) -> str:
    return _STRIP_RE.sub("", word or "").casefold()


@dataclass
class Resolution:
    """Where an emotion word landed."""

    word: str
    key: str | None           # expression key, DEFAULT_KEY for the base portrait, or None
    source: str               # exact | alias | memo | contains | lexicon | fuzzy | none
    score: float = 1.0

    @property
    def resolved(self) -> bool:
        return self.key is not None

    def to_dict(self) -> dict[str, Any]:
        return {"word": self.word, "key": self.key, "source": self.source,
                "score": round(self.score, 2)}


class ExpressionMemo:
    """The remembered word -> expression table, per character."""

    def __init__(self, data: dict[str, dict[str, str]] | None = None,
                 path: Path | None = None) -> None:
        self.data: dict[str, dict[str, str]] = {
            str(k): {str(w): str(v) for w, v in (words or {}).items()}
            for k, words in (data or {}).items()
        }
        self.path = path
        self.dirty = False

    @classmethod
    def load(cls, path: Path) -> "ExpressionMemo":
        path = Path(path)
        if not path.exists():
            return cls(path=path)
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        if not isinstance(raw, dict):
            raise ValueError(f"{path.name}: expression memo must be a mapping")
        return cls({k: v for k, v in raw.items() if isinstance(v, dict)}, path=path)

    def get(self, character_id: str, word: str) -> str | None:
        return self.data.get(character_id, {}).get(normalize(word))

    def record(self, character_id: str, word: str, key: str) -> None:
        words = self.data.setdefault(character_id, {})
        norm = normalize(word)
        if norm and words.get(norm) != key:
            words[norm] = key
            self.dirty = True

    def to_yaml(self) -> str:
        ordered = {cid: dict(sorted(words.items()))
                   for cid, words in sorted(self.data.items()) if words}
        return dump_yaml(ordered) if ordered else ""

    def save(self) -> Path | None:
        if self.path is None or not self.dirty:
            return None
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(self.to_yaml(), encoding="utf-8")
        self.dirty = False
        return self.path


def _families_of(text: str) -> list[tuple[str, int]]:
    """Emotion families named in ``text`` with the longest matching word length."""
    found: list[tuple[str, int]] = []
    for family, words in LEXICON.items():
        best = 0
        for word in words:
            token = word.casefold()
            if not token:
                continue
            # A single character ("笑", "慌") only counts as the whole word or
            # its last character ("有點慌"). Longer matches win below, so
            # "苦笑" still reads as awkward rather than as a smile.
            if len(token) == 1:
                if text.endswith(token):
                    best = max(best, 1)
                continue
            if token in text:
                best = max(best, len(token))
        if best:
            found.append((family, best))
    return sorted(found, key=lambda item: -item[1])


class ExpressionResolver:
    """Resolve words for one character's set of expressions."""

    def __init__(self, character_id: str, keys: list[str], *,
                 aliases: dict[str, list[str]] | None = None,
                 memo: ExpressionMemo | None = None,
                 default_aliases: tuple[str, ...] = ()) -> None:
        self.character_id = character_id
        self.keys = [str(k) for k in keys]
        self.aliases = {k: [a for a in v if a] for k, v in (aliases or {}).items()
                        if k in self.keys or k == DEFAULT_KEY}
        self.memo = memo
        self._norm_keys = {normalize(k): k for k in self.keys}
        self._alias_index: dict[str, str] = {}
        for key, words in self.aliases.items():
            for word in words:
                self._alias_index.setdefault(normalize(word), key)
        for word in default_aliases:
            self._alias_index.setdefault(normalize(word), DEFAULT_KEY)
        # Which emotion family each key belongs to, from its name and aliases.
        self._key_family: dict[str, str] = {}
        for key in self.keys:
            names = [normalize(key), *(normalize(a) for a in self.aliases.get(key, []))]
            for name in names:
                families = _families_of(name)
                if families:
                    self._key_family[key] = families[0][0]
                    break

    def describe(self) -> dict[str, list[str]]:
        """``{key: aliases}`` for the prompt's expression list."""
        return {key: list(self.aliases.get(key, [])) for key in self.keys}

    def _key_for_family(self, family: str) -> str | None:
        for key, fam in self._key_family.items():
            if fam == family:
                return key
        return None

    def resolve(self, word: str, *, remember: bool = True) -> Resolution:
        norm = normalize(word)
        if not norm:
            return Resolution(word, None, "none", 0.0)
        if norm in self._norm_keys:
            return Resolution(word, self._norm_keys[norm], "exact")
        if norm in self._alias_index:
            return Resolution(word, self._alias_index[norm], "alias")
        if self.memo is not None:
            remembered = self.memo.get(self.character_id, word)
            if remembered is not None and (remembered in self.keys
                                           or remembered == DEFAULT_KEY):
                return Resolution(word, remembered, "memo")

        result = self._guess(word, norm)
        if result.resolved and remember and self.memo is not None:
            self.memo.record(self.character_id, word, result.key or DEFAULT_KEY)
        return result

    def _guess(self, word: str, norm: str) -> Resolution:
        # An alias or key inside the word: "有點害羞" contains "害羞".
        best_key, best_len = None, 0
        for token, key in [*self._alias_index.items(), *self._norm_keys.items()]:
            if len(token) >= 2 and token in norm and len(token) > best_len:
                best_key, best_len = key, len(token)
        if best_key is not None:
            return Resolution(word, best_key, "contains", 0.9)

        for family, _length in _families_of(norm):
            for candidate in (family, *NEAR.get(family, ())):
                key = self._key_for_family(candidate)
                if key is not None:
                    return Resolution(word, key, "lexicon",
                                      0.8 if candidate == family else 0.6)
                if candidate == "neutral":
                    return Resolution(word, DEFAULT_KEY, "lexicon", 0.7)
            if family == "neutral":
                return Resolution(word, DEFAULT_KEY, "lexicon", 0.8)

        pool = [*self._alias_index, *self._norm_keys]
        close = difflib.get_close_matches(norm, pool, n=1, cutoff=0.6)
        if close:
            key = self._alias_index.get(close[0], self._norm_keys.get(close[0]))
            score = difflib.SequenceMatcher(None, norm, close[0]).ratio()
            if key is not None:
                return Resolution(word, key, "fuzzy", score)
        return Resolution(word, None, "none", 0.0)

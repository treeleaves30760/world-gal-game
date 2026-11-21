"""Dependency-free token estimation.

The tavern layer needs token *budgets* (world-book budget, context budget,
per-block sizes in the prompt inspector) long before any provider is involved,
and it must work offline and identically for every backend. So this is an
estimate, not a tokenizer: every CJK character counts as one token (which is
close for Chinese / Japanese / Korean text on current models) and the rest of
the text counts as one token per four characters.

Anything that needs an exact number (billing, hard context limits) should ask
the provider — for Claude that is ``client.messages.count_tokens``. Everything
in this package that shows a number derived from here labels it as an estimate.
"""
from __future__ import annotations

import re

# CJK ideographs, kana, hangul, and full-width punctuation each tend to be a
# token of their own.
_CJK_RE = re.compile(
    "["
    "⺀-⿿"   # CJK radicals / Kangxi
    "　-〿"   # CJK symbols and punctuation
    "぀-ヿ"   # hiragana + katakana
    "㐀-䶿"   # CJK ext A
    "一-鿿"   # CJK unified
    "가-힯"   # hangul syllables
    "豈-﫿"   # CJK compatibility ideographs
    "＀-￯"   # full-width forms
    "]"
)


def estimate_tokens(text: str | None) -> int:
    """Estimate how many tokens ``text`` costs (see module docstring)."""
    if not text:
        return 0
    cjk = len(_CJK_RE.findall(text))
    rest = len(text) - cjk
    # Round the non-CJK remainder up so a short ASCII string never costs zero.
    return cjk + (rest + 3) // 4


def estimate_message_tokens(messages: list[dict]) -> int:
    """Estimate a chat-message list, including a small per-message overhead."""
    total = 0
    for message in messages:
        total += 4  # role / framing overhead, same order as the chat formats
        content = message.get("content")
        if isinstance(content, str):
            total += estimate_tokens(content)
        elif isinstance(content, list):
            for part in content:
                if isinstance(part, dict):
                    total += estimate_tokens(str(part.get("text", "")))
        name = message.get("name")
        if isinstance(name, str):
            total += estimate_tokens(name)
    return total

"""World-book activation: decide which entries enter a prompt, and say why.

A world book (世界書) is a set of background-knowledge snippets that only enter
the prompt when their keys come up in the recent conversation. That keeps a
large setting out of the context until it is relevant, and it is how tavern
front-ends keep long role-plays on-model without resending the whole bible.

:func:`activate` implements the matching rules content authors expect —
primary keys, secondary keys with AND/NOT logic, constant entries,
probability, per-entry scan depth, recursion, inclusion groups and a token
budget — and adds one engine-specific rule: an entry can be gated on game
state with the same ``requires`` / ``forbids`` conditions scenes use, so one
book can follow a route ("only once the letter has been found").

Every candidate entry gets a :class:`LoreDecision` explaining what happened to
it. The prompt inspector prints that log, because "why didn't my entry fire?"
is the question a world-book author asks most.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Callable

from ..core.game_state import seeded_rng
from .card import LoreEntry, Lorebook
from .tokens import estimate_tokens

#: Recursion rounds before we stop following entry-triggers-entry chains.
MAX_RECURSION = 5

_REGEX_KEY_RE = re.compile(r"^/(.+)/([a-zA-Z]*)$", re.DOTALL)
_LATIN_WORD_RE = re.compile(r"^[\w' -]+$", re.ASCII)

# Decision statuses, in the order the inspector explains them.
ACTIVATED = "activated"
DISABLED = "disabled"
STATE_GATED = "state_gated"
NO_KEYS = "no_keys"
NO_MATCH = "no_match"
SECONDARY_FAILED = "secondary_failed"
PROBABILITY_FAILED = "probability_failed"
GROUP_LOST = "group_lost"
BUDGET_EXCEEDED = "budget_exceeded"
EMPTY = "empty_content"


@dataclass
class LoreDecision:
    """What happened to one entry on one activation pass."""

    book: str
    entry: str
    status: str
    detail: str = ""
    key: str = ""               # the key that matched, if any
    position: str = ""
    tokens: int = 0
    recursive: bool = False     # matched via another entry's text

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"book": self.book, "entry": self.entry,
                               "status": self.status}
        if self.detail:
            out["detail"] = self.detail
        if self.key:
            out["key"] = self.key
        if self.position:
            out["position"] = self.position
        if self.tokens:
            out["tokens"] = self.tokens
        if self.recursive:
            out["recursive"] = True
        return out


@dataclass
class _Candidate:
    book: Lorebook
    entry: LoreEntry
    index: int
    matched_key: str = ""
    match_count: int = 0
    recursive: bool = False

    @property
    def label(self) -> str:
        return self.entry.name or self.entry.id or f"#{self.index}"


@dataclass
class LoreActivation:
    """The entries chosen for a prompt, plus the per-entry decision log."""

    entries: list[LoreEntry] = field(default_factory=list)
    log: list[LoreDecision] = field(default_factory=list)
    tokens: int = 0
    budget: int = 0

    def at(self, position: str) -> list[LoreEntry]:
        """Active entries for one position, in insertion order."""
        return sorted((e for e in self.entries if e.position == position),
                      key=lambda e: e.order)

    def text(self, position: str, sep: str = "\n") -> str:
        return sep.join(e.content.strip() for e in self.at(position)
                        if e.content.strip())

    def outlets(self) -> dict[str, str]:
        """``{outlet name: joined content}`` for ``position: outlet`` entries."""
        out: dict[str, list[str]] = {}
        for entry in self.at("outlet"):
            if entry.outlet and entry.content.strip():
                out.setdefault(entry.outlet, []).append(entry.content.strip())
        return {name: "\n".join(parts) for name, parts in out.items()}

    def to_dict(self) -> dict[str, Any]:
        return {
            "tokens": self.tokens,
            "budget": self.budget,
            "active": [e.name or e.id for e in self.entries],
            "log": [d.to_dict() for d in self.log],
        }


def _key_matches(key: str, haystack: str, entry: LoreEntry) -> bool:
    """Whether one key matches the scan text under the entry's options."""
    key = key.strip()
    if not key:
        return False
    regex = _REGEX_KEY_RE.match(key)
    if regex:
        flags = 0
        for flag in regex.group(2):
            if flag == "i":
                flags |= re.IGNORECASE
            elif flag == "s":
                flags |= re.DOTALL
            elif flag == "m":
                flags |= re.MULTILINE
        try:
            return re.search(regex.group(1), haystack, flags) is not None
        except re.error:
            return False
    text = haystack if entry.case_sensitive else haystack.casefold()
    needle = key if entry.case_sensitive else key.casefold()
    if entry.whole_word and _LATIN_WORD_RE.match(key):
        # Whole-word only means something for space-delimited scripts.
        return re.search(r"(?<![\w])" + re.escape(needle) + r"(?![\w])", text) is not None
    return needle in text


def _match(entry: LoreEntry, haystack: str) -> tuple[str, int, str]:
    """Return ``(status, match_count, matched_key)`` for one entry."""
    hits = [key for key in entry.keys if _key_matches(key, haystack, entry)]
    if not hits:
        return NO_MATCH, 0, ""
    if entry.secondary_keys:
        second = [key for key in entry.secondary_keys
                  if _key_matches(key, haystack, entry)]
        total = len(entry.secondary_keys)
        ok = {
            "and_any": bool(second),
            "and_all": len(second) == total,
            "not_any": not second,
            "not_all": len(second) < total,
        }[entry.logic]
        if not ok:
            return SECONDARY_FAILED, len(hits), hits[0]
        return ACTIVATED, len(hits) + len(second), hits[0]
    return ACTIVATED, len(hits), hits[0]


def _state_gate(entry: LoreEntry, state: Any) -> str:
    """Return "" when the entry's state gate passes, else a short reason."""
    if state is None or not (entry.requires or entry.forbids):
        return ""
    try:
        for cond in entry.requires:
            if not state.evaluate(cond):
                return f"requires {cond.kind}:{cond.target}"
        for cond in entry.forbids:
            if state.evaluate(cond):
                return f"forbids {cond.kind}:{cond.target}"
    except Exception as exc:  # a broken condition must not break prompt building
        return f"condition error: {exc}"
    return ""


def activate(
    books: list[Lorebook],
    *,
    messages: list[str],
    state: Any = None,
    rng: Any = None,
    budget: int | None = None,
    extra_text: str = "",
    expand: Callable[[str], str] | None = None,
) -> LoreActivation:
    """Choose the world-book entries for a prompt.

    ``messages`` is the conversation, oldest first; each entry scans the last
    ``scan_depth`` of them (its own override, else its book's). ``extra_text``
    is always scanned as well — the stage passes the scene premise and the
    current direction, so entries can fire before anyone has spoken.
    ``budget`` overrides the books' own token budgets (``0`` = unlimited).
    ``expand`` is applied to the content of entries that make it in (macro
    expansion), so skipped entries never run their macros.
    """
    # No generator supplied: stay reproducible rather than draw from entropy.
    rng = rng or seeded_rng(0)
    result = LoreActivation()
    candidates: list[_Candidate] = []
    decided: dict[int, LoreDecision] = {}

    def decide(cand: _Candidate, status: str, detail: str = "") -> None:
        decided[id(cand)] = LoreDecision(
            book=cand.book.name, entry=cand.label, status=status, detail=detail,
            key=cand.matched_key, position=cand.entry.position,
            recursive=cand.recursive,
        )

    def scan_text(cand: _Candidate, recursion_text: str = "") -> str:
        depth = cand.entry.scan_depth
        if depth is None:
            depth = cand.book.scan_depth
        recent = messages[-depth:] if depth > 0 else []
        parts = list(recent)
        if extra_text:
            parts.append(extra_text)
        if recursion_text:
            parts.append(recursion_text)
        return "\n".join(parts)

    index = 0
    for book in books:
        for entry in book.entries:
            candidates.append(_Candidate(book=book, entry=entry, index=index))
            index += 1

    pending: list[_Candidate] = []
    for cand in candidates:
        entry = cand.entry
        if not entry.enabled:
            decide(cand, DISABLED)
            continue
        gate = _state_gate(entry, state)
        if gate:
            decide(cand, STATE_GATED, gate)
            continue
        if not entry.content.strip():
            decide(cand, EMPTY)
            continue
        if not entry.constant and not entry.keys:
            decide(cand, NO_KEYS)
            continue
        pending.append(cand)

    active: list[_Candidate] = []

    def try_activate(cand: _Candidate, recursion_text: str = "") -> bool:
        entry = cand.entry
        if entry.constant:
            cand.match_count = max(cand.match_count, 1)
        else:
            status, count, key = _match(entry, scan_text(cand, recursion_text))
            if status != ACTIVATED:
                # Keep the most informative failure, but stay retryable: a
                # later recursion round may still match this entry.
                decide(cand, status, f"matched '{key}' but secondary keys failed"
                       if status == SECONDARY_FAILED else "")
                return False
            cand.matched_key, cand.match_count = key, count
            cand.recursive = bool(recursion_text) and not _match(
                entry, scan_text(cand))[0] == ACTIVATED
        if entry.probability < 100 and rng.random() * 100 >= entry.probability:
            decide(cand, PROBABILITY_FAILED, f"{entry.probability}% roll missed")
            return True     # rolled once; do not roll again in later rounds
        active.append(cand)
        return True

    remaining: list[_Candidate] = []
    for cand in pending:
        if not try_activate(cand):
            remaining.append(cand)

    # Recursion: text of entries that fired may trigger further entries.
    any_recursive = any(book.recursive for book in books)
    rounds = 0
    seen_active = 0
    while any_recursive and remaining and rounds < MAX_RECURSION:
        fresh = active[seen_active:]
        seen_active = len(active)
        recursion_text = "\n".join(
            c.entry.content for c in fresh
            if c.book.recursive and not c.entry.prevent_recursion)
        if not recursion_text.strip():
            break
        rounds += 1
        still: list[_Candidate] = []
        for cand in remaining:
            if (not cand.book.recursive or cand.entry.exclude_recursion
                    or not try_activate(cand, recursion_text)):
                still.append(cand)
        remaining = still

    # Inclusion groups: one winner per group (most matches, then order).
    groups: dict[str, list[_Candidate]] = {}
    for cand in active:
        if cand.entry.group:
            groups.setdefault(cand.entry.group, []).append(cand)
    losers: set[int] = set()
    for name, members in groups.items():
        if len(members) < 2:
            continue
        winner = max(members, key=lambda c: (c.match_count, c.entry.order, -c.index))
        for cand in members:
            if cand is not winner:
                losers.add(id(cand))
                decide(cand, GROUP_LOST, f"group '{name}' kept '{winner.label}'")
    active = [c for c in active if id(c) not in losers]

    # Token budget: constants first, then by priority / order.
    if budget is None:
        budgets = [book.token_budget for book in books]
        budget = 0 if (not budgets or 0 in budgets) else max(budgets)
    result.budget = budget

    def budget_rank(cand: _Candidate) -> tuple[int, int, int]:
        entry = cand.entry
        weight = entry.priority if entry.priority is not None else entry.order
        return (0 if entry.constant else 1, -weight, cand.index)

    used = 0
    kept: list[_Candidate] = []
    for cand in sorted(active, key=budget_rank):
        content = cand.entry.content
        if expand is not None:
            content = expand(content)
        cost = estimate_tokens(content)
        if budget and used + cost > budget:
            decide(cand, BUDGET_EXCEEDED, f"needs {cost}, {max(0, budget - used)} left")
            continue
        used += cost
        entry = cand.entry if content == cand.entry.content \
            else cand.entry.model_copy(update={"content": content})
        kept.append(_Candidate(book=cand.book, entry=entry, index=cand.index,
                               matched_key=cand.matched_key,
                               match_count=cand.match_count, recursive=cand.recursive))
        decision = LoreDecision(
            book=cand.book.name, entry=cand.label, status=ACTIVATED,
            detail="constant" if cand.entry.constant else "",
            key=cand.matched_key, position=cand.entry.position, tokens=cost,
            recursive=cand.recursive,
        )
        decided[id(cand)] = decision

    result.entries = [c.entry for c in sorted(kept, key=lambda c: (c.entry.order, c.index))]
    result.tokens = used
    result.log = [decided[id(c)] for c in candidates if id(c) in decided]
    return result

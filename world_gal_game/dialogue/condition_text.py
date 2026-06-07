"""Human-readable rendering of a :class:`~world_gal_game.core.story_graph.Condition`.

A locked choice (its ``requires`` fails or a ``forbids`` hits) is illegible if
the UI only greys it out: the player cannot tell *why* it is locked or how close
they are. This module turns the structured condition data the affordances /
dialogue path already computes into one concise Chinese phrase — e.g.::

    affection_gte target=qingyi value=40  ->  "需要 與林青衣的好感度 ≥ 40"
    has_item      target=charm  value=2   ->  "需要 護身符 ×2"
    not_flag      target=angered_her      ->  "需要 尚未發生：angered_her"

It is **read-only and engine-safe**: every lookup degrades to the raw id when a
name is missing, and any unexpected shape falls back to a generic phrase, so a
bare pack (or a headless run with no NPC registry) still produces a string and
never raises. Builtin condition kinds get bespoke phrasing; unknown / plugin
kinds get a readable generic form so a custom gate is still legible.

The functions take a :class:`GameState` only for id→name resolution (NPC
registry, item registry, resource definitions, chapter manifest). Passing
``None`` for state is allowed — it just keeps the raw ids.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..core.game_state import GameState
    from ..core.story_graph import Condition


# Prefix every requirement phrase with this so the reason reads as a gate.
_NEED = "需要 "


def _character_name(state: "GameState | None", cid: str) -> str:
    """Resolve a character id to its display name, else the id itself."""
    if not cid:
        return cid
    if state is None:
        return cid
    try:
        registry = state.meta.get("__npc_registry__")
        if registry is not None:
            npc = registry.get(cid) if hasattr(registry, "get") else None
            if npc is not None and getattr(npc, "name", None):
                return npc.name
    except Exception:
        pass
    return cid


def _item_name(state: "GameState | None", iid: str) -> str:
    if not iid or state is None:
        return iid
    try:
        item = state.items.get(iid)
        if item is not None and getattr(item, "name", None):
            return item.name
    except Exception:
        pass
    return iid


def _resource_name(state: "GameState | None", rid: str) -> str:
    if not rid or state is None:
        return rid
    try:
        d = state.resources.definition(rid)
        if d is not None and getattr(d, "name", None):
            return d.name
    except Exception:
        pass
    return rid


def _chapter_title(state: "GameState | None", chid: str) -> str:
    if not chid or state is None:
        return chid
    try:
        manifest = state.meta.get("__chapters__")
        if manifest is not None:
            for c in getattr(manifest, "chapters", []) or []:
                if getattr(c, "id", None) == chid and getattr(c, "title", None):
                    return c.title
    except Exception:
        pass
    return chid


def _axis_label(stat: str | None) -> str:
    """Human label for an affection axis ('affection' -> '好感度')."""
    s = stat or "affection"
    return {"affection": "好感度", "trust": "信任", "fear": "恐懼"}.get(s, s)


def _as_list(value) -> list:
    if isinstance(value, list):
        return value
    if value is None:
        return []
    return [value]


# Time-of-day ids the engine ships (mirrors TimeOfDay); unknown ids pass through.
_TOD_LABELS = {
    "dawn": "清晨", "morning": "早上", "noon": "中午", "afternoon": "下午",
    "evening": "傍晚", "night": "夜晚", "midnight": "深夜",
}


def describe_condition(cond: "Condition", state: "GameState | None" = None,
                       *, negated: bool = False) -> str:
    """Return one concise Chinese phrase describing what ``cond`` requires.

    ``negated=True`` renders the condition as something that must NOT hold (used
    for a choice's ``forbids`` list). Builtin kinds get bespoke wording; any
    other kind gets a readable generic ``需要 條件「kind」`` form. Never raises.
    """
    try:
        return _describe_condition_inner(cond, state, negated=negated)
    except Exception:
        kind = getattr(cond, "kind", "?")
        return f"{_NEED}條件「{kind}」"


def _describe_condition_inner(cond: "Condition", state: "GameState | None",
                              *, negated: bool) -> str:
    kind = cond.kind
    target = cond.target or ""
    value = cond.value
    stat = cond.stat

    # ---- affection -------------------------------------------------------
    if kind == "affection_gte":
        who = _character_name(state, target)
        return f"{_NEED}與{who}的{_axis_label(stat)} ≥ {value}"
    if kind == "affection_lt":
        who = _character_name(state, target)
        return f"{_NEED}與{who}的{_axis_label(stat)} < {value}"

    # ---- flags -----------------------------------------------------------
    if kind == "flag":
        # A bare flag-truthy gate. When negated (it sits in forbids) it means
        # "this must not have happened yet".
        if negated:
            return f"{_NEED}尚未：{target}"
        return f"{_NEED}已達成：{target}"
    if kind == "not_flag":
        return f"{_NEED}尚未：{target}"
    if kind == "flag_eq":
        return f"{_NEED}{target} = {value}"

    # ---- resources -------------------------------------------------------
    if kind == "resource_gte":
        return f"{_NEED}{_resource_name(state, target)} ≥ {value}"
    if kind == "resource_lt":
        return f"{_NEED}{_resource_name(state, target)} < {value}"
    if kind == "resource_eq":
        return f"{_NEED}{_resource_name(state, target)} = {value}"

    # ---- inventory / achievements ---------------------------------------
    if kind == "has_item":
        n = int(value) if value is not None else 1
        suffix = f" ×{n}" if n != 1 else ""
        return f"{_NEED}{_item_name(state, target)}{suffix}"
    if kind == "achievement":
        return f"{_NEED}成就：{target}"

    # ---- time / location / scene ----------------------------------------
    if kind == "time_in":
        vals = [_TOD_LABELS.get(str(v), str(v)) for v in _as_list(value)]
        return f"{_NEED}時間為 {('、'.join(vals)) or '?'}"
    if kind == "visited":
        return f"{_NEED}曾造訪：{target}"
    if kind == "scene_played":
        return f"{_NEED}已體驗劇情：{target}"

    # ---- chapters --------------------------------------------------------
    if kind == "in_chapter":
        vals = [_chapter_title(state, str(v)) for v in _as_list(value)]
        return f"{_NEED}章節為 {('、'.join(vals)) or '?'}"
    if kind == "chapter_at_or_after":
        return f"{_NEED}進度達到「{_chapter_title(state, target)}」"

    # ---- NG+ clear data --------------------------------------------------
    if kind == "cleared_ending":
        return f"{_NEED}曾達成結局：{target}"
    if kind == "cleared_route":
        return f"{_NEED}曾通關路線：{target}"

    # ---- quests ----------------------------------------------------------
    if kind == "quest_active":
        return f"{_NEED}任務進行中：{target}"
    if kind == "quest_completed":
        return f"{_NEED}完成任務：{target}"
    if kind == "objective_completed":
        return f"{_NEED}完成目標：{stat or target}"

    # ---- generic fallback (plugin / unknown kinds) ----------------------
    bits = [kind]
    if target:
        bits.append(target)
    if value is not None:
        bits.append(str(value))
    body = " ".join(bits)
    if negated:
        return f"{_NEED}非（{body}）"
    return f"{_NEED}條件「{body}」"


def choice_lock_reasons(requires, forbids,
                        state: "GameState | None" = None) -> list[str]:
    """Human-readable reasons a choice is locked, given its unmet gates.

    ``requires`` / ``forbids`` are the lists of :class:`Condition` that are
    currently *failing* (a require that is unmet, or a forbid that is hit). The
    caller selects which conditions failed (the engine already evaluates them);
    this only renders. Each reason is one concise phrase. Never raises.
    """
    out: list[str] = []
    for c in requires or []:
        out.append(describe_condition(c, state, negated=False))
    for c in forbids or []:
        out.append(describe_condition(c, state, negated=True))
    return out


def summarize_lock(requires, forbids, state: "GameState | None" = None,
                   *, max_reasons: int = 2) -> str:
    """A single short line summarising why a choice is locked.

    Joins up to ``max_reasons`` reasons with a comma; if more gates fail it
    appends an ellipsis so the menu line stays compact. Empty string when no
    gate failed (the choice is not actually locked). Never raises.
    """
    reasons = choice_lock_reasons(requires, forbids, state)
    if not reasons:
        return ""
    shown = reasons[:max_reasons]
    text = "、".join(shown)
    if len(reasons) > max_reasons:
        text += " …"
    return text

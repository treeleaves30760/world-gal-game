"""Turn a tavern chat log into a scene.

Tavern front-ends save a conversation as a ``.jsonl`` file: one metadata line,
then one JSON object per message (``name``, ``is_user``, ``mes`` …). A good
role-play session is, in effect, an already-performed scene, so this module
reads such a log and produces a scene payload for ``PackEditor.add_scene``:

- each message is split into lines with the reply parser (quoted speech,
  narration, ``*actions*``, directive tags);
- a speaker whose name matches one of the pack's characters gets that
  character's name plate and has its emotion words resolved to portraits;
- the user's messages become the protagonist's lines.

Nothing is rewritten: the log is restructured, not edited. Run the result
through ``world-gal-game tavern lint`` — chat logs carry their model's habits.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .assets import AssetIndex
from .expressions import ExpressionResolver
from .pack import TavernPack
from .reply import parse_reply

_QUOTES = ("「", "『", "“", '"')


def read_chat(path: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Return ``(metadata, messages)`` from a tavern ``.jsonl`` chat log."""
    meta: dict[str, Any] = {}
    messages: list[dict[str, Any]] = []
    for number, raw in enumerate(Path(path).read_text(encoding="utf-8").splitlines()):
        raw = raw.strip()
        if not raw:
            continue
        try:
            obj = json.loads(raw)
        except ValueError as exc:
            raise ValueError(f"{Path(path).name}: line {number + 1} is not JSON") from exc
        if not isinstance(obj, dict):
            continue
        if "mes" in obj:
            messages.append(obj)
        elif not meta:
            meta = obj
    return meta, messages


def scene_from_chat(path: Path, *, scene_id: str, tavern: TavernPack, npcs: Any = None,
                    assets: AssetIndex | None = None, title: str = "",
                    location: str | None = None) -> tuple[dict[str, Any], list[str]]:
    """Build a scene payload from a chat log. Returns ``(scene, warnings)``."""
    meta, messages = read_chat(path)
    style = tavern.style
    warnings: list[str] = []
    user_name = str(meta.get("user_name") or "")
    known = [card.name for card in tavern.cards.values()]
    resolvers: dict[str, ExpressionResolver | None] = {}
    last_expression: dict[str, str | None] = {}

    def resolver_for(card_id: str) -> ExpressionResolver | None:
        if card_id not in resolvers:
            npc = npcs.get(card_id) if npcs is not None else None
            keys = list(getattr(npc, "portrait_set", {}) or {})
            card = tavern.cards.get(card_id)
            resolvers[card_id] = ExpressionResolver(
                card_id, keys, aliases=card.expression_aliases if card else None,
                memo=tavern.memo) if keys else None
        return resolvers[card_id]

    lines: list[dict[str, Any]] = []
    unknown: set[str] = set()
    for message in messages:
        if message.get("is_system"):
            continue
        text = str(message.get("mes") or "")
        if not text.strip():
            continue
        name = str(message.get("name") or "")
        is_user = bool(message.get("is_user"))
        if is_user:
            user_name = user_name or name
            speaker_label = tavern.persona.speaker or name
            card_id = ""
        else:
            card = tavern.card(name)
            card_id = card.id if card is not None else ""
            speaker_label = card.name if card is not None else name
            if card is None and name:
                unknown.add(name)
        has_quotes = any(q in text for q in _QUOTES)
        parsed = parse_reply(text, default_speaker=name or None,
                             known_speakers=[*known, name, user_name], style=style,
                             bare_is_speech=not has_quotes)
        resolver = resolver_for(card_id) if card_id else None
        for item in parsed.lines:
            if item.kind == "narration":
                line: dict[str, Any] = {"text": item.text}
            else:
                line = {"speaker": speaker_label,
                        "text": f"{style.quote_open}{item.text}{style.quote_close}"}
                if resolver is not None:
                    key = last_expression.get(card_id)
                    if item.sprite:
                        resolution = resolver.resolve(item.sprite)
                        if resolution.resolved:
                            key = resolution.key
                    if key:
                        line["expression"] = key
                    last_expression[card_id] = key
            if assets is not None:
                effects = []
                if item.bg:
                    asset = assets.resolve("bg", item.bg)
                    if asset is not None:
                        effects.append({"kind": "set_background", "target": asset.path})
                if item.bgm:
                    asset = assets.resolve("bgm", item.bgm)
                    if asset is not None:
                        line["bgm"] = asset.path
                if item.cg == "off":
                    effects.append({"kind": "hide_cg"})
                elif item.cg:
                    asset = assets.resolve("cg", item.cg)
                    if asset is not None:
                        line["cg"] = asset.path
                if effects:
                    line["effects"] = effects
            lines.append(line)
    if unknown:
        warnings.append("speakers with no matching character (kept as written): "
                        + ", ".join(sorted(unknown)))
    if not lines:
        warnings.append("the chat log produced no lines")
    scene: dict[str, Any] = {"id": scene_id}
    if title:
        scene["title"] = title
    if location:
        scene["location"] = location
    scene["lines"] = lines
    return scene, warnings

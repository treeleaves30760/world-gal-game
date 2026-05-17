"""Text interpolation: expand {token} placeholders in dialogue strings.

Tokens are expanded against the current GameState. Unknown tokens are left
as-is so authors notice typos visually rather than getting silent blanks.
"""
from __future__ import annotations

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .game_state import GameState

# Matches {anything_without_braces}
_TOKEN_RE = re.compile(r"\{([^{}]+)\}")


def interpolate(text: str, state: "GameState") -> str:
    """Replace {var} tokens in text. Unknown tokens stay literal.

    Supported tokens:
      {player_name}            -> state.player.name
      {state.flag.NAME}        -> state.events.get_flag(NAME) (missing -> "")
      {resource.ID}            -> state.resources.get(ID)
      {affection.NPC_ID}       -> state.affection.get(NPC_ID)
      {affection.NPC_ID.label} -> affection level label

    Unknown tokens stay literal so authors notice typos visually.
    """

    def _expand(match: re.Match[str]) -> str:
        token = match.group(1)
        result = _resolve(token, state)
        # None means the token was not recognised — return it verbatim.
        if result is None:
            return match.group(0)
        return str(result)

    return _TOKEN_RE.sub(_expand, text)


def _resolve(token: str, state: "GameState") -> str | int | None:
    """Return the resolved value for a single token, or None if unrecognised."""

    if token == "player_name":
        return state.player.name

    if token.startswith("state.flag."):
        flag_name = token[len("state.flag."):]
        val = state.events.get_flag(flag_name, "")
        return "" if val is False else str(val)

    if token.startswith("resource."):
        resource_id = token[len("resource."):]
        return state.resources.get(resource_id)

    if token.startswith("affection."):
        rest = token[len("affection."):]
        # {affection.NPC_ID.label}
        if rest.endswith(".label"):
            npc_id = rest[: -len(".label")]
            return state.affection.level_label(npc_id)
        # {affection.NPC_ID}
        return state.affection.get(rest)

    return None

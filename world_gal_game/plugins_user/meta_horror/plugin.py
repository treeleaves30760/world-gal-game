"""meta_horror — fourth-wall / meta-narrative toolkit (the DDLC seam).

Bundled, web-safe ops that let a character reach *outside* the story: glitch the
screen, address the player by their real account name, and corrupt the text
itself. Built for ghost-story / psychological packs where the engine is part of
the horror. Everything degrades gracefully and touches no files or network.

Inline dialogue ops (use inside a line's ``text:``):

- ``[[glitch]]`` / ``[[glitch:1.4]]`` — a corruption burst (screen shake + a
  magenta→cyan chromatic flash). The optional arg scales intensity (default 1).
- ``[[whoami]]`` — replaced by the player's *real* OS account name (falls back
  to the in-game name). "I know who you are."
- ``[[corrupt:那是青衣的聲音]]`` — replaced by a glitch-corrupted version of the
  given text (deterministic, so a rollback replays identically).

Effect form (use in a line/scene ``effects:`` list):

- ``glitch`` with ``value: {intensity: 1.5}`` — the same corruption burst.
"""
from __future__ import annotations

import os

from world_gal_game.plugins import effect, dialogue_op

# The per-frame visual-fx channel the dialogue scene drains and turns into
# shake/flash/tint. We only append plain JSON-able dicts — no rendering here.
try:
    from world_gal_game.plugins.builtin_effects import VISUAL_FX_QUEUE
except Exception:                      # pragma: no cover - defensive
    VISUAL_FX_QUEUE = "__visual_fx__"

# Glitch glyphs used to corrupt text (block/box-drawing chars present in most
# fonts, so no tofu). Chosen deterministically by character code.
_GLITCH_CHARS = "▓▒░█▚▞╳⌁"


def _queue_glitch(state, intensity: float) -> None:
    amt = max(0.1, min(3.0, float(intensity)))
    q = state.meta.setdefault(VISUAL_FX_QUEUE, [])
    # Shake + two offset colour flashes = a chromatic-aberration "corruption".
    # All are self-expiring (no tint/blur left persisting), so no cleanup.
    q.append({"fx": "screen_shake", "intensity": 24.0 * amt, "duration": 0.36})
    q.append({"fx": "screen_flash", "color": [190, 30, 95],
              "duration": 0.16, "max_alpha": int(min(160, 110 * amt))})
    q.append({"fx": "screen_flash", "color": [30, 200, 205],
              "duration": 0.26, "max_alpha": int(min(120, 75 * amt))})


@dialogue_op("glitch", description="Corruption burst (shake + chromatic flash); "
                                   "optional arg scales intensity.")
def op_glitch(state, arg: str) -> str:
    try:
        intensity = float(arg) if arg.strip() else 1.0
    except ValueError:
        intensity = 1.0
    _queue_glitch(state, intensity)
    return ""   # the directive renders nothing


@dialogue_op("whoami", description="Inject the player's real OS account name "
                                   "(falls back to the in-game name).")
def op_whoami(state, arg: str) -> str:
    name = os.environ.get("USER") or os.environ.get("USERNAME")
    if not name:
        try:
            name = os.getlogin()
        except Exception:
            name = None
    if not name:
        try:
            name = state.player.name
        except Exception:
            name = "你"
    return str(name)


@dialogue_op("corrupt", description="Glitch-corrupt the given text "
                                    "(deterministic).")
def op_corrupt(state, arg: str) -> str:
    out = []
    for i, ch in enumerate(arg):
        if ch.strip() and (i % 3 == 2):
            out.append(_GLITCH_CHARS[(ord(ch) + i) % len(_GLITCH_CHARS)])
        else:
            out.append(ch)
    return "".join(out)


@effect("glitch", plugin_id="meta_horror",
        description="Corruption burst (shake + chromatic flash). "
                    "value: {intensity: float}.",
        signature={"value": "dict {intensity:float?}"})
def fx_glitch(state, eff) -> dict:
    val = getattr(eff, "value", None)
    if isinstance(val, dict):
        intensity = val.get("intensity", 1.0)
    elif isinstance(val, (int, float)):
        intensity = val
    else:
        intensity = 1.0
    _queue_glitch(state, intensity)
    return {"kind": getattr(eff, "kind", "glitch"), "intensity": intensity}

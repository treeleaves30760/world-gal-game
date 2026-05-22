"""Steam achievement mirroring hook.

Registers a single :data:`HookEvent.EFFECT_AFTER_APPLY` handler that, after
every effect, diffs the engine's unlocked achievements against what's
already been pushed to Steam and unlocks the difference. Because effects are
the only thing that can unlock an achievement (``AchievementTracker.check``
runs inside ``GameState.apply_all``), this is the right and complete seam.

**Inert without a bridge.** The handler reads a live
:class:`~world_gal_game.integrations.steam_bridge.SteamBridge` from
``state.meta["__steam_bridge__"]`` (a transient, ``__``-prefixed key the
SaveManager strips on serialise). When no bridge is parked there — desktop
without Steam, headless, tests, the web — the handler returns immediately,
so importing this module and firing the hook are both byte-for-byte no-ops
for everyone who isn't shipping on Steam.

Importing this module registers the hook globally (idempotently). The app
imports it only when Steam is enabled; the registry de-dupes, so a stray
import elsewhere is harmless.
"""
from __future__ import annotations

from ..plugins import hook, HookEvent

# Transient meta key under which the running app parks its SteamBridge.
STEAM_BRIDGE_META_KEY = "__steam_bridge__"


def _bridge_from_state(state):
    """Return the SteamBridge parked on the state, or None."""
    if state is None or not hasattr(state, "meta"):
        return None
    return state.meta.get(STEAM_BRIDGE_META_KEY)


@hook(HookEvent.EFFECT_AFTER_APPLY, plugin_id="steam",
      description="Mirror newly-unlocked engine achievements onto Steam.")
def push_achievements(ctx, eff=None, result=None) -> None:
    """Push any engine achievements not yet on Steam. No-op without a bridge."""
    bridge = _bridge_from_state(ctx.state)
    if bridge is None:
        return
    achievements = getattr(ctx.state, "achievements", None)
    if achievements is None:
        return
    unlocked = getattr(achievements, "unlocked", None)
    if not unlocked:
        return
    # ``unlocked`` is {id: timestamp}; push the ids. The bridge tracks its
    # own ``_pushed`` set, so this is naturally a diff — repeats are cheap.
    bridge.push_unlocked(list(unlocked.keys()))

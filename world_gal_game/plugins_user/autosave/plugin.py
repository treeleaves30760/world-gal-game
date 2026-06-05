"""autosave — bundled plugin that silently writes rotating autosaves.

Fires on :data:`HookEvent.DIALOGUE_CHOICE_MADE` (and
:data:`HookEvent.TIME_ADVANCE`) and writes the live :class:`GameState` to
a rotating slot ``autosave_1`` .. ``autosave_N`` where ``N`` is
``config.autosave_slot_count``. Skipped entirely when
``config.autosave_enabled`` is False.

Where the config + save dir come from
-------------------------------------
The :class:`PluginContext` handed to hooks is built by ``content_loader``
*without* an ``EngineConfig`` (``ctx.config`` is ``None`` there) and it
carries no save directory. Rather than touch the loader or the dialogue
scene, ``GalGameApp.__init__`` parks a small bridge dict on
``state.meta["__autosave_config__"]`` holding the live config and the
resolved save dir. The double-underscore prefix means :class:`SaveManager`
strips it from serialised saves, so it never bloats a save file. This hook
reads that bridge; if it is absent (e.g. a bare headless load with no app),
autosave is simply a no-op.

The plugin keeps its own private rotation cursor under
``state.meta["__plugin:autosave__"]`` — also a double-underscore key, so it
is filtered from saves (the next-slot pointer is intentionally transient).

Safety
------
Hook handlers are already wrapped in ``isolate()`` by the engine, but the
whole body here is additionally guarded so a save failure (disk full, no
pygame surface, etc.) can never bubble up or interrupt play.
"""
from __future__ import annotations

import logging

from world_gal_game.plugins import hook, HookEvent

_log = logging.getLogger("world_gal_game.plugins.autosave")

# Bridge key set by GalGameApp.__init__ — holds {"config": EngineConfig,
# "save_dir": Path, "get_screen": Callable|None}. Private (``__``) so it is
# stripped from saves.
BRIDGE_KEY = "__autosave_config__"
# Private rotation cursor (also stripped from saves).
_STATE_KEY = "__plugin:autosave__"


def _cursor(state) -> dict:
    """Return (creating if missing) this plugin's private rotation slot."""
    slot = state.meta.get(_STATE_KEY)
    if not isinstance(slot, dict):
        slot = {"next": 1}
        state.meta[_STATE_KEY] = slot
    return slot


def _write_autosave(state) -> None:
    """Serialise ``state`` to the next rotating autosave slot.

    Fully defensive: any failure is logged and swallowed.
    """
    try:
        bridge = state.meta.get(BRIDGE_KEY)
        if not isinstance(bridge, dict):
            return  # no app bridge → nothing to do (e.g. bare headless load)
        config = bridge.get("config")
        save_dir = bridge.get("save_dir")
        if config is None or save_dir is None:
            return
        if not getattr(config, "autosave_enabled", False):
            return
        count = int(getattr(config, "autosave_slot_count", 0) or 0)
        if count <= 0:
            return

        cur = _cursor(state)
        n = cur.get("next", 1)
        if n < 1 or n > count:
            n = 1
        slot = f"autosave_{n}"
        # Advance the cursor (wrap), so successive saves rotate.
        cur["next"] = (n % count) + 1

        from world_gal_game.core.save_manager import SaveManager

        sm = SaveManager(save_dir)
        # mode='json' so sets/tuples serialise to JSON-friendly types and
        # the save round-trips back into GameState (mirrors save_scene).
        payload = state.model_dump(mode="json")

        # Build a human label/summary like the manual save path does. The save
        # card shows a 自動 badge + the summary line separately, so the label is
        # a clean slot title (the protagonist's name), not a repeat of summary.
        loc = state.map.current
        try:
            summary = f"{state.time.label()} · {(loc.name if loc else '無位置')}"
        except Exception:
            summary = ""
        label = (getattr(state.player, "name", "") or "自動存檔").strip()

        # Optional thumbnail from the current screen, if the app exposed one.
        thumbnail = None
        get_screen = bridge.get("get_screen")
        if callable(get_screen):
            try:
                thumbnail = get_screen()
            except Exception:
                thumbnail = None

        # Let plugins patch the payload before write (mirrors save_scene).
        from world_gal_game.plugins import fire_event
        fire_event(state, HookEvent.SAVE_BEFORE_SERIALIZE,
                   slot=slot, payload=payload)

        sm.save(
            slot,
            payload,
            label=label,
            summary=summary,
            thumbnail=thumbnail,
            pack_meta=state.meta.get("__pack_meta__", {}),
        )
    except Exception as exc:  # never let an autosave failure crash the game
        _log.warning("autosave skipped: %s", exc)


@hook(HookEvent.DIALOGUE_CHOICE_MADE,
      description="Write a rotating autosave after each dialogue choice.")
def on_choice_made(ctx, scene_id=None, choice_id=None):
    if ctx.state is None:
        return
    _write_autosave(ctx.state)


@hook(HookEvent.TIME_ADVANCE,
      description="Write a rotating autosave after time advances.")
def on_time_advance(ctx, phases=None, day=None, time_of_day=None):
    if ctx.state is None:
        return
    _write_autosave(ctx.state)

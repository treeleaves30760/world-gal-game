"""PluginContext — the service bag passed to every plugin hook callback.

A plugin's hook handlers receive a single :class:`PluginContext` as
their first argument plus event-specific keyword payload. The context
exposes the running :class:`GameState`, pack metadata, the plugin
registries, and (optionally) the :class:`PluginManager` that loaded
the plugin.

The class is intentionally a thin facade: it doesn't add behaviour,
just bundles the references hooks need so they don't have to import
through module paths. This makes hooks easy to unit-test (instantiate
:class:`PluginContext` with mock objects) and keeps the public surface
small.

This module also defines the :class:`HookEvent` constants. The engine
fires events using the string values; plugins subscribe using
``@hook(HookEvent.EFFECT_AFTER_APPLY)`` (or the bare string, since
``HookEvent.EFFECT_AFTER_APPLY == "effect.after_apply"``).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from ..config import EngineConfig
    from ..core.game_state import GameState
    from .manager import PluginManager


# ----------------------------------------------------------------------
# Event names

class HookEvent:
    """Engine lifecycle events that plugins can hook into.

    These are plain strings — declared as class attributes for IDE
    autocompletion and as a one-stop reference. Supported events:

    ============================== ==========================================
    Event                          Fired when
    ============================== ==========================================
    PACK_BEFORE_LOAD               content_loader starts reading a pack
    PACK_AFTER_LOAD                content_loader has finished, state ready
    GAME_STATE_READY               GameState is constructed and pre-stocked
    EFFECT_BEFORE_APPLY            GameState.apply, before the handler runs
    EFFECT_AFTER_APPLY             GameState.apply, after the handler returns
    SAVE_BEFORE_SERIALIZE          SaveManager is about to model_dump
    SAVE_AFTER_LOAD                SaveManager has finished restoring state
    SCENE_PUSH                     SceneManager pushed a Scene
    SCENE_POP                      SceneManager popped a Scene
    SCENE_REPLACE                  SceneManager replaced the bottom Scene
    DIALOGUE_BEFORE_LINE           DialogueEngine about to present a line
    DIALOGUE_AFTER_LINE            DialogueEngine just presented a line
    DIALOGUE_CHOICE_MADE           player picked a choice
    PLAYER_MOVE                    player successfully moved to a location
    TIME_ADVANCE                   TimeSystem advanced by N phases
    APP_FRAME                      App main loop — fires once per rendered frame
    ============================== ==========================================

    Event payloads (kwargs passed alongside ``ctx``):

    - PACK_BEFORE_LOAD:    pack_root (Path)
    - PACK_AFTER_LOAD:     pack_root (Path), meta (dict)
    - GAME_STATE_READY:    (none — read state via ctx.state)
    - EFFECT_BEFORE_APPLY: eff (Effect)
    - EFFECT_AFTER_APPLY:  eff (Effect), result (dict)
    - SAVE_BEFORE_SERIALIZE: slot (str | None), payload (dict)
    - SAVE_AFTER_LOAD:     slot (str | None), payload (dict)
    - SCENE_PUSH:          scene (Scene), kwargs (dict)
    - SCENE_POP:           scene (Scene)        # scene that was popped
    - SCENE_REPLACE:       old (Scene | None), new (Scene)
    - DIALOGUE_BEFORE_LINE: scene_id (str), line_index (int)
    - DIALOGUE_AFTER_LINE: scene_id (str), line_index (int), line (LinePresentation)
    - DIALOGUE_CHOICE_MADE: scene_id (str), choice_id (str)
    - PLAYER_MOVE:         from_location (str | None), to_location (str)
    - TIME_ADVANCE:        phases (int), day (int), time_of_day (str)
    - APP_FRAME:           dt (float)
    """

    PACK_BEFORE_LOAD = "pack.before_load"
    PACK_AFTER_LOAD = "pack.after_load"
    GAME_STATE_READY = "game.state_ready"
    EFFECT_BEFORE_APPLY = "effect.before_apply"
    EFFECT_AFTER_APPLY = "effect.after_apply"
    SAVE_BEFORE_SERIALIZE = "save.before_serialize"
    SAVE_AFTER_LOAD = "save.after_load"
    SCENE_PUSH = "scene.push"
    SCENE_POP = "scene.pop"
    SCENE_REPLACE = "scene.replace"
    DIALOGUE_BEFORE_LINE = "dialogue.before_line"
    DIALOGUE_AFTER_LINE = "dialogue.after_line"
    DIALOGUE_CHOICE_MADE = "dialogue.choice_made"
    PLAYER_MOVE = "player.move"
    TIME_ADVANCE = "time.advance"
    APP_FRAME = "app.frame"

    @classmethod
    def all(cls) -> list[str]:
        """Return every defined event name. Useful for capability manifest."""
        return [
            cls.PACK_BEFORE_LOAD,
            cls.PACK_AFTER_LOAD,
            cls.GAME_STATE_READY,
            cls.EFFECT_BEFORE_APPLY,
            cls.EFFECT_AFTER_APPLY,
            cls.SAVE_BEFORE_SERIALIZE,
            cls.SAVE_AFTER_LOAD,
            cls.SCENE_PUSH,
            cls.SCENE_POP,
            cls.SCENE_REPLACE,
            cls.DIALOGUE_BEFORE_LINE,
            cls.DIALOGUE_AFTER_LINE,
            cls.DIALOGUE_CHOICE_MADE,
            cls.PLAYER_MOVE,
            cls.TIME_ADVANCE,
            cls.APP_FRAME,
        ]


# ----------------------------------------------------------------------
# PluginContext


@dataclass
class PluginContext:
    """Service bag handed to plugin hook callbacks.

    Plugins read from this; they should not write to ``meta`` or
    ``state`` unless their declared extension explicitly permits it.
    The engine creates one context per active pack and re-uses it
    across hook fires within that pack's lifetime.
    """

    # The running GameState. May be ``None`` for very early events
    # (``pack.before_load``) where the state hasn't been built yet.
    state: "GameState | None" = None
    # Raw meta dict from meta.yaml; lets plugins read user config.
    meta: dict[str, Any] = field(default_factory=dict)
    # Absolute path to the pack root, useful when a plugin needs to
    # resolve pack-local assets.
    pack_root: Path | None = None
    # The PluginManager that owns this context — exposes other loaded
    # plugins for plugins that want to coordinate.
    manager: "PluginManager | None" = None
    # Engine config (Engine-wide settings). Optional because some
    # callers (tests, low-level loaders) don't have one.
    config: "EngineConfig | None" = None
    # Per-context arbitrary scratch dict. Plugins should namespace keys
    # by their plugin id, e.g. ctx.scratch["step_counter:warmed_up"] = True
    scratch: dict[str, Any] = field(default_factory=dict)
    # Dedicated logger so plugin output is easy to filter.
    log: logging.Logger = field(
        default_factory=lambda: logging.getLogger("world_gal_game.plugins.ctx")
    )

    # ------------------------------------------------------------------
    # Convenience accessors

    def get_pack_id(self) -> str | None:
        """Return the pack id from meta, if declared."""
        v = self.meta.get("id") if isinstance(self.meta, dict) else None
        return str(v) if v is not None else None

    def get_plugin_state(self, plugin_id: str) -> dict[str, Any]:
        """Return (creating if missing) the per-plugin meta dict.

        Plugins store their own private state under a double-underscore
        key in ``GameState.meta`` so the SaveManager filters it out
        on serialise. Convention: ``state.meta["__plugin:<id>__"]``.
        """
        if self.state is None:
            return {}
        key = f"__plugin:{plugin_id}__"
        slot = self.state.meta.get(key)
        if not isinstance(slot, dict):
            slot = {}
            self.state.meta[key] = slot
        return slot

    def fire(self, event: str, **kwargs: Any) -> list:
        """Re-fire a hook event using this context (convenience).

        Mostly useful for plugins that want to expose their own
        sub-events (e.g. a quest plugin firing "quest.complete").
        """
        from .registry import HOOK_REGISTRY
        return HOOK_REGISTRY.fire(event, self, **kwargs)

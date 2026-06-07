"""Scene base class + manager.

A Scene is one "screen" of the game: title, exploration, dialogue, etc.
The SceneManager owns a stack so overlays (map, affection, log, save,
chat) can be pushed on top of the active scene and popped without losing
the underlying state.

SceneContext is the shared bag of services every scene needs.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pygame

if TYPE_CHECKING:
    from ..config import EngineConfig
    from ..core.game_state import GameState
    from ..core.localization import Localization
    from ..dialogue.dialogue_engine import DialogueEngine
    from ..npc.llm_brain import LLMBrain
    from ..npc.npc_base import NPCRegistry
    from ..ui.assets import AssetManager
    from ..ui.fonts import FontRegistry
    from ..ui.theme import Theme


@dataclass
class SceneContext:
    config: "EngineConfig"
    state: "GameState"
    npcs: "NPCRegistry"
    brain: "LLMBrain"
    dialogue: "DialogueEngine"
    assets: "AssetManager"
    fonts: "FontRegistry"
    theme: "Theme"
    localization: "Localization"
    screen_size: tuple[int, int] = (1280, 720)
    # The parsed pack meta.yaml dict and the pack identifier, so data-driven
    # scenes (e.g. the credits overlay) can read pack-supplied content + locate
    # the pack on disk. Both default empty so existing constructors (and tests)
    # that don't pass them keep working.
    meta: dict[str, Any] = field(default_factory=dict)
    pack: str = ""

    def t(self, key: str, default: str | None = None, **fmt) -> str:
        """Shortcut for scenes that need a localized UI string."""
        return self.localization.t(key, default, **fmt)

    def pack_root(self) -> "Path | None":
        """Best-effort on-disk root of the running pack (the dir holding
        ``content/``), or None if it can't be resolved.

        Prefers the :class:`AssetManager`'s resolved root (always correct for
        the live pack, including ``--pack <path>``); falls back to the config's
        pack resolver. Never raises — a scene that can't find files degrades to
        the meta/engine-default content.
        """
        root = getattr(self.assets, "_pack_root", None)
        if root is not None:
            return Path(root)
        try:
            return self.config.pack_root(self.pack or None)
        except Exception:
            return None


class Scene:
    """Base for all screens. Subclasses override the lifecycle hooks."""

    def __init__(self, ctx: SceneContext):
        self.ctx = ctx
        # Hint to the SceneManager whether the scene below should still draw.
        self.is_overlay: bool = False

    def enter(self, **kwargs) -> None:
        """Called when scene becomes active."""
        pass

    def exit(self) -> None:
        """Called when scene is popped/replaced."""
        pass

    def resume(self) -> None:
        """Called when an overlay above this scene is popped."""
        pass

    def pause(self) -> None:
        """Called when an overlay is pushed on top of this scene."""
        pass

    def handle_event(self, event: pygame.event.Event) -> None:
        pass

    def update(self, dt: float, inp) -> None:
        pass

    def draw(self, surface: pygame.Surface) -> None:
        pass

    def describe(self) -> dict:
        """Headless-mode dump: return a JSON-able description of state."""
        return {"scene": type(self).__name__}


class SceneManager:
    """A small stack-based scene manager.

    - push(scene): overlay
    - pop(): close current overlay
    - replace(scene): swap the bottom of the stack
    - The top of the stack receives input; everything below draws if it's an overlay scenario.
    """

    def __init__(self):
        self._stack: list[Scene] = []
        self._pending: list[tuple[str, Scene | None, dict]] = []
        # Cross-scene transition: snapshot the outgoing frame and dissolve it
        # out over the incoming scene, so title→game and overlay open/close
        # animate instead of hard-cutting. Set by the app; stays inert in
        # headless/tests until a frame has actually been drawn.
        self._transition = None
        self._last_frame = None
        self.transitions_enabled: bool = True

    # ---- mutation queue (so scenes can switch in their own update) ----
    def replace(self, scene: Scene, **kwargs) -> None:
        self._pending.append(("replace", scene, kwargs))

    def push(self, scene: Scene, **kwargs) -> None:
        self._pending.append(("push", scene, kwargs))

    def pop(self) -> None:
        self._pending.append(("pop", None, {}))

    def clear_to(self, scene: Scene, **kwargs) -> None:
        self._pending.append(("clear_to", scene, kwargs))

    # ---- queries ----
    @property
    def current(self) -> Scene | None:
        return self._stack[-1] if self._stack else None

    def stack(self) -> list[Scene]:
        return list(self._stack)

    # ---- per-frame work ----
    def commit_pending(self) -> None:
        # Lazy import to avoid pulling in the plugins package at module
        # import time (scenes.base sits below plugins in the dep graph).
        from ..plugins import fire_event
        from ..plugins.context import HookEvent

        changed = bool(self._pending)
        for op, scene, kwargs in self._pending:
            if op == "replace":
                old = self._stack[-1] if self._stack else None
                if old is not None:
                    old.exit()
                    self._stack.pop()
                if scene is not None:
                    self._stack.append(scene)
                    scene.enter(**kwargs)
                    fire_event(self._state_of(scene),
                               HookEvent.SCENE_REPLACE, old=old, new=scene)
            elif op == "push":
                if self._stack:
                    self._stack[-1].pause()
                if scene is not None:
                    self._stack.append(scene)
                    scene.enter(**kwargs)
                    fire_event(self._state_of(scene),
                               HookEvent.SCENE_PUSH, scene=scene,
                               kwargs=kwargs)
            elif op == "pop":
                if self._stack:
                    popped = self._stack[-1]
                    popped.exit()
                    self._stack.pop()
                    fire_event(self._state_of(popped),
                               HookEvent.SCENE_POP, scene=popped)
                if self._stack:
                    self._stack[-1].resume()
            elif op == "clear_to":
                while self._stack:
                    self._stack[-1].exit()
                    self._stack.pop()
                if scene is not None:
                    self._stack.append(scene)
                    scene.enter(**kwargs)
                    fire_event(self._state_of(scene),
                               HookEvent.SCENE_REPLACE, old=None, new=scene)
        self._pending.clear()
        # Start a dissolve from the just-departed frame over the new scene.
        if changed and self.transitions_enabled and self._last_frame is not None:
            from ..ui.transitions import SceneTransition
            self._transition = SceneTransition(
                self._last_frame.copy(), style="dissolve", duration=0.3)

    @staticmethod
    def _state_of(scene: Scene | None):
        """Best-effort look-up of the GameState reachable from a Scene."""
        if scene is None:
            return None
        ctx = getattr(scene, "ctx", None)
        return getattr(ctx, "state", None) if ctx is not None else None

    def update(self, dt: float, inp) -> None:
        self.commit_pending()
        if self._transition is not None:
            self._transition.update(dt)
            if self._transition.done:
                self._transition = None
        if self._stack:
            self._stack[-1].update(dt, inp)
        self.commit_pending()

    def draw(self, surface: pygame.Surface) -> None:
        if not self._stack:
            return
        # Find the deepest fully-opaque scene (the last non-overlay scene
        # from the top). Draw from there upward.
        bottom_idx = len(self._stack) - 1
        while bottom_idx > 0 and self._stack[bottom_idx].is_overlay:
            bottom_idx -= 1
        for s in self._stack[bottom_idx:]:
            s.draw(surface)
        # Snapshot the clean frame for the NEXT scene-change dissolve, then
        # overlay the current (outgoing) transition on top of the new scene.
        if self.transitions_enabled:
            self._last_frame = surface.copy()
        if self._transition is not None and not self._transition.done:
            self._transition.draw(surface)

    def describe(self) -> list[dict]:
        return [s.describe() for s in self._stack]

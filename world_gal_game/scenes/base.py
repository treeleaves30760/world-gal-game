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

from ..ui.motion import FULL_MOTION, MotionKind, MotionPolicy
from ..ui.presentation_text import PresentationTextPolicy
from ..ui.visual import VisualPolicy, VisualTheme

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
    from ..ui.system_chrome import SystemChromeHost


@dataclass
class SceneContext:
    config: "EngineConfig"
    state: "GameState"
    npcs: "NPCRegistry"
    brain: "LLMBrain"
    dialogue: "DialogueEngine"
    assets: "AssetManager"
    fonts: "FontRegistry"
    theme: "Theme | VisualTheme"
    localization: "Localization"
    screen_size: tuple[int, int] = (1280, 720)
    presentation_text: PresentationTextPolicy = field(
        default_factory=PresentationTextPolicy,
        repr=False,
        compare=False,
    )
    # The parsed pack meta.yaml dict and the pack identifier, so data-driven
    # scenes (e.g. the credits overlay) can read pack-supplied content + locate
    # the pack on disk. Both default empty so existing constructors (and tests)
    # that don't pass them keep working.
    meta: dict[str, Any] = field(default_factory=dict)
    pack: str = ""
    # Optional pack-plugin renderer for system-screen chrome.  The engine
    # continues to own layout, text, focus, hit targets, and input; a missing
    # or failed renderer therefore falls back to the stock UI.
    ui_chrome: "SystemChromeHost | None" = None
    # Live visual accessibility policy.  The theme is wrapped in a
    # ``VisualTheme`` during post-init so widgets that retain it still observe
    # a Settings profile switch on their next draw.
    visual: VisualPolicy | None = field(
        default=None,
        repr=False,
        compare=False,
    )
    # Live accessibility policy shared by every scene/widget.  When omitted,
    # it observes config.reduce_motion dynamically so an already-constructed
    # scene reacts after Settings closes.
    motion: MotionPolicy = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        self.motion = MotionPolicy.from_config(self.config)
        if self.visual is None:
            self.visual = VisualPolicy.from_config(self.config)
        if not isinstance(self.theme, VisualTheme):
            self.theme = VisualTheme(self.theme, self.visual)

    def t(self, key: str, default: str | None = None, **fmt) -> str:
        """Shortcut for scenes that need a localized UI string."""
        return self.presentation_text.apply(
            self.localization.t(key, default, **fmt),
        )

    def story(
        self,
        key: str,
        source_text: str,
        *,
        scene_id: str | None = None,
        **fmt: Any,
    ) -> str:
        """Resolve authored copy and apply the display-only audit policy."""
        return self.presentation_text.apply(
            self.localization.story(
                key,
                source_text,
                scene_id=scene_id,
                **fmt,
            ),
        )

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

    def __init__(self, *, motion: MotionPolicy | None = None):
        self._stack: list[Scene] = []
        self._pending: list[tuple[str, Scene | None, dict]] = []
        self.motion = motion or FULL_MOTION
        # Cross-scene transition: snapshot the outgoing frame and dissolve it
        # out over the incoming scene, so title→game and overlay open/close
        # animate instead of hard-cutting. Set by the app; stays inert in
        # headless/tests until a frame has actually been drawn.
        self._transition = None
        self._last_frame = None
        self.transitions_enabled: bool = True
        # A clear-to operation can make its whole queued mutation batch a
        # hard cut.  This is intentionally batch-scoped: callers commonly
        # rebuild a base scene and then push a restored overlay before the
        # next commit, and neither mutation may dissolve from the abandoned
        # framebuffer.
        self._pending_transition_cut: bool = False

    # ---- mutation queue (so scenes can switch in their own update) ----
    def replace(self, scene: Scene, **kwargs) -> None:
        self._pending.append(("replace", scene, kwargs))

    def push(self, scene: Scene, **kwargs) -> None:
        self._pending.append(("push", scene, kwargs))

    def pop(self) -> None:
        self._pending.append(("pop", None, {}))

    def clear_to(
        self,
        scene: Scene,
        *,
        transition: bool = True,
        **kwargs,
    ) -> None:
        """Replace the full stack, optionally hard-cutting this batch.

        ``transition=False`` suppresses the generic cross-scene transition
        for every mutation committed alongside this clear.  It also discards
        any active transition and its last-frame source so a later transition
        cannot reuse a framebuffer that no longer belongs to the scene stack.
        """
        self._pending.append(("clear_to", scene, kwargs))
        if not transition:
            self._pending_transition_cut = True

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
        transition_cut = self._pending_transition_cut
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
        # ``enter``/hook callbacks may enqueue more work while this list is
        # being consumed. Python's list iterator includes those mutations, so
        # include a hard-cut request raised by any of them in the same batch.
        transition_cut = transition_cut or self._pending_transition_cut
        self._pending.clear()
        self._pending_transition_cut = False
        if transition_cut:
            self._transition = None
            self._last_frame = None
        # Start a dissolve from the just-departed frame over the new scene.
        elif (changed and self.transitions_enabled
              and self._last_frame is not None):
            from ..ui.transitions import SceneTransition
            style, duration = self.motion.transition("dissolve", 0.3)
            self._transition = (
                None
                if style == "cut" or duration <= 0.0
                else SceneTransition(
                    self._last_frame.copy(),
                    style=style,
                    duration=duration,
                )
            )

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
            # A live Settings toggle must affect the already-running hand-off,
            # not only the next scene change.
            if self.motion.reduced:
                remaining_cap = self.motion.duration(
                    0.3,
                    MotionKind.OPACITY,
                )
                self._transition.duration = min(
                    self._transition.duration,
                    self._transition.t + remaining_cap,
                )
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

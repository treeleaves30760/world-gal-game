"""Scene replay overlay (WP-3C).

Lists the scenes the player has completed (``state.read_log.scenes``) and
lets them re-watch any one in a **read-only sandbox**: a throwaway copy of
the live ``GameState`` is played through the regular :class:`DialogueScene`
so the on-screen result is byte-identical to the first viewing, yet nothing
the replay does (flags, affection, read-log, autosave) ever reaches the
live save.

How the sandbox is isolated
---------------------------
``build_sandbox_context`` rebuilds the state by round-tripping it through
JSON: ``GameState(**ctx.state.model_dump(mode="json"))``. ``model_dump``
runs ``GameState._serialize_meta``, which drops every transient ``__``
meta bridge — including ``__plugin_manager__`` and ``__autosave_config__``.
So the sandbox state has:

* no plugin manager → effect hooks (autosave's ``DIALOGUE_CHOICE_MADE``
  handler, Steam, etc.) never fire during replay;
* no autosave bridge → even if a hook somehow ran, it could not reach the
  live config / save dir / screen-grab;
* its own copies of every subsystem → ``set_flag`` / ``adjust_affection``
  and friends mutate only the sandbox.

A fresh :class:`DialogueEngine` is bound to that sandbox state (matching how
``app.py`` constructs it, including the optional brain-supplied
``llm_provider``), and a sandbox :class:`SceneContext` is produced with
``dataclasses.replace`` so the heavyweight, read-only services (assets,
fonts, theme, localization, npcs, config) are shared while ``state`` and
``dialogue`` point at the sandbox. The live context is never mutated.

Rather than pushing onto the global :class:`SceneManager` (overlays have no
handle to it, and we must not edit ``app.py``), this overlay *owns* the
replay :class:`DialogueScene`: it instantiates it on the sandbox context,
drives its ``enter`` / ``update`` / ``draw`` directly, and drops it when the
scene signals completion via its ``on_done`` callback. The sandbox is then
simply discarded.
"""
from __future__ import annotations

import dataclasses

import pygame

from .base import Scene, SceneContext
from .dialogue_scene import DialogueScene
from ..core.game_state import GameState
from ..dialogue.dialogue_engine import DialogueEngine
from ..ui.widgets import Button, Panel, ScrollArea


def build_sandbox_context(ctx: SceneContext) -> SceneContext:
    """Return a read-only sandbox :class:`SceneContext` for replay.

    The returned context shares ``ctx``'s assets / fonts / theme /
    localization / npcs / config but carries a deep, isolated copy of the
    game state and a fresh dialogue engine bound to it. Mutating the sandbox
    state can never touch the live save — see the module docstring for why
    the JSON round-trip is the isolation boundary.
    """
    # Round-trip through JSON. model_dump strips the transient "__" meta
    # bridges (plugin manager, autosave config, ...), so the sandbox can
    # neither fire lifecycle hooks nor reach the live autosave machinery.
    sandbox_state = GameState(**ctx.state.model_dump(mode="json"))
    # Match how app.py builds the engine: forward the brain's llm_provider
    # when the brain exposes one, otherwise None (the headless default).
    llm_provider = getattr(ctx.brain, "llm_provider", None)
    sandbox_engine = DialogueEngine(sandbox_state, llm_provider=llm_provider)
    return dataclasses.replace(
        ctx, state=sandbox_state, dialogue=sandbox_engine,
    )


class SceneReplayScene(Scene):
    """Overlay: pick a completed scene and replay it in a sandbox."""

    def __init__(self, ctx: SceneContext):
        super().__init__(ctx)
        self.is_overlay = True
        # (rect, scene_id) hit-tests, rebuilt every draw so clicks always
        # match the currently-visible row layout (mimics shop_scene).
        self._row_rects: list[tuple[pygame.Rect, str]] = []
        # The child DialogueScene playing the sandbox, plus its context. Both
        # are None when we're showing the list; set while replaying.
        self._replay_scene: DialogueScene | None = None
        self._replay_ctx: SceneContext | None = None
        self._replay_scene_id: str | None = None

    # ---- lifecycle ------------------------------------------------------
    def enter(self, *, on_close=None, **_) -> None:
        self.on_close = on_close
        sw, sh = self.ctx.screen_size
        self._panel_rect = pygame.Rect(120, 60, sw - 240, sh - 120)
        self._panel = Panel(self._panel_rect, self.ctx.theme,
                            fill=(*self.ctx.theme.bg_overlay[:3], 235),
                            border=self.ctx.theme.border_strong,
                            radius=self.ctx.theme.radius_l, border_width=2)
        self.close_btn = Button(
            pygame.Rect(self._panel_rect.right - 120 - 16,
                        self._panel_rect.y + 16, 120, 36),
            self.ctx.localization.t("close", "關閉"),
            fonts=self.ctx.fonts, theme=self.ctx.theme,
            font_size=15, style="ghost",
            on_click=(lambda: on_close() if on_close else None),
        )
        self._scroll = ScrollArea(
            pygame.Rect(self._panel_rect.x + 30, self._panel_rect.y + 70,
                        self._panel_rect.width - 60,
                        self._panel_rect.height - 100),
            fonts=self.ctx.fonts, theme=self.ctx.theme,
        )
        self._scroll.set_drawer(self._draw_content)

    def exit(self) -> None:
        # Tear down any in-flight replay so its sandbox can be collected.
        self._end_replay()

    def _end_replay(self) -> None:
        if self._replay_scene is not None:
            self._replay_scene.exit()
        self._replay_scene = None
        self._replay_ctx = None
        self._replay_scene_id = None

    # ---- replayable-scene model ----------------------------------------
    def _replayable(self) -> list[tuple[str, str]]:
        """``(scene_id, title)`` for every completed, still-known scene.

        Sorted by title for a stable list. ``read_log.scenes`` may name a
        scene the current pack no longer defines (e.g. after a content
        edit); such ids are skipped because they cannot be replayed.
        """
        story = self.ctx.state.story
        out: list[tuple[str, str]] = []
        for sid in self.ctx.state.read_log.scenes:
            scene = story.get(sid)
            if scene is None:
                continue
            out.append((sid, scene.title or sid))
        out.sort(key=lambda pair: (pair[1], pair[0]))
        return out

    # ---- list rendering -------------------------------------------------
    def _draw_content(self, surface: pygame.Surface) -> int:
        theme = self.ctx.theme
        rows = self._replayable()
        if not rows:
            empty = self.ctx.fonts.render(
                "尚無可回想的場景", 18, theme.text_mute)
            surface.blit(empty, (0, 0))
            return empty.get_height()

        w = surface.get_width() - 14
        card_h = 64
        y = 0
        for sid, title in rows:
            card = pygame.Surface((w, card_h), pygame.SRCALPHA)
            pygame.draw.rect(card, (*theme.accent[:3], 50), card.get_rect(),
                             border_radius=theme.radius_m)
            pygame.draw.rect(card, theme.border, card.get_rect(), width=1,
                             border_radius=theme.radius_m)
            # sigil chip
            chip = pygame.Surface((44, 44), pygame.SRCALPHA)
            pygame.draw.rect(chip, (*theme.accent[:3], 70), chip.get_rect(),
                             border_radius=theme.radius_s)
            pygame.draw.rect(chip, (*theme.accent[:3], 220), chip.get_rect(),
                             width=2, border_radius=theme.radius_s)
            glyph = self.ctx.fonts.render("回", 22, theme.accent, bold=True)
            chip.blit(glyph, ((44 - glyph.get_width()) // 2,
                              (44 - glyph.get_height()) // 2))
            card.blit(chip, (12, 10))
            t = self.ctx.fonts.render(title, 20, theme.text, bold=True)
            card.blit(t, (70, 10))
            sub = self.ctx.fonts.render(sid, 13, theme.text_mute)
            card.blit(sub, (70, 36))
            hint = self.ctx.fonts.render(
                self.ctx.localization.t("scene_replay", "場景重溫"),
                13, theme.text_dim)
            card.blit(hint, (w - hint.get_width() - 14, 24))
            surface.blit(card, (0, y))
            # Remember the on-screen hit rect (account for scroll offset).
            screen_y = self._scroll.rect.y + y - self._scroll.scroll_y
            self._row_rects.append(
                (pygame.Rect(self._scroll.rect.x, screen_y, w, card_h), sid))
            y += card_h + 8
        return y

    # ---- replay ---------------------------------------------------------
    def _start_replay(self, scene_id: str) -> None:
        """Play ``scene_id`` against a sandbox state, in an owned child scene.

        Nothing on the live ``ctx.state`` changes: the child DialogueScene
        runs on an isolated sandbox context (see the module docstring). When
        the scene ends it calls our ``on_done`` callback, which clears the
        replay and discards the sandbox.
        """
        if self.ctx.state.story.get(scene_id) is None:
            return  # defensively ignore a stale / unknown id
        sandbox_ctx = build_sandbox_context(self.ctx)
        scene = DialogueScene(sandbox_ctx)
        self._replay_ctx = sandbox_ctx
        self._replay_scene = scene
        self._replay_scene_id = scene_id
        # DialogueScene.enter(scene_id=..., on_done=...) starts the scene and
        # invokes on_done when it ends. We reuse it verbatim (no edits) and
        # drive its update/draw from our own loop while it's active.
        scene.enter(scene_id=scene_id, on_done=self._end_replay)

    # ---- input / draw ---------------------------------------------------
    def update(self, dt: float, inp) -> None:
        # While a replay is active, delegate fully to the child scene. Esc /
        # advancing inside it is the DialogueScene's own concern; finishing
        # the scene fires our on_done (-> _end_replay) and drops back here.
        if self._replay_scene is not None:
            self._replay_scene.update(dt, inp)
            return
        if inp.cancel and self.on_close:
            self.on_close()
            return
        self.close_btn.update(dt, inp)
        self._scroll.update(dt, inp)
        if inp.mouse_clicked:
            for rect, sid in self._row_rects:
                if rect.collidepoint(inp.mouse_pos):
                    self._start_replay(sid)
                    return

    def draw(self, surface: pygame.Surface) -> None:
        if self._replay_scene is not None:
            # The child scene paints the full frame (bg/CG/portraits/box).
            self._replay_scene.draw(surface)
            return
        # Reset hit-rects each frame so clicks match the visible layout.
        self._row_rects = []
        veil = pygame.Surface(surface.get_size(), pygame.SRCALPHA)
        veil.fill((0, 0, 0, 160))
        surface.blit(veil, (0, 0))
        self._panel.draw(surface)
        title = self.ctx.fonts.render(
            self.ctx.localization.t("scene_replay", "場景重溫"),
            self.ctx.config.font_size_header,
            self.ctx.theme.accent, bold=True,
        )
        surface.blit(title, (self._panel_rect.x + 32, self._panel_rect.y + 28))
        rows = self._replayable()
        if rows:
            cnt = self.ctx.fonts.render(
                f"{len(rows)}", 18, self.ctx.theme.accent_warm)
            surface.blit(cnt, (self._panel_rect.right - 60,
                               self._panel_rect.y + 32))
        self.close_btn.draw(surface)
        self._scroll.draw(surface)

    def describe(self) -> dict:
        return {
            "scene": "SceneReplayScene",
            "replayable": [sid for sid, _title in self._replayable()],
            "replaying": self._replay_scene is not None,
            "replay_scene_id": self._replay_scene_id,
        }

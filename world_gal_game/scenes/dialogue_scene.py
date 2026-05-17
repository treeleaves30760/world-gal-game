"""Dialogue runner: drives the engine.dialogue.DialogueEngine in pygame."""
from __future__ import annotations

from typing import Callable

import pygame

from .base import Scene, SceneContext
from ..ui.widgets import DialogueBox, ChoiceMenu, PortraitView
from ..ui.transitions import PortraitCrossfade, BackgroundFade
from ..core.portrait_spec import PortraitSpec


class DialogueScene(Scene):
    def __init__(self, ctx: SceneContext):
        super().__init__(ctx)
        self.is_overlay = True   # drawn over a darkened background
        self.box: DialogueBox | None = None
        self.choices: ChoiceMenu | None = None
        self.portrait: PortraitView | None = None
        self.cg_surface_path: str | None = None
        self.background_path: str | None = None
        self.scene_id: str | None = None
        self.on_done: Callable[[], None] | None = None
        self._current_line = None
        self._scene_started = False
        self._pending_choices = False
        # Skip / auto-play state
        self.auto_play_enabled: bool = False
        self._auto_play_timer: float = 0.0

        # Portrait state: per-slot surface + active crossfade.
        # Slots: "left", "center", "right"
        self._slot_surfaces: dict[str, pygame.Surface | None] = {
            "left": None, "center": None, "right": None,
        }
        self._slot_fades: dict[str, PortraitCrossfade | None] = {
            "left": None, "center": None, "right": None,
        }

        # Background transition.
        self._bg_surface: pygame.Surface | None = None
        self._bg_fade: BackgroundFade | None = None

    def enter(self, *, scene_id: str | None = None,
              on_done: Callable[[], None] | None = None,
              on_scrollback: Callable[[], None] | None = None,
              **_) -> None:
        self.scene_id = scene_id
        self.on_done = on_done
        self.on_scrollback = on_scrollback
        sw, sh = self.ctx.screen_size
        box_h = 230
        margin = 32
        self.box = DialogueBox(
            pygame.Rect(margin, sh - box_h - margin,
                        sw - margin * 2, box_h),
            fonts=self.ctx.fonts, theme=self.ctx.theme,
            text_speed=self.ctx.config.text_speed,
        )
        self.choices = ChoiceMenu(
            pygame.Rect(0, 0, sw, sh),
            fonts=self.ctx.fonts, theme=self.ctx.theme,
            on_choose=self._on_choose,
        )
        self.choices.visible = False
        # Portrait position: above the dialogue box, centered.
        portrait_h = sh - box_h - margin - 60
        portrait_w = 480
        self.portrait = PortraitView(
            pygame.Rect(sw // 2 - portrait_w // 2, 30,
                        portrait_w, portrait_h),
            self.ctx.assets, side="center",
        )
        if scene_id is not None:
            self._start(scene_id)

    def _start(self, scene_id: str) -> None:
        pres = self.ctx.dialogue.start_scene(scene_id)
        self._scene_started = True
        self._render_presentation(pres)

    def _on_choose(self, choice_id: str) -> None:
        pres = self.ctx.dialogue.choose(choice_id)
        self._render_presentation(pres)

    def _advance(self) -> None:
        pres = self.ctx.dialogue.next_line()
        self._render_presentation(pres)

    # ------------------------------------------------------------------
    # Portrait helpers
    # ------------------------------------------------------------------

    def _surface_for_portrait(self, portrait: str | PortraitSpec | None,
                               speaker: str | None,
                               expression: str | None) -> pygame.Surface | None:
        """Resolve a portrait field (str path, spec, or None) to a Surface."""
        sw, sh = self.ctx.screen_size
        portrait_h = sh - 230 - 32 - 60   # same as enter()
        fallback = (480, portrait_h)
        if isinstance(portrait, PortraitSpec):
            return self.ctx.assets.resolve_portrait(portrait, fallback_size=fallback)
        if isinstance(portrait, str):
            return self.ctx.assets.image(portrait, fallback_size=fallback)
        # Fall back to NPC default.
        npc = self.ctx.npcs.get(speaker) if speaker else None
        if npc is not None:
            path = npc.portrait_for(expression)
            return self.ctx.assets.image(path, fallback_size=fallback)
        return None

    def _start_portrait_fade(self, slot: str, new_surf: pygame.Surface | None) -> None:
        """Begin a crossfade on a slot from current surface to new_surf."""
        old = self._slot_surfaces.get(slot)
        if old is new_surf:
            return
        self._slot_fades[slot] = PortraitCrossfade(old, new_surf, duration=0.25)
        self._slot_surfaces[slot] = new_surf

    def _update_portraits(self, line) -> None:
        """Compute which slots change this line and start fades."""
        if line.portraits:
            # Multi-slot: clear all then populate from the spec list.
            wanted: dict[str, pygame.Surface | None] = {
                "left": None, "center": None, "right": None,
            }
            for spec in line.portraits:
                surf = self.ctx.assets.resolve_portrait(spec)
                wanted[spec.slot] = surf
            for slot, surf in wanted.items():
                self._start_portrait_fade(slot, surf)
        else:
            # Single-portrait / legacy path goes to center; clear other slots.
            center_surf = self._surface_for_portrait(
                line.portrait, line.speaker, line.expression)
            self._start_portrait_fade("center", center_surf)
            self._start_portrait_fade("left", None)
            self._start_portrait_fade("right", None)

    # ------------------------------------------------------------------
    # Background helper
    # ------------------------------------------------------------------

    def _update_background(self, new_path: str) -> None:
        """Start a BackgroundFade when the background changes."""
        if new_path == self.background_path:
            return
        sw, sh = self.ctx.screen_size
        old_surf = self._bg_surface
        new_surf = self.ctx.assets.scaled(new_path, (sw, sh), fit="cover")
        self._bg_fade = BackgroundFade(old_surf, new_surf, duration=0.6)
        self._bg_surface = new_surf
        self.background_path = new_path

    # ------------------------------------------------------------------

    def _render_presentation(self, pres) -> None:
        if pres is None:
            return
        if pres.background:
            self._update_background(pres.background)
        if pres.kind == "line":
            line = pres.line
            self._current_line = line
            self.box.set_line(line.speaker, line.text)
            # SFX/BGM
            if line.bgm:
                self.ctx.assets.play_music(line.bgm)
            if line.sfx:
                self.ctx.assets.play_sound(line.sfx)
            # CG
            self.cg_surface_path = line.cg
            # Portrait (multi-slot or single)
            self._update_portraits(line)
            # Legacy single PortraitView still used for NPC fallback display
            # when no portraits/portrait field is set; sync it too.
            if line.portrait or line.portraits:
                self.portrait.show(None)   # suppress legacy widget
            else:
                npc = self.ctx.npcs.get(line.speaker) if line.speaker else None
                if npc is not None:
                    self.portrait.show(npc.portrait_for(line.expression))
                else:
                    self.portrait.show(None)
            self.choices.visible = False
        elif pres.kind == "choice":
            self.choices.set_choices(
                [(c.id, c.text, c.enabled) for c in pres.choices])
            self.choices.visible = True
        elif pres.kind == "transition":
            if pres.next_scene:
                # Recursively render the first line of the chained scene
                # rather than discarding it.
                next_pres = self.ctx.dialogue.start_scene(pres.next_scene)
                self._render_presentation(next_pres)
            else:
                self._end()
        elif pres.kind == "end":
            self._end()

    def _end(self) -> None:
        if self.on_done is not None:
            cb = self.on_done
            self.on_done = None
            cb()

    # ------------------------------------------------------------------
    # Skip / auto-play helpers

    def _toggle_auto_play(self) -> None:
        self.auto_play_enabled = not self.auto_play_enabled
        self._auto_play_timer = 0.0

    def _trigger_skip(self) -> None:
        """Skip-mode: jump past already-read lines, stop at unread or choice."""
        if self.choices and self.choices.visible:
            return  # never skip choices
        if not self._current_line:
            return
        if self.box and not self.box.fully_revealed():
            self.box.force_reveal()
            return
        pres = self.ctx.dialogue.skip_to_next_unread()
        if pres is not None:
            self._render_presentation(pres)

    # ------------------------------------------------------------------

    def update(self, dt: float, inp) -> None:
        # Tick portrait crossfades.
        for slot in ("left", "center", "right"):
            fade = self._slot_fades.get(slot)
            if fade is not None:
                fade.update(dt)
                if fade.done:
                    self._slot_fades[slot] = None

        # Tick background fade.
        if self._bg_fade is not None:
            self._bg_fade.update(dt)
            if self._bg_fade.done:
                self._bg_fade = None

        # Open scrollback on wheel-up or B key.
        if inp.mouse_wheel > 0 and self.on_scrollback:
            self.on_scrollback()
            return
        for e in inp.events:
            if e.type == pygame.KEYDOWN and e.key == pygame.K_b and self.on_scrollback:
                self.on_scrollback()
                return

        # Check for Skip (Ctrl held) and Auto toggle.
        for e in inp.events:
            if e.type == pygame.KEYDOWN:
                if e.key in (pygame.K_LCTRL, pygame.K_RCTRL):
                    self._trigger_skip()
                # [A] key toggles auto-play
                if e.key == pygame.K_a and not (e.mod & pygame.KMOD_CTRL):
                    self._toggle_auto_play()

        if self.choices and self.choices.visible:
            self.auto_play_enabled = False   # stop auto-play at choices
            self._auto_play_timer = 0.0
            self.choices.update(dt, inp)
            return
        if self.box:
            self.box.update(dt, inp)
        if self.portrait:
            self.portrait.update(dt, inp)
        # advance on click/space — but only if click isn't on a button etc.
        if inp.advance_dialogue and self._current_line is not None:
            if not self.box.fully_revealed():
                self.box.force_reveal()
            else:
                self._advance()
                self._auto_play_timer = 0.0   # reset timer on manual advance

        # Auto-play: wait until text is fully revealed, then count down.
        if self.auto_play_enabled and self._current_line is not None:
            if self.box and self.box.fully_revealed():
                self._auto_play_timer += dt
                delay = getattr(self.ctx.config, "auto_play_delay", 2.5)
                if self._auto_play_timer >= delay:
                    self._auto_play_timer = 0.0
                    self._advance()

    def _slot_rect(self, slot: str, sw: int, sh: int) -> pygame.Rect:
        """Return the bounding rect for a portrait slot, anchored at bottom."""
        box_h = 230
        margin = 32
        portrait_h = sh - box_h - margin - 60
        portrait_w = 480
        anchor_x = {
            "left":   int(sw * 0.20),
            "center": int(sw * 0.50),
            "right":  int(sw * 0.80),
        }[slot]
        x = anchor_x - portrait_w // 2
        y = 30
        return pygame.Rect(x, y, portrait_w, portrait_h)

    def draw(self, surface: pygame.Surface) -> None:
        sw, sh = surface.get_size()

        # Background: use fade if active, else static surface or solid fill.
        if self._bg_fade is not None and not self._bg_fade.done:
            surface.fill(self.ctx.theme.bg_deep)
            self._bg_fade.draw(surface)
        elif self._bg_surface is not None:
            surface.blit(self._bg_surface, (0, 0))
        else:
            surface.fill(self.ctx.theme.bg_deep)

        # dim the bg for text readability
        veil = pygame.Surface((sw, sh), pygame.SRCALPHA)
        veil.fill((0, 0, 0, 70))
        surface.blit(veil, (0, 0))

        # CG (full-screen) takes over the background
        if self.cg_surface_path:
            cg = self.ctx.assets.scaled(self.cg_surface_path, (sw, sh), fit="contain")
            surface.blit(cg, (0, 0))

        # Slot-based portrait rendering (new system).
        if not self.cg_surface_path:
            any_slot_active = any(
                self._slot_surfaces.get(s) is not None or self._slot_fades.get(s) is not None
                for s in ("left", "center", "right")
            )
            if any_slot_active:
                for slot in ("left", "center", "right"):
                    fade = self._slot_fades.get(slot)
                    rect = self._slot_rect(slot, sw, sh)
                    if fade is not None:
                        fade.draw(surface, rect)
                    elif self._slot_surfaces.get(slot) is not None:
                        surf = pygame.transform.smoothscale(
                            self._slot_surfaces[slot], (rect.width, rect.height))
                        surface.blit(surf, rect.topleft)
            elif self.portrait:
                # Legacy fallback: no slot surfaces active, use PortraitView.
                self.portrait.draw(surface)

        if self.box:
            self.box.draw(surface)
        if self.choices and self.choices.visible:
            self.choices.draw(surface)

    def describe(self) -> dict:
        return {
            "scene": "DialogueScene",
            "scene_id": self.scene_id,
            "story_scene": self.ctx.story_id() if hasattr(self.ctx, "story_id") else None,
            "current_line": (
                {
                    "speaker": self._current_line.speaker,
                    "text": self._current_line.text,
                    "line_index": self._current_line.line_index,
                    "total_lines": self._current_line.total_lines,
                } if self._current_line else None
            ),
            "choice_visible": bool(self.choices and self.choices.visible),
        }

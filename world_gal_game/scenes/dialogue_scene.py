"""Dialogue runner: drives the engine.dialogue.DialogueEngine in pygame."""
from __future__ import annotations

from typing import Callable

import pygame

from .base import Scene, SceneContext
from ..core.history import StateHistory
from ..ui.widgets import DialogueBox, ChoiceMenu, PortraitView, QuickMenuBar
from ..ui.nvl_box import NVLBox
from ..ui.transitions import PortraitCrossfade, BackgroundFade, SceneTransition
from ..ui.camera import Camera, ScreenShake, ScreenFlash, ColorTint
from ..ui.portrait_anim import SlotAnimation, PortraitEmote
from ..ui.layout import fit_rect
from ..core.portrait_spec import PortraitSpec
from ..plugins.builtin_effects import VISUAL_FX_QUEUE


class DialogueScene(Scene):
    def __init__(self, ctx: SceneContext):
        super().__init__(ctx)
        self.is_overlay = True   # drawn over a darkened background
        # In ADV mode this is a DialogueBox; in NVL mode an NVLBox. Both expose
        # the same surface (set_line / force_reveal / fully_revealed / update /
        # draw) so the rest of the scene treats them uniformly.
        self.box: DialogueBox | NVLBox | None = None
        # Story scene id whose lines currently fill the NVL transcript; used to
        # detect scene changes and clear the transcript. None until first line.
        self._nvl_scene_id: str | None = None
        self.choices: ChoiceMenu | None = None
        self.portrait: PortraitView | None = None
        self._quick_bar: QuickMenuBar | None = None
        self.cg_surface_path: str | None = None
        self.background_path: str | None = None
        self.scene_id: str | None = None
        self.on_done: Callable[[], None] | None = None
        self.on_movie: Callable[[dict], None] | None = None
        self._current_line = None
        self._scene_started = False
        self._pending_choices = False
        # Player-facing rollback: per-scene history of (state snapshot, the
        # presentation that was on screen). Created fresh in enter() when
        # config.rollback_enabled, so rewinding stays within the current scene.
        # _rewinding guards _render_presentation from re-recording while it is
        # redrawing a rewound presentation.
        self._history: StateHistory | None = None
        self._rewinding: bool = False
        # Skip / auto-play state
        self.auto_play_enabled: bool = False
        self._auto_play_timer: float = 0.0
        # Skip is "held": pressing Ctrl latches it on, releasing latches it
        # off, so the on-screen SKIP indicator reflects an ongoing skip rather
        # than a single keypress. Mode (skip-read vs skip-all) is read from
        # config.skip_unread_only at the moment of each advance.
        self._skip_active: bool = False
        # Hide-UI (非表示): when True the text box + quick bar are hidden so the
        # full background / CG shows; any click/key restores them.
        self._ui_hidden: bool = False

        # Portrait state: per-slot surface + active crossfade + spec.
        # Slots: "left", "center", "right"
        self._slot_surfaces: dict[str, pygame.Surface | None] = {
            "left": None, "center": None, "right": None,
        }
        self._slot_fades: dict[str, PortraitCrossfade | None] = {
            "left": None, "center": None, "right": None,
        }
        # Active SlotAnimation per slot (enter/exit/move). When set it takes
        # precedence over the plain crossfade for that slot. Specs remember
        # offset/scale/flip so the static draw can keep applying them.
        self._slot_anims: dict[str, SlotAnimation | None] = {
            "left": None, "center": None, "right": None,
        }
        self._slot_specs: dict[str, PortraitSpec | None] = {
            "left": None, "center": None, "right": None,
        }
        # One-shot in-place emote (jump/shake/nod/bounce) per slot, played on
        # the settled portrait by the portrait_emote effect. None = at rest.
        self._slot_emotes: dict[str, PortraitEmote | None] = {
            "left": None, "center": None, "right": None,
        }
        # Active portrait backend instance per slot (procedural breathing,
        # sprite frames, Live2D ...). None means the slot uses the plain static
        # blit. Backends own the *resting* animation; enter/exit/crossfade
        # transitions stay surface-based (they animate base_surface()).
        self._slot_backends: dict[str, object | None] = {
            "left": None, "center": None, "right": None,
        }
        # Which slot holds the current speaker, so lip-sync only moves their
        # mouth (and only while their line is still typing). None = nobody.
        # It also drives speaker emphasis: non-speaking slots are dimmed.
        self._speaking_slot: str | None = None
        # Reusable scratch surface for dimming a non-speaking slot's portrait
        # (lazily sized to the frame). None until the first dimmed draw.
        self._dim_scratch_surf: pygame.Surface | None = None

        # Background transition.
        self._bg_surface: pygame.Surface | None = None
        self._bg_fade: BackgroundFade | None = None

        # Presentation FX (camera/screen). Driven by directives the camera_*
        # / screen_* builtin effects queue onto state.meta[VISUAL_FX_QUEUE];
        # update() drains that queue, spawns/updates these, draw() applies them.
        # The camera persists across lines (a zoom/pan stays until changed); the
        # tint likewise persists; shake/flash are transient and self-expire.
        self._camera: Camera = Camera()
        self._shakes: list[ScreenShake] = []
        self._flashes: list[ScreenFlash] = []
        self._tint: ColorTint | None = None
        # Background depth-of-field blur (the screen_blur effect): an animated
        # radius applied to the background layer only — portraits / CG stay
        # sharp. 0 = no blur (byte-identical draw). The cache holds the last
        # (bg-surface identity, rounded radius) -> blurred surface so a steady
        # blur isn't recomputed every frame.
        self._bg_blur: float = 0.0
        self._bg_blur_from: float = 0.0
        self._bg_blur_target: float = 0.0
        self._bg_blur_t: float = 0.0
        self._bg_blur_dur: float = 0.0
        self._bg_blur_cache: tuple | None = None

        # Scene transitions (set_background / show_cg / hide_cg / transition).
        # A SceneTransition snapshots the previous composed world frame and
        # reveals the freshly-composed one beneath it. ``_last_world_frame`` is
        # that snapshot, captured each draw just before the text box so the box
        # stays stable on top. The two _overridden flags record that an effect
        # (not the per-line scene data) now owns the background / CG, so
        # _render_presentation stops re-applying pres.background / line.cg and
        # clobbering an author's mid-scene change. They reset on a scene change.
        self._scene_transition: SceneTransition | None = None
        self._last_world_frame: pygame.Surface | None = None
        self._bg_overridden: bool = False
        self._cg_overridden: bool = False
        self._fx_scene_id: str | None = None

        # Ambient / weather overlay (the @ambient_backend category). A single
        # active backend instance drawn above the world layer and below the text
        # box, persisting across lines until a clear_weather / another
        # set_weather. ``_ambient_base_alpha`` is its intended opacity; a fade
        # in/out scales the backend's live alpha toward / away from it.
        self._ambient: object | None = None
        self._ambient_name: str | None = None
        self._ambient_base_alpha: int = 255
        self._ambient_fade_dir: int = 0      # +1 fading in, -1 out, 0 steady
        self._ambient_fade_t: float = 0.0
        self._ambient_fade_dur: float = 0.0

    def enter(self, *, scene_id: str | None = None,
              on_done: Callable[[], None] | None = None,
              on_scrollback: Callable[[], None] | None = None,
              on_save: Callable[[], None] | None = None,
              on_load: Callable[[], None] | None = None,
              on_config: Callable[[], None] | None = None,
              on_menu: Callable[[], None] | None = None,
              on_qsave: Callable[[], None] | None = None,
              on_qload: Callable[[], None] | None = None,
              on_movie: Callable[[dict], None] | None = None,
              **_) -> None:
        self.scene_id = scene_id
        self.on_done = on_done
        self.on_scrollback = on_scrollback
        self.on_movie = on_movie
        # Fresh rollback history per scene (rewind stays within this scene).
        self._history = (StateHistory()
                         if getattr(self.ctx.config, "rollback_enabled", True)
                         else None)
        self._rewinding = False
        sw, sh = self.ctx.screen_size
        box_h = 230
        margin = 32
        # ADV uses the bottom dialogue box; NVL uses a tall transcript panel.
        # Both expose the same set_line/force_reveal/fully_revealed/update/draw
        # surface so everything downstream (advance, skip, auto-play) is shared.
        if getattr(self.ctx.config, "nvl_mode", False):
            nvl_margin = 60
            self.box = NVLBox(
                pygame.Rect(nvl_margin, nvl_margin,
                            sw - nvl_margin * 2, sh - nvl_margin * 2),
                fonts=self.ctx.fonts, theme=self.ctx.theme,
                text_speed=self.ctx.config.text_speed,
            )
        else:
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
        # Persistent quick-menu bar (VN convention): a slim row of compact
        # actions just above the text box in ADV (near the bottom in NVL).
        bar_h = 34
        if getattr(self.ctx.config, "nvl_mode", False):
            bar_y = sh - bar_h - 14
        else:
            bar_y = (sh - box_h - margin) - bar_h - 10
        self._quick_bar = QuickMenuBar(
            sw - margin, bar_y, fonts=self.ctx.fonts, theme=self.ctx.theme,
            height=bar_h, font_size=14,
            items=[
                ("自動", self._toggle_auto_play, lambda: self.auto_play_enabled),
                ("快進", self._trigger_skip, lambda: self._skip_active),
                ("記錄", on_scrollback, None),
                ("快存", on_qsave, None),
                ("快讀", on_qload, None),
                ("存檔", on_save, None),
                ("讀取", on_load, None),
                (self.ctx.t("settings", "設定"), on_config, None),
                ("選單", on_menu, None),
                ("隱藏", self._hide_ui, None),
            ],
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
        # A manual advance cuts the current line's voice immediately.
        self.ctx.assets.stop_voice()
        pres = self.ctx.dialogue.next_line()
        self._render_presentation(pres)

    def _speaker_color(self, speaker: str | None) -> tuple | None:
        """Resolve a speaker's name-plate colour from its NPC ``name_color``.

        Returns an RGB tuple parsed from the character's ``name_color``
        ("#rgb" / "#rrggbb" / named colour), or None for narration, an unknown
        speaker, or one that declares no colour (the box then keeps the theme
        accent). Never raises — a malformed colour string resolves to None.
        """
        if not speaker:
            return None
        npcs = getattr(self.ctx, "npcs", None)
        npc = (npcs.by_name(speaker)
               if npcs is not None and hasattr(npcs, "by_name") else None)
        raw = getattr(npc, "name_color", None) if npc is not None else None
        if not raw:
            return None
        try:
            from ..dialogue.richtext import _parse_color
            return _parse_color(raw)
        except Exception:
            return None

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

    def _character_backend_spec(self, line) -> PortraitSpec | None:
        """Route a single expression/default portrait through the speaker's
        declared resting backend (``NPC.portrait_backend``).

        Returns None — so the byte-identical legacy still path runs — unless the
        line uses an expression/default portrait (not an explicit PortraitSpec),
        the speaker resolves to an NPC declaring a non-"static" backend, and a
        still image resolves. The explicit ``image`` pins resolution to exactly
        the file the static path would have used, so only the resting animation
        (breath / layered) is added — never a different picture.
        """
        speaker = getattr(line, "speaker", None)
        npcs = getattr(self.ctx, "npcs", None)
        npc = (npcs.by_name(speaker)
               if npcs is not None and hasattr(npcs, "by_name") and speaker
               else None)
        if npc is None:
            return None
        backend = getattr(npc, "portrait_backend", "static") or "static"
        if backend == "static":
            return None
        still = line.portrait if isinstance(line.portrait, str) \
            else npc.portrait_for(line.expression)
        if not still:
            return None
        return PortraitSpec(
            character=npc.id,
            expression=(line.expression or "default"),
            backend=backend,
            backend_args=dict(getattr(npc, "portrait_backend_args", {}) or {}),
            image=still,
        )

    @staticmethod
    def _spec_has_staging(spec: PortraitSpec | None) -> bool:
        """True when a spec uses any non-neutral staging field.

        Legacy specs (all defaults) return False so they keep the original
        crossfade path and render pixel-identically.
        """
        if spec is None:
            return False
        return bool(
            spec.enter or spec.exit
            or spec.offset != (0, 0)
            or spec.scale != 1.0
            or spec.flip
        )

    def _start_slot_transition(self, slot: str, new_surf: pygame.Surface | None,
                                new_spec: PortraitSpec | None,
                                sw: int, sh: int) -> None:
        """Transition a slot to ``new_surf``/``new_spec``.

        Uses a :class:`SlotAnimation` when either the incoming spec declares
        staging (enter/offset/scale/flip) or the outgoing spec declared an
        exit animation; otherwise falls back to the plain crossfade so packs
        that don't opt in are unchanged.
        """
        old_surf = self._slot_surfaces.get(slot)
        old_spec = self._slot_specs.get(slot)
        if old_surf is new_surf and old_spec is new_spec:
            return

        animated = self._spec_has_staging(new_spec) or (
            old_spec is not None and old_spec.exit
            and old_spec.exit != "none"
        )

        if not animated:
            # Neutral path: identical to the historical behaviour.
            self._slot_anims[slot] = None
            self._slot_fades[slot] = PortraitCrossfade(
                old_surf, new_surf, duration=0.25)
            self._slot_surfaces[slot] = new_surf
            self._slot_specs[slot] = new_spec
            return

        target = self._slot_rect(slot, sw, sh, new_spec)
        self._slot_fades[slot] = None
        if new_surf is not None and new_spec is not None and new_spec.enter \
                and new_spec.enter != "none":
            self._slot_anims[slot] = SlotAnimation(
                kind="enter", rect=target, duration=0.3,
                new=new_surf, anim=new_spec.enter, flip=new_spec.flip,
            )
        elif new_surf is None and old_surf is not None and old_spec is not None \
                and old_spec.exit and old_spec.exit != "none":
            exit_rect = self._slot_rect(slot, sw, sh, old_spec)
            self._slot_anims[slot] = SlotAnimation(
                kind="exit", rect=exit_rect, duration=0.3,
                old=old_surf, anim=old_spec.exit, flip=old_spec.flip,
            )
        else:
            # Staging present (offset/scale/flip) but no named enter anim:
            # crossfade at the staged rect so flip/offset/scale still apply.
            self._slot_anims[slot] = SlotAnimation(
                kind="crossfade", rect=target, duration=0.25,
                old=old_surf, new=new_surf, flip=new_spec.flip if new_spec else False,
            )
        self._slot_surfaces[slot] = new_surf
        self._slot_specs[slot] = new_spec

    def _resolve_slot(self, spec: PortraitSpec | None, sw: int, sh: int
                      ) -> tuple[pygame.Surface | None, object | None]:
        """Resolve a spec to ``(surface, backend_instance)``.

        A spec naming a registered, non-``"static"`` backend gets a per-slot
        backend instance; its resting frame (``base_surface()``) becomes the
        surface the enter/exit/crossfade transition animates, so the handoff to
        the live backend on settle is seamless. Anything else (no spec, static
        backend, unknown/failed backend) returns the plain resolved still and no
        backend — i.e. byte-for-byte the historical path.
        """
        if spec is None:
            return None, None
        backend = None
        name = getattr(spec, "backend", "static")
        if name and name != "static":
            try:
                from ..plugins.registry import PORTRAIT_BACKEND_REGISTRY
                if PORTRAIT_BACKEND_REGISTRY.has(name):
                    fallback = (480, sh - 230 - 32 - 60)
                    backend = PORTRAIT_BACKEND_REGISTRY.spawn(
                        name, spec, self.ctx.assets, fallback)
            except Exception:
                backend = None  # unknown / broken backend -> static fallback
        if backend is not None:
            base = None
            try:
                base = backend.base_surface()
            except Exception:
                base = None
            if base is None:
                base = self.ctx.assets.resolve_portrait(spec)
            return base, backend
        return self.ctx.assets.resolve_portrait(spec), None

    def _update_portraits(self, line) -> None:
        """Compute which slots change this line and start transitions."""
        sw, sh = self.ctx.screen_size
        speaker = getattr(line, "speaker", None)
        # A portrait spec names its character by id (e.g. "qingyi"), but a line's
        # speaker is the display name (e.g. "林青衣"); resolve one to the other so
        # the speaking slot is found whichever form the pack uses.
        speaker_id = None
        npcs = getattr(self.ctx, "npcs", None)
        if speaker and npcs is not None and hasattr(npcs, "by_name"):
            _npc = npcs.by_name(speaker)
            speaker_id = _npc.id if _npc is not None else None
        self._speaking_slot = None
        if line.portraits:
            # Multi-slot: clear all then populate from the spec list.
            wanted: dict[str, tuple[pygame.Surface | None,
                                    PortraitSpec | None, object | None]] = {
                "left": (None, None, None), "center": (None, None, None),
                "right": (None, None, None),
            }
            for spec in line.portraits:
                surf, backend = self._resolve_slot(spec, sw, sh)
                wanted[spec.slot] = (surf, spec, backend)
                # The slot whose character is speaking drives lip-sync + speaker
                # emphasis. Match the spec's character against both the raw
                # speaker string and its resolved id.
                if speaker and spec.character in (speaker, speaker_id):
                    self._speaking_slot = spec.slot
            for slot, (surf, spec, backend) in wanted.items():
                self._slot_backends[slot] = backend
                self._start_slot_transition(slot, surf, spec, sw, sh)
        else:
            # Single-portrait / legacy path goes to center; clear other slots.
            if isinstance(line.portrait, PortraitSpec):
                center_surf, center_backend = self._resolve_slot(
                    line.portrait, sw, sh)
                center_spec = line.portrait
            else:
                center_spec = self._character_backend_spec(line)
                if center_spec is not None:
                    center_surf, center_backend = self._resolve_slot(
                        center_spec, sw, sh)
                else:
                    center_surf = self._surface_for_portrait(
                        line.portrait, line.speaker, line.expression)
                    center_backend = None
            self._slot_backends["center"] = center_backend
            # Single portrait == the speaker on screen, so it drives lip-sync.
            if center_surf is not None and speaker:
                self._speaking_slot = "center"
            self._start_slot_transition("center", center_surf, center_spec, sw, sh)
            self._slot_backends["left"] = None
            self._start_slot_transition("left", None, None, sw, sh)
            self._slot_backends["right"] = None
            self._start_slot_transition("right", None, None, sw, sh)

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
        # A change of story scene clears any mid-scene background / CG override
        # so the new scene's own background and per-line CG apply normally.
        cur_sid = getattr(self.ctx.state.story, "current_scene", None)
        if cur_sid != self._fx_scene_id:
            self._fx_scene_id = cur_sid
            self._bg_overridden = False
            self._cg_overridden = False
        # Apply the scene-level background unless a set_background effect has
        # taken over the background for this scene (then it owns the swap).
        if pres.background and not self._bg_overridden:
            self._update_background(pres.background)
        if pres.kind == "line":
            line = pres.line
            self._current_line = line
            # NVL transcript is per-scene: clear it whenever the active story
            # scene changes (a chained transition starts a new scene, so the
            # previous scene's lines should not bleed into the new one).
            if isinstance(self.box, NVLBox):
                cur_sid = self.ctx.state.story.current_scene
                if cur_sid != self._nvl_scene_id:
                    self.box.reset()
                    self._nvl_scene_id = cur_sid
            self.box.set_line(line.speaker, line.text,
                              speaker_color=self._speaker_color(line.speaker))
            # SFX/BGM
            if line.bgm:
                self.ctx.assets.play_music(
                    line.bgm, volume=self.ctx.config.bgm_volume)
            if line.sfx:
                self.ctx.assets.play_sound(
                    line.sfx, volume=self.ctx.config.sfx_volume)
            # Voice: cut any in-flight clip, then play this line's (if any).
            self.ctx.assets.stop_voice()
            if line.voice:
                # Per-character override falls back to the global voice volume
                # for any speaker not listed in the config dict.
                vol = self.ctx.config.per_character_voice_volume.get(
                    line.speaker, self.ctx.config.voice_volume)
                self.ctx.assets.play_voice(line.voice, volume=vol)
            # CG: per-line CG, unless a show_cg/hide_cg effect has taken over
            # the CG layer for this scene (then it owns what is displayed).
            if not self._cg_overridden:
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
            self._ui_hidden = False   # a decision always shows the UI
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

        # Record this display for player rollback. Only line / choice displays
        # are recorded (not transitions / ends), and never while we are redrawing
        # a rewound presentation (self._rewinding) — otherwise a rewind would
        # immediately re-push what it just restored. The presentation is stored
        # as the payload so a rewind redraws it without re-running the engine
        # (which would re-fire the line's effects / dialogue ops).
        if (self._history is not None and not self._rewinding
                and pres.kind in ("line", "choice")):
            self._history.record(self.ctx.state, pres)

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

    def _hide_ui(self) -> None:
        """Hide the text box + quick bar so the full image / CG shows (the VN
        非表示 convention). Any click or key restores the UI."""
        self._ui_hidden = True

    def _draw_hidden_hint(self, surface: pygame.Surface) -> None:
        sw, sh = surface.get_size()
        hint = self.ctx.fonts.render(
            "點擊 / 按 H 顯示介面", 14,
            (*self.ctx.theme.text_mute[:3], 150))
        surface.blit(hint, (sw - hint.get_width() - 20,
                            sh - hint.get_height() - 16))

    def _update_quick_bar(self, dt: float, inp) -> bool:
        """Update the quick-menu bar; return True when the pointer is over it
        so the caller suppresses click-to-advance. Hidden while choices show."""
        if self._quick_bar is None:
            return False
        showing_choices = bool(self.choices and self.choices.visible)
        self._quick_bar.visible = not showing_choices
        if showing_choices:
            return False
        self._quick_bar.update(dt, inp)
        return self._quick_bar.consumed(inp)

    def _trigger_skip(self) -> None:
        """Advance one skip step.

        Two-stage skip, switched by ``config.skip_unread_only``:

        - ``True`` (default): jump past already-read lines, stopping at the
          first unread line or any choice point (``skip_to_next_unread``).
        - ``False`` (skip-all): race through *all* remaining lines — read or
          unread — until a choice or scene end (``skip_all``).

        Either way an in-progress typewriter is completed first, and the
        current line's voice is cut so a held skip stays silent.
        """
        if self.choices and self.choices.visible:
            return  # never skip choices
        if not self._current_line:
            return
        if self.box and not self.box.fully_revealed():
            self.box.force_reveal()
            return
        # Skipping cuts the current line's voice immediately.
        self.ctx.assets.stop_voice()
        if self.ctx.config.skip_unread_only:
            pres = self.ctx.dialogue.skip_to_next_unread()
        else:
            pres = self.ctx.dialogue.skip_all()
        if pres is not None:
            self._render_presentation(pres)
        # Skip-all reaching the end returns None; the scene's _present_current
        # already ended it (current_scene cleared), so flush the end here.
        elif self.ctx.dialogue.current_scene() is None:
            self._end()

    # ------------------------------------------------------------------
    # Visual FX (camera / shake / flash / tint)
    #
    # Effect handlers cannot touch pygame, so camera_* / screen_* effects only
    # push directives onto state.meta[VISUAL_FX_QUEUE]. We drain that queue here
    # each frame, turn each directive into the matching ui.camera object, then
    # advance everything by dt and drop the transient ones once finished.

    def _consume_visual_fx(self) -> None:
        """Drain queued directives and spawn/update the matching FX objects."""
        queue = self.ctx.state.meta.pop(VISUAL_FX_QUEUE, None)
        if not queue:
            return
        for d in queue:
            try:
                self._spawn_visual_fx(d)
            except Exception:
                # A malformed directive must never crash the scene; skip it.
                # (Handlers already coerce their inputs, but stay defensive.)
                continue

    def _spawn_visual_fx(self, d: dict) -> None:
        fx = d.get("fx")
        if fx == "camera_pan":
            self._camera.pan_to(d.get("x", 0.0), d.get("y", 0.0),
                                duration=d.get("duration", 0.6),
                                easing=d.get("easing"))
        elif fx == "camera_zoom":
            self._camera.zoom_to(d.get("scale", 1.0),
                                 duration=d.get("duration", 0.6),
                                 easing=d.get("easing"))
        elif fx == "screen_shake":
            self._shakes.append(ScreenShake(
                intensity=d.get("intensity", 12.0),
                duration=d.get("duration", 0.4),
                easing=d.get("easing")))
        elif fx == "screen_flash":
            self._flashes.append(ScreenFlash(
                color=d.get("color", (255, 255, 255)),
                duration=d.get("duration", 0.3),
                max_alpha=d.get("max_alpha", 255),
                easing=d.get("easing")))
        elif fx == "screen_tint":
            if d.get("clear"):
                self._tint = None
            else:
                self._tint = ColorTint(
                    color=d.get("color", (0, 0, 0)),
                    duration=d.get("duration", 0.5),
                    max_alpha=d.get("max_alpha", 120),
                    easing=d.get("easing"))
        elif fx == "screen_blur":
            target = 0.0 if d.get("clear") else float(d.get("radius", 8.0))
            self._start_bg_blur(target, float(d.get("duration", 0.5)))
        elif fx == "set_background":
            self._apply_set_background(d)
        elif fx == "show_cg":
            self.cg_surface_path = d.get("path")
            self._cg_overridden = True
            self._begin_scene_transition(d.get("transition"))
        elif fx == "hide_cg":
            self.cg_surface_path = None
            self._cg_overridden = True
            self._begin_scene_transition(d.get("transition"))
        elif fx == "transition":
            # A stand-alone beat over the current frame (no state change).
            self._begin_scene_transition(d.get("transition"))
        elif fx == "set_weather":
            self._apply_set_weather(d)
        elif fx == "clear_weather":
            self._apply_clear_weather(d)
        elif fx == "portrait_emote":
            self._apply_portrait_emote(d)
        elif fx == "play_movie":
            # Push a full-screen movie overlay via the app callback (the scene
            # has no manager backref). No callback wired → silently ignored.
            if self.on_movie is not None:
                self.on_movie(d)

    def _resolve_emote_slot(self, target: str) -> str | None:
        """Map an emote ``target`` to a slot: a slot name as-is, else the slot
        whose settled spec's character matches ``target`` (else center if it
        holds a portrait, else None)."""
        if target in ("left", "center", "right"):
            return target
        for slot in ("left", "center", "right"):
            spec = self._slot_specs.get(slot)
            if spec is not None and getattr(spec, "character", None) == target:
                return slot
        if self._slot_surfaces.get("center") is not None:
            return "center"
        return None

    def _apply_portrait_emote(self, d: dict) -> None:
        slot = self._resolve_emote_slot(str(d.get("target", "")))
        if slot is None:
            return
        kwargs = {"kind": str(d.get("emote", "jump")),
                  "duration": float(d.get("duration", 0.45))}
        if d.get("intensity") is not None:
            kwargs["intensity"] = float(d["intensity"])
        self._slot_emotes[slot] = PortraitEmote(**kwargs)

    def _apply_set_weather(self, d: dict) -> None:
        """Instantiate the named ambient backend and (optionally) fade it in.

        An unknown / broken backend degrades to no overlay (isolated like the
        portrait backends), so a missing weather plugin never breaks the frame.
        """
        name = d.get("backend")
        params = d.get("params") if isinstance(d.get("params"), dict) else {}
        backend = None
        try:
            from ..plugins.registry import AMBIENT_BACKEND_REGISTRY
            if name and AMBIENT_BACKEND_REGISTRY.has(name):
                backend = AMBIENT_BACKEND_REGISTRY.spawn(
                    name, dict(params), self.ctx.screen_size)
        except Exception:
            backend = None
        if backend is None:
            self._ambient = None
            self._ambient_name = None
            return
        self._ambient = backend
        self._ambient_name = name
        self._ambient_base_alpha = int(getattr(backend, "alpha", 255))
        fade = float(d.get("fade", 0.0) or 0.0)
        if fade > 0.0:
            self._ambient_fade_dir = 1
            self._ambient_fade_t = 0.0
            self._ambient_fade_dur = fade
            try:
                backend.alpha = 0           # start invisible, ramp up
            except Exception:
                pass
        else:
            self._ambient_fade_dir = 0

    def _apply_clear_weather(self, d: dict) -> None:
        fade = float(d.get("fade", 0.0) or 0.0)
        if fade > 0.0 and self._ambient is not None:
            self._ambient_fade_dir = -1     # fade out, then drop in update()
            self._ambient_fade_t = 0.0
            self._ambient_fade_dur = fade
        else:
            self._ambient = None
            self._ambient_name = None
            self._ambient_fade_dir = 0

    def _advance_ambient(self, dt: float) -> None:
        if self._ambient is None:
            return
        try:
            self._ambient.update(dt)
        except Exception:
            self._ambient = None            # a broken backend is dropped
            self._ambient_name = None
            return
        if self._ambient_fade_dir != 0 and self._ambient_fade_dur > 0.0:
            self._ambient_fade_t = min(self._ambient_fade_t + dt,
                                       self._ambient_fade_dur)
            frac = self._ambient_fade_t / self._ambient_fade_dur
            if self._ambient_fade_dir > 0:
                live = int(self._ambient_base_alpha * frac)
            else:
                live = int(self._ambient_base_alpha * (1.0 - frac))
            try:
                self._ambient.alpha = max(0, min(255, live))
            except Exception:
                pass
            if self._ambient_fade_t >= self._ambient_fade_dur:
                if self._ambient_fade_dir < 0:
                    self._ambient = None    # fade-out finished → remove
                    self._ambient_name = None
                self._ambient_fade_dir = 0

    def _apply_set_background(self, d: dict) -> None:
        """Swap the background immediately and reveal it via a transition.

        Unlike :meth:`_update_background` (the implicit per-line crossfade), the
        change is authoritative: ``_bg_overridden`` keeps a later line's
        scene-level background from reverting it until the scene changes.
        """
        path = d.get("path")
        sw, sh = self.ctx.screen_size
        self._bg_surface = self.ctx.assets.scaled(path, (sw, sh), fit="cover")
        self.background_path = path
        self._bg_fade = None   # the scene transition supersedes the plain fade
        self._bg_overridden = True
        self._begin_scene_transition(d.get("transition"))

    def _begin_scene_transition(self, tr: dict | None) -> None:
        """Spawn a :class:`SceneTransition` from the last composed world frame.

        ``tr`` is the transition sub-dict authored on the effect; a missing /
        malformed value falls back to a plain dissolve. A mask-style transition
        resolves its image path to a surface here (the scene owns asset access).
        """
        tr = tr if isinstance(tr, dict) else {}
        style = str(tr.get("style", "dissolve"))
        if style == "cut" or self._last_world_frame is None:
            # Nothing to animate from (or an explicit hard cut): just snap.
            self._scene_transition = None
            return
        mask_surf = None
        mask_path = tr.get("mask")
        if mask_path:
            try:
                sw, sh = self.ctx.screen_size
                mask_surf = self.ctx.assets.scaled(mask_path, (sw, sh),
                                                   fit="cover")
            except Exception:
                mask_surf = None
        self._scene_transition = SceneTransition(
            self._last_world_frame, style=style,
            duration=float(tr.get("duration", 0.6)),
            color=tr.get("color", (0, 0, 0)),
            mask=mask_surf, easing=tr.get("easing"))

    def _advance_visual_fx(self, dt: float) -> None:
        self._camera.update(dt)
        for s in self._shakes:
            s.update(dt)
        self._shakes = [s for s in self._shakes if not s.done]
        for f in self._flashes:
            f.update(dt)
        self._flashes = [f for f in self._flashes if not f.done]
        if self._tint is not None:
            self._tint.update(dt)   # fades in then persists; never auto-removed
        self._advance_bg_blur(dt)
        if self._scene_transition is not None:
            self._scene_transition.update(dt)
            if self._scene_transition.done:
                self._scene_transition = None
        self._advance_ambient(dt)

    def _start_bg_blur(self, target: float, duration: float) -> None:
        """Animate the background blur radius toward ``target`` over ``duration``
        (instant when duration<=0)."""
        self._bg_blur_from = self._bg_blur
        self._bg_blur_target = max(0.0, target)
        self._bg_blur_dur = max(0.0, duration)
        self._bg_blur_t = 0.0
        if self._bg_blur_dur <= 0.0:
            self._bg_blur = self._bg_blur_target

    def _advance_bg_blur(self, dt: float) -> None:
        if self._bg_blur == self._bg_blur_target or self._bg_blur_dur <= 0.0:
            return
        self._bg_blur_t = min(self._bg_blur_t + dt, self._bg_blur_dur)
        frac = self._bg_blur_t / self._bg_blur_dur
        self._bg_blur = self._bg_blur_from + (
            self._bg_blur_target - self._bg_blur_from) * frac
        if self._bg_blur_t >= self._bg_blur_dur:
            self._bg_blur = self._bg_blur_target

    def _active_shake_offset(self) -> tuple[int, int]:
        ox = oy = 0
        for s in self._shakes:
            dx, dy = s.offset()
            ox += dx
            oy += dy
        return (ox, oy)

    # ------------------------------------------------------------------

    def update(self, dt: float, inp) -> None:
        # Drain queued camera/screen-FX directives, then advance all live FX.
        self._consume_visual_fx()
        self._advance_visual_fx(dt)

        # Tick portrait transitions (animation takes precedence over fade).
        for slot in ("left", "center", "right"):
            anim = self._slot_anims.get(slot)
            if anim is not None:
                anim.update(dt)
                if anim.done:
                    self._slot_anims[slot] = None
            fade = self._slot_fades.get(slot)
            if fade is not None:
                fade.update(dt)
                if fade.done:
                    self._slot_fades[slot] = None
            emote = self._slot_emotes.get(slot)
            if emote is not None:
                emote.update(dt)
                if emote.done:
                    self._slot_emotes[slot] = None
            # Advance the resting animation backend (breathing / sprite / rig).
            # The speaker's slot is "talking" while their line is still typing,
            # which a layered rig uses to drive lip-sync. Isolated: a backend
            # that raises is dropped, reverting the slot to the static blit
            # rather than crashing the frame.
            backend = self._slot_backends.get(slot)
            if backend is not None:
                # Lip-sync source: a voiced line moves the mouth for as long as
                # the voice clip actually plays; an unvoiced line falls back to
                # the typewriter (mouth moves while text is still revealing).
                if slot != self._speaking_slot:
                    talking = False
                elif getattr(self._current_line, "voice", None):
                    talking = self.ctx.assets.voice_busy()
                else:
                    talking = (self.box is not None
                               and not self.box.fully_revealed())
                try:
                    backend.update(dt, talking=talking)
                except Exception:
                    self._slot_backends[slot] = None

        # Tick background fade.
        if self._bg_fade is not None:
            self._bg_fade.update(dt)
            if self._bg_fade.done:
                self._bg_fade = None

        # Hide-UI (非表示): H toggles. While hidden, any advance/cancel input
        # restores the UI (without advancing) so the full image stays viewable.
        for e in inp.events:
            if e.type == pygame.KEYDOWN and e.key == pygame.K_h:
                self._ui_hidden = not self._ui_hidden
                return
        if self._ui_hidden:
            if inp.advance_dialogue or inp.cancel:
                self._ui_hidden = False
            return

        # Quick-menu bar: update before the advance logic so a click that
        # lands on it is handled by the bar and does NOT also advance the line.
        bar_consumed = self._update_quick_bar(dt, inp)

        # Open scrollback on wheel-up or B key.
        if inp.mouse_wheel > 0 and self.on_scrollback:
            self.on_scrollback()
            return
        for e in inp.events:
            if e.type == pygame.KEYDOWN and e.key == pygame.K_b and self.on_scrollback:
                self.on_scrollback()
                return

        # Rollback on Backspace: rewind the game state to the previous line in
        # this scene and redraw it. Distinct from scrollback (wheel-up / B),
        # which is a read-only text log; rollback actually rewinds state.
        if self._history is not None:
            for e in inp.events:
                if e.type == pygame.KEYDOWN and e.key == pygame.K_BACKSPACE:
                    if self._history.can_rewind():
                        pres = self._history.rewind(self.ctx.state)
                        if pres is not None:
                            self.ctx.assets.stop_voice()
                            self._rewinding = True
                            try:
                                self._render_presentation(pres)
                            finally:
                                self._rewinding = False
                    return

        # Skip: each Ctrl-down fires one skip step (which itself jumps all the
        # way to the next stopping point — see _trigger_skip). Ctrl-down also
        # latches _skip_active so the on-screen SKIP indicator stays lit while
        # the key is held; Ctrl-up clears it. [A] toggles auto-play. keys_down
        # only carries this frame's KEYDOWNs, so the held flag is tracked
        # across frames rather than re-read.
        skip_pressed_this_frame = False
        for e in inp.events:
            if e.type == pygame.KEYDOWN:
                if e.key in (pygame.K_LCTRL, pygame.K_RCTRL):
                    self._skip_active = True
                    skip_pressed_this_frame = True
                # [A] key toggles auto-play
                if e.key == pygame.K_a and not (e.mod & pygame.KMOD_CTRL):
                    self._toggle_auto_play()
            elif e.type == pygame.KEYUP:
                if e.key in (pygame.K_LCTRL, pygame.K_RCTRL):
                    self._skip_active = False

        if self.choices and self.choices.visible:
            self.auto_play_enabled = False   # stop auto-play at choices
            self._auto_play_timer = 0.0
            self._skip_active = False         # a choice always halts skipping
            self.choices.update(dt, inp)
            return

        # A fresh Ctrl press performs one skip step. We deliberately do NOT
        # repeat per-frame while held: each helper (skip_to_next_unread /
        # skip_all) already loops internally to its natural stop — the next
        # unread line, a choice, or the scene end — so a single call is a full
        # skip. (Repeating would blow past the unread line a skip-read is meant
        # to pause on.)
        if skip_pressed_this_frame and self._current_line is not None:
            self._trigger_skip()
            return
        if self.box:
            self.box.update(dt, inp)
        if self.portrait:
            self.portrait.update(dt, inp)
        # advance on click/space — but only if click isn't on a button etc.
        if inp.advance_dialogue and not bar_consumed \
                and self._current_line is not None:
            if not self.box.fully_revealed():
                self.box.force_reveal()
            else:
                self._advance()
                self._auto_play_timer = 0.0   # reset timer on manual advance

        # Auto-play: wait until text is fully revealed, then count down.
        # The delay is scaled by config.auto_play_speed (higher = faster) and,
        # when config.auto_play_wait_voice is on, we hold the advance until the
        # current voice clip finishes playing before applying the delay.
        if self.auto_play_enabled and self._current_line is not None:
            if self.box and self.box.fully_revealed():
                if getattr(self.ctx.config, "auto_play_wait_voice", True) \
                        and self.ctx.assets.voice_busy():
                    # Voice still playing — pause the countdown so we never cut
                    # a line off, and reset so the full delay starts afterward.
                    self._auto_play_timer = 0.0
                else:
                    self._auto_play_timer += dt
                    base = getattr(self.ctx.config, "auto_play_delay", 2.5)
                    speed = getattr(self.ctx.config, "auto_play_speed", 1.0)
                    delay = base / max(0.1, speed)
                    if self._auto_play_timer >= delay:
                        self._auto_play_timer = 0.0
                        self._advance()

    @staticmethod
    def _emote_rect(rect: pygame.Rect, emote: PortraitEmote | None) -> pygame.Rect:
        """Apply an active emote's transform to a settled slot rect.

        Scales about the rect's bottom-centre (feet stay planted) and offsets by
        the emote's ``(dx, dy)``. A finished / absent emote returns the rect
        unchanged so the resting path is identical.
        """
        if emote is None or emote.done:
            return rect
        dx, dy, sx, sy = emote.transform()
        if sx == 1.0 and sy == 1.0 and dx == 0 and dy == 0:
            return rect
        new_w = max(1, int(rect.width * sx))
        new_h = max(1, int(rect.height * sy))
        out = pygame.Rect(0, 0, new_w, new_h)
        out.midbottom = (rect.centerx + dx, rect.bottom + dy)
        return out

    def _slot_rect(self, slot: str, sw: int, sh: int,
                   spec: PortraitSpec | None = None) -> pygame.Rect:
        """Return the bounding rect for a portrait slot, anchored at bottom.

        When ``spec`` carries staging fields the rect is scaled about its
        center and nudged by ``offset``. With no spec (or a neutral one) the
        rect is byte-for-byte the historical one.
        """
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
        rect = pygame.Rect(x, y, portrait_w, portrait_h)
        if spec is not None and (spec.scale != 1.0 or spec.offset != (0, 0)):
            if spec.scale != 1.0:
                cx, cy = rect.center
                rect.width = max(1, int(portrait_w * spec.scale))
                rect.height = max(1, int(portrait_h * spec.scale))
                rect.center = (cx, cy)
            rect.x += spec.offset[0]
            rect.y += spec.offset[1]
        return rect

    def _blur_bg(self, src: pygame.Surface) -> pygame.Surface:
        """Return a blurred copy of the background for the current blur radius.

        Cached by (background-surface identity, rounded radius) so a steady blur
        isn't recomputed each frame. A radius below 1 returns the source
        unchanged, so the no-blur path is byte-identical to before.
        """
        r = int(round(self._bg_blur))
        if r < 1:
            return src
        key = (id(src), r)
        cache = self._bg_blur_cache
        if cache is not None and cache[0] == key:
            return cache[1]
        try:
            out = pygame.transform.gaussian_blur(src, r)
        except (AttributeError, pygame.error, ValueError):
            out = self._box_blur(src, r)   # older/web pygame without gaussian_blur
        self._bg_blur_cache = (key, out)
        return out

    @staticmethod
    def _box_blur(src: pygame.Surface, radius: int) -> pygame.Surface:
        """Cheap, web-safe blur fallback: downscale then upscale (soft box)."""
        w, h = src.get_size()
        f = max(2, min(10, radius // 2 + 2))
        small = pygame.transform.smoothscale(
            src, (max(1, w // f), max(1, h // f)))
        return pygame.transform.smoothscale(small, (w, h))

    def _render_bg_fade(self, sw: int, sh: int) -> pygame.Surface:
        """Compose the active background crossfade onto an opaque surface.

        The fade historically drew straight onto the frame; routing it through
        a surface lets the camera transform apply to it like a static bg.
        """
        buf = pygame.Surface((sw, sh))
        buf.fill(self.ctx.theme.bg_deep)
        self._bg_fade.draw(buf)
        return buf

    # Brightness multiplier applied to a non-speaking portrait so the active
    # speaker visually stands out (the commercial-VN convention).
    _INACTIVE_DIM: float = 0.55

    def _slot_dim_factor(self, slot: str) -> float:
        """1.0 = full brightness; <1.0 dims a non-speaking slot.

        Only dims when speaker-dimming is enabled, a speaking slot is known,
        this slot is not it, and this slot actually holds a portrait. Narration
        (no speaking slot) and single-speaker frames are never dimmed, so the
        common path is unchanged.
        """
        if not getattr(self.ctx.config, "dim_inactive_speakers", True):
            return 1.0
        speaking = self._speaking_slot
        if speaking is None or slot == speaking:
            return 1.0
        if self._slot_surfaces.get(slot) is None:
            return 1.0
        return self._INACTIVE_DIM

    def _dim_scratch(self, sw: int, sh: int) -> pygame.Surface:
        """A cleared, frame-sized transparent scratch surface for dimming."""
        s = self._dim_scratch_surf
        if s is None or s.get_size() != (sw, sh):
            s = pygame.Surface((sw, sh), pygame.SRCALPHA)
            self._dim_scratch_surf = s
        else:
            s.fill((0, 0, 0, 0))
        return s

    def _draw_with_camera(self, target: pygame.Surface,
                          src: pygame.Surface) -> None:
        """Blit a full-screen layer through the active camera transform.

        A neutral camera blits ``src`` at ``(0, 0)`` unchanged (byte-identical
        to the historical path); a zoomed/panned camera scales+offsets it.
        """
        out, topleft = self._camera.apply(src)
        target.blit(out, topleft)

    def draw(self, surface: pygame.Surface) -> None:
        real_surface = surface
        sw, sh = surface.get_size()

        # Screen-shake shifts the whole composed frame. When a shake is active
        # we compose into an offscreen surface and blit it with the offset (so
        # the exposed border is the deep bg, not garbage); otherwise we draw
        # straight to the real surface so the no-FX path is byte-identical.
        # All the per-element draws below reference ``surface``, so we point it
        # at the frame and restore the real target for the final composite.
        shake_ox, shake_oy = self._active_shake_offset()
        shaking = shake_ox != 0 or shake_oy != 0
        if shaking:
            surface = pygame.Surface((sw, sh))
            surface.fill(self.ctx.theme.bg_deep)

        # Background: use fade if active, else static surface or solid fill.
        # The camera transform (zoom/pan) is applied to the background blit.
        if self._bg_fade is not None and not self._bg_fade.done:
            surface.fill(self.ctx.theme.bg_deep)
            self._draw_with_camera(
                surface, self._blur_bg(self._render_bg_fade(sw, sh)))
        elif self._bg_surface is not None:
            # Opaque base first so a semi-transparent (placeholder) background
            # never lets the scene beneath — e.g. an exploration top bar —
            # bleed through. With real opaque art this fill is invisible.
            surface.fill(self.ctx.theme.bg_deep)
            self._draw_with_camera(surface, self._blur_bg(self._bg_surface))
        else:
            surface.fill(self.ctx.theme.bg_deep)

        # dim the bg for text readability
        veil = pygame.Surface((sw, sh), pygame.SRCALPHA)
        veil.fill((0, 0, 0, 70))
        surface.blit(veil, (0, 0))

        # CG (full-screen) takes over the background; also rides the camera.
        if self.cg_surface_path:
            cg = self.ctx.assets.scaled(self.cg_surface_path, (sw, sh), fit="contain")
            self._draw_with_camera(surface, cg)

        # Slot-based portrait rendering (new system).
        if not self.cg_surface_path:
            any_slot_active = any(
                self._slot_surfaces.get(s) is not None
                or self._slot_fades.get(s) is not None
                or self._slot_anims.get(s) is not None
                for s in ("left", "center", "right")
            )
            if any_slot_active:
                for slot in ("left", "center", "right"):
                    anim = self._slot_anims.get(slot)
                    fade = self._slot_fades.get(slot)
                    spec = self._slot_specs.get(slot)
                    rect = self._slot_rect(slot, sw, sh, spec)
                    if anim is not None:
                        anim.draw(surface)
                    elif fade is not None:
                        fade.draw(surface, rect)
                    elif self._slot_surfaces.get(slot) is not None:
                        # Once a slot has settled (no transition in flight), a
                        # backend owns its resting animation. Isolated: a backend
                        # that raises is dropped and the static still drawn, so a
                        # bad backend degrades instead of crashing the frame.
                        # A one-shot emote (jump/shake/nod/bounce) nudges/squashes
                        # the settled draw rect this frame without altering art.
                        rect = self._emote_rect(rect, self._slot_emotes.get(slot))
                        backend = self._slot_backends.get(slot)
                        # Non-speaking slots dim so the speaker stands out. A
                        # dimmed slot draws onto a scratch surface that is then
                        # multiplied down — only its opaque pixels darken, the
                        # transparent margins stay clear — and composited. The
                        # full-brightness path draws straight to the frame and is
                        # byte-identical to before.
                        dim = self._slot_dim_factor(slot)
                        scratch = self._dim_scratch(sw, sh) if dim < 1.0 else None
                        target = scratch if scratch is not None else surface
                        drawn = False
                        if backend is not None:
                            try:
                                backend.draw(target, rect,
                                             flip=bool(spec and spec.flip))
                                drawn = True
                            except Exception:
                                self._slot_backends[slot] = None
                        if not drawn:
                            src = self._slot_surfaces[slot]
                            # Fit (don't stretch) the portrait into the slot so
                            # its native aspect ratio is preserved; bottom-anchored.
                            dest = fit_rect(src.get_size(), rect)
                            surf = pygame.transform.smoothscale(src, dest.size)
                            if spec is not None and spec.flip:
                                surf = pygame.transform.flip(surf, True, False)
                            target.blit(surf, dest.topleft)
                        if scratch is not None:
                            d = max(0, min(255, int(255 * dim)))
                            scratch.fill((d, d, d, 255),
                                         special_flags=pygame.BLEND_RGB_MULT)
                            surface.blit(scratch, (0, 0))
            elif self.portrait:
                # Legacy fallback: no slot surfaces active, use PortraitView.
                self.portrait.draw(surface)

        # Ambient / weather overlay: above the world layer, below the text box.
        # Drawn before the snapshot so a transition carries the weather too.
        # Isolated: a backend that raises is dropped rather than crashing.
        if self._ambient is not None:
            try:
                self._ambient.draw(surface)
            except Exception:
                self._ambient = None
                self._ambient_name = None

        # Scene transition: ``surface`` now holds the freshly-composed world
        # layer (background + CG + portraits). Overlay the retreating snapshot
        # of the previous world frame so the new one is revealed beneath it,
        # then snapshot the current world layer for the *next* transition. Both
        # happen before the text box so the box stays stable on top. The capture
        # is taken after the overlay so it always reflects what is on screen.
        if self._scene_transition is not None and not self._scene_transition.done:
            self._scene_transition.draw(surface)
        self._last_world_frame = surface.copy()

        if self.box and not self._ui_hidden:
            self.box.draw(surface)
        if self.choices and self.choices.visible:
            self.choices.draw(surface)

        # Composite the (possibly offscreen) frame back onto the real surface,
        # shifted by the active screen-shake offset.
        if shaking:
            real_surface.fill(self.ctx.theme.bg_deep)
            real_surface.blit(surface, (shake_ox, shake_oy))
        surface = real_surface

        # Persistent colour tint then transient flash, both full-screen and on
        # top of everything (drawn on the real surface so shake never clips
        # them). Tint sits under the flash so a flash still reads as a pop.
        if self._tint is not None:
            self._tint.draw(surface)
        for flash in self._flashes:
            flash.draw(surface)

        # Quick-menu bar — drawn on the real surface (stable, unshaken),
        # hidden while choices show or the UI is hidden.
        if self._quick_bar is not None and not self._ui_hidden and not (
                self.choices and self.choices.visible):
            self._quick_bar.draw(surface)

        # Playback-mode badges (AUTO / SKIP), or the restore hint when hidden.
        if not self._ui_hidden:
            self._draw_mode_indicators(surface)
        else:
            self._draw_hidden_hint(surface)

    def _draw_mode_indicators(self, surface: pygame.Surface) -> None:
        """Draw small AUTO / SKIP status badges in the top-right corner.

        These are functional state labels (not decoration): AUTO shows while
        auto-play is on, SKIP while a skip is held. Stacked so both can show
        at once if a skip is held while auto-play is enabled.
        """
        labels: list[str] = []
        if self.auto_play_enabled:
            labels.append("AUTO")
        if self._skip_active:
            labels.append("SKIP")
        if not labels:
            return
        sw, _sh = surface.get_size()
        pad_x, pad_y = 12, 6
        gap = 8
        x_right = sw - 16
        y = 16
        for text in labels:
            ts = self.ctx.fonts.render(text, 16, self.ctx.theme.text, bold=True)
            w = ts.get_width() + pad_x * 2
            h = ts.get_height() + pad_y * 2
            rect = pygame.Rect(x_right - w, y, w, h)
            badge = pygame.Surface((w, h), pygame.SRCALPHA)
            pygame.draw.rect(badge, (*self.ctx.theme.accent[:3], 210),
                             badge.get_rect(),
                             border_radius=self.ctx.theme.radius_s)
            pygame.draw.rect(badge, (*self.ctx.theme.text[:3], 60),
                             badge.get_rect(), 1,
                             border_radius=self.ctx.theme.radius_s)
            surface.blit(badge, rect.topleft)
            surface.blit(ts, (rect.x + pad_x, rect.y + pad_y))
            y += h + gap

    def describe(self) -> dict:
        return {
            "scene": "DialogueScene",
            "scene_id": self.scene_id,
            "story_scene": self.ctx.story_id() if hasattr(self.ctx, "story_id") else None,
            "current_line": (
                {
                    "speaker": self._current_line.speaker,
                    "text": (self._current_line.plain_text
                             or self._current_line.text),
                    "line_index": self._current_line.line_index,
                    "total_lines": self._current_line.total_lines,
                } if self._current_line else None
            ),
            "choice_visible": bool(self.choices and self.choices.visible),
            "auto_play": self.auto_play_enabled,
            "skip_active": self._skip_active,
            "nvl_mode": isinstance(self.box, NVLBox),
            "nvl_lines": (self.box.line_count
                          if isinstance(self.box, NVLBox) else 0),
            "camera": {
                "zoom": round(self._camera.zoom, 4),
                "pan_x": round(self._camera.pan_x, 2),
                "pan_y": round(self._camera.pan_y, 2),
            },
            "fx_active": {
                "shakes": len(self._shakes),
                "flashes": len(self._flashes),
                "tint": self._tint is not None,
                "transition": (self._scene_transition.style
                               if self._scene_transition is not None else None),
                "weather": self._ambient_name,
            },
            "background": self.background_path,
            "cg": self.cg_surface_path,
        }

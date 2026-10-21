"""Dialogue runner: drives the engine.dialogue.DialogueEngine in pygame."""
from __future__ import annotations

import json
from typing import Callable

import pygame

from .base import Scene, SceneContext
from ..config import MIN_TOUCH_TARGET_PX
from ..core.history import StateHistory
from ..ui.widgets import (
    ChoiceMenu,
    DialogueBox,
    PortraitView,
    QuickMenuBar,
    SoundCaptionOverlay,
)
from ..ui.nvl_box import NVLBox
from ..ui.transitions import PortraitCrossfade, BackgroundFade, SceneTransition
from ..ui.camera import Camera, ScreenShake, ScreenFlash, ColorTint
from ..ui.portrait_anim import SlotAnimation, PortraitEmote
from ..ui.layout import fit_rect
from ..ui.input_bindings import BindingProfile, InputAction
from ..ui.motion import MotionKind, MotionPolicy
from ..ui.easing import resolve as resolve_easing
from ..ui.system_chrome import ChromeRequest
from ..core.portrait_spec import PortraitSpec
from ..core.story_graph import ChoiceCardSpec
from ..plugins.builtin_effects import VISUAL_FX_QUEUE
from ..dialogue.dialogue_engine import (
    REPLAY_HUD_META_KEY,
    ScenePresentation,
    presentation_to_dict,
    resume_presentations_for_state,
    resume_matches_state,
)
from ..dialogue.richtext import parse


_AUDIO_SILENCE_SENTINELS = frozenset({"none", "silence", "-"})


class DialogueScene(Scene):
    def __init__(self, ctx: SceneContext):
        super().__init__(ctx)
        # Focused unit tests and third-party embedders may provide the legacy
        # duck-typed context rather than SceneContext.  Preserve that additive
        # seam while real runtime scenes share ctx.motion.
        self._motion = getattr(ctx, "motion", None) or MotionPolicy.from_config(
            getattr(ctx, "config", None),
        )
        self.is_overlay = True   # drawn over a darkened background
        # In ADV mode this is a DialogueBox; in NVL mode an NVLBox. Both expose
        # the same surface (set_line / force_reveal / fully_revealed / update /
        # draw) so the rest of the scene treats them uniformly.
        self.box: DialogueBox | NVLBox | None = None
        # Story scene id whose lines currently fill the NVL transcript; used to
        # detect scene changes and clear the transcript. None until first line.
        self._nvl_scene_id: str | None = None
        # Story scene id the staged portraits belong to, so they can persist
        # across narration WITHIN a scene but clear on a scene change.
        self._portrait_scene_id: str | None = None
        self.choices: ChoiceMenu | None = None
        self.portrait: PortraitView | None = None
        self._quick_bar: QuickMenuBar | None = None
        self._sound_caption_overlay: SoundCaptionOverlay | None = None
        self._status_hud_override_y: int | None = None
        self._mode_indicator_rects: list[pygame.Rect] = []
        self.cg_surface_path: str | None = None
        self.background_path: str | None = None
        self.scene_id: str | None = None
        self.on_done: Callable[[], None] | None = None
        self.on_resume_failed: Callable[[], None] | None = None
        self.on_scrollback: Callable[[], None] | None = None
        self.on_save: Callable[[], None] | None = None
        self.on_load: Callable[[], None] | None = None
        self.on_config: Callable[[], None] | None = None
        self.on_menu: Callable[[], None] | None = None
        self.on_qsave: Callable[[], None] | None = None
        self.on_qload: Callable[[], None] | None = None
        self.on_movie: Callable[[dict], None] | None = None
        self.on_chapter_card: Callable[[dict], None] | None = None
        # True while a modal scene is pushed above the dialogue. The authored
        # world keeps drawing underneath, but dialogue chrome is suppressed so
        # its box/quick rail cannot bleed through menus and save overlays.
        self._covered_by_overlay = False
        self._current_line = None
        self._current_presentation: ScenePresentation | None = None
        # Opaque ownership of the exact Channel-0 playback for the current
        # line. A path alone is insufficient because adjacent lines may reuse
        # the same voice asset.
        self._voice_playback_handle = None
        self._scene_started = False
        self._pending_choices = False
        # A load redraws an already-rendered presentation. This flag suppresses
        # transient audio and history writes while that visual state is rebuilt.
        self._restoring_resume: bool = False
        # Raw NVL transcript entries are retained because RichText stores a
        # parsed document, not the original markup string.
        self._nvl_transcript: list[dict[str, str | None]] = []
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
        # Typewriter blip throttle (plays a soft tick while text reveals).
        self._blip_timer: float = 0.0
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
        # JSON-native descriptors parallel the pygame-only slot surfaces. They
        # let a save reconstruct even legacy string/expression portraits whose
        # effective slot spec is None.
        self._slot_resume: dict[str, dict | None] = {
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
        # Cached readability scrim (light global veil + bottom gradient band),
        # rebuilt only on a resolution change.
        self._scrim_surf: pygame.Surface | None = None
        # Cached cinematic CG backdrop (blurred cover-fit of the current CG),
        # keyed by (path, w, h).
        self._cg_fill_cache: tuple | None = None
        # Last (character, expression) settled in each slot, for emotion-change
        # auto-emote. None until a slot is first populated.
        self._slot_expr: dict[str, tuple[str, str] | None] = {
            "left": None, "center": None, "right": None,
        }

        # Background transition.
        self._bg_surface: pygame.Surface | None = None
        self._bg_fade: BackgroundFade | None = None
        self._bg_fade_from_path: str | None = None

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
        self._ambient_directive: dict | None = None
        self._adv_metrics = None

    def enter(self, *, scene_id: str | None = None,
              on_done: Callable[[], None] | None = None,
              on_resume_failed: Callable[[], None] | None = None,
              on_scrollback: Callable[[], None] | None = None,
              on_save: Callable[[], None] | None = None,
              on_load: Callable[[], None] | None = None,
              on_config: Callable[[], None] | None = None,
              on_menu: Callable[[], None] | None = None,
              on_qsave: Callable[[], None] | None = None,
              on_qload: Callable[[], None] | None = None,
              on_movie: Callable[[dict], None] | None = None,
              on_chapter_card: Callable[[dict], None] | None = None,
              resume_context: dict | None = None,
              **_) -> None:
        self.scene_id = scene_id
        self.on_done = on_done
        self.on_resume_failed = on_resume_failed
        self.on_scrollback = on_scrollback
        self.on_save = on_save
        self.on_load = on_load
        self.on_config = on_config
        self.on_menu = on_menu
        self.on_qsave = on_qsave
        self.on_qload = on_qload
        self.on_movie = on_movie
        self.on_chapter_card = on_chapter_card
        self._covered_by_overlay = False
        # Fresh rollback history per scene (rewind stays within this scene).
        self._history = (StateHistory()
                         if getattr(self.ctx.config, "rollback_enabled", True)
                         else None)
        self._rewinding = False
        sw, sh = self.ctx.screen_size
        # Accessibility text-size: scale the box height with the font so larger
        # text still fits. The measured viewport keeps three standard rows (two
        # on short canvases) plus a separate footer, then paginates a longer
        # authored line without changing its narrative presentation.
        text_scale = max(1.0, min(1.4, getattr(self.ctx.config, "text_scale", 1.0)))
        layout_density = max(0.8, sh / 720.0)
        chrome_density = max(
            0.01,
            float(getattr(self.ctx.fonts, "scale", 1.0)),
        )
        margin = max(16, int(round(32 * layout_density)))
        body_lines = 2 if sh < 540 else 3
        body_font_size = int(getattr(
            self.ctx.config, "font_size_dialogue", 24))
        speaker_font_size = int(getattr(
            self.ctx.config, "font_size_speaker", 28))
        self._adv_metrics = DialogueBox.measure(
            self.ctx.fonts,
            self.ctx.theme,
            text_scale=text_scale,
            density=layout_density,
            body_font_size=body_font_size,
            speaker_font_size=speaker_font_size,
            body_lines=body_lines,
        )
        box_h = self._adv_metrics.preferred_height
        # The reading rail is always pointer/gamepad friendly, not only when
        # touch emulation is enabled. Its visual labels are compact, so the
        # 44 px hit floor does not make the rail visually dominant.
        bar_h = max(44, int(round(36 * layout_density)))
        # FontRegistry already multiplies requested sizes by screen_height/720,
        # so keep this requested size constant instead of double-scaling it.
        bar_font_size = 16
        # Keep the six controls visually grouped at large resolutions. The
        # glyphs scale with the canvas, while the gap stops growing after
        # 1080p so the rail does not reclaim the portrait/CG safe area.
        bar_gap = max(4, int(round(4 * min(layout_density, 1.5))))
        bindings = BindingProfile(
            getattr(self.ctx.config, "input_bindings", {}),
        )
        advance_key = bindings.primary_display(
            InputAction.CONFIRM_ADVANCE
        )
        advance_hint = self.ctx.t(
            "dialogue_continue_hint",
            "按 {binding} / 點擊 繼續",
            binding=advance_key,
        )
        next_page_hint = self.ctx.t(
            "dialogue_next_page_hint",
            "按 {binding} / 點擊 下一頁",
            binding=advance_key,
        )
        if getattr(self.ctx.config, "touch_mode", False):
            bar_h = max(bar_h, MIN_TOUCH_TARGET_PX)
            bar_gap = max(bar_gap, self.ctx.theme.pad_s)
        # ADV uses the bottom dialogue box; NVL uses a tall transcript panel.
        # Both expose the same set_line/force_reveal/fully_revealed/update/draw
        # surface so everything downstream (advance, skip, auto-play) is shared.
        if getattr(self.ctx.config, "nvl_mode", False):
            nvl_margin = max(24, int(round(60 * layout_density)))
            bar_bottom_margin = max(
                14,
                int(round(14 * layout_density)),
            )
            bar_y = sh - bar_h - bar_bottom_margin
            overflow_top = bar_y - bar_gap - bar_h
            nvl_bottom = max(
                nvl_margin + 1,
                overflow_top - self.ctx.theme.pad_s,
            )
            self.box = NVLBox(
                pygame.Rect(nvl_margin, nvl_margin,
                            sw - nvl_margin * 2, nvl_bottom - nvl_margin),
                fonts=self.ctx.fonts, theme=self.ctx.theme,
                text_speed=self.ctx.config.text_speed,
                chrome=self.ctx.ui_chrome,
                density=chrome_density,
                text_scale=text_scale,
                body_font_size=body_font_size,
                speaker_font_size=speaker_font_size,
                continue_hint=advance_hint,
                motion=self._motion,
            )
        else:
            nameplate_clearance = (
                self._adv_metrics.nameplate_height // 2
                + max(2, int(round(4 * layout_density)))
            )
            bar_y = (
                (sh - box_h - margin)
                - bar_h
                - nameplate_clearance
            )
            self.box = DialogueBox(
                pygame.Rect(margin, sh - box_h - margin,
                            sw - margin * 2, box_h),
                fonts=self.ctx.fonts, theme=self.ctx.theme,
                text_speed=self.ctx.config.text_speed,
                text_scale=text_scale,
                chrome=self.ctx.ui_chrome,
                density=chrome_density,
                metrics=self._adv_metrics,
                body_font_size=body_font_size,
                speaker_font_size=speaker_font_size,
                body_lines=body_lines,
                continue_hint=advance_hint,
                next_page_hint=next_page_hint,
                motion=self._motion,
            )
        self.choices = ChoiceMenu(
            pygame.Rect(0, 0, sw, sh),
            fonts=self.ctx.fonts, theme=self.ctx.theme,
            on_choose=self._on_choose,
            chrome=self.ctx.ui_chrome,
            assets=self.ctx.assets,
            controls_hint=self.ctx.t(
                "choice_controls_hint",
                "{left}／{right}／滑鼠選擇 · {confirm} 確認",
                left=bindings.primary_display(InputAction.NAV_LEFT),
                right=bindings.primary_display(InputAction.NAV_RIGHT),
                confirm=advance_key,
            ),
            prompt=self.ctx.t("choice_prompt", "請選擇"),
            card_prompt=self.ctx.t(
                "choice_card_prompt",
                "請選擇接下來的方向",
            ),
            locked_label=self.ctx.t("choice_locked", "尚未開放"),
            scroll_hint=self.ctx.t(
                "choice_scroll_hint",
                "↑／↓／滾輪閱讀",
            ),
            text_scale=text_scale,
            motion=self._motion,
        )
        self.choices.visible = False
        self._sound_caption_overlay = SoundCaptionOverlay(
            (sw, sh),
            fonts=self.ctx.fonts,
            theme=self.ctx.theme,
            localization=getattr(self.ctx, "localization", None),
            chrome=getattr(self.ctx, "ui_chrome", None),
            text_scale=text_scale,
        )
        # Portrait position: above the dialogue box, centered.
        portrait_h = max(
            1,
            sh - box_h - margin - int(round(60 * layout_density)),
        )
        portrait_w = max(1, int(round(480 * layout_density)))
        self.portrait = PortraitView(
            pygame.Rect(
                sw // 2 - portrait_w // 2,
                max(1, int(round(30 * layout_density))),
                        portrait_w, portrait_h),
            self.ctx.assets, side="center", motion=self._motion,
        )
        # Persistent quick-menu bar (VN convention): a slim row of compact
        # actions just above the text box in ADV (near the bottom in NVL).
        # Scale non-decorative controls with the logical canvas. The previous
        # fixed 14 px text was undersized at both 720p and 1080p even though the
        # dialogue body itself scaled correctly.
        # The overflow rail expands upward. Large text on a short canvas can
        # otherwise place that row above y=0; keep both rows inside the safe
        # area even when this slightly reduces the textbox clearance.
        bar_y = max(
            bar_y,
            bar_h + bar_gap + self.ctx.theme.pad_s,
        )
        self._quick_bar = QuickMenuBar(
            sw - margin, bar_y, fonts=self.ctx.fonts, theme=self.ctx.theme,
            height=bar_h, font_size=bar_font_size, gap=bar_gap, left=margin,
            chrome=self.ctx.ui_chrome,
            density=chrome_density,
            motion=self._motion,
            items=[
                (self.ctx.t("auto", "自動"), self._toggle_auto_play,
                 lambda: self.auto_play_enabled),
                (self.ctx.t("skip", "快進"), self._trigger_skip,
                 lambda: self._skip_active),
                (self.ctx.t("history", "記錄"), on_scrollback, None),
                (self.ctx.t("quick_save", "快存"), self.on_qsave, None),
                (self.ctx.t("hide_ui", "隱藏"), self._hide_ui, None),
            ],
            overflow_label=self.ctx.t("more", "更多"),
            # These conventional, language-neutral marks are visual labels
            # only. Button.label and the tooltip retain each localized full
            # action name for inspection and accessibility adapters.
            primary_glyphs=("A", ">>", "LOG", "QS", "UI−", "…"),
            overflow_items=[
                (self.ctx.t("quick_load", "快讀"), self.on_qload, None),
                (self.ctx.t("save", "存檔"), on_save, None),
                (self.ctx.t("load", "讀取"), on_load, None),
                (self.ctx.t("settings", "設定"), on_config, None),
                (self.ctx.t("menu", "完整選單"), on_menu, None),
            ],
        )
        if resume_context is not None:
            if self.restore_resume_context(resume_context):
                return
            # An explicit resume is never permission to restart the authored
            # scene.  The saved state already contains the line's effects and
            # cursor; falling through to _start() would replay both.  Close this
            # failed overlay through a dedicated callback: normal on_done also
            # checks ambient hooks and could start unrelated authored content.
            if self.on_resume_failed is not None:
                cb = self.on_resume_failed
                self.on_resume_failed = None
                cb()
            return
        if scene_id is not None:
            self._start(scene_id)

    def _start(self, scene_id: str) -> None:
        pres = self.ctx.dialogue.start_scene(scene_id)
        self._scene_started = True
        self._render_presentation(pres)

    def exit(self) -> None:
        """Release this dialogue's voice when the screen is truly removed.

        Modal overlays call :meth:`pause`, so backlog/save/replay can preserve
        or temporarily checkpoint the live line. A title/load replacement calls
        ``exit`` and must not leave abandoned speech over the next screen.
        """
        if self.choices is not None:
            self.choices.visible = False
            self.choices.release_render_cache()
        current_handle = getattr(
            self.ctx.assets,
            "current_voice_handle",
            None,
        )
        current_path = getattr(
            self.ctx.assets,
            "current_voice_path",
            None,
        )
        authored_path = getattr(self._current_line, "voice", None)
        if (
            (
                self._voice_playback_handle is not None
                and current_handle == self._voice_playback_handle
            )
            or (
                isinstance(authored_path, str)
                and authored_path
                and current_path == authored_path
            )
        ):
            self.ctx.assets.stop_voice()
        self._voice_playback_handle = None

    def pause(self) -> None:
        """Hide dialogue chrome while a modal overlay is above this scene."""
        self._covered_by_overlay = True
        if self._quick_bar is not None:
            self._quick_bar.close_overflow()

    def resume(self) -> None:
        self._covered_by_overlay = False
        # The Controls overlay can change the confirm binding while this
        # dialogue remains underneath it. Refresh the footer from the live
        # profile without rebuilding the box or disturbing reveal/page state.
        bindings = BindingProfile(
            getattr(self.ctx.config, "input_bindings", {}),
        )
        advance_key = bindings.primary_display(InputAction.CONFIRM_ADVANCE)
        self.box.continue_hint = self.ctx.t(
            "dialogue_continue_hint",
            "按 {binding} / 點擊 繼續",
            binding=advance_key,
        )
        if isinstance(self.box, DialogueBox):
            self.box.next_page_hint = self.ctx.t(
                "dialogue_next_page_hint",
                "按 {binding} / 點擊 下一頁",
                binding=advance_key,
            )
        # A replay/backlog/music-room overlay may have interrupted Channel 0
        # and restored this line through an exact PCM tail. That creates a new
        # ownership handle; adopt it only when it still matches this authored
        # line so subsequent saves can checkpoint the continued clip.
        voice_path = getattr(self._current_line, "voice", None)
        capture_voice = getattr(
            self.ctx.assets,
            "capture_voice_playback",
            None,
        )
        if voice_path and callable(capture_voice):
            try:
                checkpoint = capture_voice(expected_path=voice_path)
            except Exception:
                checkpoint = None
            self._voice_playback_handle = (
                getattr(self.ctx.assets, "current_voice_handle", None)
                if checkpoint is not None
                else None
            )
        elif not voice_path:
            self._voice_playback_handle = None
        # Settings can change text_scale while this retained scene is covered.
        # Re-measure captions and reflow a live decision by stable choice id
        # before the next frame, without reconstructing or advancing dialogue.
        live_text_scale = max(
            1.0,
            min(1.4, float(getattr(self.ctx.config, "text_scale", 1.0))),
        )
        if self.choices is not None:
            self.choices.set_text_scale(live_text_scale)
        self._sync_sound_caption_layout(self.ctx.screen_size)

    def _text_reveal_cursor(self) -> int | None:
        """Return the live typewriter cursor for the active text body."""
        body = self._active_text_body()
        cursor = getattr(body, "reveal_chars", None)
        return int(cursor) if isinstance(cursor, (int, float)) else None

    def _text_page_index(self) -> int | None:
        """Return the ADV visual page without changing narrative state."""
        if isinstance(self.box, DialogueBox):
            return self.box.page_index
        return None

    def _text_plain_cursor(self) -> int | None:
        """Return the resolution-independent authored-character cursor."""
        body = self._active_text_body()
        if body is None:
            return None
        source, displayed = self._active_plain_texts(body)
        return self._display_to_source_plain_cursor(
            source,
            displayed,
            body.plain_cursor_for_reveal(),
        )

    def _text_wait_timeline(self) -> dict[str, object] | None:
        """Return layout-independent progress for the active line's waits."""
        body = self._active_text_body()
        if body is None:
            return None
        timeline = body.export_wait_timeline()
        source, displayed = self._active_plain_texts(body)
        return self._project_wait_timeline(
            timeline,
            source=source,
            displayed=displayed,
            to_display=False,
        )

    def _text_document_fully_revealed(self) -> bool:
        """Preserve the v1 save field's whole-authored-line semantics."""
        body = self._active_text_body()
        if body is None:
            return bool(self.box is None or self.box.fully_revealed())
        source, displayed = self._active_plain_texts(body)
        canonical_cursor = self._display_to_source_plain_cursor(
            source,
            displayed,
            body.plain_cursor_for_reveal(),
        )
        return canonical_cursor == len(source)

    def _active_text_body(self):
        """Return the RichText currently carrying the authored line."""
        if isinstance(self.box, NVLBox):
            if self.box.entries:
                return self.box.entries[-1].body
            return None
        if isinstance(self.box, DialogueBox):
            return self.box.body
        return getattr(self.box, "body", None)

    def _active_plain_texts(self, body) -> tuple[str, str]:
        """Pair canonical plain copy with the text measured by the widget."""
        line_text = getattr(self._current_line, "text", "")
        source = parse(line_text).plain_text() if isinstance(line_text, str) else ""
        displayed = body.plain_text()
        return source, displayed

    @staticmethod
    def _plain_line_lengths(text: str) -> list[tuple[int, int]]:
        """Return ``(body, newline)`` lengths using policy-compatible splits."""
        rows: list[tuple[int, int]] = []
        for line in text.splitlines(keepends=True):
            if line.endswith("\r\n"):
                ending = 2
            elif line.endswith(("\n", "\r")):
                ending = 1
            else:
                ending = 0
            rows.append((len(line) - ending, ending))
        return rows

    @classmethod
    def _display_to_source_plain_cursor(
        cls,
        source: str,
        displayed: str,
        cursor: int,
    ) -> int:
        """Project a display-only expansion cursor back to authored text."""
        bounded = max(0, min(int(cursor), len(displayed)))
        source_rows = cls._plain_line_lengths(source)
        display_rows = cls._plain_line_lengths(displayed)
        if len(source_rows) != len(display_rows):
            return min(bounded, len(source))

        source_at = 0
        display_at = 0
        for (source_body, source_end), (display_body, display_end) in zip(
            source_rows,
            display_rows,
            strict=True,
        ):
            if display_body < source_body or display_end != source_end:
                return min(bounded, len(source))
            if bounded <= display_at + display_body:
                return source_at + min(bounded - display_at, source_body)
            source_at += source_body
            display_at += display_body
            if bounded <= display_at + display_end:
                return source_at + min(bounded - display_at, source_end)
            source_at += source_end
            display_at += display_end
        return len(source)

    @classmethod
    def _source_to_display_plain_cursor(
        cls,
        source: str,
        displayed: str,
        cursor: int,
        *,
        complete_to_end: bool = False,
    ) -> int:
        """Project an authored cursor into the active display-only expansion."""
        bounded = max(0, min(int(cursor), len(source)))
        if complete_to_end and bounded == len(source):
            return len(displayed)
        source_rows = cls._plain_line_lengths(source)
        display_rows = cls._plain_line_lengths(displayed)
        if len(source_rows) != len(display_rows):
            return min(bounded, len(displayed))

        source_at = 0
        display_at = 0
        for (source_body, source_end), (display_body, display_end) in zip(
            source_rows,
            display_rows,
            strict=True,
        ):
            if display_body < source_body or display_end != source_end:
                return min(bounded, len(displayed))
            if bounded <= source_at + source_body:
                return display_at + (bounded - source_at)
            source_at += source_body
            display_at += display_body
            if bounded <= source_at + source_end:
                return display_at + (bounded - source_at)
            source_at += source_end
            display_at += display_end
        return len(displayed)

    @classmethod
    def _project_wait_timeline(
        cls,
        timeline: dict[str, object] | None,
        *,
        source: str,
        displayed: str,
        to_display: bool,
    ) -> dict[str, object] | None:
        """Keep persisted wait positions canonical across display expansion."""
        if timeline is None:
            return None
        projector = (
            cls._source_to_display_plain_cursor
            if to_display
            else cls._display_to_source_plain_cursor
        )
        consumed_raw = timeline.get("consumed_plain_positions", [])
        consumed = [
            projector(source, displayed, position)
            for position in consumed_raw
            if type(position) is int
        ]
        active_raw = timeline.get("active")
        active = None
        if isinstance(active_raw, dict):
            position = active_raw.get("plain_position")
            remaining = active_raw.get("remaining_seconds")
            if type(position) is int:
                active = {
                    "plain_position": projector(source, displayed, position),
                    "remaining_seconds": remaining,
                }
        return {
            "consumed_plain_positions": sorted(set(consumed)),
            "active": active,
        }

    def _resume_display_plain_cursor(
        self,
        body,
        canonical_cursor: int | None,
        raw_reveal_cursor: int | None,
        *,
        fully_revealed: bool,
    ) -> int | None:
        """Map a validated canonical save cursor into the current widget."""
        if canonical_cursor is None:
            return None
        source, displayed = self._active_plain_texts(body)
        if raw_reveal_cursor is not None:
            raw_display_cursor = body.plain_cursor_for_reveal(
                raw_reveal_cursor,
            )
            if self._display_to_source_plain_cursor(
                source,
                displayed,
                raw_display_cursor,
            ) == canonical_cursor:
                return raw_display_cursor
        return self._source_to_display_plain_cursor(
            source,
            displayed,
            canonical_cursor,
            complete_to_end=fully_revealed,
        )

    def _visual_timeline(self) -> dict[str, object]:
        """Export reconstructible presentation clocks, never raw surfaces.

        Full-frame transitions, shake/flash/emote, and plugin particle phase
        hold non-serializable runtime state. Their declared fallback is to keep
        the committed destination and settle/drop the transient on restore.
        """
        background_fade = None
        fade = self._bg_fade
        if fade is not None and not fade.done:
            background_fade = {
                "from": self._bg_fade_from_path,
                "to": self.background_path,
                "elapsed_seconds": float(fade.t),
                "duration_seconds": float(fade.duration),
                "easing": getattr(fade, "easing_name", "linear"),
            }

        camera = None
        if not self._camera.done:
            camera = {
                "from": [float(value) for value in self._camera._from],
                "to": [float(value) for value in self._camera._to],
                "elapsed_seconds": float(self._camera.t),
                "duration_seconds": float(self._camera.duration),
                "easing": getattr(
                    self._camera,
                    "easing_name",
                    "linear",
                ),
            }

        tint = None
        if self._tint is not None and not self._tint.done:
            tint = {
                "color": list(self._tint.color),
                "max_alpha": int(self._tint.max_alpha),
                "elapsed_seconds": float(self._tint.t),
                "duration_seconds": float(self._tint.duration),
                "easing": getattr(self._tint, "easing_name", "linear"),
            }

        background_blur = None
        if (
            self._bg_blur_dur > 0.0
            and self._bg_blur_t < self._bg_blur_dur
            and self._bg_blur != self._bg_blur_target
        ):
            background_blur = {
                "from": float(self._bg_blur_from),
                "to": float(self._bg_blur_target),
                "elapsed_seconds": float(self._bg_blur_t),
                "duration_seconds": float(self._bg_blur_dur),
            }

        weather = None
        if (
            self._ambient is not None
            and self._ambient_fade_dir != 0
            and self._ambient_fade_dur > 0.0
        ):
            weather = {
                "lifecycle": (
                    "active" if self._ambient_fade_dir > 0 else "clearing"
                ),
                "base_alpha": int(self._ambient_base_alpha),
                "current_alpha": int(getattr(self._ambient, "alpha", 255)),
                "elapsed_seconds": float(self._ambient_fade_t),
                "duration_seconds": float(self._ambient_fade_dur),
                "backend_phase": "restart",
            }

        return {
            "version": 2,
            "background_fade": background_fade,
            "camera": camera,
            "tint": tint,
            "background_blur": background_blur,
            "weather": weather,
            # A portrait backend instance owns plugin-specific runtime phase
            # that cannot be serialized generically. Slot identity/spec is
            # exact, while the backend deterministically starts at phase zero.
            "portrait_backend_phase": "restart",
            "unreconstructible_fallback": "settle",
        }

    def export_resume_context(self) -> dict | None:
        """Snapshot the active dialogue presentation as JSON-native metadata.

        ``GameState`` already owns the authoritative story cursor. This
        context contains only the presentation needed to put the player back
        on the same screen without calling ``DialogueEngine.start_scene`` or
        ``next_line`` (which would repeat narrative side effects).
        """
        current = self._current_presentation
        story = getattr(self.ctx.state, "story", None)
        story_scene = getattr(story, "current_scene", None)
        if (current is None or current.kind not in ("line", "choice")
                or not story_scene):
            return None
        authored_scene = story.get(story_scene)
        story_line_index = getattr(story, "current_line_index", None)
        if (
            authored_scene is None
            or type(story_line_index) is not int
            or not 0 <= story_line_index <= len(authored_scene.lines)
        ):
            return None
        story_cursor_at_end = story_line_index == len(authored_scene.lines)
        story_cursor_loc_id = (
            None
            if story_cursor_at_end
            else authored_scene.lines[story_line_index].loc_id
        )

        try:
            wait_timeline = (
                self._text_wait_timeline()
                if current.kind == "line" else None
            )
        except (ArithmeticError, RuntimeError, TypeError, ValueError):
            return None

        voice_playback = None
        capture_voice = getattr(
            self.ctx.assets,
            "capture_voice_playback",
            None,
        )
        voice_field_supported = callable(capture_voice)
        if (
            voice_field_supported
            and current.kind == "line"
            and current.line is not None
            and current.line.voice
            and self._voice_playback_handle is not None
        ):
            try:
                voice_playback = capture_voice(
                    expected_path=current.line.voice,
                    handle=self._voice_playback_handle,
                )
            except Exception:
                # A new save must never turn a checkpoint failure into replaying
                # already-heard speech. Explicit null is the silent contract.
                voice_playback = None

        last_line = None
        if (self._current_line is not None
                and getattr(self._current_line, "scene_id", None)
                == story_scene):
            last_line = presentation_to_dict(ScenePresentation(
                kind="line",
                line=self._current_line,
                scene_id=getattr(self._current_line, "scene_id", story_scene),
                background=getattr(
                    self._current_line, "background", self.background_path),
            ))

        tint = None
        if self._tint is not None:
            tint = {
                "color": list(self._tint.color),
                # Persist the live alpha, not the original fade target. Loading
                # settles transient animation at the exact currently-visible
                # strength.
                "alpha": int(self._tint.alpha()),
            }
        context = {
            "version": 1,
            "screen": "dialogue",
            "locale": str(
                getattr(self.ctx.localization, "effective", "") or ""
            ),
            "locale_source_digest": str(
                getattr(self.ctx.localization, "source_digest", "") or ""
            ),
            "scene_id": self.scene_id or story_scene,
            "story_scene": story_scene,
            "story_line_index": story_line_index,
            "story_cursor_loc_id": story_cursor_loc_id,
            "story_cursor_at_end": story_cursor_at_end,
            "presentation": presentation_to_dict(current),
            "last_line": last_line,
            "background": self.background_path,
            "cg": self.cg_surface_path,
            "background_overridden": self._bg_overridden,
            "cg_overridden": self._cg_overridden,
            "slots": {
                slot: self._slot_resume.get(slot)
                for slot in ("left", "center", "right")
            },
            "speaking_slot": self._speaking_slot,
            "camera": {
                "zoom": float(self._camera.zoom),
                "pan_x": float(self._camera.pan_x),
                "pan_y": float(self._camera.pan_y),
            },
            "tint": tint,
            "background_blur": float(self._bg_blur),
            "weather": self._ambient_directive,
            "current_bgm": self.ctx.state.meta.get("current_bgm"),
            "current_ambient": self.ctx.state.meta.get("current_ambient"),
            "ui_hidden": bool(self._ui_hidden),
            "text_fully_revealed": self._text_document_fully_revealed(),
            "text_page_fully_revealed": bool(
                self.box is None or self.box.fully_revealed()),
            "text_reveal_cursor": self._text_reveal_cursor(),
            "text_page_index": self._text_page_index(),
            "text_plain_cursor": self._text_plain_cursor(),
            "text_wait_timeline": wait_timeline,
            "nvl_transcript": list(self._nvl_transcript),
            "visual_timeline": self._visual_timeline(),
        }
        if voice_field_supported:
            # Missing means a legacy save whose voice restarts. New engine saves
            # always make the distinction explicit, including silent/choice and
            # unavailable-backend states.
            context["voice_playback"] = voice_playback
        # Convert tuples/enums from plugin result metadata into the same native
        # JSON types that will come back from disk. Opaque diagnostic values
        # are stringified; none of them is used to execute narrative logic.
        try:
            native = json.loads(json.dumps(
                context, ensure_ascii=False, allow_nan=False, default=str,
            ))
        except (OverflowError, TypeError, ValueError):
            return None
        # Do not write a semantically poisonous envelope when an authored or
        # plugin-provided presentation exceeds the renderer-safe resume bounds.
        # Returning None keeps the state save loadable through the legacy
        # exploration fallback.
        if not resume_matches_state(
            native,
            self.ctx.state,
            localization=self.ctx.localization,
        ):
            return None
        return native

    def _restore_slot_state(self, raw_slots: dict) -> None:
        sw, sh = self.ctx.screen_size
        for slot in ("left", "center", "right"):
            raw = raw_slots.get(slot)
            spec = None
            surface = None
            backend = None
            descriptor = None
            if isinstance(raw, dict):
                if isinstance(raw.get("spec"), dict):
                    try:
                        spec = PortraitSpec.model_validate(raw["spec"])
                        surface, backend = self._resolve_slot(spec, sw, sh)
                        descriptor = {
                            "spec": spec.model_dump(mode="json"),
                        }
                    except Exception:
                        spec = surface = backend = None
                elif raw.get("kind") == "legacy":
                    portrait = raw.get("portrait")
                    if portrait is not None and not isinstance(portrait, str):
                        portrait = None
                    speaker = raw.get("speaker")
                    speaker_id = raw.get("speaker_id")
                    expression = raw.get("expression")
                    if not isinstance(speaker_id, str) or not speaker_id:
                        speaker_id = self._legacy_speaker_id(speaker)
                    surface = self._surface_for_portrait(
                        portrait, speaker_id, expression)
                    if surface is not None:
                        descriptor = {
                            "kind": "legacy",
                            "portrait": portrait,
                            "speaker": speaker,
                            "speaker_id": speaker_id,
                            "expression": expression,
                            "slot": slot,
                        }
            self._slot_surfaces[slot] = surface
            self._slot_specs[slot] = spec
            self._slot_backends[slot] = backend
            self._slot_resume[slot] = descriptor
            self._slot_fades[slot] = None
            self._slot_anims[slot] = None
            self._slot_emotes[slot] = None
            self._slot_expr[slot] = (
                (spec.character, spec.expression) if spec is not None else None
            )

    def _restore_text_state(self, resume: dict) -> None:
        if isinstance(self.box, NVLBox):
            transcript = resume.get("nvl_transcript")
            if isinstance(transcript, list) and transcript:
                self.box.reset()
                restored: list[dict[str, str | None]] = []
                for entry in transcript:
                    if not isinstance(entry, dict):
                        continue
                    speaker = entry.get("speaker")
                    speaker_id = entry.get("speaker_id")
                    text = entry.get("text")
                    if speaker is not None and not isinstance(speaker, str):
                        speaker = str(speaker)
                    if not isinstance(speaker_id, str) or not speaker_id:
                        speaker_id = self._legacy_speaker_id(speaker)
                    if not isinstance(text, str):
                        continue
                    self.box.set_line(
                        (
                            self._display_text(speaker)
                            if speaker is not None
                            else None
                        ),
                        self._display_text(text),
                        speaker_color=self._speaker_color(speaker_id),
                    )
                    restored_entry = {"speaker": speaker, "text": text}
                    if speaker_id is not None:
                        restored_entry["speaker_id"] = speaker_id
                    restored.append(restored_entry)
                self._nvl_transcript = restored
                self._nvl_scene_id = resume.get("story_scene")
            elif isinstance(transcript, list):
                # An ADV save intentionally has no accumulated NVL transcript.
                # When the player changes presentation mode before loading,
                # rebuild the current/last line as the first NVL entry instead
                # of clearing it with the valid empty ADV list. Reset first so a
                # direct same-scene restore cannot retain an abandoned transcript.
                line = self._current_line
                self.box.reset()
                if line is not None:
                    speaker_id = self._line_speaker_id(line)
                    self.box.set_line(
                        (
                            self._display_text(line.speaker)
                            if line.speaker is not None
                            else None
                        ),
                        self._display_text(line.text),
                        speaker_color=self._speaker_color(speaker_id),
                    )
                    transcript_entry = {
                        "speaker": line.speaker,
                        "text": line.text,
                    }
                    if speaker_id is not None:
                        transcript_entry["speaker_id"] = speaker_id
                    self._nvl_transcript = [transcript_entry]
                else:
                    self._nvl_transcript = []
                self._nvl_scene_id = resume.get("story_scene")

        fully_revealed = bool(resume.get("text_fully_revealed", False))
        page_fully_revealed = bool(
            resume.get("text_page_fully_revealed", fully_revealed),
        )
        cursor = resume.get("text_reveal_cursor")
        plain_cursor = resume.get("text_plain_cursor")
        wait_timeline = resume.get("text_wait_timeline")
        if not isinstance(wait_timeline, dict):
            wait_timeline = None
        if type(plain_cursor) is not int:
            plain_cursor = None
        page_index = resume.get("text_page_index")
        if type(page_index) is not int:
            page_index = None
        body = None
        restored_adv_page = False
        if isinstance(self.box, NVLBox):
            if self.box.entries:
                body = self.box.entries[-1].body
        elif isinstance(self.box, DialogueBox):
            body = self.box.body
            source, displayed = self._active_plain_texts(body)
            display_plain_cursor = self._resume_display_plain_cursor(
                body,
                plain_cursor,
                cursor if type(cursor) is int else None,
                fully_revealed=fully_revealed,
            )
            display_wait_timeline = self._project_wait_timeline(
                wait_timeline,
                source=source,
                displayed=displayed,
                to_display=True,
            )
            self.box.restore_page(
                page_index,
                cursor if isinstance(cursor, int) else None,
                display_plain_cursor,
                wait_timeline=display_wait_timeline,
            )
            restored_adv_page = True
        elif self.box is not None:
            body = getattr(self.box, "body", None)
        if (
            (fully_revealed or page_fully_revealed)
            and restored_adv_page
            and cursor is None
            and plain_cursor is None
        ):
            # Only a legacy envelope with no cursor needs a synthetic reveal.
            # A document-complete legacy line lands on its final page; a newer
            # page-complete envelope stays on its stored/inferred page.
            if fully_revealed:
                self.box.reveal_last_page()
            self.box.force_reveal()
        elif fully_revealed and not restored_adv_page:
            if self.box is not None:
                self.box.force_reveal()
        elif (
            not restored_adv_page
            and body is not None
            and (isinstance(cursor, int) or plain_cursor is not None)
        ):
            source, displayed = self._active_plain_texts(body)
            display_plain_cursor = self._resume_display_plain_cursor(
                body,
                plain_cursor,
                cursor if type(cursor) is int else None,
                fully_revealed=fully_revealed,
            )
            display_wait_timeline = self._project_wait_timeline(
                wait_timeline,
                source=source,
                displayed=displayed,
                to_display=True,
            )
            restored_cursor = (
                body.reveal_cursor_for_plain(display_plain_cursor)
                if display_plain_cursor is not None
                else max(0, int(cursor))
            )
            body.restore_reveal(
                restored_cursor,
                wait_timeline=display_wait_timeline,
            )

    def _restore_audio_state(self, resume: dict,
                             current: ScenePresentation) -> None:
        # Loading replaces the active voice timeline even when the restored
        # line is silent or is a choice prompt.  Without this cut, a voice clip
        # from the abandoned pre-load screen continues over the restored view.
        self.ctx.assets.stop_voice()
        self._voice_playback_handle = None

        # A v1 envelope may predate these additive presentation fields. Missing
        # means "derive from the already-loaded state"; an explicit null still
        # means silence. This distinction keeps minimal-v1 saves compatible.
        bgm = (
            resume.get("current_bgm")
            if "current_bgm" in resume
            else self.ctx.state.meta.get("current_bgm")
        )
        if not bgm or bgm in _AUDIO_SILENCE_SENTINELS:
            self.ctx.assets.stop_music(fade_ms=0)
        elif isinstance(bgm, str):
            # Loading is a replacement boundary, not a normal crossfade. If
            # the saved track differs, cut the abandoned live track before
            # attempting the saved asset so a missing/corrupt replacement
            # fails to silence instead of leaking pre-load audio. Reissuing
            # the same path remains a no-op and preserves its playback point.
            current_music = getattr(
                self.ctx.assets,
                "current_music_path",
                None,
            )
            if current_music != bgm:
                self.ctx.assets.stop_music(fade_ms=0)
            self.ctx.assets.play_music(
                bgm, volume=self.ctx.config.bgm_volume, fade_ms=0)

        ambient = (
            resume.get("current_ambient")
            if "current_ambient" in resume
            else self.ctx.state.meta.get("current_ambient")
        )
        if not ambient or ambient in _AUDIO_SILENCE_SENTINELS:
            self.ctx.assets.stop_ambient(fade_ms=0)
        elif isinstance(ambient, str):
            current_ambient = getattr(
                self.ctx.assets,
                "current_ambient_path",
                None,
            )
            if current_ambient != ambient:
                self.ctx.assets.stop_ambient(fade_ms=0)
            self.ctx.assets.play_ambient(
                ambient, volume=self.ctx.config.sfx_volume * 0.55,
                fade_ms=0)

        # Missing checkpoint is a legacy v1 save and keeps historical
        # restart-from-zero compatibility. New checkpoints distinguish playing
        # from completed/stopped, and a failed exact seek remains silent.
        if current.kind == "line" and current.line is not None \
                and current.line.voice:
            vol = self._voice_volume(self._line_speaker_id(current.line))
            if "voice_playback" not in resume:
                self._voice_playback_handle = self.ctx.assets.play_voice(
                    current.line.voice,
                    volume=vol,
                )
                return
            checkpoint = resume.get("voice_playback")
            if (
                isinstance(checkpoint, dict)
                and checkpoint.get("version") == 2
                and checkpoint.get("state") == "playing"
            ):
                resume_voice = getattr(self.ctx.assets, "resume_voice", None)
                if callable(resume_voice):
                    outcome = resume_voice(
                        current.line.voice,
                        position_seconds=checkpoint["position_seconds"],
                        duration_seconds=checkpoint["duration_seconds"],
                        pcm_sha256=checkpoint["pcm_sha256"],
                        mixer_format=checkpoint["mixer_format"],
                        volume=vol,
                    )
                    if outcome == "exact":
                        self._voice_playback_handle = getattr(
                            self.ctx.assets,
                            "current_voice_handle",
                            None,
                        )

    def restore_resume_context(self, resume: dict) -> bool:
        """Rebuild a saved dialogue screen without advancing narrative state."""
        if self._scene_started and not self._restoring_resume:
            return self._restore_live_resume_context(resume)
        story = getattr(self.ctx.state, "story", None)
        story_scene = getattr(story, "current_scene", None)
        scene = (
            story.get(story_scene)
            if story is not None and story_scene else None
        )
        if scene is None:
            return False
        try:
            current, last_line = resume_presentations_for_state(
                resume,
                self.ctx.state,
                localization=self.ctx.localization,
            )
            self._restore_validated_resume(
                resume, current, last_line, story_scene,
            )
        except Exception:
            # Resume metadata is an untrusted persistence envelope. Validation
            # rejects known malformed shapes before reconstruction, while this
            # final guard also contains asset/backend failures. Never fall
            # through to _start(): the loaded state already includes the line's
            # narrative effects and cursor.
            self._rewinding = False
            self._restoring_resume = False
            return False
        return True

    def _restore_live_resume_context(self, resume: dict) -> bool:
        """Transactionally replace a live scene through a fresh UI state.

        App loads normally restore into a newly constructed DialogueScene. This
        path gives direct/tool callers the same atomicity: renderer or backend
        failure cannot leave the existing box half-rewound.
        """
        previous = self.__dict__
        ctx = self.ctx
        callbacks = {
            "on_done": self.on_done,
            "on_scrollback": self.on_scrollback,
            "on_save": self.on_save,
            "on_load": self.on_load,
            "on_config": self.on_config,
            "on_menu": self.on_menu,
            "on_qsave": self.on_qsave,
            "on_qload": self.on_qload,
            "on_movie": self.on_movie,
            "on_chapter_card": self.on_chapter_card,
        }
        resume_failed = self.on_resume_failed
        audio_snapshot = None
        capture_audio = getattr(
            ctx.assets,
            "capture_audio_playback",
            None,
        )
        if callable(capture_audio):
            try:
                audio_snapshot = capture_audio()
            except Exception:
                # A callable snapshot API declares that audio is part of this
                # transaction. If it cannot establish a rollback point, fail
                # before replacing widgets or touching any mixer channel.
                return False
        try:
            # Keep the old dictionary (and every mutable widget it owns)
            # untouched until a completely fresh scene has restored.
            self.__dict__ = {}
            DialogueScene.__init__(self, ctx)
            self.enter(
                scene_id=getattr(ctx.state.story, "current_scene", None),
                on_resume_failed=None,
                resume_context=resume,
                **callbacks,
            )
            if not self._scene_started:
                raise ValueError("fresh dialogue resume did not complete")
        except Exception:
            self.__dict__ = previous
            restore_audio = getattr(
                ctx.assets,
                "restore_audio_playback",
                None,
            )
            if audio_snapshot is not None and callable(restore_audio):
                try:
                    restore_audio(audio_snapshot)
                    authored_path = getattr(self._current_line, "voice", None)
                    if (
                        authored_path
                        and getattr(
                            ctx.assets,
                            "current_voice_path",
                            None,
                        ) == authored_path
                    ):
                        self._voice_playback_handle = getattr(
                            ctx.assets,
                            "current_voice_handle",
                            None,
                        )
                    elif not authored_path:
                        self._voice_playback_handle = None
                except Exception:
                    # The UI/state transaction is still rolled back even when
                    # an already-failing external mixer cannot be recovered.
                    pass
            return False
        self.on_resume_failed = resume_failed
        return True

    def _restore_validated_resume(
        self,
        resume: dict,
        current: ScenePresentation,
        last_line: ScenePresentation | None,
        story_scene: str,
    ) -> None:
        """Restore a context already accepted by the shared pure validator."""
        self.scene_id = str(resume.get("scene_id") or story_scene)
        self._restoring_resume = True
        self._rewinding = True
        try:
            # A choice presentation carries no line of its own; render the last
            # line first to restore its text/portrait/CG, then layer choices.
            if current.kind == "choice" and last_line is not None:
                self._render_presentation(last_line)
            self._render_presentation(current)
        finally:
            self._rewinding = False
            self._restoring_resume = False

        # Missing additive v1 keys preserve what _render_presentation rebuilt
        # from the serialized line/choice. Explicit null remains a deliberate
        # clear produced by hide_background/hide_cg presentation state.
        if "background" in resume:
            background = resume.get("background")
            if background is None or isinstance(background, str):
                self.background_path = background
                self._bg_surface = (
                    self.ctx.assets.scaled(
                        background, self.ctx.screen_size, fit="cover")
                    if background else None
                )
        if "cg" in resume:
            cg = resume.get("cg")
            self.cg_surface_path = cg if isinstance(cg, str) else None
        self._cg_fill_cache = None
        self._bg_fade = None
        self._bg_fade_from_path = None
        self._scene_transition = None
        if "background_overridden" in resume:
            self._bg_overridden = bool(resume["background_overridden"])
        if "cg_overridden" in resume:
            self._cg_overridden = bool(resume["cg_overridden"])
        self._fx_scene_id = story_scene

        raw_slots = resume.get("slots")
        if isinstance(raw_slots, dict):
            self._restore_slot_state(raw_slots)
        speaking = resume.get("speaking_slot")
        self._speaking_slot = (
            speaking if speaking in ("left", "center", "right") else None
        )
        self._portrait_scene_id = story_scene

        camera = resume.get("camera")
        if isinstance(camera, dict):
            try:
                self._camera = Camera(
                    zoom=float(camera.get("zoom", 1.0)),
                    pan_x=float(camera.get("pan_x", 0.0)),
                    pan_y=float(camera.get("pan_y", 0.0)),
                )
            except (TypeError, ValueError):
                self._camera = Camera()
        tint = resume.get("tint")
        if isinstance(tint, dict):
            try:
                self._tint = ColorTint(
                    color=tint.get("color", (0, 0, 0)),
                    duration=0.0,
                    max_alpha=int(tint.get("alpha", 0)),
                )
            except (TypeError, ValueError):
                self._tint = None
        else:
            self._tint = None
        try:
            self._bg_blur = max(
                0.0, float(resume.get("background_blur", 0.0)))
        except (TypeError, ValueError):
            self._bg_blur = 0.0
        self._bg_blur_from = self._bg_blur_target = self._bg_blur
        self._bg_blur_t = self._bg_blur_dur = 0.0
        self._bg_blur_cache = None

        weather = resume.get("weather")
        if isinstance(weather, dict):
            directive = dict(weather)
            directive["fade"] = 0.0
            self._apply_set_weather(directive)
        else:
            self._ambient = None
            self._ambient_name = None
            self._ambient_directive = None

        self._restore_visual_timeline(resume)

        self._ui_hidden = (
            bool(resume.get("ui_hidden", False))
            if current.kind == "line" else False
        )
        self._restore_text_state(resume)
        self._restore_audio_state(resume, current)
        self._scene_started = True
        if self._history is not None:
            self._history.record(self.ctx.state, current)

    def _restore_visual_timeline(self, resume: dict) -> None:
        """Resume validated semantic tweens or deterministically settle them."""
        timeline = resume.get("visual_timeline")
        if not isinstance(timeline, dict):
            return
        reduced = self._motion.reduced

        background_fade = timeline.get("background_fade")
        if isinstance(background_fade, dict) and not reduced:
            old_path = background_fade.get("from")
            old_surface = (
                self.ctx.assets.scaled(
                    old_path,
                    self.ctx.screen_size,
                    fit="cover",
                )
                if isinstance(old_path, str) and old_path
                else None
            )
            fade = BackgroundFade(
                old_surface,
                self._bg_surface,
                duration=float(background_fade["duration_seconds"]),
                easing=background_fade["easing"],
            )
            fade.t = float(background_fade["elapsed_seconds"])
            self._bg_fade = fade
            self._bg_fade_from_path = old_path

        camera = timeline.get("camera")
        if isinstance(camera, dict):
            source = tuple(float(value) for value in camera["from"])
            target = tuple(float(value) for value in camera["to"])
            if reduced:
                self._camera = Camera(
                    zoom=target[0],
                    pan_x=target[1],
                    pan_y=target[2],
                )
            else:
                restored = Camera(
                    zoom=source[0],
                    pan_x=source[1],
                    pan_y=source[2],
                )
                restored._from = source
                restored._to = target
                restored.duration = float(camera["duration_seconds"])
                restored.t = float(camera["elapsed_seconds"])
                restored.easing_name = str(camera["easing"])
                restored._ease = resolve_easing(restored.easing_name)
                restored.update(0.0)
                self._camera = restored

        tint = timeline.get("tint")
        if isinstance(tint, dict):
            restored_tint = ColorTint(
                color=tint["color"],
                duration=(
                    0.0 if reduced else float(tint["duration_seconds"])
                ),
                max_alpha=int(tint["max_alpha"]),
                easing=tint["easing"],
            )
            if not reduced:
                restored_tint.t = float(tint["elapsed_seconds"])
            self._tint = restored_tint

        blur = timeline.get("background_blur")
        if isinstance(blur, dict):
            if reduced:
                self._bg_blur = 0.0
                self._bg_blur_from = self._bg_blur_target = 0.0
                self._bg_blur_t = self._bg_blur_dur = 0.0
            else:
                self._bg_blur_from = float(blur["from"])
                self._bg_blur_target = float(blur["to"])
                self._bg_blur_t = float(blur["elapsed_seconds"])
                self._bg_blur_dur = float(blur["duration_seconds"])
                fraction = self._bg_blur_t / self._bg_blur_dur
                self._bg_blur = self._bg_blur_from + (
                    self._bg_blur_target - self._bg_blur_from
                ) * fraction
                self._bg_blur_cache = None

        weather = timeline.get("weather")
        if isinstance(weather, dict):
            lifecycle = weather["lifecycle"]
            if reduced:
                if lifecycle == "clearing":
                    self._ambient = None
                    self._ambient_name = None
                    self._ambient_directive = None
                elif self._ambient is not None:
                    self._ambient_fade_dir = 0
                    self._ambient_fade_t = self._ambient_fade_dur = 0.0
                    self._ambient_base_alpha = int(weather["base_alpha"])
                    try:
                        self._ambient.alpha = self._ambient_base_alpha
                    except Exception:
                        pass
            elif self._ambient is not None:
                self._ambient_base_alpha = int(weather["base_alpha"])
                self._ambient_fade_dir = (
                    1 if lifecycle == "active" else -1
                )
                self._ambient_fade_t = float(weather["elapsed_seconds"])
                self._ambient_fade_dur = float(weather["duration_seconds"])
                try:
                    self._ambient.alpha = int(weather["current_alpha"])
                except Exception:
                    pass

    def _on_choose(self, choice_id: str) -> None:
        pres = self.ctx.dialogue.choose(choice_id)
        self._render_presentation(pres)

    def _advance(self) -> None:
        # A manual advance cuts the current line's voice immediately.
        self.ctx.assets.stop_voice()
        self._voice_playback_handle = None
        pres = self.ctx.dialogue.next_line()
        self._render_presentation(pres)

    def _advance_page_or_line(self) -> bool:
        """Advance the visual page first, then the authored line.

        Returns True when only the view changed. Narrative state, rollback,
        history, effects and voice all remain attached to the one authored
        presentation until its final page is left.
        """
        if isinstance(self.box, DialogueBox) and self.box.advance_page():
            if self._quick_bar is not None:
                self._quick_bar.close_overflow()
            self._auto_play_timer = 0.0
            return True
        self._advance()
        return False

    def _legacy_speaker_id(self, speaker: str | None) -> str | None:
        """Resolve display copy only for presentations made by old engines.

        Current presentations carry ``speaker_id``.  This narrow compatibility
        path keeps old saves and manually-constructed embedding presentations
        working when their source-language speaker still maps to an NPC.  An
        unknown label remains an identity token, but strict id lookup below
        safely gives it no NPC-backed colour, portrait, or backend.
        """
        if not speaker:
            return None
        npcs = getattr(self.ctx, "npcs", None)
        by_name = getattr(npcs, "by_name", None)
        if not callable(by_name):
            return speaker
        try:
            npc = by_name(speaker)
        except Exception:
            npc = None
        npc_id = getattr(npc, "id", None)
        return npc_id if isinstance(npc_id, str) and npc_id else speaker

    def _display_text(self, text: str) -> str:
        """Apply the session's audit policy at the widget boundary only."""
        policy = getattr(self.ctx, "presentation_text", None)
        apply = getattr(policy, "apply", None)
        return apply(text) if callable(apply) else text

    def _display_choice_card(
        self,
        card: ChoiceCardSpec | None,
    ) -> ChoiceCardSpec | None:
        """Build an expanded visual card without mutating canonical choice data."""
        if card is None:
            return None
        details = [
            detail.model_copy(update={
                "label": self._display_text(detail.label),
                "text": self._display_text(detail.text),
            })
            for detail in card.details
        ]
        return card.model_copy(update={
            "title": self._display_text(card.title),
            "subtitle": self._display_text(card.subtitle),
            "badge": self._display_text(card.badge),
            "image_alt": self._display_text(card.image_alt),
            "details": details,
            "tags": [self._display_text(tag) for tag in card.tags],
            "action": self._display_text(card.action),
        })

    def _line_speaker_id(self, line) -> str | None:
        """Return the line's stable identity with a legacy-only fallback."""
        speaker_id = getattr(line, "speaker_id", None)
        if isinstance(speaker_id, str) and speaker_id:
            return speaker_id
        return self._legacy_speaker_id(getattr(line, "speaker", None))

    def _npc_for_speaker_id(self, speaker_id: str | None):
        """Look up an NPC strictly by stable id."""
        if not speaker_id:
            return None
        npcs = getattr(self.ctx, "npcs", None)
        get_npc = getattr(npcs, "get", None)
        if not callable(get_npc):
            return None
        try:
            return get_npc(speaker_id)
        except Exception:
            return None

    def _speaker_color(self, speaker_id: str | None) -> tuple | None:
        """Resolve a speaker's name-plate colour from its NPC ``name_color``.

        Returns an RGB tuple parsed from the character's ``name_color``
        ("#rgb" / "#rrggbb" / named colour), or None for narration, an unknown
        speaker, or one that declares no colour (the box then keeps the theme
        accent). Never raises — a malformed colour string resolves to None.
        """
        npc = self._npc_for_speaker_id(speaker_id)
        raw = getattr(npc, "name_color", None) if npc is not None else None
        if not raw:
            return None
        try:
            from ..dialogue.richtext import _parse_color
            return _parse_color(raw)
        except Exception:
            return None

    def _voice_volume(self, speaker_id: str | None) -> float:
        """Resolve a per-character override by stable identity only."""
        overrides = (
            getattr(self.ctx.config, "per_character_voice_volume", {}) or {}
        )
        value = overrides.get(speaker_id, self.ctx.config.voice_volume)
        try:
            return max(0.0, min(1.0, float(value)))
        except (TypeError, ValueError):
            return 1.0

    # ------------------------------------------------------------------
    # Portrait helpers
    # ------------------------------------------------------------------

    def _surface_for_portrait(self, portrait: str | PortraitSpec | None,
                               speaker_id: str | None,
                               expression: str | None) -> pygame.Surface | None:
        """Resolve a portrait field (str path, spec, or None) to a Surface."""
        sw, sh = self.ctx.screen_size
        portrait_h = sh - 230 - 32 - 60   # same as enter()
        fallback = (480, portrait_h)
        if isinstance(portrait, PortraitSpec):
            return self.ctx.assets.resolve_portrait(portrait, fallback_size=fallback)
        if isinstance(portrait, str):
            return self.ctx.assets.image(portrait, fallback_size=fallback)
        # Fall back to the stable speaker's expression/default portrait. This
        # is what static-backend NPCs use for bare ``expression:`` lines.
        npc = self._npc_for_speaker_id(speaker_id)
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
        speaker_id = self._line_speaker_id(line)
        npc = self._npc_for_speaker_id(speaker_id)
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

    def _apply_npc_backend(self, spec: PortraitSpec | None) -> PortraitSpec | None:
        """Route an explicit portrait spec through its character's resting
        backend when the spec doesn't name one itself.

        Packs stage heroines with ``portrait: {character, expression}`` specs,
        which default to ``backend: "static"`` — so those lines used to render as
        dead stills even when the NPC declares a ``portrait_backend`` (breath /
        layered). This pins the exact same still the static path would show and
        only adds the resting animation, so explicitly-staged portraits breathe
        like the expression-only ones. A spec that names its own backend, or a
        character with no declared backend, is returned unchanged.
        """
        if spec is None or (spec.backend and spec.backend != "static"):
            return spec
        npcs = getattr(self.ctx, "npcs", None)
        npc = npcs.get(spec.character) if npcs is not None \
            and hasattr(npcs, "get") else None
        if npc is None:
            return spec
        backend = getattr(npc, "portrait_backend", "static") or "static"
        if backend == "static":
            return spec
        still = spec.image
        if not still and hasattr(npc, "portrait_for"):
            try:
                still = npc.portrait_for(spec.expression)
            except Exception:
                still = None
        npc_args = dict(getattr(npc, "portrait_backend_args", {}) or {})
        return spec.model_copy(update={
            "backend": backend,
            "backend_args": {**npc_args, **(spec.backend_args or {})},
            "image": still,
        })

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

        # Accessibility (reduce_motion): a sliding / rising / popping / bouncing
        # entrance is a vestibular trigger, so when the setting is on we drop the
        # translation and settle the slot with a plain alpha crossfade instead —
        # the same "keep the gentle dissolve, drop the motion" rule the camera /
        # scene FX use. The portrait still appears (and at its staged rect via
        # the crossfade), it just doesn't travel.
        reduce_motion = self._motion.reduced
        fade_duration = self._motion.duration(0.25, MotionKind.OPACITY)

        animated = self._spec_has_staging(new_spec) or (
            old_spec is not None and old_spec.exit
            and old_spec.exit != "none"
        )

        if not animated:
            if old_surf is None and new_surf is not None and not reduce_motion:
                # Genuine ENTRANCE (slot was empty): drift up + fade in, so a
                # character *arrives* rather than just materialising. A one-time
                # transition, not idle breathing — the baseline 演出 lift for
                # static sprites. Expression-changes (old_surf present) fall
                # through to the in-place crossfade so the face just morphs.
                target = self._slot_rect(slot, sw, sh, new_spec)
                self._slot_fades[slot] = None
                self._slot_anims[slot] = SlotAnimation(
                    kind="enter", rect=target, duration=0.34,
                    new=new_surf, anim="rise",
                    flip=new_spec.flip if new_spec else False)
            else:
                self._slot_anims[slot] = None
                self._slot_fades[slot] = PortraitCrossfade(
                    old_surf, new_surf, duration=fade_duration)
            self._slot_surfaces[slot] = new_surf
            self._slot_specs[slot] = new_spec
            return

        target = self._slot_rect(slot, sw, sh, new_spec)
        self._slot_fades[slot] = None
        if reduce_motion:
            # Honour the staged rect (offset/scale/flip) but drop the slide: a
            # straight crossfade at the target rect, or a fade-out for a leave.
            self._slot_anims[slot] = SlotAnimation(
                kind="crossfade",
                rect=(self._slot_rect(slot, sw, sh, old_spec)
                      if new_surf is None and old_spec is not None else target),
                duration=fade_duration, old=old_surf, new=new_surf,
                flip=(new_spec.flip if new_spec else
                      (old_spec.flip if old_spec else False)),
            )
        elif new_surf is not None and new_spec is not None and new_spec.enter \
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
        speaker_id = self._line_speaker_id(line)
        # Portraits belong to a scene. When the story scene changes, drop any
        # staged sprites so they can't leak into the next scene; within a scene
        # they persist (see the narration branch below). Defensive getattr chain
        # so minimal test contexts without a full state/story still work.
        _story = getattr(getattr(self.ctx, "state", None), "story", None)
        cur_sid = getattr(_story, "current_scene", None)
        if cur_sid != self._portrait_scene_id:
            self._portrait_scene_id = cur_sid
            for _s in ("left", "center", "right"):
                self._slot_backends[_s] = None
                self._slot_surfaces[_s] = None
                self._slot_specs[_s] = None
                self._slot_resume[_s] = None
                self._slot_fades[_s] = None
                self._slot_anims[_s] = None
                self._slot_expr[_s] = None
            # Weather is scene-scoped too: drop any ambient overlay so a scene's
            # dust/rain doesn't leak into the next. A new scene's first line that
            # carries a set_weather effect re-applies it from the fx queue this
            # same frame, so opted-in scenes are unaffected.
            if self._ambient is not None:
                self._ambient = None
                self._ambient_name = None
                self._ambient_fade_dir = 0
                self._ambient_directive = None
        # Pure narration (no portrait + no speaker-backed sprite + no speaker):
        # keep whatever is already on screen so a character stays put while the
        # narrator describes the scene, instead of popping out every narration
        # line (the "slideshow" feel). True only within a scene — a scene change
        # cleared the slots just above.
        _has_portrait = bool(line.portraits) or bool(line.portrait)
        if (not _has_portrait and not speaker_id
                and self._character_backend_spec(line) is None):
            self._speaking_slot = None
            return
        self._speaking_slot = None
        if line.portraits:
            # Multi-slot: clear all then populate from the spec list.
            wanted: dict[str, tuple[pygame.Surface | None,
                                    PortraitSpec | None, object | None]] = {
                "left": (None, None, None), "center": (None, None, None),
                "right": (None, None, None),
            }
            for spec in line.portraits:
                spec = self._apply_npc_backend(spec)
                surf, backend = self._resolve_slot(spec, sw, sh)
                wanted[spec.slot] = (surf, spec, backend)
                # The slot whose character is speaking drives lip-sync + speaker
                # emphasis. Portrait character ids and speaker identity share
                # the same non-localized namespace.
                if speaker_id and spec.character == speaker_id:
                    self._speaking_slot = spec.slot
            for slot, (surf, spec, backend) in wanted.items():
                self._slot_resume[slot] = (
                    {"spec": spec.model_dump(mode="json")}
                    if spec is not None else None
                )
                # Unchanged portrait: keep the live backend instance so its
                # breathing phase stays continuous across consecutive lines
                # instead of resetting (the per-line stutter) every line.
                if (spec is not None and spec == self._slot_specs.get(slot)
                        and self._slot_backends.get(slot) is not None):
                    continue
                self._slot_backends[slot] = backend
                self._start_slot_transition(slot, surf, spec, sw, sh)
        else:
            # Single-portrait / legacy path. A bare string / expression goes to
            # ``center`` unless the line carries ``portrait_pos`` (the author
            # shorthand for placing the simple forms); an explicit spec is
            # honoured at its own ``slot`` (so ``portrait: {character, slot:
            # left}`` / ``{character, position: left}`` place it directly).
            pos = getattr(line, "portrait_pos", None)
            target_slot = pos if pos in ("left", "center", "right") else "center"
            if isinstance(line.portrait, PortraitSpec):
                cur_spec = self._apply_npc_backend(line.portrait)
                # An explicit spec's own slot wins; portrait_pos only overrides
                # it when the spec left slot at the default centre.
                if cur_spec.slot == "center" and target_slot != "center":
                    cur_spec = cur_spec.model_copy(update={"slot": target_slot})
                target_slot = cur_spec.slot
                cur_surf, cur_backend = self._resolve_slot(cur_spec, sw, sh)
            else:
                cur_spec = self._character_backend_spec(line)
                if cur_spec is not None:
                    if target_slot != "center":
                        cur_spec = cur_spec.model_copy(
                            update={"slot": target_slot})
                    target_slot = cur_spec.slot
                    cur_surf, cur_backend = self._resolve_slot(cur_spec, sw, sh)
                else:
                    cur_surf = self._surface_for_portrait(
                        line.portrait, speaker_id, line.expression)
                    cur_backend = None
            if cur_spec is not None:
                self._slot_resume[target_slot] = {
                    "spec": cur_spec.model_dump(mode="json"),
                }
            elif cur_surf is not None:
                self._slot_resume[target_slot] = {
                    "kind": "legacy",
                    "portrait": (
                        line.portrait
                        if isinstance(line.portrait, str) else None
                    ),
                    "speaker": line.speaker,
                    "speaker_id": speaker_id,
                    "expression": line.expression,
                    "slot": target_slot,
                }
            else:
                self._slot_resume[target_slot] = None
            # Single portrait == the speaker on screen, so it drives lip-sync.
            if cur_surf is not None and speaker_id:
                self._speaking_slot = target_slot
            # Unchanged portrait: keep the live backend instance (continuous
            # breathing) rather than re-spawning and resetting it every line.
            if (cur_spec is not None
                    and cur_spec == self._slot_specs.get(target_slot)
                    and self._slot_backends.get(target_slot) is not None):
                pass
            else:
                self._slot_backends[target_slot] = cur_backend
                self._start_slot_transition(
                    target_slot, cur_surf, cur_spec, sw, sh)
            # Clear whichever of the three slots this portrait did NOT take, so a
            # single portrait never leaves a stale sprite in another slot.
            for _other in ("left", "center", "right"):
                if _other == target_slot:
                    continue
                self._slot_backends[_other] = None
                self._slot_resume[_other] = None
                self._start_slot_transition(_other, None, None, sw, sh)
        self._apply_auto_emotes()

    # Expression keywords that read as high-energy (→ a livelier emote) vs.
    # subdued (→ a small nod); anything else gets a gentle default bounce.
    _ENERGETIC_EXPR = frozenset({
        "excited", "surprised", "scared", "angry", "radiant", "playing",
        "determined", "happy", "shocked", "panic",
    })
    _SUBDUED_EXPR = frozenset({
        "sad", "crying", "worried", "embarrassed", "shy", "nervous",
        "distant", "relieved", "annoyed",
    })

    def _emote_for_expression(self, expr: str) -> tuple[str, float]:
        e = (expr or "").lower()
        if e in self._ENERGETIC_EXPR:
            return ("bounce", 22.0)
        if e in self._SUBDUED_EXPR:
            return ("nod", 15.0)
        return ("bounce", 11.0)

    def _apply_auto_emotes(self) -> None:
        """Play a small one-shot emote when a slot keeps the same character but
        changes expression — the E-mote 'reacts to emotion' feel, without the
        author writing an emote on every line.

        Gated by ``config.auto_emote_on_emotion``. Never fires on first
        appearance, a character swap, or when an emote is already running on the
        slot. The per-slot last-expression is tracked either way so toggling the
        feature on mid-scene doesn't fire a burst of stale reactions.
        """
        enabled = (
            getattr(
                getattr(self.ctx, "config", None),
                "auto_emote_on_emotion",
                True,
            )
            and self._motion.allows(MotionKind.SPATIAL)
        )
        for slot in ("left", "center", "right"):
            spec = self._slot_specs.get(slot)
            if spec is None:
                self._slot_expr[slot] = None
                continue
            key = (spec.character, getattr(spec, "expression", "default"))
            prev = self._slot_expr.get(slot)
            if (enabled and prev is not None and prev[0] == key[0]
                    and prev[1] != key[1]
                    and self._slot_emotes.get(slot) is None):
                kind, intensity = self._emote_for_expression(key[1])
                # 0.5s so the reaction's tail still shows after the ~0.25s
                # expression crossfade settles.
                self._slot_emotes[slot] = PortraitEmote(
                    kind=kind, duration=0.5, intensity=intensity)
            self._slot_expr[slot] = key

    # ------------------------------------------------------------------
    # Background helper
    # ------------------------------------------------------------------

    def _update_background(self, new_path: str) -> None:
        """Start a BackgroundFade when the background changes."""
        if new_path == self.background_path:
            return
        sw, sh = self.ctx.screen_size
        old_surf = self._bg_surface
        old_path = self.background_path
        new_surf = self.ctx.assets.scaled(new_path, (sw, sh), fit="cover")
        self._bg_fade = BackgroundFade(
            old_surf,
            new_surf,
            duration=self._motion.duration(0.6, MotionKind.OPACITY),
        )
        self._bg_surface = new_surf
        self.background_path = new_path
        self._bg_fade_from_path = old_path

    # ------------------------------------------------------------------

    def _render_presentation(self, pres) -> None:
        if pres is None:
            return
        if (
            pres.kind != "choice"
            and self.choices is not None
            and self.choices.visible
        ):
            # A choice can retain close to 90 MiB of stock 4K layers, plus the
            # finite plugin-chrome working set. Release only when narrative
            # control genuinely leaves that presentation; pause()/resume() for
            # Settings and other overlays intentionally keeps it warm.
            self.choices.visible = False
            self.choices.release_render_cache()
        # More is contextual to the line currently being read. Never carry an
        # expanded disclosure row into a new line, scene, or decision.
        if self._quick_bar is not None:
            self._quick_bar.close_overflow()
        if pres.kind in ("line", "choice"):
            self._current_presentation = pres
            if pres.scene_id:
                # A chained scene continues in the same DialogueScene instance.
                # Keep the scene-level resume identity aligned with the engine
                # instead of retaining the id originally passed to enter().
                self.scene_id = pres.scene_id
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
            if self._sound_caption_overlay is not None:
                self._sound_caption_overlay.set_captions(
                    getattr(line, "sound_captions", ()),
                )
            # NVL transcript is per-scene: clear it whenever the active story
            # scene changes (a chained transition starts a new scene, so the
            # previous scene's lines should not bleed into the new one).
            if isinstance(self.box, NVLBox):
                cur_sid = self.ctx.state.story.current_scene
                if cur_sid != self._nvl_scene_id:
                    self.box.reset()
                    self._nvl_transcript.clear()
                    self._nvl_scene_id = cur_sid
            speaker_id = self._line_speaker_id(line)
            self.box.set_line(
                (
                    self._display_text(line.speaker)
                    if line.speaker is not None
                    else None
                ),
                self._display_text(line.text),
                speaker_color=self._speaker_color(speaker_id),
            )
            if isinstance(self.box, NVLBox) and not self._restoring_resume:
                transcript_entry = {
                    "speaker": line.speaker,
                    "text": line.text,
                }
                if speaker_id is not None:
                    transcript_entry["speaker_id"] = speaker_id
                self._nvl_transcript.append(transcript_entry)
            # SFX/BGM/ambient. play_ambient carries over by path, so re-issuing
            # the scene's bed every line is a no-op; it only fades when the bed
            # actually changes.
            if not self._restoring_resume:
                if line.bgm:
                    if line.bgm in _AUDIO_SILENCE_SENTINELS:
                        # Sentinel: cut the music to silence — the Key/WA2
                        # gut-punch move. stop_music() (a clean fade) already
                        # exists in the asset layer but was unreachable from
                        # pack content; a line's bgm carries forward as
                        # current_bgm, so the silence persists until a later
                        # line names a track.
                        self.ctx.assets.stop_music()
                    else:
                        self.ctx.assets.play_music(
                            line.bgm, volume=self.ctx.config.bgm_volume)
                if getattr(line, "ambient", None):
                    if line.ambient in _AUDIO_SILENCE_SENTINELS:
                        # Keep the ambient bed symmetric with BGM: an authored
                        # silence sentinel must stop the inherited room tone,
                        # not resolve as a missing file and leave it playing.
                        self.ctx.assets.stop_ambient()
                    else:
                        self.ctx.assets.play_ambient(
                            line.ambient,
                            volume=self.ctx.config.sfx_volume * 0.55)
                if line.sfx:
                    self.ctx.assets.play_sound(
                        line.sfx, volume=self.ctx.config.sfx_volume)
                # Voice: cut any in-flight clip, then play this line's (if any).
                self.ctx.assets.stop_voice()
                self._voice_playback_handle = None
                if line.voice:
                    # Per-character override falls back to the global voice
                    # volume for any speaker not listed in the config dict.
                    vol = self._voice_volume(speaker_id)
                    self._voice_playback_handle = self.ctx.assets.play_voice(
                        line.voice,
                        volume=vol,
                    )
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
                npc = self._npc_for_speaker_id(speaker_id)
                if npc is not None:
                    self.portrait.show(npc.portrait_for(line.expression))
                else:
                    self.portrait.show(None)
            self.choices.visible = False
            self._sync_sound_caption_layout(self.ctx.screen_size)
        elif pres.kind == "choice":
            self._ui_hidden = False   # a decision always shows the UI
            # Carry each choice's lock reason (4th tuple element) so a locked
            # option renders WHY it is locked, not just a greyed ghost.
            self.choices.set_choices(
                [(
                    c.id,
                    self._display_text(c.text),
                    c.enabled,
                    self._display_text(getattr(c, "reason", "")),
                    self._display_choice_card(getattr(c, "card", None)),
                )
                 for c in pres.choices])
            self.choices.visible = True
        elif pres.kind == "transition":
            if pres.next_scene:
                # Recursively render the first line of the chained scene
                # rather than discarding it.
                next_pres = self.ctx.dialogue.start_scene(pres.next_scene)
                self._render_presentation(next_pres)
            else:
                if self._sound_caption_overlay is not None:
                    self._sound_caption_overlay.set_captions(())
                self._end()
        elif pres.kind == "end":
            if self._sound_caption_overlay is not None:
                self._sound_caption_overlay.set_captions(())
            self._end()

        # Record this display for player rollback. Only line / choice displays
        # are recorded (not transitions / ends), and never while we are redrawing
        # a rewound presentation (self._rewinding) — otherwise a rewind would
        # immediately re-push what it just restored. The presentation is stored
        # as the payload so a rewind redraws it without re-running the engine
        # (which would re-fire the line's effects / dialogue ops).
        if (self._history is not None and not self._rewinding
                and not self._restoring_resume
                and pres.kind in ("line", "choice")):
            self._history.record(self.ctx.state, pres)
        # Presentation effects are queued by pure GameState handlers. Apply the
        # directives before announcing the commit so autosave sees the same
        # background/CG/tint/weather/camera state that the next draw will use;
        # the private queue itself is intentionally stripped from save data.
        if not self._rewinding and not self._restoring_resume:
            self._consume_visual_fx()
        # Engine hooks can request an autosave while a line/choice transaction
        # is still mutating state. This UI-level commit signal fires only after
        # the new presentation is fully available for export, so persistence
        # plugins can flush one coherent state + screen pair.
        if not self._rewinding and not self._restoring_resume:
            from ..plugins import fire_event
            from ..plugins.context import HookEvent
            fire_event(
                self.ctx.state,
                HookEvent.DIALOGUE_PRESENTATION_READY,
                presentation=pres,
            )

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
        if self._quick_bar is not None:
            self._quick_bar.close_overflow()
        self._ui_hidden = True

    def _draw_hidden_hint(self, surface: pygame.Surface) -> None:
        sw, sh = surface.get_size()
        density = max(0.8, sh / 720.0)
        hint = self.ctx.fonts.render(
            "點擊 / 按 H 顯示介面", 14,
            (*self.ctx.theme.text_mute[:3], 150))
        surface.blit(
            hint,
            (
                sw - hint.get_width() - int(round(20 * density)),
                sh - hint.get_height() - int(round(16 * density)),
            ),
        )

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
        if isinstance(self.box, DialogueBox) and self.box.reveal_last_page():
            return
        # Skipping cuts the current line's voice immediately.
        self.ctx.assets.stop_voice()
        self._voice_playback_handle = None
        if self.ctx.config.skip_unread_only:
            pres = self.ctx.dialogue.skip_to_next_unread()
        else:
            pres = self.ctx.dialogue.skip_all()
        if pres is not None:
            self._render_presentation(pres)
        # Skip-all reaching the end returns None; the scene's _present_current
        # already ended it (current_scene cleared). Route the synthetic end
        # through the normal renderer so queued presentation FX and deferred
        # autosaves still receive the same commit signal as manual advance.
        elif self.ctx.dialogue.current_scene() is None:
            self._render_presentation(ScenePresentation(kind="end"))

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
        motion = self._motion
        if fx == "camera_pan":
            self._camera.pan_to(d.get("x", 0.0), d.get("y", 0.0),
                                duration=motion.duration(
                                    d.get("duration", 0.6),
                                    MotionKind.SPATIAL,
                                ),
                                easing=d.get("easing"))
        elif fx == "camera_zoom":
            self._camera.zoom_to(d.get("scale", 1.0),
                                 duration=motion.duration(
                                     d.get("duration", 0.6),
                                     MotionKind.SPATIAL,
                                 ),
                                 easing=d.get("easing"))
        elif (
            fx == "screen_shake"
            and motion.allows(MotionKind.SPATIAL)
        ):
            self._shakes.append(ScreenShake(
                intensity=d.get("intensity", 12.0),
                duration=d.get("duration", 0.4),
                easing=d.get("easing")))
        elif (
            fx == "screen_flash"
            and motion.allows(MotionKind.FLASH)
        ):
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
                    duration=motion.duration(
                        d.get("duration", 0.5),
                        MotionKind.OPACITY,
                    ),
                    max_alpha=d.get("max_alpha", 120),
                    easing=d.get("easing"))
        elif fx == "screen_blur":
            clear = bool(d.get("clear"))
            if clear or motion.allows(MotionKind.SPATIAL):
                target = 0.0 if clear else float(d.get("radius", 8.0))
                self._start_bg_blur(
                    target,
                    motion.duration(
                        d.get("duration", 0.5),
                        MotionKind.SPATIAL,
                    ),
                )
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
        elif fx == "chapter_card":
            # Push the chapter title-card overlay via the app callback (same
            # pattern as play_movie). No callback wired → silently ignored,
            # which is what keeps headless / tests safe.
            if self.on_chapter_card is not None:
                self.on_chapter_card(d)

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
        if not self._motion.allows(MotionKind.SPATIAL):
            return
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
            self._ambient_directive = None
            return
        self._ambient = backend
        self._ambient_name = name
        self._ambient_directive = {
            "fx": "set_weather",
            "backend": name,
            "params": dict(params),
            "fade": float(d.get("fade", 0.0) or 0.0),
        }
        self._ambient_base_alpha = int(getattr(backend, "alpha", 255))
        fade = self._motion.duration(
            d.get("fade", 0.0) or 0.0,
            MotionKind.OPACITY,
        )
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
        fade = self._motion.duration(
            d.get("fade", 0.0) or 0.0,
            MotionKind.OPACITY,
        )
        if fade > 0.0 and self._ambient is not None:
            # Retain the set directive until fade-out completes so a mid-clear
            # save can reconstruct the backend and continue the lifecycle.
            try:
                self._ambient_base_alpha = max(
                    0,
                    min(255, int(getattr(self._ambient, "alpha", 255))),
                )
            except Exception:
                self._ambient_base_alpha = 255
            self._ambient_fade_dir = -1     # fade out, then drop in update()
            self._ambient_fade_t = 0.0
            self._ambient_fade_dur = fade
        else:
            self._ambient = None
            self._ambient_name = None
            self._ambient_fade_dir = 0
            self._ambient_directive = None

    def _advance_ambient(self, dt: float) -> None:
        if self._ambient is None:
            return
        if self._motion.continuous_allowed:
            try:
                self._ambient.update(dt)
            except Exception:
                self._ambient = None        # a broken backend is dropped
                self._ambient_name = None
                self._ambient_directive = None
                self._ambient_fade_dir = 0
                return
        if self._ambient_fade_dir != 0 and self._ambient_fade_dur > 0.0:
            if self._motion.reduced:
                # A preference change is live. Preserve the current alpha and
                # finish the existing hand-off within the same short-opacity
                # budget used by newly-authored weather fades.
                self._ambient_fade_dur = min(
                    self._ambient_fade_dur,
                    self._ambient_fade_t + self._motion.duration(
                        self._ambient_fade_dur,
                        MotionKind.OPACITY,
                    ),
                )
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
                    self._ambient_directive = None
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
        self._bg_fade_from_path = None
        self._bg_overridden = True
        self._begin_scene_transition(d.get("transition"))

    def _begin_scene_transition(self, tr: dict | None) -> None:
        """Spawn a :class:`SceneTransition` from the last composed world frame.

        ``tr`` is the transition sub-dict authored on the effect; a missing /
        malformed value falls back to a plain dissolve. A mask-style transition
        resolves its image path to a surface here (the scene owns asset access).
        """
        tr = tr if isinstance(tr, dict) else {}
        style, duration = self._motion.transition(
            str(tr.get("style", "dissolve")),
            tr.get("duration", 0.6),
        )
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
            duration=duration,
            color=tr.get("color", (0, 0, 0)),
            mask=mask_surf, easing=tr.get("easing"))

    def _advance_visual_fx(self, dt: float) -> None:
        if self._motion.reduced:
            # A live toggle settles active spatial/flash effects on the same
            # frame.  Camera targets are retained as static authored framing;
            # blur is removed, and transient hazards are discarded.
            self._camera.update(self._camera.duration)
            self._shakes.clear()
            self._flashes.clear()
            self._start_bg_blur(0.0, 0.0)
            if self._tint is not None:
                self._tint.update(self._tint.duration)
            if self._scene_transition is not None:
                self._scene_transition.style = "dissolve"
                self._scene_transition.mask = None
                remaining_cap = self._motion.duration(
                    0.6,
                    MotionKind.OPACITY,
                )
                self._scene_transition.duration = min(
                    self._scene_transition.duration,
                    self._scene_transition.t + remaining_cap,
                )
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
                if self._motion.reduced:
                    if anim.anim not in (None, "none"):
                        # Preserve the final staged rect/surface, but stop an
                        # entry/exit already travelling when Settings changed.
                        anim.update(anim.duration)
                    else:
                        anim.duration = min(
                            anim.duration,
                            anim.t + self._motion.duration(
                                anim.duration,
                                MotionKind.OPACITY,
                            ),
                        )
                anim.update(dt)
                if anim.done:
                    self._slot_anims[slot] = None
            fade = self._slot_fades.get(slot)
            if fade is not None:
                if self._motion.reduced:
                    fade.duration = min(
                        fade.duration,
                        fade.t + self._motion.duration(
                            fade.duration,
                            MotionKind.OPACITY,
                        ),
                    )
                fade.update(dt)
                if fade.done:
                    self._slot_fades[slot] = None
            emote = self._slot_emotes.get(slot)
            if emote is not None:
                if self._motion.reduced:
                    self._slot_emotes[slot] = None
                else:
                    emote.update(dt)
                    if emote.done:
                        self._slot_emotes[slot] = None
            # Advance the resting animation backend (breathing / sprite / rig).
            # The speaker's slot is "talking" while their line is still typing,
            # which a layered rig uses to drive lip-sync. Isolated: a backend
            # that raises is dropped, reverting the slot to the static blit
            # rather than crashing the frame.
            backend = self._slot_backends.get(slot)
            if backend is not None and self._motion.continuous_allowed:
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
            if self._motion.reduced:
                self._bg_fade.duration = min(
                    self._bg_fade.duration,
                    self._bg_fade.t + self._motion.duration(
                        self._bg_fade.duration,
                        MotionKind.OPACITY,
                    ),
                )
            self._bg_fade.update(dt)
            if self._bg_fade.done:
                self._bg_fade = None
                self._bg_fade_from_path = None

        # Hide-UI (非表示): the configured action toggles. While hidden, any
        # advance/cancel input restores the UI without advancing the story.
        if inp.pressed(InputAction.HIDE_UI):
            if self._ui_hidden:
                self._ui_hidden = False
            else:
                self._hide_ui()
            return
        if self._ui_hidden:
            if inp.advance_dialogue or inp.cancel:
                self._ui_hidden = False
            return

        # Quick-menu bar: update before the advance logic so a click that
        # lands on it is handled by the bar and does NOT also advance the line.
        bar_consumed = self._update_quick_bar(dt, inp)
        # A quick-menu action owns the whole frame. Besides suppressing manual
        # click-to-advance, this prevents an Auto timer expiring later in the
        # same update from advancing beneath a freshly opened overlay (or,
        # worse, immediately after quick-load replaced the state).
        if bar_consumed:
            return

        # Reading shortcuts. Escape lives here rather than in the App so the
        # overflow-close priority above is deterministic.
        if inp.mouse_wheel > 0 and self.on_scrollback:
            self.on_scrollback()
            return
        if inp.pressed(InputAction.OPEN_HISTORY) and self.on_scrollback:
            self.on_scrollback()
            return
        if (
            inp.pressed(InputAction.OPEN_MENU)
            or inp.pressed(InputAction.BACK)
        ) and self.on_menu:
            self.on_menu()
            return

        # Rollback on Backspace: rewind the game state to the previous line in
        # this scene and redraw it. Distinct from scrollback (wheel-up / B),
        # which is a read-only text log; rollback actually rewinds state.
        if (
            self._history is not None
            and inp.pressed(InputAction.ROLLBACK)
        ):
            if self._history.can_rewind():
                pres = self._history.rewind(self.ctx.state)
                if pres is not None:
                    self.ctx.assets.stop_voice()
                    self._voice_playback_handle = None
                    self._rewinding = True
                    try:
                        self._render_presentation(pres)
                        # Rollback revisits a line the player already read.
                        # Land on its final visual page, fully visible.
                        if isinstance(self.box, DialogueBox):
                            self.box.reveal_last_page()
                            self.box.force_reveal()
                    finally:
                        self._rewinding = False
            return

        # A fresh HOLD_SKIP press fires one skip step (which itself jumps all
        # the way to the next stopping point — see _trigger_skip). Its held
        # semantic state keeps the on-screen SKIP indicator lit; TOGGLE_AUTO
        # remains a separate edge-triggered action. The router owns physical
        # keys and release tracking, so rebinding does not leak into reading
        # logic.
        skip_pressed_this_frame = inp.pressed(InputAction.HOLD_SKIP)
        self._skip_active = inp.held(InputAction.HOLD_SKIP)
        choices_visible = bool(self.choices and self.choices.visible)
        if (
            inp.pressed(InputAction.TOGGLE_AUTO)
            and not choices_visible
        ):
            self._toggle_auto_play()

        if choices_visible:
            self.auto_play_enabled = False   # stop auto-play at choices
            self._auto_play_timer = 0.0
            self._skip_active = False         # a choice always halts skipping
            self.choices.update(dt, inp)
            return

        # A fresh skip-action press performs one step. We deliberately do NOT
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
        # Typewriter blip: a soft tick on a fixed throttle while the line is
        # still revealing, so reading has texture instead of dead silence
        # between BGM. Gated on a pack-provided blip asset + the user toggle;
        # rides the SFX volume at half level. Degrades silently with no asset.
        blip = getattr(self.ctx.config, "text_blip", None)
        if (blip and getattr(self.ctx.config, "typewriter_sound", True)
                and self.box is not None and self._current_line is not None
                and not self.box.fully_revealed()):
            self._blip_timer += dt
            if self._blip_timer >= 0.055:
                self._blip_timer = 0.0
                self.ctx.assets.play_sound(
                    blip, volume=self.ctx.config.sfx_volume * 0.5)
        else:
            self._blip_timer = 0.0
        if self.portrait:
            self.portrait.update(dt, inp)
        # advance on click/space — but only if click isn't on a button etc.
        if inp.advance_dialogue and not bar_consumed \
                and self._current_line is not None:
            if not self.box.fully_revealed():
                self.box.force_reveal()
            else:
                self._advance_page_or_line()
            self._auto_play_timer = 0.0   # reset timer on manual advance
            # Manual input owns exactly one reveal/page/line action. Returning
            # prevents an Auto timer at its boundary from advancing again in
            # the same frame.
            return

        # Auto-play: wait until text is fully revealed, then count down.
        # The delay is scaled by config.auto_play_speed (higher = faster) and,
        # when config.auto_play_wait_voice is on, we hold the advance until the
        # current voice clip finishes playing before applying the delay.
        if self.auto_play_enabled and self._current_line is not None:
            if self.box and self.box.fully_revealed():
                has_more_pages = (
                    isinstance(self.box, DialogueBox)
                    and self.box.has_more_pages()
                )
                if (
                    not has_more_pages
                    and getattr(
                        self.ctx.config,
                        "auto_play_wait_voice",
                        True,
                    )
                    and self.ctx.assets.voice_busy()
                ):
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
                        self._advance_page_or_line()

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

        Commercial-VN scale: a tall slot whose baseline sits at the screen
        bottom, so the figure reads at ~90% of screen height and its lower body
        bleeds behind the textbox — rather than a small bust floating in the
        upper-centre. Width is generous so the sprite's own aspect (not a fixed
        box) sets its on-screen size; ``fit_rect`` bottom-anchors within it.

        When ``spec`` carries staging fields the rect is scaled about its centre
        and nudged by ``offset``.
        """
        # Side slots (two-character staging) render a touch shorter than the
        # centre single, with a little more bottom bleed, so a heroine whose
        # expression PNG frames the head higher in-canvas still clears the top
        # of the frame instead of being cropped. Centre (single-sprite) keeps
        # the original 0.94/0.60/0.03 numbers byte-for-byte.
        side = slot in ("left", "right")
        portrait_h = int(sh * (0.86 if side else 0.94))
        portrait_w = int(sw * (0.52 if side else 0.60))
        anchor_x = {
            "left":   int(sw * 0.27),
            "center": int(sw * 0.50),
            "right":  int(sw * 0.73),
        }[slot]
        x = anchor_x - portrait_w // 2
        # Bottom-anchored at the screen bottom with a small bleed so the feet
        # sit behind the textbox instead of floating above it.
        y = sh + int(sh * (0.05 if side else 0.03)) - portrait_h
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

    def _cg_backdrop(self, sw: int, sh: int) -> pygame.Surface:
        """A blurred, slightly-darkened cover-fit copy of the current CG to sit
        behind the contain-fit CG (cinematic framing). Cached per (path, size)."""
        key = (self.cg_surface_path, sw, sh)
        cache = self._cg_fill_cache
        if cache is not None and cache[0] == key:
            return cache[1]
        cover = self.ctx.assets.scaled(self.cg_surface_path, (sw, sh),
                                       fit="cover")
        fill = self._box_blur(cover, 24).copy()
        dark = pygame.Surface(fill.get_size(), pygame.SRCALPHA)
        dark.fill((0, 0, 0, 95))
        fill.blit(dark, (0, 0))   # darken so the sharp CG on top pops
        self._cg_fill_cache = (key, fill)
        return fill

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

    def _readability_scrim(self, sw: int, sh: int) -> pygame.Surface:
        """A light full-screen veil with a stronger gradient band at the bottom,
        for text contrast without flattening the whole background. Cached per
        resolution (it is static)."""
        s = self._scrim_surf
        if s is not None and s.get_size() == (sw, sh):
            return s
        s = pygame.Surface((sw, sh), pygame.SRCALPHA)
        s.fill((0, 0, 0, 22))                       # gentle global unification
        band_h = max(1, int(sh * 0.42))
        band = pygame.Surface((sw, band_h), pygame.SRCALPHA)
        for i in range(band_h):
            a = int(155 * (i / band_h) ** 1.7)      # transparent top → dark base
            pygame.draw.line(band, (0, 0, 0, a), (0, i), (sw, i))
        s.blit(band, (0, sh - band_h))              # alpha-composited over veil
        self._scrim_surf = s
        return s

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

    # Time-of-day base light (pack opt-in). Warm through the day, cold at night —
    # which also serves the ghost story's day/night arc (the warmth drains as the
    # campus turns haunted). Subtle by day, stronger at night. (color, max_alpha).
    _TIME_TINT = {
        "morning":   ((202, 196, 178), 26),
        "noon":      ((220, 202, 164), 24),
        "afternoon": ((216, 186, 142), 30),
        "evening":   ((206, 150, 104), 44),
        "night":     ((40, 50, 82), 58),
        "midnight":  ((28, 36, 66), 70),
    }

    def _presentation_time_of_day(self) -> str | None:
        """Historical tint period, falling back to the live simulation clock."""
        try:
            replay_hud = self.ctx.state.meta.get(REPLAY_HUD_META_KEY)
            if isinstance(replay_hud, dict):
                value = replay_hud.get("time_of_day")
                if isinstance(value, str):
                    return value
            return self.ctx.state.time.time_of_day.value
        except Exception:
            return None

    def _time_tint_overlay(self, sw: int, sh: int):
        """Cached full-screen wash for the current time of day, or None when the
        time is unknown/unmapped. Rebuilt only when screen size or period change."""
        tod = self._presentation_time_of_day()
        if tod is None:
            return None
        spec = self._TIME_TINT.get(tod)
        if spec is None:
            return None
        key = (sw, sh, tod)
        if getattr(self, "_tod_tint_key", None) != key:
            color, alpha = spec
            surf = pygame.Surface((sw, sh), pygame.SRCALPHA)
            surf.fill((*color, alpha))
            self._tod_tint_surf = surf
            self._tod_tint_key = key
        return self._tod_tint_surf

    def draw(self, surface: pygame.Surface) -> None:
        real_surface = surface
        sw, sh = surface.get_size()
        # Geometry must settle before either ADV/NVL text is painted. In NVL
        # mode this applies the measured header inset that keeps transcript
        # content below the semantic-caption panel.
        self._sync_sound_caption_layout((sw, sh))

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
        if (
            self._covered_by_overlay
            and self._bg_fade is not None
            and not self._bg_fade.done
            and self._bg_fade.new is not None
        ):
            # A modal can be pushed in the same transaction as the opening
            # dialogue (first-run onboarding). The covered DialogueScene then
            # receives no updates, so a just-created fade would otherwise stay
            # forever at t=0 and leave a black backdrop behind the modal.
            # Render the settled destination only while covered; the fade's
            # timeline is untouched and can still play after the modal closes.
            surface.fill(self.ctx.theme.bg_deep)
            self._draw_with_camera(
                surface,
                self._blur_bg(self._bg_fade.new),
            )
        elif self._bg_fade is not None and not self._bg_fade.done:
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

        # Ambient time-of-day base light (pack opt-in via meta.ambient_time_tint):
        # gives a scene that sets NO tint of its own a lighting signature for the
        # current time of day, so the dailies read with a mood instead of a flat
        # frame. Skipped when the scene owns an explicit tint (the directed peaks)
        # or a CG is up — they never double up.
        if (getattr(self.ctx.config, "ambient_time_tint", False)
                and self._tint is None and not self.cg_surface_path):
            tod = self._time_tint_overlay(sw, sh)
            if tod is not None:
                surface.blit(tod, (0, 0))

        # Readability scrim: a light global veil + a stronger bottom gradient
        # behind the textbox region — instead of a heavy flat veil that washes
        # the whole image out every frame (now that sprites read large, the art
        # should stay vivid; only the text band needs darkening).
        surface.blit(self._readability_scrim(sw, sh), (0, 0))

        # CG takes over the background; rides the camera. Cinematic framing:
        # a blurred cover-fit copy fills the frame behind the contain-fit CG, so
        # any pillar/letterbox margin is a soft echo of the art instead of flat
        # deep-bg.
        if self.cg_surface_path:
            self._draw_with_camera(surface, self._cg_backdrop(sw, sh))
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
                        if (
                            backend is not None
                            and self._motion.continuous_allowed
                        ):
                            try:
                                backend.draw(target, rect,
                                             flip=bool(spec and spec.flip))
                                drawn = True
                            except Exception:
                                self._slot_backends[slot] = None
                        if not drawn:
                            src = self._slot_surfaces[slot]
                            if (
                                backend is not None
                                and not self._motion.continuous_allowed
                            ):
                                try:
                                    base_surface = backend.base_surface()
                                    if base_surface is not None:
                                        src = base_surface
                                except Exception:
                                    self._slot_backends[slot] = None
                            # Fit (don't stretch) the portrait into the slot so
                            # its native aspect ratio is preserved; bottom-anchored.
                            dest = fit_rect(src.get_size(), rect)
                            surf = pygame.transform.smoothscale(src, dest.size)
                            if spec is not None and spec.flip:
                                surf = pygame.transform.flip(surf, True, False)
                            target.blit(surf, dest.topleft)
                        if scratch is not None:
                            # A non-speaker recedes: partially desaturate, then
                            # cool-darken (commercial speaker emphasis), rather
                            # than a flat grey multiply that reads weakly.
                            try:
                                gray = pygame.transform.grayscale(scratch)
                                gray.set_alpha(120)        # ~47% desaturation
                                scratch.blit(gray, (0, 0))
                            except (AttributeError, pygame.error):
                                pass                       # older pygame: skip
                            r = max(0, min(255, int(255 * dim * 0.92)))
                            g = max(0, min(255, int(255 * dim * 0.97)))
                            b = max(0, min(255, int(255 * dim * 1.08)))
                            scratch.fill((r, g, b, 255),
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

        # In the high-contrast profile, authored full-screen colour effects
        # remain part of the world presentation but must not recolour the
        # accessibility text/control plane.  Paint them before ADV/NVL and
        # choice chrome; the standard branch below retains the historical
        # full-frame composition byte-for-byte.
        protect_accessibility_chrome = bool(
            self.ctx.visual is not None
            and self.ctx.visual.high_contrast
        )
        if protect_accessibility_chrome:
            if self._tint is not None:
                self._tint.draw(surface)
            for flash in self._flashes:
                flash.draw(surface)

        # While a choice menu is up, hide the (stale) previous-line textbox so a
        # decision over a CG isn't a dark empty box slab + a double-darkening
        # veil — only the choice panel shows.
        showing_choices = bool(self.choices and self.choices.visible)
        chrome_visible = not self._ui_hidden and not self._covered_by_overlay
        if self.box and chrome_visible and not showing_choices:
            self.box.draw(surface)
        if showing_choices and not self._covered_by_overlay:
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
        # High contrast already painted these into the world layer above so
        # functional text and boundaries retain their certified contrast.
        if not protect_accessibility_chrome:
            if self._tint is not None:
                self._tint.draw(surface)
            for flash in self._flashes:
                flash.draw(surface)

        # Semantic sound captions live on the stable real surface. They are
        # painted after tint/flash so camera shake and authored screen effects
        # cannot move, clip, or obscure this accessibility channel.
        if self._sound_captions_visible():
            self._sound_caption_overlay.draw(surface)

        # Quick-menu bar — drawn on the real surface (stable, unshaken),
        # hidden while choices show or the UI is hidden.
        if self._quick_bar is not None and chrome_visible and not (
                self.choices and self.choices.visible):
            self._quick_bar.draw(surface)

        # Persistent chapter/date HUD (top-left, subtle): shown while the UI is
        # visible and a choice menu is not up, so it never overlaps the choice
        # panel. Gated behind config.show_status_hud (on by default).
        if (chrome_visible and not showing_choices
                and getattr(self.ctx.config, "show_status_hud", True)):
            self._draw_status_hud(surface)

        # Playback-mode badges (AUTO / SKIP), or the restore hint when hidden.
        if not self._covered_by_overlay:
            if not self._ui_hidden:
                self._draw_mode_indicators(surface)
            else:
                self._draw_hidden_hint(surface)

    def _sound_captions_visible(self) -> bool:
        overlay = self._sound_caption_overlay
        return bool(
            overlay is not None
            and overlay.captions
            and getattr(self.ctx.config, "show_sound_captions", True) is True
            and not self._ui_hidden
            and not self._covered_by_overlay
            and not (self.choices and self.choices.visible)
        )

    def _mode_indicator_labels(self) -> list[str]:
        labels: list[str] = []
        if self.auto_play_enabled:
            labels.append(self.ctx.t("auto", "自動"))
        if self._skip_active:
            labels.append(self.ctx.t("skip", "快進"))
        return labels

    def _measure_mode_indicator_rects(
        self,
        size: tuple[int, int],
    ) -> list[pygame.Rect]:
        sw, sh = size
        density = max(0.8, sh / 720.0)
        pad_x = max(1, int(round(12 * density)))
        pad_y = max(1, int(round(6 * density)))
        gap = max(1, int(round(8 * density)))
        edge = max(1, int(round(16 * density)))
        x_right = sw - edge
        y = edge
        rects: list[pygame.Rect] = []
        for text in self._mode_indicator_labels():
            rendered = self.ctx.fonts.render(
                text,
                16,
                self.ctx.theme.text,
                bold=True,
            )
            width = rendered.get_width() + pad_x * 2
            height = rendered.get_height() + pad_y * 2
            rects.append(pygame.Rect(x_right - width, y, width, height))
            y += height + gap
        return rects

    def _measure_status_hud_rect(
        self,
        size: tuple[int, int],
    ) -> pygame.Rect | None:
        try:
            _sw, sh = size
            chapter = self._current_chapter_title()
            date = self._status_date_label()
            density = max(0.8, sh / 720.0)
            lines: list[tuple[str, int]] = []
            if chapter:
                lines.append((chapter, 18))
            if date:
                lines.append((date, 17))
            if not lines:
                return None
            surfaces = [
                self.ctx.fonts.render(text, font_size, self.ctx.theme.text)
                for text, font_size in lines
            ]
            pad_x = max(12, int(round(12 * density)))
            pad_y = max(7, int(round(7 * density)))
            line_gap = max(3, int(round(3 * density)))
            width = max(surface.get_width() for surface in surfaces) + pad_x * 2
            height = (
                sum(surface.get_height() for surface in surfaces)
                + line_gap * (len(surfaces) - 1)
                + pad_y * 2
            )
            edge = max(12, int(round(16 * density)))
            return pygame.Rect(edge, edge, width, height)
        except Exception:
            return None

    def _sound_caption_safe_area(
        self,
        size: tuple[int, int],
        *,
        mode_rects: list[pygame.Rect],
    ) -> pygame.Rect:
        sw, sh = size
        canvas = pygame.Rect(0, 0, sw, sh)
        density = max(0.5, sh / 720.0)
        gap = max(4, int(round(8 * density)))
        bottom = sh - gap
        if self._quick_bar is not None:
            bottom = min(bottom, self._quick_bar.rect.top - gap)
        if isinstance(self.box, DialogueBox):
            bottom = min(bottom, self.box.rect.top - gap)
        elif isinstance(self.box, NVLBox):
            # Leave at least one readable NVL body line below the reserved
            # header. If the status pill must move below the caption, the
            # second layout pass in ``_sync_sound_caption_layout`` reserves its
            # measured height as well.
            bottom = min(
                bottom,
                self.box.content_rect.bottom
                - self.box.minimum_content_height
                - gap,
            )
        bottom = max(1, min(sh, bottom))

        # Keep the caption canvas-centred and exclude only the fixed top-right
        # AUTO/SKIP badges. The left HUD is movable; spending caption width on
        # it can reduce four useful semantic cues to labels plus ellipses on a
        # 360p canvas. ``_sync_sound_caption_layout`` instead tries the HUD in
        # the free band immediately below the measured caption.
        centre = sw // 2
        half = centre
        if mode_rects:
            half = min(
                half,
                min(rect.left for rect in mode_rects) - gap - centre,
            )
        half = max(1, half)
        left = max(0, centre - half)
        right = min(sw, centre + half)
        safe = pygame.Rect(left, 0, max(1, right - left), bottom)
        return safe.clip(canvas)

    def _sync_sound_caption_layout(
        self,
        size: tuple[int, int],
    ) -> None:
        overlay = self._sound_caption_overlay
        if overlay is None:
            return
        live_scale = max(
            1.0,
            min(1.4, float(getattr(self.ctx.config, "text_scale", 1.0))),
        )
        status_enabled = bool(
            getattr(self.ctx.config, "show_status_hud", True)
        )
        status_rect = (
            self._measure_status_hud_rect(size)
            if status_enabled
            else None
        )
        mode_rects = self._measure_mode_indicator_rects(size)
        self._mode_indicator_rects = [rect.copy() for rect in mode_rects]
        self._status_hud_override_y = None

        if not self._sound_captions_visible():
            overlay.layout(size, text_scale=live_scale)
            if isinstance(self.box, NVLBox):
                self.box.set_header_inset(0)
            return

        safe = self._sound_caption_safe_area(
            size,
            mode_rects=mode_rects,
        )
        caption_rect = overlay.layout(
            size,
            text_scale=live_scale,
            safe_rect=safe,
        )
        density = max(0.5, size[1] / 720.0)
        gap = max(4, int(round(8 * density)))

        target_status = status_rect.copy() if status_rect is not None else None
        if target_status is not None and caption_rect.colliderect(target_status):
            target_status.y = caption_rect.bottom + gap
            canvas = pygame.Rect((0, 0), size)
            candidate_blocked = not canvas.contains(target_status)
            if self._quick_bar is not None:
                candidate_blocked = (
                    candidate_blocked
                    or target_status.colliderect(self._quick_bar.rect)
                )
            if isinstance(self.box, DialogueBox):
                candidate_blocked = (
                    candidate_blocked
                    or target_status.colliderect(self.box.rect)
                )
            if candidate_blocked:
                # Only now spend vertical caption space. This fallback keeps
                # the displaced HUD wholly above a horizontally-overlapping
                # quickbar/ADV box or inside the canvas.
                reserved_safe = safe.copy()
                reserved_safe.height = max(
                    1,
                    reserved_safe.height - target_status.height - gap,
                )
                caption_rect = overlay.layout(
                    size,
                    text_scale=live_scale,
                    safe_rect=reserved_safe,
                )
                target_status.y = caption_rect.bottom + gap
            self._status_hud_override_y = target_status.y
        if target_status is not None:
            self._status_hud_rect = target_status.copy()

        if isinstance(self.box, NVLBox):
            reserved_bottom = caption_rect.bottom
            if target_status is not None:
                reserved_bottom = max(reserved_bottom, target_status.bottom)
            if mode_rects:
                reserved_bottom = max(
                    reserved_bottom,
                    max(rect.bottom for rect in mode_rects),
                )
            inset = max(
                0,
                reserved_bottom + gap - self.box.base_content_top,
            )
            self.box.set_header_inset(inset)

    def _draw_mode_indicators(self, surface: pygame.Surface) -> None:
        """Draw small AUTO / SKIP status badges in the top-right corner.

        These are functional state labels (not decoration): AUTO shows while
        auto-play is on, SKIP while a skip is held. Stacked so both can show
        at once if a skip is held while auto-play is enabled.
        """
        labels = self._mode_indicator_labels()
        if not labels:
            self._mode_indicator_rects = []
            return
        sw, sh = surface.get_size()
        density = max(0.8, sh / 720.0)
        pad_x = max(1, int(round(12 * density)))
        pad_y = max(1, int(round(6 * density)))
        radius = max(
            1,
            int(round(self.ctx.theme.radius_s * density)),
        )
        border_width = max(1, int(round(density)))
        self._mode_indicator_rects = self._measure_mode_indicator_rects((sw, sh))
        for text, rect in zip(labels, self._mode_indicator_rects):
            ts = self.ctx.fonts.render(text, 16, self.ctx.theme.text, bold=True)
            w, h = rect.size
            badge = pygame.Surface((w, h), pygame.SRCALPHA)
            chrome = self.ctx.ui_chrome
            painted = (
                chrome is not None
                and chrome.paint(
                    badge,
                    badge.get_rect(),
                    ChromeRequest(
                        part="card",
                        screen="dialogue",
                        variant="mode",
                        size=badge.get_size(),
                        density=max(
                            0.01,
                            float(getattr(self.ctx.fonts, "scale", 1.0)),
                        ),
                        selected=True,
                        emphasis=2,
                    ),
                )
            )
            if not painted:
                pygame.draw.rect(
                    badge,
                    (*self.ctx.theme.accent[:3], 210),
                    badge.get_rect(),
                    border_radius=radius,
                )
                pygame.draw.rect(
                    badge,
                    (*self.ctx.theme.text[:3], 60),
                    badge.get_rect(),
                    border_width,
                    border_radius=radius,
                )
            surface.blit(badge, rect.topleft)
            # Functional state text remains above any pack-owned chrome.
            surface.blit(ts, (rect.x + pad_x, rect.y + pad_y))

    def _current_chapter_title(self) -> str | None:
        """Resolve ``state.current_chapter`` to its ChapterManifest title.

        Returns None when there is no chapter cursor / no manifest / the id is
        unknown — the HUD then shows only the date line. Never raises."""
        try:
            replay_hud = self.ctx.state.meta.get(REPLAY_HUD_META_KEY)
            if isinstance(replay_hud, dict):
                value = replay_hud.get("chapter_title")
                return value if isinstance(value, str) and value else None
            cid = getattr(self.ctx.state, "current_chapter", None)
            if not cid:
                return None
            manifest = self.ctx.state.meta.get("__chapters__")
            if manifest is None:
                return None
            spec = next((c for c in manifest.chapters if c.id == cid), None)
            if spec is None:
                return None
            return spec.title or cid
        except Exception:
            return None

    def _status_date_label(self) -> str | None:
        """Return the date text for the dialogue HUD.

        A scene may describe an authored flash-forward or epilogue without
        mutating the live simulation clock.  Existing ``Scene.tags`` provide a
        schema-compatible presentation contract for those cases:

        ``hud-date:<label>``
            Replace the live ``TimeSystem.label`` with ``<label>``.

        ``hud-hide-date``
            Omit the date row while retaining the optional chapter row.

        Ordinary scenes have neither tag and continue to display the live
        calendar. Blank overrides are ignored. Never raises, including when a
        historical save references a scene that is no longer authored.
        """
        try:
            replay_hud = self.ctx.state.meta.get(REPLAY_HUD_META_KEY)
            if isinstance(replay_hud, dict):
                value = replay_hud.get("date_label")
                return value if isinstance(value, str) and value else None
            story = self.ctx.state.story
            current_id = getattr(story, "current_scene", None)
            current = story.get(current_id) if current_id else None
            tags = tuple(getattr(current, "tags", ()) or ())
            if "hud-hide-date" in tags:
                return None
            prefix = "hud-date:"
            for tag in tags:
                if tag.startswith(prefix):
                    authored = tag[len(prefix):].strip()
                    if authored:
                        return authored
                    break
        except Exception:
            pass
        try:
            return self.ctx.state.time.label()
        except Exception:
            return None

    def _draw_status_hud(self, surface: pygame.Surface) -> None:
        """Draw a subtle top-left chapter/date indicator.

        Two stacked lines inside one low-alpha pill: the current chapter title
        (omitted when unknown) above either the live in-game date/time or a
        scene-authored ``hud-date:`` label. Functional, non-decorative, and
        deliberately unobtrusive so it doesn't compete with the art. Never
        raises."""
        try:
            theme = self.ctx.theme
            chapter = self._current_chapter_title()
            date = self._status_date_label()
            density = max(0.8, surface.get_height() / 720.0)
            # FontRegistry applies the canvas-density scale to these requested
            # sizes. 18/17 produce >=17 px text at 720p and >=26 px at 1080p
            # with the bundled CJK fonts used by shipping packs.
            chapter_size = 18
            date_size = 17
            self._status_hud_font_sizes = (chapter_size, date_size)
            lines: list[tuple[str, int, tuple]] = []
            if chapter:
                lines.append((chapter, chapter_size, theme.accent_warm))
            if date:
                lines.append((date, date_size, theme.text_mute))
            if not lines:
                return
            surfs = [self.ctx.fonts.render(t, sz, col) for t, sz, col in lines]
            pad_x = max(12, int(round(12 * density)))
            pad_y = max(7, int(round(7 * density)))
            line_gap = max(3, int(round(3 * density)))
            w = max(s.get_width() for s in surfs) + pad_x * 2
            h = sum(s.get_height() for s in surfs) \
                + line_gap * (len(surfs) - 1) + pad_y * 2
            edge = max(12, int(round(16 * density)))
            x = edge
            y = (
                max(edge, int(self._status_hud_override_y))
                if self._status_hud_override_y is not None
                else edge
            )
            y = min(y, max(0, surface.get_height() - h))
            self._status_hud_rect = pygame.Rect(x, y, w, h)
            pill = pygame.Surface((w, h), pygame.SRCALPHA)
            chrome = self.ctx.ui_chrome
            painted = (
                chrome is not None
                and chrome.paint(
                    pill,
                    pill.get_rect(),
                    ChromeRequest(
                        part="card",
                        screen="dialogue",
                        variant="status",
                        size=pill.get_size(),
                        density=max(
                            0.01,
                            float(getattr(self.ctx.fonts, "scale", 1.0)),
                        ),
                    ),
                )
            )
            if not painted:
                pygame.draw.rect(
                    pill,
                    (*theme.bg_overlay[:3], 170),
                    pill.get_rect(),
                    border_radius=max(
                        1,
                        int(round(theme.radius_s * density)),
                    ),
                )
                pygame.draw.rect(
                    pill,
                    (*theme.accent_warm[:3], 70),
                    pill.get_rect(),
                    max(1, int(round(density))),
                    border_radius=max(
                        1,
                        int(round(theme.radius_s * density)),
                    ),
                )
            cy = pad_y
            for s in surfs:
                # Chapter/date text is functional and always painted after the
                # optional pack chrome.
                pill.blit(s, (pad_x, cy))
                cy += s.get_height() + line_gap
            surface.blit(pill, (x, y))
        except Exception:
            pass

    def describe(self) -> dict:
        caption_overlay = self._sound_caption_overlay
        return {
            "scene": "DialogueScene",
            "scene_id": self.scene_id,
            "story_scene": self.ctx.story_id() if hasattr(self.ctx, "story_id") else None,
            "current_line": (
                {
                    "speaker": self._current_line.speaker,
                    "speaker_id": self._line_speaker_id(self._current_line),
                    "text": (self._current_line.plain_text
                             or self._current_line.text),
                    "line_index": self._current_line.line_index,
                    "total_lines": self._current_line.total_lines,
                } if self._current_line else None
            ),
            "choice_visible": bool(self.choices and self.choices.visible),
            "sound_captions": (
                [
                    {"kind": cue.kind, "text": cue.text}
                    for cue in caption_overlay.captions
                ]
                if caption_overlay is not None
                else []
            ),
            "sound_captions_visible": self._sound_captions_visible(),
            "auto_play": self.auto_play_enabled,
            "voice_resume_outcome": getattr(
                self.ctx.assets,
                "last_voice_resume_outcome",
                None,
            ),
            "skip_active": self._skip_active,
            "nvl_mode": isinstance(self.box, NVLBox),
            "nvl_lines": (self.box.line_count
                          if isinstance(self.box, NVLBox) else 0),
            "adv_page": (
                {
                    "index": self.box.page_index,
                    "count": self.box.page_count,
                    "current_fully_revealed": self.box.fully_revealed(),
                    "has_more": self.box.has_more_pages(),
                }
                if isinstance(self.box, DialogueBox)
                else None
            ),
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

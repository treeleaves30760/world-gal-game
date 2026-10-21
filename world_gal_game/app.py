"""Main pygame App that orchestrates the engine.

Owns the SceneContext + SceneManager. Each scene communicates with others
through callbacks given on enter(). The App is also responsible for the
screenshot system and a no-op headless mode.
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from typing import Callable

import pygame

from .config import EngineConfig, resolve_asset, writable_root
from .content_loader import load_pack
from .core.game_state import GameState
from .core.map_system import rebind_authored_map_catalog
from .core.endings import rebind_authored_ending_catalog
from .core.cg_gallery_spec import CGGalleryManifest
from .core.music_room_spec import MusicRoomManifest
from .core.media_authority import (
    MediaAuthority,
    build_media_authority,
    rebind_authorized_media_progress,
)
from .core.story_graph import (
    rebind_authored_audio_meta,
    rebind_authored_dialogue_history_voices,
    rebind_authored_story_catalog,
)
from .core.action_policy import action_availability
from .core.guided_destinations import guided_story_should_auto_reopen
from .core.travel import (
    TravelPlan,
    TravelResult,
    TravelService,
    graph_travel_enabled,
    minimum_travel_cost,
)
from .core.save_manager import SaveManager
from .core.localization import Localization
from .core.title_screen_spec import (
    TitleScreenSpec,
    parse_title_screen_spec,
)
from .core.ui_skin_spec import UISkinSpec, parse_ui_skin_spec
from .dialogue.dialogue_engine import (
    DialogueEngine,
    rebind_dialogue_history_localization,
    rebind_resume_choice_presentations,
)
from .npc.llm_brain import default_brain, LLMBrain
from .scenes.base import SceneManager, SceneContext
from .scenes.title import TitleScene
from .scenes.exploration import ExplorationScene
from .scenes.dialogue_scene import DialogueScene
from .scenes.map_scene import MapScene
from .scenes.destination_picker import DestinationPickerScene
from .scenes.affection_scene import AffectionScene
from .scenes.relationships_scene import RelationshipsScene
from .scenes.event_log_scene import EventLogScene
from .scenes.save_scene import SaveScene
from .scenes.npc_action_scene import NPCActionScene
from .scenes.settings_scene import SettingsScene
from .scenes.controls_scene import ControlsScene
from .scenes.achievements_scene import AchievementsScene
from .scenes.inventory_scene import InventoryScene
from .scenes.scrollback_scene import ScrollbackScene
from .scenes.menu_scene import MenuScene
from .scenes.flowchart_scene import FlowchartScene
from .scenes.character_profile_scene import CharacterProfileScene
from .scenes.onboarding_scene import OnboardingScene
from .scenes.quest_log_scene import QuestLogScene
from .scenes.clues_scene import CluesScene
from .scenes.shop_scene import ShopScene
from .scenes.cg_gallery_scene import CGGalleryScene
from .scenes.music_room_scene import MusicRoomScene
from .scenes.endings_scene import EndingsScene
from .scenes.scene_replay_scene import SceneReplayScene
from .scenes.chapter_card_scene import ChapterCardScene
from .scenes.credits_scene import CreditsScene
from .ui.assets import AssetManager
from .ui.fonts import FontRegistry
from .ui.input import InputRouter
from .ui.input_bindings import InputAction
from .ui.system_chrome import build_system_chrome
from .ui.visual import VisualPolicy, VisualTheme
from .ui.widgets.toast import Toast, ToastStack


def _letterbox_view(logical: tuple[int, int], window: tuple[int, int]
                    ) -> tuple[float, tuple[int, int], tuple[int, int]]:
    """Fit ``logical`` into ``window`` preserving aspect; return
    ``(scale, offset, view_size)`` for a centered letterbox blit."""
    lw, lh = logical
    dw, dh = window
    scale = min(dw / lw, dh / lh) if (lw and lh) else 1.0
    vw, vh = max(1, int(lw * scale)), max(1, int(lh * scale))
    return scale, ((dw - vw) // 2, (dh - vh) // 2), (vw, vh)


def _unproject(pos: tuple[int, int], scale: float,
               offset: tuple[int, int]) -> tuple[int, int]:
    """Map a window-pixel point back into logical-canvas coords."""
    x, y = pos
    ox, oy = offset
    s = scale or 1.0
    return (int((x - ox) / s), int((y - oy) / s))


class GalGameApp:
    def __init__(self, *, config: EngineConfig, pack: str | None = None,
                 brain: LLMBrain | None = None, headless: bool = False):
        self.config = config
        # Pull persisted user preferences off disk before anything reads
        # them (audio volumes, text speed, autosave settings, ...). Robust
        # by contract: missing/corrupt settings.json keeps defaults (F1).
        self.config.load_from_disk()
        self.pack = pack or config.default_pack
        self.headless = headless
        # Brain selection: explicit kwarg > meta.yaml after load (set later) >
        # config.brain > engine default (EchoBrain). The post-load promotion
        # happens once load_pack returns: see _bind_brain_from_meta.
        self.brain = brain or default_brain()

        # ----- pygame init -----
        if headless:
            os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
            os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
        pygame.init()
        if not headless:
            try:
                pygame.mixer.init()
                # Reserve channel 0 for per-line voice, channels 1-2 for the
                # two-stream BGM crossfade, and channel 3 for the looping
                # ambient bed, so auto-allocated SFX never steal them; 8 total
                # mixing channels is plenty for a VN.
                pygame.mixer.set_num_channels(8)
                pygame.mixer.set_reserved(4)
            except pygame.error:
                # Fine in CI / no-audio environments.
                pass
            # Gamepad: open every connected controller so its JOY* events flow
            # (InputRouter maps A→advance/confirm, B→cancel, and D-pad/left
            # analogue stick→menu navigation).
            # Steam Deck / Steam Input present as a virtual pad here.
            try:
                pygame.joystick.init()
                for _ji in range(pygame.joystick.get_count()):
                    pygame.joystick.Joystick(_ji).init()
            except pygame.error:
                pass
        # ----- logical canvas + (optional) real window -----
        # Everything draws to ``self.screen`` at the fixed design resolution
        # (config.screen_size). In windowed mode we scale-blit that canvas onto
        # ``self.display`` (a resizable OS window) each frame, letterboxing to
        # preserve aspect ratio; input is mapped back from window space to
        # canvas space. Screenshots / visual baselines / the dev driver all
        # capture ``self.screen`` directly, so they stay resolution-stable
        # regardless of window size. In headless mode there is no window and
        # the canvas is the only surface.
        self.screen = pygame.Surface(config.screen_size)
        self.display: pygame.Surface | None = None
        self._view_scale: float = 1.0
        self._view_offset: tuple[int, int] = (0, 0)
        self._view_size: tuple[int, int] = tuple(config.screen_size)
        # Device input has cross-frame state: left-stick repeat timing and
        # touch down/up contacts cannot be reconstructed from one event batch.
        self._input_router = InputRouter(touch_mode=config.touch_mode)
        # Retained for the compatibility helper ``_update_touch_gesture``;
        # production touch classification lives in ``InputRouter``.
        self._touch_start: tuple[float, float] | None = None
        if not headless:
            display_flags = 0
            if config.fullscreen:
                display_flags |= pygame.FULLSCREEN
            else:
                display_flags |= pygame.RESIZABLE
            self.display = pygame.display.set_mode(
                config.screen_size, display_flags, vsync=1 if config.vsync else 0,
            )
            pygame.display.set_caption(config.title)
            self._compute_view()
        self.clock = pygame.time.Clock()

        # ----- load content -----
        content_root = config.pack_content(self.pack)
        if not content_root.exists():
            raise FileNotFoundError(f"Game pack not found: {content_root}")
        self.state, self.npcs, self.meta = load_pack(content_root)
        # Saves retain map *progress*, never an obsolete copy of the pack's
        # location catalogue. Keep an immutable-in-practice baseline to
        # rebind current coordinates, exits, hooks, and presentation data on
        # every load.
        self._authored_map_catalog = self.state.map.model_copy(deep=True)
        self._authored_ending_catalog = self.state.endings.model_copy(deep=True)
        self._authored_story_catalog = self.state.story.model_copy(deep=True)
        self._authored_media_authority = self._build_media_authority(
            self.state,
        )
        (
            self.state.cg_gallery,
            self.state.music_room,
        ) = rebind_authorized_media_progress(
            self.state.cg_gallery,
            self.state.music_room,
            self._authored_media_authority,
        )
        # Pin the pack identity on the config *before* any save_dir() call so
        # every save path (clear_data, autosave bridge, save scene) lands in this
        # pack's own namespace. Mirrors SaveManager's pack_id stamping
        # (meta.id, falling back to the pack directory name).
        self.config.pack_id = str(
            self.state.meta.get("__pack_meta__", {}).get("pack_id", "")
            or self.pack)
        # Cross-playthrough clear data (New Game+): load once and park it on the
        # state so conditions (cleared_ending / cleared_route) can read it; it is
        # global, so it lives outside the per-save schema.
        from .core.clear_data import ClearData
        self._clear_data_path = self.config.save_dir() / "clear_data.json"
        self.clear_data = ClearData.load(self._clear_data_path)
        # Ending definitions belong to the currently installed pack, not to a
        # save file.  Keep their ids as a transient authority so loading an old
        # save cannot cause clear data to resurrect an ending the pack removed.
        self._pack_ending_ids = frozenset(self.state.endings.endings)
        self.state.meta["__pack_ending_ids__"] = self._pack_ending_ids
        self.state.meta["__pack_ending_catalog__"] = (
            self._authored_ending_catalog
        )
        self.state.meta["__pack_map_catalog__"] = self._authored_map_catalog
        self.state.meta["__pack_story_catalog__"] = (
            self._authored_story_catalog
        )
        self.state.meta["__pack_media_authority__"] = (
            self._authored_media_authority
        )
        self.clear_data.apply_to_state(
            self.state,
            registered_endings=self._pack_ending_ids,
            authored_cg_ids=self._authored_media_authority.cg_ids,
            authored_music_ids=self._authored_media_authority.music_ids,
        )
        # Apply pack title/subtitle to config so the title screen reflects them.
        if "title" in self.meta:
            self.config.title = self.meta["title"]
            if not headless:
                pygame.display.set_caption(self.meta["title"])
        if "subtitle" in self.meta:
            self.config.subtitle = self.meta["subtitle"]
        self.title_screen_spec: TitleScreenSpec | None = (
            parse_title_screen_spec(self.meta)
            if "title_screen" in self.meta
            else None
        )
        self.ui_skin_spec: UISkinSpec | None = parse_ui_skin_spec(self.meta)
        if "text_speed" in self.meta:
            self.config.text_speed = float(self.meta["text_speed"])
        # Pack-provided typewriter blip asset (played softly while text reveals).
        self.config.text_blip = self.meta.get("text_blip")
        # Pack opt-in: subtle time-of-day base tint on un-directed scenes.
        self.config.ambient_time_tint = bool(self.meta.get("ambient_time_tint", False))
        # Pack-provided UI click sound + install the global Button hook so every
        # menu button gives audible feedback (rides the SFX bus; toggleable).
        self.config.ui_sound = self.meta.get("ui_sound")
        from world_gal_game.ui.widgets.button import set_click_sound_hook

        def _ui_click() -> None:
            cfg = self.config
            if cfg.ui_sound and cfg.ui_sound_enabled:
                self.assets.play_sound(cfg.ui_sound, volume=cfg.sfx_volume * 0.6)
        set_click_sound_hook(_ui_click)

        # Promote a plugin-registered brain if meta.yaml names one. Caller-
        # supplied brain (constructor kwarg) takes precedence — that's the
        # explicit-override channel for tests and embedding scenarios.
        if brain is None:
            wanted = self.meta.get("brain")
            if wanted:
                from .plugins import BRAIN_REGISTRY
                entry = BRAIN_REGISTRY.get(wanted)
                if entry is not None:
                    try:
                        self.brain = entry.cls()
                    except Exception:
                        import logging
                        logging.getLogger("world_gal_game.app").exception(
                            "failed to spawn brain '%s'; falling back to default",
                            wanted,
                        )

        # ----- UI services -----
        pack_root = self.config.pack_root(self.pack)
        bundled_font = self.meta.get("bundled_font")
        bundled_font_path = None
        if bundled_font:
            # First check pack-relative, then resource-root.
            cand = pack_root / bundled_font
            if cand.exists():
                bundled_font_path = cand
            else:
                bundled_font_path = resolve_asset(bundled_font)
        # UI scale tracks the canvas height (design baseline 720) so typography
        # stays proportionate at 1080p/4k instead of shrinking. Never below 1.0.
        ui_scale = max(1.0, self.config.screen_size[1] / 720.0)
        self.fonts = FontRegistry(self.config.font_candidates,
                                  bundled=bundled_font_path, scale=ui_scale)
        # Retain the pre-locale sources as a compatibility fallback for packs
        # that still use the legacy flat ``bundled_font`` declaration.
        self._base_font_candidates = tuple(self.config.font_candidates)
        self._legacy_bundled_font_path = bundled_font_path
        self.assets = AssetManager(
            pack_root=pack_root,
            max_image_cache_bytes=max(0, self.config.max_image_cache_mb)
            * 1024
            * 1024,
            max_sound_cache_bytes=max(0, self.config.max_sound_cache_mb)
            * 1024
            * 1024,
        )
        # Push configured audio volumes into the asset manager so voice lines
        # play at the user's chosen level.
        self.assets._voice_volume = self.config.voice_volume
        # Theme + localization come from meta.yaml so packs can fully restyle.
        from .ui.theme import Theme as _Theme
        self.authored_theme = _Theme.from_meta(self.meta)
        self.visual = VisualPolicy.from_config(self.config)
        self.theme = VisualTheme(self.authored_theme, self.visual)
        self.localization = Localization.from_meta(
            self.meta,
            pack_root=pack_root,
            requested=self.config.preferred_locale(self.config.pack_id),
        )
        from .ui.presentation_text import PresentationTextPolicy
        expansion = float(self.config.presentation_text_expansion)
        self.presentation_text = (
            PresentationTextPolicy()
            if expansion == 1.0
            else PresentationTextPolicy.pseudo_expand(expansion)
        )
        self._apply_locale_fonts()
        if not headless:
            pygame.display.set_caption(
                self.localization.story("meta.title", self.config.title),
            )
        self.ui_chrome = build_system_chrome(
            self.ui_skin_spec,
            assets=self.assets,
            fonts=self.fonts,
            theme=self.theme,
            visual=self.visual,
        )
        # Runtime labels belong to this state/application. Never bind a
        # process-global locale: multiple embedded apps/headless sessions may
        # coexist in one interpreter.
        self.state.time.bind_localization(self.localization)
        self.state.affection.bind_localization(self.localization)

        # ----- dialogue engine -----
        # No live LLM brain is wired up in this release; lines marked
        # `llm_speaker: true` will fall back to their `text:` field.
        self.dialogue = DialogueEngine(
            self.state,
            llm_provider=None,
            speaker_id_resolver=self._dialogue_speaker_id,
            localization=self.localization,
        )

        # ----- autosave bridge -----
        # The bundled `autosave` plugin runs inside a PluginContext that
        # carries no EngineConfig and no save dir (content_loader builds it
        # before the app exists). Park a small bridge on state.meta under a
        # private `__` key (stripped from saves) so the plugin can reach the
        # live config + resolved save dir + a screen-grab for thumbnails.
        self.state.meta["__autosave_config__"] = {
            "config": self.config,
            "save_dir": self.config.save_dir(),
            "get_screen": (None if headless else (lambda: self.screen)),
            "get_resume": self._capture_resume_context,
        }

        # ----- scene context -----
        self.ctx = SceneContext(
            config=self.config, state=self.state, npcs=self.npcs,
            brain=self.brain, dialogue=self.dialogue, assets=self.assets,
            fonts=self.fonts, theme=self.theme,
            localization=self.localization,
            presentation_text=self.presentation_text,
            screen_size=self.config.screen_size,
            meta=self.meta, pack=self.pack,
            ui_chrome=self.ui_chrome,
            visual=self.visual,
        )
        plugin_manager = self.state.meta.get("__plugin_manager__")
        plugin_ctx = getattr(plugin_manager, "_ctx", None)
        if plugin_ctx is not None:
            plugin_ctx.config = self.config
            plugin_ctx.motion = self.ctx.motion
        self.manager = SceneManager(motion=self.ctx.motion)
        self.manager.replace(
            TitleScene(self.ctx),
            **self._title_scene_kwargs(),
        )
        self._running = True
        self._screenshot_pending: str | None = None
        self._inspect_pending = False
        self._last_inspect: dict | None = None

        # Achievement toasts: a small stack drawn over everything that
        # watches for unlocked-but-unseen achievements each frame.
        self.toast_stack = ToastStack(
            pygame.Rect(0, 0, self.config.screen_size[0],
                        self.config.screen_size[1]),
            fonts=self.fonts, theme=self.theme, assets=self.assets,
            motion=self.ctx.motion,
        )
        # Pre-seed seen with anything already unlocked at startup so old
        # saves don't dump 9 toasts at once.
        for ach_id in list(self.state.achievements.unlocked):
            self.state.achievements.mark_seen(ach_id)

        # ----- optional Steam integration -----
        # Off by default. Enabled when meta.yaml has `steam.enabled: true`
        # OR the WGG_STEAM env var is set. Never on the web. Any failure
        # (lib missing, init false) leaves self._steam = None → the game
        # runs identically without Steam, so headless / CI / itch builds
        # are byte-identical.
        self._steam = None
        self._init_steam()

        # Dev tools (no-op if WGG_DEV not set)

        # Dev tools (no-op if WGG_DEV not set)
        self._dev_mode = bool(os.environ.get("WGG_DEV"))
        if self._dev_mode:
            try:
                from .ui.widgets.debug_overlay import DebugOverlay
                from .dev.hot_reload import HotReloader
                self._debug_overlay = DebugOverlay(
                    pygame.Rect(self.config.screen_size[0] - 360, 8, 350,
                                self.config.screen_size[1] - 100),
                    fonts=self.fonts, theme=self.theme,
                )
                self._hot_reloader = HotReloader(pack_root)
            except Exception as _dev_exc:
                import sys as _sys
                print(f"[dev] init failed: {_dev_exc}", file=_sys.stderr)
                self._debug_overlay = None
                self._hot_reloader = None
        else:
            self._debug_overlay = None
            self._hot_reloader = None

    # ----------- optional Steam integration -----------------------------

    def _init_steam(self) -> None:
        """Bring up the Steam bridge when enabled; otherwise stay a no-op.

        Gated on ``meta.yaml: steam.enabled`` or the ``WGG_STEAM`` env var.
        Never constructs the bridge on the web. Every step is wrapped so a
        failure degrades to ``self._steam = None`` (no Steam) rather than
        breaking startup. On success the bridge is parked on
        ``state.meta["__steam_bridge__"]`` (a transient ``__`` key, stripped
        on save) so the achievement hook can find it, the hook module is
        imported (registering its handler), and already-unlocked
        achievements are pre-seeded to Steam.
        """
        try:
            from .platform_web import is_web
            if is_web():
                return  # never on the browser
            steam_meta = self.meta.get("steam", {}) if isinstance(self.meta, dict) else {}
            if not isinstance(steam_meta, dict):
                steam_meta = {}
            enabled = bool(steam_meta.get("enabled")) or bool(os.environ.get("WGG_STEAM"))
            if not enabled:
                return
            app_id = steam_meta.get("app_id") or os.environ.get("WGG_STEAM", "480")
            mapping = steam_meta.get("achievements") or None
            if mapping is not None and not isinstance(mapping, dict):
                mapping = None

            from .integrations.steam_bridge import SteamBridge
            from .integrations.steam_plugin import (
                push_achievements,  # noqa: F401 — import registers the hook
                STEAM_BRIDGE_META_KEY,
            )
            bridge = SteamBridge.try_init(app_id, mapping=mapping)
            if bridge is None:
                return  # Steam absent; run without it.
            self._steam = bridge
            # Park the bridge where the EFFECT_AFTER_APPLY hook reads it.
            self.state.meta[STEAM_BRIDGE_META_KEY] = bridge
            # Pre-seed: push everything already unlocked (e.g. a loaded save).
            try:
                bridge.push_unlocked(list(self.state.achievements.unlocked.keys()))
                bridge.run_callbacks()
            except Exception:
                pass
        except Exception as exc:
            # Total isolation — Steam must never break a normal launch.
            print(f"[steam] init skipped: {exc}", file=sys.stderr)
            self._steam = None

    def _shutdown_steam(self) -> None:
        """Tear down the Steam bridge if one is live (idempotent)."""
        if self._steam is not None:
            try:
                self._steam.shutdown()
            except Exception:
                pass
            self._steam = None

    # ----------- scene navigation helpers -------------------------------

    def _dialogue_speaker_id(self, speaker: str) -> str | None:
        """Resolve authored/display speaker copy to a stable NPC id.

        Dialogue history and per-character voice settings must not use a
        localized display name as their persistence key. Unknown narrators,
        player-name interpolation, and old packs safely return None.
        """
        npc = self.npcs.by_name(speaker)
        return npc.id if npc is not None else None

    def _build_media_authority(self, state: GameState) -> MediaAuthority:
        """Build persistent gallery/jukebox authority from current pack data.

        The story and optional CG manifest are declarative authority.  Initial
        tracker values support packs/plugins that deliberately unlock an extra
        on boot, while the physical BGM roster preserves Music Room's legacy
        convention of discovering current-pack tracks under ``assets/bgm``.
        No value from a save candidate participates in this snapshot.
        """
        manifest = state.meta.get("__cg_gallery_manifest__")
        if not isinstance(manifest, CGGalleryManifest):
            manifest = None
        music_manifest = state.meta.get("__music_room_manifest__")
        if not isinstance(music_manifest, MusicRoomManifest):
            music_manifest = None

        discovered_music: list[str] = []
        bgm_root = self.config.pack_assets(self.pack) / "bgm"
        try:
            discovered_music = [
                f"assets/bgm/{entry.name}"
                for entry in bgm_root.iterdir()
                if entry.is_file()
                and entry.suffix.lower() in {".ogg", ".mp3", ".wav"}
            ]
        except OSError:
            pass

        return build_media_authority(
            state.story,
            cg_manifest=manifest,
            music_manifest=music_manifest,
            additional_cg_ids=tuple(state.cg_gallery.unlocked),
            additional_music_ids=(
                *state.music_room.unlocked,
                *discovered_music,
                self.meta.get("title_bgm", ""),
            ),
        )

    def _refresh_media_authority(self) -> MediaAuthority:
        """Fold explicitly registered runtime story scenes into authority."""
        current = self._authored_media_authority
        manifest = self.state.meta.get("__cg_gallery_manifest__")
        if not isinstance(manifest, CGGalleryManifest):
            manifest = None
        music_manifest = self.state.meta.get("__music_room_manifest__")
        if not isinstance(music_manifest, MusicRoomManifest):
            music_manifest = None
        self._authored_media_authority = build_media_authority(
            self._authored_story_catalog,
            cg_manifest=manifest,
            music_manifest=music_manifest,
            additional_cg_ids=current.cg_ids,
            additional_music_ids=current.music_ids,
        )
        self.state.meta["__pack_media_authority__"] = (
            self._authored_media_authority
        )
        return self._authored_media_authority

    def _install_new_game_state(
        self,
        state: GameState,
        npcs,
        meta: dict,
    ) -> None:
        """Make a freshly loaded pack state the one live runtime state.

        ``GameState`` is referenced by more than ``self.state``: the shared
        scene context, dialogue engine, plugin context, autosave bridge, and
        optional Steam bridge all retain a direct reference.  New Game must
        rebind every one of them; mutating the old object in place would keep
        playthrough data that is absent from the pack's authored initial
        state.
        """
        old_dialogue = self.dialogue

        self.state = state
        self.npcs = npcs
        self.meta = meta
        self._authored_map_catalog = state.map.model_copy(deep=True)
        self._authored_ending_catalog = state.endings.model_copy(deep=True)
        self._authored_story_catalog = state.story.model_copy(deep=True)
        self._authored_media_authority = self._build_media_authority(state)
        (
            state.cg_gallery,
            state.music_room,
        ) = rebind_authorized_media_progress(
            state.cg_gallery,
            state.music_room,
            self._authored_media_authority,
        )
        self.title_screen_spec = (
            parse_title_screen_spec(meta)
            if "title_screen" in meta
            else None
        )
        self.ui_skin_spec = parse_ui_skin_spec(meta)
        self.ui_chrome = build_system_chrome(
            self.ui_skin_spec,
            assets=self.assets,
            fonts=self.fonts,
            theme=self.theme,
            visual=self.visual,
        )

        # Cross-playthrough data is deliberately the only gameplay progress
        # carried into a new run. It powers NG+ conditions and seeds the live
        # read/completion trackers, but remains outside normal save-state
        # persistence.
        self._pack_ending_ids = frozenset(state.endings.endings)
        state.meta["__pack_ending_ids__"] = self._pack_ending_ids
        state.meta["__pack_ending_catalog__"] = (
            self._authored_ending_catalog
        )
        state.meta["__pack_map_catalog__"] = self._authored_map_catalog
        state.meta["__pack_story_catalog__"] = self._authored_story_catalog
        state.meta["__pack_media_authority__"] = (
            self._authored_media_authority
        )
        self.clear_data.apply_to_state(
            state,
            registered_endings=self._pack_ending_ids,
            authored_cg_ids=self._authored_media_authority.cg_ids,
            authored_music_ids=self._authored_media_authority.music_ids,
        )
        state.time.bind_localization(self.localization)
        state.affection.bind_localization(self.localization)
        if self.config.seed is not None:
            state.meta["__seed__"] = self.config.seed

        self.dialogue = DialogueEngine(
            state,
            llm_provider=getattr(old_dialogue, "llm_provider", None),
            speaker_id_resolver=self._dialogue_speaker_id,
            localization=self.localization,
        )
        state.meta["__autosave_config__"] = {
            "config": self.config,
            "save_dir": self.config.save_dir(),
            "get_screen": (None if self.headless else (lambda: self.screen)),
            "get_resume": self._capture_resume_context,
        }
        if self._steam is not None:
            from .integrations.steam_plugin import STEAM_BRIDGE_META_KEY
            state.meta[STEAM_BRIDGE_META_KEY] = self._steam

        # ``load_pack`` creates a new manager/context pair.  Its context
        # already points at ``state``; add the application service that is not
        # available to the content loader and explicitly refresh all fields so
        # hooks cannot retain the previous playthrough.
        plugin_manager = state.meta.get("__plugin_manager__")
        plugin_ctx = getattr(plugin_manager, "_ctx", None)
        if plugin_ctx is not None:
            plugin_ctx.state = state
            plugin_ctx.meta = meta
            plugin_ctx.pack_root = self.config.pack_root(self.pack)
            plugin_ctx.manager = plugin_manager
            plugin_ctx.config = self.config
            plugin_ctx.motion = self.ctx.motion

        # Every live and subsequently-created scene shares this object.
        self.ctx.state = state
        self.ctx.npcs = npcs
        self.ctx.dialogue = self.dialogue
        self.ctx.meta = meta
        self.ctx.ui_chrome = self.ui_chrome
        # Notifications describe mutations in one specific playthrough.
        # Carrying an in-flight toast across Return to Title -> New Game would
        # visually attribute the previous run's reward/error to the fresh run.
        if hasattr(self, "toast_stack"):
            self.toast_stack.toasts.clear()

    def _start_new_game(self) -> None:
        # The title writes the chosen protagonist name onto the current state
        # immediately before invoking this callback.  Capture that one piece
        # of title input, then rebuild the rest from authored pack data so a
        # return-to-title -> New Game cannot leak flags, time, inventory,
        # affection, route, story cursors, or plugin state from the old run.
        player_name = self.state.player.name
        content_root = self.config.pack_content(self.pack)
        new_state, new_npcs, new_meta = load_pack(content_root)
        new_state.player.name = player_name
        self._install_new_game_state(new_state, new_npcs, new_meta)

        # Optional intro scene if defined in meta.
        self.manager.replace(ExplorationScene(self.ctx),
                             **self._exploration_callbacks())
        intro = self.meta.get("intro_scene")
        if intro and intro in self.state.story.scenes:
            # Route through _start_dialogue so the post-dialogue ambient
            # hook re-check fires (in case the intro's on_end satisfies a
            # hook at the starting location).
            self._start_dialogue(intro)
        else:
            # No intro — still check whether the starting location has an
            # auto/enter hook eligible right now.
            self._check_ambient_hooks()
        # First run only: teach the controls once, over the first scene.
        if not getattr(self.config, "seen_intro", False):
            self._open_onboarding()

    def _open_onboarding(self) -> None:
        def done() -> None:
            self.manager.pop()
            self.config.seen_intro = True
            self.config.save_to_disk()
        self.manager.push(OnboardingScene(self.ctx), on_close=done)

    def _exploration_callbacks(self) -> dict[str, Callable]:
        return dict(
            on_map=self._open_map,
            on_affection=self._open_affection,
            on_log=self._open_event_log,
            on_save=self._open_save_menu,
            on_settings=self._open_settings,
            on_menu=self._open_menu,
            on_achievements=self._open_achievements,
            on_inventory=self._open_inventory,
            on_cg_gallery=self._open_cg_gallery,
            on_music_room=self._open_music_room,
            on_endings=self._open_endings,
            on_scene_replay=self._open_scene_replay,
            on_clues=self._open_clues,
            on_quit_to_title=self._quit_to_title,
            on_open_npc=self._open_npc_actions,
            on_start_scene=self._start_dialogue,
            on_move_to=self._move_to,
            on_advance_time=self._advance_time,
            get_advance_time_availability=self._advance_time_availability,
            get_map_availability=self._map_availability,
        )

    def _advance_time_availability(self) -> tuple[bool, str | None]:
        """Return the pack-authored availability of the free-wait action.

        Packs can stage an opening or other directed sequence without hiding
        the clock mechanic by declaring a flag gate in ``content/meta.yaml``::

            exploration_guidance:
              advance_time:
                requires_flags: [orientation_done]
                locked_reason: "先完成目前的目標。"

        This is deliberately generic: core knows only flags and a message,
        never a pack's characters or story.  Missing/malformed optional
        metadata keeps the historical allow-by-default behaviour.
        """
        return action_availability(self.meta, self.state, "advance_time")

    def _map_availability(self) -> tuple[bool, str | None]:
        """Return one policy for the CTA, M shortcut, menu, and open guard."""
        return action_availability(self.meta, self.state, "map")

    def _open_menu(self) -> None:
        """Open the consolidated in-game menu overlay."""
        def from_menu(callback: Callable[[], None]) -> Callable[[], None]:
            """Wrap a target callback so it first closes the menu overlay,
            then opens the target. Otherwise the new overlay would stack
            on top of the menu."""
            def wrapped():
                self.manager.pop()
                callback()
            return wrapped

        self.manager.push(
            MenuScene(self.ctx),
            on_close=self.manager.pop,
            on_map=from_menu(self._open_map),
            on_affection=from_menu(self._open_affection),
            on_log=from_menu(self._open_event_log),
            on_achievements=from_menu(self._open_achievements),
            on_inventory=from_menu(self._open_inventory),
            on_cg_gallery=from_menu(self._open_cg_gallery),
            on_music_room=from_menu(self._open_music_room),
            on_endings=from_menu(self._open_endings),
            on_scene_replay=from_menu(self._open_scene_replay),
            on_flowchart=from_menu(self._open_flowchart),
            on_relationships=from_menu(self._open_relationships),
            on_character_profiles=from_menu(self._open_character_profiles),
            on_quest_log=from_menu(self._open_quest_log),
            on_clues=from_menu(self._open_clues),
            on_credits=from_menu(self._open_credits),
            on_save=from_menu(self._open_save_menu),
            on_load=from_menu(self._open_load_menu),
            # Settings is a subordinate System surface.  Keep MenuScene below
            # it so Back returns to the same category/focus instead of dropping
            # the player straight into gameplay.
            on_settings=self._open_settings,
            on_quit_to_title=from_menu(self._quit_to_title),
            on_quit_app=self._quit_app,
            get_map_availability=self._map_availability,
        )

    def _open_quest_log(self) -> None:
        self.manager.push(QuestLogScene(self.ctx),
                          on_close=self.manager.pop)

    def _open_clues(self) -> None:
        self.manager.push(CluesScene(self.ctx),
                          on_close=self.manager.pop)

    def _open_settings(self) -> None:
        stack = self.manager.stack()
        locale_change_allowed = bool(
            stack and isinstance(stack[0], TitleScene)
        )
        self.manager.push(
            SettingsScene(self.ctx),
            on_close=self.manager.pop,
            on_controls=self._open_controls,
            on_fullscreen_change=self._apply_fullscreen_setting,
            on_locale_change=self._change_locale_from_settings,
            locale_change_allowed=locale_change_allowed,
        )

    def _apply_fullscreen_setting(self, enabled: bool) -> None:
        """Apply Settings' persisted fullscreen authority to the live window."""
        if self.display is None:
            return
        flags = pygame.FULLSCREEN if enabled else pygame.RESIZABLE
        self.display = pygame.display.set_mode(
            self.config.screen_size,
            flags,
            vsync=1 if self.config.vsync else 0,
        )
        self._compute_view()

    def _apply_locale_fonts(self) -> None:
        """Rebind the shared font registry to the effective locale sources."""
        sources = self.localization.font_sources
        locale_font_declared = bool(sources.bundled or sources.candidates)
        candidates = (
            tuple(sources.candidates) or self._base_font_candidates
        )
        bundled = (
            None if locale_font_declared else self._legacy_bundled_font_path
        )
        if sources.bundled:
            pack_root = self.config.pack_root(self.pack).resolve()
            candidate = (pack_root / sources.bundled).resolve()
            try:
                contained = candidate.is_relative_to(pack_root)
            except AttributeError:  # pragma: no cover - Python < 3.9 embedder
                contained = pack_root == candidate or pack_root in candidate.parents
            if contained and candidate.is_file():
                bundled = candidate
        self.fonts.reconfigure(candidates, bundled=bundled)

    def _change_locale_from_settings(self, locale_id: str) -> str:
        """Apply a presentation-only locale switch at the safe title boundary."""
        stack = self.manager.stack()
        if not stack or not isinstance(stack[0], TitleScene):
            return self.localization.effective

        previous = self.localization.effective
        effective = self.localization.activate(locale_id)
        if (
            not self.localization.requested
            or effective != self.localization.requested
        ):
            self.localization.activate(previous)
            return previous

        self.config.set_preferred_locale(effective, self.config.pack_id)
        self.config.save_to_disk()
        self._apply_locale_fonts()
        self.state.time.bind_localization(self.localization)
        self.state.affection.bind_localization(self.localization)

        title = stack[0]
        kwargs = self._title_scene_kwargs()
        title.refresh_localized_copy(
            title=str(kwargs["title"]),
            subtitle=str(kwargs["subtitle"]),
            presentation=kwargs["presentation"],
        )
        if pygame.display.get_init():
            pygame.display.set_caption(str(kwargs["title"]))
        return effective

    def _open_controls(self) -> None:
        self.manager.push(
            ControlsScene(self.ctx),
            on_close=self.manager.pop,
        )

    def _open_achievements(self) -> None:
        self.manager.push(AchievementsScene(self.ctx),
                          on_close=self.manager.pop)

    def _open_inventory(self) -> None:
        self.manager.push(InventoryScene(self.ctx),
                          on_close=self.manager.pop)

    def _open_cg_gallery(self) -> None:
        self.manager.push(CGGalleryScene(self.ctx),
                          on_close=self.manager.pop)

    def _open_music_room(self) -> None:
        self.manager.push(MusicRoomScene(self.ctx),
                          on_close=self.manager.pop)

    def _open_endings(self) -> None:
        self.manager.push(EndingsScene(self.ctx),
                          on_close=self.manager.pop)

    def _open_scene_replay(self) -> None:
        self.manager.push(SceneReplayScene(self.ctx),
                          on_close=self.manager.pop)

    def _open_flowchart(self) -> None:
        """Open the chapter chart with read-only replay for completed scenes.

        A chart card is historical navigation, not a live branch jump. Keep
        the chart below a one-shot :class:`SceneReplayScene` so every authored
        effect runs only in its isolated sandbox; completion or Back returns to
        the same chart without changing the active playthrough.
        """
        def jump(scene_id: str) -> None:
            self.manager.push(
                SceneReplayScene(self.ctx),
                on_close=self.manager.pop,
                initial_scene_id=scene_id,
                close_after_replay=True,
            )
        self.manager.push(FlowchartScene(self.ctx),
                          on_close=self.manager.pop, on_jump=jump)

    def _open_flowchart_browse(self) -> None:
        """Open the chapter flowchart in browse-only mode (from the title).

        There is no live scene to jump into from the title, so ``on_jump`` is
        None — the chart reads as a chapter overview; clicking a chapter is
        inert (FlowchartScene guards the replay on ``on_jump``). The pause-menu
        entry (``_open_flowchart``) opens the same safe replay sandbox used by
        the scene-replay gallery.
        """
        self.manager.push(FlowchartScene(self.ctx),
                          on_close=self.manager.pop, on_jump=None)

    def _open_relationships(self) -> None:
        """Open the read-only relationship-status panel (affection legibility)."""
        self.manager.push(RelationshipsScene(self.ctx),
                          on_close=self.manager.pop)

    def _open_credits(self) -> None:
        """Open the credits / 鳴謝 overlay (pack-supplied attributions)."""
        self.manager.push(CreditsScene(self.ctx),
                          on_close=self.manager.pop)

    def _open_character_profiles(self) -> None:
        self.manager.push(CharacterProfileScene(self.ctx),
                          on_close=self.manager.pop)

    def _open_shop(self, npc_id: str) -> None:
        self.manager.push(ShopScene(self.ctx),
                          npc_id=npc_id, on_close=self.manager.pop)

    def _open_scrollback(self) -> None:
        self.manager.push(ScrollbackScene(self.ctx),
                          on_close=self.manager.pop)

    def _open_gift_picker(self, npc_id: str,
                          on_picked: Callable[[str], None]) -> None:
        def picked(item_id: str) -> None:
            self.manager.pop()    # close the inventory overlay
            on_picked(item_id)
        self.manager.push(InventoryScene(self.ctx),
                          pick_for_npc=npc_id,
                          on_pick=picked,
                          on_close=self.manager.pop)

    def _record_clear_data(self) -> None:
        """Merge the current playthrough's progress into the persisted clear
        data (New Game+).

        Saving even when the in-memory union is unchanged makes shutdown
        self-healing: a missing file or a prior transient write failure gets
        another atomic write attempt at every normal exit.
        """
        try:
            self._refresh_media_authority()
            self.clear_data.record_from_state(
                self.state,
                authored_cg_ids=self._authored_media_authority.cg_ids,
                authored_music_ids=self._authored_media_authority.music_ids,
            )
            self.clear_data.save(self._clear_data_path)
        except Exception:
            pass

    def _title_scene_kwargs(self) -> dict[str, object]:
        """Return the single authoritative title-screen wiring.

        Startup and Return to Title must expose the same pack-authored
        presentation, callbacks, BGM and save-aware Continue entry.  Keeping
        this in one place prevents a new title option from working only on one
        of those two paths.
        """
        presentation = getattr(
            self,
            "title_screen_spec",
            None,
        )
        if presentation is not None:
            presentation = presentation.model_copy(update={
                "tagline": self.presentation_text.apply(
                    self.localization.story(
                        "meta.title_screen.tagline",
                        presentation.tagline,
                    ),
                ),
                "footer_text": self.presentation_text.apply(
                    self.localization.story(
                        "meta.title_screen.footer_text",
                        presentation.footer_text,
                    ),
                ),
            })
        return {
            "title": self.presentation_text.apply(
                self.localization.story("meta.title", self.config.title),
            ),
            "subtitle": self.presentation_text.apply(
                self.localization.story(
                    "meta.subtitle",
                    self.config.subtitle,
                ),
            ),
            "version": self.meta.get("version"),
            "bg": self.meta.get("title_bg"),
            "bgm": self.meta.get("title_bgm"),
            "presentation": presentation,
            "on_continue": (
                self._continue_game if self._has_saves() else None
            ),
            "on_new_game": self._start_new_game,
            "on_load": self._open_load_menu,
            "on_quit": self._quit_app,
            "on_cg_gallery": self._open_cg_gallery,
            "on_music_room": self._open_music_room,
            "on_endings": self._open_endings,
            "on_flowchart": self._open_flowchart_browse,
            "on_scene_replay": self._open_scene_replay,
            "on_credits": self._open_credits,
            "on_settings": self._open_settings,
        }

    def _quit_to_title(self) -> None:
        self._record_clear_data()
        self.manager.clear_to(
            TitleScene(self.ctx),
            **self._title_scene_kwargs(),
        )

    def _open_map(self) -> None:
        # The authored spatial overview is the primary travel surface. A node
        # selection opens the existing detail/confirmation sheet; this screen
        # itself never mutates location or time.
        if not self._map_transition_allowed():
            return
        self.manager.push(
            MapScene(self.ctx),
            on_close=self.manager.pop,
            on_move_to=self._preview_from_world_map,
        )

    def _map_transition_allowed(self) -> bool:
        """Guard every app-level route into the authored travel surface."""
        available, reason = self._map_availability()
        if not available:
            queue = self.state.meta.setdefault("__pending_toasts__", [])
            queue.append((
                "notice",
                self.localization.t("map", "地圖"),
                reason or "先完成目前的目標。",
            ))
            return False
        return True

    def _open_world_map(self) -> None:
        # When the detail sheet sits above a live MapScene, simply reveal that
        # same map so its story/general toggle and focus remain intact. Keep a
        # replace fallback for callers that opened a standalone picker.
        if not self._map_transition_allowed():
            return
        stack = self.manager.stack()
        if len(stack) >= 2 and isinstance(stack[-2], MapScene):
            self.manager.pop()
            return
        kwargs = {
            "on_close": self.manager.pop,
            "on_move_to": self._preview_from_world_map,
        }
        if isinstance(self.manager.current, DestinationPickerScene):
            self.manager.replace(MapScene(self.ctx), **kwargs)
        else:
            self.manager.push(MapScene(self.ctx), **kwargs)

    def _preview_from_world_map(self, loc_id: str) -> None:
        if not self._map_transition_allowed():
            return
        self.manager.push(
            DestinationPickerScene(self.ctx),
            on_close=self.manager.pop,
            on_move_to=self._move_from_map,
            on_travel=self._travel_from_map_plan,
            on_wait=self._advance_time,
            on_world_map=self._open_world_map,
            initial_destination=loc_id,
            detail_only=True,
        )

    def _open_affection(self) -> None:
        self.manager.push(AffectionScene(self.ctx),
                          on_close=self.manager.pop)

    def _open_event_log(self) -> None:
        self.manager.push(EventLogScene(self.ctx),
                          on_close=self.manager.pop)

    def _open_save_menu(self) -> None:
        # Pass a screen-grabber so SaveScene can capture a thumbnail for
        # each save (320x180, stored as a sibling PNG by SaveManager).
        self.manager.push(SaveScene(self.ctx),
                          mode="save", on_close=self.manager.pop,
                          get_screen_surface=lambda: self.screen,
                          get_resume_context=self._capture_resume_context)

    def _open_load_menu(self) -> None:
        # From title or in-game both go to load mode.
        self.manager.push(SaveScene(self.ctx),
                          mode="load", on_close=self._after_load_pop)

    def _after_load_pop(self) -> None:
        """Called when the load overlay self-closes after loading.

        Rebuild the saved dialogue presentation when present; legacy saves
        without that envelope use the exploration fallback.
        """
        resume = self.state.meta.pop("__loaded_resume__", None)
        self._restore_loaded_view(resume)

    def _capture_resume_context(self) -> dict | None:
        """Return the top-most live dialogue scene's save presentation."""
        for scene in reversed(self.manager.stack()):
            if isinstance(scene, DialogueScene):
                try:
                    return scene.export_resume_context()
                except Exception:
                    return None
        return None

    @staticmethod
    def _resume_matches_state(
        resume: object,
        state: GameState,
        *,
        authored_story=None,
        authored_npcs=None,
        authored_map=None,
        localization=None,
    ) -> bool:
        """Validate untrusted presentation metadata against ``state``."""
        from .dialogue.dialogue_engine import resume_matches_state

        return resume_matches_state(
            resume,
            state,
            authored_story=authored_story,
            authored_npcs=authored_npcs,
            authored_map=authored_map,
            localization=localization,
        )

    def _resume_matches_loaded_state(self, resume: object) -> bool:
        """Validate a resume envelope before adding a DialogueScene."""
        return self._resume_matches_state(
            resume,
            self.state,
            authored_story=self._authored_story_catalog,
            authored_npcs=self.npcs,
            authored_map=self._authored_map_catalog,
            localization=self.localization,
        )

    def _restore_loaded_view(self, resume: object) -> bool:
        """Replace all stale scenes with the loaded exploration/dialogue view."""
        can_resume = self._resume_matches_loaded_state(resume)

        # Exploration is the stable base even before a pack assigns its first
        # location (new-game already supports that state). Keeping it under a
        # resumed dialogue prevents the normal on_done pop from emptying the
        # scene stack.
        self.manager.clear_to(
            ExplorationScene(self.ctx),
            transition=False,
            **self._exploration_callbacks(),
        )
        if can_resume:
            dialogue_scene = DialogueScene(self.ctx)
            self.manager.push(
                dialogue_scene,
                scene_id=self.state.story.current_scene,
                resume_context=resume,
                **self._dialogue_enter_callbacks(dialogue_scene),
            )
        return True

    # ----------- quicksave / quickload ----------------------------------
    #
    # Key choice: F5 is the dev hot-reload (see _step ~640) and F11/F12 are
    # the state-dump / screenshot keys. To avoid colliding with any of
    # those, quicksave is bound to F6 and quickload to F9. They are wired
    # in the main loop unconditionally (they don't depend on dev mode), and
    # the dev F5 handler is left untouched, so there is no key collision.

    def _quicksave(self) -> None:
        """Write the live state to ``config.quicksave_slot``.

        Mirrors the manual save path in ``save_scene._on_action``: dump the
        state as JSON, attach a label/summary + screen thumbnail, fire the
        SAVE_BEFORE_SERIALIZE hook so plugins can patch the payload, then
        write through SaveManager. Failures are swallowed so a bad save can
        never crash the game.
        """
        try:
            slot = self.config.quicksave_slot
            sm = SaveManager(self.config.save_dir())
            loc = self.state.map.current
            summary = (
                f"{self.state.time.label()} · "
                f"{(loc.name if loc else '無位置')}"
            )
            # Clean slot title — the card already shows a 快速 badge + the
            # summary line, so don't repeat the summary in the label.
            label = self.state.player.name or self.localization.t('quicksave', '快速存檔')
            payload = self.state.model_dump(mode="json")
            from .plugins import fire_event
            from .plugins.context import HookEvent
            fire_event(self.state, HookEvent.SAVE_BEFORE_SERIALIZE,
                       slot=slot, payload=payload)
            thumbnail = None if self.headless else self.screen
            sm.save(
                slot,
                payload,
                label=label,
                summary=summary,
                thumbnail=thumbnail,
                pack_meta=self.state.meta.get("__pack_meta__", {}),
                resume_context=self._capture_resume_context(),
            )
        except Exception as exc:
            print(f"[quicksave] skipped: {exc}", file=sys.stderr)

    def _load_slot(self, slot: str) -> bool:
        """Load ``slot`` and restore into exploration; return True on success.

        Shared by quick-load (F9) and the title-screen Continue. Mirrors
        ``save_scene._on_action`` (load branch) for the state swap. A
        missing/invalid save is a no-op returning False.
        """
        try:
            self._refresh_media_authority()
            sm = SaveManager(self.config.save_dir())
            from .core.save_manager import SaveError
            try:
                data = sm.load(slot)
            except SaveError:
                return False  # nothing to load yet
            from .core.pack_migration import (
                check_and_migrate_pack, PACK_MIGRATIONS,
                SavePackMismatchError, SavePackSchemaError,
            )
            pack_meta = self.state.meta.get("__pack_meta__", {})
            try:
                data = check_and_migrate_pack(
                    data,
                    current_pack_id=str(pack_meta.get("pack_id", "")),
                    current_pack_version=str(
                        pack_meta.get("pack_format_version", "0")),
                    registry=PACK_MIGRATIONS,
                )
            except (SavePackMismatchError, SavePackSchemaError) as exc:
                print(f"[load] incompatible save: {exc}", file=sys.stderr)
                return False
            # Keep presentation metadata in the migration payload so a pack
            # migration that renames a scene or shifts a cursor can update the
            # resume envelope atomically with StoryGraph. It is removed only
            # after migration because it is not a GameState field.
            has_resume_context = "_resume" in data
            resume_context = data.pop("_resume", None)
            for key in ("_saved_at", "_label", "_summary",
                        "_schema_version", "_thumbnail_path",
                        "_pack_id", "_pack_format_version", "_engine_version"):
                data.pop(key, None)
            new_state = GameState(**data)
            new_state.map = rebind_authored_map_catalog(
                self._authored_map_catalog,
                new_state.map,
            )
            new_state.endings = rebind_authored_ending_catalog(
                self._authored_ending_catalog,
                new_state.endings,
            )
            new_state.story = rebind_authored_story_catalog(
                self._authored_story_catalog,
                new_state.story,
            )
            (
                new_state.cg_gallery,
                new_state.music_room,
            ) = rebind_authorized_media_progress(
                new_state.cg_gallery,
                new_state.music_room,
                self._authored_media_authority,
            )
            new_state.meta = rebind_authored_audio_meta(
                self._authored_story_catalog,
                new_state.meta,
                self._authored_map_catalog,
                has_resume_context=has_resume_context,
                resume_context=resume_context,
            )
            new_state.dialogue_history = (
                rebind_authored_dialogue_history_voices(
                    self._authored_story_catalog,
                    new_state.dialogue_history,
                )
            )
            new_state.dialogue_history = rebind_dialogue_history_localization(
                self._authored_story_catalog,
                new_state.dialogue_history,
                new_state,
                localization=self.localization,
                speaker_id_resolver=self._dialogue_speaker_id,
            )
            from .dialogue.replay_engine import (
                rebind_authored_replay_visual_fx,
            )
            new_state.read_log = rebind_authored_replay_visual_fx(
                new_state.read_log,
                self._authored_story_catalog,
            )
            if has_resume_context:
                resume_context = rebind_resume_choice_presentations(
                    resume_context,
                    new_state,
                    localization=self.localization,
                    speaker_id_resolver=self._dialogue_speaker_id,
                )
            # Resume validation belongs to the candidate state, before either
            # the live GameState or scene stack is touched. A malformed
            # user-edited envelope must make quickload/Continue an atomic no-op,
            # not leave a newly-loaded state underneath the old screen.
            if has_resume_context and not self._resume_matches_state(
                    resume_context,
                    new_state,
                    authored_story=self._authored_story_catalog,
                    authored_npcs=self.npcs,
                    authored_map=self._authored_map_catalog,
                    localization=self.localization,
            ):
                print(
                    "[load] invalid dialogue resume metadata",
                    file=sys.stderr,
                )
                return False
            # Preserve transient `__`-bridges (plugin manager, npc registry,
            # autosave config, pack meta) that were filtered out at save.
            preserved = {
                k: v for k, v in self.state.meta.items()
                if k.startswith("__")
            }
            self.state.__dict__.update(new_state.__dict__)
            self.state.meta.update(preserved)
            self.state.time.bind_localization(self.localization)
            self.state.affection.bind_localization(self.localization)
            self.clear_data.apply_to_state(
                self.state,
                registered_endings=self._pack_ending_ids,
                authored_cg_ids=self._authored_media_authority.cg_ids,
                authored_music_ids=self._authored_media_authority.music_ids,
            )
            from .plugins import fire_event
            from .plugins.context import HookEvent
            fire_event(self.state, HookEvent.SAVE_AFTER_LOAD,
                       slot=slot, payload=data)
            self._restore_loaded_view(resume_context)
            return True
        except Exception as exc:
            print(f"[load] skipped: {exc}", file=sys.stderr)
            return False

    def _quickload(self) -> None:
        """Load ``config.quicksave_slot`` (F9). No-op if none exists."""
        self._load_slot(self.config.quicksave_slot)

    def _continue_game(self) -> None:
        """Title-screen Continue: resume the most recently written save."""
        try:
            saves = SaveManager(self.config.save_dir()).list_saves()
        except Exception:
            saves = []
        if saves:
            self._load_slot(saves[0]["slot"])

    def _has_saves(self) -> bool:
        try:
            return bool(SaveManager(self.config.save_dir()).list_saves())
        except Exception:
            return False

    def _open_npc_actions(self, npc_id: str) -> None:
        """Show the lightweight NPC overlay (gift + shop).

        Replaces the previous LLM-driven free-chat overlay. When an LLM
        brain is wired back in this method will also offer a chat entry.
        """
        self.manager.push(NPCActionScene(self.ctx),
                          npc_id=npc_id, on_close=self.manager.pop,
                          on_request_gift=(lambda nid, callback:
                                             self._open_gift_picker(nid, callback)),
                          on_open_shop=self._open_shop)

    def _start_dialogue(self, scene_id: str) -> None:
        dialogue_scene = DialogueScene(self.ctx)
        self.manager.push(
            dialogue_scene,
            scene_id=scene_id,
            **self._dialogue_enter_callbacks(dialogue_scene),
        )

    def _dialogue_enter_callbacks(
        self,
        dialogue_scene: DialogueScene | None = None,
    ) -> dict[str, Callable]:
        """Callbacks shared by newly-started and save-resumed dialogue."""
        # Wrap on_done so that returning from any dialogue re-checks for
        # enter/auto hooks at the current location. Reason: a dialogue's
        # on_end / choice effects can advance time, set flags, or otherwise
        # newly satisfy an ambient hook (e.g. setting met_ruoxue while at
        # library) — the player should not have to leave and come back for
        # it to fire.
        def _after_dialogue() -> None:
            completed_scene_id = (
                dialogue_scene.scene_id
                if dialogue_scene is not None
                else None
            )
            self.manager.pop()
            if self._should_auto_open_map(completed_scene_id):
                self._open_map()
                return
            # A newly eligible same-location hook owns the next beat. Only a
            # quiet dialogue boundary may hand control back to the guided map.
            if self._check_ambient_hooks():
                return
            if self._should_auto_reopen_guided_map(dialogue_scene):
                self._open_map()
        return dict(
            on_done=_after_dialogue,
            on_resume_failed=self.manager.pop,
            on_scrollback=self._open_scrollback,
            on_save=self._open_save_menu,
            on_load=self._open_load_menu,
            on_config=self._open_settings,
            on_menu=self._open_menu,
            on_qsave=self._quicksave,
            on_qload=self._quickload,
            on_movie=self._open_movie,
            on_chapter_card=self._open_chapter_card,
        )

    def _should_auto_open_map(self, completed_scene_id: str | None) -> bool:
        """Whether a completed authored scene should reveal the travel map.

        Packs opt in under ``travel.spatial_map.auto_open_after_scenes``.
        Scene identity comes from the live :class:`DialogueScene`, so chained
        ``play_scene`` transitions correctly report the final tutorial scene
        instead of the scene that originally opened the dialogue overlay.
        """
        if not completed_scene_id:
            return False
        travel = self.meta.get("travel")
        if not isinstance(travel, dict):
            return False
        spatial_map = travel.get("spatial_map")
        if not isinstance(spatial_map, dict):
            return False
        configured = spatial_map.get("auto_open_after_scenes", ())
        if isinstance(configured, str):
            configured = (configured,)
        if not isinstance(configured, (list, tuple, set)):
            return False
        return completed_scene_id in {
            str(scene_id).strip()
            for scene_id in configured
            if str(scene_id).strip()
        }

    def _should_auto_reopen_guided_map(
        self,
        completed_dialogue: DialogueScene | None,
    ) -> bool:
        """Resolve the continuous guided-map hand-off at a safe UI boundary.

        The ending dialogue is still the current scene while its queued pop is
        being assembled. Any other active surface, or a modal visual effect
        queued by the completed presentation, owns the hand-off instead. An
        ambient hook is checked by the caller first and therefore never sits
        underneath a newly opened map.
        """
        if not guided_story_should_auto_reopen(
            self.meta,
            self.state.events.flags,
        ):
            return False
        if completed_dialogue is None:
            return False
        stack = self.manager.stack()
        if not stack or stack[-1] is not completed_dialogue:
            return False
        if any(
            not isinstance(scene, ExplorationScene)
            for scene in stack[:-1]
        ):
            return False

        # These directives push modal surfaces after ``on_done`` returns.
        # Avoid placing MapScene underneath them merely because their pygame
        # objects have not been constructed yet.
        visual_fx = self.state.meta.get("__visual_fx__", ())
        if isinstance(visual_fx, list) and any(
            isinstance(directive, dict)
            and directive.get("fx") in {"chapter_card", "play_movie"}
            for directive in visual_fx
        ):
            return False
        return True

    def _open_movie(self, directive: dict) -> None:
        """Push a full-screen movie overlay (from a play_movie effect).

        ``directive`` is the queued play_movie payload ({path, kind, fps, loop,
        skippable}). The overlay pops itself when the movie finishes / is
        skipped, returning to the dialogue beneath.
        """
        from .scenes.movie_scene import MoviePlayerScene
        self.manager.push(
            MoviePlayerScene(self.ctx),
            path=directive.get("path", ""),
            kind=directive.get("kind", "auto"),
            fps=directive.get("fps", 24.0),
            loop=directive.get("loop", False),
            skippable=directive.get("skippable", True),
            on_done=self.manager.pop,
        )

    def _open_chapter_card(self, directive: dict) -> None:
        """Push the chapter title-card overlay (from a set_chapter /
        advance_chapter directive).

        ``directive`` is the queued chapter_card payload ({chapter, title,
        subtitle}). The overlay pops itself when dismissed (click / key /
        auto-timeout), returning to the dialogue beneath.
        """
        self.manager.push(
            ChapterCardScene(self.ctx),
            title=directive.get("title", ""),
            subtitle=directive.get("subtitle", ""),
            on_done=self.manager.pop,
        )

    def _check_ambient_hooks(self) -> bool:
        """Fire the first eligible enter/auto hook at the current location.

        Returns True if a hook was queued (a DialogueScene push is pending).
        This is what makes route scenes feel alive: when time advances, when
        a dialogue ends with a flag/time change, or when the player just
        walked in, we look at the current location's enter/auto hooks and
        fire whichever became newly eligible.
        """
        flags = self.state.events.flags
        for hook in self.state.map.available_scenes(
            time_of_day=self.state.time.time_of_day.value,
            flags=flags,
            played_scenes=self.state.story.played,
            state=self.state,
        ):
            if hook.trigger in ("enter", "auto"):
                self._start_dialogue(hook.scene_id)
                return True
        return False

    def _move_to(
        self,
        loc_id: str,
        *,
        start_dialogue: bool = True,
        allow_distant: bool = False,
    ) -> TravelResult:
        """Commit travel through the shared service.

        Exploration keeps the historical one-exit contract.  The destination
        picker opts into an atomic graph itinerary after the player confirms
        it, so intermediate locations never become visited or fire hooks.
        """
        plan = TravelService.plan(
            self.state,
            loc_id,
            allow_distant=allow_distant,
            minimum_cost=(
                minimum_travel_cost(self.meta) if allow_distant else 0
            ),
        )
        if plan is None:
            return TravelResult(
                ok=False,
                error=f"目前無法前往 {loc_id}",
            )
        result = TravelService.commit(self.state, plan)
        if result.ok and result.scene_triggered and start_dialogue:
            self._start_dialogue(result.scene_triggered)
        return result

    def _move_from_map(self, loc_id: str) -> None:
        """Compatibility path for callers that only supply a location id."""
        plan = TravelService.plan(
            self.state,
            loc_id,
            allow_distant=(
                graph_scope := graph_travel_enabled(
                    self.meta, self.state.events.flags
                )
            ),
            minimum_cost=(
                minimum_travel_cost(self.meta) if graph_scope else 0
            ),
        )
        result = (
            TravelService.commit(self.state, plan)
            if plan is not None
            else TravelResult(ok=False, error=f"目前無法前往 {loc_id}")
        )
        self._finish_map_travel(result)

    def _travel_from_map_plan(self, plan: TravelPlan) -> None:
        """Commit the exact immutable itinerary the detail sheet displayed."""
        self._finish_map_travel(TravelService.commit(self.state, plan))

    def _finish_map_travel(self, result: TravelResult) -> None:
        # Commit first. A stale/blocked quote keeps the detail sheet open so
        # the player sees a reason instead of being silently dropped back into
        # exploration.
        if result.ok:
            # Queue operations in this order: remove the confirmation sheet,
            # remove its underlying map when present, then place arrival
            # dialogue above exploration. SceneManager applies queued
            # mutations in order at the end of the frame.
            stack = self.manager.stack()
            close_underlying_map = (
                len(stack) >= 2 and isinstance(stack[-2], MapScene)
            )
            self.manager.pop()
            if close_underlying_map:
                self.manager.pop()
            if result.scene_triggered:
                self._start_dialogue(result.scene_triggered)
            return
        queue = self.state.meta.setdefault("__pending_toasts__", [])
        queue.append((
            "notice",
            "無法前往",
            result.error or "目的地狀態已改變，請重新確認。",
        ))

    def _advance_time(self) -> bool:
        """Advance one phase and report whether deliberate waiting must stop.

        Destination confirmations call this once per advertised phase.  A
        blocked action or newly eligible ambient scene is an interruption;
        returning ``True`` prevents a multi-phase wait from skipping it.
        Ordinary one-click wait callers may ignore the return value.
        """
        available, reason = self._advance_time_availability()
        if not available:
            queue = self.state.meta.setdefault("__pending_toasts__", [])
            queue.append((
                "notice",
                "現在還不能等待",
                reason or "先完成目前的目標，再推進時間。",
            ))
            return True
        self.state.time.advance(1)
        self.state.events.record(kind="system",
                                 title=f"時間流動到 {self.state.time.label()}")
        return self._check_ambient_hooks()

    def _quit_app(self) -> None:
        self._running = False
        # Menu callbacks and an OS QUIT event can be exercised without driving
        # another frame (embedding tests and some web shells do exactly that),
        # so persist at the request boundary as well as in the loop finalizer.
        self._record_clear_data()

    def _finalize_shutdown(self) -> None:
        """Persist global progress and tear down integrations on every exit."""
        try:
            self._record_clear_data()
        finally:
            try:
                self._shutdown_steam()
            finally:
                pygame.quit()

    # ----------- main loop ----------------------------------------------

    def run(self) -> None:
        """Synchronous main loop (desktop / PyInstaller)."""
        try:
            while self._running:
                dt = self.clock.tick(self.config.fps) / 1000.0
                self._step(dt)
        finally:
            self._finalize_shutdown()

    async def run_async(self) -> None:
        """Async main loop for the web (pygbag/Emscripten) target.

        Identical to :meth:`run` but yields to the browser event loop once
        per frame via ``await asyncio.sleep(0)`` — without this the WASM tab
        would freeze. Desktop never uses this path, so its behavior is
        unchanged.
        """
        import asyncio
        try:
            while self._running:
                dt = self.clock.tick(self.config.fps) / 1000.0
                self._step(dt)
                await asyncio.sleep(0)
        finally:
            self._finalize_shutdown()

    def _step(self, dt: float) -> None:
        """Run exactly one frame: input -> update -> draw -> present.

        Contains no blocking calls, so it is safe under both the sync and
        async drivers. ``dt`` is clamped to absorb the huge spikes that
        happen after a stall (e.g. a backgrounded browser tab).
        """
        dt = min(dt, 0.1)
        events = pygame.event.get()
        win_size = self.display.get_size() if self.display is not None else None
        inp = self._input_router.collect(
            events,
            dt=dt,
            transform=self._window_to_logical,
            window_size=win_size,
            touch_mode=self.config.touch_mode,
            binding_overrides=getattr(
                self.config,
                "input_bindings",
                {},
            ),
        )
        if inp.quit_requested:
            self._quit_app()
        # Window resize -> recompute the letterbox view transform.
        for e in events:
            if e.type == pygame.VIDEORESIZE and self.display is not None:
                self._on_resize(e.w, e.h)
        # A captured physical key belongs exclusively to ControlsScene.  In
        # particular F6/F9/F12 must not quick-save, quick-load, or take a
        # screenshot before the scene can assign the new binding.
        capturing_binding = bool(
            getattr(self.manager.current, "capturing", False)
        )
        if not capturing_binding:
            # Player-configurable global actions. They are semantic so the old
            # physical key stops firing as soon as Controls assigns a new one.
            if inp.pressed(InputAction.SCREENSHOT):
                self.take_screenshot()
            elif inp.pressed(InputAction.QUICKSAVE):
                self._quicksave()
            elif inp.pressed(InputAction.QUICKLOAD):
                self._quickload()
            # Dev-only diagnostics remain deliberately outside the player
            # profile. F11 used to dump state in release builds; keep that
            # internal data surface behind the same gate as the debug overlay
            # and hot reload.
            if self._dev_mode:
                for e in inp.events:
                    if e.type == pygame.KEYDOWN:
                        if e.key == pygame.K_F1 and self._debug_overlay:
                            self._debug_overlay.toggle()
                        elif e.key == pygame.K_F5 and self._hot_reloader:
                            self._do_hot_reload()
                        elif e.key == pygame.K_F11:
                            self.dump_state(verbose=True)
        self.manager.update(dt, inp)
        self._poll_achievement_toasts()
        # Pump Steam callbacks once per frame (flushes stored stats when
        # dirty). No-op when Steam is not enabled / not present.
        if self._steam is not None:
            self._steam.run_callbacks()
        self.toast_stack.update(dt, inp)
        self.manager.draw(self.screen)
        # Dev overlay drawn after scene stack, before toasts.
        if self._dev_mode and self._debug_overlay:
            try:
                self._debug_overlay.set_state(
                    self.state,
                    cache_stats=self.assets.cache_stats(),
                )
                self._debug_overlay.update(dt, inp)
                self._debug_overlay.draw(self.screen)
            except Exception as _ov_exc:
                import sys as _sys
                print(f"[dev] overlay error: {_ov_exc}", file=_sys.stderr)
        self.toast_stack.draw(self.screen)
        self._maybe_take_pending_screenshot()
        if self._inspect_pending:
            self._do_inspect_dump()
            self._inspect_pending = False
        self._present()

    # ----------- responsive presentation --------------------------------

    def _compute_view(self) -> None:
        """Recompute the scale + letterbox offset that fits the logical
        canvas into the current display, preserving 16:9 aspect ratio."""
        if self.display is None:
            return
        self._view_scale, self._view_offset, self._view_size = _letterbox_view(
            tuple(self.config.screen_size), self.display.get_size(),
        )

    def _on_resize(self, w: int, h: int) -> None:
        flags = pygame.FULLSCREEN if self.config.fullscreen else pygame.RESIZABLE
        self.display = pygame.display.set_mode(
            (max(1, w), max(1, h)), flags, vsync=1 if self.config.vsync else 0,
        )
        self._compute_view()

    def _window_to_logical(self, pos: tuple[int, int]) -> tuple[int, int]:
        """Map a point in window pixels back to logical canvas coords."""
        return _unproject(pos, self._view_scale, self._view_offset)

    def _present(self) -> None:
        """Scale-blit the logical canvas onto the real window + flip.

        No-op in headless mode (no window). When the window matches the
        logical size exactly (the default), blits 1:1 for a pixel-identical
        result and skips the smoothscale.
        """
        if self.display is None:
            return
        if (self._view_scale == 1.0 and self._view_offset == (0, 0)
                and self._view_size == tuple(self.config.screen_size)):
            self.display.blit(self.screen, (0, 0))
        else:
            # The window aspect differs from the 16:9 canvas (e.g. a 16:10
            # laptop display). Instead of hard black letterbox bars framing the
            # art, fill the whole window with a window-COVERING, darkened copy
            # of the frame, then blit the undistorted CONTAIN-fit frame centred
            # on top — so the scene bleeds softly into the margin rather than
            # being boxed in. Costs two scales, but only on the non-1:1 path.
            dw, dh = self.display.get_size()
            lw, lh = self.screen.get_size()
            cover = max(dw / lw, dh / lh) if (lw and lh) else 1.0
            cw, ch = max(1, int(lw * cover)), max(1, int(lh * cover))
            back = pygame.transform.smoothscale(self.screen, (cw, ch))
            self.display.fill((0, 0, 0))
            self.display.blit(back, ((dw - cw) // 2, (dh - ch) // 2))
            shade = pygame.Surface((dw, dh), pygame.SRCALPHA)
            shade.fill((0, 0, 0, 150))      # darken the bleed so it reads as frame, not a 2nd scene
            self.display.blit(shade, (0, 0))
            scaled = pygame.transform.smoothscale(self.screen, self._view_size)
            self.display.blit(scaled, self._view_offset)
        pygame.display.flip()

    def _update_touch_gesture(self, events, inp) -> None:
        """Legacy one-off touch classifier for embedding compatibility.

        The application runtime uses :class:`InputRouter`, which additionally
        distinguishes taps from drags, tracks multiple contacts, and prevents
        emulated-mouse duplicates. This helper remains for clients that called
        it directly before the router existed.
        """
        for e in events:
            if e.type == pygame.FINGERDOWN:
                self._touch_start = (e.x, e.y)
            elif e.type == pygame.FINGERUP and self._touch_start is not None:
                sx, _sy = self._touch_start
                dx = e.x - sx
                dy = e.y - self._touch_start[1]
                self._touch_start = None
                if abs(dx) > 0.12 and abs(dx) > abs(dy):
                    inp.swipe = "right" if dx > 0 else "left"

    def quit(self) -> None:
        """Stop the main loop (used by the web entry / external drivers)."""
        self._quit_app()

    def _poll_achievement_toasts(self) -> None:
        """Surface any unlocked-but-unseen achievements as toasts, plus
        any item / resource deltas queued by ``apply_all``."""
        for ach in self.state.achievements.newly_unlocked():
            self.toast_stack.push(Toast(
                title=ach.title,
                detail=ach.description,
                icon=ach.icon,
            ))
            self.state.achievements.mark_seen(ach.id)
        # Pending item / resource deltas — drain and toast.
        queue = self.state.meta.pop("__pending_toasts__", None)
        if queue:
            for kind, key, delta in queue:
                if kind == "item":
                    item = self.state.items.get(key)
                    name = item.name if item else key
                    sign = "+" if delta > 0 else ""
                    self.toast_stack.push(Toast(
                        title=name,
                        detail=f"{sign}{delta}",
                        icon=item.icon if item else None,
                    ))
                elif kind == "resource":
                    d = self.state.resources.definition(key)
                    label = (d.name or key) if d else key
                    sym = d.symbol if d else ""
                    sign = "+" if delta > 0 else ""
                    self.toast_stack.push(Toast(
                        title=label,
                        detail=f"{sign}{sym}{delta}",
                        icon=d.icon if d else None,
                    ))
                elif kind == "affection":
                    # Per-choice affection feedback: a subtle signed "好感度 +N"
                    # for the affected character (`key` is the name, `delta` the
                    # signed change). Gated on the user setting so a player who
                    # finds a per-choice number too gamey can silence it; the
                    # heavier named-threshold "notice" beat is unaffected. Brief
                    # (2.2s) so it reads as an accent, not an interruption.
                    if not getattr(self.config, "show_affection_feedback", True):
                        continue
                    sign = "+" if delta > 0 else ""
                    self.toast_stack.push(Toast(
                        title=str(key),
                        detail=f"好感度 {sign}{delta}",
                        icon=None, duration=2.2,
                    ))
                elif kind == "notice":
                    # `key` is the title, `delta` is the detail text.
                    self.toast_stack.push(Toast(
                        title=str(key), detail=str(delta), icon=None,
                    ))
                elif kind == "clue":
                    # `key` is the clue id; `delta` is the title.
                    self.toast_stack.push(Toast(
                        title="新線索",
                        detail=str(delta),
                        icon=None,
                    ))

    # ----------- screenshot + inspect ------------------------------------

    def _make_screenshot_path(self, name: str | None = None) -> Path:
        shots_dir = writable_root() / "screenshots"
        shots_dir.mkdir(parents=True, exist_ok=True)
        if name is None:
            name = time.strftime("shot_%Y%m%d_%H%M%S.png")
        return shots_dir / name

    def take_screenshot(self, name: str | None = None) -> Path:
        path = self._make_screenshot_path(name)
        pygame.image.save(self.screen, str(path))
        print(f"[screenshot] saved -> {path}", file=sys.stderr)
        return path

    def schedule_screenshot(self, path: str | None = None) -> None:
        self._screenshot_pending = path

    def _maybe_take_pending_screenshot(self) -> None:
        if self._screenshot_pending is None:
            return
        target = self._screenshot_pending
        self._screenshot_pending = None
        if target:
            p = Path(target)
            p.parent.mkdir(parents=True, exist_ok=True)
            pygame.image.save(self.screen, str(p))
            print(f"[screenshot] saved -> {p}", file=sys.stderr)
        else:
            self.take_screenshot()

    def schedule_inspect(self) -> None:
        self._inspect_pending = True

    def inspect(self) -> dict:
        loc = self.state.map.current
        return {
            "title": self.config.title,
            "player": self.state.player.model_dump(),
            "time": {
                "day": self.state.time.day,
                "weekday": self.state.time.day_of_week.value,
                "weekday_label": self.state.time.weekday_label(),
                "time_of_day": self.state.time.time_of_day.value,
                "time_label": self.state.time.time_label(),
                "is_night": self.state.time.is_night(),
                "is_haunting": self.state.time.is_haunting_hour(),
            },
            "location": loc.id if loc else None,
            "location_name": loc.name if loc else None,
            "exits": [e.id for e in self.state.map.available_exits(
                self.state.events.flags)],
            "npcs_present": self.state.map.present_npcs(
                self.state.time.time_of_day.value,
                self.state.time.day_of_week.value,
                self.state.events.flags,
            ),
            "scenes_available": [
                h.scene_id for h in self.state.map.available_scenes(
                    time_of_day=self.state.time.time_of_day.value,
                    flags=self.state.events.flags,
                    played_scenes=self.state.story.played,
                    state=self.state,
                )
            ],
            "scenes_played": list(self.state.story.played),
            "current_scene": self.state.story.current_scene,
            "current_line_index": self.state.story.current_line_index,
            "affection": self.state.affection.all_stats(),
            "flags": dict(self.state.events.flags),
            "recent_events": [
                {"kind": e.kind, "title": e.title, "summary": e.summary,
                 "location": e.location, "timestamp": e.timestamp}
                for e in self.state.events.recent(15)
            ],
            "asset_cache": self.assets.cache_stats(),
            "scene_stack": self.manager.describe(),
        }

    def dump_state(self, *, verbose: bool = False) -> None:
        data = self.inspect()
        if verbose:
            print(json.dumps(data, ensure_ascii=False, indent=2),
                  file=sys.stderr)
        else:
            slim = {k: v for k, v in data.items()
                    if k not in ("recent_events", "flags", "scenes_played")}
            print(json.dumps(slim, ensure_ascii=False, indent=2),
                  file=sys.stderr)

    def _do_hot_reload(self) -> None:
        """Reload pack YAML, keeping player progress. Called on F5."""
        if self._hot_reloader is None:
            return
        try:
            new_state, new_npcs, new_meta = self._hot_reloader.reload(
                self.state, self.npcs
            )
            # Use the same complete runtime rebind as New Game. The reloader
            # has already overlaid player progress onto fresh authored data;
            # this refreshes dialogue/plugin/context owners, clear-data
            # bridges, UI specs, and the map/ending baselines used by later
            # quickloads.
            self._install_new_game_state(
                new_state,
                new_npcs,
                new_meta,
            )
            print("[dev] hot reload OK", file=sys.stderr)
        except Exception as exc:
            print(f"[dev] hot reload failed: {exc}", file=sys.stderr)

    def _do_inspect_dump(self) -> None:
        self._last_inspect = self.inspect()

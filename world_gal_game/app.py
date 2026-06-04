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
from .core.save_manager import SaveManager
from .core.localization import Localization
from .core.story_graph import Effect
from .core.time_system import bind_localization as bind_time_localization
from .dialogue.dialogue_engine import DialogueEngine
from .npc.llm_brain import default_brain, LLMBrain, EchoBrain
from .npc.npc_base import NPCRegistry
from .scenes.base import SceneManager, SceneContext
from .scenes.title import TitleScene
from .scenes.exploration import ExplorationScene
from .scenes.dialogue_scene import DialogueScene
from .scenes.map_scene import MapScene
from .scenes.destination_picker import DestinationPickerScene
from .scenes.affection_scene import AffectionScene
from .scenes.event_log_scene import EventLogScene
from .scenes.save_scene import SaveScene
from .scenes.npc_action_scene import NPCActionScene
from .scenes.settings_scene import SettingsScene
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
from .ui.assets import AssetManager
from .ui.fonts import FontRegistry
from .ui.theme import default_theme
from .ui.input import InputState
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
                # Reserve channel 0 for per-line voice and channels 1-2 for the
                # two-stream BGM crossfade, so auto-allocated SFX never steal
                # them; 8 total mixing channels is plenty for a VN.
                pygame.mixer.set_num_channels(8)
                pygame.mixer.set_reserved(3)
            except pygame.error:
                # Fine in CI / no-audio environments.
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
        # Apply pack title/subtitle to config so the title screen reflects them.
        if "title" in self.meta:
            self.config.title = self.meta["title"]
            if not headless:
                pygame.display.set_caption(self.meta["title"])
        if "subtitle" in self.meta:
            self.config.subtitle = self.meta["subtitle"]
        if "text_speed" in self.meta:
            self.config.text_speed = float(self.meta["text_speed"])

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
        self.assets = AssetManager(pack_root=pack_root)
        # Push configured audio volumes into the asset manager so voice lines
        # play at the user's chosen level.
        self.assets._voice_volume = self.config.voice_volume
        # Theme + localization come from meta.yaml so packs can fully restyle.
        from .ui.theme import Theme as _Theme
        self.theme = _Theme.from_meta(self.meta)
        self.localization = Localization.from_meta(self.meta)
        # Bind localization globally so existing TimeOfDay / AffectionTracker
        # helpers honor pack overrides without explicit threading.
        bind_time_localization(self.localization)
        self.state.affection.bind_localization(self.localization)

        # ----- dialogue engine -----
        # No live LLM brain is wired up in this release; lines marked
        # `llm_speaker: true` will fall back to their `text:` field.
        self.dialogue = DialogueEngine(self.state, llm_provider=None)

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
        }

        # ----- scene context -----
        self.ctx = SceneContext(
            config=self.config, state=self.state, npcs=self.npcs,
            brain=self.brain, dialogue=self.dialogue, assets=self.assets,
            fonts=self.fonts, theme=self.theme,
            localization=self.localization,
            screen_size=self.config.screen_size,
        )
        self.manager = SceneManager()
        self.manager.replace(TitleScene(self.ctx),
                             title=self.config.title,
                             subtitle=self.config.subtitle,
                             version=self.meta.get("version"),
                             bg=self.meta.get("title_bg"),
                             bgm=self.meta.get("title_bgm"),
                             on_continue=(self._continue_game
                                          if self._has_saves() else None),
                             on_new_game=self._start_new_game,
                             on_load=self._open_load_menu,
                             on_quit=self._quit_app,
                             on_cg_gallery=self._open_cg_gallery,
                             on_music_room=self._open_music_room,
                             on_endings=self._open_endings,
                             on_settings=self._open_settings)
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

    def _start_new_game(self) -> None:
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
        )

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
            on_character_profiles=from_menu(self._open_character_profiles),
            on_quest_log=from_menu(self._open_quest_log),
            on_clues=from_menu(self._open_clues),
            on_save=from_menu(self._open_save_menu),
            on_load=from_menu(self._open_load_menu),
            on_settings=from_menu(self._open_settings),
            on_quit_to_title=from_menu(self._quit_to_title),
            on_quit_app=self._quit_app,
        )

    def _open_quest_log(self) -> None:
        self.manager.push(QuestLogScene(self.ctx),
                          on_close=self.manager.pop)

    def _open_clues(self) -> None:
        self.manager.push(CluesScene(self.ctx),
                          on_close=self.manager.pop)

    def _open_settings(self) -> None:
        self.manager.push(SettingsScene(self.ctx),
                          on_close=self.manager.pop)

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
        """Open the chapter flowchart (チャート). Clicking a read chapter closes
        the chart and jumps to (replays) that scene."""
        def jump(scene_id: str) -> None:
            self.manager.pop()           # close the flowchart overlay
            self._start_dialogue(scene_id)
        self.manager.push(FlowchartScene(self.ctx),
                          on_close=self.manager.pop, on_jump=jump)

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

    def _quit_to_title(self) -> None:
        self.manager.clear_to(TitleScene(self.ctx),
                              title=self.config.title,
                              subtitle=self.config.subtitle,
                              version=self.meta.get("version"),
                              bg=self.meta.get("title_bg"),
                              bgm=self.meta.get("title_bgm"),
                              on_continue=(self._continue_game
                                           if self._has_saves() else None),
                              on_new_game=self._start_new_game,
                              on_load=self._open_load_menu,
                              on_quit=self._quit_app,
                              on_cg_gallery=self._open_cg_gallery,
                              on_music_room=self._open_music_room,
                              on_endings=self._open_endings,
                              on_settings=self._open_settings)

    def _open_map(self) -> None:
        # The card-based destination picker is the primary travel surface.
        # The node-graph map is reachable from it as a "世界地圖" overview.
        self.manager.push(DestinationPickerScene(self.ctx),
                          on_close=self.manager.pop,
                          on_move_to=self._move_from_map,
                          on_world_map=self._open_world_map)

    def _open_world_map(self) -> None:
        # Swap the picker for the node graph at the same overlay level, so
        # closing the map returns straight to exploration (not back to the
        # picker) and a move pops exactly one overlay before travelling.
        self.manager.replace(MapScene(self.ctx),
                             on_close=self.manager.pop,
                             on_move_to=self._move_from_map)

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
                          get_screen_surface=lambda: self.screen)

    def _open_load_menu(self) -> None:
        # From title or in-game both go to load mode.
        self.manager.push(SaveScene(self.ctx),
                          mode="load", on_close=self._after_load_pop)

    def _after_load_pop(self) -> None:
        """Called when the load overlay self-closes after loading.

        Replace the title with exploration (if a save was actually loaded
        — the GameState now has a current location).
        """
        self.manager.pop()
        if self.state.map.current_location_id:
            self.manager.replace(ExplorationScene(self.ctx),
                                 **self._exploration_callbacks())

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
            label = f"{self.localization.t('quicksave', '快速存檔')} {summary}"
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
            for key in ("_saved_at", "_label", "_summary",
                        "_schema_version", "_thumbnail_path",
                        "_pack_id", "_pack_format_version", "_engine_version"):
                data.pop(key, None)
            new_state = GameState(**data)
            # Preserve transient `__`-bridges (plugin manager, npc registry,
            # autosave config, pack meta) that were filtered out at save.
            preserved = {
                k: v for k, v in self.state.meta.items()
                if k.startswith("__")
            }
            self.state.__dict__.update(new_state.__dict__)
            self.state.meta.update(preserved)
            from .plugins import fire_event
            from .plugins.context import HookEvent
            fire_event(self.state, HookEvent.SAVE_AFTER_LOAD,
                       slot=slot, payload=data)
            # Restore into exploration if the loaded state has a location
            # (mirrors _after_load_pop). clear_to drops any open overlays.
            if self.state.map.current_location_id:
                self.manager.clear_to(ExplorationScene(self.ctx),
                                      **self._exploration_callbacks())
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
        # Wrap on_done so that returning from any dialogue re-checks for
        # enter/auto hooks at the current location. Reason: a dialogue's
        # on_end / choice effects can advance time, set flags, or otherwise
        # newly satisfy an ambient hook (e.g. setting met_qingyi while at
        # library) — the player should not have to leave and come back for
        # it to fire.
        def _after_dialogue() -> None:
            self.manager.pop()
            self._check_ambient_hooks()
        self.manager.push(DialogueScene(self.ctx),
                          scene_id=scene_id, on_done=_after_dialogue,
                          on_scrollback=self._open_scrollback,
                          on_save=self._open_save_menu,
                          on_load=self._open_load_menu,
                          on_config=self._open_settings,
                          on_menu=self._open_menu,
                          on_qsave=self._quicksave,
                          on_qload=self._quickload,
                          on_movie=self._open_movie)

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

    def _move_to(self, loc_id: str) -> None:
        flags = self.state.events.flags
        if not self.state.map.can_move_to(
            loc_id, flags, time_of_day=self.state.time.time_of_day.value
        ):
            return
        # Look up the exit (from current to target) BEFORE moving so we
        # can read its travel_cost. Local moves cost 0 phases; long trips
        # opt in via Exit.travel_cost.
        cur = self.state.map.current
        cost = 0
        if cur is not None:
            ex = next((e for e in cur.exits if e.target == loc_id), None)
            if ex is not None:
                cost = ex.travel_cost
        loc = self.state.map.move_to(loc_id)
        if cost > 0:
            self.state.time.advance(cost)
        self.state.events.record(kind="location",
                                 title=f"前往 {loc.name}",
                                 location=loc.id)
        self._check_ambient_hooks()

    def _move_from_map(self, loc_id: str) -> None:
        # Pop the map overlay, then move.
        self.manager.pop()
        self._move_to(loc_id)

    def _advance_time(self) -> None:
        self.state.time.advance(1)
        self.state.events.record(kind="system",
                                 title=f"時間流動到 {self.state.time.label()}")
        self._check_ambient_hooks()

    def _quit_app(self) -> None:
        self._running = False

    # ----------- main loop ----------------------------------------------

    def run(self) -> None:
        """Synchronous main loop (desktop / PyInstaller)."""
        while self._running:
            dt = self.clock.tick(self.config.fps) / 1000.0
            self._step(dt)
        self._shutdown_steam()
        pygame.quit()

    async def run_async(self) -> None:
        """Async main loop for the web (pygbag/Emscripten) target.

        Identical to :meth:`run` but yields to the browser event loop once
        per frame via ``await asyncio.sleep(0)`` — without this the WASM tab
        would freeze. Desktop never uses this path, so its behavior is
        unchanged.
        """
        import asyncio
        while self._running:
            dt = self.clock.tick(self.config.fps) / 1000.0
            self._step(dt)
            await asyncio.sleep(0)
        self._shutdown_steam()
        pygame.quit()

    def _step(self, dt: float) -> None:
        """Run exactly one frame: input -> update -> draw -> present.

        Contains no blocking calls, so it is safe under both the sync and
        async drivers. ``dt`` is clamped to absorb the huge spikes that
        happen after a stall (e.g. a backgrounded browser tab).
        """
        dt = min(dt, 0.1)
        events = pygame.event.get()
        win_size = self.display.get_size() if self.display is not None else None
        inp = InputState.collect(
            events, transform=self._window_to_logical, window_size=win_size,
        )
        if inp.quit_requested:
            self._running = False
        # Window resize -> recompute the letterbox view transform.
        for e in events:
            if e.type == pygame.VIDEORESIZE and self.display is not None:
                self._on_resize(e.w, e.h)
        # Touch swipe (spans frames) -> populate inp.swipe.
        self._update_touch_gesture(events, inp)
        # F12 screenshot / F11 state dump / F6 quicksave / F9 quickload.
        # F6 + F9 are chosen so they never collide with the dev F5
        # hot-reload (handled below) or F11/F12 above.
        for e in events:
            if e.type == pygame.KEYDOWN and e.key == pygame.K_F12:
                self.take_screenshot()
            elif e.type == pygame.KEYDOWN and e.key == pygame.K_F11:
                self.dump_state(verbose=True)
            elif e.type == pygame.KEYDOWN and e.key == pygame.K_F6:
                self._quicksave()
            elif e.type == pygame.KEYDOWN and e.key == pygame.K_F9:
                self._quickload()
        # Dev-mode key handling (F1 overlay toggle, F5 hot reload)
        if self._dev_mode:
            for e in inp.events:
                if e.type == pygame.KEYDOWN:
                    if e.key == pygame.K_F1 and self._debug_overlay:
                        self._debug_overlay.toggle()
                    elif e.key == pygame.K_F5 and self._hot_reloader:
                        self._do_hot_reload()
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
                self._debug_overlay.set_state(self.state)
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
            self.display.fill((0, 0, 0))
            scaled = pygame.transform.smoothscale(self.screen, self._view_size)
            self.display.blit(scaled, self._view_offset)
        pygame.display.flip()

    def _update_touch_gesture(self, events, inp) -> None:
        """Classify a touch drag into inp.swipe ('left'/'right').

        FINGERDOWN / FINGERUP can land on different frames, so the start
        point is tracked on the app. Coordinates are normalized [0,1].
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
        self._running = False

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
                "weekday_label": self.state.time.day_of_week.label,
                "time_of_day": self.state.time.time_of_day.value,
                "time_label": self.state.time.time_of_day.label,
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
            self.state = new_state
            self.npcs = new_npcs
            self.meta = new_meta
            # Propagate to scene context so scenes read updated data.
            self.ctx.state = new_state
            self.ctx.npcs = new_npcs
            print("[dev] hot reload OK", file=sys.stderr)
        except Exception as exc:
            print(f"[dev] hot reload failed: {exc}", file=sys.stderr)

    def _do_inspect_dump(self) -> None:
        self._last_inspect = self.inspect()

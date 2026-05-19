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
from .scenes.affection_scene import AffectionScene
from .scenes.event_log_scene import EventLogScene
from .scenes.save_scene import SaveScene
from .scenes.npc_action_scene import NPCActionScene
from .scenes.settings_scene import SettingsScene
from .scenes.achievements_scene import AchievementsScene
from .scenes.inventory_scene import InventoryScene
from .scenes.scrollback_scene import ScrollbackScene
from .scenes.menu_scene import MenuScene
from .scenes.quest_log_scene import QuestLogScene
from .scenes.clues_scene import CluesScene
from .scenes.shop_scene import ShopScene
from .ui.assets import AssetManager
from .ui.fonts import FontRegistry
from .ui.theme import default_theme
from .ui.input import InputState
from .ui.widgets.toast import Toast, ToastStack


class GalGameApp:
    def __init__(self, *, config: EngineConfig, pack: str | None = None,
                 brain: LLMBrain | None = None, headless: bool = False):
        self.config = config
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
            except pygame.error:
                # Fine in CI / no-audio environments.
                pass
        flags = 0
        if config.fullscreen and not headless:
            flags |= pygame.FULLSCREEN
        if headless:
            # tiny surface — no actual window
            self.screen = pygame.Surface(config.screen_size)
        else:
            self.screen = pygame.display.set_mode(
                config.screen_size, flags, vsync=1 if config.vsync else 0,
            )
            pygame.display.set_caption(config.title)
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
        self.fonts = FontRegistry(self.config.font_candidates,
                                  bundled=bundled_font_path)
        self.assets = AssetManager(pack_root=pack_root)
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
                             bg=self.meta.get("title_bg"),
                             on_new_game=self._start_new_game,
                             on_load=self._open_load_menu,
                             on_quit=self._quit_app)
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
            return
        # No intro — still check whether the starting location has an
        # auto/enter hook eligible right now.
        self._check_ambient_hooks()

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
            on_quest_log=from_menu(self._open_quest_log),
            on_clues=from_menu(self._open_clues),
            on_save=from_menu(self._open_save_menu),
            on_load=from_menu(self._open_load_menu),
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
                              bg=self.meta.get("title_bg"),
                              on_new_game=self._start_new_game,
                              on_load=self._open_load_menu,
                              on_quit=self._quit_app)

    def _open_map(self) -> None:
        self.manager.push(MapScene(self.ctx),
                          on_close=self.manager.pop,
                          on_move_to=self._move_from_map)

    def _open_affection(self) -> None:
        self.manager.push(AffectionScene(self.ctx),
                          on_close=self.manager.pop)

    def _open_event_log(self) -> None:
        self.manager.push(EventLogScene(self.ctx),
                          on_close=self.manager.pop)

    def _open_save_menu(self) -> None:
        self.manager.push(SaveScene(self.ctx),
                          mode="save", on_close=self.manager.pop)

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
                          on_scrollback=self._open_scrollback)

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
        ):
            if hook.trigger in ("enter", "auto"):
                self._start_dialogue(hook.scene_id)
                return True
        return False

    def _move_to(self, loc_id: str) -> None:
        flags = self.state.events.flags
        if not self.state.map.can_move_to(loc_id, flags):
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
        while self._running:
            dt = self.clock.tick(self.config.fps) / 1000.0
            events = pygame.event.get()
            inp = InputState.collect(events)
            if inp.quit_requested:
                self._running = False
            # F12 screenshot
            for e in events:
                if e.type == pygame.KEYDOWN and e.key == pygame.K_F12:
                    self.take_screenshot()
                elif e.type == pygame.KEYDOWN and e.key == pygame.K_F11:
                    self.dump_state(verbose=True)
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
            if not self.headless:
                pygame.display.flip()

        pygame.quit()

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

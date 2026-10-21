"""AI-friendly game driver.

Lets an external agent (or test) boot the real pygame App headlessly,
inject synthetic mouse / keyboard events, advance frames, snapshot the
screen, and inspect game state — all without a window.

Goal: make it cheap to verify "did clicking this exit button actually
move the player?" or "did Space skip the typewriter?" programmatically,
so AI tooling can debug gameplay bugs without hand-driven testing.

Usage:

    from world_gal_game.dev.driver import GameDriver

    d = GameDriver(pack="my_pack")
    d.new_game()
    d.advance_frames(60)              # let prologue start
    d.press_space(count=20)           # blast through dialogue
    d.advance_frames(30)

    print(d.snapshot())               # JSON-able state dump
    d.screenshot("dist/01.png")

    # Find a clickable widget by label substring and click it
    btn = d.find_widget(label="校門口")
    d.click(btn["rect_center"])
    d.advance_frames(10)
    print(d.snapshot()["location"])   # changed?
"""
from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pygame


def _ensure_headless_env() -> None:
    os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
    os.environ.setdefault("SDL_AUDIODRIVER", "dummy")


@dataclass
class PendingInput:
    """Events to feed into the next advance_frames() call."""
    events: list[pygame.event.Event] = field(default_factory=list)
    mouse_pos: tuple[int, int] | None = None

    def clear(self) -> None:
        self.events = []
        # mouse_pos persists; SDL behaves the same way.


class GameDriver:
    """Drive a real pygame App in headless mode for AI inspection."""

    def __init__(
        self,
        pack: str | None = None,
        screen_size: tuple[int, int] = (1280, 720),
        dev_mode: bool = False,
        frame_dt: float = 1 / 60,
        seed: int | None = None,
        *,
        isolated_runtime: bool = False,
    ):
        _ensure_headless_env()
        if dev_mode:
            os.environ.setdefault("WGG_DEV", "1")
        self._writable_sandbox: tempfile.TemporaryDirectory[str] | None = None
        self._isolate_user_plugins = isolated_runtime
        self._previous_user_plugin_root = os.environ.get(
            "WGG_USER_PLUGIN_ROOT",
        )
        writable_override = None
        if isolated_runtime:
            self._writable_sandbox = tempfile.TemporaryDirectory(
                prefix="wgg-game-driver-",
            )
            writable_override = Path(self._writable_sandbox.name)
            os.environ["WGG_USER_PLUGIN_ROOT"] = str(
                writable_override / "user-plugins",
            )
        try:
            # Import inside __init__ so pygame env vars are honored.  Keep the
            # complete construction path inside the cleanup boundary: a bad
            # pack, config validation error, or first-frame renderer failure
            # must not strand the temporary player root or plugin override.
            from ..config import EngineConfig
            from ..app import GalGameApp
            cfg = EngineConfig(
                screen_size=screen_size,
                seed=seed,
                writable_root_override=writable_override,
            )
            if pack is not None:
                cfg.default_pack = pack
            self.cfg = cfg
            self.app = GalGameApp(config=cfg, headless=True)
            self.frame_dt = frame_dt
            self._pending = PendingInput(mouse_pos=(0, 0))
            # Force-draw once so widgets get laid out.
            self.advance_frames(1)
        except BaseException:
            self._cleanup_runtime_isolation()
            raise

    # ---------- frame stepping -----------------------------------------

    def advance_frames(self, count: int = 1) -> None:
        for _ in range(count):
            events = list(self._pending.events)
            self._pending.clear()
            inp = self.app._input_router.collect(
                events,
                dt=self.frame_dt,
                touch_mode=self.app.config.touch_mode,
                binding_overrides=getattr(
                    self.app.config,
                    "input_bindings",
                    {},
                ),
            )
            inp.mouse_pos = self._pending.mouse_pos or (0, 0)
            self.app.manager.update(self.frame_dt, inp)
            self.app.manager.draw(self.app.screen)

    # ---------- input injection ----------------------------------------

    def click(self, pos: tuple[int, int], *, button: int = 1) -> None:
        """Schedule a left-click at (x, y) for the next advance."""
        self._pending.mouse_pos = pos
        self._pending.events.append(
            pygame.event.Event(pygame.MOUSEBUTTONDOWN, button=button, pos=pos)
        )
        self._pending.events.append(
            pygame.event.Event(pygame.MOUSEBUTTONUP, button=button, pos=pos)
        )

    def move_mouse(self, pos: tuple[int, int]) -> None:
        self._pending.mouse_pos = pos

    def press_key(self, key: int) -> None:
        self._pending.events.append(
            pygame.event.Event(pygame.KEYDOWN, key=key, mod=0, unicode="")
        )
        self._pending.events.append(
            pygame.event.Event(pygame.KEYUP, key=key, mod=0)
        )

    def press_action(self, action: str) -> bool:
        """Schedule the first keyboard binding for a semantic action.

        Returns ``False`` when an optional action is intentionally unbound.
        This keeps high-level driver workflows faithful to the player's live
        control profile instead of silently relying on factory-default keys.
        """
        from ..ui.input_bindings import BindingProfile

        profile = BindingProfile(
            getattr(self.app.config, "input_bindings", {})
        )
        keys = profile.keys_for(action)
        if not keys:
            return False
        self.press_key(keys[0])
        return True

    def press_space(self, count: int = 1, frames_between: int = 8) -> None:
        for _ in range(count):
            self.press_key(pygame.K_SPACE)
            self.advance_frames(frames_between)

    def press_escape(self) -> None:
        self.press_key(pygame.K_ESCAPE)

    # ---------- high-level actions -------------------------------------

    def new_game(self) -> None:
        """Click "新遊戲" from title; then settle into exploration."""
        # Title scene listens for confirm key (Enter/Space/Z), which picks
        # the focused menu entry. New Game is the default first entry.
        self.advance_frames(2)
        # Bypass the menu by calling app's wiring directly — robust and
        # avoids fighting the title-screen menu_list selection state.
        self.app._start_new_game()
        self.app.manager.commit_pending()
        self.advance_frames(2)

    def skip_dialogue(self, max_frames: int = 600) -> None:
        """Advance dialogue until exploration or a player decision is reached.

        The driver must never turn a presentation helper into an implicit
        narrative choice.  In particular, a single enabled choice may be
        focused for normal keyboard/controller usability, but ``skip_dialogue``
        still stops before activating it.
        """
        from ..scenes.dialogue_scene import DialogueScene
        from ..scenes.exploration import ExplorationScene
        from ..ui.input_bindings import InputAction
        for _ in range(max_frames // 4):
            cur = self.app.manager.current
            if isinstance(cur, ExplorationScene):
                return
            if (
                isinstance(cur, DialogueScene)
                and cur.choices is not None
                and cur.choices.visible
            ):
                return
            if not self.press_action(InputAction.CONFIRM_ADVANCE):
                return
            self.advance_frames(4)

    def travel_to(self, label: str, *, settle: int = 12) -> bool:
        """Travel through the spatial map and its confirmation sheet.

        Mirrors the human flow: open the map (M), select the authored marker,
        then confirm 前往 in the existing destination detail sheet. Returns
        True when the move was issued (False when the marker/confirmation is
        unavailable).
        """
        from ..scenes.exploration import ExplorationScene
        from ..scenes.destination_picker import DestinationPickerScene
        from ..scenes.map_scene import MapScene
        from ..ui.input_bindings import InputAction
        if isinstance(self.app.manager.current, ExplorationScene):
            if not self.press_action(InputAction.OPEN_MAP):
                return False
            self.advance_frames(4)
        if isinstance(self.app.manager.current, MapScene):
            target_id = next(
                (
                    location.id
                    for location in self.app.state.map.locations.values()
                    if location.name == label
                ),
                None,
            )
            marker = self.find_widget(label=label)
            if marker is None:
                toggle = self.find_widget(label="顯示一般探索")
                if toggle is not None:
                    self.click(tuple(toggle["rect_center"]))
                    self.advance_frames(2)
                    marker = self.find_widget(label=label)
            world = self.app.manager.current
            marker_target = (
                marker.get("target_id") if marker is not None else None
            )
            target_id = marker_target or target_id
            # A spatial-map node may use a concise authored label while the
            # caller supplies the location's formal name. Resolve that name
            # through the current catalog, then use the same marker callback;
            # never fall back to mutating travel directly.
            if (
                target_id
                and world.map_view is not None
                and target_id in world.map_view._node_rects
            ):
                world.map_view._focused_id = target_id
                world._on_node_click(target_id)
                self.advance_frames(3)
            elif marker is not None:
                self.click(tuple(marker["rect_center"]))
                self.advance_frames(3)
            else:
                return False
        if not isinstance(self.app.manager.current, DestinationPickerScene):
            return False
        if self.app.manager.current.describe().get("selected") is None:
            card = self.find_widget(label=label)
            if card is None:
                return False
            self.click(tuple(card["rect_center"]))
            self.advance_frames(3)
        go = self.find_widget(label="前往")
        if go is None:
            return False
        self.click(tuple(go["rect_center"]))
        self.advance_frames(settle)
        return True

    # ---------- inspection ---------------------------------------------

    def snapshot(self) -> dict[str, Any]:
        """Return a JSON-able snapshot of the app's state + UI layout."""
        st = self.app.state
        out: dict[str, Any] = {
            "pack": self.app.pack,
            "scene_stack": [s.describe() for s in self.app.manager.stack()],
            "scene_top": (type(self.app.manager.current).__name__
                          if self.app.manager.current else None),
            "location": st.map.current_location_id,
            "location_name": (st.map.current.name
                              if st.map.current else None),
            "time": st.time.label(),
            "time_of_day": st.time.time_of_day.value,
            "player_name": st.player.name,
            "flags": dict(st.events.flags),
            "affection": {
                aid: st.affection.get(aid)
                for aid in st.affection.characters.keys()
            },
            "resources": {
                r["id"]: r["value"]
                for r in st.resources.visible_snapshot()
            },
            "inventory": dict(st.inventory.list_owned()),
            "achievements_unlocked": list(st.achievements.unlocked),
            "quests_active": [q.id for q in st.quests.active()],
            "quests_completed": [q.id for q in st.quests.completed()],
            "current_scene_id": st.story.current_scene,
            "current_line_index": st.story.current_line_index,
            "asset_cache": self.app.assets.cache_stats(),
        }
        out["widgets"] = self._widget_catalogue()
        return out

    def _widget_catalogue(self) -> list[dict]:
        """Enumerate buttons / clickable rects on the current top scene."""
        scene = self.app.manager.current
        if scene is None:
            return []
        cat: list[dict] = []

        # Buttons are usually exposed as attributes; collect by name.
        for attr_name in dir(scene):
            if attr_name.startswith("_"):
                # private attrs sometimes hold lists of buttons
                pass
            try:
                attr = getattr(scene, attr_name)
            except Exception:
                continue
            cat.extend(self._inspect_attr(attr_name, attr))

        # De-dupe by id(widget) so widgets reachable via two paths
        # only show up once.
        seen = set()
        deduped = []
        for entry in cat:
            key = entry["_id"]
            if key in seen:
                continue
            seen.add(key)
            deduped.append({k: v for k, v in entry.items() if k != "_id"})
        return deduped

    def _inspect_attr(self, name: str, attr: Any) -> list[dict]:
        from ..ui.widgets.button import Button
        from ..ui.widgets.choice_menu import ChoiceMenu
        from ..ui.widgets.map_view import MapView
        from ..ui.widgets.quick_menu import QuickMenuBar
        out: list[dict] = []
        if isinstance(attr, Button):
            out.append(self._button_entry(name, attr))
        elif isinstance(attr, ChoiceMenu):
            if not attr.visible:
                return out
            for i, button in enumerate(attr.buttons):
                if not button.visible:
                    continue
                out.append(self._button_entry(
                    f"{name}.buttons[{i}]",
                    button,
                ))
        elif isinstance(attr, QuickMenuBar):
            for i, button in enumerate(attr.visible_buttons()):
                out.append(self._button_entry(
                    f"{name}.visible_buttons[{i}]",
                    button,
                ))
        elif isinstance(attr, MapView):
            for node in attr.nodes:
                node_id = node["id"]
                rect = attr._node_rects.get(node_id)
                if rect is None:
                    continue
                out.append({
                    "_id": ("map_node", id(attr), node_id),
                    "path": f"{name}.nodes[{node_id}]",
                    "label": node.get("name"),
                    "enabled": node_id != attr.current_id,
                    "visible": attr.visible,
                    "has_on_click": attr.on_click is not None,
                    "rect": [rect.x, rect.y, rect.width, rect.height],
                    "rect_center": [rect.centerx, rect.centery],
                    "style": node.get("marker_kind"),
                    "tooltip": node.get("description"),
                    "target_id": node_id,
                })
        elif isinstance(attr, (list, tuple)):
            for i, item in enumerate(attr):
                if isinstance(item, Button):
                    out.append(self._button_entry(f"{name}[{i}]", item))
                elif isinstance(item, tuple):
                    # (rect, id) — NPC card pattern, or
                    # (button, desc, available) — exit pattern
                    for sub_i, sub in enumerate(item):
                        if isinstance(sub, Button):
                            out.append(self._button_entry(
                                f"{name}[{i}][{sub_i}]", sub,
                                extra={"sibling": [
                                    (repr(x)[:60] if not isinstance(x, Button) else "<button>")
                                    for x in item
                                ]},
                            ))
        return out

    def _button_entry(self, path: str, btn, extra: dict | None = None) -> dict:
        rect = btn.rect
        info = {
            "_id": id(btn),
            "path": path,
            "label": getattr(btn, "label", None),
            "enabled": getattr(btn, "enabled", True),
            "visible": getattr(btn, "visible", True),
            "has_on_click": btn.on_click is not None,
            "rect": [rect.x, rect.y, rect.width, rect.height],
            "rect_center": [rect.centerx, rect.centery],
            "style": getattr(btn, "style", None),
            "tooltip": getattr(btn, "tooltip", None),
        }
        if extra:
            info.update(extra)
        return info

    def find_widget(self, *, label: str | None = None,
                    path: str | None = None,
                    enabled_only: bool = True,
                    visible_only: bool = True) -> dict | None:
        """Find a button matching label substring / path substring.

        The exploration UI hides travel targets behind the spatial map. For
        headless tests/debug scripts that ask for a visible
        destination by label (for example ``校門口`` while standing in the
        dorm), transparently open the map and search once more. Direct
        action buttons still match on the first pass and are unchanged.
        """
        def label_matches(w_label: str | None) -> bool:
            if label is None:
                return True
            if w_label is None:
                return False
            if label in w_label:
                return True
            # Destination cards show location names, while old exit buttons may
            # have used author-provided exit labels.  Accept either form so
            # legacy tests/scripts remain stable after the destination-picker UI.
            st = self.app.state
            for ex in st.map.current.exits if st.map.current else []:
                target = ex.target
                loc = st.map.locations.get(target)
                if ex.label and label in ex.label and loc and loc.name == w_label:
                    return True
            return False

        def search() -> dict | None:
            for w in self._widget_catalogue():
                if enabled_only and not w.get("enabled", True):
                    continue
                if visible_only and not w.get("visible", True):
                    continue
                if not label_matches(w.get("label")):
                    continue
                if path is not None and path not in w["path"]:
                    continue
                return w
            return None

        found = search()
        if found is not None:
            return found
        if label is None or path is not None:
            return found

        from ..scenes.exploration import ExplorationScene
        from ..scenes.destination_picker import DestinationPickerScene
        from ..scenes.map_scene import MapScene
        if isinstance(self.app.manager.current, ExplorationScene):
            # Search the visible travel button without recursing into the
            # destination-picker fallback again.
            travel = None
            for w in self._widget_catalogue():
                if enabled_only and not w.get("enabled", True):
                    continue
                if visible_only and not w.get("visible", True):
                    continue
                if w.get("label") and "外出" in w["label"]:
                    travel = w
                    break
            if travel is not None:
                self.click(tuple(travel["rect_center"]))
                self.advance_frames(4)
                if isinstance(self.app.manager.current, MapScene):
                    marker = search()
                    if marker is None:
                        toggle = next(
                            (
                                item for item in self._widget_catalogue()
                                if item.get("label") == "顯示一般探索"
                                and item.get("visible", True)
                            ),
                            None,
                        )
                        if toggle is not None:
                            self.click(tuple(toggle["rect_center"]))
                            self.advance_frames(2)
                            marker = search()
                    return marker
                if isinstance(self.app.manager.current, DestinationPickerScene):
                    card = search()
                    if card is None:
                        return None
                    card_index = int(card["path"].split("[")[1].split("]")[0])
                    dest, _button = self.app.manager.current._cards[card_index]
                    self.app.manager.current._open_preview(dest.loc.id)
                    self.advance_frames(1)
                    # Return the confirmation button.  The caller's click then
                    # completes the human flow for this destination label.
                    for w in self._widget_catalogue():
                        if enabled_only and not w.get("enabled", True):
                            continue
                        if visible_only and not w.get("visible", True):
                            continue
                        if w.get("label") and "前往" in w["label"]:
                            return w
        return None

    def find_widgets(self, *, label: str | None = None,
                     path: str | None = None) -> list[dict]:
        out = []
        for w in self._widget_catalogue():
            if label is not None and (
                w.get("label") is None or label not in w["label"]
            ):
                continue
            if path is not None and path not in w["path"]:
                continue
            out.append(w)
        return out

    # ---------- output --------------------------------------------------

    def screenshot(self, path: str | Path) -> Path:
        p = Path(path).expanduser().resolve()
        p.parent.mkdir(parents=True, exist_ok=True)
        # Re-draw to ensure current state is on the surface.
        self.app.manager.draw(self.app.screen)
        pygame.image.save(self.app.screen, str(p))
        return p

    def dump_snapshot(self, path: str | Path) -> Path:
        p = Path(path).expanduser().resolve()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(self.snapshot(), indent=2, ensure_ascii=False))
        return p

    def quit(self) -> None:
        try:
            pygame.quit()
        except Exception:
            pass
        finally:
            self._cleanup_runtime_isolation()

    def _cleanup_runtime_isolation(self) -> None:
        if self._isolate_user_plugins:
            if self._previous_user_plugin_root is None:
                os.environ.pop("WGG_USER_PLUGIN_ROOT", None)
            else:
                os.environ["WGG_USER_PLUGIN_ROOT"] = (
                    self._previous_user_plugin_root
                )
            self._isolate_user_plugins = False
        if self._writable_sandbox is not None:
            self._writable_sandbox.cleanup()
            self._writable_sandbox = None


# ---------- CLI ------------------------------------------------------------


def _cli_main(argv: list[str] | None = None) -> int:
    """``world-gal-game debug <script.json>`` driver.

    Script format:
    {
      "pack": "demo_pack",
      "screen_size": [1280, 720],
      "actions": [
        {"do": "new_game"},
        {"do": "frames", "n": 60},
        {"do": "space", "count": 20},
        {"do": "screenshot", "path": "step1.png"},
        {"do": "snapshot", "path": "step1.json"},
        {"do": "click_label", "label": "校門口"},
        {"do": "frames", "n": 10},
        {"do": "screenshot", "path": "step2.png"},
        {"do": "snapshot", "path": "step2.json"},
      ]
    }
    """
    import argparse
    import sys
    p = argparse.ArgumentParser(prog="world-gal-game debug",
                                description="Drive a game pack headlessly "
                                            "for AI-assisted debugging.")
    p.add_argument("script", nargs="?", type=Path,
                   help="JSON file with an actions list. Omit for stdin.")
    p.add_argument("--out-dir", type=Path, default=Path("debug_out"),
                   help="Where screenshots/snapshots are written by default.")
    p.add_argument("--pack", default=None, help="Override pack from script.")
    args = p.parse_args(argv)

    if args.script:
        spec = json.loads(args.script.read_text())
    else:
        spec = json.loads(sys.stdin.read())
    pack = args.pack or spec.get("pack")
    screen_size = tuple(spec.get("screen_size", [1280, 720]))
    out_dir = args.out_dir.resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    drv = GameDriver(pack=pack, screen_size=screen_size)
    report: list[dict] = []
    actions = spec.get("actions", [])

    def resolve_path(p_str: str) -> Path:
        p = Path(p_str)
        if not p.is_absolute():
            p = out_dir / p
        return p

    for i, action in enumerate(actions):
        op = action.get("do")
        result: dict[str, Any] = {"step": i, "do": op}
        try:
            if op == "new_game":
                drv.new_game()
            elif op == "skip_dialogue":
                drv.skip_dialogue(action.get("max_frames", 600))
            elif op == "frames":
                drv.advance_frames(int(action.get("n", 1)))
            elif op == "space":
                drv.press_space(count=int(action.get("count", 1)),
                                frames_between=int(action.get("between", 8)))
            elif op == "key":
                key_name = action["key"]
                key_const = getattr(pygame, f"K_{key_name.lower()}", None)
                if key_const is None:
                    result["error"] = f"unknown key: {key_name}"
                else:
                    drv.press_key(key_const)
                    drv.advance_frames(int(action.get("after", 4)))
            elif op == "click":
                pos = tuple(action["at"])
                drv.click(pos)
                drv.advance_frames(int(action.get("after", 4)))
            elif op == "click_label":
                w = drv.find_widget(label=action["label"])
                if w is None:
                    result["error"] = f"no widget with label substring " \
                                       f"{action['label']!r}"
                else:
                    drv.click(tuple(w["rect_center"]))
                    drv.advance_frames(int(action.get("after", 4)))
                    result["clicked"] = w["label"]
            elif op == "travel_to":
                ok = drv.travel_to(action["label"],
                                   settle=int(action.get("after", 12)))
                result["traveled"] = ok
                if not ok:
                    result["error"] = f"could not travel to {action['label']!r}"
            elif op == "set_flag":
                drv.app.state.events.set_flag(action["key"],
                                              action.get("value", True))
            elif op == "screenshot":
                p = resolve_path(action.get("path", f"step_{i:03d}.png"))
                drv.screenshot(p)
                result["screenshot"] = str(p)
            elif op == "snapshot":
                if "path" in action:
                    p = resolve_path(action["path"])
                    drv.dump_snapshot(p)
                    result["snapshot_file"] = str(p)
                result["snapshot"] = drv.snapshot()
            elif op == "find":
                result["matches"] = drv.find_widgets(
                    label=action.get("label"),
                    path=action.get("path"),
                )
            else:
                result["error"] = f"unknown op: {op}"
        except Exception as exc:
            result["error"] = f"{type(exc).__name__}: {exc}"
        report.append(result)

    # Always emit a summary at the end.
    summary_path = out_dir / "report.json"
    summary_path.write_text(json.dumps(report, indent=2, ensure_ascii=False))
    print(json.dumps(report, indent=2, ensure_ascii=False))
    drv.quit()
    return 0

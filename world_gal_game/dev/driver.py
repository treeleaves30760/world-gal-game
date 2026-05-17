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

    def __init__(self, pack: str | None = None,
                 screen_size: tuple[int, int] = (1280, 720),
                 dev_mode: bool = False,
                 frame_dt: float = 1 / 60):
        _ensure_headless_env()
        if dev_mode:
            os.environ.setdefault("WGG_DEV", "1")
        # Import inside __init__ so pygame env vars are honored.
        from ..config import EngineConfig
        from ..app import GalGameApp
        cfg = EngineConfig(screen_size=screen_size)
        if pack is not None:
            cfg.default_pack = pack
        self.cfg = cfg
        self.app = GalGameApp(config=cfg, headless=True)
        self.frame_dt = frame_dt
        self._pending = PendingInput(mouse_pos=(0, 0))
        # Force-draw once so widgets get laid out.
        self.advance_frames(1)

    # ---------- frame stepping -----------------------------------------

    def advance_frames(self, count: int = 1) -> None:
        from ..ui.input import InputState
        for _ in range(count):
            events = list(self._pending.events)
            self._pending.clear()
            inp = InputState(events=events)
            inp.mouse_pos = self._pending.mouse_pos or (0, 0)
            # Mirror what InputState.collect() does for the events we
            # injected (mouse_clicked / confirm / advance_dialogue /
            # cancel / wheel / quit). We can't call collect() directly
            # because it reads pygame.mouse.get_pos() which is not set
            # in dummy mode.
            for e in events:
                if e.type == pygame.QUIT:
                    inp.quit_requested = True
                elif e.type == pygame.MOUSEBUTTONDOWN:
                    if e.button == 1:
                        inp.mouse_clicked = True
                        inp.advance_dialogue = True
                    elif e.button == 3:
                        inp.mouse_rclicked = True
                    elif e.button == 4:
                        inp.mouse_wheel += 1
                    elif e.button == 5:
                        inp.mouse_wheel -= 1
                elif e.type == pygame.MOUSEWHEEL:
                    inp.mouse_wheel += e.y
                elif e.type == pygame.KEYDOWN:
                    inp.keys_down.add(e.key)
                    if e.key in (pygame.K_RETURN, pygame.K_KP_ENTER,
                                 pygame.K_SPACE, pygame.K_z):
                        inp.confirm = True
                        inp.advance_dialogue = True
                    if e.key in (pygame.K_ESCAPE, pygame.K_x):
                        inp.cancel = True
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
        """Hammer Space until exploration is on top of the stack."""
        from ..scenes.exploration import ExplorationScene
        for _ in range(max_frames // 4):
            cur = self.app.manager.current
            if isinstance(cur, ExplorationScene):
                return
            self.press_key(pygame.K_SPACE)
            self.advance_frames(4)

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
        out: list[dict] = []
        if isinstance(attr, Button):
            out.append(self._button_entry(name, attr))
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
        }
        if extra:
            info.update(extra)
        return info

    def find_widget(self, *, label: str | None = None,
                    path: str | None = None,
                    enabled_only: bool = True,
                    visible_only: bool = True) -> dict | None:
        """Find a button matching label substring / path substring."""
        for w in self._widget_catalogue():
            if enabled_only and not w.get("enabled", True):
                continue
            if visible_only and not w.get("visible", True):
                continue
            if label is not None and (
                w.get("label") is None or label not in w["label"]
            ):
                continue
            if path is not None and path not in w["path"]:
                continue
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


# ---------- CLI ------------------------------------------------------------


def _cli_main(argv: list[str] | None = None) -> int:
    """`wgg debug <script.json>` driver.

    Script format:
    {
      "pack": "tsing_hua_strange_tales",
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
    import argparse, sys
    p = argparse.ArgumentParser(prog="wgg debug",
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

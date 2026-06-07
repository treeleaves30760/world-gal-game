"""Headless driver: run the engine without a window for inspection / scripting.

This module is what makes the game *agent-friendly*: I (Claude) can run

    python main.py --headless --inspect

to dump the current game state as JSON, or

    python main.py --headless --script script.json

to execute a sequence of actions (move, start scene, choose, next, chat,
advance_time, ...) and dump the resulting state. It does NOT require any
display or audio device, and runs on macOS/Linux/Windows CI alike.

The headless driver bypasses the SceneManager and pokes the GameState
directly through HeadlessSession. This lets us validate game logic
without rendering.
"""
from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .config import EngineConfig
from .content_loader import load_pack
from .core.game_state import GameState
from .core.localization import Localization
from .core.time_system import bind_localization as bind_time_localization
from .dialogue.dialogue_engine import DialogueEngine, ScenePresentation
from .npc.llm_brain import default_brain, EchoBrain, LLMBrain
from .npc.npc_base import NPCRegistry


@dataclass
class HeadlessSession:
    """Pure-Python in-memory session of the game; no pygame needed."""

    config: EngineConfig
    state: GameState
    npcs: NPCRegistry
    brain: LLMBrain
    dialogue: DialogueEngine
    meta: dict
    last_presentation: dict | None = None
    transcript: list[dict] = field(default_factory=list)
    pack: str = ""
    # Named state snapshots for branch exploration (op: snapshot/restore).
    _snapshots: dict[str, dict] = field(default_factory=dict)
    # Execution-trace recorder; created lazily and active only during run_script.
    _trace: Any = None
    # ----- warm structural-edit loop (op: edit.* / begin / commit / rollback) -
    # A PackEditor in dry_run (staging) mode; created lazily, discarded on
    # commit/rollback so the next edit starts clean.
    _editor: Any = None
    # True between `begin` and `commit`/`rollback`: edit.* ops stage only.
    _in_tx: bool = False
    # World-model snapshot captured at `begin`, diffed at `commit`.
    _tx_baseline: dict | None = None
    # Cached post-edit world snapshot; the "before" of the next autocommit edit,
    # so a run of single edits costs one analysis each, not two.
    _world_baseline: dict | None = None

    @classmethod
    def open(cls, config: EngineConfig, *, pack: str | None = None,
             brain: LLMBrain | None = None) -> "HeadlessSession":
        pack = pack or config.default_pack
        content_root = config.pack_content(pack)
        if not content_root.exists():
            raise FileNotFoundError(f"Game pack not found: {content_root}")
        state, npcs, meta = load_pack(content_root)
        if "title" in meta:
            config.title = meta["title"]
        if "subtitle" in meta:
            config.subtitle = meta["subtitle"]
        if "text_speed" in meta:
            config.text_speed = float(meta["text_speed"])
        # Apply pack-level localization so inspect()/script outputs use
        # the pack's chosen labels, not the engine defaults.
        localization = Localization.from_meta(meta)
        bind_time_localization(localization)
        state.affection.bind_localization(localization)
        # Headless defaults to EchoBrain so deterministic scripts work.
        # No live LLM brain is wired up; llm_speaker lines fall back to
        # `line.text`.
        brain = brain or EchoBrain()
        dialogue = DialogueEngine(state, llm_provider=None)
        if config.seed is not None:
            # Thread the determinism seed onto the state so GameState.rng() is
            # reproducible for any plugin/brain that uses it.
            state.meta["__seed__"] = config.seed
        return cls(config=config, state=state, npcs=npcs, brain=brain,
                   dialogue=dialogue, meta=meta, pack=pack)

    # ----- inspect ------------------------------------------------------

    def inspect(self) -> dict:
        loc = self.state.map.current
        return {
            "pack": self.pack,
            "title": self.config.title,
            "subtitle": self.config.subtitle,
            "player": self.state.player.model_dump(),
            "time": {
                "day": self.state.time.day,
                "weekday": self.state.time.day_of_week.value,
                "weekday_label": self.state.time.day_of_week.label,
                "time_of_day": self.state.time.time_of_day.value,
                "time_label": self.state.time.time_of_day.label,
                "label": self.state.time.label(),
                "is_night": self.state.time.is_night(),
                "is_haunting": self.state.time.is_haunting_hour(),
            },
            "chapter": self._chapter_view(),
            "location": loc.id if loc else None,
            "location_name": loc.name if loc else None,
            "location_description": loc.description if loc else None,
            "exits": [e.id for e in self.state.map.available_exits(
                self.state.events.flags)],
            "all_locations": [
                {"id": l.id, "name": l.name, "region": l.region,
                 "visited": l.id in self.state.map.visited}
                for l in self.state.map.locations.values()
            ],
            "npcs_present": [
                {"id": n, "name": (self.npcs.get(n).name if self.npcs.get(n) else n)}
                for n in self.state.map.present_npcs(
                    self.state.time.time_of_day.value,
                    self.state.time.day_of_week.value,
                    self.state.events.flags,
                )
            ],
            "all_characters": [
                {"id": n.id, "name": n.name, "role": n.role,
                 "is_heroine": n.is_heroine,
                 "affection": self.state.affection.get(n.id),
                 "affection_label": self.state.affection.level_label(n.id)}
                for n in self.npcs.all()
            ],
            "scenes_available": [
                h.scene_id for h in self.state.map.available_scenes(
                    time_of_day=self.state.time.time_of_day.value,
                    flags=self.state.events.flags,
                    played_scenes=self.state.story.played,
                    state=self.state,
                )
            ],
            "scenes_played": sorted(self.state.story.played),
            "current_scene": self.state.story.current_scene,
            "current_line_index": self.state.story.current_line_index,
            "inventory": dict(self.state.inventory.counts),
            "achievements": {
                "unlocked": list(self.state.achievements.unlocked.keys()),
                "total": len(self.state.achievements.achievements),
            },
            "flags": dict(self.state.events.flags),
            "recent_events": [
                {"kind": e.kind, "title": e.title, "summary": e.summary,
                 "actors": e.actors, "location": e.location}
                for e in self.state.events.recent(20)
            ],
            "variables": self._variables_view(),
            "last_presentation": self.last_presentation,
        }

    def _variables_view(self) -> list[dict]:
        """Declared narrative-state variables joined with their live values.

        Reads the pack's :class:`VariableManifest` from the ``__variables__``
        meta bridge (empty if the pack declares none) and pairs each declared
        variable with its current flag value, so an agent sees the *schema* of
        the narrative state (key/type/default/description/category) alongside
        what it currently holds — not just an untyped flag dump.
        """
        manifest = self.state.meta.get("__variables__")
        variables = getattr(manifest, "variables", None)
        if not variables:
            return []
        flags = self.state.events.flags
        out: list[dict] = []
        for key in sorted(variables):
            spec = variables[key]
            default = spec.coerced_default()
            out.append({
                "key": key,
                "type": spec.type,
                "category": spec.category,
                "description": spec.description,
                "default": default,
                "value": flags.get(key, default),
                "is_set": key in flags,
            })
        return out

    def _chapter_view(self) -> dict:
        """The pack's chapter structure paired with the live cursor.

        Reads the pack's :class:`ChapterManifest` from the ``__chapters__`` meta
        bridge (empty if the pack declares none) and reports the current chapter
        plus the ordered list, each row flagged ``is_current`` and ``reached``
        (its entry/member scenes appear in the read log). Mirrors
        ``_variables_view``: schema-aware, not just a raw scalar dump.
        """
        manifest = self.state.meta.get("__chapters__")
        chapters = manifest.ordered() if manifest is not None else []
        cur = self.state.current_chapter
        seen = getattr(self.state.read_log, "scenes", set()) or set()

        def _reached(c) -> bool:
            return (c.entry_scene in seen) or any(s in seen for s in c.scenes)

        return {
            "current": cur,
            "current_title": next((c.title for c in chapters if c.id == cur), None),
            "ordered": [
                {"id": c.id, "title": c.title, "route": c.route, "order": c.order,
                 "is_current": c.id == cur, "reached": _reached(c)}
                for c in chapters
            ],
        }

    # ----- actions ------------------------------------------------------

    def move_to(self, loc_id: str) -> dict:
        flags = self.state.events.flags
        if not self.state.map.can_move_to(loc_id, flags):
            return {"ok": False, "error": f"can't move to {loc_id}"}
        loc = self.state.map.move_to(loc_id)
        self.state.time.advance(1)
        self.state.events.record(kind="location", title=f"前往 {loc.name}",
                                 location=loc.id)
        for hook in self.state.map.available_scenes(
            time_of_day=self.state.time.time_of_day.value,
            flags=flags,
            played_scenes=self.state.story.played,
            state=self.state,
        ):
            if hook.trigger in ("enter", "auto"):
                pres = self.dialogue.start_scene(hook.scene_id)
                self.last_presentation = self._serialize_pres(pres)
                return {"ok": True, "moved_to": loc.id,
                        "scene_triggered": hook.scene_id,
                        "presentation": self.last_presentation}
        return {"ok": True, "moved_to": loc.id}

    def start_scene(self, scene_id: str) -> dict:
        pres = self.dialogue.start_scene(scene_id)
        self.last_presentation = self._serialize_pres(pres)
        return {"ok": True, "presentation": self.last_presentation}

    def next_line(self, count: int = 1) -> dict:
        """Advance up to ``count`` lines. Auto-follows scene transitions
        triggered by on_end play_scene effects."""
        last = None
        for _ in range(count):
            pres = self.dialogue.next_line()
            # Auto-follow transitions (on_end -> play_scene chain).
            while pres.kind == "transition" and pres.next_scene:
                pres = self.dialogue.start_scene(pres.next_scene)
            last = self._serialize_pres(pres)
            self.last_presentation = last
            if pres.kind in ("choice", "end"):
                break
        return {"ok": True, "presentation": last}

    def choose(self, choice_id: str) -> dict:
        pres = self.dialogue.choose(choice_id)
        # Auto-follow transitions when picking a choice that ends the scene
        # and the on_end chains into a sequel scene.
        while pres.kind == "transition" and pres.next_scene:
            pres = self.dialogue.start_scene(pres.next_scene)
        self.last_presentation = self._serialize_pres(pres)
        return {"ok": True, "presentation": self.last_presentation}

    def chat(self, npc_id: str, message: str) -> dict:
        """Free-chat with an NPC.

        LLM-backed chat is not yet wired up — the deterministic
        EchoBrain returns a placeholder line and the +1 affection /
        event-log side effects still fire. Useful for testing the
        envelope around the future LLM integration.
        """
        npc = self.npcs.get(npc_id)
        if npc is None:
            return {"ok": False, "error": f"unknown npc {npc_id}"}
        loc = self.state.map.current
        loc_label = loc.name if loc else "（不明）"
        recent = [f"[{e.kind}] {e.title}"
                  for e in self.state.events.recent(8)]
        system = npc.system_prompt(
            player_name=self.state.player.name,
            affection=self.state.affection.get(npc.id),
            location=loc_label,
            time_of_day=self.state.time.time_of_day.label,
            recent_events=recent,
        )
        user = (
            f"場景：自由對話\n地點：{loc_label}\n"
            f"時刻：{self.state.time.time_of_day.label}\n"
            f"玩家對你說：「{message}」\n"
            f"請以 {npc.name} 的身份回覆，1~3 句中文對白。"
        )
        try:
            reply = self.brain.respond(npc=npc, system_prompt=system,
                                       user_context=user, history=None)
        except Exception as e:
            reply = f"(brain-error: {e})"
        new_val, unlocked = self.state.affection.adjust(npc.id, 1)
        self.state.events.record(
            kind="dialogue",
            title=f"{self.state.player.name}: {message[:30]}",
            location=self.state.map.current_location_id,
            actors=[npc.id], data={"speaker": "player", "to": npc.id,
                                    "message": message},
        )
        self.state.events.record(
            kind="dialogue",
            title=f"{npc.name}: {reply[:30]}",
            location=self.state.map.current_location_id,
            actors=[npc.id], data={"speaker": npc.id, "reply": reply},
        )
        return {"ok": True, "speaker": npc.name, "reply": reply,
                "affection": new_val, "unlocked": unlocked}

    def advance_time(self, phases: int = 1) -> dict:
        self.state.time.advance(phases)
        return {"ok": True, "time": self.state.time.label()}

    def set_flag(self, key: str, value: Any = True) -> dict:
        self.state.events.set_flag(key, value)
        # Re-evaluate achievements so headless-driven flag changes still
        # trigger them.
        unlocked = self.state.achievements.check(self.state)
        return {"ok": True, "flag": key, "value": value,
                "achievements_unlocked": [a.id for a in unlocked]}

    def adjust_affection(self, npc_id: str, delta: int,
                         stat: str = "affection") -> dict:
        new_val, unlocked = self.state.affection.adjust(npc_id, delta, stat)
        return {"ok": True, "new": new_val, "unlocked": unlocked}

    def _serialize_pres(self, pres: ScenePresentation) -> dict:
        d = {"kind": pres.kind, "scene_id": pres.scene_id,
             "title": pres.title, "next_scene": pres.next_scene}
        if pres.line is not None:
            # ``text`` is the clean, markup-stripped string so headless / script
            # consumers never see rich-text tags. ``raw_text`` keeps the markup
            # for tools that want it.
            clean = pres.line.plain_text or pres.line.text
            d["line"] = {
                "speaker": pres.line.speaker,
                "text": clean,
                "raw_text": pres.line.text,
                "line_index": pres.line.line_index,
                "total_lines": pres.line.total_lines,
                "voice": pres.line.voice,
                "effects": pres.line.effects_applied,
                "is_llm_generated": pres.line.is_llm_generated,
            }
        if pres.choices:
            d["choices"] = [{"id": c.id, "text": c.text, "enabled": c.enabled}
                            for c in pres.choices]
        return d

    # ----- control: drive the full effect/condition vocabulary ----------

    def apply_effect(self, effect: dict) -> dict:
        """Apply a raw effect dict (any registered kind) via GameState.apply."""
        from .core.story_graph import Effect
        result = self.state.apply(Effect(**effect))
        return {"ok": "error" not in result, "result": result}

    def check_condition(self, condition: dict) -> dict:
        """Evaluate a raw condition dict via GameState.evaluate."""
        from .core.story_graph import Condition
        return {"ok": True, "result": bool(self.state.evaluate(Condition(**condition)))}

    def assert_expect(self, cmd: dict) -> dict:
        """Check an expectation about current state. ``ok`` is the pass/fail.

        Forms: ``{flag, equals?}`` · ``{affection, gte|lt|equals, stat?}`` ·
        ``{scene_played}`` · ``{condition: {...}}``.
        """
        if "flag" in cmd:
            actual = self.state.events.get_flag(cmd["flag"])
            if "equals" in cmd:
                return {"ok": actual == cmd["equals"],
                        "assert": f"flag {cmd['flag']} == {cmd['equals']!r}",
                        "actual": actual}
            return {"ok": bool(actual),
                    "assert": f"flag {cmd['flag']} truthy", "actual": actual}
        if "affection" in cmd:
            stat = cmd.get("stat", "affection")
            actual = self.state.affection.get(cmd["affection"], stat)
            if "gte" in cmd:
                ok, rel = actual >= cmd["gte"], f">= {cmd['gte']}"
            elif "lt" in cmd:
                ok, rel = actual < cmd["lt"], f"< {cmd['lt']}"
            elif "equals" in cmd:
                ok, rel = actual == cmd["equals"], f"== {cmd['equals']}"
            else:
                ok, rel = True, "present"
            return {"ok": ok,
                    "assert": f"affection[{cmd['affection']}.{stat}] {rel}",
                    "actual": actual}
        if "scene_played" in cmd:
            actual = self.state.story.is_played(cmd["scene_played"])
            return {"ok": bool(actual),
                    "assert": f"scene_played {cmd['scene_played']}",
                    "actual": actual}
        if "condition" in cmd:
            from .core.story_graph import Condition
            actual = bool(self.state.evaluate(Condition(**cmd["condition"])))
            return {"ok": actual,
                    "assert": f"condition {cmd['condition'].get('kind')}",
                    "actual": actual}
        return {"ok": False, "error": "unknown assert form"}

    # ----- branch exploration: snapshot / restore / diff ----------------

    def snapshot(self) -> dict:
        """A portable JSON-safe snapshot of the current state."""
        from .dev.diff import snapshot
        return snapshot(self.state)

    def restore(self, data: dict) -> dict:
        """Restore state in place from a :meth:`snapshot` dict."""
        from .dev.diff import restore
        restore(self.state, data)
        self.last_presentation = None
        return {"ok": True}

    def diff(self, before: dict, after: dict) -> dict:
        """Structured leaf-level diff between two snapshots."""
        from .dev.diff import diff
        return diff(before, after)

    def take_snapshot(self, name: str = "default") -> dict:
        """Store a named snapshot (run_script ``snapshot`` op)."""
        from .dev.diff import snapshot
        self._snapshots[name] = snapshot(self.state)
        return {"ok": True, "name": name}

    def restore_snapshot(self, name: str = "default") -> dict:
        """Restore a previously stored named snapshot (run_script ``restore`` op)."""
        from .dev.diff import restore
        snap = self._snapshots.get(name)
        if snap is None:
            return {"ok": False, "error": f"no snapshot named '{name}'"}
        restore(self.state, snap)
        self.last_presentation = None
        return {"ok": True, "name": name}

    def affordances(self) -> dict:
        """The current action space: what the agent can do, and why-not.

        Reports available/blocked exits (with reasons), the current scene's
        choices (with the conditions that block each disabled one), available
        scene hooks, and the full effect/condition vocabulary the ``apply`` /
        ``check`` ops accept.
        """
        from .dev.capability_manifest import all_condition_kinds, all_effect_kinds
        flags = self.state.events.flags
        tod = self.state.time.time_of_day.value
        loc = self.state.map.current

        exits = []
        if loc:
            for ex in loc.exits:
                avail = ex.is_available(tod, flags)
                row = {"target": ex.target, "label": ex.label, "available": avail}
                if not avail:
                    row["blocked_reason"] = ex.unavailable_reason(tod)
                exits.append(row)

        choices = []
        scene_id = self.state.story.current_scene
        scene = self.state.story.scenes.get(scene_id) if scene_id else None
        if scene:
            from .dialogue.condition_text import summarize_lock
            for ch in scene.choices:
                blocked_by = []
                failed_requires = []
                hit_forbids = []
                for c in ch.requires:
                    if not self.state.evaluate(c):
                        blocked_by.append({"requires": c.kind, "target": c.target})
                        failed_requires.append(c)
                for c in ch.forbids:
                    if self.state.evaluate(c):
                        blocked_by.append({"forbids": c.kind, "target": c.target})
                        hit_forbids.append(c)
                row = {"id": ch.id, "text": ch.text,
                       "enabled": not blocked_by, "blocked_by": blocked_by}
                if blocked_by:
                    # A concise human-readable reason ("需要 與林青衣的好感度 ≥
                    # 40") alongside the structured blocked_by — the same string
                    # the choice menu shows under a locked option.
                    row["lock_reason"] = summarize_lock(
                        failed_requires, hit_forbids, self.state)
                choices.append(row)

        scenes = [h.scene_id for h in self.state.map.available_scenes(
            time_of_day=tod, flags=flags,
            played_scenes=self.state.story.played, state=self.state)]

        return {
            "location": loc.id if loc else None,
            "exits": exits,
            "choices": choices,
            "scenes_available": scenes,
            "applicable_effects": all_effect_kinds(),
            "applicable_conditions": all_condition_kinds(),
        }

    # ----- warm structural editing --------------------------------------

    def _pack_dir(self) -> Path:
        """The pack root (dir containing ``content/``) for editor + analysis."""
        return self.config.pack_root(self.pack)

    def _editor_handle(self):
        """Lazily build a dry-run :class:`PackEditor` that stages edits."""
        from .dev.pack_editor import PackEditor
        if self._editor is None:
            self._editor = PackEditor(self._pack_dir(), dry_run=True)
        return self._editor

    def _stage_edit(self, editor, name: str, cmd: dict) -> None:
        """Route one ``edit.<name>`` op to the matching PackEditor mutator.

        Raises :class:`PackEditError` (structured) on a bad payload and
        ``KeyError`` on a missing required field — both caught by :meth:`edit`.
        """
        from .dev.pack_editor import PackEditError
        if name == "add_scene":
            editor.add_scene(cmd["scene"], into_file=cmd.get("file"))
        elif name == "update_scene":
            editor.update_scene(cmd["scene_id"], cmd["updates"])
        elif name == "remove_scene":
            editor.remove_scene(cmd["scene_id"])
        elif name == "add_choice":
            editor.add_choice(cmd["scene_id"], cmd["choice"])
        elif name == "update_line":
            editor.update_line(cmd["scene_id"], int(cmd["line_index"]), cmd["updates"])
        elif name == "add_npc":
            editor.add_npc(cmd["npc"], into_file=cmd.get("file"))
        elif name == "update_npc":
            editor.update_npc(cmd["npc_id"], cmd["updates"])
        elif name == "remove_npc":
            editor.remove_npc(cmd["npc_id"])
        elif name == "add_location":
            editor.add_location(cmd["location"], into_file=cmd.get("file"))
        elif name == "update_location":
            editor.update_location(cmd["loc_id"], cmd["updates"])
        elif name == "remove_location":
            editor.remove_location(cmd["loc_id"])
        elif name == "add_item":
            editor.add_item(cmd["item"], into_file=cmd.get("file"))
        elif name == "add_quest":
            editor.add_quest(cmd["quest"], into_file=cmd.get("file"))
        elif name == "add_clue":
            editor.add_clue(cmd["clue"], into_file=cmd.get("file"))
        elif name == "add_achievement":
            editor.add_achievement(cmd["achievement"], into_file=cmd.get("file"))
        elif name == "add_resource":
            editor.add_resource(cmd["resource"], into_file=cmd.get("file"))
        else:
            raise PackEditError(
                op=name, path="", message=f"unknown edit op: edit.{name}",
                hint="known: add_scene/update_scene/remove_scene/add_choice/"
                     "update_line/add_npc/update_npc/remove_npc/add_location/"
                     "update_location/remove_location/add_item/add_quest/"
                     "add_clue/add_achievement/add_resource")

    def edit(self, op: str, cmd: dict) -> dict:
        """Stage one structural edit; autocommit + report impact unless in a tx.

        Outside a transaction each edit is its own atomic unit: the change is
        validated + staged, written to disk, the pack's static world model is
        re-derived, and the response carries the YAML ``diff`` *and* the
        ``impact`` delta (new dead-ends / unreachable endings / undeclared
        flags) — the whole understand-edit-verify loop in one round-trip.
        Inside a ``begin``/``commit`` transaction the edit only stages
        (``staged: true``); the impact is reported once at ``commit``.
        """
        from .dev.pack_editor import PackEditError
        editor = self._editor_handle()
        name = op.split(".", 1)[1] if "." in op else op
        try:
            self._stage_edit(editor, name, cmd)
        except PackEditError as exc:
            return {"op": op, "ok": False, "changed": False, "error": exc.to_dict()}
        except KeyError as exc:
            return {"op": op, "ok": False, "changed": False,
                    "error": {"op": name, "message": f"missing required field: {exc}"}}
        diff = editor.diff()
        if self._in_tx:
            return {"op": op, "ok": True, "changed": editor.has_pending(),
                    "staged": True, "diff": diff, "files": editor.pending_files()}
        return self._commit_edits(op, diff)

    def _commit_edits(self, op_label: str, diff: str) -> dict:
        """Write staged edits, re-derive the world model, return the impact."""
        from .dev.world_model import world_delta, world_snapshot
        editor = self._editor
        if editor is None or not editor.has_pending():
            return {"op": op_label, "ok": True, "changed": False, "diff": ""}
        root = self._pack_dir()
        # Disk still holds the pre-edit pack (edits are staged in memory), so a
        # snapshot now is the true "before". Reuse the cached baseline when we
        # have one to avoid recomputing it.
        before = self._world_baseline if self._world_baseline is not None \
            else world_snapshot(root)
        changes = editor.list_changes()
        info = editor.commit()
        self._editor = None
        after = world_snapshot(root)
        self._world_baseline = after
        return {"op": op_label, "ok": True, "changed": True, "diff": diff,
                "files": info.get("files_written", []), "changes": changes,
                "impact": world_delta(before, after)}

    def begin_edit(self) -> dict:
        """Open a structural-edit transaction (subsequent edits stage only)."""
        from .dev.world_model import world_snapshot
        if self._in_tx:
            return {"op": "begin", "ok": True, "tx": "begin", "note": "already open"}
        self._in_tx = True
        self._editor_handle()
        self._tx_baseline = self._world_baseline if self._world_baseline is not None \
            else world_snapshot(self._pack_dir())
        return {"op": "begin", "ok": True, "tx": "begin"}

    def commit_edit(self) -> dict:
        """Write every staged edit and report the aggregate impact delta."""
        from .dev.world_model import world_delta, world_snapshot
        if not self._in_tx:
            return {"op": "commit", "ok": False, "error": "no transaction open"}
        editor = self._editor
        diff = editor.diff() if editor else ""
        if editor is None or not editor.has_pending():
            self._in_tx = False
            self._tx_baseline = None
            return {"op": "commit", "ok": True, "tx": "commit", "changed": False}
        changes = editor.list_changes()
        info = editor.commit()
        self._editor = None
        after = world_snapshot(self._pack_dir())
        impact = world_delta(self._tx_baseline or {}, after)
        self._world_baseline = after
        self._in_tx = False
        self._tx_baseline = None
        return {"op": "commit", "ok": True, "tx": "commit", "changed": True,
                "diff": diff, "files": info.get("files_written", []),
                "changes": changes, "impact": impact}

    def rollback_edit(self) -> dict:
        """Discard all staged edits and close any open transaction."""
        had_pending = self._editor is not None and self._editor.has_pending()
        if self._editor is not None:
            self._editor.rollback()
            self._editor = None
        was_open = self._in_tx
        self._in_tx = False
        self._tx_baseline = None
        return {"op": "rollback", "ok": True, "tx": "rollback",
                "discarded": had_pending, "tx_was_open": was_open}

    def reload_content(self) -> dict:
        """Rebuild the in-memory pack from disk so runtime ops see edits.

        Edits never auto-reload (they touch disk, not the live state); call
        this when ready to *play* the edited pack. Resets the runtime position
        — a scene you just rewrote cannot keep its cursor — and clears named
        snapshots, which referred to the old content. ``load_pack`` runs first,
        so a pack broken by an edit raises here and leaves the live session
        untouched.
        """
        content_root = self.config.pack_content(self.pack)
        state, npcs, meta = load_pack(content_root)
        localization = Localization.from_meta(meta)
        bind_time_localization(localization)
        state.affection.bind_localization(localization)
        if self.config.seed is not None:
            state.meta["__seed__"] = self.config.seed
        self.state = state
        self.npcs = npcs
        self.meta = meta
        self.dialogue = DialogueEngine(state, llm_provider=None)
        self._snapshots.clear()
        self.last_presentation = None
        return {"op": "reload", "ok": True, "reloaded": True,
                "scenes": len(state.story.scenes)}

    # ----- scripted run -------------------------------------------------

    # Ops that act on the pack/session rather than the live GameState; their
    # per-step runtime diff would be empty (or, for `reload`, a misleading
    # full-state swap), so run_script skips diffing them.
    _META_OPS = frozenset({"begin", "commit", "rollback", "reload"})

    def _dispatch_op(self, op: str, cmd: dict) -> dict:
        if isinstance(op, str) and op.startswith("edit."):
            return self.edit(op, cmd)
        if op == "begin":
            return self.begin_edit()
        if op == "commit":
            return self.commit_edit()
        if op == "rollback":
            return self.rollback_edit()
        if op == "reload":
            return self.reload_content()
        if op == "move":
            return self.move_to(cmd["location"])
        if op == "start_scene":
            return self.start_scene(cmd["scene"])
        if op == "next":
            return self.next_line(cmd.get("count", 1))
        if op == "choose":
            return self.choose(cmd["choice"])
        if op == "chat":
            return self.chat(cmd["npc"], cmd["message"])
        if op == "advance_time":
            return self.advance_time(cmd.get("phases", 1))
        if op == "set_flag":
            return self.set_flag(cmd["key"], cmd.get("value", True))
        if op == "adjust_affection":
            return self.adjust_affection(cmd["npc"], int(cmd["delta"]),
                                         cmd.get("stat", "affection"))
        if op == "inspect":
            return {"ok": True, "snapshot": self.inspect()}
        if op == "apply":
            return self.apply_effect(cmd["effect"])
        if op == "check":
            return self.check_condition(cmd["condition"])
        if op == "assert":
            return self.assert_expect(cmd)
        if op == "affordances":
            return {"ok": True, "affordances": self.affordances()}
        if op == "snapshot":
            return self.take_snapshot(cmd.get("name", "default"))
        if op == "restore":
            return self.restore_snapshot(cmd.get("name", "default"))
        return {"ok": False, "error": f"unknown op: {op}"}

    def run_script(self, commands: list[dict]) -> list[dict]:
        """Execute a batch of ops; return one result dict per op.

        Each result carries its ``op``, the op's return dict, and — when the
        state changed — a structured ``diff`` of that step. The full ordered
        execution trace (effects fired with results, lines/choices shown,
        moves, time) is collected into ``self.transcript`` so the whole run is
        understandable from a single call (the anti-MCP "rich batch").

        Runtime ops: move, start_scene, next, choose, chat, advance_time,
        set_flag, adjust_affection, inspect, apply, check, assert, affordances,
        snapshot, restore. Structural-edit ops (warm authoring loop): edit.*
        (add_scene/update_scene/remove_scene/add_choice/update_line/add_npc/
        update_npc/remove_npc/add_location/update_location/remove_location/
        add_item), and the transaction controls begin / commit / rollback /
        reload. An edit op autocommits and returns its YAML ``diff`` plus an
        ``impact`` delta; wrap several in begin ... commit to stage them and get
        one aggregate impact.
        """
        from .dev.diff import diff as _diff, snapshot as _snapshot
        if self._trace is None:
            from .dev.trace import TraceRecorder
            self._trace = TraceRecorder()
        self._trace.attach()
        self._trace.clear()
        out: list[dict] = []
        try:
            for cmd in commands:
                op = cmd.get("op")
                # edit.* / begin / commit / rollback / reload act on the pack
                # or session, not the live state; they carry their own diff +
                # impact, so skip the runtime state-diff for them.
                is_meta = isinstance(op, str) and (
                    op.startswith("edit.") or op in self._META_OPS)
                before = None if is_meta else _snapshot(self.state)
                try:
                    r = self._dispatch_op(op, cmd)
                except Exception as e:
                    r = {"ok": False, "error": str(e)}
                r["op"] = op
                if not is_meta:
                    step_diff = _diff(before, _snapshot(self.state))
                    if step_diff:
                        r["diff"] = step_diff
                    # Uniform envelope: every runtime op result reports whether
                    # it changed live state (edit ops set their own `changed`).
                    r["changed"] = bool(step_diff)
                out.append(r)
        finally:
            # Detach so trace hooks never leak across sessions / tests.
            self.transcript = self._trace.drain()
            self._trace.detach()
        return out


# --------- CLI ---------------------------------------------------------------


def run_inspect(config: EngineConfig, pack: str | None = None) -> None:
    sess = HeadlessSession.open(config, pack=pack)
    print(json.dumps(sess.inspect(), ensure_ascii=False, indent=2))


def run_script(config: EngineConfig, script_path: str,
               pack: str | None = None,
               inspect_after: bool = True) -> None:
    sess = HeadlessSession.open(config, pack=pack)
    data = json.loads(Path(script_path).read_text(encoding="utf-8"))
    commands = data if isinstance(data, list) else data.get("commands", [])
    results = sess.run_script(commands)
    output = {"results": results, "transcript": sess.transcript}
    if inspect_after:
        output["final_state"] = sess.inspect()
    print(json.dumps(output, ensure_ascii=False, indent=2))

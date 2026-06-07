"""Dialogue engine: walks through scenes one line at a time.

The engine is *cooperative*: the UI calls .next_line() to advance a line,
or .choose(choice_id) to pick a choice at a decision point. State is held
inside GameState (not in the engine itself), so the engine is safe to
recreate between requests in a web server.
"""
from __future__ import annotations

from typing import Any
from dataclasses import dataclass, field

import re

from ..core.game_state import GameState
from ..core.story_graph import Scene, Line, Choice
from ..core.portrait_spec import PortraitSpec
from ..core.text_interpolation import interpolate
from .richtext import strip_markup


_DIALOGUE_OP_RE = re.compile(r"\[\[([a-zA-Z_][a-zA-Z0-9_]*)(?::([^\]]*))?\]\]")


def _apply_dialogue_ops(text: str, state: GameState) -> str:
    """Strip `[[op:arg]]` directives and fire registered handlers.

    The handler's return value (when a string) replaces the directive
    inline; otherwise the directive is removed entirely. Unknown ops
    pass through untouched so plugin authors can spot missing handlers
    in the rendered text (rather than silently dropping them).
    """
    from ..plugins.registry import DIALOGUE_OP_REGISTRY
    from ..plugins.errors import isolate

    def _sub(m: re.Match) -> str:
        name = m.group(1)
        arg = m.group(2) or ""
        entry = DIALOGUE_OP_REGISTRY.get(name)
        if entry is None:
            return m.group(0)  # leave [[unknown:foo]] in place
        repl_holder: list[str] = []
        with isolate(entry.plugin_id, f"dialogue_op:{name}"):
            result = entry.fn(state, arg)
            if isinstance(result, str):
                repl_holder.append(result)
        return repl_holder[0] if repl_holder else ""

    return _DIALOGUE_OP_RE.sub(_sub, text)


@dataclass
class LinePresentation:
    """What the UI needs to render a single line."""

    speaker: str | None
    text: str                       # raw markup (parsed by the UI layer)
    plain_text: str = ""            # markup stripped, for headless / history
    portrait: "str | PortraitSpec | None" = None
    portraits: list = field(default_factory=list)   # list[PortraitSpec], multi-character staging
    portrait_pos: str | None = None                 # slot for the simple portrait forms
    expression: str | None = None
    cg: str | None = None
    background: str | None = None
    bgm: str | None = None
    ambient: str | None = None
    sfx: str | None = None
    voice: str | None = None
    scene_id: str | None = None
    line_index: int = 0
    total_lines: int = 0
    effects_applied: list[dict[str, Any]] = field(default_factory=list)
    is_llm_generated: bool = False


@dataclass
class ChoiceOption:
    id: str
    text: str
    enabled: bool
    # When a visible choice is locked, ``reason`` is a concise human-readable
    # phrase explaining why (the unmet condition, e.g. "需要 與林青衣的好感度 ≥
    # 40"), so the UI can render it instead of a silent ghost button. Empty for
    # an enabled choice. Computed from the failing requires/forbids.
    reason: str = ""


@dataclass
class ScenePresentation:
    """State returned by next() — either a line or a choice point or end."""

    kind: str   # "line", "choice", "end", "transition"
    line: LinePresentation | None = None
    choices: list[ChoiceOption] = field(default_factory=list)
    next_scene: str | None = None
    scene_id: str | None = None
    title: str | None = None
    background: str | None = None


class DialogueEngine:
    def __init__(self, state: GameState, *, llm_provider=None):
        self.state = state
        self.llm_provider = llm_provider   # optional callable for LLM lines

    # ---------- public API ----------------------------------------------------

    def current_scene(self) -> Scene | None:
        sid = self.state.story.current_scene
        if sid is None:
            return None
        return self.state.story.get(sid)

    def start_scene(self, scene_id: str) -> ScenePresentation:
        scene = self.state.story.start(scene_id)
        self.state.events.record(
            kind="scene", title=scene.title or scene.id,
            location=scene.location, data={"scene": scene_id},
        )
        if scene.bgm:
            self.state.meta["current_bgm"] = scene.bgm
            # Scene-level BGM is the common case (most packs set bgm per scene,
            # not per line), so unlock it here too — otherwise the music room
            # would miss almost every track. Line-level cg/bgm unlock in
            # _present_line complements this.
            self.state.music_room.unlock(scene.bgm)
        # Scene-level ambient bed carries until another scene changes it. When a
        # scene sets none, fall back to its location's default room-tone, so every
        # scene at a place inherits that place's ambient with no per-scene authoring
        # (the 演出 audit's "silence is the loudest tell in a daily" fix).
        if scene.ambient:
            self.state.meta["current_ambient"] = scene.ambient
        elif scene.location:
            loc = self.state.map.locations.get(scene.location)
            if loc is not None and loc.ambient:
                self.state.meta["current_ambient"] = loc.ambient
        if scene.cg:
            self.state.cg_gallery.unlock(scene.cg)
        return self._present_current()

    def next_line(self) -> ScenePresentation:
        """Render the next line, or surface choices / end.

        ``_present_current`` already advances the index after rendering a
        line, so this method must NOT pre-advance — doing so would skip
        every second line. (Earlier versions had this bug; the smoke
        scripts hid it by calling next() with a high count and breaking
        on end.)
        """
        scene = self.current_scene()
        if scene is None:
            return ScenePresentation(kind="end")
        return self._present_current()

    def choose(self, choice_id: str) -> ScenePresentation:
        scene = self.current_scene()
        if scene is None:
            return ScenePresentation(kind="end")
        choice = next((c for c in scene.choices if c.id == choice_id), None)
        if choice is None:
            return self._present_current()
        if not self._choice_available(choice):
            return self._present_current()
        from ..plugins import fire_event
        from ..plugins.context import HookEvent
        fire_event(self.state, HookEvent.DIALOGUE_CHOICE_MADE,
                   scene_id=scene.id, choice_id=choice.id)
        # Apply effects.
        effects_out = self.state.apply_all(choice.effects)
        # Interpolate the recorded choice text so the event log shows the
        # resolved player-name / resource values at decision time.
        choice_text = interpolate(choice.text, self.state)
        self.state.events.record(
            kind="choice", title=choice_text,
            location=scene.location,
            data={"scene": scene.id, "choice": choice.id, "effects": effects_out},
        )
        # Transition to next scene (the current scene is "done": a choice
        # that hands off to a sequel scene counts as completing this one).
        if choice.next_scene:
            self.state.story.mark_played(scene.id)
            self.state.read_log.mark_scene_done(scene.id)
            return self.start_scene(choice.next_scene)
        # Otherwise end this scene.
        return self._end_current_scene()

    # ---------- skip / auto-play helpers --------------------------------------

    def is_current_read(self) -> bool:
        """Return True if the line the engine is *about to present* is already
        in the read log.  Useful for skip-mode to decide whether to pause."""
        scene = self.current_scene()
        if scene is None:
            return False
        idx = self.state.story.current_line_index
        # Peek past any invisible lines (mirrors _present_current logic).
        while idx < len(scene.lines) and not self._line_visible(scene.lines[idx]):
            idx += 1
        if idx >= len(scene.lines):
            # At the choice/end boundary — treat as unread so UI pauses.
            return False
        return self.state.read_log.is_read(scene.id, idx)

    def skip_to_next_unread(self) -> "ScenePresentation | None":
        """Auto-advance through already-read lines; stop at the first unread
        line or any choice point (choices always require player input).

        Returns the first unread ScenePresentation, or None when the scene ends
        without finding unread content.
        """
        while True:
            scene = self.current_scene()
            if scene is None:
                return None
            idx = self.state.story.current_line_index
            # Skip invisible lines same as _present_current.
            while idx < len(scene.lines) and not self._line_visible(scene.lines[idx]):
                idx += 1
            self.state.story.current_line_index = idx

            # Reached end-of-lines -> choices or end (always stop skipping).
            if idx >= len(scene.lines):
                pres = self.next_line()
                return None if pres.kind == "end" else pres

            # Unread line: stop and present it.
            if not self.state.read_log.is_read(scene.id, idx):
                return self.next_line()

            # Already-read line: advance past it without pausing.
            # We call next_line() to apply effects and push history, then
            # continue the loop to check the *following* line.
            pres = self.next_line()
            # Stop at any non-line result.  end -> None; choice/transition -> return.
            if pres.kind == "end":
                return None
            if pres.kind in ("choice", "transition"):
                return pres

    def skip_all(self) -> "ScenePresentation | None":
        """Skip-all mode: race through *every* remaining line (read or not),
        stopping only at a choice point or the end of the scene.

        This is the unread-inclusive sibling of :meth:`skip_to_next_unread`.
        Like that method it advances line-by-line through the engine (so each
        line's effects fire and history/read-log update) but never pauses on
        an unread line — only a choice or transition halts the skip. Returning
        the *final* presentation lets the scene render once at the stopping
        point rather than re-rendering every skipped line (matching the
        existing skip helper's contract).

        Returns the choice / transition presentation reached, or ``None`` when
        the scene ends without one.
        """
        while True:
            scene = self.current_scene()
            if scene is None:
                return None
            pres = self.next_line()
            if pres.kind == "end":
                return None
            if pres.kind in ("choice", "transition"):
                return pres
            # kind == "line": keep skipping.

    # ---------- internals -----------------------------------------------------

    def _choice_available(self, choice: Choice) -> bool:
        if not self.state.evaluate_all(choice.requires):
            return False
        if not self.state.evaluate_none(choice.forbids):
            return False
        return True

    def _choice_lock_reason(self, choice: Choice) -> str:
        """A concise human-readable phrase explaining why ``choice`` is locked.

        Collects the choice's *failing* gates — a ``requires`` condition that is
        unmet, or a ``forbids`` condition that is currently hit — and renders
        them via :mod:`condition_text`. Returns "" for an available choice.
        Isolated: any failure degrades to an empty reason (the choice still
        renders, just without the why), never raising on the present path.
        """
        try:
            failed_requires = [c for c in choice.requires
                               if not self.state.evaluate(c)]
            hit_forbids = [c for c in choice.forbids
                           if self.state.evaluate(c)]
            if not failed_requires and not hit_forbids:
                return ""
            from .condition_text import summarize_lock
            return summarize_lock(failed_requires, hit_forbids, self.state)
        except Exception:
            return ""

    def _line_visible(self, line: Line) -> bool:
        return self.state.evaluate_all(line.requires)

    def _present_line(self, scene: Scene, line: Line, idx: int) -> LinePresentation:
        text = line.text
        is_llm = False
        if line.llm_speaker and self.llm_provider is not None:
            try:
                text = self.llm_provider(
                    speaker=line.speaker or "narrator",
                    directive=line.llm_directive or "",
                    state=self.state,
                    scene=scene,
                ) or line.text
                is_llm = True
            except Exception as e:
                text = line.text + f"\n[LLM 失敗: {e}]"
        # Apply {token} interpolation after LLM resolution so LLM-generated
        # text can also embed state variables.
        text = interpolate(text, self.state)
        # Parse + fire any [[op:arg]] dialogue directives. ``text`` is now the
        # raw markup string the UI renders; ``plain`` strips rich-text tags for
        # headless / history so no [tag] leaks into clean-text consumers.
        text = _apply_dialogue_ops(text, self.state)
        plain = strip_markup(text)
        effects_out: list[dict[str, Any]] = []
        if line.effects:
            effects_out = self.state.apply_all(line.effects)
        if line.bgm:
            self.state.meta["current_bgm"] = line.bgm
        if line.ambient:
            self.state.meta["current_ambient"] = line.ambient
        # Mark as read *before* pushing to history (order doesn't matter for
        # the ReadLog, but it's consistent with "user has now seen this").
        self.state.read_log.mark_line(scene.id, idx)
        # Auto-unlock the CG / BGM this line shows so the gallery and music
        # room can offer them later. Scene-level cg / bgm unlock similarly in
        # start_scene(); the viewer scenes themselves stay read-only.
        if line.cg:
            self.state.cg_gallery.unlock(line.cg)
        if line.bgm:
            self.state.music_room.unlock(line.bgm)
        # Interpolate speaker too so lines spoken by the player can use
        # `speaker: "{player_name}"` and render the chosen name.
        speaker_rendered = (interpolate(line.speaker, self.state)
                            if line.speaker else None)
        # Push to dialogue history so the scrollback overlay can show it
        # later. History stores clean text (no rich-text markup) — the
        # scrollback isn't a styled renderer.
        self.state.dialogue_history.push(
            speaker=speaker_rendered, text=plain,
            scene_id=scene.id, portrait=line.portrait,
            voice=line.voice,
        )
        return LinePresentation(
            speaker=speaker_rendered,
            text=text,
            plain_text=plain,
            portrait=line.portrait,
            portraits=list(line.portraits),
            portrait_pos=line.portrait_pos,
            expression=line.expression,
            cg=line.cg or scene.cg,
            background=scene.background,
            bgm=line.bgm or self.state.meta.get("current_bgm"),
            ambient=line.ambient or self.state.meta.get("current_ambient"),
            sfx=line.sfx,
            voice=line.voice,
            scene_id=scene.id,
            line_index=idx,
            total_lines=len(scene.lines),
            effects_applied=effects_out,
            is_llm_generated=is_llm,
        )

    def _present_current(self) -> ScenePresentation:
        scene = self.current_scene()
        if scene is None:
            return ScenePresentation(kind="end")
        idx = self.state.story.current_line_index

        # Skip any non-visible lines.
        while idx < len(scene.lines) and not self._line_visible(scene.lines[idx]):
            idx += 1
        self.state.story.current_line_index = idx

        if idx < len(scene.lines):
            # We're showing a line.
            line = scene.lines[idx]
            # Fire dialogue.before_line so plugins can preview / mutate state
            # (e.g. trigger a cinematic effect on a specific line).
            from ..plugins import fire_event
            from ..plugins.context import HookEvent
            fire_event(self.state, HookEvent.DIALOGUE_BEFORE_LINE,
                       scene_id=scene.id, line_index=idx, line=line)
            pres = self._present_line(scene, line, idx)
            fire_event(self.state, HookEvent.DIALOGUE_AFTER_LINE,
                       scene_id=scene.id, line_index=idx, line=pres)
            # advance pointer past this line so the next call goes to the next
            self.state.story.current_line_index = idx + 1
            return ScenePresentation(
                kind="line", line=pres,
                scene_id=scene.id, title=scene.title,
                background=scene.background,
            )

        # We've consumed all lines — surface choices or end.
        available = [c for c in scene.choices if self._choice_available(c)]
        hidden_locked = [c for c in scene.choices
                         if not self._choice_available(c) and not c.hidden_if_locked]
        if available or hidden_locked:
            options = [
                ChoiceOption(id=c.id, text=interpolate(c.text, self.state), enabled=True)
                for c in available
            ]
            # Visible-but-locked choices carry a concise reason (the unmet
            # gate, e.g. "需要 與林青衣的好感度 ≥ 40") so the UI can show *why*
            # rather than a silent ghost button. ``hidden_if_locked`` choices
            # were already filtered out above, so only legitimately-visible
            # locked choices reach here.
            options += [
                ChoiceOption(id=c.id,
                             text=interpolate(c.text, self.state),
                             enabled=False,
                             reason=self._choice_lock_reason(c))
                for c in hidden_locked
            ]
            return ScenePresentation(
                kind="choice", choices=options,
                scene_id=scene.id, title=scene.title,
                background=scene.background,
            )
        return self._end_current_scene()

    def _end_current_scene(self) -> ScenePresentation:
        scene = self.current_scene()
        if scene is None:
            return ScenePresentation(kind="end")
        # apply on_end effects, mark played, surface next transition (if any).
        end_results = self.state.apply_all(scene.on_end)
        self.state.story.mark_played(scene.id)
        self.state.read_log.mark_scene_done(scene.id)
        self.state.story.current_scene = None
        self.state.story.current_line_index = 0
        # Look for a play_scene effect to suggest a transition.
        next_scene = None
        for r in end_results:
            if r.get("kind") == "play_scene":
                next_scene = r.get("scene")
        return ScenePresentation(
            kind="transition" if next_scene else "end",
            next_scene=next_scene,
            scene_id=scene.id,
            title=scene.title,
        )

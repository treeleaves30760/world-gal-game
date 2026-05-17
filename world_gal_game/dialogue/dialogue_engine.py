"""Dialogue engine: walks through scenes one line at a time.

The engine is *cooperative*: the UI calls .next_line() to advance a line,
or .choose(choice_id) to pick a choice at a decision point. State is held
inside GameState (not in the engine itself), so the engine is safe to
recreate between requests in a web server.
"""
from __future__ import annotations

from typing import Any
from dataclasses import dataclass, field

from ..core.game_state import GameState
from ..core.story_graph import Scene, Line, Choice
from ..core.portrait_spec import PortraitSpec
from ..core.text_interpolation import interpolate


@dataclass
class LinePresentation:
    """What the UI needs to render a single line."""

    speaker: str | None
    text: str
    portrait: "str | PortraitSpec | None" = None
    portraits: list = field(default_factory=list)   # list[PortraitSpec], multi-character staging
    expression: str | None = None
    cg: str | None = None
    background: str | None = None
    bgm: str | None = None
    sfx: str | None = None
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

    # ---------- internals -----------------------------------------------------

    def _choice_available(self, choice: Choice) -> bool:
        if not self.state.evaluate_all(choice.requires):
            return False
        if not self.state.evaluate_none(choice.forbids):
            return False
        return True

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
        effects_out: list[dict[str, Any]] = []
        if line.effects:
            effects_out = self.state.apply_all(line.effects)
        if line.bgm:
            self.state.meta["current_bgm"] = line.bgm
        # Mark as read *before* pushing to history (order doesn't matter for
        # the ReadLog, but it's consistent with "user has now seen this").
        self.state.read_log.mark_line(scene.id, idx)
        # Interpolate speaker too so lines spoken by the player can use
        # `speaker: "{player_name}"` and render the chosen name.
        speaker_rendered = (interpolate(line.speaker, self.state)
                            if line.speaker else None)
        # Push to dialogue history so the scrollback overlay can show it
        # later. We capture the rendered text (post-LLM resolution).
        self.state.dialogue_history.push(
            speaker=speaker_rendered, text=text,
            scene_id=scene.id, portrait=line.portrait,
        )
        return LinePresentation(
            speaker=speaker_rendered,
            text=text,
            portrait=line.portrait,
            portraits=list(line.portraits),
            expression=line.expression,
            cg=line.cg or scene.cg,
            background=scene.background,
            bgm=line.bgm or self.state.meta.get("current_bgm"),
            sfx=line.sfx,
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
            pres = self._present_line(scene, line, idx)
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
            options += [
                ChoiceOption(id=c.id,
                             text=interpolate(c.text, self.state) + " (條件未達)",
                             enabled=False)
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

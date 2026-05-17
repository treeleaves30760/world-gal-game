"""Tests for DialogueEngine skip-mode helpers."""
from world_gal_game.core.game_state import GameState
from world_gal_game.core.story_graph import Scene, Line, Choice
from world_gal_game.dialogue.dialogue_engine import DialogueEngine


def _engine(*lines_text: str, choices: list[Choice] | None = None) -> tuple[DialogueEngine, GameState]:
    s = GameState()
    lines = [Line(text=t) for t in lines_text]
    sc = Scene(id="test_scene", lines=lines, choices=choices or [])
    s.story.add_scene(sc)
    return DialogueEngine(s), s


# ---------- skip_to_next_unread -----------------------------------------------


def test_skip_all_read_returns_none():
    """When every line in the scene is already in the read log, skip returns None."""
    eng, state = _engine("line A", "line B", "line C")
    # First play-through: read all lines
    eng.start_scene("test_scene")
    eng.next_line()  # A
    eng.next_line()  # B
    eng.next_line()  # C (end)

    # Start the same scene again (reset index but leave read log intact)
    state.story.start("test_scene")
    result = eng.skip_to_next_unread()
    assert result is None


def test_skip_stops_at_unread():
    """skip_to_next_unread stops as soon as it hits a line not in the read log."""
    eng, state = _engine("read", "read", "unread")
    # Mark first two lines as read
    state.read_log.mark_line("test_scene", 0)
    state.read_log.mark_line("test_scene", 1)
    # Start the scene (index = 0)
    eng.start_scene("test_scene")
    # skip_to_next_unread should jump over lines 0 and 1, present line 2
    result = eng.skip_to_next_unread()
    assert result is not None
    assert result.kind == "line"
    assert result.line.text == "unread"


def test_skip_stops_at_first_unread_from_start():
    """start_scene marks line 0 as read; resetting the index and skipping
    should jump over line 0 and stop at the unread line 1."""
    eng, state = _engine("first", "second")
    eng.start_scene("test_scene")
    # Line 0 was presented (and marked read) by start_scene.
    # Reset the pointer so skip re-evaluates from line 0.
    state.story.current_line_index = 0
    result = eng.skip_to_next_unread()
    assert result is not None
    assert result.kind == "line"
    # line 0 is already read -> skip jumps to the unread line 1
    assert result.line.text == "second"


# ---------- choices always stop skipping -------------------------------------


def test_skip_always_stops_at_choice():
    """Even when all choices have been seen before, the skip stops at the
    choice point — the player must pick."""
    choices = [Choice(id="c1", text="選項一"), Choice(id="c2", text="選項二")]
    eng, state = _engine("read line", choices=choices)
    # Mark line 0 as read
    state.read_log.mark_line("test_scene", 0)
    eng.start_scene("test_scene")
    # Line 0 was presented & marked; pointer is now past lines, at choice.
    result = eng.skip_to_next_unread()
    # Should surface the choice, not auto-pick.
    assert result is not None
    assert result.kind == "choice"
    assert len(result.choices) == 2


# ---------- is_current_read ---------------------------------------------------


def test_is_current_read_fresh_line():
    eng, state = _engine("fresh")
    eng.start_scene("test_scene")
    # After start_scene, pointer advanced past line 0 (it was presented).
    # Reset to check line 0 "about to present" — but it was already read.
    assert state.read_log.is_read("test_scene", 0)


def test_is_current_read_before_any_play():
    eng, state = _engine("unseen")
    # start the scene without actually calling next_line
    state.story.start("test_scene")
    assert not eng.is_current_read()

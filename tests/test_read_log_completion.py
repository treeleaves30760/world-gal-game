"""Scene-completion wiring: the engine must populate read_log.scenes.

read_log.mark_scene_done() existed but nothing called it, so read_log.scenes
stayed empty in real play — silently breaking the completion-% metric
(endings scene) and the scene-replay list, both of which read that set.
The dialogue engine now mirrors story.mark_played() with
read_log.mark_scene_done() at both scene-completion points.
"""
from world_gal_game.core.game_state import GameState
from world_gal_game.core.story_graph import Scene, Line, Choice
from world_gal_game.dialogue.dialogue_engine import DialogueEngine


def _build_engine(scenes: list[Scene]) -> tuple[DialogueEngine, GameState]:
    s = GameState()
    for sc in scenes:
        s.story.add_scene(sc)
    return DialogueEngine(s), s


def test_scene_marked_done_on_natural_end():
    sc = Scene(id="s", lines=[Line(text="a"), Line(text="b")])
    eng, state = _build_engine([sc])
    eng.start_scene("s")   # shows line "a"
    eng.next_line()        # shows line "b"
    eng.next_line()        # past the last line -> _end_current_scene
    assert "s" in state.read_log.scenes


def test_scene_marked_done_on_choice_transition():
    s1 = Scene(id="s1", lines=[Line(text="a")],
               choices=[Choice(id="c", text="go", next_scene="s2")])
    s2 = Scene(id="s2", lines=[Line(text="b")])
    eng, state = _build_engine([s1, s2])
    eng.start_scene("s1")
    eng.choose("c")        # hands off to s2 -> s1 is "done"
    assert "s1" in state.read_log.scenes


def test_unfinished_scene_not_marked_done():
    sc = Scene(id="s", lines=[Line(text="a"), Line(text="b"), Line(text="c")])
    eng, state = _build_engine([sc])
    eng.start_scene("s")   # only the first line seen
    assert "s" not in state.read_log.scenes

"""DialogueEngine: line advance, choice transitions, scene chaining."""
from world_gal_game.core.game_state import GameState
from world_gal_game.core.story_graph import Scene, Line, Choice, Effect
from world_gal_game.dialogue.dialogue_engine import DialogueEngine


def _build_engine(scenes: list[Scene]) -> tuple[DialogueEngine, GameState]:
    s = GameState()
    for sc in scenes:
        s.story.add_scene(sc)
    return DialogueEngine(s), s


def test_lines_advance_and_end():
    sc = Scene(id="s", lines=[Line(text="a"), Line(text="b")])
    eng, state = _build_engine([sc])
    p = eng.start_scene("s")
    assert p.kind == "line"
    assert p.line.text == "a"
    p = eng.next_line()
    assert p.line.text == "b"
    p = eng.next_line()
    assert p.kind == "end"
    assert "s" in state.story.played


def test_choice_with_next_scene_transitions_and_marks_played():
    s1 = Scene(id="s1", lines=[Line(text="a")], choices=[
        Choice(id="c", text="→ s2", next_scene="s2"),
    ])
    s2 = Scene(id="s2", lines=[Line(text="b")])
    eng, state = _build_engine([s1, s2])
    eng.start_scene("s1")
    eng.next_line()           # consume the only line
    p = eng.choose("c")
    assert p.kind == "line"
    assert p.line.text == "b"
    # both scenes should be marked played
    assert state.story.is_played("s1")
    # s2 will be played once it ends


def test_on_end_play_scene_chains():
    # Scene has a single line; on_end fires a play_scene to "s2".
    # After start_scene renders that one line, the next next_line ends
    # the scene and returns a transition presentation.
    s1 = Scene(id="s1", lines=[Line(text="a")], on_end=[
        Effect(kind="play_scene", target="s2"),
    ])
    s2 = Scene(id="s2", lines=[Line(text="b")])
    eng, state = _build_engine([s1, s2])
    eng.start_scene("s1")     # renders "a", index now past it
    p = eng.next_line()       # end of s1 + play_scene effect
    assert p.kind == "transition"
    assert p.next_scene == "s2"


def test_choice_requires_condition_disables():
    s1 = Scene(id="s1", lines=[Line(text="a")], choices=[
        Choice(id="c", text="locked",
               requires=[__import__("world_gal_game.core.story_graph",
                                     fromlist=["Condition"]).Condition(
                            kind="flag", target="never")]),
    ])
    eng, state = _build_engine([s1])
    eng.start_scene("s1")
    p = eng.next_line()    # consume line, end of lines -> choice phase
    assert p.kind == "choice"
    # the only choice is locked
    assert p.choices[0].enabled is False

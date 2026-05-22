"""Scene-level cg/bgm must also populate the gallery / music-room trackers.

Line-level cg/bgm unlock in _present_line; but most packs set bgm (and
sometimes cg) at the *scene* level, so start_scene unlocks those too —
otherwise the music room would miss nearly every track.
"""
from world_gal_game.core.game_state import GameState
from world_gal_game.core.story_graph import Scene, Line
from world_gal_game.dialogue.dialogue_engine import DialogueEngine


def _engine(scenes: list[Scene]) -> tuple[DialogueEngine, GameState]:
    s = GameState()
    for sc in scenes:
        s.story.add_scene(sc)
    return DialogueEngine(s), s


def test_scene_level_bgm_unlocks_music_room():
    sc = Scene(id="s", bgm="assets/bgm/town_theme.ogg", lines=[Line(text="hi")])
    eng, state = _engine([sc])
    eng.start_scene("s")
    assert "assets/bgm/town_theme.ogg" in state.music_room.unlocked


def test_scene_level_cg_unlocks_gallery():
    sc = Scene(id="s", cg="assets/cgs/title_art.png", lines=[Line(text="hi")])
    eng, state = _engine([sc])
    eng.start_scene("s")
    assert "assets/cgs/title_art.png" in state.cg_gallery.unlocked


def test_line_level_still_unlocks():
    sc = Scene(id="s", lines=[
        Line(text="a", cg="assets/cgs/line_cg.png", bgm="assets/bgm/line_bgm.ogg"),
    ])
    eng, state = _engine([sc])
    eng.start_scene("s")
    assert "assets/cgs/line_cg.png" in state.cg_gallery.unlocked
    assert "assets/bgm/line_bgm.ogg" in state.music_room.unlocked

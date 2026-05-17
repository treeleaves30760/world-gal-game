"""Tests for world_gal_game.core.text_interpolation."""
from world_gal_game.core.game_state import GameState
from world_gal_game.core.text_interpolation import interpolate


def _state(**kwargs) -> GameState:
    s = GameState()
    for k, v in kwargs.items():
        setattr(s, k, v)
    return s


# ---------- player_name -------------------------------------------------------


def test_player_name_basic():
    s = GameState()
    s.player.name = "靜宜"
    assert interpolate("你好，{player_name}！", s) == "你好，靜宜！"


def test_player_name_default():
    s = GameState()
    result = interpolate("{player_name}", s)
    assert result == s.player.name


# ---------- multiple tokens in one string ------------------------------------


def test_multiple_tokens():
    s = GameState()
    s.player.name = "小明"
    s.resources.register(
        __import__(
            "world_gal_game.core.resources", fromlist=["Resource"]
        ).Resource(id="money", starting=100)
    )
    result = interpolate("{player_name} 有 {resource.money} 元", s)
    assert result == "小明 有 100 元"


# ---------- unknown token stays literal ---------------------------------------


def test_unknown_token_stays_literal():
    s = GameState()
    text = "這是 {not_a_real_token} 測試"
    assert interpolate(text, s) == text


def test_partial_unknown_mixed():
    s = GameState()
    s.player.name = "Alice"
    result = interpolate("{player_name} and {bogus}", s)
    assert result == "Alice and {bogus}"


# ---------- state.flag --------------------------------------------------------


def test_state_flag_truthy():
    s = GameState()
    s.events.set_flag("intro_done", "yes")
    assert interpolate("{state.flag.intro_done}", s) == "yes"


def test_state_flag_missing_returns_empty():
    s = GameState()
    assert interpolate("{state.flag.nonexistent}", s) == ""


# ---------- resource ----------------------------------------------------------


def test_resource_token():
    from world_gal_game.core.resources import Resource
    s = GameState()
    s.resources.register(Resource(id="energy", starting=75))
    assert interpolate("體力：{resource.energy}", s) == "體力：75"


# ---------- affection tokens --------------------------------------------------


def test_affection_numeric():
    s = GameState()
    s.affection.adjust("qingyi", 42)
    result = interpolate("{affection.qingyi}", s)
    assert result == "42"


def test_affection_label():
    s = GameState()
    # default label for 0 affection is "陌生"
    result = interpolate("{affection.qingyi.label}", s)
    assert result == "陌生"


def test_affection_label_high():
    s = GameState()
    s.affection.adjust("qingyi", 90)
    result = interpolate("{affection.qingyi.label}", s)
    # >= 80 = "心動"
    assert result == "心動"


# ---------- no tokens — text unchanged ----------------------------------------


def test_no_tokens_passthrough():
    s = GameState()
    text = "普通的旁白，沒有任何插值符號。"
    assert interpolate(text, s) == text


# ---------- regression: speaker field interpolated too -----------------------


def test_speaker_field_interpolated_via_engine():
    """Regression: when line.speaker contains {player_name}, the dialogue
    engine must render the resolved player name in the speaker label,
    not the raw token. Reported visible as '{player_name}' on the
    Tsing-Hua prologue."""
    from world_gal_game.core.story_graph import Scene, Line, StoryGraph
    from world_gal_game.dialogue.dialogue_engine import DialogueEngine

    s = GameState()
    s.player.name = "靜宜"
    s.story = StoryGraph()
    s.story.add_scene(Scene(id="t", lines=[
        Line(speaker="{player_name}", text="（自言自語。）"),
    ]))
    eng = DialogueEngine(s)
    pres = eng.start_scene("t")
    assert pres.line is not None
    assert pres.line.speaker == "靜宜"

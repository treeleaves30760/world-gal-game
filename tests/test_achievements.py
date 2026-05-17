"""Achievement evaluation."""
from world_gal_game.core.achievements import Achievement, AchievementTracker
from world_gal_game.core.game_state import GameState
from world_gal_game.core.story_graph import Condition, Effect


def test_achievement_unlocks_when_flag_set():
    s = GameState()
    ach = Achievement(id="a", title="A", requires=[
        Condition(kind="flag", target="done"),
    ])
    s.achievements.register(ach)
    # Not yet
    s.apply(Effect(kind="set_flag", target="other"))
    assert "a" not in s.achievements.unlocked
    # Flag set → unlocked on next apply_all (because apply_all reevaluates)
    s.apply_all([Effect(kind="set_flag", target="done")])
    assert "a" in s.achievements.unlocked


def test_hidden_achievement_invisible_until_unlocked():
    t = AchievementTracker()
    t.register(Achievement(id="a", title="visible", hidden=False))
    t.register(Achievement(id="b", title="secret", hidden=True))
    visible_ids = [a.id for a in t.visible_to_player()]
    assert "a" in visible_ids
    assert "b" not in visible_ids
    # Once unlocked, the hidden one becomes visible
    t.unlocked["b"] = "2026-01-01"
    visible_ids = [a.id for a in t.visible_to_player()]
    assert "b" in visible_ids


def test_achievement_with_forbids():
    s = GameState()
    s.achievements.register(Achievement(
        id="a", title="A",
        requires=[Condition(kind="flag", target="done")],
        forbids=[Condition(kind="flag", target="cheated")],
    ))
    s.apply_all([
        Effect(kind="set_flag", target="cheated"),
        Effect(kind="set_flag", target="done"),
    ])
    assert "a" not in s.achievements.unlocked

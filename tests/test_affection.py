"""Affection tracker behaviours: thresholds, multi-stat, labels."""
from world_gal_game.core.affection import AffectionTracker, AffectionThreshold


def test_register_and_adjust():
    t = AffectionTracker()
    t.register("alice")
    assert t.get("alice") == 0
    new_val, unlocked = t.adjust("alice", 10)
    assert new_val == 10
    assert unlocked == []


def test_threshold_unlock_fires_once():
    t = AffectionTracker()
    t.register("alice", thresholds=[
        AffectionThreshold(name="friend", value=25, unlocks=["friend_mode"]),
    ])
    _, unlocked = t.adjust("alice", 30)
    assert unlocked == ["friend_mode"]
    # crossing again from a different direction should not re-fire
    _, unlocked2 = t.adjust("alice", 1)
    assert unlocked2 == []


def test_multi_stat_separate():
    t = AffectionTracker()
    t.register("alice", stats={"affection": 0, "trust": 0, "fear": 0})
    t.adjust("alice", 5, "affection")
    t.adjust("alice", 7, "trust")
    t.adjust("alice", -3, "fear")
    assert t.get("alice") == 5
    assert t.get("alice", "trust") == 7
    assert t.get("alice", "fear") == -3


def test_level_labels_default_chinese():
    t = AffectionTracker()
    t.register("alice")
    assert t.level_label("alice") == "陌生"
    t.adjust("alice", 30)
    assert t.level_label("alice") == "朋友"
    t.adjust("alice", 100)
    assert t.level_label("alice") == "戀人"

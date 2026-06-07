"""Affection tracker behaviours: thresholds, multi-stat, labels."""
from world_gal_game.core.affection import (
    AffectionTracker, AffectionThreshold, CharacterAffection,
)
from world_gal_game.core.game_state import GameState
from world_gal_game.core.story_graph import Effect
from world_gal_game.npc.npc_base import NPC, NPCRegistry


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


# --------------------------------------------------------------------------
# Affection legibility (Fix 1): named-threshold crossing detection + toast.
# --------------------------------------------------------------------------
def test_crossed_thresholds_returns_named_tier_just_passed():
    ca = CharacterAffection(character_id="c", thresholds=[
        AffectionThreshold(name="成為朋友", value=25, unlocks=["a"]),
        AffectionThreshold(name="在意你", value=50, unlocks=["b"]),
    ])
    # rising 40 -> 60 crosses only the 50 ("在意你") tier
    crossed = ca.crossed_thresholds(40, 60, "affection")
    assert [th.name for th in crossed] == ["在意你"]
    # rising 0 -> 60 crosses both named tiers in value order
    crossed_both = ca.crossed_thresholds(0, 60, "affection")
    assert [th.name for th in crossed_both] == ["成為朋友", "在意你"]


def test_crossed_thresholds_silent_when_not_rising_or_anonymous():
    ca = CharacterAffection(character_id="c", thresholds=[
        AffectionThreshold(name="在意你", value=50, unlocks=["b"]),
        AffectionThreshold(name="", value=70, unlocks=["c"]),  # anonymous
    ])
    assert ca.crossed_thresholds(60, 70) == []   # anonymous tier stays silent
    assert ca.crossed_thresholds(60, 40) == []   # falling never fires
    assert ca.crossed_thresholds(50, 50) == []   # no movement


def test_apply_all_enqueues_threshold_toast_with_npc_name():
    """An affection effect that crosses a NAMED threshold queues a 'notice'
    toast (name + tier) and surfaces the crossing in apply_all's result."""
    st = GameState()
    st.affection.register("qingyi")
    st.affection.characters["qingyi"].thresholds.append(
        AffectionThreshold(name="在意你", value=50, unlocks=["k"]))
    reg = NPCRegistry()
    reg.add(NPC(id="qingyi", name="林青衣"))
    st.meta["__npc_registry__"] = reg

    out = st.apply_all([Effect(kind="affection", target="qingyi", value=60)])
    toasts = st.meta.get("__pending_toasts__", [])
    assert ("notice", "林青衣", "「在意你」") in toasts
    assert any(r.get("kind") == "affection_threshold"
               and r.get("threshold") == "在意你" for r in out)


def test_apply_all_threshold_toast_only_on_crossing_frame():
    """The toast fires once, on the batch that crosses — not again while the
    stat stays above the threshold."""
    st = GameState()
    st.affection.register("qingyi")
    st.affection.characters["qingyi"].thresholds.append(
        AffectionThreshold(name="在意你", value=50, unlocks=["k"]))
    st.apply_all([Effect(kind="affection", target="qingyi", value=60)])
    st.meta.pop("__pending_toasts__", None)
    # already above 50 — a further bump must not re-toast the tier
    st.apply_all([Effect(kind="affection", target="qingyi", value=5)])
    toasts = st.meta.get("__pending_toasts__", [])
    assert not any(t[0] == "notice" and t[2] == "「在意你」" for t in toasts)


def test_apply_all_threshold_toast_falls_back_to_id_without_registry():
    """No NPC registry / no NPC -> the toast still fires, keyed by character id
    (headless-safe; never crashes)."""
    st = GameState()
    st.affection.register("ghost")
    st.affection.characters["ghost"].thresholds.append(
        AffectionThreshold(name="現身", value=10, unlocks=["x"]))
    st.apply_all([Effect(kind="affection", target="ghost", value=15)])
    toasts = st.meta.get("__pending_toasts__", [])
    assert ("notice", "ghost", "「現身」") in toasts

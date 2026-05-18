"""Unit tests for the gift-affection heuristic.

Exercises `inventory.evaluate_gift` directly (precedence rules) and the
full `gift` effect handler on GameState (inventory removal + affection
delta + event log). Pack-agnostic — does not load any YAML.

Coverage previously supplied by the deleted test_qa_interactions
suite.
"""
from __future__ import annotations

from world_gal_game.core.game_state import GameState
from world_gal_game.core.inventory import Item, evaluate_gift
from world_gal_game.core.story_graph import Effect
from world_gal_game.npc.npc_base import NPC, NPCRegistry


# ---------------------------------------------------------------------------
# evaluate_gift — precedence
# ---------------------------------------------------------------------------


def _npc(**kwargs) -> NPC:
    base = dict(id="heroine_1", name="林清雪",
                likes=[], dislikes=[])
    base.update(kwargs)
    return NPC(**base)


def _item(**kwargs) -> Item:
    base = dict(id="jasmine_tea", name="茉莉花茶",
                category="gift")
    base.update(kwargs)
    return Item(**base)


def test_evaluate_gift_modifier_wins_over_likes():
    """gift_modifier[npc.id] takes precedence over likes match."""
    npc = _npc(likes=["茉莉花茶"])
    item = _item(gift_modifier={"heroine_1": 20})
    # Likes would give +8 but modifier should override.
    assert evaluate_gift(item, npc) == 20


def test_evaluate_gift_modifier_can_be_negative():
    npc = _npc()
    item = _item(gift_modifier={"heroine_1": -7})
    assert evaluate_gift(item, npc) == -7


def test_evaluate_gift_likes_match_by_name():
    npc = _npc(likes=["茉莉花茶"])
    item = _item()
    assert evaluate_gift(item, npc) == 8


def test_evaluate_gift_likes_match_by_id():
    npc = _npc(likes=["jasmine_tea"])
    item = _item()
    assert evaluate_gift(item, npc) == 8


def test_evaluate_gift_likes_match_by_matches_tag():
    npc = _npc(likes=["茶葉"])
    item = _item(matches_tags=["茶葉"])
    assert evaluate_gift(item, npc) == 8


def test_evaluate_gift_dislikes_overrides_likes():
    """If item matches both likes AND dislikes, dislikes wins (penalty)."""
    npc = _npc(likes=["茉莉花茶"], dislikes=["茉莉花茶"])
    item = _item()
    assert evaluate_gift(item, npc) == -5


def test_evaluate_gift_neutral_fallback():
    """Item with no match falls back to +2 (the gesture itself)."""
    npc = _npc()
    item = _item()
    assert evaluate_gift(item, npc) == 2


# ---------------------------------------------------------------------------
# Effect kind=gift end-to-end through GameState
# ---------------------------------------------------------------------------


def _state_with_npc(npc: NPC, item: Item, item_count: int = 1) -> GameState:
    s = GameState()
    s.items.add(item)
    s.inventory.add(item.id, item_count)
    registry = NPCRegistry()
    registry.add(npc)
    s.meta["__npc_registry__"] = registry
    return s


def test_gift_effect_applies_modifier_delta():
    npc = _npc(likes=["茉莉花茶"])
    item = _item(gift_modifier={"heroine_1": 12})
    s = _state_with_npc(npc, item)
    [res] = s.apply_all([
        Effect(kind="gift", target="heroine_1", stat="jasmine_tea")
    ])
    assert res["delta"] == 12
    assert s.affection.get("heroine_1") == 12


def test_gift_effect_consumes_item_by_default():
    """consumed_on_gift=True (default) removes the item from inventory."""
    npc = _npc(likes=["茉莉花茶"])
    item = _item()
    s = _state_with_npc(npc, item, item_count=2)
    s.apply_all([
        Effect(kind="gift", target="heroine_1", stat="jasmine_tea")
    ])
    assert s.inventory.counts.get("jasmine_tea", 0) == 1


def test_gift_effect_keeps_item_when_not_consumed():
    npc = _npc(likes=["茉莉花茶"])
    item = _item(consumed_on_gift=False)
    s = _state_with_npc(npc, item, item_count=2)
    s.apply_all([
        Effect(kind="gift", target="heroine_1", stat="jasmine_tea")
    ])
    assert s.inventory.counts.get("jasmine_tea", 0) == 2


def test_gift_effect_errors_when_item_missing():
    npc = _npc()
    item = _item()
    s = _state_with_npc(npc, item, item_count=0)
    [res] = s.apply_all([
        Effect(kind="gift", target="heroine_1", stat="jasmine_tea")
    ])
    assert res.get("error") == "missing item"
    # Affection must remain at zero.
    assert s.affection.get("heroine_1") == 0


def test_gift_effect_falls_back_when_no_registry():
    """Without an NPCRegistry, the engine still applies a generic +2."""
    item = _item(gift_modifier={"heroine_1": 50})  # would normally win
    s = GameState()
    s.items.add(item)
    s.inventory.add(item.id, 1)
    # No __npc_registry__ in meta.
    [res] = s.apply_all([
        Effect(kind="gift", target="heroine_1", stat="jasmine_tea")
    ])
    # Falls back to the generic +2 without registry lookup.
    assert res["delta"] == 2
    assert s.affection.get("heroine_1") == 2

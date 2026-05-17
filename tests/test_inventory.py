"""Inventory + gift heuristic."""
from world_gal_game.core.inventory import Inventory, Item, ItemRegistry, evaluate_gift
from world_gal_game.npc.npc_base import NPC
from world_gal_game.core.game_state import GameState
from world_gal_game.core.story_graph import Effect


def _make_npc(name="alice", likes=None, dislikes=None) -> NPC:
    return NPC(id=name, name=name, likes=likes or [], dislikes=dislikes or [])


def test_inventory_add_remove_count():
    inv = Inventory()
    inv.add("tea", 2)
    assert inv.count("tea") == 2
    assert inv.has("tea", 2)
    assert not inv.has("tea", 3)
    assert inv.remove("tea", 1)
    assert inv.count("tea") == 1
    assert inv.remove("tea", 1)
    assert inv.count("tea") == 0
    assert not inv.remove("tea", 1)


def test_evaluate_gift_likes_dislikes_neutral():
    flowers = Item(id="flower", name="花束", matches_tags=["flowers", "花"])
    npc_likes = _make_npc(likes=["flowers"])
    npc_dislikes = _make_npc(likes=[], dislikes=["花"])
    npc_neutral = _make_npc()
    assert evaluate_gift(flowers, npc_likes) == 8
    assert evaluate_gift(flowers, npc_dislikes) == -5
    assert evaluate_gift(flowers, npc_neutral) == 2


def test_evaluate_gift_explicit_override_wins():
    book = Item(id="b", name="書", gift_modifier={"alice": 100})
    assert evaluate_gift(book, _make_npc(dislikes=["書"])) == 100


def test_apply_gift_effect_consumes_item_and_changes_affection():
    s = GameState()
    s.items.add(Item(id="tea", name="茶", matches_tags=["茶"]))
    s.inventory.add("tea", 1)
    s.affection.register("alice")
    s.meta["__npc_registry__"] = _registry({"alice": _make_npc(likes=["茶"])})
    r = s.apply(Effect(kind="gift", target="alice", stat="tea"))
    assert r["delta"] == 8
    assert s.affection.get("alice") == 8
    assert s.inventory.count("tea") == 0


def _registry(npcs):
    """Tiny pseudo-registry that mimics NPCRegistry.get()."""
    class _Reg:
        def __init__(self, d): self._d = d
        def get(self, k): return self._d.get(k)
    return _Reg(npcs)

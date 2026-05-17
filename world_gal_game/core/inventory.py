"""Inventory and items.

Items are declared in ``content/items.yaml`` and live in the engine; the
player can pick them up, give them as gifts, or have them consumed by
scenes. The gift mechanic uses each NPC's likes/dislikes (declared in
characters.yaml) to compute an affection delta:

- item appears in ``npc.likes``         -> +8 affection
- item appears in ``npc.dislikes``      -> -5 affection
- otherwise (neutral, but the gesture)  -> +2 affection

Either the delta or the affinity tag can be overridden per item via the
``gift_modifier`` block on the item.
"""
from __future__ import annotations

from typing import Any
from pydantic import BaseModel, ConfigDict, Field

from .story_graph import Effect


class Item(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    description: str = ""
    icon: str | None = None
    # Free-form category — engine doesn't enforce it, but the inventory
    # UI groups by this and game packs can filter by it.
    #   "consumable" — has use_effects, vanishes when used
    #   "gift"       — primarily meant to be given to NPCs
    #   "key"        — story-critical, can't be sold / dropped
    #   "quest"      — temporary, tied to an objective
    #   "material"   — crafting input
    #   "wearable"   — worn / equipped (engine doesn't model equipment
    #                   slots yet; this is a hint for the pack)
    category: str = "misc"
    # If True, "use_item" effect consumes one of these and applies
    # use_effects. If False, use_item is rejected.
    consumable: bool = False
    # Effects applied (in order) when the item is consumed. They run
    # through GameState.apply_all so they nest with all the regular
    # effect kinds: affection, set_flag, gain_resource, advance_time...
    use_effects: list[Effect] = Field(default_factory=list)
    # Sell value in the default currency (used by `sell_item` if no
    # explicit price is given on the effect).
    value: int = 0
    # Optional per-currency price override: {currency_id: price}.
    prices: dict[str, int] = Field(default_factory=dict)
    # Optional per-item rarity tag (engine ignores; useful for filtering
    # in inventory / shop UIs that game packs can build later).
    rarity: str = ""
    # Stackable cap — if non-None, the inventory will refuse to push
    # over this many. None = unbounded.
    max_stack: int | None = None
    # Misc tags for game-side grouping / matching.
    tags: list[str] = Field(default_factory=list)
    stackable: bool = True
    consumed_on_gift: bool = True
    # Explicit affection deltas keyed by character_id; takes priority
    # over the like/dislike heuristic when present.
    gift_modifier: dict[str, int] = Field(default_factory=dict)
    # An item matches NPCs' likes/dislikes through any of these tag
    # strings (in addition to its own id / name).
    matches_tags: list[str] = Field(default_factory=list)
    # If True, blocks "sell_item" / "drop" / "gift" so the player can't
    # accidentally lose a story-critical item.
    locked: bool = False


class ItemRegistry(BaseModel):
    items: dict[str, Item] = Field(default_factory=dict)

    def add(self, item: Item) -> None:
        self.items[item.id] = item

    def get(self, item_id: str) -> Item | None:
        return self.items.get(item_id)

    def all(self) -> list[Item]:
        return list(self.items.values())


class Inventory(BaseModel):
    """Player's pouch of items, mapping item_id -> count."""

    counts: dict[str, int] = Field(default_factory=dict)

    def add(self, item_id: str, n: int = 1, *, max_stack: int | None = None) -> int:
        new = self.counts.get(item_id, 0) + n
        if max_stack is not None:
            new = min(new, max_stack)
        self.counts[item_id] = new
        return self.counts[item_id]

    def remove(self, item_id: str, n: int = 1) -> bool:
        cur = self.counts.get(item_id, 0)
        if cur < n:
            return False
        cur -= n
        if cur <= 0:
            self.counts.pop(item_id, None)
        else:
            self.counts[item_id] = cur
        return True

    def count(self, item_id: str) -> int:
        return self.counts.get(item_id, 0)

    def has(self, item_id: str, n: int = 1) -> bool:
        return self.counts.get(item_id, 0) >= n

    def list_owned(self) -> list[tuple[str, int]]:
        return sorted(self.counts.items())


def evaluate_gift(item: Item, npc) -> int:
    """Compute affection delta for giving ``item`` to ``npc``.

    Order of precedence:
    1. item.gift_modifier[npc.id]   (explicit override)
    2. likes/dislikes match (by name or by tag)
    3. fallback: small positive (the gesture itself)
    """
    if npc.id in item.gift_modifier:
        return int(item.gift_modifier[npc.id])

    name_tokens = {item.name, item.id, *item.tags, *item.matches_tags}
    liked = any(t in npc.likes for t in name_tokens)
    disliked = any(t in npc.dislikes for t in name_tokens)
    if liked and not disliked:
        return 8
    if disliked:
        return -5
    return 2

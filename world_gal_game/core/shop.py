"""Shop / merchant system.

A *Shop* is a declarative listing of items the player can buy in a given
currency. Shops are owned by NPCs (via ``NPC.shop`` field) so they only
become reachable when the player is co-located with that NPC during
their open hours.

Example YAML on a character::

    - id: cafeteria_aunty
      name: "餐廳阿姨"
      shop:
        currency: money
        buy_back_ratio: 0.5     # how much of an item's `value` she pays on resell
        listings:
          - {item: dumpling,        price: 30, stock: -1}   # -1 = unlimited
          - {item: rice_box,        price: 80}
          - {item: cold_brew,       price: 60, stock: 3}    # daily limit
        # Optional restock policy (engine doesn't enforce time — game does
        # via a `restock` effect or a scheduled scene).

Buying invokes ``buy_item`` on GameState (which handles cost + inventory
add); selling invokes ``sell_item``. The :class:`ShopScene` overlay is
the canonical UI but a pack can also drive transactions purely through
scene choices.
"""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class ShopListing(BaseModel):
    model_config = ConfigDict(extra="forbid")

    item: str
    price: int
    stock: int = -1    # -1 = unlimited; otherwise decremented on buy
    requires_flag: str | None = None   # gating — listing hidden until flag set


class Shop(BaseModel):
    model_config = ConfigDict(extra="forbid")

    currency: str = "money"
    listings: list[ShopListing] = Field(default_factory=list)
    # If set, the shop will buy items back from the player. Sell price =
    # round(item.value * buy_back_ratio). Set to 0 to disable buyback.
    buy_back_ratio: float = 0.0
    # An optional short headline shown on top of the shop overlay
    # (e.g. "歡迎來到便當部！").
    greeting: str = ""

    def visible_listings(self, flags: dict) -> list[ShopListing]:
        out: list[ShopListing] = []
        for l in self.listings:
            if l.stock == 0:
                continue
            if l.requires_flag and not flags.get(l.requires_flag):
                continue
            out.append(l)
        return out

    def consume_stock(self, item_id: str) -> bool:
        """Decrement stock on a listing for the given item (if finite).
        Returns True if the sale is allowed."""
        for l in self.listings:
            if l.item == item_id:
                if l.stock < 0:
                    return True
                if l.stock == 0:
                    return False
                l.stock -= 1
                return True
        return False

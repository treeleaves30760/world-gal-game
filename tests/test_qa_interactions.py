"""Scene-interaction tests: NPC actions, shop buy/sell, gifting.

Tests use the driver to navigate to cafeteria, open NPCActionScene for
cafeteria_aunty, exercise the shop, and test gift affection deltas.
"""
import os
import pytest

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

QA_SHOTS = os.path.join(os.path.dirname(__file__), "..", "qa_shots")


@pytest.fixture
def driver_at_cafeteria():
    """Return a driver that has skipped to ExplorationScene at cafeteria,
    at noon (cafeteria_aunty is present noon-evening)."""
    from world_gal_game.dev.driver import GameDriver
    from world_gal_game.core.time_system import TimeOfDay
    d = GameDriver(pack="tsing_hua_strange_tales")
    d.new_game()
    d.skip_dialogue(max_frames=800)
    d.advance_frames(5)

    # Move player to cafeteria via direct state manipulation
    # (avoids fighting the exit-availability system which differs by time).
    d.app.state.map.move_to("cafeteria")
    # Set time to noon so cafeteria_aunty is present.
    d.app.state.time.set_phase(TimeOfDay.NOON)
    d.app.manager.current.resume()
    d.advance_frames(5)

    yield d
    d.quit()


# ---------------------------------------------------------------------------
# NPCActionScene
# ---------------------------------------------------------------------------

def test_npc_action_overlay_opens(driver_at_cafeteria):
    """Clicking cafeteria_aunty's NPC card should open NPCActionScene with
    a '送禮' and '看貨' button visible."""
    driver = driver_at_cafeteria
    snap = driver.snapshot()

    # Confirm we are at cafeteria.
    assert snap["location"] == "cafeteria", (
        f"Expected cafeteria, got {snap['location']!r}"
    )

    # Find the NPC card rect for cafeteria_aunty.
    scene = driver.app.manager.current
    from world_gal_game.scenes.exploration import ExplorationScene
    assert isinstance(scene, ExplorationScene), "Top scene must be ExplorationScene"

    card_rect = None
    for rect, nid in scene._npc_cards:
        if nid == "cafeteria_aunty":
            card_rect = rect
            break

    assert card_rect is not None, (
        "cafeteria_aunty NPC card not found. Present NPCs: "
        + str([nid for _, nid in scene._npc_cards])
    )

    driver.click(card_rect.center)
    driver.advance_frames(10)

    snap2 = driver.snapshot()
    assert snap2["scene_top"] == "NPCActionScene", (
        f"Expected NPCActionScene, got {snap2['scene_top']!r}"
    )

    # Both 送禮 and 看貨 buttons should be visible.
    labels = [w["label"] for w in snap2["widgets"] if w.get("label")]
    assert any("送禮" in lbl for lbl in labels), (
        f"'送禮' button not found in NPCActionScene widgets: {labels}"
    )
    assert any("看貨" in lbl for lbl in labels), (
        f"'看貨' button not found in NPCActionScene widgets: {labels}"
    )

    driver.screenshot(os.path.join(QA_SHOTS, "npc_action_cafeteria.png"))


# ---------------------------------------------------------------------------
# Shop buy: money deducted, inventory increased
# ---------------------------------------------------------------------------

def test_shop_buy_deducts_money(driver_at_cafeteria):
    """Buy a rice_box from cafeteria_aunty. Money should decrease by 80 and
    inventory should gain one rice_box."""
    driver = driver_at_cafeteria
    state = driver.app.state

    # Give the player plenty of money.
    state.resources.set("money", 500)
    money_before = state.resources.get("money")
    inv_before = state.inventory.count("rice_box")

    # Open shop directly via app method (bypasses NPC card click, tests shop logic).
    driver.app._open_shop("cafeteria_aunty")
    driver.app.manager.commit_pending()
    driver.advance_frames(5)

    assert driver.snapshot()["scene_top"] == "ShopScene", (
        "Expected ShopScene after _open_shop"
    )

    # Draw once so the shop populates its _row_rects hit-test list.
    driver.advance_frames(2)

    # Now click buy via state-level effect as a fallback if row-rect click fails.
    # The ShopScene._row_rects are populated during draw(), and our headless
    # surface does render. Try to find and click a buy row.
    from world_gal_game.core.story_graph import Effect
    from world_gal_game.scenes.shop_scene import ShopScene
    shop_scene = driver.app.manager.current
    if isinstance(shop_scene, ShopScene):
        # Attempt screen-coordinate click on the first visible buy row.
        # ShopScene draws rows in its _buy_scroll area starting at panel_rect.y + 110.
        # We simulate the click via the apply_all path which is what the scene's
        # _buy method calls.
        shop_scene._buy("rice_box", 80)
    driver.advance_frames(5)

    money_after = state.resources.get("money")
    inv_after = state.inventory.count("rice_box")

    assert money_after == money_before - 80, (
        f"Money should be {money_before - 80} after buying rice_box (80), "
        f"got {money_after}"
    )
    assert inv_after == inv_before + 1, (
        f"rice_box count should be {inv_before + 1}, got {inv_after}"
    )

    driver.screenshot(os.path.join(QA_SHOTS, "shop_buy.png"))


# ---------------------------------------------------------------------------
# Shop sell: money increases, inventory decreases
# ---------------------------------------------------------------------------

def test_shop_sell_returns_money(driver_at_cafeteria):
    """Sell a rice_box back to cafeteria_aunty (buy_back_ratio=0.5, price=80
    -> sell price=40). Money should increase by 40 and rice_box count decrease."""
    driver = driver_at_cafeteria
    state = driver.app.state

    # Give the player a rice_box to sell.
    state.inventory.add("rice_box", 1)
    state.resources.set("money", 200)
    money_before = state.resources.get("money")
    inv_before = state.inventory.count("rice_box")
    assert inv_before >= 1, "Need at least one rice_box to sell"

    # Open shop.
    driver.app._open_shop("cafeteria_aunty")
    driver.app.manager.commit_pending()
    driver.advance_frames(5)

    from world_gal_game.scenes.shop_scene import ShopScene
    shop_scene = driver.app.manager.current
    assert isinstance(shop_scene, ShopScene), "Expected ShopScene"

    # Expected sell price: 80 * 0.5 = 40
    sell_price = round(80 * 0.5)
    shop_scene._sell("rice_box", sell_price)
    driver.advance_frames(5)

    money_after = state.resources.get("money")
    inv_after = state.inventory.count("rice_box")

    assert money_after == money_before + sell_price, (
        f"Money should be {money_before + sell_price} after selling "
        f"(sell_price={sell_price}), got {money_after}"
    )
    assert inv_after == inv_before - 1, (
        f"rice_box count should be {inv_before - 1}, got {inv_after}"
    )

    driver.screenshot(os.path.join(QA_SHOTS, "shop_sell.png"))


# ---------------------------------------------------------------------------
# Gift: liked item raises affection, disliked item lowers it
# ---------------------------------------------------------------------------

def test_gift_changes_affection(driver_at_cafeteria):
    """Giving qingyi a liked item (jasmine_tea, matches 'teacup'/'茉莉花茶')
    should raise affection; giving her a disliked item (instant_camera,
    matches '拍照') should lower it.

    We apply gifts via state.apply_all() with the 'gift' effect kind, which
    is the same path the NPCActionScene uses.
    """
    driver = driver_at_cafeteria
    state = driver.app.state

    # Ensure qingyi is registered in affection tracker.
    state.affection.register("qingyi")
    aff_before = state.affection.get("qingyi")

    # Give a liked item: jasmine_tea (matches qingyi's "茉莉花茶" like).
    state.inventory.add("jasmine_tea", 1)
    from world_gal_game.core.story_graph import Effect
    results = state.apply_all([
        Effect(kind="gift", target="qingyi", stat="jasmine_tea"),
    ])
    aff_after_like = state.affection.get("qingyi")
    assert aff_after_like > aff_before, (
        f"Affection should increase after gifting liked item; "
        f"before={aff_before}, after={aff_after_like}"
    )

    # Give a disliked item: instant_camera (matches qingyi's "拍照" dislike).
    state.inventory.add("instant_camera", 1)
    results2 = state.apply_all([
        Effect(kind="gift", target="qingyi", stat="instant_camera"),
    ])
    aff_after_dislike = state.affection.get("qingyi")
    assert aff_after_dislike < aff_after_like, (
        f"Affection should decrease after gifting disliked item; "
        f"before gift={aff_after_like}, after={aff_after_dislike}"
    )

    driver.screenshot(os.path.join(QA_SHOTS, "gift_affection.png"))

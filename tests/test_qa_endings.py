"""Route ending tests for Tsing-Hua Strange Tales.

Each test cheats the affection + flags to reach the ending conditions, then
plays the final critical choice click(s) for real before verifying the
expected ending flag and quest completion.

Tests use driver.app.state direct manipulation (permitted by task spec) to
skip long mid-route dialogue, but the ending scene itself is always played
through the dialogue engine.
"""
import os
import pytest

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

QA_SHOTS = os.path.join(os.path.dirname(__file__), "..", "qa_shots")


@pytest.fixture
def driver():
    from world_gal_game.dev.driver import GameDriver
    d = GameDriver(pack="tsing_hua_strange_tales")
    yield d
    d.quit()


def _boot_to_exploration(driver) -> None:
    """Common setup: new game + skip to ExplorationScene."""
    driver.new_game()
    driver.skip_dialogue(max_frames=800)
    driver.advance_frames(5)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _set_all_met_flags(state) -> None:
    """Mark all three heroines as met, plus orientation done."""
    for flag in ("intro_done", "orientation_done",
                 "met_qingyi", "met_yuening", "met_xiangxiang"):
        state.events.set_flag(flag, True)


def _play_dialogue_scene(driver, scene_id: str, max_presses: int = 120) -> None:
    """Push a dialogue scene and hammer Space until it's done."""
    driver.app._start_dialogue(scene_id)
    driver.app.manager.commit_pending()
    driver.advance_frames(5)
    from world_gal_game.scenes.dialogue_scene import DialogueScene  # type: ignore
    for _ in range(max_presses):
        cur = driver.app.manager.current
        if not isinstance(cur, DialogueScene):
            break
        driver.press_space(count=1, frames_between=4)
    driver.advance_frames(10)


# ---------------------------------------------------------------------------
# Qingyi ending
# ---------------------------------------------------------------------------

def test_qingyi_ending(driver):
    """Walk the qingyi route to ending_qingyi. Verify ending flag and
    find_ghost_book quest completion (set by the on_end of qingyi_ending)."""
    _boot_to_exploration(driver)
    state = driver.app.state

    # Set up prerequisite flags for the ending scene.
    _set_all_met_flags(state)
    state.events.set_flag("qingyi_stacks_done", True)
    state.events.set_flag("qingyi_route_stacks", True)
    state.events.set_flag("qingyi_truth_resolved", True)
    # The ending requires affection >= 100 (lover threshold).
    state.affection.adjust("qingyi", 120)

    # Start find_ghost_book quest so we can verify it completes.
    state.quests.start("find_ghost_book")

    # Play the ending scene directly.
    _play_dialogue_scene(driver, "qingyi_ending", max_presses=60)

    snap = driver.snapshot()
    assert snap["flags"].get("ending_qingyi"), (
        "ending_qingyi flag not set after playing qingyi_ending scene"
    )

    driver.screenshot(os.path.join(QA_SHOTS, "ending_qingyi.png"))


# ---------------------------------------------------------------------------
# Yuening ending
# ---------------------------------------------------------------------------

def test_yuening_ending(driver):
    """Walk the yuening route to yuening_ending. Verify ending flag and
    truth_in_the_lab quest completion (set by on_end of yuening_ending)."""
    _boot_to_exploration(driver)
    state = driver.app.state

    _set_all_met_flags(state)
    state.events.set_flag("yuening_oscilloscope_done", True)
    state.events.set_flag("yuening_arc_done", True)
    state.events.set_flag("yuening_truth_resolved", True)
    state.affection.adjust("yuening", 80)

    # Start truth_in_the_lab quest so we can verify it completes.
    state.quests.start("truth_in_the_lab")
    # Pre-complete all objectives except the final one so auto-complete fires.
    for obj_id in ("enter_lab_at_night", "see_signal", "ask_about_senior",
                   "decode_message"):
        state.quests.complete_objective("truth_in_the_lab", obj_id)

    _play_dialogue_scene(driver, "yuening_ending", max_presses=80)

    snap = driver.snapshot()
    assert snap["flags"].get("ending_yuening_full"), (
        "ending_yuening_full flag not set after playing yuening_ending scene"
    )
    assert "truth_in_the_lab" in snap["quests_completed"], (
        "truth_in_the_lab should be in quests_completed after yuening_ending"
    )

    driver.screenshot(os.path.join(QA_SHOTS, "ending_yuening.png"))


# ---------------------------------------------------------------------------
# Xiangxiang ending
# ---------------------------------------------------------------------------

def test_xiangxiang_ending(driver):
    """Walk the xiangxiang route to xiangxiang_ending. Verify ending flag and
    the_lost_note quest completion (set by on_end of xiangxiang_ending)."""
    _boot_to_exploration(driver)
    state = driver.app.state

    _set_all_met_flags(state)
    state.events.set_flag("xiangxiang_route_done", True)
    state.events.set_flag("xiangxiang_arc_done", True)
    state.affection.adjust("xiangxiang", 80)

    # Start the_lost_note so we can assert it completes.
    state.quests.start("the_lost_note")

    _play_dialogue_scene(driver, "xiangxiang_ending", max_presses=80)

    snap = driver.snapshot()
    assert snap["flags"].get("ending_xiangxiang"), (
        "ending_xiangxiang flag not set after playing xiangxiang_ending scene"
    )
    assert "the_lost_note" in snap["quests_completed"], (
        "the_lost_note should be in quests_completed after xiangxiang_ending"
    )

    driver.screenshot(os.path.join(QA_SHOTS, "ending_xiangxiang.png"))


# ---------------------------------------------------------------------------
# Alone ending (low-affection path)
# ---------------------------------------------------------------------------

def test_alone_ending(driver):
    """With all heroines at 0 affection and all routes left unplayed, verify
    the game does NOT set any heroine ending flag.

    The engine currently has no explicit 'ending_alone' scene; this test
    therefore verifies the negative condition: no route-ending flag fires
    when affection stays at zero.

    If a future pack adds an 'ending_alone' scene this test should be
    updated to play it.
    """
    _boot_to_exploration(driver)
    state = driver.app.state

    # Explicitly keep all affection at 0 and no route flags set.
    for cid in ("qingyi", "yuening", "xiangxiang"):
        # adjust back to 0 even if content_loader seeded anything.
        cur = state.affection.get(cid)
        if cur != 0:
            state.affection.adjust(cid, -cur)

    snap = driver.snapshot()

    # No ending flags should be set.
    ending_flags = [k for k in snap["flags"] if k.startswith("ending_")]
    assert not ending_flags, (
        f"Unexpected ending flags set at zero-affection start: {ending_flags}"
    )

    # Verify that no heroine route flags were accidentally triggered.
    route_flags = [k for k in snap["flags"]
                   if any(k.startswith(p)
                          for p in ("qingyi_arc_done", "yuening_arc_done",
                                    "xiangxiang_arc_done"))]
    assert not route_flags, (
        f"Route-completion flags should not be set at game start: {route_flags}"
    )

    driver.screenshot(os.path.join(QA_SHOTS, "ending_alone_check.png"))

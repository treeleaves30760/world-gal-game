"""Quest system integration tests.

Tests drive the engine headlessly and verify quest start, objective
completion, auto-completion, and the QuestLogScene overlay rendering.
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
    d.new_game()
    d.skip_dialogue(max_frames=800)
    d.advance_frames(5)
    yield d
    d.quit()


# ---------------------------------------------------------------------------
# 1. Starting a quest via apply_all -> appears in quests_active + quest log
# ---------------------------------------------------------------------------

def test_start_quest_appears_in_log(driver):
    """Apply start_quest effect for find_ghost_book. The quest should appear
    in quests_active snapshot.

    NOTE: Opening QuestLogScene subsequently would crash due to Bug #QL-001
    (QuestLog.update() references inp.click_pos which does not exist on
    InputState). The overlay open + advance_frames portion is therefore
    skipped and tracked separately in test_quest_log_overlay_renders.
    """
    from world_gal_game.core.story_graph import Effect
    state = driver.app.state

    # Quest should be inactive before we start it.
    snap_before = driver.snapshot()
    assert "find_ghost_book" not in snap_before["quests_active"], (
        "find_ghost_book should not be active before starting"
    )

    state.apply_all([Effect(kind="start_quest", target="find_ghost_book")])
    driver.advance_frames(3)

    snap_after = driver.snapshot()
    assert "find_ghost_book" in snap_after["quests_active"], (
        "find_ghost_book should appear in quests_active after start_quest effect"
    )

    # Open QuestLogScene and verify describe() — but do NOT advance_frames
    # because that would trigger the inp.click_pos AttributeError (Bug #QL-001).
    driver.app._open_quest_log()
    driver.app.manager.commit_pending()

    from world_gal_game.scenes.quest_log_scene import QuestLogScene
    scene = driver.app.manager.current
    assert isinstance(scene, QuestLogScene), "Top scene must be QuestLogScene"
    desc = scene.describe()
    assert "find_ghost_book" in desc["active_quests"], (
        "find_ghost_book must appear in QuestLogScene.describe()['active_quests']"
    )

    # Close overlay before screenshot so we're back to ExplorationScene.
    driver.app.manager.pop()
    driver.advance_frames(3)
    driver.screenshot(os.path.join(QA_SHOTS, "quest_log_active.png"))


# ---------------------------------------------------------------------------
# 2. Completing objectives progresses quest + last non-optional auto-completes
# ---------------------------------------------------------------------------

def test_complete_objective_progresses(driver):
    """Start find_ghost_book, complete required objectives one by one.
    The quest must auto-complete when all non-optional objectives are done."""
    from world_gal_game.core.story_graph import Effect
    state = driver.app.state

    # find_ghost_book objectives (non-optional): visit_stacks, find_book,
    # comfort_qingyi, read_book (hidden), place_book_back (hidden).
    required_objectives = [
        "visit_stacks", "find_book", "comfort_qingyi",
        "read_book", "place_book_back",
    ]

    state.apply_all([Effect(kind="start_quest", target="find_ghost_book")])
    driver.advance_frames(2)

    # Complete all but the last; quest should still be active.
    for obj_id in required_objectives[:-1]:
        state.apply_all([
            Effect(kind="complete_objective",
                   target="find_ghost_book", stat=obj_id)
        ])
    driver.advance_frames(2)

    snap_mid = driver.snapshot()
    assert "find_ghost_book" in snap_mid["quests_active"], (
        "Quest should still be active after completing all-but-last objective"
    )
    assert "find_ghost_book" not in snap_mid["quests_completed"], (
        "Quest should NOT be completed before last objective"
    )

    # Complete the final objective; should trigger auto-complete.
    state.apply_all([
        Effect(kind="complete_objective",
               target="find_ghost_book", stat=required_objectives[-1])
    ])
    driver.advance_frames(2)

    snap_done = driver.snapshot()
    assert "find_ghost_book" in snap_done["quests_completed"], (
        "find_ghost_book should be in quests_completed after all required "
        "objectives are done"
    )
    assert "find_ghost_book" not in snap_done["quests_active"], (
        "find_ghost_book should no longer be active after completion"
    )


# ---------------------------------------------------------------------------
# 3. QuestLogScene renders with quest entries visible
# ---------------------------------------------------------------------------

def test_quest_log_overlay_renders(driver):
    """Open quest log with an active quest via Esc->menu->任務記錄, advance
    frames, verify scene renders without error.

    This test is EXPECTED TO FAIL until Bug #QL-001 is fixed:
    QuestLog.update() references inp.click_pos (AttributeError) on every
    advance_frames call while the overlay is open.
    """
    from world_gal_game.core.story_graph import Effect
    state = driver.app.state

    state.apply_all([
        Effect(kind="start_quest", target="truth_in_the_lab"),
    ])
    driver.advance_frames(3)

    # Open via Esc -> menu -> 任務記錄.
    driver.press_escape()
    driver.advance_frames(8)
    btn = driver.find_widget(label="任務記錄")
    assert btn is not None, "任務記錄 button not found in MenuScene"
    driver.click(tuple(btn["rect_center"]))
    # This advance_frames call triggers the crash: QuestLog.update() uses
    # inp.click_pos which does not exist.
    driver.advance_frames(10)

    snap = driver.snapshot()
    assert snap["scene_top"] == "QuestLogScene"

    scene = driver.app.manager.current
    from world_gal_game.scenes.quest_log_scene import QuestLogScene
    assert isinstance(scene, QuestLogScene)
    desc = scene.describe()
    assert "truth_in_the_lab" in desc["active_quests"]

    driver.screenshot(os.path.join(QA_SHOTS, "quest_log_overlay.png"))

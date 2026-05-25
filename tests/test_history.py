"""Tests for the player-facing rollback buffer (``core/history.py``).

The buffer stores (state snapshot, display payload) checkpoints. ``record`` is
called after each displayed step; the top is the current display; ``rewind``
drops it, restores the previous one into the live state, and returns that
entry's payload. It therefore needs at least two entries to move.
"""
from __future__ import annotations

from world_gal_game.core.game_state import GameState
from world_gal_game.core.history import StateHistory


def test_rewind_walks_back_through_displays() -> None:
    """record P0 -> P1 -> P2, then rewinds walk the trunk backwards."""
    st = GameState()
    st.events.set_flag("a", 1)
    h = StateHistory()
    h.record(st, "P0")              # showing P0 with a=1

    st.events.set_flag("a", 2)
    st.events.set_flag("b", True)
    h.record(st, "P1")              # showing P1 with a=2, b=True

    st.events.set_flag("a", 3)
    h.record(st, "P2")              # showing P2 with a=3 (current)

    # First rewind drops P2, restores P1's state and returns its payload.
    assert h.rewind(st) == "P1"
    assert st.events.get_flag("a") == 2
    assert st.events.get_flag("b") is True

    # Second rewind steps back to P0 -> a=1, b unset.
    assert h.rewind(st) == "P0"
    assert st.events.get_flag("a") == 1
    assert st.events.get_flag("b") is False

    # Only the base display remains: a third rewind returns None, state intact.
    assert h.rewind(st) is None
    assert st.events.get_flag("a") == 1
    assert st.events.get_flag("b") is False


def test_max_entries_cap_drops_oldest() -> None:
    """With max_entries=3, only the 3 most recent checkpoints survive."""
    st = GameState()
    h = StateHistory(max_entries=3)

    for n in range(1, 6):           # record 5 displays (a=1..5), mutate between
        st.events.set_flag("a", n)
        h.record(st, f"P{n}")

    assert h.depth() == 3           # keeps P3, P4, P5

    # Two rewinds succeed (P5->P4->P3); then only the base P3 remains.
    assert h.rewind(st) == "P4"
    assert st.events.get_flag("a") == 4
    assert h.rewind(st) == "P3"
    assert st.events.get_flag("a") == 3
    assert h.rewind(st) is None
    assert h.depth() == 1


def test_can_rewind_needs_two_entries() -> None:
    """can_rewind() is True only with a current display AND a previous one."""
    st = GameState()
    h = StateHistory()

    assert h.can_rewind() is False
    h.record(st, "P0")
    assert h.can_rewind() is False  # one entry: nothing to go back to
    h.record(st, "P1")
    assert h.can_rewind() is True
    assert h.rewind(st) == "P0"
    assert h.can_rewind() is False  # back to one entry


def test_clear_empties_stack() -> None:
    """clear() discards every checkpoint."""
    st = GameState()
    h = StateHistory()
    h.record(st, "P0")
    h.record(st, "P1")
    assert h.depth() == 2

    h.clear()
    assert h.depth() == 0
    assert h.can_rewind() is False


def test_rewind_restores_a_deep_snapshot_not_a_reference() -> None:
    """A rewind restores the values as they were when that display was recorded."""
    st = GameState()
    st.events.set_flag("score", 10)
    h = StateHistory()
    h.record(st, "first")           # snapshot at score=10

    st.events.set_flag("score", 99)
    h.record(st, "second")          # snapshot at score=99 (current)
    assert st.events.get_flag("score") == 99

    # Rewind drops "second" and restores "first" -> score back to 10.
    assert h.rewind(st) == "first"
    assert st.events.get_flag("score") == 10


def test_rollback_restores_engine_position_with_real_presentations() -> None:
    """Integration: drive the real dialogue engine the way DialogueScene does
    (record the presentation after each displayed line), then rewind and
    confirm the engine's line position is restored and the payload comes back.
    Proves rollback works end-to-end with actual ScenePresentation payloads,
    not just synthetic ones."""
    from world_gal_game.config import EngineConfig
    from world_gal_game.headless import HeadlessSession

    sess = HeadlessSession.open(EngineConfig(seed=1), pack="demo_pack")
    eng = sess.dialogue
    h = StateHistory()

    pres = eng.start_scene("prologue")
    h.record(sess.state, pres)            # first display recorded
    indices = [sess.state.story.current_line_index]
    # Advance through lines only; stop before the line that ends the scene
    # (which would reset the index and make idx tracking meaningless).
    for _ in range(3):
        pres = eng.next_line()
        if pres.kind != "line":
            break
        h.record(sess.state, pres)
        indices.append(sess.state.story.current_line_index)
    assert len(indices) >= 3               # recorded several displays
    idx_before = indices[-1]

    assert h.can_rewind() is True
    payload = h.rewind(sess.state)
    assert payload is not None             # got the previous presentation back
    # State was restored to the previous display: same scene, earlier line.
    assert sess.state.story.current_scene == "prologue"
    assert sess.state.story.current_line_index == indices[-2]
    assert sess.state.story.current_line_index < idx_before

"""Tests for the quest system.

Covers:
- QuestTracker CRUD methods
- start_quest / complete_objective / complete_quest / fail_quest effects
- Auto-complete when all required objectives done
- quest_active / quest_completed / objective_completed conditions
- Round-trip JSON serialization
"""
import pytest
from pydantic import ValidationError

from world_gal_game.core.quest import Quest, Objective, QuestTracker
from world_gal_game.core.game_state import GameState
from world_gal_game.core.story_graph import Effect, Condition


# ------------------------------------------------------------------
# Fixtures

def _make_tracker() -> QuestTracker:
    tracker = QuestTracker()
    quest = Quest(
        id="find_ghost_book",
        title="尋找消失的書",
        description="圖書館深處傳來歌聲。",
        objectives=[
            Objective(id="visit_stacks", text="進入特藏書庫"),
            Objective(id="find_book",    text="找到古書"),
            Objective(id="read_book",    text="讀完它", hidden=True),
            Objective(id="bonus_note",   text="找到附贈便條", optional=True),
        ],
    )
    tracker.register(quest)
    return tracker


def _make_state_with_quest() -> GameState:
    state = GameState()
    quest = Quest(
        id="find_ghost_book",
        title="尋找消失的書",
        objectives=[
            Objective(id="visit_stacks", text="進入特藏書庫"),
            Objective(id="find_book",    text="找到古書"),
            Objective(id="bonus_note",   text="選擇性", optional=True),
        ],
    )
    state.quests.register(quest)
    return state


# ------------------------------------------------------------------
# QuestTracker unit tests

class TestQuestTracker:
    def test_register_and_retrieve(self):
        tracker = _make_tracker()
        assert "find_ghost_book" in tracker.quests

    def test_start_changes_status(self):
        tracker = _make_tracker()
        assert tracker.quests["find_ghost_book"].status == "inactive"
        changed = tracker.start("find_ghost_book")
        assert changed is True
        assert tracker.quests["find_ghost_book"].status == "active"

    def test_start_returns_false_when_already_active(self):
        tracker = _make_tracker()
        tracker.start("find_ghost_book")
        assert tracker.start("find_ghost_book") is False

    def test_start_unknown_quest_returns_false(self):
        tracker = _make_tracker()
        assert tracker.start("no_such_quest") is False

    def test_complete_objective(self):
        tracker = _make_tracker()
        tracker.start("find_ghost_book")
        ok = tracker.complete_objective("find_ghost_book", "visit_stacks")
        assert ok is True
        assert tracker.objective_completed("find_ghost_book", "visit_stacks") is True

    def test_complete_objective_idempotent(self):
        tracker = _make_tracker()
        tracker.start("find_ghost_book")
        tracker.complete_objective("find_ghost_book", "visit_stacks")
        # Second call returns False (already done, no change).
        assert tracker.complete_objective("find_ghost_book", "visit_stacks") is False

    def test_objective_completed_false_before_done(self):
        tracker = _make_tracker()
        assert tracker.objective_completed("find_ghost_book", "find_book") is False

    def test_complete_quest(self):
        tracker = _make_tracker()
        tracker.start("find_ghost_book")
        done = tracker.complete("find_ghost_book")
        assert done is True
        assert tracker.is_completed("find_ghost_book") is True

    def test_fail_quest(self):
        tracker = _make_tracker()
        tracker.start("find_ghost_book")
        failed = tracker.fail("find_ghost_book")
        assert failed is True
        assert tracker.quests["find_ghost_book"].status == "failed"

    def test_fail_completed_quest_returns_false(self):
        tracker = _make_tracker()
        tracker.start("find_ghost_book")
        tracker.complete("find_ghost_book")
        assert tracker.fail("find_ghost_book") is False

    def test_is_active(self):
        tracker = _make_tracker()
        assert tracker.is_active("find_ghost_book") is False
        tracker.start("find_ghost_book")
        assert tracker.is_active("find_ghost_book") is True
        tracker.complete("find_ghost_book")
        assert tracker.is_active("find_ghost_book") is False

    def test_active_list(self):
        tracker = _make_tracker()
        assert tracker.active() == []
        tracker.start("find_ghost_book")
        active = tracker.active()
        assert len(active) == 1
        assert active[0].id == "find_ghost_book"

    def test_completed_list(self):
        tracker = _make_tracker()
        tracker.start("find_ghost_book")
        tracker.complete("find_ghost_book")
        completed = tracker.completed()
        assert len(completed) == 1

    def test_all_required_done_ignores_optional(self):
        tracker = _make_tracker()
        tracker.start("find_ghost_book")
        # complete all non-optional
        for obj_id in ("visit_stacks", "find_book", "read_book"):
            tracker.complete_objective("find_ghost_book", obj_id)
        # optional bonus_note is NOT done — should still be True
        assert tracker._all_required_done("find_ghost_book") is True


# ------------------------------------------------------------------
# Effect tests via GameState

class TestQuestEffects:
    def test_start_quest_effect(self):
        state = _make_state_with_quest()
        eff = Effect(kind="start_quest", target="find_ghost_book")
        result = state.apply(eff)
        assert result["started"] is True
        assert state.quests.is_active("find_ghost_book")

    def test_start_quest_effect_already_active(self):
        state = _make_state_with_quest()
        state.quests.start("find_ghost_book")
        eff = Effect(kind="start_quest", target="find_ghost_book")
        result = state.apply(eff)
        assert result["started"] is False

    def test_complete_objective_effect(self):
        state = _make_state_with_quest()
        state.quests.start("find_ghost_book")
        eff = Effect(kind="complete_objective", target="find_ghost_book",
                     stat="visit_stacks")
        result = state.apply(eff)
        assert result["ok"] is True
        assert state.quests.objective_completed("find_ghost_book", "visit_stacks")

    def test_complete_objective_auto_completes_quest(self):
        """Completing all required objectives triggers auto-complete."""
        state = _make_state_with_quest()
        state.quests.start("find_ghost_book")
        state.apply(Effect(kind="complete_objective", target="find_ghost_book",
                           stat="visit_stacks"))
        # Last required objective — auto_completed should flip.
        result = state.apply(Effect(kind="complete_objective",
                                    target="find_ghost_book",
                                    stat="find_book"))
        assert result["auto_completed"] is True
        assert state.quests.is_completed("find_ghost_book")

    def test_complete_objective_optional_does_not_auto_complete(self):
        """Completing only optional objectives should NOT auto-complete."""
        state = _make_state_with_quest()
        state.quests.start("find_ghost_book")
        # Only complete the optional objective; required ones are untouched.
        result = state.apply(Effect(kind="complete_objective",
                                    target="find_ghost_book",
                                    stat="bonus_note"))
        assert result["ok"] is True
        assert result["auto_completed"] is False
        assert not state.quests.is_completed("find_ghost_book")

    def test_complete_quest_effect(self):
        state = _make_state_with_quest()
        state.quests.start("find_ghost_book")
        result = state.apply(Effect(kind="complete_quest",
                                    target="find_ghost_book"))
        assert result["done"] is True
        assert state.quests.is_completed("find_ghost_book")

    def test_fail_quest_effect(self):
        state = _make_state_with_quest()
        state.quests.start("find_ghost_book")
        result = state.apply(Effect(kind="fail_quest", target="find_ghost_book"))
        assert result["failed"] is True
        assert state.quests.quests["find_ghost_book"].status == "failed"

    def test_unknown_quest_start_returns_started_false(self):
        state = _make_state_with_quest()
        result = state.apply(Effect(kind="start_quest", target="no_such"))
        assert result["started"] is False


# ------------------------------------------------------------------
# Condition tests via GameState

class TestQuestConditions:
    def test_quest_active_true_when_active(self):
        state = _make_state_with_quest()
        state.quests.start("find_ghost_book")
        cond = Condition(kind="quest_active", target="find_ghost_book")
        assert state.evaluate(cond) is True

    def test_quest_active_false_when_inactive(self):
        state = _make_state_with_quest()
        cond = Condition(kind="quest_active", target="find_ghost_book")
        assert state.evaluate(cond) is False

    def test_quest_active_false_when_completed(self):
        state = _make_state_with_quest()
        state.quests.start("find_ghost_book")
        state.quests.complete("find_ghost_book")
        cond = Condition(kind="quest_active", target="find_ghost_book")
        assert state.evaluate(cond) is False

    def test_quest_completed_condition(self):
        state = _make_state_with_quest()
        cond = Condition(kind="quest_completed", target="find_ghost_book")
        assert state.evaluate(cond) is False
        state.quests.start("find_ghost_book")
        state.quests.complete("find_ghost_book")
        assert state.evaluate(cond) is True

    def test_objective_completed_condition(self):
        state = _make_state_with_quest()
        state.quests.start("find_ghost_book")
        cond = Condition(kind="objective_completed", target="find_ghost_book",
                         stat="visit_stacks")
        assert state.evaluate(cond) is False
        state.quests.complete_objective("find_ghost_book", "visit_stacks")
        assert state.evaluate(cond) is True

    def test_objective_completed_missing_quest(self):
        state = _make_state_with_quest()
        cond = Condition(kind="objective_completed", target="no_quest",
                         stat="obj")
        assert state.evaluate(cond) is False


# ------------------------------------------------------------------
# Serialization round-trip

class TestQuestSerialization:
    def test_quest_model_dump_validate(self):
        q = Quest(
            id="test_q",
            title="測試",
            objectives=[Objective(id="o1", text="做點什麼", completed=True)],
            status="active",
        )
        data = q.model_dump()
        q2 = Quest.model_validate(data)
        assert q2.id == q.id
        assert q2.status == "active"
        assert q2.objectives[0].completed is True

    def test_tracker_round_trip(self):
        tracker = _make_tracker()
        tracker.start("find_ghost_book")
        tracker.complete_objective("find_ghost_book", "visit_stacks")
        data = tracker.model_dump()
        tracker2 = QuestTracker.model_validate(data)
        assert tracker2.is_active("find_ghost_book")
        assert tracker2.objective_completed("find_ghost_book", "visit_stacks")

    def test_game_state_includes_quests(self):
        state = _make_state_with_quest()
        state.quests.start("find_ghost_book")
        data = state.model_dump()
        assert "quests" in data
        state2 = GameState.model_validate(data)
        assert state2.quests.is_active("find_ghost_book")

    def test_model_config_extra_forbid(self):
        """Pydantic should reject unknown fields."""
        with pytest.raises(ValidationError):
            Quest(id="x", title="y", unknown_field="bad")  # type: ignore[call-arg]

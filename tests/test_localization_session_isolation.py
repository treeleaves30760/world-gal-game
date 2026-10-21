"""Localization services remain private to one state/session.

These regressions intentionally interleave two languages in one process.  A
class/module-global localization slot makes the first state silently adopt the
second state's labels, which is especially damaging to warm headless sessions.
"""
from __future__ import annotations

from world_gal_game.config import EngineConfig
from world_gal_game.core.affection import AffectionTracker
from world_gal_game.core.game_state import GameState
from world_gal_game.core.localization import Localization
from world_gal_game.core.time_system import (
    DayOfWeek,
    TimeOfDay,
    TimeSystem,
    bind_localization as bind_legacy_time_localization,
)
from world_gal_game.dialogue.dialogue_engine import DialogueEngine
from world_gal_game.headless import HeadlessSession
from world_gal_game.npc.llm_brain import EchoBrain
from world_gal_game.npc.npc_base import NPCRegistry


def _locale(prefix: str) -> Localization:
    return Localization.from_meta({
        "locale": {
            "affection_levels": [
                {"min": -999, "label": f"{prefix}-bond"},
            ],
            "time_of_day": {
                "morning": f"{prefix}-morning",
                "night": f"{prefix}-night",
            },
            "day_of_week": {
                "mon": f"{prefix}-monday",
                "tue": f"{prefix}-tuesday",
            },
            "ui": {
                "day_format": (
                    f"{prefix} day {{day}} / {{weekday}} / {{time_of_day}}"
                ),
            },
        },
    })


def _session(localization: Localization) -> HeadlessSession:
    state = GameState()
    state.affection.register("heroine")
    return HeadlessSession(
        config=EngineConfig(),
        state=state,
        npcs=NPCRegistry(),
        brain=EchoBrain(),
        dialogue=DialogueEngine(state, llm_provider=None),
        meta={},
        localization=localization,
    )


def test_affection_localization_is_private_to_each_tracker() -> None:
    left = AffectionTracker()
    right = AffectionTracker()
    left.register("heroine")
    right.register("heroine")

    left.bind_localization(_locale("left"))
    right.bind_localization(_locale("right"))

    assert left.level_label("heroine") == "left-bond"
    assert right.level_label("heroine") == "right-bond"

    # Rebinding either tracker cannot mutate the other tracker.
    right.bind_localization(_locale("new-right"))
    assert left.level_label("heroine") == "left-bond"
    assert right.level_label("heroine") == "new-right-bond"
    assert "_localization" not in left.model_dump(mode="json")


def test_time_system_formats_all_labels_from_its_instance_locale() -> None:
    left = TimeSystem()
    right = TimeSystem()
    left.bind_localization(_locale("left"))
    right.bind_localization(_locale("right"))

    # Changing the legacy enum compatibility binding must not affect either
    # live TimeSystem instance.
    try:
        bind_legacy_time_localization(_locale("legacy"))
        assert left.time_label() == "left-morning"
        assert left.weekday_label() == "left-monday"
        assert left.time_label(TimeOfDay.NIGHT) == "left-night"
        assert left.weekday_label(DayOfWeek.TUE) == "left-tuesday"
        assert left.label() == "left day 1 / left-monday / left-morning"

        assert right.time_label() == "right-morning"
        assert right.weekday_label() == "right-monday"
        assert right.label() == "right day 1 / right-monday / right-morning"
    finally:
        bind_legacy_time_localization(None)

    assert "_localization" not in left.model_dump(mode="json")


def test_legacy_bare_enum_label_api_remains_compatible() -> None:
    try:
        bind_legacy_time_localization(_locale("legacy"))
        assert TimeOfDay.MORNING.label == "legacy-morning"
        assert DayOfWeek.MON.label == "legacy-monday"
    finally:
        bind_legacy_time_localization(None)


def test_two_headless_sessions_do_not_leak_time_or_affection_labels() -> None:
    left = _session(_locale("left"))
    right = _session(_locale("right"))

    assert left.inspect()["time"] == {
        "day": 1,
        "weekday": "mon",
        "weekday_label": "left-monday",
        "time_of_day": "morning",
        "time_label": "left-morning",
        "label": "left day 1 / left-monday / left-morning",
        "is_night": False,
        "is_haunting": False,
    }
    assert right.inspect()["time"]["label"] == (
        "right day 1 / right-monday / right-morning"
    )
    assert left.state.affection.level_label("heroine") == "left-bond"
    assert right.state.affection.level_label("heroine") == "right-bond"

    # Interleave state changes and even mutate the legacy global seam. The
    # already-open sessions must continue to use only their own authority.
    right.state.time.advance(4)
    bind_legacy_time_localization(_locale("legacy"))
    try:
        assert left.inspect()["time"]["label"] == (
            "left day 1 / left-monday / left-morning"
        )
        assert right.inspect()["time"]["time_label"] == "right-night"
        assert left.state.affection.level_label("heroine") == "left-bond"
    finally:
        bind_legacy_time_localization(None)


def test_headless_snapshot_restore_preserves_private_locale_bindings() -> None:
    session = _session(_locale("session"))
    before = session.snapshot()
    session.state.time.advance(4)
    session.state.affection.adjust("heroine", 10)

    session.restore(before)

    assert session.state.time.label() == (
        "session day 1 / session-monday / session-morning"
    )
    assert session.state.affection.level_label("heroine") == "session-bond"
    assert "_localization" not in session.snapshot()["time"]
    assert "_localization" not in session.snapshot()["affection"]

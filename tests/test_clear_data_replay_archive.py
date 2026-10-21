"""Cross-playthrough recollection archive remains first-path monotonic."""

from world_gal_game.core.clear_data import ClearData
from world_gal_game.core.game_state import GameState
from world_gal_game.core.story_graph import Choice, Line, Scene
from world_gal_game.dialogue.dialogue_engine import DialogueEngine


def _state_with_memory() -> GameState:
    state = GameState()
    state.story.add_scene(
        Scene(
            id="memory",
            title="第一次",
            lines=[Line(text="共同正文")],
            choices=[
                Choice(id="left", text="左邊"),
                Choice(id="right", text="右邊"),
            ],
        )
    )
    return state


def _complete(state: GameState, choice_id: str) -> None:
    engine = DialogueEngine(state)
    engine.start_scene("memory")
    engine.next_line()
    engine.choose(choice_id)


def _state_with_stable_memory(
    lines: list[tuple[str, str]],
) -> GameState:
    state = GameState()
    state.story.add_scene(Scene(
        id="stable_memory",
        title="Stable memory",
        lines=[Line(text=text, loc_id=loc_id) for loc_id, text in lines],
    ))
    return state


def _legacy_index_only_stable_memory() -> ClearData:
    state = _state_with_stable_memory([
        ("alpha", "original alpha"),
        ("beta", "original beta"),
    ])
    engine = DialogueEngine(state)
    engine.start_scene("stable_memory")
    engine.next_line()
    engine.next_line()
    entry = state.read_log.replay_entry("stable_memory")
    assert entry is not None
    legacy = entry.model_dump(mode="json")
    for payload in legacy["presentations"]:
        if payload.get("kind") == "line":
            payload["line"].pop("loc_id", None)
    return ClearData(replay_archive={"stable_memory": legacy})


def test_clear_data_roundtrip_carries_complete_first_view_trace(tmp_path):
    original = _state_with_memory()
    _complete(original, "left")
    clear = ClearData()
    assert clear.record_from_state(original)

    path = tmp_path / "clear_data.json"
    clear.save(path)
    restored = ClearData.load(path)
    entry = restored.replay_archive["memory"]
    assert entry.complete is True
    assert entry.selected_choices == ["left"]

    new_game = _state_with_memory()
    assert restored.apply_to_state(new_game)
    assert "memory" in new_game.read_log.scenes
    assert new_game.read_log.replay_entry("memory") is not None
    assert (
        new_game.read_log.replay_entry("memory").selected_choices
        == ["left"]
    )


def test_global_first_trace_overrides_an_older_or_divergent_slot():
    first = _state_with_memory()
    _complete(first, "left")
    clear = ClearData()
    clear.record_from_state(first)

    other_slot = _state_with_memory()
    _complete(other_slot, "right")
    assert other_slot.read_log.replay_entry("memory").selected_choices == [
        "right"
    ]

    clear.apply_to_state(other_slot)
    assert other_slot.read_log.replay_entry("memory").selected_choices == [
        "left"
    ]


def test_global_seen_bit_does_not_promote_a_partial_slot_trace():
    state = _state_with_memory()
    engine = DialogueEngine(state)
    engine.start_scene("memory")
    assert state.read_log.replay_archive["memory"].complete is False

    clear = ClearData(scenes_seen={"memory"})
    clear.apply_to_state(state)

    assert "memory" in state.read_log.scenes
    assert state.read_log.replay_archive["memory"].complete is False
    assert state.read_log.replay_entry("memory") is None


def test_legacy_clear_data_without_trace_stays_non_replayable():
    state = _state_with_memory()
    ClearData(scenes_seen={"memory"}).apply_to_state(state)

    assert "memory" in state.read_log.scenes
    assert state.read_log.replayable_scene_ids() == set()


def test_legacy_replay_identity_hydration_persists_across_later_reorder(
    tmp_path,
):
    clear = _legacy_index_only_stable_memory()
    original_order = _state_with_stable_memory([
        ("alpha", "current alpha"),
        ("beta", "current beta"),
    ])

    clear.apply_to_state(original_order)
    hydrated = clear.replay_archive["stable_memory"]
    assert [
        payload["line"]["loc_id"]
        for payload in hydrated.presentations
        if payload.get("kind") == "line"
    ] == ["alpha", "beta"]

    path = tmp_path / "clear_data.json"
    clear.save(path)
    restored = ClearData.load(path)
    reordered = _state_with_stable_memory([
        ("beta", "beta moved"),
        ("inserted", "new content"),
        ("alpha", "alpha moved"),
    ])

    restored.apply_to_state(reordered)

    assert [
        payload["line"]["loc_id"]
        for payload in restored.replay_archive[
            "stable_memory"
        ].presentations
        if payload.get("kind") == "line"
    ] == ["alpha", "beta"]
    live_entry = reordered.read_log.replay_entry("stable_memory")
    assert live_entry is not None
    assert [
        payload["line"]["loc_id"]
        for payload in live_entry.presentations
        if payload.get("kind") == "line"
    ] == ["alpha", "beta"]


def test_clear_data_drops_replay_when_stable_line_was_deleted(tmp_path):
    clear = _legacy_index_only_stable_memory()
    original_order = _state_with_stable_memory([
        ("alpha", "current alpha"),
        ("beta", "current beta"),
    ])
    clear.apply_to_state(original_order)
    path = tmp_path / "clear_data.json"
    clear.save(path)
    restored = ClearData.load(path)

    deleted = _state_with_stable_memory([
        ("alpha", "alpha remains"),
    ])
    restored.apply_to_state(deleted)

    assert "stable_memory" not in restored.replay_archive
    assert deleted.read_log.replay_entry("stable_memory") is None

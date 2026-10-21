"""Tests for world_gal_game.core.read_log.ReadLog."""
import json

import pytest
from pydantic import ValidationError

from world_gal_game.core.read_log import ReadLog
from world_gal_game.core.story_graph import Line, Scene


def test_mark_line_first_time_returns_true():
    log = ReadLog()
    is_new = log.mark_line("intro", 0)
    assert is_new is True


def test_mark_line_second_time_returns_false():
    log = ReadLog()
    log.mark_line("intro", 0)
    is_new = log.mark_line("intro", 0)
    assert is_new is False


def test_is_read_after_mark():
    log = ReadLog()
    assert not log.is_read("sc1", 3)
    log.mark_line("sc1", 3)
    assert log.is_read("sc1", 3)


def test_different_lines_independent():
    log = ReadLog()
    log.mark_line("sc1", 0)
    assert not log.is_read("sc1", 1)
    assert not log.is_read("sc2", 0)


def test_mark_scene_done():
    log = ReadLog()
    log.mark_scene_done("ch1_intro")
    assert "ch1_intro" in log.scenes
    assert "other" not in log.scenes


# ---------- JSON round-trip ---------------------------------------------------


def test_roundtrip_preserves_lines():
    log = ReadLog()
    log.mark_line("scene_a", 0)
    log.mark_line("scene_a", 5)
    log.mark_line("scene_b", 2)
    log.mark_scene_done("scene_a")

    dumped = log.model_dump()
    # pydantic v2 serialises set -> list in JSON
    raw = json.dumps(dumped)
    restored = ReadLog.model_validate(json.loads(raw))

    assert restored.is_read("scene_a", 0)
    assert restored.is_read("scene_a", 5)
    assert restored.is_read("scene_b", 2)
    assert not restored.is_read("scene_b", 99)
    assert "scene_a" in restored.scenes


def test_roundtrip_empty():
    log = ReadLog()
    dumped = log.model_dump()
    restored = ReadLog.model_validate(dumped)
    assert len(restored.lines) == 0
    assert len(restored.scenes) == 0


def test_validate_from_list_input():
    """model_validate must accept list[str] for lines/scenes (JSON format)."""
    data = {
        "lines": ["sc1:0", "sc1:1"],
        "scenes": ["sc1"],
    }
    log = ReadLog.model_validate(data)
    assert log.is_read("sc1", 0)
    assert log.is_read("sc1", 1)
    assert "sc1" in log.scenes


def test_stable_line_identity_survives_insertion_and_reordering():
    log = ReadLog()
    log.mark_line("scene", 0, loc_id="alpha")
    log.mark_line("scene", 1, loc_id="beta")

    # The authored order becomes beta, a newly inserted line, alpha, gamma.
    # Stable identity skips the moved lines, but never mistakes the new line at
    # beta's former positional index for already-read content.
    assert log.is_read("scene", 0, loc_id="beta")
    assert not log.is_read("scene", 1, loc_id="inserted")
    assert log.is_read("scene", 2, loc_id="alpha")
    assert not log.is_read("scene", 3, loc_id="gamma")
    assert log.lines == {"scene:@alpha", "scene:@beta"}


def test_stable_line_identity_roundtrips_without_positional_alias():
    log = ReadLog()
    assert log.mark_line("scene", 7, loc_id="welcome")

    restored = ReadLog.model_validate_json(log.model_dump_json())

    assert restored.is_read("scene", 999, loc_id="welcome")
    assert not restored.is_read("scene", 7)
    assert restored.lines == {"scene:@welcome"}


def test_legacy_read_log_api_and_payload_remain_positional():
    log = ReadLog.model_validate({"lines": ["legacy:2"]})

    assert log.is_read("legacy", 2)
    assert not log.is_read("legacy", 9)
    assert not log.is_read("legacy", 2, loc_id="line_two")
    assert log.mark_line("legacy", 9)
    assert log.lines == {"legacy:2", "legacy:9"}


@pytest.mark.parametrize("loc_id", ["", "_leading", "has.dot", "a" * 65])
def test_read_log_rejects_unbounded_or_ambiguous_stable_ids(loc_id: str):
    log = ReadLog()

    with pytest.raises(ValueError, match="line loc_id must match"):
        log.mark_line("scene", 0, loc_id=loc_id)
    assert log.lines == set()


def test_scene_rejects_duplicate_line_loc_ids():
    with pytest.raises(ValidationError, match="duplicate line loc_id 'same'"):
        Scene(
            id="duplicate",
            lines=[
                Line(text="first", loc_id="same"),
                Line(text="second", loc_id="same"),
            ],
        )


# ---------- faithful replay archive -----------------------------------------


def _line_payload(scene_id: str, text: str) -> dict:
    return {
        "kind": "line",
        "line": {
            "speaker": None,
            "text": text,
            "plain_text": text,
            "scene_id": scene_id,
            "line_index": 0,
            "total_lines": 1,
        },
        "choices": [],
        "scene_id": scene_id,
        "title": "回想",
        "background": None,
    }


def test_replay_archive_requires_a_complete_non_empty_trace():
    log = ReadLog()
    log.begin_replay_record("memory", "第一次")
    assert log.replay_entry("memory") is None

    log.record_replay_presentation("memory", _line_payload("memory", "原文"))
    assert log.replay_entry("memory") is None

    log.mark_scene_done("memory")
    entry = log.replay_entry("memory")
    assert entry is not None
    assert entry.title == "第一次"
    assert entry.presentations[0]["line"]["text"] == "原文"
    assert log.replayable_scene_ids() == {"memory"}


def test_completed_replay_archive_is_immutable_on_natural_revisit():
    log = ReadLog()
    log.begin_replay_record("memory", "第一次")
    log.record_replay_presentation("memory", _line_payload("memory", "原文"))
    log.mark_scene_done("memory")

    assert log.begin_replay_record("memory", "第二次") is False
    assert not log.record_replay_presentation(
        "memory", _line_payload("memory", "改寫")
    )
    entry = log.replay_entry("memory")
    assert entry is not None
    assert entry.title == "第一次"
    assert [p["line"]["text"] for p in entry.presentations] == ["原文"]


def test_replay_archive_deduplicates_choice_boundary_and_roundtrips():
    log = ReadLog()
    log.begin_replay_record("decision", "選擇")
    payload = {
        "kind": "choice",
        "line": None,
        "choices": [
            {"id": "stay", "text": "留下", "enabled": True, "reason": ""},
            {"id": "go", "text": "離開", "enabled": True, "reason": ""},
        ],
        "scene_id": "decision",
        "title": "選擇",
        "background": None,
    }
    assert log.record_replay_presentation("decision", payload)
    assert not log.record_replay_presentation("decision", payload)
    assert log.record_replay_choice("decision", "stay")
    log.mark_scene_done("decision")

    restored = ReadLog.model_validate_json(log.model_dump_json())
    entry = restored.replay_entry("decision")
    assert entry is not None
    assert entry.selected_choices == ["stay"]
    assert len(entry.presentations) == 1


def test_legacy_completed_scene_without_trace_fails_closed():
    log = ReadLog()
    log.mark_scene_done("legacy")
    assert "legacy" in log.scenes
    assert log.replay_entry("legacy") is None
    assert "legacy" not in log.replayable_scene_ids()


def test_presentation_overflow_never_becomes_a_truncated_complete_trace():
    log = ReadLog()
    log.begin_replay_record("long", "過長")
    for index in range(4097):
        payload = _line_payload("long", f"line {index}")
        payload["line"]["line_index"] = index
        payload["line"]["total_lines"] = 4097
        accepted = log.record_replay_presentation("long", payload)
        assert accepted is (index < 4096)

    log.mark_scene_done("long")
    entry = log.replay_archive["long"]
    assert entry.overflowed is True
    assert entry.complete is False
    assert log.replay_entry("long") is None
    # The engine can still reload its own bounded, fail-closed save.
    restored = ReadLog.model_validate_json(log.model_dump_json())
    assert restored.replay_entry("long") is None


def test_runtime_archive_entry_limit_never_creates_an_unloadable_save():
    log = ReadLog()
    for index in range(4096):
        assert log.begin_replay_record(f"scene_{index}")
    assert not log.begin_replay_record("scene_4096")
    assert len(log.replay_archive) == 4096
    restored = ReadLog.model_validate_json(log.model_dump_json())
    assert len(restored.replay_archive) == 4096


def test_runtime_global_presentation_limit_fails_closed(monkeypatch):
    import world_gal_game.core.read_log as module

    monkeypatch.setattr(module, "_MAX_REPLAY_TOTAL_PRESENTATIONS", 2)
    log = ReadLog()
    for scene_id in ("first", "second"):
        assert log.begin_replay_record(scene_id)
        assert log.record_replay_presentation(
            scene_id,
            _line_payload(scene_id, scene_id),
        )

    assert log.begin_replay_record("overflow")
    assert not log.record_replay_presentation(
        "overflow",
        _line_payload("overflow", "too much"),
    )
    assert log.replay_archive["overflow"].overflowed is True
    # Hitting the global budget must leave a bounded state that still validates.
    restored = ReadLog.model_validate_json(log.model_dump_json())
    assert sum(
        len(entry.presentations)
        for entry in restored.replay_archive.values()
    ) == 2


def test_unknown_archive_format_version_is_rejected():
    payload = {
        "lines": [],
        "scenes": ["memory"],
        "replay_archive": {
            "memory": {
                "format_version": 999,
                "scene_id": "memory",
                "presentations": [_line_payload("memory", "原文")],
                "complete": True,
            }
        },
    }
    with pytest.raises(ValueError):
        ReadLog.model_validate(payload)


@pytest.mark.parametrize(
    ("mutation", "match"),
    [
        (
            lambda item: item["line"].__setitem__("scene_id", "foreign"),
            "another scene",
        ),
        (
            lambda item: item["line"].__setitem__("line_index", 999_999),
            "cursor",
        ),
        (
            lambda item: item["line"].__setitem__("total_lines", -1),
            "cursor",
        ),
        (
            lambda item: item["line"].__setitem__("cg", "/etc/passwd"),
            "unsafe",
        ),
        (
            lambda item: item.__setitem__("unknown", True),
            "unknown fields",
        ),
        (
            lambda item: item["line"].__setitem__("effects_applied", []),
            "unknown or missing fields",
        ),
        (
            lambda item: item["line"].__setitem__(
                "portraits", ["portrait.png"] * 9
            ),
            "too many portraits",
        ),
    ],
)
def test_user_edited_replay_payloads_fail_structural_validation(
    mutation,
    match: str,
):
    item = _line_payload("memory", "原文")
    mutation(item)
    payload = {
        "lines": [],
        "scenes": ["memory"],
        "replay_archive": {
            "memory": {
                "scene_id": "memory",
                "presentations": [item],
                "complete": True,
            }
        },
    }
    with pytest.raises(ValueError, match=match):
        ReadLog.model_validate(payload)


def test_archive_mapping_key_must_match_entry_scene_id():
    payload = {
        "replay_archive": {
            "alias": {
                "scene_id": "memory",
                "presentations": [_line_payload("memory", "原文")],
                "complete": True,
            }
        }
    }
    with pytest.raises(ValueError, match="key does not match"):
        ReadLog.model_validate(payload)

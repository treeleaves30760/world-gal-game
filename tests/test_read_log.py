"""Tests for world_gal_game.core.read_log.ReadLog."""
import json

from world_gal_game.core.read_log import ReadLog


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

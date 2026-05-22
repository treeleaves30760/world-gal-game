"""Music room tracker: unlock semantics + set serialization round-trip."""
import json

from world_gal_game.core.music_room import MusicRoom


def test_unlock_returns_true_first_time_then_false():
    m = MusicRoom()
    assert m.unlock("assets/bgm/theme.ogg") is True
    # Second time the same track is already unlocked.
    assert m.unlock("assets/bgm/theme.ogg") is False
    assert m.is_unlocked("assets/bgm/theme.ogg") is True
    assert m.is_unlocked("assets/bgm/never.ogg") is False


def test_set_serializes_as_sorted_list_in_json():
    m = MusicRoom()
    m.unlock("assets/bgm/b.ogg")
    m.unlock("assets/bgm/a.ogg")
    dumped = m.model_dump()
    # field_serializer turns the set into a sorted list.
    assert dumped["unlocked"] == ["assets/bgm/a.ogg", "assets/bgm/b.ogg"]
    # And the dump is plain-JSON serialisable (no custom encoder needed).
    assert json.loads(json.dumps(dumped)) == dumped


def test_round_trip_preserves_set():
    m = MusicRoom()
    m.unlock("assets/bgm/a.ogg")
    m.unlock("assets/bgm/b.ogg")
    restored = MusicRoom.model_validate(m.model_dump())
    assert restored.unlocked == {"assets/bgm/a.ogg", "assets/bgm/b.ogg"}
    assert isinstance(restored.unlocked, set)
    assert restored.is_unlocked("assets/bgm/a.ogg")

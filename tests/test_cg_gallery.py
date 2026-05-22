"""CG gallery tracker: unlock semantics + set serialization round-trip."""
import json

from world_gal_game.core.cg_gallery import CGGallery


def test_unlock_returns_true_first_time_then_false():
    g = CGGallery()
    assert g.unlock("assets/cgs/lake.png") is True
    # Second time the same path is already unlocked.
    assert g.unlock("assets/cgs/lake.png") is False
    assert g.is_unlocked("assets/cgs/lake.png") is True
    assert g.is_unlocked("assets/cgs/never.png") is False


def test_set_serializes_as_sorted_list_in_json():
    g = CGGallery()
    g.unlock("assets/cgs/b.png")
    g.unlock("assets/cgs/a.png")
    dumped = g.model_dump()
    # field_serializer turns the set into a sorted list.
    assert dumped["unlocked"] == ["assets/cgs/a.png", "assets/cgs/b.png"]
    # And the dump is plain-JSON serialisable (no custom encoder needed).
    assert json.loads(json.dumps(dumped)) == dumped


def test_round_trip_preserves_set():
    g = CGGallery()
    g.unlock("assets/cgs/a.png")
    g.unlock("assets/cgs/b.png")
    restored = CGGallery.model_validate(g.model_dump())
    assert restored.unlocked == {"assets/cgs/a.png", "assets/cgs/b.png"}
    assert isinstance(restored.unlocked, set)
    assert restored.is_unlocked("assets/cgs/a.png")

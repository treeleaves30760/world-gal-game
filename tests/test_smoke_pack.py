"""Smoke test: the shipped Tsinghua Strange Tales pack still plays through."""
from world_gal_game.config import EngineConfig
from world_gal_game.headless import HeadlessSession


def test_pack_loads_and_qingyi_route_completes():
    sess = HeadlessSession.open(EngineConfig(), pack="tsinghua_strange_tales")
    sess.start_scene("prologue_arrival")
    sess.next_line(20)
    sess.move_to("main_gate")
    sess.next_line(20)
    sess.move_to("front_lawn")
    sess.move_to("library")
    sess.start_scene("meet_qingyi")
    sess.next_line(10)
    sess.choose("ask_name")
    sess.next_line(8)
    sess.adjust_affection("qingyi", 55)
    sess.move_to("library_stacks")
    sess.start_scene("qingyi_route_stacks")
    sess.next_line(12)
    sess.choose("protect")
    sess.next_line(8)
    sess.start_scene("qingyi_climax")
    sess.next_line(16)
    sess.choose("place_book")
    sess.next_line(20)
    snap = sess.inspect()
    flags = snap["flags"]
    played = snap["scenes_played"]
    qingyi = next(c for c in snap["all_characters"] if c["id"] == "qingyi")
    assert "ending_qingyi" in flags and flags["ending_qingyi"]
    assert "qingyi_arc_done" in flags and flags["qingyi_arc_done"]
    assert "qingyi_ending" in played
    assert qingyi["affection"] >= 80


def test_pack_loads_and_yuening_route_completes():
    sess = HeadlessSession.open(EngineConfig(), pack="tsinghua_strange_tales")
    # We skip to met_yuening directly and drive only her arc.
    sess.set_flag("met_yuening", True)
    sess.set_flag("yuening_oscilloscope_done", False)
    sess.adjust_affection("yuening", 60)
    sess.start_scene("yuening_climax")
    sess.next_line(16)
    sess.choose("turn_with_her")
    sess.next_line(20)
    snap = sess.inspect()
    flags = snap["flags"]
    assert flags.get("yuening_truth_resolved") is True
    assert flags.get("ending_yuening") is True

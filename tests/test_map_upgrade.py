"""Tests for Map / Location upgrade: multi-time backgrounds, Exit model,
Region grouping, and content_loader YAML parsing."""
from __future__ import annotations

from pathlib import Path
import textwrap
import tempfile
import yaml
import pytest

from world_gal_game.core.map_system import (
    Exit, Location, MapSystem, Region, SceneHook, NPCPresence,
)


# ---------------------------------------------------------------------------
# 1. Location.background_for
# ---------------------------------------------------------------------------

class TestBackgroundFor:
    def test_returns_time_specific_key_when_present(self):
        loc = Location(
            id="lib", name="圖書館",
            background="assets/bg/lib.png",
            backgrounds={"night": "assets/bg/lib_night.png"},
        )
        assert loc.background_for("night") == "assets/bg/lib_night.png"

    def test_falls_back_to_default_when_key_missing(self):
        loc = Location(
            id="lib", name="圖書館",
            background="assets/bg/lib.png",
            backgrounds={"morning": "assets/bg/lib_morning.png"},
        )
        assert loc.background_for("evening") == "assets/bg/lib.png"

    def test_returns_none_when_no_background_at_all(self):
        loc = Location(id="void", name="虛空")
        assert loc.background_for("noon") is None

    def test_all_six_time_slots_resolvable(self):
        slots = ["morning", "noon", "afternoon", "evening", "night", "midnight"]
        bgs = {s: f"assets/bg/loc_{s}.png" for s in slots}
        loc = Location(id="rich", name="豐富地點", backgrounds=bgs)
        for s in slots:
            assert loc.background_for(s) == f"assets/bg/loc_{s}.png"


# ---------------------------------------------------------------------------
# 2. Exit.requires_time availability
# ---------------------------------------------------------------------------

class TestExitRequiresTime:
    def test_exit_available_at_required_time(self):
        ex = Exit(target="secret", requires_time=["night", "midnight"])
        assert ex.is_available("night", {}) is True
        assert ex.is_available("midnight", {}) is True

    def test_exit_unavailable_at_wrong_time(self):
        ex = Exit(target="secret", requires_time=["night", "midnight"])
        assert ex.is_available("morning", {}) is False
        assert ex.is_available("noon", {}) is False

    def test_exit_with_no_time_restriction_always_available(self):
        ex = Exit(target="anywhere")
        for t in ["morning", "noon", "afternoon", "evening", "night", "midnight"]:
            assert ex.is_available(t, {}) is True

    def test_unavailable_reason_includes_times(self):
        ex = Exit(target="cave", requires_time=["night"])
        reason = ex.unavailable_reason("morning")
        assert reason is not None
        assert "night" in reason

    def test_no_reason_when_available(self):
        ex = Exit(target="open", requires_time=["morning"])
        assert ex.unavailable_reason("morning") is None

    def test_exit_blocked_by_required_flag(self):
        ex = Exit(target="vip", requires_flags=["has_vip_pass"])
        assert ex.is_available("noon", {}) is False
        assert ex.is_available("noon", {"has_vip_pass": True}) is True

    def test_exit_blocked_by_forbidden_flag(self):
        ex = Exit(target="clean", forbids_flags=["banned"])
        assert ex.is_available("noon", {"banned": True}) is False
        assert ex.is_available("noon", {}) is True


# ---------------------------------------------------------------------------
# 3. one_way exit: should not appear as a reverse exit
# ---------------------------------------------------------------------------

class TestOneWayExit:
    def _build_system(self) -> MapSystem:
        ms = MapSystem()
        # A -> B one-way; B has no exit back to A
        ms.add_location(Location(
            id="A", name="Alpha",
            exits=[Exit(target="B", one_way=True)],
        ))
        ms.add_location(Location(
            id="B", name="Beta",
            exits=[],   # no reverse exit
        ))
        return ms

    def test_one_way_not_in_reverse_exits(self):
        ms = self._build_system()
        b = ms.get("B")
        assert b is not None
        targets = [e.target for e in b.exits]
        assert "A" not in targets

    def test_forward_exit_exists(self):
        ms = self._build_system()
        a = ms.get("A")
        assert any(e.target == "B" for e in a.exits)

    def test_one_way_flag_is_stored(self):
        ms = self._build_system()
        a = ms.get("A")
        ex = next(e for e in a.exits if e.target == "B")
        assert ex.one_way is True


# ---------------------------------------------------------------------------
# 4. Region grouping
# ---------------------------------------------------------------------------

class TestRegionGrouping:
    def _build(self) -> MapSystem:
        ms = MapSystem()
        ms.add_region(Region(id="campus", name="校園", color=(120, 180, 200)))
        ms.add_region(Region(id="town", name="校外", color=(180, 140, 110)))
        ms.add_location(Location(id="lib", name="圖書館", region="campus"))
        ms.add_location(Location(id="quad", name="廣場", region="campus"))
        ms.add_location(Location(id="cafe", name="咖啡廳", region="town"))
        ms.add_location(Location(id="nowhere", name="虛空"))
        return ms

    def test_locations_by_region_correct_groups(self):
        ms = self._build()
        groups = ms.locations_by_region()
        campus_ids = {l.id for l in groups["campus"]}
        assert campus_ids == {"lib", "quad"}
        town_ids = {l.id for l in groups["town"]}
        assert town_ids == {"cafe"}

    def test_no_region_under_none_key(self):
        ms = self._build()
        groups = ms.locations_by_region()
        none_ids = {l.id for l in groups.get(None, [])}
        assert "nowhere" in none_ids

    def test_region_objects_stored(self):
        ms = self._build()
        assert "campus" in ms.regions
        assert ms.regions["campus"].color == (120, 180, 200)


# ---------------------------------------------------------------------------
# 5. MapSystem.available_exits with time gating
# ---------------------------------------------------------------------------

class TestAvailableExitsTimegating:
    def _build(self) -> MapSystem:
        ms = MapSystem()
        ms.add_location(Location(
            id="hub", name="Hub",
            exits=[
                Exit(target="day_zone", requires_time=["morning", "noon"]),
                Exit(target="night_zone", requires_time=["night", "midnight"]),
                Exit(target="free_zone"),
            ],
        ))
        ms.add_location(Location(id="day_zone", name="日區"))
        ms.add_location(Location(id="night_zone", name="夜區"))
        ms.add_location(Location(id="free_zone", name="自由區"))
        ms.move_to("hub")
        return ms

    def test_morning_shows_day_and_free(self):
        ms = self._build()
        ids = {l.id for l in ms.available_exits({}, "morning")}
        assert "day_zone" in ids
        assert "free_zone" in ids
        assert "night_zone" not in ids

    def test_night_shows_night_and_free(self):
        ms = self._build()
        ids = {l.id for l in ms.available_exits({}, "night")}
        assert "night_zone" in ids
        assert "free_zone" in ids
        assert "day_zone" not in ids

    def test_all_exits_with_status_includes_unavailable(self):
        ms = self._build()
        infos = ms.all_exits_with_status({}, "morning")
        all_targets = {loc.id for _, loc, _, _ in infos}
        assert "night_zone" in all_targets
        night_entry = next(i for i in infos if i[1].id == "night_zone")
        assert night_entry[2] is False   # not available
        assert night_entry[3] is not None


# ---------------------------------------------------------------------------
# 6. content_loader YAML parsing (dict + string form exits, regions)
# ---------------------------------------------------------------------------

class TestContentLoaderParsing:
    """These tests exercise content_loader.load_locations without a GameState
    by importing the internal helpers directly."""

    def _make_yaml(self, content: str) -> Path:
        tmp = tempfile.mktemp(suffix=".yaml")
        Path(tmp).write_text(textwrap.dedent(content), encoding="utf-8")
        return Path(tmp)

    def test_string_form_exits_parse(self):
        from world_gal_game.content_loader import _parse_exits
        exits = _parse_exits(["park", "library"])
        assert len(exits) == 2
        assert exits[0].target == "park"
        assert exits[1].target == "library"
        assert exits[0].one_way is False

    def test_dict_form_exits_parse(self):
        from world_gal_game.content_loader import _parse_exits
        raw = [
            {"target": "secret_stacks", "description": "夜晚才能進入",
             "requires_time": ["night"], "requires_flags": ["met_qingyi"]},
        ]
        exits = _parse_exits(raw)
        assert exits[0].target == "secret_stacks"
        assert exits[0].description == "夜晚才能進入"
        assert exits[0].requires_time == ["night"]
        assert exits[0].requires_flags == ["met_qingyi"]

    def test_mixed_exits_parse(self):
        from world_gal_game.content_loader import _parse_exits
        raw = ["park", {"target": "lab", "one_way": True}]
        exits = _parse_exits(raw)
        assert exits[0].target == "park"
        assert exits[1].target == "lab"
        assert exits[1].one_way is True

    def test_full_load_with_regions_and_dict_exits(self):
        from world_gal_game.core.game_state import GameState
        from world_gal_game.content_loader import load_locations
        yaml_text = """\
            regions:
              - id: campus
                name: "校園"
                color: [120, 180, 200]
              - id: town
                name: "校外"
                color: [180, 140, 110]
            locations:
              - id: library
                name: "圖書館"
                region: campus
                background: assets/bg/lib.png
                backgrounds:
                  morning: assets/bg/lib_morning.png
                  night: assets/bg/lib_night.png
                exits:
                  - main_quad
                  - target: secret_stacks
                    description: "黃昏後請勿進入"
                    requires_time: [evening, night]
                    requires_flags: [met_qingyi]
              - id: main_quad
                name: "中央廣場"
                region: campus
                exits:
                  - library
              - id: secret_stacks
                name: "秘密書庫"
                region: campus
                exits: []
        """
        p = self._make_yaml(yaml_text)
        state = GameState()
        load_locations(p.parent, state)   # expects locations.yaml in parent

        # Override: write as locations.yaml in a fresh temp dir
        import tempfile, shutil
        tmpdir = Path(tempfile.mkdtemp())
        (tmpdir / "locations.yaml").write_text(
            textwrap.dedent(yaml_text), encoding="utf-8"
        )
        state2 = GameState()
        load_locations(tmpdir, state2)
        shutil.rmtree(tmpdir)

        assert "library" in state2.map.locations
        lib = state2.map.locations["library"]
        assert lib.background_for("morning") == "assets/bg/lib_morning.png"
        assert lib.background_for("noon") == "assets/bg/lib.png"  # fallback
        assert lib.background_for("night") == "assets/bg/lib_night.png"
        assert "campus" in state2.map.regions
        assert state2.map.regions["campus"].color == (120, 180, 200)
        # Dict-form exit
        secret_exit = next(e for e in lib.exits if e.target == "secret_stacks")
        assert secret_exit.description == "黃昏後請勿進入"
        assert secret_exit.requires_time == ["evening", "night"]

    def test_backward_compat_string_only_background(self):
        """Old YAML with only background: str still loads correctly."""
        import tempfile, shutil
        yaml_text = """\
            locations:
              - id: old_room
                name: "舊房間"
                background: assets/bg/old.png
                exits:
                  - corridor
              - id: corridor
                name: "走廊"
                exits: []
        """
        tmpdir = Path(tempfile.mkdtemp())
        (tmpdir / "locations.yaml").write_text(
            textwrap.dedent(yaml_text), encoding="utf-8"
        )
        from world_gal_game.core.game_state import GameState
        from world_gal_game.content_loader import load_locations
        state = GameState()
        load_locations(tmpdir, state)
        shutil.rmtree(tmpdir)

        assert "old_room" in state.map.locations
        loc = state.map.locations["old_room"]
        assert loc.background == "assets/bg/old.png"
        assert loc.background_for("night") == "assets/bg/old.png"
        assert loc.backgrounds == {}

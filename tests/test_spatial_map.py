"""Pack-authored illustrated map presentation and directional edge cards."""
from __future__ import annotations

import os
from types import SimpleNamespace

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

import pygame
import pytest

from world_gal_game.core.game_state import GameState
from world_gal_game.core.localization import Localization
from world_gal_game.core.map_system import Exit, Location, SceneHook
from world_gal_game.core.spatial_map import spatial_map_from_meta
from world_gal_game.core.story_graph import Line, Scene
from world_gal_game.scenes.base import SceneContext
from world_gal_game.scenes.destination_picker import DestinationPickerScene
from world_gal_game.scenes.map_scene import MapScene
from world_gal_game.ui.assets import AssetManager
from world_gal_game.ui.fonts import FontRegistry
from world_gal_game.ui.input import InputState
from world_gal_game.ui.system_chrome import ChromeRequest
from world_gal_game.ui.theme import default_theme
from world_gal_game.ui.widgets.map_view import MapView


@pytest.fixture(scope="module", autouse=True)
def _pygame() -> None:
    pygame.init()
    yield
    pygame.quit()


def _node(node_id: str, x: int, y: int, **overrides) -> dict:
    node = {
        "id": node_id,
        "name": node_id.replace("_", " ").title(),
        "region": "campus",
        "region_name": "Campus",
        "region_color": (90, 120, 150),
        "description": f"Visit {node_id}",
        "map_x": x,
        "map_y": y,
        "exits": [],
        "visited": True,
        "accessible": True,
        "offer_status": None,
    }
    node.update(overrides)
    return node


def _save_solid(path, color: tuple[int, int, int]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    surface = pygame.Surface((320, 180))
    surface.fill(color)
    pygame.image.save(surface, path)


def _dense_map_scene(
    tmp_path,
    screen_size: tuple[int, int],
) -> MapScene:
    scale = screen_size[1] / 720
    _save_solid(
        tmp_path / "assets/maps/dense-campus.png",
        (48, 72, 92),
    )
    _save_solid(
        tmp_path / "assets/maps/dense-edge.png",
        (102, 64, 82),
    )
    state = GameState()
    target_ids = [
        "story",
        "edge",
        *(f"ordinary_{index:02d}" for index in range(18)),
    ]
    state.map.add_location(Location(
        id="start",
        name="目前位置",
        map_x=50,
        map_y=50,
        exits=[
            Exit(target=target_id, travel_cost=1)
            for target_id in target_ids
        ],
    ))
    state.map.add_location(Location(
        id="story",
        name="故事人物正在等候",
        description="這裡有一段新的故事。",
        map_x=70,
        map_y=22,
        scene_hooks=[
            SceneHook(scene_id="dense_story", trigger="enter"),
        ],
    ))
    state.map.add_location(Location(
        id="edge",
        name="名稱很長但仍必須安全收進卡片的校外目的地",
        description="校外地點。",
        map_x=100,
        map_y=85,
    ))
    for index in range(18):
        state.map.add_location(Location(
            id=f"ordinary_{index:02d}",
            name=f"一般探索地點 {index:02d} 的完整名稱",
            description=f"第 {index:02d} 個一般地點的簡短介紹。",
            map_x=8 + (index % 6) * 17,
            map_y=14 + (index // 6) * 31,
        ))
    state.story.add_scene(Scene(
        id="dense_story",
        title="Dense story",
        lines=[Line(text="Dense story")],
    ))
    state.map.move_to("start")
    ctx = SceneContext(
        config=SimpleNamespace(
            reduce_motion=True,
            font_size_header=28,
            font_size_small=14,
        ),
        state=state,
        npcs=SimpleNamespace(get=lambda _npc_id: None),
        brain=None,
        dialogue=None,
        assets=AssetManager(pack_root=tmp_path),
        fonts=FontRegistry(candidates=(), scale=scale),
        theme=default_theme(),
        localization=Localization(),
        meta={
            "travel": {
                "scope": "graph",
                "minimum_cost": 1,
                "guided_story": {
                    "future_horizon_phases": 4,
                    "max_primary": 3,
                },
                "spatial_map": {
                    "background": "assets/maps/dense-campus.png",
                    "bounds": [0, 0, 100, 100],
                    "attribution": (
                        "Dense map responsive visual regression fixture"
                    ),
                    "edge_destinations": {
                        "edge": {
                            "direction": "se",
                            "thumbnail": "assets/maps/dense-edge.png",
                            "caption": (
                                "很長的校外方向與說明文字會安全省略"
                            ),
                            "attribution": "Photo © Example",
                        },
                    },
                },
            },
        },
        screen_size=screen_size,
    )
    scene = MapScene(ctx)
    scene.enter(on_close=lambda: None, on_move_to=lambda _target: None)
    scene._toggle_ordinary()
    return scene


def test_spatial_map_meta_parses_fixed_bounds_and_compass_aliases() -> None:
    presentation = spatial_map_from_meta({
        "travel": {
            "spatial_map": {
                "background": " assets/maps/campus.png ",
                "attribution": " Map data attribution ",
                "bounds": [-20, 10, 180, 210],
                "background_fit": "contain",
                "edge_destinations": {
                    "station": {
                        "direction": "North-East",
                        "thumbnail": "assets/maps/station.jpg",
                        "caption": "Rail connection",
                    },
                    "market": {"direction": "south west"},
                    "bad": {"direction": "sideways"},
                },
            },
        },
    })

    assert presentation is not None
    assert presentation.background == "assets/maps/campus.png"
    assert presentation.attribution == "Map data attribution"
    assert presentation.bounds == (-20.0, 10.0, 180.0, 210.0)
    assert presentation.background_fit == "contain"
    assert presentation.edge_destinations["station"].direction == "ne"
    assert presentation.edge_destinations["market"].direction == "sw"
    assert "bad" not in presentation.edge_destinations


def test_absent_config_preserves_legacy_mode_and_bad_bounds_fall_back() -> None:
    assert spatial_map_from_meta({}) is None
    presentation = spatial_map_from_meta({
        "travel": {
            "spatial_map": {
                "bounds": [10, 10, 0, 0],
                "background_fit": "tile",
            },
        },
    })
    assert presentation is not None
    assert presentation.bounds == (0.0, 0.0, 100.0, 100.0)
    assert presentation.background_fit == "cover"
    non_finite = spatial_map_from_meta({
        "travel": {"spatial_map": {"bounds": [0, 0, float("nan"), 100]}},
    })
    assert non_finite is not None
    assert non_finite.bounds == (0.0, 0.0, 100.0, 100.0)


def test_edge_cards_use_compass_perimeter_and_stable_canvas_coordinates(
    tmp_path,
) -> None:
    _save_solid(tmp_path / "assets/maps/campus.png", (180, 70, 40))
    _save_solid(tmp_path / "assets/maps/market.png", (40, 120, 80))
    assets = AssetManager(pack_root=tmp_path)
    presentation = spatial_map_from_meta({
        "travel": {
            "spatial_map": {
                "background": "assets/maps/campus.png",
                "bounds": [0, 0, 100, 100],
                "edge_destinations": {
                    "station": {"direction": "n"},
                    "market": {
                        "direction": "sw",
                        "thumbnail": "assets/maps/market.png",
                        "caption": "Old town",
                    },
                },
            },
        },
    })
    assert presentation is not None
    view = MapView(
        pygame.Rect(20, 20, 960, 520),
        fonts=FontRegistry(candidates=()),
        theme=default_theme(),
        assets=assets,
        reduce_motion=True,
    )
    view.set_presentation(presentation)
    assert view.reduce_motion is True
    view.set_data(
        nodes=[
            _node("origin", 50, 50),
            _node("east_hall", 80, 50),
            _node("station", 20, 20),
            _node("market", 20, 80),
        ],
        current_id="origin",
        reachable_ids={"east_hall", "station", "market"},
    )

    station = view._node_rects["station"]
    market = view._node_rects["market"]
    assert station.top <= view.rect.top + 8
    assert abs(station.centerx - view.rect.centerx) <= 1
    assert market.left <= view.rect.left + 8
    assert market.bottom >= view.rect.bottom - 8
    assert view.canvas_rect.width / view.canvas_rect.height == pytest.approx(
        16 / 9,
        rel=0.02,
    )
    assert not station.colliderect(view.canvas_rect)
    assert not market.colliderect(view.canvas_rect)
    assert view.edge_destination_ids == {"station", "market"}
    assert view.background_available is True
    assert all(
        rect.width >= 44 and rect.height >= 44
        for rect in view._node_rects.values()
    )
    assert all(view.rect.contains(rect) for rect in view._node_rects.values())

    origin_before = view._node_rects["origin"].center
    # Fixed authored bounds prevent a marker from drifting when another
    # currently-hidden story destination changes the visible min/max.
    view.set_data(
        nodes=[
            _node("origin", 50, 50),
            _node("station", 20, 20),
            _node("market", 20, 80),
        ],
        current_id="origin",
        reachable_ids={"station", "market"},
    )
    assert view._node_rects["origin"].center == origin_before

    # Hiding every off-campus marker removes its empty perimeter rail.  The
    # guided story layer is allowed to reflow once when the player explicitly
    # switches layers; within either layer hover/focus never changes geometry.
    edge_canvas = view.canvas_rect
    view.set_data(
        nodes=[_node("origin", 50, 50)],
        current_id="origin",
        reachable_ids=set(),
    )
    assert view.edge_destination_ids == set()
    assert view.canvas_rect.width > edge_canvas.width
    assert abs(view.canvas_rect.centerx - view.rect.centerx) <= 1
    assert abs(view.canvas_rect.centery - view.rect.centery) <= 1
    assert view._node_rects["origin"].center == view.marker_area_rect.center

    surface = pygame.Surface((1024, 576), pygame.SRCALPHA)
    view.draw(surface)
    sample = surface.get_at((
        view.canvas_rect.x + 20,
        view.canvas_rect.y + 20,
    ))
    assert sample.r > sample.g
    assert sample.r > sample.b


def test_edge_card_pointer_and_confirm_use_the_existing_click_callback() -> None:
    chosen: list[str] = []
    presentation = spatial_map_from_meta({
        "travel": {
            "spatial_map": {
                "edge_destinations": {
                    "market": {
                        "direction": "west",
                        "caption": "Off campus",
                    },
                },
            },
        },
    })
    assert presentation is not None
    view = MapView(
        pygame.Rect(0, 0, 800, 480),
        fonts=FontRegistry(candidates=()),
        theme=default_theme(),
        on_click=chosen.append,
    )
    view.set_presentation(presentation)
    view.set_data(
        nodes=[_node("origin", 50, 50), _node("market", 0, 50)],
        current_id="origin",
        reachable_ids={"market"},
    )
    edge_rect = view._node_rects["market"]

    view.update(
        0.016,
        InputState(mouse_pos=edge_rect.center, mouse_clicked=True),
    )
    assert chosen == ["market"]
    assert view.focused_id == "market"

    view.update(0.016, InputState(confirm=True))
    assert chosen == ["market", "market"]


def test_map_view_sends_semantic_marker_edge_and_focus_chrome_requests() -> None:
    class RecordingChrome:
        def __init__(self) -> None:
            self.requests: list[ChromeRequest] = []

        def paint(self, _surface, _rect, request: ChromeRequest) -> bool:
            self.requests.append(request)
            return True

    presentation = spatial_map_from_meta({
        "travel": {
            "spatial_map": {
                "edge_destinations": {
                    "market": {"direction": "nw"},
                },
            },
        },
    })
    assert presentation is not None
    chrome = RecordingChrome()
    view = MapView(
        pygame.Rect(0, 0, 900, 520),
        fonts=FontRegistry(candidates=()),
        theme=default_theme(),
        chrome=chrome,  # type: ignore[arg-type]
    )
    view.set_presentation(presentation)
    view.set_data(
        nodes=[
            _node("origin", 50, 50, marker_kind="current"),
            _node(
                "story",
                70,
                20,
                marker_kind="story",
                offer_status="current",
                travel_available=True,
            ),
            _node(
                "ordinary",
                25,
                70,
                marker_kind="ordinary",
                travel_available=True,
            ),
            _node(
                "market",
                0,
                0,
                marker_kind="off_campus",
                travel_available=True,
            ),
        ],
        current_id="origin",
        reachable_ids={"story", "ordinary", "market"},
    )
    assert view.focus_first({"story"}) == "story"
    view.update(1 / 60, InputState())
    view.draw(pygame.Surface((900, 520), pygame.SRCALPHA))

    card_requests = [
        request for request in chrome.requests
        if request.part == "card"
    ]
    assert {request.variant for request in card_requests} == {
        "marker:current",
        "marker:story",
        "marker:ordinary",
        "edge:off_campus:nw",
    }
    current = next(
        request for request in card_requests
        if request.variant == "marker:current"
    )
    story = next(
        request for request in card_requests
        if request.variant == "marker:story"
    )
    assert current.selected is True
    assert story.focused is True
    assert story.emphasis > 0

    focus_requests = [
        request for request in chrome.requests
        if request.part == "focus"
    ]
    assert [request.variant for request in focus_requests] == [
        "marker:story",
    ]


def test_missing_images_degrade_to_static_canvas_without_placeholder(
    tmp_path,
) -> None:
    presentation = spatial_map_from_meta({
        "travel": {
            "spatial_map": {
                "background": "assets/maps/missing-campus.png",
                "attribution": "Example attribution",
                "edge_destinations": {
                    "market": {
                        "direction": "e",
                        "thumbnail": "assets/maps/missing-market.png",
                    },
                },
            },
        },
    })
    assert presentation is not None
    view = MapView(
        pygame.Rect(0, 0, 800, 480),
        fonts=FontRegistry(candidates=()),
        theme=default_theme(),
        assets=AssetManager(pack_root=tmp_path),
    )
    view.set_presentation(presentation)
    view.set_data(
        nodes=[_node("origin", 50, 50), _node("market", 90, 50)],
        current_id="origin",
        reachable_ids={"market"},
    )

    assert view.background_available is False
    view.draw(pygame.Surface((800, 480), pygame.SRCALPHA))


def test_story_portrait_marker_adds_identity_without_reducing_hit_area(
    tmp_path,
) -> None:
    portrait_path = tmp_path / "assets/portraits/heroine.png"
    _save_solid(portrait_path, (214, 126, 166))
    view = MapView(
        pygame.Rect(0, 0, 1000, 600),
        fonts=FontRegistry(candidates=()),
        theme=default_theme(),
        assets=AssetManager(pack_root=tmp_path),
        reduce_motion=True,
    )
    view.set_data(
        nodes=[
            _node(
                "plain",
                20,
                50,
                marker_kind="story",
                marker_label="現在可出發 · 圖書館",
                offer_status="current",
            ),
            _node(
                "heroine",
                80,
                50,
                name="白若雪",
                marker_kind="story",
                marker_label="現在可出發 · 總圖",
                offer_status="current",
                portrait="assets/portraits/heroine.png",
                marker_color=(74, 150, 176),
            ),
        ],
        current_id=None,
        reachable_ids={"plain", "heroine"},
    )

    plain = view._node_rects["plain"]
    heroine = view._node_rects["heroine"]
    assert heroine.width > plain.width
    assert heroine.height >= plain.height
    assert heroine.width >= view._hit_size
    assert heroine.height >= view._hit_size

    surface = pygame.Surface((1000, 600), pygame.SRCALPHA)
    view.draw(surface)
    horizontal_pad = max(6, round(10 * view.density))
    avatar_pad = max(4, round(5 * view.density))
    avatar_size = min(
        view._portrait_size,
        heroine.height - avatar_pad * 2,
    )
    avatar_center = (
        heroine.x + horizontal_pad + avatar_size // 2,
        heroine.centery,
    )
    assert all(
        abs(actual - expected) <= 3
        for actual, expected in zip(
            surface.get_at(avatar_center)[:3],
            (214, 126, 166),
            strict=True,
        )
    )


def test_map_scene_exposes_spatial_contract_without_changing_move_flow(
    tmp_path,
) -> None:
    state = GameState()
    state.map.add_location(Location(
        id="campus",
        name="Campus",
        map_x=50,
        map_y=50,
        exits=[Exit(target="market")],
    ))
    state.map.add_location(Location(
        id="market",
        name="Market",
        map_x=0,
        map_y=80,
    ))
    state.map.move_to("campus")
    meta = {
        "travel": {
            "spatial_map": {
                "background": "assets/maps/campus.png",
                "bounds": [0, 0, 100, 100],
                "edge_destinations": {
                    "market": {
                        "direction": "sw",
                        "caption": "Off-campus district",
                    },
                },
            },
        },
    }
    ctx = SceneContext(
        config=SimpleNamespace(
            reduce_motion=True,
            font_size_header=28,
            font_size_small=14,
        ),
        state=state,
        npcs=None,
        brain=None,
        dialogue=None,
        assets=AssetManager(pack_root=tmp_path),
        fonts=FontRegistry(candidates=()),
        theme=default_theme(),
        localization=Localization(),
        meta=meta,
        screen_size=(1280, 720),
    )
    moved: list[str] = []
    scene = MapScene(ctx)
    scene.enter(on_close=lambda: None, on_move_to=moved.append)
    assert scene.map_view is not None
    assert scene.map_view.edge_destination_ids == {"market"}
    described = scene.describe()["spatial_map"]
    assert described["enabled"] is True
    assert described["bounds"] == [0.0, 0.0, 100.0, 100.0]
    assert described["edge_destinations"]["market"]["direction"] == "sw"

    scene.map_view._focused_id = "market"
    scene.update(0.016, InputState(confirm=True))
    assert moved == ["market"]
    assert state.map.current_location_id == "campus"


def test_map_scene_uses_authored_labels_and_discloses_interiors_at_parent(
    tmp_path,
) -> None:
    state = GameState()
    state.map.add_location(Location(
        id="campus",
        name="Campus",
        map_x=50,
        map_y=50,
        exits=[
            Exit(target="library"),
            Exit(target="story_only"),
        ],
    ))
    state.map.add_location(Location(
        id="library",
        name="Long Formal Library Name",
        map_x=60,
        map_y=30,
        exits=[
            Exit(target="campus"),
            Exit(target="library_2f"),
        ],
    ))
    state.map.add_location(Location(
        id="library_2f",
        name="Long Formal Reading Room Name",
        map_x=62,
        map_y=28,
        exits=[Exit(target="library")],
    ))
    state.map.add_location(Location(
        id="story_only",
        name="Spoiler Location",
        map_x=70,
        map_y=40,
        exits=[Exit(target="campus")],
    ))
    state.map.move_to("campus")
    meta = {
        "travel": {
            "scope": "graph",
            "spatial_map": {
                "nodes": {
                    "library": {"label": "Library"},
                    "library_2f": {
                        "layer": "interior",
                        "parent": "library",
                        "label": "Reading Room",
                    },
                    "story_only": {"layer": "story_only"},
                },
            },
        },
    }
    ctx = SceneContext(
        config=SimpleNamespace(
            reduce_motion=True,
            font_size_header=28,
            font_size_small=14,
        ),
        state=state,
        npcs=None,
        brain=None,
        dialogue=None,
        assets=AssetManager(pack_root=tmp_path),
        fonts=FontRegistry(candidates=()),
        theme=default_theme(),
        localization=Localization(),
        meta=meta,
        screen_size=(1280, 720),
    )
    scene = MapScene(ctx)
    scene.enter(on_close=lambda: None, on_move_to=lambda _target: None)
    assert scene.map_view is not None
    assert {
        node["id"]: node["name"] for node in scene.map_view.nodes
    } == {
        "campus": "Campus",
        "library": "Library",
    }
    described = scene.describe()["spatial_map"]
    assert described["nodes"]["library_2f"] == {
        "layer": "interior",
        "parent": "library",
        "label": "Reading Room",
    }

    state.map.move_to("library")
    scene.resume()
    assert {
        node["id"]: node["name"] for node in scene.map_view.nodes
    } == {
        "campus": "Campus",
        "library": "Library",
        "library_2f": "Reading Room",
    }
    room = next(
        node for node in scene.map_view.nodes
        if node["id"] == "library_2f"
    )
    assert room["map_layer"] == "interior"
    assert room["map_parent"] == "library"
    assert "story_only" not in scene.describe()["markers"]


def test_guided_interior_story_offer_remains_visible_from_campus(
    tmp_path,
) -> None:
    state = GameState()
    state.map.add_location(Location(
        id="campus",
        name="Campus",
        map_x=50,
        map_y=50,
        exits=[Exit(target="library")],
    ))
    state.map.add_location(Location(
        id="library",
        name="Library",
        map_x=60,
        map_y=30,
        exits=[
            Exit(target="campus"),
            Exit(target="library_2f"),
        ],
    ))
    state.map.add_location(Location(
        id="library_2f",
        name="Library 2F",
        map_x=62,
        map_y=28,
        exits=[Exit(target="library")],
        scene_hooks=[
            SceneHook(scene_id="reading_room_story", trigger="enter"),
        ],
    ))
    state.story.add_scene(Scene(
        id="reading_room_story",
        title="Reading room story",
        lines=[Line(text="Story")],
    ))
    state.map.move_to("campus")
    meta = {
        "travel": {
            "scope": "graph",
            "guided_story": {
                "future_horizon_phases": 4,
                "max_primary": 3,
            },
            "spatial_map": {
                "nodes": {
                    "library_2f": {
                        "layer": "interior",
                        "parent": "library",
                    },
                },
            },
        },
    }
    ctx = SceneContext(
        config=SimpleNamespace(
            reduce_motion=True,
            font_size_header=28,
            font_size_small=14,
        ),
        state=state,
        npcs=SimpleNamespace(get=lambda _npc_id: None),
        brain=None,
        dialogue=None,
        assets=AssetManager(pack_root=tmp_path),
        fonts=FontRegistry(candidates=()),
        theme=default_theme(),
        localization=Localization(),
        meta=meta,
        screen_size=(1280, 720),
    )
    scene = MapScene(ctx)
    scene.enter(on_close=lambda: None, on_move_to=lambda _target: None)

    assert scene.describe()["markers"] == {
        "campus": "current",
        "library_2f": "story",
    }
    assert scene.map_view is not None
    room = next(
        node for node in scene.map_view.nodes
        if node["id"] == "library_2f"
    )
    assert room["map_layer"] == "interior"
    assert room["offer_status"] == "current"


def test_guided_map_hides_unrelated_edges_until_graph_scope_opens(
    tmp_path,
) -> None:
    state = GameState()
    state.map.add_location(Location(
        id="start",
        name="Start",
        map_x=50,
        map_y=50,
        exits=[
            Exit(target="story"),
            Exit(target="edge_open", travel_cost=1),
            Exit(target="edge_spoiler", travel_cost=1),
        ],
    ))
    state.map.add_location(Location(
        id="story",
        name="Story",
        map_x=60,
        map_y=40,
        scene_hooks=[SceneHook(scene_id="story_scene", trigger="enter")],
    ))
    state.map.add_location(Location(
        id="edge_open",
        name="Open District",
        map_x=0,
        map_y=80,
    ))
    state.map.add_location(Location(
        id="edge_blocked",
        name="Blocked District",
        map_x=100,
        map_y=80,
    ))
    state.map.add_location(Location(
        id="edge_spoiler",
        name="Route Spoiler",
        map_x=100,
        map_y=20,
        tags=["story_only"],
    ))
    state.map.add_location(Location(
        id="ordinary_hidden",
        name="Ordinary",
        map_x=40,
        map_y=70,
    ))
    state.story.add_scene(Scene(
        id="story_scene",
        title="Story scene",
        lines=[Line(text="Story")],
    ))
    state.map.move_to("start")
    meta = {
        "travel": {
            "scope": "graph",
            "requires_flags": ["graph_open"],
            "minimum_cost": 1,
            "hidden_tags": ["story_only"],
            "guided_story": {
                "future_horizon_phases": 4,
                "max_primary": 3,
                "direct_before_graph": True,
            },
            "spatial_map": {
                "bounds": [0, 0, 100, 100],
                "edge_destinations": {
                    "edge_open": {
                        "direction": "sw",
                        "caption": "Open edge",
                    },
                    "edge_blocked": {
                        "direction": "se",
                        "caption": "Blocked edge",
                    },
                    "edge_spoiler": {
                        "direction": "ne",
                        "caption": "Hidden route spoiler",
                    },
                },
            },
        },
    }
    ctx = SceneContext(
        config=SimpleNamespace(
            reduce_motion=True,
            font_size_header=28,
            font_size_small=14,
        ),
        state=state,
        npcs=SimpleNamespace(get=lambda _npc_id: None),
        brain=None,
        dialogue=None,
        assets=AssetManager(pack_root=tmp_path),
        fonts=FontRegistry(candidates=()),
        theme=default_theme(),
        localization=Localization(),
        meta=meta,
        screen_size=(1280, 720),
    )
    requested: list[str] = []
    world_map = MapScene(ctx)
    world_map.enter(on_close=lambda: None, on_move_to=requested.append)

    # A directed opening may use the same illustrated map for one current
    # story choice before the wider travel graph opens.  Unrelated edge cards
    # must not compete with that tutorial offer, even when one happens to be
    # directly reachable.
    directed = world_map.describe()
    assert directed["view_mode"] == "story_only"
    assert directed["markers"] == {
        "start": "current",
        "story": "story",
    }
    assert directed["guided_offers"].keys() == {"story"}
    assert world_map.map_view is not None
    assert world_map.map_view.edge_destination_ids == set()
    assert world_map.mode_btn is not None
    assert world_map.mode_btn.visible is False

    # Once the authored graph gate opens, story mode remains focused on the
    # current location and authored offer.  Neutral off-campus cards belong to
    # the explicit ordinary-exploration layer and cannot compete with it.
    state.events.set_flag("graph_open", True)
    world_map.resume()

    described = world_map.describe()
    assert described["view_mode"] == "story_only"
    assert described["markers"] == {
        "start": "current",
        "story": "story",
    }
    assert "edge_open" not in described["guided_offers"]
    assert "edge_blocked" not in described["guided_offers"]
    assert "edge_open" in described["reachable"]
    assert "edge_blocked" not in described["reachable"]
    assert "edge_spoiler" not in described["reachable"]
    assert "edge_spoiler" not in described["markers"]
    assert "ordinary_hidden" not in described["markers"]
    assert world_map.map_view is not None
    assert world_map.map_view.edge_destination_ids == set()
    assert world_map.mode_btn is not None
    assert world_map.mode_btn.visible is True

    world_map._toggle_ordinary()
    described = world_map.describe()
    assert described["view_mode"] == "all_reachable"
    assert described["markers"] == {
        "start": "current",
        "story": "story",
        "edge_open": "off_campus",
        "edge_blocked": "off_campus",
    }
    assert world_map.map_view.edge_destination_ids == {
        "edge_open",
        "edge_blocked",
    }
    edge_nodes = {
        node["id"]: node
        for node in world_map.map_view.nodes
        if node["id"].startswith("edge_")
    }
    assert edge_nodes["edge_open"]["accessible"] is True
    assert edge_nodes["edge_open"]["travel_available"] is True
    assert edge_nodes["edge_open"]["marker_label"] == "現在可前往"
    assert edge_nodes["edge_blocked"]["accessible"] is False
    assert edge_nodes["edge_blocked"]["travel_available"] is False
    assert edge_nodes["edge_blocked"]["marker_label"] == "尚未開放"
    assert described["marker_details"]["edge_blocked"] == {
        "kind": "off_campus",
        "label": "尚未開放",
        "travel_available": False,
        "blocked_reason": "目前沒有可行路線",
        "portrait": None,
    }

    hidden_detail = DestinationPickerScene(ctx)
    hidden_detail.enter(
        on_close=lambda: None,
        on_travel=lambda _plan: None,
        initial_destination="edge_spoiler",
        detail_only=True,
    )
    hidden_description = hidden_detail.describe()
    assert hidden_description["selected"] is None
    assert "edge_spoiler" not in {
        row["id"] for row in hidden_description["destinations"]
    }

    before = state.model_dump(mode="json")
    world_map._on_node_click("edge_open")
    assert requested == ["edge_open"]
    assert state.model_dump(mode="json") == before

    # Detail mode quotes only the clicked marker. The primary story layer
    # remains on the map beneath it and unrelated offers are not re-planned.
    plans = []
    detail = DestinationPickerScene(ctx)
    detail.enter(
        on_close=lambda: None,
        on_travel=plans.append,
        initial_destination="edge_open",
        detail_only=True,
    )
    detail_description = detail.describe()
    assert detail_description["selected"] == "edge_open"
    assert {
        row["id"] for row in detail_description["destinations"]
    } == {"edge_open"}
    assert "edge_blocked" not in {
        row["id"] for row in detail_description["destinations"]
    }
    assert (
        detail_description["travel_previews"]["edge_open"]["path"]
        == ["start", "edge_open"]
    )

    detail._confirm()
    assert len(plans) == 1
    assert plans[0].destination_id == "edge_open"
    assert state.model_dump(mode="json") == before


@pytest.mark.parametrize(
    ("screen_size", "expected_density"),
    [
        ((1138, 640), 640 / 720),
        ((1280, 720), 1.0),
        ((1920, 1080), 1.5),
        ((3840, 2160), 3.0),
    ],
)
def test_dense_map_is_responsive_layered_and_screenshot_safe(
    tmp_path,
    monkeypatch,
    screen_size: tuple[int, int],
    expected_density: float,
) -> None:
    scene = _dense_map_scene(tmp_path, screen_size)
    view = scene.map_view
    assert view is not None
    assert scene.mode_btn is not None
    assert scene.close_btn is not None
    assert view.reduce_motion is True
    assert scene._density == expected_density
    assert view.density == expected_density

    # Measured header rows and scaled controls own separate vertical bands.
    chrome_safe_bottom = scene._panel_rect.top + max(
        24,
        round(28 * expected_density),
    )
    assert min(
        scene._title_rect.top,
        scene.mode_btn.rect.top,
        scene.close_btn.rect.top,
        view.legend_rect.top,
    ) >= chrome_safe_bottom
    assert scene._panel_rect.contains(scene._title_rect)
    assert scene._panel_rect.contains(scene._hint_rect)
    assert scene._panel_rect.contains(scene.mode_btn.rect)
    assert scene._panel_rect.contains(scene.close_btn.rect)
    assert scene._panel_rect.contains(view.rect)
    assert not scene._title_rect.colliderect(scene.mode_btn.rect)
    assert not scene._title_rect.colliderect(scene.close_btn.rect)
    assert not scene.mode_btn.rect.colliderect(scene.close_btn.rect)
    assert max(
        scene._title_rect.bottom,
        scene.mode_btn.rect.bottom,
        scene.close_btn.rect.bottom,
    ) <= scene._hint_rect.top
    assert scene._hint_rect.bottom <= view.rect.top
    assert scene.mode_btn.rect.height >= round(44 * expected_density)
    assert scene.close_btn.rect.height >= round(44 * expected_density)
    assert scene.mode_btn.rect.height >= 44
    assert scene.close_btn.rect.height >= 44
    assert view.legend_visible is True
    assert scene._panel_rect.contains(view.legend_rect)
    assert view.legend_rect.bottom <= view.rect.top
    assert not view.legend_rect.colliderect(scene._title_rect)
    assert not view.legend_rect.colliderect(scene.mode_btn.rect)
    assert not view.legend_rect.colliderect(scene.close_btn.rect)
    assert all(
        not rect.colliderect(view.legend_rect)
        for rect in view._node_rects.values()
    )

    ordinary_nodes = [
        node for node in view.nodes
        if node.get("marker_kind") == "ordinary"
    ]
    assert len(ordinary_nodes) == 18
    expected_hit = max(44, round(44 * expected_density))
    for node in ordinary_nodes:
        hit_rect = view._node_rects[node["id"]]
        visual_rect = view.ordinary_pin_rect(node["id"])
        assert hit_rect.size == (expected_hit, expected_hit)
        assert view.rect.contains(hit_rect)
        assert visual_rect is not None
        assert visual_rect.width < hit_rect.width
        assert visual_rect.height < hit_rect.height
        assert hit_rect.contains(visual_rect)

    assert view.canvas_rect.contains(view.information_strip_rect)
    assert view.marker_area_rect.bottom < view.information_strip_rect.top
    story_rect = view._node_rects["story"]
    assert story_rect.height >= (
        view.fonts.get(view._label_size, bold=True).get_linesize()
        + view.fonts.get(view._sub_label_size).get_linesize()
        + round(10 * expected_density)
    )
    edge_rect = view._node_rects["edge"]
    edge_vertical_pad = max(4, round(5 * expected_density))
    assert edge_rect.height >= (
        view.fonts.get(16, bold=True).get_linesize()
        + view.fonts.get(12).get_linesize()
        + edge_vertical_pad * 2
        + max(1, round(expected_density))
    )

    target = ordinary_nodes[0]
    target_id = target["id"]
    target_name = target["name"]
    # Expanding the ordinary layer selects exactly one authored-order target
    # so the detail strip is useful immediately without labeling every pin.
    assert view.focused_id == target_id
    assert view.detail_node_id == target_id
    view.focus_first(())
    assert view.focused_id is None
    rendered_text: list[str] = []
    real_render = view.fonts.render

    def recording_render(text, *args, **kwargs):
        rendered_text.append(str(text))
        return real_render(text, *args, **kwargs)

    monkeypatch.setattr(view.fonts, "render", recording_render)
    line_calls: list[tuple] = []
    monkeypatch.setattr(
        pygame.draw,
        "line",
        lambda *args, **_kwargs: line_calls.append(args),
    )

    # Ordinary names are absent from the map until hover/focus disclosure.
    surface = pygame.Surface(screen_size, pygame.SRCALPHA)
    scene.draw(surface)
    assert target_name not in rendered_text
    assert "Photo © Example" in rendered_text
    assert view.detail_node_id is None
    assert line_calls == []
    assert {
        "故事地點",
        "目前位置",
        "一般地點",
        "校外地點",
        "操作焦點",
    }.issubset(rendered_text)

    # The explanatory row is passive chrome outside the map. Clicking it
    # cannot select a destination or mutate the persistent map focus.
    chosen: list[str] = []
    view.on_click = chosen.append
    focus_before_legend_click = view.focused_id
    view.update(
        0.016,
        InputState(
            mouse_pos=view.legend_rect.center,
            mouse_clicked=True,
        ),
    )
    assert chosen == []
    assert view.focused_id == focus_before_legend_click

    draw_order: list[tuple[str, str]] = []
    real_ordinary = view._draw_ordinary_pin
    real_edge = view._draw_edge_card
    real_labeled = view._draw_labeled_marker

    def recording_ordinary(surface, node, rect):
        draw_order.append(("ordinary", node["id"]))
        return real_ordinary(surface, node, rect)

    def recording_edge(surface, node, rect):
        draw_order.append(("edge", node["id"]))
        return real_edge(surface, node, rect)

    def recording_labeled(surface, node, rect):
        draw_order.append(("labeled", node["id"]))
        return real_labeled(surface, node, rect)

    monkeypatch.setattr(view, "_draw_ordinary_pin", recording_ordinary)
    monkeypatch.setattr(view, "_draw_edge_card", recording_edge)
    monkeypatch.setattr(view, "_draw_labeled_marker", recording_labeled)
    view._focused_id = target_id
    rendered_text.clear()
    surface.fill((0, 0, 0, 0))
    scene.draw(surface)

    assert target_name in rendered_text
    assert view.detail_node_id == target_id
    ordinary_before_focus = [
        index
        for index, row in enumerate(draw_order)
        if row[0] == "ordinary" and row[1] != target_id
    ]
    labeled = [
        index
        for index, row in enumerate(draw_order)
        if row[0] == "labeled"
    ]
    assert ordinary_before_focus
    assert labeled
    assert max(ordinary_before_focus) < min(labeled)
    assert draw_order[-1] == ("ordinary", target_id)

    # Pointer hover is authoritative; a story hover suppresses the ordinary
    # keyboard-focus disclosure instead of showing two competing targets.
    view._hover_id = "story"
    assert view.detail_node_id is None
    view._hover_id = None

    # The full hit target, including a visually empty corner, remains active.
    chosen.clear()
    hit_rect = view._node_rects[target_id]
    view.update(
        0.016,
        InputState(
            mouse_pos=(hit_rect.left + 1, hit_rect.top + 1),
            mouse_clicked=True,
        ),
    )
    assert chosen == [target_id]

    horizontal_pad = max(8, round(10 * expected_density))
    edge_node = next(node for node in view.nodes if node["id"] == "edge")
    fitted_name = view._fit_text(
        edge_node["name"],
        16,
        edge_rect.width - horizontal_pad * 2,
        bold=True,
    )
    fitted_caption = view._fit_text(
        (
            f"{edge_node['marker_label']} · "
            "很長的校外方向與說明文字會安全省略"
        ),
        12,
        edge_rect.width - horizontal_pad * 2,
    )
    assert (
        real_render(
            fitted_name,
            16,
            view.theme.text,
            bold=True,
        ).get_width()
        <= edge_rect.width - horizontal_pad * 2
    )
    assert (
        real_render(
            fitted_caption,
            12,
            view.theme.text_mute,
        ).get_width()
        <= edge_rect.width - horizontal_pad * 2
    )

    screenshot = tmp_path / f"dense-map-{screen_size[1]}p.png"
    pygame.image.save(surface, screenshot)
    assert screenshot.exists()
    assert screenshot.stat().st_size > 5000


def test_overlapping_marker_hit_priority_matches_visual_z_order() -> None:
    chosen: list[str] = []
    view = MapView(
        pygame.Rect(0, 0, 800, 480),
        fonts=FontRegistry(candidates=()),
        theme=default_theme(),
        on_click=chosen.append,
    )
    view.set_data(
        nodes=[
            _node(
                "ordinary",
                50,
                50,
                marker_kind="ordinary",
                marker_label="一般探索",
            ),
            _node(
                "story",
                50,
                50,
                marker_kind="story",
                marker_label="故事地點",
                offer_status="current",
            ),
        ],
        current_id=None,
        reachable_ids={"ordinary", "story"},
    )
    assert (
        view._node_rects["ordinary"].center
        == view._node_rects["story"].center
    )

    overlap = view._node_rects["ordinary"].center
    view.update(
        0.016,
        InputState(mouse_pos=overlap, mouse_clicked=True),
    )
    assert chosen == ["story"]

    view._focused_id = "ordinary"
    view.update(
        0.016,
        InputState(mouse_pos=overlap, mouse_clicked=True),
    )
    assert chosen == ["story", "ordinary"]


def test_focused_ordinary_strip_names_region_purpose_and_use(
    monkeypatch,
) -> None:
    view = MapView(
        pygame.Rect(0, 0, 800, 480),
        fonts=FontRegistry(candidates=()),
        theme=default_theme(),
    )
    view.set_data(
        nodes=[
            _node(
                "ordinary",
                50,
                50,
                name="學生餐廳",
                region_name="北校區",
                marker_kind="ordinary",
                marker_label="一般探索",
                description="用餐與打聽消息\n也能交換情報",
            ),
        ],
        current_id=None,
        reachable_ids={"ordinary"},
    )
    assert view.focus_first({"ordinary"}) == "ordinary"

    rendered_text: list[str] = []
    real_render = view.fonts.render

    def recording_render(text, *args, **kwargs):
        rendered_text.append(str(text))
        return real_render(text, *args, **kwargs)

    monkeypatch.setattr(view.fonts, "render", recording_render)
    view.draw(pygame.Surface((800, 480), pygame.SRCALPHA))

    assert "學生餐廳" in rendered_text
    assert any(
        all(
            part in text
            for part in (
                "北校區",
                "一般探索",
                "用餐與打聽消息",
                "也能交換情報",
            )
        )
        for text in rendered_text
    )
    assert all("\n" not in text for text in rendered_text)


def test_map_scene_localizes_chrome_status_legend_progress_and_bindings(
    tmp_path,
    monkeypatch,
) -> None:
    localization = Localization()
    localization.ui.update({
        "map": "Campus Map",
        "close": "Close",
        "menu_item_with_binding": "{label} [{binding}]",
        "map_show_ordinary_exploration": "Show Free Exploration",
        "map_story_locations_only": "Story Locations Only",
        "map_available_now": "Available Now",
        "map_current_location": "Current Location",
        "map_depart_after_time_slots": "Depart in {count} Time Slots",
        "map_depart_after_wait": "Depart After Waiting",
        "map_depart_now": "Depart Now",
        "map_focus": "Input Focus",
        "map_hint_guided_all": (
            "Legend: story, current, and exploration locations · "
            "Select to review · {focus}/{controller} Top controls"
        ),
        "map_hint_guided_story": (
            "Story locations first · Waiting markers cannot depart yet · "
            "{focus}/{controller} Top controls"
        ),
        "map_hint_progress": (
            "{label} · {stage} · {status} · Markers show departure timing · "
            "{focus}/{controller} Top controls"
        ),
        "map_hint_standard": (
            "Legend: location status · {nav} Select · {confirm} Review · "
            "{focus}/{controller} Top controls"
        ),
        "map_marker_status": "{location} — {status}",
        "map_no_viable_route": "No Viable Route",
        "map_not_yet_available": "Not Yet Available",
        "map_off_campus_location": "Off-Campus Location",
        "map_ordinary_exploration": "Free Exploration",
        "map_ordinary_location": "Exploration Location",
        "map_progress_complete": "Complete",
        "map_progress_count": "{completed} of {total}",
        "map_progress_pending_wrap": (
            "{completed} of {total} · Awaiting Round Wrap"
        ),
        "map_story_location": "Story Location",
    })
    state = GameState()
    state.map.add_location(Location(
        id="start",
        name="Starting Point",
        map_x=50,
        map_y=50,
        exits=[
            Exit(target="story_now", travel_cost=1),
            Exit(target="story_future", travel_cost=1),
            Exit(target="ordinary", travel_cost=1),
            Exit(target="edge_open", travel_cost=1),
        ],
    ))
    state.map.add_location(Location(
        id="story_now",
        name="Library",
        map_x=60,
        map_y=30,
        scene_hooks=[SceneHook(scene_id="story_now_scene", trigger="enter")],
    ))
    state.map.add_location(Location(
        id="story_future",
        name="Music Hall",
        map_x=75,
        map_y=45,
        scene_hooks=[SceneHook(
            scene_id="story_future_scene",
            trigger="enter",
            requires_time=["afternoon"],
        )],
    ))
    state.map.add_location(Location(
        id="ordinary",
        name="Cafeteria",
        map_x=35,
        map_y=70,
    ))
    state.map.add_location(Location(
        id="edge_open",
        name="Railway Station",
        map_x=0,
        map_y=80,
    ))
    state.map.add_location(Location(
        id="edge_blocked",
        name="Old Town",
        map_x=100,
        map_y=80,
    ))
    for scene_id in ("story_now_scene", "story_future_scene"):
        state.story.add_scene(Scene(
            id=scene_id,
            title=scene_id,
            lines=[Line(text=scene_id)],
        ))
    state.map.move_to("start")
    meta = {
        "travel": {
            "scope": "graph",
            "minimum_cost": 1,
            "guided_story": {
                "future_horizon_phases": 4,
                "max_primary": 3,
                "progress": {
                    "label": "Freshman Guide",
                    "stages": [{
                        "label": "First Round",
                        "flags": ["met_now", "met_future"],
                    }],
                },
            },
            "spatial_map": {
                "edge_destinations": {
                    "edge_open": {"direction": "sw"},
                    "edge_blocked": {"direction": "se"},
                },
            },
        },
    }
    config = SimpleNamespace(
        reduce_motion=True,
        font_size_header=28,
        font_size_small=14,
        input_bindings={
            "back": ["b"],
            "focus_next": ["q"],
            "confirm_advance": ["p"],
            "nav_up": ["r"],
            "nav_down": ["f"],
            "nav_left": ["j"],
            "nav_right": ["k"],
        },
    )
    ctx = SceneContext(
        config=config,
        state=state,
        npcs=SimpleNamespace(get=lambda _npc_id: None),
        brain=None,
        dialogue=None,
        assets=AssetManager(pack_root=tmp_path),
        fonts=FontRegistry(candidates=()),
        theme=default_theme(),
        localization=localization,
        meta=meta,
        screen_size=(1280, 720),
    )
    scene = MapScene(ctx)
    scene.enter(on_close=lambda: None, on_move_to=lambda _target: None)

    assert scene._title_text == "Campus Map"
    assert scene.close_btn is not None
    assert scene.close_btn.label == "Close [B]"
    assert scene.mode_btn is not None
    assert scene.mode_btn.label == "Show Free Exploration"
    assert scene.map_view is not None
    assert scene.map_view.legend_labels == {
        "story": "Story Location",
        "current": "Current Location",
        "ordinary": "Exploration Location",
        "off_campus": "Off-Campus Location",
        "focus": "Input Focus",
    }

    nodes = {node["id"]: node for node in scene.map_view.nodes}
    assert nodes["start"]["marker_label"] == "Current Location"
    assert nodes["story_now"]["marker_label"] == "Library — Depart Now"
    wait_phases = nodes["story_future"]["future_wait_phases"]
    assert nodes["story_future"]["marker_label"] == (
        f"Music Hall — Depart in {wait_phases} Time Slots"
    )
    assert "ordinary" not in nodes
    assert "edge_open" not in nodes
    assert "edge_blocked" not in nodes
    assert scene._hint_text() == (
        "Freshman Guide · First Round · 0 of 2 · "
        "Markers show departure timing · Q/Y Top controls"
    )

    scene._toggle_ordinary()
    assert scene.mode_btn.label == "Story Locations Only"
    nodes = {node["id"]: node for node in scene.map_view.nodes}
    assert nodes["ordinary"]["marker_label"] == "Free Exploration"
    assert nodes["edge_open"]["marker_label"] == "Available Now"
    assert nodes["edge_blocked"]["marker_label"] == "Not Yet Available"
    assert nodes["edge_blocked"]["blocked_reason"] == "No Viable Route"
    assert scene.map_view.legend_visible is True

    rendered_text: list[str] = []
    rendered_calls: list[tuple[str, int]] = []
    original_render = scene.ctx.fonts.render

    def _record_render(text, size, *args, **kwargs):
        rendered_text.append(str(text))
        rendered_calls.append((str(text), int(size)))
        return original_render(text, size, *args, **kwargs)

    monkeypatch.setattr(scene.ctx.fonts, "render", _record_render)
    scene.draw(pygame.Surface((1280, 720), pygame.SRCALPHA))
    assert {
        "Campus Map",
        "Story Location",
        "Current Location",
        "Exploration Location",
        "Off-Campus Location",
        "Input Focus",
    }.issubset(rendered_text)
    legend_labels = [
        "Story Location",
        "Current Location",
        "Exploration Location",
        "Off-Campus Location",
        "Input Focus",
    ]
    rendered_text.clear()
    rendered_calls.clear()
    scene.map_view._draw_legend(
        pygame.Surface((1280, 720), pygame.SRCALPHA),
    )
    assert rendered_text == legend_labels
    assert all(size >= 16 for _text, size in rendered_calls)
    assert scene.map_view.legend_font_size == 16
    item_rects = scene.map_view.legend_item_rects
    assert set(item_rects) == set(scene.map_view.LEGEND_KINDS)
    assert all(
        scene.map_view.legend_rect.contains(item_rect)
        for item_rect in item_rects.values()
    )
    item_values = list(item_rects.values())
    assert all(
        not left.colliderect(right)
        for index, left in enumerate(item_values)
        for right in item_values[index + 1:]
    )

    assert scene._guide_progress is not None
    scene._guide_progress.update({
        "completed": 2,
        "total": 2,
        "complete": False,
    })
    assert "2 of 2 · Awaiting Round Wrap" in scene._hint_text()
    scene._guide_progress["complete"] = True
    assert "Complete" in scene._hint_text()

    scene._guide_progress = None
    assert scene._hint_text() == (
        "Legend: story, current, and exploration locations · "
        "Select to review · Q/Y Top controls"
    )
    scene._show_ordinary = False
    assert scene._hint_text() == (
        "Story locations first · Waiting markers cannot depart yet · "
        "Q/Y Top controls"
    )
    scene._guided_enabled = False
    assert scene._hint_text() == (
        "Legend: location status · R / F / J / K Select · P Review · "
        "Q/Y Top controls"
    )

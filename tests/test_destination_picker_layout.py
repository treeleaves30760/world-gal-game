"""Responsive containment contracts for destination cards and detail sheets."""
from __future__ import annotations

import os

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

import pygame
import pytest

from world_gal_game.config import EngineConfig
from world_gal_game.core.game_state import GameState
from world_gal_game.core.guided_destinations import (
    DestinationEvent,
    DestinationFuture,
    DestinationOffer,
)
from world_gal_game.core.localization import Localization
from world_gal_game.core.map_system import Exit, Location
from world_gal_game.core.story_graph import Line, Scene
from world_gal_game.core.travel import TravelMoment
from world_gal_game.npc.npc_base import NPC, NPCRegistry
from world_gal_game.scenes.base import SceneContext
from world_gal_game.scenes.destination_picker import (
    DestinationPickerScene,
    _CARD_H,
    _PREVIEW_H,
    _Dest,
)
from world_gal_game.ui.assets import AssetManager
from world_gal_game.ui.fonts import FontRegistry
from world_gal_game.ui.input import InputState
from world_gal_game.ui.theme import default_theme


_LONG_TEASER = (
    "你在遠處看見熟悉的人影，她似乎正在等誰，卻又不時望向校舍的鐘。"
    "走近之前，先確認這是不是你現在最想追查的線索與相遇。"
)
_LONG_REASON = (
    "你還沒有完成新生指引中的校園導覽，也尚未向學姊確認社團博覽會的集合時間；"
    "先回到目前的主要行程，完成眼前事件後，這個地點就會依照故事進度正式開放。"
    "這段完整說明只應出現在確認面板，卡片上只能顯示簡短狀態。"
)


@pytest.fixture(scope="module", autouse=True)
def _pygame_init():
    pygame.display.init()
    pygame.font.init()
    yield
    pygame.quit()


def _make_picker(
    size: tuple[int, int],
    tmp_path,
    *,
    localization: Localization | None = None,
    input_bindings: dict[str, list[str]] | None = None,
):
    state = GameState()
    state.map.add_location(Location(id="start", name="校門口"))
    state.map.move_to("start")

    npcs = NPCRegistry()
    for index in range(3):
        npc = NPC(
            id=f"heroine_{index}",
            name=f"女主角{index + 1}",
            name_color=("#ff91c5" if index == 0 else None),
            role=f"新生指引協助者 {index + 1}",
            is_heroine=index == 0,
        )
        npcs.add(npc)

    hook_ids: list[str] = []
    for index in range(3):
        scene_id = f"event_{index}"
        hook_ids.append(scene_id)
        state.story.add_scene(Scene(
            id=scene_id,
            title=f"很長的事件標題 {index + 1}：鐘聲後的約定與未解線索",
            lines=[Line(text="測試")],
        ))

    config = EngineConfig(
        screen_size=size,
        input_bindings=input_bindings or {},
    )
    fonts = FontRegistry(
        candidates=config.font_candidates,
        scale=max(1.0, size[1] / 720.0),
    )
    localization = localization or Localization()
    state.time.bind_localization(localization)
    state.affection.bind_localization(localization)
    ctx = SceneContext(
        config=config,
        state=state,
        npcs=npcs,
        brain=None,  # type: ignore[arg-type]
        dialogue=None,  # type: ignore[arg-type]
        assets=AssetManager(pack_root=tmp_path),
        fonts=fonts,
        theme=default_theme(),
        localization=localization,
        screen_size=size,
    )
    picker = DestinationPickerScene(ctx)
    picker._recompute_geometry()

    destinations: list[_Dest] = []
    statuses = (
        ("locked", "none", False, _LONG_REASON),
        ("future", "wait", True, "再等一個時段即可安排。"),
        ("current", "travel", True, None),
    )
    for index, (status, action, available, reason) in enumerate(statuses):
        loc = Location(
            id=f"destination_{index}",
            name=f"櫻丘校園地點 {index + 1}",
            description=_LONG_TEASER,
        )
        state.map.add_location(loc)
        event = DestinationEvent(
            scene_id=hook_ids[index],
            title=f"事件主題 {index + 1}：她在鐘聲之後留下的訊息與邀請",
            npc_ids=tuple(npcs.npcs),
            teaser=_LONG_TEASER,
        )
        offer = DestinationOffer(
            target=loc.id,
            visibility="primary",
            status=status,  # type: ignore[arg-type]
            action=action,  # type: ignore[arg-type]
            available=available,
            plan=None,
            event=event,
            future=(
                DestinationFuture(
                    wait_phases=3,
                    departure=TravelMoment(
                        day=1,
                        weekday_index=0,
                        phase_index=3,
                    ),
                    arrival=TravelMoment(
                        day=1,
                        weekday_index=0,
                        phase_index=4,
                    ),
                )
                if action == "wait" else None
            ),
            blocked_reason=reason,
            state_revision="layout-test",
        )
        destinations.append(_Dest(
            loc,
            Exit(target=loc.id, travel_cost=index),
            available,
            reason,
            list(npcs.npcs),
            hook_ids,
            hook_ids,
            True,
            index,
            None,
            offer,
        ))

    picker._layout(destinations)
    picker._build_header_buttons()
    return picker, destinations


def _english_destination_localization() -> Localization:
    localization = Localization()
    localization.ui.update({
        "cancel": "Cancel",
        "close": "Close",
        "day_format": "Day {day} · {weekday} · {time_of_day}",
        "destination_arrangement_hint_reason": "Planning note: {reason}",
        "destination_arrival_available": "Available After Arrival",
        "destination_arrival_characters_heading": "Expected to Be Here",
        "destination_arrival_starts": "Starts After Arrival",
        "destination_arrive_immediately": "Arrive Immediately",
        "destination_badge": "● {label}",
        "destination_cannot_plan": "This Cannot Be Scheduled Yet",
        "destination_cannot_travel_reason": "Cannot travel: {reason}",
        "destination_character_affection": (
            "{name}  Affection {level} · {value}"
        ),
        "destination_current_location": "You are at: {location}",
        "destination_empty_exits": "There Are No Other Exits Here",
        "destination_empty_filtered": "No Destinations Match These Filters",
        "destination_expand_all_regions": "Expand All Regions",
        "destination_inspect_conditions": "Review Availability Requirements",
        "destination_inspect_details": "Review Details",
        "destination_itinerary_arrival": (
            "Expected arrival: {location} · {time}"
        ),
        "destination_itinerary_cost": "Cost: {cost}",
        "destination_itinerary_now": "Now: {location} · {time}",
        "destination_itinerary_route": "Route: {route}",
        "destination_later_event": "Later Event",
        "destination_location_info": "Location Information",
        "destination_new_event": "New Event",
        "destination_no_characters_here": "No Characters Are Here Right Now",
        "destination_no_triggerable_events": "No Events Can Be Triggered Here",
        "destination_other_region": "Other Areas",
        "destination_reachable_only": "Show Reachable Destinations Only",
        "destination_title": "Choose Your Next Destination",
        "destination_travel": "Travel Now",
        "destination_trip_time_slots": "Travel Time: {count} Slots",
        "destination_unset": "Not Set",
        "destination_wait_one_slot": "Wait One Time Slot",
        "destination_wait_until": (
            "Wait Until Day {day}, {time_of_day} ({count} Slots)"
        ),
        "destination_with_characters_only": (
            "Show Destinations with Characters"
        ),
        "destination_world_map": "Open Campus World Map",
        "destination_cost_future": (
            "Depart after waiting {wait} slots; travel takes {cost} slots"
        ),
        "destination_cost_instant": "Immediate; no time slots used",
        "destination_cost_slots": "{count} time slots",
        "map_depart_after_time_slots": "Depart in {count} Time Slots",
        "map_not_yet_available": "Not Yet Available",
        "map_ordinary_location": "Exploration Location",
        "menu_item_with_binding": "{label} ({binding})",
        "no_one_here": "No one is here.",
    })
    localization.day_of_week.update({
        "mon": "Mon",
        "tue": "Tue",
        "wed": "Wed",
        "thu": "Thu",
        "fri": "Fri",
        "sat": "Sat",
        "sun": "Sun",
    })
    localization.time_of_day.update({
        "morning": "Morning",
        "noon": "Noon",
        "afternoon": "Afternoon",
        "evening": "Evening",
        "night": "Night",
        "midnight": "Late Night",
    })
    return localization


@pytest.mark.parametrize("size", [(1280, 720), (1920, 1080)])
def test_story_cards_scale_equally_and_clip_everything(size, tmp_path) -> None:
    picker, destinations = _make_picker(size, tmp_path)
    density = size[1] / 720.0

    assert picker._cols == 3
    assert len(picker._cards) == 3
    assert {destination.crect.size for destination in destinations} == {
        (picker._card_w, round(_CARD_H * density))
    }
    assert picker._thumb_h == round(104 * density)
    assert picker._card_gap == round(18 * density)
    assert picker._filter_h == round(44 * density)
    assert all(
        button.rect.height == round(44 * density)
        for button in picker._buttons
    )

    # A locked card remains visibly focusable and hoverable.
    picker._focused_card_id = destinations[0].loc.id
    picker._hover_anim[destinations[0].loc.id] = 1.0
    surface = pygame.Surface(size, pygame.SRCALPHA)
    picker.draw(surface)

    for destination in destinations:
        geometry = picker._card_debug_layouts[destination.loc.id]
        card = geometry["card"]
        for name, rect in geometry.items():
            assert card.contains(rect), (name, rect, card)
        assert geometry["thumbnail"].bottom <= geometry["eyebrow"].top
        assert geometry["eyebrow"].bottom <= geometry["title"].top
        assert geometry["title"].bottom <= geometry["identity"].top
        assert geometry["identity"].bottom <= geometry["footer"].top
        assert geometry["portrait"].size == (
            round(52 * density),
            round(52 * density),
        )

    status, _color = picker._card_status(destinations[0])
    assert status == "尚未開放"
    assert _LONG_REASON not in status

    # Direct drawing must leave every pixel outside the card untouched. This
    # catches the historical long red reason that painted through neighbours.
    sentinel = (3, 7, 11, 255)
    isolated = pygame.Surface((picker._card_w + 80, picker._card_h + 80),
                              pygame.SRCALPHA)
    isolated.fill(sentinel)
    card_rect = pygame.Rect(40, 40, picker._card_w, picker._card_h)
    picker._focused_card_id = None
    picker._hover_anim[destinations[0].loc.id] = 0.0
    picker._draw_card(isolated, destinations[0], card_rect)
    left_gap = isolated.subsurface((0, 0, 40, isolated.get_height()))
    right_gap = isolated.subsurface((
        card_rect.right,
        0,
        isolated.get_width() - card_rect.right,
        isolated.get_height(),
    ))
    expected_left = bytes(sentinel[:3]) * (left_gap.get_width()
                                           * left_gap.get_height())
    expected_right = bytes(sentinel[:3]) * (right_gap.get_width()
                                            * right_gap.get_height())
    assert pygame.image.tobytes(left_gap, "RGB") == expected_left
    assert pygame.image.tobytes(right_gap, "RGB") == expected_right


@pytest.mark.parametrize("size", [(1280, 720), (1920, 1080)])
def test_worst_case_preview_keeps_body_and_footer_separate(size, tmp_path) -> None:
    picker, destinations = _make_picker(size, tmp_path)
    locked = destinations[0]
    picker._open_preview(locked.loc.id)
    layout = picker._preview_layout

    panel = pygame.Rect(layout["panel"])
    body = pygame.Rect(layout["body"])
    footer = pygame.Rect(layout["footer"])
    details = pygame.Rect(layout["details"])
    status_card = pygame.Rect(layout["status_card"])
    status = pygame.Rect(layout["status"])
    left = pygame.Rect(layout["left_column"])
    right = pygame.Rect(layout["right_column"])

    assert panel.size == (
        round(920 * size[1] / 720),
        round(_PREVIEW_H * size[1] / 720),
    )
    assert panel.height <= round(size[1] * 0.70)
    for name in ("image", "body", "footer", "go_button", "cancel_button"):
        assert panel.contains(pygame.Rect(layout[name])), name
    for name in ("title", "teaser", "details", "status_card"):
        assert body.contains(pygame.Rect(layout[name])), name
    assert status_card.contains(status)
    assert pygame.Rect(layout["image"]).bottom < body.top
    assert details.bottom <= status_card.top
    assert body.bottom < footer.top
    assert footer.contains(pygame.Rect(layout["go_button"]))
    assert footer.contains(pygame.Rect(layout["cancel_button"]))
    assert details.contains(left)
    assert details.contains(right)
    assert left.right < right.left

    assert len(layout["teaser_lines"]) <= 2
    assert len(layout["itinerary_lines"]) == 4
    assert layout["itinerary_lines"][0].startswith("現在：校門口")
    assert layout["itinerary_lines"][1].startswith(
        "預計抵達：櫻丘校園地點 1"
    )
    assert layout["itinerary_lines"][2] == (
        "路線：校門口 → 櫻丘校園地點 1"
    )
    assert layout["itinerary_lines"][3].startswith("消耗：")
    assert "".join(layout["reason_lines"]) == f"無法前往：{_LONG_REASON}"
    head_h = picker.ctx.fonts.get(15, bold=True).get_linesize()
    assert (
        head_h + picker._sp(6) + 3 * int(layout["npc_row_h"])
        <= left.height
    )
    assert (
        head_h + picker._sp(6) + 3 * int(layout["hook_row_h"])
        <= right.height
    )
    assert picker._preview_buttons[0].enabled is False
    assert picker._preview_focus == 1

    surface = pygame.Surface(size, pygame.SRCALPHA)
    picker.draw(surface)
    # An arbitrarily verbose pack reason switches to an internal scroll
    # viewport. The 3-by-3 detail grid and fixed action footer keep their
    # complete geometry while every reason line remains available.
    locked.reason = _LONG_REASON * 12
    picker._build_preview_buttons()
    long_layout = picker._preview_layout
    long_details = pygame.Rect(long_layout["details"])
    long_status_card = pygame.Rect(long_layout["status_card"])
    long_status = pygame.Rect(long_layout["status"])
    long_reason_view = pygame.Rect(long_layout["reason_view"])
    long_left = pygame.Rect(long_layout["left_column"])
    long_right = pygame.Rect(long_layout["right_column"])
    assert pygame.Rect(long_layout["body"]).contains(long_status)
    assert long_status_card.contains(long_status)
    assert long_status.contains(long_reason_view)
    assert long_details.bottom <= long_status_card.top
    assert (
        head_h + picker._sp(6) + 3 * int(long_layout["npc_row_h"])
        <= long_left.height
    )
    assert (
        head_h + picker._sp(6) + 3 * int(long_layout["hook_row_h"])
        <= long_right.height
    )
    assert "".join(long_layout["reason_lines"]) == (
        f"無法前往：{locked.reason}"
    )
    assert int(long_layout["reason_scroll_max"]) > 0
    picker.update(0.016, InputState(
        mouse_pos=long_reason_view.center,
        mouse_wheel=-1,
    ))
    assert picker._preview_reason_scroll > 0
    picker._preview_reason_scroll = 0
    shoulder = pygame.event.Event(
        pygame.JOYBUTTONDOWN,
        {"joy": 0, "instance_id": 0, "button": 5},
    )
    picker.update(0.016, InputState.collect([shoulder]))
    assert picker._preview_reason_scroll > 0
    picker.draw(surface)


def test_available_preview_is_compact_portrait_forward_and_cancel_safe(
    tmp_path,
) -> None:
    picker, destinations = _make_picker((1920, 1080), tmp_path)
    available = destinations[2]

    picker._open_preview(available.loc.id)
    layout = picker._preview_layout
    panel = pygame.Rect(layout["panel"])
    status_card = pygame.Rect(layout["status_card"])
    status = pygame.Rect(layout["status"])

    assert panel.height == 750
    assert panel.height < 1080 * 0.70
    assert int(layout["npc_avatar_size"]) == 57
    assert int(layout["npc_avatar_size"]) > round(30 * 1.5)
    assert status_card.contains(status)
    assert len(layout["itinerary_lines"]) == 4
    assert picker.describe()["focus"]["preview"] == "取消"
    assert picker.focused_preview_index == 1


def test_future_wait_cta_renders_exact_departure_without_layout_shift(
    tmp_path,
) -> None:
    localization = _english_destination_localization()
    picker, destinations = _make_picker(
        (1280, 720),
        tmp_path,
        localization=localization,
    )
    picker.on_wait = lambda: False
    future = destinations[1]
    before = picker.ctx.state.model_dump(mode="json")

    picker._open_preview(future.loc.id)
    action, cancel = picker._preview_buttons
    footer = pygame.Rect(picker._preview_layout["footer"])

    assert action.label == "Wait Until Day 1, Evening (3 Slots)"
    assert action.rect.height >= 44
    assert cancel.rect.height >= 44
    assert footer.contains(action.rect)
    assert footer.contains(cancel.rect)
    assert not action.rect.colliderect(cancel.rect)

    picker.draw(pygame.Surface((1280, 720), pygame.SRCALPHA))
    assert action.text_overflow is False

    # Preview and its conservative default Cancel action are presentation
    # only; neither the live clock nor any other persistent field changes.
    assert picker.describe()["focus"]["preview"] == "Cancel"
    assert cancel.activate() is True
    assert picker.ctx.state.model_dump(mode="json") == before


def test_detail_only_preview_does_not_repaint_card_browser(
    tmp_path,
    monkeypatch,
) -> None:
    picker, destinations = _make_picker((1280, 720), tmp_path)
    picker._detail_only = True
    picker._open_preview(destinations[2].loc.id)

    monkeypatch.setattr(
        picker,
        "_draw_cards",
        lambda _surface: pytest.fail(
            "detail-only confirmation repainted the card browser"
        ),
    )
    surface = pygame.Surface((1280, 720), pygame.SRCALPHA)
    picker.draw(surface)


def test_english_destination_chrome_reflows_and_keeps_confirmation_safe(
    tmp_path,
) -> None:
    localization = _english_destination_localization()
    moved: list[str] = []
    opened_map: list[bool] = []
    picker, destinations = _make_picker(
        (1280, 720),
        tmp_path,
        localization=localization,
        input_bindings={"back": ["b"]},
    )
    picker.on_move_to = moved.append
    picker.on_world_map = lambda: opened_map.append(True)
    picker._collapsed = {"unused-region"}
    picker._build_header_buttons()

    labels = [button.label for button in picker._buttons]
    assert labels == [
        "Close (B)",
        "Open Campus World Map",
        "Show Reachable Destinations Only",
        "Show Destinations with Characters",
        "Expand All Regions",
    ]
    assert all(picker._panel.contains(button.rect) for button in picker._buttons)
    for index, left in enumerate(picker._buttons):
        for right in picker._buttons[index + 1:]:
            assert not left.rect.colliderect(right.rect)

    available = destinations[2]
    picker._open_preview(available.loc.id)
    layout = picker._preview_layout
    panel = pygame.Rect(layout["panel"])
    go = pygame.Rect(layout["go_button"])
    cancel = pygame.Rect(layout["cancel_button"])
    assert [button.label for button in picker._preview_buttons] == [
        "Travel Now",
        "Cancel",
    ]
    assert picker.describe()["focus"]["preview"] == "Cancel"
    assert panel.contains(go)
    assert panel.contains(cancel)
    assert not go.colliderect(cancel)
    assert layout["itinerary_lines"] == (
        "Now: 校門口 · Day 1 · Mon · Morning",
        "Expected arrival: 櫻丘校園地點 3 · Day 1 · Mon · Morning",
        "Route: 校門口 → 櫻丘校園地點 3",
        "Cost: 2 time slots",
    )

    picker._move_preview_focus((-1, 0))
    assert picker.describe()["focus"]["preview"] == "Travel Now"
    assert picker._activate_preview_focus() is True
    assert moved == [available.loc.id]

    locked = destinations[0]
    picker._open_preview(locked.loc.id)
    assert [button.label for button in picker._preview_buttons] == [
        "This Cannot Be Scheduled Yet",
        "Cancel",
    ]
    assert "".join(picker._preview_layout["reason_lines"]) == (
        f"Cannot travel: {_LONG_REASON}"
    )
    assert picker._preview_buttons[0].enabled is False
    picker.draw(pygame.Surface((1280, 720), pygame.SRCALPHA))

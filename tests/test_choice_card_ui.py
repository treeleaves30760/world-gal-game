"""Visual-card contracts for the dialogue choice menu.

The card renderer is an additive, explicit presentation mode. Functional
choice labels, input ownership, focus, disabled state, and the legacy list
layout remain engine contracts.
"""
from __future__ import annotations

import os
from itertools import combinations
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

import pygame
import pytest

from world_gal_game.core.story_graph import ChoiceCardDetail, ChoiceCardSpec
from world_gal_game.ui.assets import AssetManager
from world_gal_game.ui.fonts import FontRegistry
from world_gal_game.ui.input import InputRouter, InputState
from world_gal_game.ui.system_chrome import ChromeRequest
from world_gal_game.ui.theme import Theme, default_theme
from world_gal_game.ui.visual import VisualPolicy, VisualTheme
from world_gal_game.ui.widgets.choice_menu import ChoiceMenu


@pytest.fixture(scope="module", autouse=True)
def _pygame_init():
    pygame.display.init()
    pygame.font.init()
    pygame.display.set_mode((10, 10))
    yield
    pygame.quit()


def _card(
    title: str,
    *,
    accent: tuple[int, int, int],
    image: str | None = None,
) -> ChoiceCardSpec:
    return ChoiceCardSpec(
        title=title,
        subtitle="在校園裡遇見的同行者",
        badge="路線候選",
        image=image,
        image_alt=f"{title}的角色立繪" if image else "",
        accent=accent,
        details=[
            ChoiceCardDetail(label="日常", text="一起走過課堂與社團生活。"),
            ChoiceCardDetail(label="異常", text="追查校園裡無法解釋的線索。"),
        ],
        tags=["校園", "懸疑"],
        action=f"選擇{title}",
    )


def _card_rows(
    *,
    enabled: tuple[bool, bool, bool] = (True, True, True),
    image_prefix: str | None = None,
) -> list[tuple[str, str, bool, str, ChoiceCardSpec]]:
    names = ("若雪", "千秋", "小鈴")
    accents = ((74, 150, 176), (109, 116, 198), (207, 126, 156))
    rows = []
    for index, (name, accent, is_enabled) in enumerate(
        zip(names, accents, enabled, strict=True),
    ):
        image = (
            f"{image_prefix}/missing-{index}.png"
            if image_prefix is not None
            else None
        )
        rows.append((
            f"route-{index}",
            f"選擇{name}路線",
            is_enabled,
            "" if is_enabled else "尚未完成前置事件",
            _card(name, accent=accent, image=image),
        ))
    return rows


def _compact_card_rows(
    *,
    enabled: tuple[bool, bool, bool] = (True, True, True),
) -> list[tuple[str, str, bool, str, ChoiceCardSpec]]:
    """Short copy that still exercises the compact three-card grid."""
    names = ("若雪", "千秋", "小鈴")
    accents = ((74, 150, 176), (109, 116, 198), (207, 126, 156))
    return [
        (
            f"route-{index}",
            f"選擇{name}路線",
            is_enabled,
            "" if is_enabled else "未解鎖",
            ChoiceCardSpec(
                title=name,
                subtitle="校園同行",
                badge="候選",
                accent=accent,
                details=[
                    ChoiceCardDetail(label="日常", text="一起上課。"),
                    ChoiceCardDetail(label="異常", text="追查線索。"),
                ],
                action=f"選擇{name}",
            ),
        )
        for index, (name, accent, is_enabled) in enumerate(
            zip(names, accents, enabled, strict=True),
        )
    ]


def _menu(
    size: tuple[int, int] = (1280, 720),
    *,
    rows: list[tuple] | None = None,
    chrome=None,
    assets: AssetManager | None = None,
    text_scale: float = 1.0,
    theme=None,
) -> tuple[ChoiceMenu, list[str]]:
    chosen: list[str] = []
    density = max(1.0, size[1] / 720.0)
    menu = ChoiceMenu(
        pygame.Rect((0, 0), size),
        fonts=FontRegistry(candidates=(), scale=density),
        theme=theme or default_theme(),
        on_choose=chosen.append,
        chrome=chrome,
        assets=assets,
        text_scale=text_scale,
    )
    menu.set_choices(rows or _card_rows())
    return menu, chosen


def _long_card(title: str, accent: tuple[int, int, int]) -> ChoiceCardSpec:
    return ChoiceCardSpec(
        title=title,
        subtitle="需要大字排版仍能完整閱讀的比較資料",
        badge="完整預覽",
        accent=accent,
        details=[
            ChoiceCardDetail(
                label="普通面",
                text=(
                    "她把今天的工作、明天的約定與仍然沒有答案的問題全部寫進同一份交接紀錄，讓任何接手的人都知道事情從哪裡開始。"
                    * 3
                ),
            ),
            ChoiceCardDetail(
                label="異常",
                text=(
                    "關閉電源、拔掉插頭並確認房間無人之後，原本應該沉默的設備仍留下了一段可由第二位見證人重複核對的訊號。"
                    * 3
                ),
            ),
            ChoiceCardDetail(
                label="路線承諾",
                text=(
                    "兩個人共同寫下停止條件、求助對象與交接方式；任何一方說停，另一方都必須相信，而且不能事後改寫這項承諾。"
                    * 3
                ),
            ),
        ],
        tags=["共同紀錄", "停止條件", "第二位見證人"],
        action=f"確認閱讀{title}的完整資料後再選擇",
    )


def _long_card_rows(
    *,
    enabled: tuple[bool, bool, bool] = (True, True, True),
) -> list[
    tuple[str, str, bool, str, ChoiceCardSpec]
]:
    names = ("若雪", "千秋", "小鈴")
    accents = ((74, 150, 176), (109, 116, 198), (207, 126, 156))
    return [
        (
            f"route-{index}",
            f"選擇{name}路線",
            is_enabled,
            "" if is_enabled else "尚未完成前置事件",
            _long_card(name, accent),
        )
        for index, (name, accent, is_enabled) in enumerate(
            zip(names, accents, enabled, strict=True),
        )
    ]


def _expanded_translation_card(
    title: str,
    accent: tuple[int, int, int],
) -> ChoiceCardSpec:
    """English-like card copy expanded beyond the compact grid budget."""
    return ChoiceCardSpec(
        title=f"{title} — Library Volunteer Investigation Route",
        subtitle=(
            "Compare her ordinary campus life, investigation boundaries, "
            "and support plan before making a final decision."
        ),
        badge="Localization Expansion Preview",
        accent=accent,
        details=[
            ChoiceCardDetail(
                label="Shared Campus",
                text=(
                    "Attend classes, complete the volunteer handover, and "
                    "record every promise that the two of you make together."
                ),
            ),
            ChoiceCardDetail(
                label="Signal Evidence",
                text=(
                    "Compare the signal with a second witness before either "
                    "person treats the anomaly as reliable evidence."
                ),
            ),
            ChoiceCardDetail(
                label="Stop Conditions",
                text=(
                    "Either person may stop the investigation immediately, "
                    "contact the named supporter, and preserve the full record."
                ),
            ),
        ],
        tags=[
            "Shared Campus Records",
            "Explicit Stop Conditions",
            "Independent Witness",
        ],
        action=f"Read every detail and choose {title}'s complete route",
    )


def _expanded_translation_card_rows() -> list[
    tuple[str, str, bool, str, ChoiceCardSpec]
]:
    names = ("Lin Ruoxue", "Shen Qianqiu", "Jiang Suxiaoling")
    accents = ((74, 150, 176), (109, 116, 198), (207, 126, 156))
    return [
        (
            f"expanded-route-{index}",
            f"Choose {name}'s fully described route",
            True,
            "",
            _expanded_translation_card(name, accent),
        )
        for index, (name, accent) in enumerate(zip(names, accents, strict=True))
    ]


def _scale_sensitive_card_rows() -> list[
    tuple[str, str, bool, str, ChoiceCardSpec]
]:
    """Cards that fit the grid at 1.0 but require reflow at 1.4."""
    rows = _card_rows()
    titles = (
        "Ruoxue Library Volunteer Route",
        "Qianqiu Library Volunteer Route",
        "Suxiaoling Library Volunteer Route",
    )
    return [
        (
            *row[:4],
            row[4].model_copy(update={"title": titles[index]}),
        )
        for index, row in enumerate(rows)
    ]


def _key_input(key: int) -> InputState:
    event = pygame.event.Event(
        pygame.KEYDOWN,
        {"key": key, "mod": 0, "unicode": ""},
    )
    return InputState.collect([event])


def _motion_input(point: tuple[int, int]) -> InputState:
    event = pygame.event.Event(
        pygame.MOUSEMOTION,
        {"pos": point, "rel": (1, 0), "buttons": (0, 0, 0)},
    )
    return InputState(events=[event], mouse_pos=point)


def test_card_layout_is_explicit_all_options_opt_in() -> None:
    menu, _ = _menu()

    menu.set_choices([
        ("first", "普通選項一", True),
        ("second", "普通選項二", True),
        ("third", "普通選項三", True),
    ])
    assert menu.layout_mode == "list"

    card_rows = _card_rows()
    mixed_rows = [
        card_rows[0],
        card_rows[1][0:4],
        card_rows[2],
    ]
    menu.set_choices(mixed_rows)
    assert menu.layout_mode == "list"

    menu.set_choices(card_rows)
    assert menu.layout_mode == "cards"
    assert len(menu.buttons) == 3


@pytest.mark.parametrize(
    "size",
    [(1280, 720), (1920, 1080), (3840, 2160), (800, 450)],
)
def test_card_layout_regions_stay_bounded_and_non_overlapping(
    size: tuple[int, int],
) -> None:
    rows = _compact_card_rows() if size == (800, 450) else _card_rows()
    menu, _ = _menu(size, rows=rows)

    assert menu.layout_mode == "cards"
    assert menu.panel is not None
    bounds = pygame.Rect((0, 0), size)
    panel = menu.panel.rect
    assert bounds.contains(panel)
    assert panel.contains(menu._prompt_rect)

    assert menu._help_surface is not None
    help_rect = menu._help_surface.get_rect(topleft=menu._help_pos)
    assert panel.contains(help_rect)

    button_rects = [button.rect for button in menu.buttons]
    assert all(panel.contains(rect) for rect in button_rects)
    assert all(bounds.contains(rect) for rect in button_rects)
    assert all(rect.width >= 44 and rect.height >= 44 for rect in button_rects)
    assert all(
        not left.colliderect(right)
        for left, right in combinations(button_rects, 2)
    )
    assert menu._prompt_rect.bottom <= min(rect.top for rect in button_rects)
    assert max(rect.bottom for rect in button_rects) <= help_rect.top

    expected_regions = {"header", "image", "details", "tags", "action"}
    assert len(menu._card_regions) == len(button_rects)
    for card_rect, regions in zip(
        button_rects,
        menu._card_regions,
        strict=True,
    ):
        assert set(regions) == expected_regions
        non_empty = [
            region
            for region in regions.values()
            if region.width > 0 and region.height > 0
        ]
        assert all(card_rect.contains(region) for region in non_empty)
        assert all(
            not first.colliderect(second)
            for first, second in combinations(non_empty, 2)
        )
        ordered = [
            regions[name]
            for name in ("header", "image", "details", "tags", "action")
            if regions[name].height > 0
        ]
        assert all(
            first.bottom <= second.top
            for first, second in zip(ordered, ordered[1:])
        )

    surface = pygame.Surface(size, pygame.SRCALPHA)
    menu.draw(surface)


def test_compact_view_reflows_when_the_authored_copy_would_clip() -> None:
    menu, _ = _menu((800, 450), rows=_card_rows())

    assert menu.layout_mode == "cards_accessible"
    assert menu.panel is not None
    assert all(menu.panel.rect.contains(button.rect) for button in menu.buttons)
    assert menu.panel.rect.contains(menu._accessible_preview_rect)
    assert menu._accessible_action_button is not None
    assert menu.panel.rect.contains(menu._accessible_action_button.rect)
    menu.draw(pygame.Surface((800, 450), pygame.SRCALPHA))


@pytest.mark.parametrize(
    "size",
    [(1280, 720), (1920, 1080), (3840, 2160)],
)
def test_standard_card_action_card_and_section_gaps_follow_density(
    size: tuple[int, int],
) -> None:
    menu, _ = _menu(size)

    assert menu._card_compact is False
    density = size[1] / 720.0
    card_gap = round(14 * density)
    section_gap = round(8 * density)
    card_pad = round(14 * density)
    action_h = round(44 * density)

    for left, right in zip(menu.buttons, menu.buttons[1:]):
        assert right.rect.left - left.rect.right == card_gap
    assert menu._row_gap == card_gap

    for card_rect, regions in zip(
        (button.rect for button in menu.buttons),
        menu._card_regions,
        strict=True,
    ):
        assert regions["header"].left - card_rect.left == card_pad
        assert regions["header"].top - card_rect.top == card_pad
        assert regions["image"].top - regions["header"].bottom == section_gap
        assert regions["details"].top - regions["image"].bottom == section_gap
        assert regions["tags"].top - regions["details"].bottom == section_gap
        assert regions["action"].top - regions["tags"].bottom == section_gap
        assert regions["action"].height == action_h
        assert card_rect.bottom - regions["action"].bottom == card_pad


@pytest.mark.parametrize(
    ("size", "expected"),
    [
        (
            (1280, 720),
            {
                "subtitle": 14,
                "detail_label": 13,
                "detail_body": 15,
                "tag": 12,
                "action": 14,
            },
        ),
        (
            (800, 450),
            {
                "subtitle": 11,
                "detail_label": 10,
                "detail_body": 11,
                "action": 11,
            },
        ),
    ],
    ids=["standard", "compact"],
)
def test_card_typography_keeps_standard_readable_and_compact_unchanged(
    size: tuple[int, int],
    expected: dict[str, int],
) -> None:
    compact = size == (800, 450)
    detail_bodies = (
        {"一起上課。", "追查線索。"}
        if compact
        else {
            "一起走過課堂與社團生活。",
            "追查校園裡無法解釋的線索。",
        }
    )
    menu, _ = _menu(
        size,
        rows=(
            _compact_card_rows(enabled=(True, False, True))
            if compact
            else _card_rows(enabled=(True, False, True))
        ),
    )
    line_calls: list[tuple[str, int]] = []
    wrapped_calls: list[tuple[str, int]] = []
    original_line = menu._draw_line
    original_wrapped = menu._draw_wrapped

    def record_line(*args, **kwargs):
        line_calls.append((args[1], kwargs["size"]))
        return original_line(*args, **kwargs)

    def record_wrapped(*args, **kwargs):
        wrapped_calls.append((args[1], kwargs["size"]))
        return original_wrapped(*args, **kwargs)

    menu._draw_line = record_line  # type: ignore[method-assign]
    menu._draw_wrapped = record_wrapped  # type: ignore[method-assign]
    menu.draw(pygame.Surface(size, pygame.SRCALPHA))

    assert {
        font_size
        for text, font_size in line_calls
        if text == ("校園同行" if compact else "在校園裡遇見的同行者")
    } == {expected["subtitle"]}
    assert {
        font_size
        for text, font_size in line_calls
        if text in {"日常", "異常"}
    } == {expected["detail_label"]}
    assert {
        font_size
        for text, font_size in wrapped_calls
        if text in detail_bodies
    } == {expected["detail_body"]}
    assert {
        font_size
        for text, font_size in line_calls
        if text.startswith("選擇") and len(text) == 4
    } == {expected["action"]}
    assert {
        font_size
        for text, font_size in line_calls
        if text == ("未解鎖" if compact else "尚未完成前置事件")
    } == {expected["action"]}
    if "tag" in expected:
        assert {
            font_size
            for text, font_size in line_calls
            if text in {"校園", "懸疑"}
        } == {expected["tag"]}
    else:
        assert not any(text in {"校園", "懸疑"} for text, _ in line_calls)


def test_text_scale_clamps_and_scales_the_legacy_choice_list() -> None:
    menu, _ = _menu(text_scale=0.25)
    menu.set_choices([
        ("first", "第一個普通選項", True),
        ("second", "第二個普通選項", True),
    ])

    assert menu.text_scale == 1.0
    assert {button.font_size for button in menu.buttons} == {18}
    assert menu.set_text_scale(float("inf")) is False

    assert menu.set_text_scale(9.0) is True
    assert menu.text_scale == 1.4
    assert {button.font_size for button in menu.buttons} == {25}
    assert menu.panel is not None
    assert all(menu.panel.rect.contains(button.rect) for button in menu.buttons)
    menu.draw(pygame.Surface((1280, 720), pygame.SRCALPHA))


def test_large_text_short_cards_scale_without_unnecessary_reflow() -> None:
    normal, _ = _menu(text_scale=1.0)
    enlarged, _ = _menu(text_scale=1.4)

    assert normal.layout_mode == "cards"
    assert enlarged.layout_mode == "cards"
    assert enlarged._card_grid_fits(enlarged._rows) is True

    normal_surface = pygame.Surface((1280, 720), pygame.SRCALPHA)
    enlarged_surface = pygame.Surface((1280, 720), pygame.SRCALPHA)
    normal.draw(normal_surface)
    enlarged.draw(enlarged_surface)
    assert pygame.image.tobytes(normal_surface, "RGBA") != (
        pygame.image.tobytes(enlarged_surface, "RGBA")
    )


def test_complete_card_wrapper_preserves_an_overlong_token() -> None:
    menu, _ = _menu()
    font = menu.fonts.get(menu._font_size(15))
    token = "AccessibilityWithoutWhitespace" * 6

    lines = menu._complete_wrap_lines(token, font, 160)

    assert "".join(lines) == token
    assert len(lines) > 1
    assert all(font.size(line)[0] <= 160 for line in lines)


def test_large_text_card_pages_keep_every_field_and_require_explicit_focus() -> None:
    rows = _long_card_rows()
    cards = tuple(row[4] for row in rows)
    menu, chosen = _menu(rows=rows, text_scale=1.4)

    assert menu.layout_mode == "cards_accessible"
    assert menu.focused_index is None
    assert menu._accessible_preview_index == 0
    assert all(
        button.rect.width >= 44 and button.rect.height >= 44
        for button in menu.buttons
    )
    assert menu.panel is not None
    assert all(menu.panel.rect.contains(button.rect) for button in menu.buttons)
    assert menu.panel.rect.contains(menu._accessible_preview_rect)
    action = menu._accessible_action_button
    assert action is not None
    assert action.rect.width >= 44 and action.rect.height >= 44
    assert menu.panel.rect.contains(action.rect)
    assert action.label == cards[0].action
    assert all(button.on_click is None for button in menu.buttons)

    # A preview is visible before navigation, but an unarmed Enter must not
    # silently choose the first route.
    menu.update(0.0, InputState(confirm=True, mouse_pos=(-1, -1)))
    assert chosen == []

    drawn_text: list[str] = []
    original = menu._draw_full_wrapped

    def record_text(*args, **kwargs):
        drawn_text.append(args[1])
        return original(*args, **kwargs)

    menu._draw_full_wrapped = record_text  # type: ignore[method-assign]
    menu.draw(pygame.Surface((1280, 720), pygame.SRCALPHA))
    first = cards[0]
    expected = {
        first.badge,
        first.title,
        first.subtitle,
        *first.tags,
        *(detail.label for detail in first.details),
        *(detail.text for detail in first.details),
    }
    assert expected <= set(drawn_text)
    assert first.action in drawn_text
    assert menu._accessible_document_height > 0

    menu.update(0.0, _key_input(pygame.K_RIGHT))
    assert menu.focused_choice_id == "route-0"
    menu.update(0.0, _key_input(pygame.K_RIGHT))
    assert menu.focused_choice_id == "route-1"
    assert menu._accessible_preview_index == 1
    menu.update(0.0, InputState(confirm=True, mouse_pos=(-1, -1)))
    assert chosen == ["route-1"]


@pytest.mark.parametrize(
    "text_scale",
    [1.0, 1.4],
    ids=["default-scale", "large-scale"],
)
def test_expanded_translation_cards_reflow_without_ellipsizing_authored_copy(
    text_scale: float,
) -> None:
    rows = _expanded_translation_card_rows()
    menu, _ = _menu(rows=rows, text_scale=text_scale)

    assert menu.layout_mode == "cards_accessible"

    full_text: list[str] = []
    single_line_text: list[str] = []
    original_full = menu._draw_full_wrapped
    original_line = menu._draw_line

    def record_full(*args, **kwargs):
        full_text.append(args[1])
        return original_full(*args, **kwargs)

    def record_line(*args, **kwargs):
        single_line_text.append(args[1])
        return original_line(*args, **kwargs)

    menu._draw_full_wrapped = record_full  # type: ignore[method-assign]
    menu._draw_line = record_line  # type: ignore[method-assign]
    surface = pygame.Surface((1280, 720), pygame.SRCALPHA)

    for index, row in enumerate(rows):
        full_text.clear()
        single_line_text.clear()
        menu._set_accessible_preview(index)
        menu.draw(surface)

        card = row[4]
        authored_copy = {
            card.badge,
            card.title,
            card.subtitle,
            card.action,
            *card.tags,
            *(detail.label for detail in card.details),
            *(detail.text for detail in card.details),
        }
        assert authored_copy <= set(full_text)
        assert authored_copy.isdisjoint(single_line_text)
        assert menu._accessible_action_button is not None
        assert menu._accessible_action_button.label == card.action
        assert menu._accessible_document_height > 0


def test_accessible_tabs_only_preview_before_the_real_cta_is_clicked() -> None:
    menu, chosen = _menu(rows=_long_card_rows(), text_scale=1.4)
    first, second, _third = menu.buttons

    # A first click without a preceding hover only establishes the preview.
    menu.update(0.016, InputState(
        mouse_pos=first.rect.center,
        mouse_clicked=True,
    ))
    assert chosen == []
    assert menu.focused_choice_id == "route-0"
    assert menu._accessible_preview_index == 0

    # Motion and click may arrive in one frame; the tab must still never
    # inherit the route callback.
    motion = pygame.event.Event(
        pygame.MOUSEMOTION,
        {"pos": second.rect.center, "rel": (20, 0), "buttons": (0, 0, 0)},
    )
    menu.update(0.016, InputState(
        events=[motion],
        mouse_pos=second.rect.center,
        mouse_clicked=True,
    ))
    assert chosen == []
    assert menu.focused_choice_id == "route-1"
    assert menu._accessible_preview_index == 1
    assert all(button.on_click is None for button in menu.buttons)

    action = menu._accessible_action_button
    assert action is not None
    assert action.label == menu._cards[1].action
    menu.update(0.016, InputState(
        mouse_pos=action.rect.center,
        mouse_clicked=True,
    ))
    assert chosen == ["route-1"]


def test_accessible_locked_tabs_are_previewable_but_cta_stays_disabled() -> None:
    rows = _long_card_rows(enabled=(True, False, True))
    menu, chosen = _menu(rows=rows, text_scale=1.4)

    menu.update(0.0, _key_input(pygame.K_RIGHT))
    menu.update(0.0, _key_input(pygame.K_RIGHT))
    assert menu.focused_choice_id == "route-1"
    assert menu._accessible_preview_index == 1

    action = menu._accessible_action_button
    assert action is not None
    assert action.label == rows[1][4].action
    assert action.enabled is False
    assert action.tooltip == rows[1][3]
    menu.update(0.0, InputState(confirm=True, mouse_pos=(-1, -1)))
    menu.update(0.016, InputState(
        mouse_pos=action.rect.center,
        mouse_clicked=True,
    ))
    assert chosen == []

    drawn_text: list[str] = []
    original = menu._draw_full_wrapped

    def record_text(*args, **kwargs):
        drawn_text.append(args[1])
        return original(*args, **kwargs)

    menu._draw_full_wrapped = record_text  # type: ignore[method-assign]
    menu.draw(pygame.Surface((1280, 720), pygame.SRCALPHA))
    assert rows[1][3] in drawn_text
    assert rows[1][4].action in drawn_text


def test_accessible_gamepad_navigation_can_preview_a_locked_tab() -> None:
    menu, chosen = _menu(
        rows=_long_card_rows(enabled=(True, False, True)),
        text_scale=1.4,
    )
    right = pygame.event.Event(
        pygame.JOYHATMOTION,
        {"joy": 0, "instance_id": 0, "hat": 0, "value": (1, 0)},
    )
    menu.update(0.0, InputState.collect([right]))
    menu.update(0.0, InputState.collect([right]))

    assert menu.focused_choice_id == "route-1"
    assert menu._accessible_preview_index == 1
    assert menu._accessible_action_button is not None
    assert menu._accessible_action_button.enabled is False
    menu.update(0.0, InputState.collect([
        pygame.event.Event(
            pygame.JOYBUTTONDOWN,
            {"joy": 0, "instance_id": 0, "button": 0},
        ),
    ]))
    assert chosen == []


def test_accessible_sole_enabled_choice_aligns_focus_preview_and_cta() -> None:
    rows = _long_card_rows(enabled=(False, True, False))
    menu, chosen = _menu(rows=rows, text_scale=1.4)

    action = menu._accessible_action_button
    assert menu.focused_choice_id == "route-1"
    assert menu._accessible_preview_index == 1
    assert action is not None
    assert action.enabled is True
    assert action.label == rows[1][4].action
    assert action.focused is True

    menu.update(0.0, InputState(confirm=True, mouse_pos=(-1, -1)))
    assert chosen == ["route-1"]


def test_large_text_card_page_scrolls_when_the_viewport_is_short() -> None:
    menu, _ = _menu(
        (800, 450),
        rows=_long_card_rows(),
        text_scale=1.4,
    )
    menu.draw(pygame.Surface((800, 450), pygame.SRCALPHA))

    assert menu.layout_mode == "cards_accessible"
    assert menu._accessible_scroll_max > 0
    before_scroll = menu._accessible_scroll_y
    menu.update(
        0.0,
        InputState(
            mouse_pos=menu._accessible_preview_rect.center,
            mouse_wheel=-1,
        ),
    )
    assert menu._accessible_scroll_y > before_scroll


def test_accessible_card_page_keeps_portrait_art_in_an_identity_frame(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A vertical character must not become a full-width strip of eyes."""
    portrait_path = tmp_path / "assets" / "cards" / "portrait.png"
    portrait_path.parent.mkdir(parents=True)
    portrait = pygame.Surface((300, 400), pygame.SRCALPHA)
    portrait.fill((76, 121, 168, 255))
    pygame.image.save(portrait, portrait_path)

    image = "assets/cards/portrait.png"
    rows = [
        (
            *row[:4],
            row[4].model_copy(update={
                "image": image,
                "image_alt": f"{row[4].title}的直式角色立繪",
                "image_fit": "cover",
                "image_focus_y": 0.31,
                "image_zoom": 1.2,
            }),
        )
        for row in _long_card_rows()
    ]
    assets = AssetManager(pack_root=tmp_path)
    scaled_calls: list[tuple[str | None, tuple[int, int], dict]] = []
    original_scaled = assets.scaled

    def record_scaled(path, size, **kwargs):
        scaled_calls.append((path, size, kwargs))
        return original_scaled(path, size, **kwargs)

    monkeypatch.setattr(assets, "scaled", record_scaled)
    menu, _ = _menu(rows=rows, assets=assets, text_scale=1.4)
    assert menu.layout_mode == "cards_accessible"

    build_calls = 0
    original_build = menu._build_accessible_card_document

    def record_build(**kwargs):
        nonlocal build_calls
        build_calls += 1
        return original_build(**kwargs)

    monkeypatch.setattr(
        menu,
        "_build_accessible_card_document",
        record_build,
    )

    menu.update(0.0, _key_input(pygame.K_RIGHT))
    surface = pygame.Surface((1280, 720), pygame.SRCALPHA)
    menu.draw(surface)
    menu.draw(surface)
    menu.update(0.0, InputState(nav=1, mouse_pos=(-1, -1)))
    menu.draw(surface)
    assert build_calls == 1

    call = next(call for call in scaled_calls if call[0] == image)
    frame_w, frame_h = call[1]
    assert (frame_w, frame_h) == (210, 280)
    assert frame_w * 4 == frame_h * 3
    assert call[2]["fit"] == "cover"
    assert call[2]["focus"] == (0.5, 0.31)
    assert call[2]["zoom"] == 1.2

    # Explicit image-cache invalidation must rebuild the composition; ordinary
    # scroll and repeated frames above must not. Switching preview then replaces
    # the single retained page instead of accumulating all route documents.
    assets.clear_image_cache()
    menu.draw(surface)
    assert build_calls == 2
    menu.update(0.0, _key_input(pygame.K_RIGHT))
    menu.draw(surface)
    assert build_calls == 3


def test_accessible_stock_chrome_caches_are_pixel_identical_and_invalidate() -> None:
    config = SimpleNamespace(visual_accessibility_mode="standard")
    theme = VisualTheme(
        Theme(),
        VisualPolicy.from_config(config),
    )
    menu, _ = _menu(
        rows=_long_card_rows(),
        text_scale=1.4,
        theme=theme,
    )

    def background() -> pygame.Surface:
        surface = pygame.Surface((1280, 720), pygame.SRCALPHA)
        surface.fill((17, 31, 53, 255))
        pygame.draw.rect(surface, (89, 47, 113, 255), (0, 0, 640, 360))
        return surface

    cold = background()
    menu.draw(cold)
    assert menu.panel is not None
    assert menu._fallback_backdrop_cache is not None
    assert menu.panel._fallback_cache is not None
    assert menu._accessible_page_cache is not None
    assert menu._prompt_surface is not None
    assert menu._help_surface is not None
    standard_layers = (
        menu._fallback_backdrop_cache,
        menu.panel._fallback_cache,
        menu._accessible_page_cache,
    )
    standard_prompt = menu._prompt_surface
    standard_help = menu._help_surface
    standard_prompt_bytes = pygame.image.tobytes(standard_prompt, "RGBA")
    standard_help_bytes = pygame.image.tobytes(standard_help, "RGBA")

    warm = background()
    menu.draw(warm)
    assert pygame.image.tobytes(warm, "RGBA") == pygame.image.tobytes(
        cold,
        "RGBA",
    )
    assert standard_layers == (
        menu._fallback_backdrop_cache,
        menu.panel._fallback_cache,
        menu._accessible_page_cache,
    )
    assert menu._prompt_surface is standard_prompt
    assert menu._help_surface is standard_help

    # The real input path changes the selected card and therefore its authored
    # page-border accent. Only that page layer must be replaced.
    menu.update(0.0, _key_input(pygame.K_RIGHT))
    menu.update(0.0, _key_input(pygame.K_RIGHT))
    switched_cold = background()
    menu.draw(switched_cold)
    switched_layers = (
        menu._fallback_backdrop_cache,
        menu.panel._fallback_cache,
        menu._accessible_page_cache,
    )
    assert switched_layers[:2] == standard_layers[:2]
    assert switched_layers[2] is not standard_layers[2]

    switched_warm = background()
    menu.draw(switched_warm)
    assert pygame.image.tobytes(
        switched_warm,
        "RGBA",
    ) == pygame.image.tobytes(switched_cold, "RGBA")
    assert switched_layers == (
        menu._fallback_backdrop_cache,
        menu.panel._fallback_cache,
        menu._accessible_page_cache,
    )

    # Live accessibility changes replace every stock layer whose resolved
    # pixels differ, while the theme-independent black veil remains reusable.
    config.visual_accessibility_mode = "high_contrast"
    contrast_cold = background()
    menu.draw(contrast_cold)
    contrast_layers = (
        menu._fallback_backdrop_cache,
        menu.panel._fallback_cache,
        menu._accessible_page_cache,
    )
    assert contrast_layers[0] is switched_layers[0]
    assert contrast_layers[1] is not switched_layers[1]
    assert contrast_layers[2] is not switched_layers[2]
    contrast_prompt = menu._prompt_surface
    contrast_help = menu._help_surface
    assert contrast_prompt is not None
    assert contrast_help is not None
    assert contrast_prompt is not standard_prompt
    assert contrast_help is not standard_help
    assert pygame.image.tobytes(contrast_prompt, "RGBA") != standard_prompt_bytes
    assert pygame.image.tobytes(contrast_help, "RGBA") != standard_help_bytes

    contrast_warm = background()
    menu.draw(contrast_warm)
    assert pygame.image.tobytes(
        contrast_warm,
        "RGBA",
    ) == pygame.image.tobytes(contrast_cold, "RGBA")
    assert contrast_layers == (
        menu._fallback_backdrop_cache,
        menu.panel._fallback_cache,
        menu._accessible_page_cache,
    )
    assert menu._prompt_surface is contrast_prompt
    assert menu._help_surface is contrast_help

    config.visual_accessibility_mode = "standard"
    restored = background()
    menu.draw(restored)

    assert menu._prompt_surface is not contrast_prompt
    assert menu._help_surface is not contrast_help
    assert menu._prompt_surface is not None
    assert menu._help_surface is not None
    assert pygame.image.tobytes(
        menu._prompt_surface,
        "RGBA",
    ) == standard_prompt_bytes
    assert pygame.image.tobytes(
        menu._help_surface,
        "RGBA",
    ) == standard_help_bytes


def test_release_render_cache_preserves_choice_state_and_rebuilds_pixels() -> None:
    released_screens: list[str | None] = []

    class DecliningChrome:
        def paint(self, *_args, **_kwargs) -> bool:
            return False

        def clear_cache(self, *, screen=None) -> None:
            released_screens.append(screen)

    menu, _ = _menu(
        rows=_long_card_rows(),
        text_scale=1.4,
        chrome=DecliningChrome(),
    )
    menu.update(0.0, _key_input(pygame.K_RIGHT))
    menu.update(0.0, _key_input(pygame.K_RIGHT))

    before = pygame.Surface((1280, 720), pygame.SRCALPHA)
    before.fill((17, 31, 53, 255))
    menu.draw(before)
    menu._accessible_scroll_y = min(80, menu._accessible_scroll_max)
    before.fill((17, 31, 53, 255))
    menu.draw(before)
    focused_id = menu.focused_choice_id
    preview_index = menu._accessible_preview_index
    scroll_y = menu._accessible_scroll_y
    retained = (
        menu._fallback_backdrop_cache,
        menu.panel._fallback_cache if menu.panel is not None else None,
        menu._accessible_page_cache,
        menu._accessible_document_cache,
        menu._prompt_surface,
        menu._help_surface,
    )
    assert all(surface is not None for surface in retained)

    menu.release_render_cache()

    assert released_screens == ["choice"]
    assert menu.focused_choice_id == focused_id
    assert menu._accessible_preview_index == preview_index
    assert menu._accessible_scroll_y == scroll_y
    assert menu._fallback_backdrop_cache is None
    assert menu._accessible_page_cache is None
    assert menu._accessible_document_cache is None
    assert menu._prompt_surface is None
    assert menu._help_surface is None
    assert menu.panel is not None
    assert menu.panel._fallback_cache is None

    rebuilt = pygame.Surface((1280, 720), pygame.SRCALPHA)
    rebuilt.fill((17, 31, 53, 255))
    menu.draw(rebuilt)

    assert pygame.image.tobytes(rebuilt, "RGBA") == pygame.image.tobytes(
        before,
        "RGBA",
    )
    rebuilt_layers = (
        menu._fallback_backdrop_cache,
        menu.panel._fallback_cache,
        menu._accessible_page_cache,
        menu._accessible_document_cache,
        menu._prompt_surface,
        menu._help_surface,
    )
    assert all(surface is not None for surface in rebuilt_layers)
    assert all(
        rebuilt_surface is not retained_surface
        for rebuilt_surface, retained_surface in zip(
            rebuilt_layers,
            retained,
            strict=True,
        )
    )


def test_font_revision_invalidates_choice_text_and_document_surfaces() -> None:
    menu, _ = _menu(
        rows=_long_card_rows(),
        text_scale=1.4,
    )
    surface = pygame.Surface((1280, 720), pygame.SRCALPHA)
    menu.draw(surface)
    first_prompt = menu._prompt_surface
    first_help = menu._help_surface
    first_document = menu._accessible_document_cache
    assert first_prompt is not None
    assert first_help is not None
    assert first_document is not None
    revision = menu.fonts.revision

    menu.fonts.reconfigure(
        menu.fonts.candidates,
        bundled=menu.fonts.bundled,
    )
    menu.draw(surface)
    assert menu.fonts.revision == revision
    assert menu._prompt_surface is first_prompt
    assert menu._help_surface is first_help
    assert menu._accessible_document_cache is first_document

    menu.fonts.reconfigure(("DejaVu Sans",), bundled=None)
    menu.draw(surface)

    assert menu.fonts.revision == revision + 1
    assert menu._prompt_surface is not first_prompt
    assert menu._help_surface is not first_help
    assert menu._accessible_document_cache is not first_document


def test_card_reflow_preserves_focus_by_choice_id_in_both_directions() -> None:
    menu, _ = _menu(rows=_scale_sensitive_card_rows())
    menu.update(0.0, _key_input(pygame.K_RIGHT))
    menu.update(0.0, _key_input(pygame.K_RIGHT))
    assert menu.layout_mode == "cards"
    assert menu.focused_choice_id == "route-1"

    assert menu.set_text_scale(1.4) is True
    assert menu.layout_mode == "cards_accessible"
    assert menu.focused_choice_id == "route-1"
    assert menu._accessible_preview_index == 1

    assert menu.set_text_scale(1.0) is True
    assert menu.layout_mode == "cards"
    assert menu.focused_choice_id == "route-1"
    assert menu.buttons[1].focused is True


def test_dialogue_scene_applies_initial_scale_and_resyncs_after_overlay() -> None:
    from world_gal_game.dev.driver import GameDriver
    from world_gal_game.scenes.dialogue_scene import DialogueScene

    initial = GameDriver(pack="demo_pack")
    try:
        initial.app.config.text_scale = 1.4
        initial.new_game()
        scene = initial.app.manager.current
        assert isinstance(scene, DialogueScene)
        assert scene.choices is not None
        assert scene.choices.text_scale == 1.4
        scene.choices.set_choices(_long_card_rows())
        assert scene.choices.layout_mode == "cards_accessible"
    finally:
        initial.quit()
        pygame.display.init()
        pygame.font.init()
        pygame.display.set_mode((10, 10))

    resumed = GameDriver(pack="demo_pack")
    try:
        resumed.new_game()
        scene = resumed.app.manager.current
        assert isinstance(scene, DialogueScene)
        assert scene.choices is not None
        scene.choices.set_choices(_long_card_rows())
        scene.choices.update(0.0, _key_input(pygame.K_RIGHT))
        scene.choices.update(0.0, _key_input(pygame.K_RIGHT))
        assert scene.choices.focused_choice_id == "route-1"

        scene.pause()
        resumed.app.config.text_scale = 1.4
        scene.resume()

        assert scene.choices.layout_mode == "cards_accessible"
        assert scene.choices.focused_choice_id == "route-1"
        assert scene.choices._accessible_preview_index == 1
    finally:
        resumed.quit()
        pygame.display.init()
        pygame.font.init()
        pygame.display.set_mode((10, 10))


def test_card_navigation_uses_all_directions_and_skips_disabled_cards() -> None:
    menu, chosen = _menu(
        rows=_card_rows(enabled=(True, False, True)),
    )

    assert menu.focused_index is None
    menu.update(0.0, InputState(confirm=True, mouse_pos=(-1, -1)))
    assert chosen == []

    menu.update(0.0, _key_input(pygame.K_RIGHT))
    assert menu.focused_index == 0
    menu.update(0.0, _key_input(pygame.K_RIGHT))
    assert menu.focused_index == 2
    menu.update(0.0, _key_input(pygame.K_LEFT))
    assert menu.focused_index == 0
    menu.update(0.0, _key_input(pygame.K_DOWN))
    assert menu.focused_index == 2
    menu.update(0.0, _key_input(pygame.K_UP))
    assert menu.focused_index == 0

    menu.update(0.0, InputState(confirm=True, mouse_pos=(-1, -1)))
    assert chosen == ["route-0"]


def test_pointer_motion_cannot_leave_a_stale_confirm_target() -> None:
    menu, chosen = _menu(
        rows=_card_rows(enabled=(True, False, True)),
    )
    first, disabled, last = menu.buttons

    menu.update(0.0, _key_input(pygame.K_RIGHT))
    assert menu.focused_index == 0

    menu.update(0.0, _motion_input(disabled.rect.center))
    assert menu.focused_index is None
    menu.update(0.0, InputState(confirm=True, mouse_pos=disabled.rect.center))
    assert chosen == []

    menu.update(0.0, _motion_input(last.rect.center))
    assert menu.focused_index == 2
    menu.update(0.0, InputState(confirm=True, mouse_pos=(-1, -1)))
    assert chosen == ["route-2"]

    menu.update(0.0, _motion_input((-20, -20)))
    assert menu.focused_index is None
    menu.update(0.0, InputState(confirm=True, mouse_pos=(-20, -20)))
    assert chosen == ["route-2"]

    menu.update(
        0.016,
        InputState(mouse_pos=disabled.rect.center, mouse_clicked=True),
    )
    assert chosen == ["route-2"]
    menu.update(
        0.016,
        InputState(mouse_pos=first.rect.center, mouse_clicked=True),
    )
    assert chosen == ["route-2", "route-0"]


def _finger_event(
    kind: int,
    point: tuple[int, int],
    size: tuple[int, int],
) -> pygame.event.Event:
    return pygame.event.Event(
        kind,
        x=point[0] / size[0],
        y=point[1] / size[1],
        dx=0.0,
        dy=0.0,
        finger_id=3,
        touch_id=7,
        pressure=1.0,
    )


def test_real_touch_tabs_preview_and_only_the_cta_commits() -> None:
    size = (1280, 720)
    rows = _long_card_rows(enabled=(True, False, True))
    menu, chosen = _menu(size, rows=rows, text_scale=1.4)
    router = InputRouter(touch_mode=True)

    def tap(point: tuple[int, int]) -> None:
        down = router.collect(
            [_finger_event(pygame.FINGERDOWN, point, size)],
            window_size=size,
        )
        assert down.mouse_clicked is False
        menu.update(0.016, down)
        up = router.collect(
            [_finger_event(pygame.FINGERUP, point, size)],
            dt=0.016,
            window_size=size,
        )
        assert up.mouse_clicked is True
        menu.update(0.016, up)

    tap(menu.buttons[1].rect.center)
    assert chosen == []
    assert menu.focused_choice_id == "route-1"
    assert menu._accessible_preview_index == 1
    assert menu._accessible_action_button is not None
    assert menu._accessible_action_button.enabled is False

    tap(menu.buttons[2].rect.center)
    assert chosen == []
    assert menu.focused_choice_id == "route-2"
    assert menu._accessible_preview_index == 2
    assert menu._accessible_action_button is not None
    assert menu._accessible_action_button.enabled is True

    tap(menu._accessible_action_button.rect.center)
    assert chosen == ["route-2"]


def test_accessible_preview_touch_drag_captures_pointer_without_selection() -> None:
    size = (800, 450)
    menu, chosen = _menu(
        size,
        rows=_long_card_rows(),
        text_scale=1.4,
    )
    menu.draw(pygame.Surface(size, pygame.SRCALPHA))
    assert menu._accessible_scroll_max > 0
    assert menu._accessible_action_button is not None

    router = InputRouter(touch_mode=True)
    start = (
        menu._accessible_preview_rect.centerx,
        menu._accessible_preview_rect.bottom - 12,
    )
    down = router.collect(
        [_finger_event(pygame.FINGERDOWN, start, size)],
        window_size=size,
    )
    menu.update(0.016, down)
    assert menu._accessible_touch_dragging is True

    outside = (
        start[0],
        max(1, menu._accessible_preview_rect.top - 40),
    )
    motion = router.collect(
        [_finger_event(pygame.FINGERMOTION, outside, size)],
        dt=0.016,
        window_size=size,
    )
    assert motion.touch_delta[1] < 0
    menu.update(0.016, motion)
    assert menu._accessible_scroll_y > 0
    assert menu._accessible_touch_dragging is True
    assert chosen == []

    # End over the CTA after a real drag. Pointer capture owns the release and
    # InputRouter must not turn it into a tap on the route action.
    action_center = menu._accessible_action_button.rect.center
    motion_to_action = router.collect(
        [_finger_event(pygame.FINGERMOTION, action_center, size)],
        dt=0.016,
        window_size=size,
    )
    menu.update(0.016, motion_to_action)
    assert menu._accessible_touch_dragging is True
    release = router.collect(
        [_finger_event(pygame.FINGERUP, action_center, size)],
        dt=0.016,
        window_size=size,
    )
    assert release.mouse_clicked is False
    menu.update(0.016, release)
    assert menu._accessible_touch_dragging is False
    assert chosen == []


def test_accessible_high_contrast_ignores_black_authored_semantic_accent() -> None:
    config = SimpleNamespace(visual_accessibility_mode="high_contrast")
    theme = VisualTheme(
        Theme(radius_m=0),
        VisualPolicy.from_config(config),
    )
    rows = [
        (*row[:4], row[4].model_copy(update={"accent": (0, 0, 0)}))
        for row in _long_card_rows()
    ]
    menu, _ = _menu(rows=rows, text_scale=1.4, theme=theme)
    menu.update(0.0, _key_input(pygame.K_RIGHT))

    semantic_text_colors: list[tuple] = []
    original = menu._draw_full_wrapped

    def record_text(*args, **kwargs):
        if args[1] == "01":
            semantic_text_colors.append(kwargs["color"])
        return original(*args, **kwargs)

    menu._draw_full_wrapped = record_text  # type: ignore[method-assign]
    surface = pygame.Surface((1280, 720), pygame.SRCALPHA)
    surface.fill((91, 23, 147, 255))
    menu.draw(surface)

    assert menu._accessible_action_button is not None
    tab_focus = surface.get_at((
        menu.buttons[0].rect.centerx,
        menu.buttons[0].rect.top,
    ))
    page_boundary = surface.get_at((
        menu._accessible_preview_rect.centerx,
        menu._accessible_preview_rect.top,
    ))
    cta_focus = surface.get_at((
        menu._accessible_action_button.rect.centerx,
        menu._accessible_action_button.rect.top,
    ))
    assert tab_focus[:3] == theme.accent_warm[:3]
    assert page_boundary[:3] == theme.border_strong[:3]
    assert cta_focus[:3] == theme.accent_warm[:3]
    assert semantic_text_colors == [theme.accent_alt]
    assert all(pixel[:3] != (0, 0, 0) for pixel in (
        tab_focus,
        page_boundary,
        cta_focus,
    ))


@pytest.mark.parametrize(
    ("target_index", "expected"),
    [(0, ["route-0"]), (1, [])],
    ids=["enabled-card", "disabled-card"],
)
def test_real_input_router_touch_tap_activates_only_on_finger_release(
    target_index: int,
    expected: list[str],
) -> None:
    size = (1280, 720)
    menu, chosen = _menu(
        size,
        rows=_card_rows(enabled=(True, False, True)),
    )
    target = menu.buttons[target_index].rect.center
    router = InputRouter(touch_mode=True)

    down = router.collect(
        [_finger_event(pygame.FINGERDOWN, target, size)],
        window_size=size,
    )
    assert down.touch_started is True
    assert down.mouse_clicked is False
    menu.update(0.016, down)
    assert chosen == []

    up = router.collect(
        [_finger_event(pygame.FINGERUP, target, size)],
        dt=0.016,
        window_size=size,
    )
    assert up.touch_released is True
    assert up.mouse_clicked is True
    menu.update(0.016, up)
    assert chosen == expected


class _OpaqueRecordingChrome:
    """Handled chrome that tries to paint every semantic state identically."""

    color = (181, 23, 91, 255)

    def __init__(self) -> None:
        self.requests: list[tuple[pygame.Rect, ChromeRequest]] = []

    def paint(
        self,
        surface: pygame.Surface,
        rect: pygame.Rect,
        request: ChromeRequest,
    ) -> bool:
        self.requests.append((rect.copy(), request))
        layer = pygame.Surface(rect.size, pygame.SRCALPHA)
        layer.fill(self.color)
        surface.blit(layer, rect.topleft)
        return True


def test_card_chrome_is_paint_only_and_engine_keeps_state_semantics() -> None:
    chrome = _OpaqueRecordingChrome()
    menu, _ = _menu(
        rows=_card_rows(enabled=(True, False, True)),
        chrome=chrome,
    )
    menu.update(0.0, _key_input(pygame.K_RIGHT))

    surface = pygame.Surface((1280, 720), pygame.SRCALPHA)
    menu.draw(surface)

    card_requests = [
        request
        for _rect, request in chrome.requests
        if request.screen == "choice"
        and request.part == "card"
        and request.variant == "option_card"
    ]
    assert len(card_requests) == 3
    assert [request.enabled for request in card_requests] == [
        True,
        False,
        True,
    ]
    assert card_requests[0].focused is True

    focus_requests = [
        request
        for _rect, request in chrome.requests
        if request.screen == "choice"
        and request.part == "focus"
        and request.variant == "option_card"
    ]
    assert len(focus_requests) == 1
    assert focus_requests[0].enabled is True
    assert focus_requests[0].focused is True

    focused, disabled, enabled = menu.buttons
    focus_pixel = surface.get_at((focused.rect.centerx, focused.rect.top + 1))
    assert focus_pixel[:3] == default_theme().accent_warm[:3]

    safe_enabled_point = (enabled.rect.right - 2, enabled.rect.centery)
    safe_disabled_point = (disabled.rect.right - 2, disabled.rect.centery)
    assert surface.get_at(safe_enabled_point)[:3] == chrome.color[:3]
    assert surface.get_at(safe_disabled_point)[:3] != chrome.color[:3]


def test_missing_card_images_draw_asset_placeholders(tmp_path: Path) -> None:
    assets = AssetManager(pack_root=tmp_path)
    rows = _card_rows(image_prefix="assets/choice-cards")
    menu, _ = _menu(rows=rows, assets=assets)
    assert all(
        card.image is not None and not assets.has_image(card.image)
        for card in menu._cards
        if card is not None
    )

    surface = pygame.Surface((1280, 720), pygame.SRCALPHA)
    surface.fill((1, 2, 3, 255))
    menu.draw(surface)

    assert assets.image_cache_entries >= len(rows) * 2
    for regions in menu._card_regions:
        image_rect = regions["image"]
        image = surface.subsurface(image_rect)
        raw = pygame.image.tobytes(image, "RGB")
        colors = {raw[offset:offset + 3] for offset in range(0, len(raw), 3)}
        assert len(colors) > 1

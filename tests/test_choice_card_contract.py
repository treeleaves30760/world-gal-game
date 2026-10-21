"""End-to-end contract tests for optional visual choice-card metadata.

The functional ``Choice.text``/conditions/effects/transition remain the
authoritative narrative API. Cards are presentation-only enrichment that must
survive dialogue serialization, refresh safely in historical saves, and stay
hidden from headless clients until the real choice boundary is presented.
"""
from __future__ import annotations

import copy
import json
import math
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from world_gal_game.config import EngineConfig
from world_gal_game.core.game_state import GameState
from world_gal_game.core.story_graph import (
    Choice,
    ChoiceCardDetail,
    ChoiceCardSpec,
    Condition,
    Effect,
    Line,
    Scene,
    StoryGraph,
    rebind_authored_dialogue_history_voices,
    rebind_authored_choice_presentations,
    rebind_authored_story_catalog,
)
from world_gal_game.dialogue.dialogue_engine import (
    ChoiceOption,
    DialogueEngine,
    ScenePresentation,
    presentation_from_dict,
    presentation_to_dict,
    rebind_resume_choice_presentations,
)
from world_gal_game.headless import HeadlessSession
from world_gal_game.npc.llm_brain import EchoBrain
from world_gal_game.npc.npc_base import NPCRegistry
from world_gal_game.validator import validate_pack


def _card(
    *,
    title: str = "白若雪",
    image: str | None = "assets/characters/ruoxue/smile.png",
    image_alt: str = "白若雪微笑的半身立繪",
    accent: tuple[int, int, int] = (74, 150, 176),
) -> ChoiceCardSpec:
    return ChoiceCardSpec(
        title=title,
        subtitle="總圖學姊",
        badge="畢業前還有一年",
        image=image,
        image_alt=image_alt if image is not None else "",
        image_fit="cover",
        accent=accent,
        details=[
            ChoiceCardDetail(label="日常", text="總圖與沒署名的文章"),
            ChoiceCardDetail(label="異常", text="書架後的無名歌聲"),
        ],
        tags=["安靜", "文學"],
        action="翻到若雪那一頁",
    )


def _headless(state: GameState) -> HeadlessSession:
    return HeadlessSession(
        config=EngineConfig(),
        state=state,
        npcs=NPCRegistry(),
        brain=EchoBrain(),
        dialogue=DialogueEngine(state),
        meta={},
    )


def _write_yaml(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.safe_dump(data, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )


def test_choice_card_spec_is_strict_bounded_normalized_and_frozen() -> None:
    card = ChoiceCardSpec(
        title="  白若雪  ",
        subtitle="  總圖   學姊 ",
        badge=" 畢業前一年 ",
        image=None,
        details=[{"label": " 日常 ", "text": " 總圖   與文章 "}],
        tags=[" 安靜 ", "文學"],
        action=" 翻到   下一頁 ",
        accent=(0, 127, 255),
    )

    assert card.title == "白若雪"
    assert card.subtitle == "總圖 學姊"
    assert card.details == [
        ChoiceCardDetail(label="日常", text="總圖 與文章"),
    ]
    assert card.tags == ["安靜", "文學"]
    assert card.action == "翻到 下一頁"
    assert card.accent == (0, 127, 255)

    with pytest.raises(ValidationError, match="Extra inputs"):
        ChoiceCardSpec(title="若雪", unknown_copy="typo")
    with pytest.raises(ValidationError, match="Extra inputs"):
        ChoiceCardSpec(
            title="若雪",
            details=[{"label": "日常", "text": "總圖", "lable": "typo"}],
        )
    with pytest.raises(ValidationError, match="frozen"):
        card.title = "千秋"


@pytest.mark.parametrize(
    "payload",
    [
        {"title": ""},
        {"title": "角" * 65},
        {
            "title": "若雪",
            "details": [
                {"label": str(index), "text": "內容"}
                for index in range(4)
            ],
        },
        {"title": "若雪", "tags": ["一", "二", "三", "四"]},
        {"title": "若雪", "tags": ["重複", "重複"]},
        {"title": "若雪", "action": "字" * 65},
    ],
)
def test_choice_card_spec_rejects_copy_outside_authored_bounds(
    payload: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        ChoiceCardSpec.model_validate(payload)


def test_choice_card_image_requires_nonblank_alternative_text() -> None:
    with pytest.raises(ValidationError, match="image_alt is required"):
        ChoiceCardSpec(
            title="若雪",
            image="assets/characters/ruoxue/smile.png",
        )

    without_image = ChoiceCardSpec(title="若雪")
    assert without_image.image is None
    assert without_image.image_alt == ""


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("image_focus_y", True),
        ("image_focus_y", False),
        ("image_focus_y", "0.5"),
        ("image_focus_y", math.nan),
        ("image_focus_y", math.inf),
        ("image_focus_y", -math.inf),
        ("image_focus_y", -0.01),
        ("image_focus_y", 1.01),
        ("image_zoom", True),
        ("image_zoom", False),
        ("image_zoom", "1.5"),
        ("image_zoom", math.nan),
        ("image_zoom", math.inf),
        ("image_zoom", -math.inf),
        ("image_zoom", 0.99),
        ("image_zoom", 2.01),
    ],
)
def test_choice_card_crop_values_reject_coercion_nonfinite_and_out_of_bounds(
    field: str,
    value: object,
) -> None:
    with pytest.raises(ValidationError):
        ChoiceCardSpec.model_validate({"title": "若雪", field: value})


@pytest.mark.parametrize(
    ("field", "value", "expected"),
    [
        ("image_focus_y", 0, 0.0),
        ("image_focus_y", 1, 1.0),
        ("image_focus_y", 0.0, 0.0),
        ("image_focus_y", 1.0, 1.0),
        ("image_zoom", 1, 1.0),
        ("image_zoom", 2, 2.0),
        ("image_zoom", 1.0, 1.0),
        ("image_zoom", 2.0, 2.0),
    ],
)
def test_choice_card_crop_values_accept_numeric_endpoints(
    field: str,
    value: int | float,
    expected: float,
) -> None:
    card = ChoiceCardSpec.model_validate({"title": "若雪", field: value})

    assert getattr(card, field) == expected
    assert type(getattr(card, field)) is float


@pytest.mark.parametrize(
    "accent",
    [
        (-1, 0, 0),
        (0, 0, 256),
        (0, 1),
        (0, 1, 2, 3),
        (True, 1, 2),
        ("0", 1, 2),
    ],
)
def test_choice_card_rgb_is_exactly_three_strict_bytes(
    accent: object,
) -> None:
    with pytest.raises(ValidationError):
        ChoiceCardSpec(title="若雪", accent=accent)


def test_dialogue_interpolates_all_card_copy_and_roundtrips_json() -> None:
    state = GameState()
    state.player.name = "靜宜"
    authored = _card(
        title="{player_name}與白若雪",
        image="assets/cards/{player_name}.png",
        image_alt="{player_name}面前的白若雪",
    ).model_copy(update={
        "subtitle": "{player_name}的總圖學姊",
        "badge": "寫給{player_name}",
        "details": [
            ChoiceCardDetail(label="日常", text="{player_name}在總圖讀文章"),
            ChoiceCardDetail(label="異常", text="{player_name}聽見歌聲"),
        ],
        "tags": ["{player_name}", "文學"],
        "action": "讓{player_name}翻頁",
    })
    state.story.add_scene(Scene(
        id="route_choice",
        choices=[Choice(
            id="choose_ruoxue",
            text="{player_name}選擇若雪",
            card=authored,
        )],
    ))

    presented = DialogueEngine(state).start_scene("route_choice")

    assert presented.kind == "choice"
    assert presented.choices[0].text == "靜宜選擇若雪"
    assert presented.choices[0].card == ChoiceCardSpec(
        title="靜宜與白若雪",
        subtitle="靜宜的總圖學姊",
        badge="寫給靜宜",
        image="assets/cards/{player_name}.png",
        image_alt="靜宜面前的白若雪",
        image_fit="cover",
        accent=(74, 150, 176),
        details=[
            ChoiceCardDetail(label="日常", text="靜宜在總圖讀文章"),
            ChoiceCardDetail(label="異常", text="靜宜聽見歌聲"),
        ],
        tags=["靜宜", "文學"],
        action="讓靜宜翻頁",
    )
    # Renderer copy is resolved on a new model; authored pack data is untouched.
    assert authored.title == "{player_name}與白若雪"
    assert authored.image == "assets/cards/{player_name}.png"

    encoded = presentation_to_dict(presented)
    json.dumps(encoded, allow_nan=False)
    restored = presentation_from_dict(encoded)

    assert restored == presented
    assert restored.choices[0].card == presented.choices[0].card


def test_presentation_decoder_accepts_legacy_choice_without_card() -> None:
    old_payload = {
        "kind": "choice",
        "line": None,
        "choices": [
            {
                "id": "legacy",
                "text": "沿用文字選項",
                "enabled": True,
            },
        ],
        "scene_id": "legacy_scene",
    }

    restored = presentation_from_dict(old_payload)

    assert restored == ScenePresentation(
        kind="choice",
        choices=[
            ChoiceOption(
                id="legacy",
                text="沿用文字選項",
                enabled=True,
                reason="",
                card=None,
            ),
        ],
        scene_id="legacy_scene",
    )
    encoded = presentation_to_dict(restored)
    assert "card" not in encoded["choices"][0]


def test_authored_story_rebind_replaces_saved_logic_and_keeps_progress() -> None:
    authored = StoryGraph(
        scenes={
            "decision": Scene(
                id="decision",
                lines=[
                    Line(text="目前版本的第一句"),
                    Line(
                        text="目前版本的下一句",
                        portrait="assets/portraits/current.png",
                        cg="assets/cgs/current.png",
                        bgm="assets/audio/current.ogg",
                        ambient="assets/audio/current-ambient.ogg",
                        voice="assets/voice/current.ogg",
                        effects=[
                            Effect(kind="set_flag", target="current_logic"),
                        ],
                    ),
                ],
                choices=[
                    Choice(
                        id="same_id",
                        text="目前版本的 functional text",
                        effects=[Effect(kind="set_flag", target="new_logic")],
                        next_scene="new_next",
                        card=_card(title="新版若雪卡"),
                    ),
                ],
            ),
        },
    )
    saved = StoryGraph(
        scenes={
            "decision": Scene(
                id="decision",
                lines=[
                    Line(text="存檔中的舊台詞"),
                    Line(
                        text="竄改的下一句",
                        portrait="/tmp/forged-portrait.png",
                        cg="/tmp/forged-cg.png",
                        bgm="/tmp/forged-bgm.ogg",
                        ambient="/tmp/forged-ambient.ogg",
                        voice="/tmp/forged-voice.ogg",
                        effects=[
                            Effect(kind="set_flag", target="forged_logic"),
                        ],
                    ),
                ],
                choices=[
                    Choice(
                        id="same_id",
                        text="存檔中的 functional text",
                        requires=[Condition(kind="flag", target="saved_gate")],
                        forbids=[Condition(kind="flag", target="saved_forbid")],
                        effects=[
                            Effect(
                                kind="set_flag",
                                target="saved_logic",
                                value=True,
                            ),
                        ],
                        next_scene="saved_next",
                        hidden_if_locked=True,
                        card=_card(title="舊版若雪卡"),
                    ),
                    Choice(id="saved_only", text="runtime branch"),
                ],
            ),
            "saved_only_scene": Scene(id="saved_only_scene"),
        },
        played={"decision", "saved_only_scene"},
        current_scene="decision",
        current_line_index=1,
    )
    saved_before = saved.model_dump(mode="json")
    authored_before = authored.model_dump(mode="json")

    rebound = rebind_authored_story_catalog(authored, saved)

    assert rebound.scenes == authored.scenes
    assert rebound.played == {"decision"}
    assert rebound.current_scene == "decision"
    assert rebound.current_line_index == 1
    assert saved.model_dump(mode="json") == saved_before
    assert authored.model_dump(mode="json") == authored_before
    assert rebound is not saved
    assert rebound.scenes["decision"] is not saved.scenes["decision"]
    assert rebound.scenes["decision"] is not authored.scenes["decision"]
    # The compatibility import follows the same complete-authority contract.
    assert rebind_authored_choice_presentations(authored, saved) == rebound


@pytest.mark.parametrize(
    ("current_scene", "current_line_index"),
    (
        ("removed_scene", 0),
        ("decision", -1),
        ("decision", 3),
        (None, 1),
    ),
)
def test_authored_story_rebind_rejects_unsafe_cursor(
    current_scene: str | None,
    current_line_index: int,
) -> None:
    authored = StoryGraph(scenes={
        "decision": Scene(
            id="decision",
            lines=[Line(text="一"), Line(text="二")],
        ),
    })
    saved = StoryGraph(
        scenes={
            "decision": authored.scenes["decision"],
            "removed_scene": Scene(id="removed_scene"),
        },
        current_scene=current_scene,
        current_line_index=current_line_index,
    )

    with pytest.raises(ValueError, match="saved story"):
        rebind_authored_story_catalog(authored, saved)


def test_dialogue_history_voice_rebind_clears_untrusted_assets() -> None:
    authored = StoryGraph(scenes={
        "decision": Scene(
            id="decision",
            lines=[
                Line(text="有配音", voice="assets/voice/current.ogg"),
                Line(text="無配音"),
            ],
        ),
    })
    state = GameState()
    state.dialogue_history.lines = [
        {"text": "保留", "voice": "assets/voice/current.ogg"},
        {"text": "清除", "voice": "/tmp/forged-voice.ogg"},
        {"text": "舊存檔沒有 key"},
        {"text": "非字串也清除", "voice": {"path": "forged"}},
    ]
    before = state.dialogue_history.model_dump(mode="json")

    rebound = rebind_authored_dialogue_history_voices(
        authored,
        state.dialogue_history,
    )

    assert rebound.lines[0]["voice"] == "assets/voice/current.ogg"
    assert rebound.lines[1]["voice"] is None
    assert "voice" not in rebound.lines[2]
    assert rebound.lines[3]["voice"] is None
    assert state.dialogue_history.model_dump(mode="json") == before


def test_resume_rebind_refreshes_only_known_card_rows_with_interpolation() -> None:
    state = GameState()
    state.player.name = "靜宜"
    state.story = StoryGraph(
        scenes={
            "decision": Scene(
                id="decision",
                lines=[Line(text="選擇之前")],
                choices=[
                    Choice(
                        id="with_card",
                        text="authoritative text",
                        card=_card(title="{player_name}的新卡"),
                    ),
                    Choice(id="without_card", text="plain fallback"),
                ],
            ),
        },
        played={"already_done"},
        current_scene="decision",
        current_line_index=1,
    )
    resume = {
        "version": 1,
        "screen": "dialogue",
        "story_scene": "decision",
        "story_line_index": 1,
        "presentation": {
            "kind": "choice",
            "scene_id": "decision",
            "choices": [
                {
                    "id": "with_card",
                    "text": "玩家當時看到的文字",
                    "enabled": False,
                    "reason": "玩家當時看到的鎖定理由",
                    "card": _card(title="過期卡片").model_dump(mode="json"),
                },
                {
                    "id": "without_card",
                    "text": "舊文字仍保留",
                    "enabled": True,
                    "card": _card(title="應移除的舊卡").model_dump(mode="json"),
                },
                {
                    "id": "runtime_only",
                    "text": "插件選項",
                    "enabled": True,
                    "card": {"runtime": "must remain untouched"},
                },
            ],
        },
        "last_line": {"opaque": "unrelated resume state"},
    }
    resume_before = copy.deepcopy(resume)
    state_before = state.model_dump(mode="json")

    rebound = rebind_resume_choice_presentations(resume, state)

    assert resume == resume_before
    assert state.model_dump(mode="json") == state_before
    assert rebound is not resume
    rows = rebound["presentation"]["choices"]
    assert rows[0]["text"] == "玩家當時看到的文字"
    assert rows[0]["enabled"] is False
    assert rows[0]["reason"] == "玩家當時看到的鎖定理由"
    assert rows[0]["card"]["title"] == "靜宜的新卡"
    assert "card" not in rows[1]
    assert rows[1]["text"] == "舊文字仍保留"
    assert rows[2] == resume_before["presentation"]["choices"][2]
    assert rebound["last_line"] == resume_before["last_line"]


def test_headless_exposes_cards_only_after_choice_boundary_is_presented() -> None:
    state = GameState()
    state.player.name = "靜宜"
    state.story.add_scene(Scene(
        id="decision",
        lines=[Line(text="先讀完這一句。")],
        choices=[
            Choice(
                id="choose_ruoxue",
                text="{player_name}選擇若雪",
                card=_card(
                    title="{player_name}與白若雪",
                    image=None,
                    image_alt="",
                ),
            ),
        ],
    ))
    session = _headless(state)

    opening = session.start_scene("decision")["presentation"]

    assert opening["kind"] == "line"
    assert "choices" not in opening
    # The story cursor has advanced past the final line, but the player is still
    # looking at that line. Affordances must not leak the upcoming decision.
    assert state.story.current_line_index == 1
    assert session.affordances()["choices"] == []

    boundary = session.next_line()["presentation"]
    assert boundary["kind"] == "choice"
    assert boundary["choices"][0]["text"] == "靜宜選擇若雪"
    assert boundary["choices"][0]["card"]["title"] == "靜宜與白若雪"

    affordance = session.affordances()["choices"][0]
    assert affordance["id"] == "choose_ruoxue"
    assert affordance["text"] == "靜宜選擇若雪"
    assert affordance["enabled"] is True
    assert affordance["card"] == boundary["choices"][0]["card"]


def test_validator_reports_nested_typo_unsafe_path_and_missing_card_image(
    tmp_path: Path,
) -> None:
    _write_yaml(
        tmp_path / "content" / "scenes" / "choice_cards.yaml",
        {
            "scenes": [
                {
                    "id": "choice_card_validation",
                    "lines": [{"text": "選擇。"}],
                    "choices": [
                        {
                            "id": "nested_typo",
                            "text": "拼字錯誤",
                            "card": {
                                "title": "若雪",
                                "details": [
                                    {
                                        "lable": "日常",
                                        "text": "總圖",
                                    },
                                ],
                            },
                        },
                        {
                            "id": "unsafe_image",
                            "text": "不安全路徑",
                            "card": {
                                "title": "千秋",
                                "image": "../outside.png",
                                "image_alt": "千秋的半身立繪",
                            },
                        },
                        {
                            "id": "missing_image",
                            "text": "遺失圖片",
                            "card": {
                                "title": "小鈴",
                                "image": "assets/cards/missing.png",
                                "image_alt": "小鈴的半身立繪",
                            },
                        },
                    ],
                },
            ],
        },
    )

    issues = validate_pack(tmp_path)

    nested = next(
        issue
        for issue in issues
        if issue.path.endswith(".card.details.0.lable")
    )
    assert nested.severity == "error"
    assert "未知欄位 'lable'" in nested.message
    assert nested.hint is not None
    assert "label" in nested.hint

    unsafe = next(
        issue
        for issue in issues
        if issue.path.endswith(".choices[1].card.image")
    )
    assert unsafe.severity == "error"
    assert "不是安全的 pack 相對路徑" in unsafe.message
    assert unsafe.hint is not None
    assert "'..'" in unsafe.hint

    missing = next(
        issue
        for issue in issues
        if issue.path.endswith(".choices[2].card.image")
    )
    assert missing.severity == "warning"
    assert "選項卡圖片檔案不存在" in missing.message

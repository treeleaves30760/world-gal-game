"""Tavern mode: presets, prompt assembly, style, the de-slop linter,
expression resolution and reply parsing."""
from __future__ import annotations

import pytest

from world_gal_game.tavern.card import CharacterCard, LoreEntry, Lorebook
from world_gal_game.tavern.expressions import (DEFAULT_KEY, ExpressionMemo,
                                               ExpressionResolver)
from world_gal_game.tavern.preset import Preset, PromptBlock, ensure_engine_blocks
from world_gal_game.tavern.prompt import (AuthorNote, ChatMessage, PrivateBrief,
                                          PromptInputs, SceneContext, build_prompt)
from world_gal_game.tavern.reply import parse_reply
from world_gal_game.tavern.slop import SlopLinter, TextLine, lines_from_scene
from world_gal_game.tavern.style import StyleGuide, detect_locale, normalize_locale


def _card(**extra) -> CharacterCard:
    data = dict(
        id="h1", name="白若", description="{{char}}在書店打工。", personality="慢熟",
        secrets=["祕密：最後一頁"], speech={"particles": ["啦"], "habits": ["被稱讚就換話題"]},
        mes_example="<START>\n{{user}}: 妳好\n{{char}}: 「嗯。」\n<START>\n{{char}}: 「欸。」",
    )
    data.update(extra)
    return CharacterCard.model_validate(data)


def _inputs(**extra) -> PromptInputs:
    data = dict(card=_card(), preset=Preset.builtin("zh-Hant"),
                style=StyleGuide.builtin("zh-Hant"), user_name="阿哲",
                history=[ChatMessage("user", "阿哲：「妳好」")])
    data.update(extra)
    return PromptInputs(**data)


def _text(build) -> str:
    return "\n".join(message["content"] for message in build.messages)


# --------------------------------------------------------------------- preset


def test_builtin_preset_order_and_engine_blocks():
    ids = [block.id for block in Preset.builtin().blocks]
    assert ids.index("main") < ids.index("charDescription") < ids.index("chatHistory")
    assert ids[-2:] == ["styleGuide", "direction"]


def test_tavern_preset_import_and_engine_block_backfill():
    preset = Preset.from_tavern({
        "temperature": 0.9, "openai_max_context": 4000,
        "prompts": [
            {"identifier": "main", "name": "Main", "content": "MAIN {{char}}"},
            {"identifier": "charDescription", "marker": True},
            {"identifier": "chatHistory", "marker": True},
            {"identifier": "jailbreak", "content": "POST"},
            {"identifier": "extra", "content": "DEPTH", "injection_position": 1,
             "injection_depth": 1, "role": "user"},
            {"identifier": "off", "content": "never"},
        ],
        "prompt_order": [
            {"character_id": 100000, "order": [{"identifier": "main", "enabled": True}]},
            {"character_id": 100001, "order": [
                {"identifier": "main", "enabled": True},
                {"identifier": "charDescription", "enabled": True},
                {"identifier": "chatHistory", "enabled": True},
                {"identifier": "jailbreak", "enabled": True},
                {"identifier": "extra", "enabled": True},
                {"identifier": "off", "enabled": False}]},
        ]}, name="mine")
    assert preset.temperature == 0.9 and preset.max_context == 4000
    assert preset.block("extra").position == "in_chat"
    assert not preset.block("off").enabled
    filled = ensure_engine_blocks(preset)
    ids = [block.id for block in filled.blocks]
    assert ids.index("charDescription") < ids.index("speechProfile") < ids.index(
        "chatHistory")
    assert ids[-1] == "direction"
    with pytest.raises(ValueError, match="Text Completion"):
        Preset.from_tavern({"temperature": 1})


# --------------------------------------------------------------------- prompt


def test_prompt_contains_private_blocks_only_when_asked():
    private = PrivateBrief(objective="想被問", hidden=["場景祕密"])
    build = build_prompt(_inputs(private=private, direction="先講別的",
                                 output_format="輪到{{char}}。"))
    text = _text(build)
    assert "祕密：最後一頁" in text and "場景祕密" in text and "想被問" in text
    assert "白若在書店打工。" in text                      # macro in the description
    assert build.messages[-1]["content"].endswith("輪到白若。")
    assert "先講別的" in build.messages[-1]["content"]
    public = _text(build_prompt(_inputs(private=private, include_private=False)))
    assert "祕密：最後一頁" not in public and "場景祕密" not in public


def test_prompt_examples_become_few_shot_turns_with_one_header():
    build = build_prompt(_inputs(speech_format="{name}：「{text}」",
                                 example_separator="（另一次）"))
    roles = [m["role"] for m in build.messages]
    assert "assistant" in roles
    text = _text(build)
    assert text.count("平常說話的樣子") == 1
    assert "阿哲：「妳好」" in text and "（另一次）" in text


def test_prompt_card_overrides_and_original_macro():
    card = _card(system_prompt="CARD + {{original}}", post_history_instructions="PHI")
    preset = Preset(id="p", blocks=[
        PromptBlock(id="main", content="PRESET MAIN"),
        PromptBlock(id="chatHistory", marker=True),
        PromptBlock(id="jailbreak", content="PRESET PHI"),
    ], engine_blocks=False)
    build = build_prompt(_inputs(card=card, preset=preset))
    assert build.messages[0]["content"] == "CARD + PRESET MAIN"
    assert build.messages[-1]["content"] == "PHI"
    status = {block.id: block.status for block in build.blocks}
    assert status["main"] == "overridden" and status["jailbreak"] == "overridden"
    locked = preset.model_copy(update={"blocks": [
        PromptBlock(id="main", content="PRESET MAIN", forbid_overrides=True),
        PromptBlock(id="chatHistory", marker=True)]})
    assert build_prompt(_inputs(card=card, preset=locked)).messages[0]["content"] \
        == "PRESET MAIN"


def test_prompt_depth_injection_lore_positions_and_report():
    book = Lorebook(name="w", token_budget=0, entries=[
        LoreEntry(name="前", constant=True, content="LORE-BEFORE"),
        LoreEntry(name="深", constant=True, content="LORE-DEPTH", position="at_depth",
                  depth=1),
        LoreEntry(name="沒中", keys=["不存在"], content="x"),
    ])
    preset = Preset(id="p", blocks=[
        PromptBlock(id="main", content="M"),
        PromptBlock(id="worldInfoBefore", marker=True),
        PromptBlock(id="chatHistory", marker=True),
        PromptBlock(id="inj", content="INJ0", position="in_chat", depth=0, role="user"),
    ], engine_blocks=False)
    history = [ChatMessage("user", "u1"), ChatMessage("assistant", "a1"),
               ChatMessage("user", "u2")]
    build = build_prompt(_inputs(preset=preset, history=history, lorebooks=[book],
                                 author_note=AuthorNote("NOTE", depth=2)))
    contents = [m["content"] for m in build.messages]
    assert contents[:2] == ["M", "LORE-BEFORE"]
    # depth 2 = before the last two messages; depth 1 = before the last one.
    assert contents.index("NOTE") < contents.index("a1") < contents.index("LORE-DEPTH")
    assert contents[-1].endswith("INJ0") and "u2" in contents[-1]
    log = {d.entry: d.status for d in build.lore.log}
    assert log == {"前": "activated", "深": "activated", "沒中": "no_match"}
    report = build.render()
    assert "world book" in report and "no_match" in report and "authorNote" in report
    assert build.to_dict()["tokens_estimate"] == build.tokens


def test_prompt_budget_drops_oldest_history_first():
    preset = Preset.builtin().model_copy(update={"max_context": 900, "max_tokens": 0})
    history = [ChatMessage("user" if i % 2 == 0 else "assistant", f"第{i}句" + "話" * 40)
               for i in range(30)]
    build = build_prompt(_inputs(preset=preset, history=history))
    assert build.dropped_history > 0
    text = _text(build)
    assert "第29句" in text and "第0句" not in text
    assert build.tokens <= 900 or build.dropped_examples > 0


def test_prompt_scene_context_and_expression_list():
    scene = SceneContext(location="公園", time="下午", premise="{{user}}走過來。",
                         relationship="客氣", affection=8, affection_label="陌生")
    build = build_prompt(_inputs(scene=scene, expressions={"shy": ["害羞"], "sad": []}))
    text = _text(build)
    assert "地點：公園" in text and "阿哲走過來。" in text and "好感 8" in text
    assert "shy（害羞）" in text and "<sprite:害羞>" in text


# ---------------------------------------------------------------- style + slop


def test_locale_helpers():
    assert normalize_locale("zh_TW") == "zh-Hant" and normalize_locale("zh-CN") == "zh-Hans"
    assert detect_locale("你們這個時候來做什麼") == "zh-Hant"
    assert detect_locale("你们这个时候来做什么") == "zh-Hans"
    assert detect_locale("Hello there") == "en"
    assert detect_locale("そうだね、ありがとう") == "ja"


def test_style_guide_pack_overrides(tmp_path):
    path = tmp_path / "style.yaml"
    path.write_text("line_max_chars: 20\nbanned_phrases: [命運的安排]\n"
                    "disable: [stock.trace]\nextra_rules: [不要用敬語]\n"
                    "regional_terms: {視頻: 影片檔}\n", encoding="utf-8")
    guide = StyleGuide.load(path, locale="zh-Hant")
    assert guide.line_max_chars == 20 and guide.region == "tw"
    assert "stock.trace" not in [p.id for p in guide.active_patterns()]
    assert guide.regional_terms["視頻"] == "影片檔"
    assert "不要用敬語" in guide.render_dialogue_rules()
    hits = SlopLinter(guide).lint_turn("「這是命運的安排。」", speaker="A")
    assert any(hit.rule == "banned.phrase" and hit.hard for hit in hits)


SLOPPY = [
    (None, "空氣彷彿凝固了。她的眼中閃過一絲不易察覺的悲傷，嘴角微微上揚。"),
    ("林", "「謝謝你願意告訴我這些。我能理解你的感受，你並不孤單。」"),
    ("陳", "「林，我覺得很開心能夠認識妳。這不是巧合，而是命運的安排。」"),
    ("林", "「陳，我也感到非常溫暖。也許，這就是所謂的緣分吧。」"),
    ("陳", "「林，妳的笑容就像陽光一樣，照亮了我的整個世界。」"),
    ("林", "「……陳，我深吸一口氣，心跳漏了一拍，不知道該說什麼。」"),
    ("陳", "「林，無論如何，我都會一直陪在妳的身邊，不會離開的。」"),
    ("林", "「陳，謝謝你。我真的覺得很幸福，也覺得非常感動。」"),
    ("陳", "「林，不用謝。這是我應該做的事情，因為妳值得。」"),
    ("林", "「陳，那一刻，我終於明白了什麼才是真正的幸福。」"),
]
NATURAL = [
    (None, "她把紙貼歪了，又撕下來重貼。"),
    ("林", "「欸。」"),
    ("林", "「你看得到上面的字嗎？我寫太小了。」"),
    ("陳", "「拾獲素描本……請聯絡？」"),
    ("林", "「嗯。昨天掉的。」"),
    ("陳", "「在哪掉的？」"),
    ("林", "「知道的話就不用貼這個了吧。」"),
    ("陳", "「也是。」"),
    ("林", "「裡面有畫到一半的東西，所以有點麻煩。」"),
    ("陳", "「藍色的？」"),
    ("林", "「你怎麼知道。」"),
    (None, "她低頭看了一眼包包，把吊飾轉到背面。"),
]


def _lint(rows):
    lines = [TextLine(index=i, text=text, speaker=speaker)
             for i, (speaker, text) in enumerate(rows)]
    return SlopLinter(StyleGuide.builtin("zh-Hant"), names=["林", "陳"]).lint(lines)


def test_linter_separates_sloppy_from_natural():
    sloppy, natural = _lint(SLOPPY), _lint(NATURAL)
    assert sloppy.grade == "D" and natural.grade == "A"
    rules = set(sloppy.by_rule())
    assert {"stock.eyes", "stock.mouth", "stock.air", "voice.therapy",
            "struct.not_but", "tell.emotion_named", "rhythm.uniform",
            "rhythm.no_short"} <= rules
    assert "close.aphorism" in rules                     # only in the closing lines
    assert natural.hits == []
    assert sloppy.to_dict()["score"] > 50
    assert "slop score" in sloppy.render()


def test_linter_regional_script_and_markup():
    linter = SlopLinter(StyleGuide.builtin("zh-Hant"))
    report = linter.lint([
        TextLine(0, "「這個視頻的質量好嗎？」", "A"),
        TextLine(1, "「我们走吧。」", "A"),
        TextLine(2, "「[shake]真的假的[/shake]{player_name}[[glitch]]」", "A"),
        TextLine(3, "「啦啦啦♪」", "A"),
    ])
    rules = report.by_rule()
    assert rules.get("regional.term") == 2 and rules.get("script.mixed") == 1
    assert not any(hit.rule.startswith("fmt.") for hit in report.hits)
    assert SlopLinter(StyleGuide.builtin("zh-Hans")).lint(
        [TextLine(0, "「我们看视频吧。」", "A")]).hits == []


def test_linter_turn_checks_and_retake_note():
    linter = SlopLinter(StyleGuide.builtin("zh-Hant"),
                        never_says={"白若": ["我很感動"]})
    assert linter.lint_turn("", speaker="白若")[0].rule == "fmt.empty"
    hits = linter.lint_turn("「我很感動。」\n阿哲：「真的嗎」", speaker="白若",
                            others=["阿哲"])
    rules = {hit.rule for hit in hits}
    assert {"card.never_says", "fmt.speaks_for_other"} <= rules
    assert linter.needs_retake(hits)
    assert "→" in linter.retake_note(hits)
    long = linter.lint_turn("「" + "長" * 60 + "」", speaker="白若")
    assert [hit.rule for hit in long] == ["rhythm.long_line"]
    assert not linter.needs_retake(long, 2.5)
    assert linter.needs_retake(linter.lint_turn("「**不行**」", speaker="白若"))
    assert linter.lint_turn("「嗯。」", speaker="白若") == []


def test_lines_from_scene_accepts_models_and_dicts():
    rows = lines_from_scene({"id": "s", "lines": [{"text": "a"},
                                                  {"speaker": "B", "text": "b"}]})
    assert [(r.scene_id, r.speaker) for r in rows] == [("s", None), ("s", "B")]


# ---------------------------------------------------------------- expressions


def test_expression_resolution_order_and_memo(tmp_path):
    memo = ExpressionMemo(path=tmp_path / "memo.yaml")
    resolver = ExpressionResolver("h1", ["smile", "shy", "sad"],
                                  aliases={"shy": ["害羞", "難為情"]}, memo=memo)
    assert resolver.resolve("smile").source == "exact"
    assert resolver.resolve("難為情").to_dict()["source"] == "alias"
    assert resolver.resolve("有點害羞").key == "shy"            # contains an alias
    assert resolver.resolve("有點慌").key == "shy"              # nearest art
    assert resolver.resolve("苦笑").key == "shy"                # awkward, not smile
    assert resolver.resolve("開心").key == "smile"
    assert resolver.resolve("面無表情").key == DEFAULT_KEY
    assert resolver.resolve("氣到發抖").key is None             # no angry art: unresolved
    assert memo.dirty and memo.get("h1", "開心") == "smile"
    memo.data["h1"]["開心"] = "sad"                             # a human corrects it
    assert resolver.resolve("開心").to_dict()["source"] == "memo"
    assert resolver.resolve("開心").key == "sad"
    memo.dirty = True
    memo.save()
    assert ExpressionMemo.load(tmp_path / "memo.yaml").get("h1", "開心") == "sad"


# ----------------------------------------------------------------------- reply


def test_parse_reply_actor_turn():
    reply = parse_reply("<think>先躲</think>\n＜表情：難為情＞\n「我的素描本……」\n「算了。」\n"
                        "（白若把紙壓在腿下。）", default_speaker="白若")
    assert reply.thoughts == ["先躲"]
    assert [(l.kind, l.text, l.sprite) for l in reply.lines] == [
        ("dialogue", "我的素描本……", "難為情"), ("dialogue", "算了。", "難為情"),
        ("narration", "白若把紙壓在腿下。", "")]


def test_parse_reply_mixed_prose_prefixes_and_directives():
    reply = parse_reply("<bg:圖書館>\n她抬起頭。「你來了。」然後又低下去。\n"
                        "阿哲：「嗯。」\n<bgm:雨天><cg:off>\n*窗外開始下雨*",
                        default_speaker="白若", known_speakers=["阿哲"])
    kinds = [(l.kind, l.speaker, l.text) for l in reply.lines]
    assert kinds == [
        ("narration", None, "她抬起頭。"), ("dialogue", "白若", "你來了。"),
        ("narration", None, "然後又低下去。"), ("dialogue", "阿哲", "嗯。"),
        ("narration", None, "窗外開始下雨")]
    assert reply.lines[0].bg == "圖書館"
    assert reply.lines[-1].bgm == "雨天" and reply.lines[-1].cg == "off"


def test_parse_reply_narrator_and_bare_lines():
    narrator = parse_reply("湖面沒有風。\n「有人嗎？」", default_speaker=None)
    assert [l.kind for l in narrator.lines] == ["narration", "narration"]
    assert narrator.warnings
    bare = parse_reply("你也是來躲太陽的嗎？", default_speaker="白若")
    assert bare.lines[0].kind == "dialogue" and bare.lines[0].speaker == "白若"
    assert parse_reply("   ", default_speaker="白若").lines == []

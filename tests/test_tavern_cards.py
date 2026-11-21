"""Tavern mode: character cards, PNG embedding, macros and the world book."""
from __future__ import annotations

import base64
import json
import random
from pathlib import Path

import pytest

from world_gal_game.npc.npc_base import NPC, NPCRegistry
from world_gal_game.tavern import png_meta
from world_gal_game.tavern.card import (CharacterCard, LoreEntry, Lorebook,
                                        card_from_npc)
from world_gal_game.tavern.lorebook import activate
from world_gal_game.tavern.macros import MacroContext, expand, find_macros
from world_gal_game.tavern.pack import TavernPack


def _card() -> CharacterCard:
    return CharacterCard(
        id="h1", name="白若", description="書店店員。", personality="慢熟",
        mes_example=("<START>\n{{user}}: 妳在畫什麼？\n{{char}}: 「沒什麼啦。」\n"
                     "<START>\n{{char}}: 「欸，你又來了。」\n他說：「好」"),
        secrets=["最後一頁沒畫完"], speech={"particles": ["啦"], "never_says": ["我很感動"]},
        affection_voices=[{"min": 0, "note": "客氣"}, {"min": 30, "note": "會吐槽"}],
        character_book={"name": "b", "entries": [
            {"keys": ["素描本"], "content": "藍色封面", "requires": ["flag:found"]}]},
    )


# ---------------------------------------------------------------------- cards


def test_tavern_v2_round_trip_keeps_engine_fields():
    card = _card()
    obj = card.to_tavern("v2")
    assert obj["spec"] == "chara_card_v2"
    assert obj["data"]["extensions"]["world_gal_game"]["secrets"] == ["最後一頁沒畫完"]
    back = CharacterCard.from_tavern(obj)
    assert back.secrets == card.secrets
    assert back.speech.never_says == ["我很感動"]
    assert back.character_book.entries[0].requires[0].target == "found"
    assert back.voice_for_affection(40) == "會吐槽"
    assert back.voice_for_affection(5) == "客氣"


def test_tavern_v1_and_v3_and_unknown_keys_are_tolerated():
    v1 = CharacterCard.from_tavern({"name": "A", "description": "d", "avatar": "x.png",
                                    "talkativeness": "0.8", "chat": "zzz"})
    assert v1.name == "A" and v1.talkativeness == 0.8
    v3 = CharacterCard.from_tavern({"spec": "chara_card_v3", "data": {
        "name": "B", "nickname": "小B", "extensions": {"depth_prompt": {
            "prompt": "note", "depth": 2, "role": 1}}}})
    assert v3.nickname == "小B"
    assert v3.depth_prompt is not None and v3.depth_prompt.role == "user"
    with pytest.raises(ValueError):
        CharacterCard.from_tavern({"description": "no name"})


@pytest.mark.parametrize("name", ["c.yaml", "c.json", "c.png"])
def test_card_file_round_trip(tmp_path: Path, name: str):
    card = _card()
    card.save(tmp_path / name, spec="v3")
    loaded = CharacterCard.load(tmp_path / name)
    assert loaded.name == "白若"
    assert loaded.secrets == card.secrets
    assert loaded.id == "h1"
    assert loaded.source == f"file:{name}"


def test_png_chunks_and_rejections(tmp_path: Path):
    png = png_meta.embed_card_json(png_meta.minimal_png(), v2={"name": "x"},
                                   v3={"name": "y"})
    assert set(png_meta.read_text_chunks(png)) == {"chara", "ccv3"}
    assert png_meta.read_card_json(png) == {"name": "y"}          # V3 preferred
    # Re-embedding replaces instead of piling up chunks.
    again = png_meta.embed_card_json(png, v2={"name": "z"})
    assert json.loads(base64.b64decode(png_meta.read_text_chunks(again)["chara"])) \
        == {"name": "z"}
    with pytest.raises(png_meta.PngMetaError):
        png_meta.read_card_json(b"not a png")
    with pytest.raises(png_meta.PngMetaError):
        png_meta.read_card_json(png_meta.minimal_png())           # no card inside
    with pytest.raises(png_meta.PngMetaError):
        png_meta.read_text_chunks(png[:-9])                       # truncated


def test_example_blocks_do_not_split_prose_on_a_colon():
    blocks = _card().example_blocks()
    assert [[t.speaker for t in b] for b in blocks] == [["user", "char"], ["char"]]
    assert "他說：「好」" in blocks[1][0].text
    assert _card().example_lines()[0] == "「沒什麼啦。」"


def test_card_from_npc_and_merge():
    npc = NPC(id="h1", name="白若", role="店員", persona="慢熟", voice="輕、慢",
              backstory="去年搬來", secrets=["s1"], likes=["茶"], age=19)
    base = card_from_npc(npc)
    assert base.speech.diction == "輕、慢" and base.role == "店員" and base.age == 19
    authored = CharacterCard.model_validate(
        {"name": "白若", "speech": {"particles": ["欸"]}, "secrets": ["s2"]})
    merged = authored.merged_over(base)
    assert merged.secrets == ["s2"]                 # authored wins
    assert merged.speech.diction == "輕、慢"        # untouched NPC field kept
    assert merged.speech.particles == ["欸"]
    assert merged.personality == "慢熟"


def test_tavern_pack_layers_cards_and_survives_a_bad_file(tmp_path: Path):
    root = tmp_path / "pack"
    cards = root / "content" / "tavern" / "cards"
    cards.mkdir(parents=True)
    (cards / "h1.yaml").write_text("name: 白若\nsecrets: [authored]\n", encoding="utf-8")
    (cards / "broken.yaml").write_text("name: [unclosed\n", encoding="utf-8")
    (cards / "byname.json").write_text(json.dumps(
        {"name": "老闆", "description": "from tavern"}, ensure_ascii=False),
        encoding="utf-8")
    registry = NPCRegistry()
    registry.add(NPC(id="h1", name="白若", persona="慢熟"))
    registry.add(NPC(id="boss", name="老闆"))
    pack = TavernPack.load(root, registry)
    assert pack.cards["h1"].secrets == ["authored"]
    assert pack.cards["h1"].personality == "慢熟"
    assert pack.cards["boss"].description == "from tavern"     # bound by name
    assert any(issue["file"] == "broken.yaml" for issue in pack.issues)
    assert pack.card("白若") is pack.cards["h1"]
    assert pack.locale == "zh-Hant"


# --------------------------------------------------------------------- macros


def test_macros_identity_variables_and_order():
    ctx = MacroContext(char="白若", user="阿哲", card=_card(), rng=random.Random(1),
                       values={"location": "廣場"})
    out = expand("{{setvar::mood::calm}}{{if .mood}}A:{{.mood}}{{else}}B{{/if}}|"
                 "{{char}}/{{user}}|{{location}}|{{description}}|<USER>|{{// note}}"
                 "{{nope}}", ctx)
    assert out == "A:calm|白若/阿哲|廣場|書店店員。|阿哲|{{nope}}"
    assert ctx.unknown == ["nope"]


def test_macros_untaken_branch_has_no_side_effects_and_nested_conditions():
    ctx = MacroContext(variables={"x": "1"})
    assert expand("{{if .missing}}{{setvar::y::2}}{{/if}}{{getvar::y}}", ctx) == ""
    assert "y" not in ctx.variables
    assert expand("{{if {{getvar::x}}}}yes{{/if}}{{if !.missing}}!{{/if}}", ctx) == "yes!"


def test_macros_are_deterministic_and_sandboxed():
    a = expand("{{random::a::b::c}}{{roll::2d6}}{{pick::x::y}}",
               MacroContext(rng=random.Random(5), pick_seed="s"))
    b = expand("{{random::a::b::c}}{{roll::2d6}}{{pick::x::y}}",
               MacroContext(rng=random.Random(5), pick_seed="s"))
    assert a == b
    # No clock supplied: never the wall clock.
    assert expand("[{{time}}{{date}}]", MacroContext()) == "[]"
    # Chat text is data: a macro inside the last message is not expanded.
    ctx = MacroContext(char="C", last_message="{{setvar::pwn::1}}")
    assert expand("{{lastMessage}}", ctx) == "{{setvar::pwn::1}}"
    assert "pwn" not in ctx.variables
    ctx = MacroContext()
    expand('{{banned "嘴角上揚"}}', ctx)
    assert ctx.banned == ["嘴角上揚"]
    assert find_macros("{{char}} {{random::{{user}}::b}}") == ["char", "random", "user"]
    assert expand("a {{ unbalanced", MacroContext()) == "a {{ unbalanced"


# ------------------------------------------------------------------ world book


class _State:
    def __init__(self, flags):
        self.flags = flags

    def evaluate(self, cond):
        return bool(self.flags.get(cond.target))


def _book() -> Lorebook:
    return Lorebook(name="w", scan_depth=2, token_budget=40, entries=[
        LoreEntry(name="本子", keys=["素描本"], content="素描本是藍色封面的。"),
        LoreEntry(name="湖", keys=["湖"], secondary_keys=["夜"], logic="and_all",
                  content="湖邊半夜有聲音。"),
        LoreEntry(name="常駐", constant=True, content="這是一個小鎮。", order=1),
        LoreEntry(name="機率", keys=["素描本"], probability=0, content="不會出現"),
        LoreEntry(name="大", keys=["素描本"], content="長" * 120),
        LoreEntry(name="關", keys=["素描本"], enabled=False, content="x"),
        LoreEntry(name="旗", keys=["素描本"], content="gated", requires=["flag:found"]),
    ])


def test_lorebook_activation_and_decision_log():
    act = activate([_book()], messages=["早安", "我的素描本不見了"],
                   state=_State({}), rng=random.Random(0))
    status = {d.entry: d.status for d in act.log}
    assert status == {
        "本子": "activated", "湖": "no_match", "常駐": "activated",
        "機率": "probability_failed", "大": "budget_exceeded", "關": "disabled",
        "旗": "state_gated",
    }
    assert [e.name for e in act.entries] == ["常駐", "本子"]      # insertion order
    assert "藍色" in act.text("before_char")


def test_lorebook_state_gate_scan_depth_and_secondary_logic():
    book = _book()
    book.token_budget = 0
    act = activate([book], messages=["我的素描本不見了"], state=_State({"found": 1}),
                   rng=random.Random(0))
    assert "旗" in [e.name for e in act.entries]
    # Out of scan depth: the key is three messages back.
    old = activate([book], messages=["素描本", "a", "b"], rng=random.Random(0))
    assert "本子" not in [e.name for e in old.entries]
    night = activate([book], messages=["夜裡的湖"], rng=random.Random(0))
    assert "湖" in [e.name for e in night.entries]
    day = activate([book], messages=["湖"], rng=random.Random(0))
    assert {d.entry: d.status for d in day.log}["湖"] == "secondary_failed"


def test_lorebook_recursion_groups_and_regex_keys():
    book = Lorebook(name="r", recursive=True, token_budget=0, entries=[
        LoreEntry(name="a", keys=["鑰匙"], content="鑰匙在閣樓。"),
        LoreEntry(name="b", keys=["閣樓"], content="閣樓有一口箱子。"),
        LoreEntry(name="g1", keys=["貓"], content="白貓", group="cat", order=1),
        LoreEntry(name="g2", keys=["貓"], content="黑貓", group="cat", order=9),
        LoreEntry(name="re", keys=["/第\\d+頁/"], content="頁碼"),
    ])
    act = activate([book], messages=["鑰匙呢？那隻貓翻到第12頁"], rng=random.Random(0))
    names = [e.name for e in act.entries]
    assert "b" in names and "re" in names                 # recursion + regex key
    assert "g2" in names and "g1" not in names            # one winner per group
    assert next(d for d in act.log if d.entry == "b").recursive


def test_lorebook_reads_tavern_world_file_and_card_book():
    world = Lorebook.from_tavern({"entries": {
        "1": {"uid": 1, "key": ["B"], "content": "b", "position": 4, "depth": 2,
              "selectiveLogic": 3, "keysecondary": ["x"], "disable": True},
        "0": {"uid": 0, "key": ["A"], "content": "a", "order": 5, "probability": 50,
              "useProbability": True}}})
    assert [e.keys for e in world.entries] == [["A"], ["B"]]
    assert world.entries[1].position == "at_depth" and world.entries[1].depth == 2
    assert world.entries[1].logic == "and_all" and not world.entries[1].enabled
    assert world.entries[0].probability == 50
    entry = LoreEntry(name="n", keys=["k"], content="c", position="at_depth", depth=3,
                      requires=["flag:f"])
    back = Lorebook.from_tavern({"entries": [entry.to_tavern()]}).entries[0]
    assert back.position == "at_depth" and back.depth == 3
    assert back.requires[0].target == "f"

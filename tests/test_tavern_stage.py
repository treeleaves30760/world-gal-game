"""Tavern mode: the multi-agent stage, its backends, and the CLI."""
from __future__ import annotations

import json
import shutil
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

from world_gal_game.cli import main as cli_main
from world_gal_game.tavern.backends import (AnthropicBackend, BackendError, EchoBackend,
                                            OpenAICompatBackend, ScriptedBackend,
                                            make_backend, run_stage, shape_chat)
from world_gal_game.tavern.commit import commit_stage
from world_gal_game.tavern.stage import (PlayerSpec, SceneBrief, Stage, StageContext,
                                         StageError, StageRequest, Turn)

BRIEF = Path("games/demo_pack/content/tavern/briefs/park_bench.yaml")
SECRET = "最後幾頁畫的是廣場上的路人"


@pytest.fixture
def ctx() -> StageContext:
    return StageContext.from_pack("demo_pack", seed=7)


@pytest.fixture
def pack(tmp_path: Path) -> Path:
    dst = tmp_path / "demo_pack"
    shutil.copytree("games/demo_pack", dst)
    return dst


def _brief(**updates) -> SceneBrief:
    brief = SceneBrief.load(BRIEF)
    return brief.model_copy(update=updates) if updates else brief


def _director(*rows: str) -> list[str]:
    return [f"下一個：{who}\n提示：{note}\n節拍：{beat}" for who, note, beat in
            (row.split("|") for row in rows)]


def _all_text(request: StageRequest) -> str:
    return "\n".join(message["content"] for message in request.messages)


# ---------------------------------------------------------------------- stage


def test_director_run_retakes_slop_and_builds_a_valid_scene(ctx, pack):
    ctx.apply_setup(_brief().setup)
    stage = Stage(_brief(), ctx, seed=7)
    backend = ScriptedBackend(
        by_role={
            # The narrator opens and closes on its own; the director only casts.
            "director": _director("player|隨口問|stay", "heroine_1|不回答，反問|stay",
                                  "heroine_1|提起素描本，講一半|next", "end||next"),
            "narrator": ["湖面沒有風。\n她膝蓋上那張紙，邊緣是撕下來的毛邊。",
                         "那張紙還是背面朝上。"],
            "player": ["「妳在畫什麼？」"],
            "choices": ["ask_again｜所以妳到底在畫什麼\nlet_it_go｜我坐這邊可以嗎"],
        },
        by_speaker={"heroine_1": [
            "<sprite:微笑>\n「謝謝你願意關心我。」\n（她的嘴角微微上揚，眼中閃過一絲慌張。）",
            "<sprite:有點慌>\n「你也是來躲太陽的嗎？」\n（林清雪把紙壓在大腿底下。）",
            "<sprite:難為情>\n「我的素描本如果還在的話……」\n「算了。」",
        ]})
    run_stage(stage, backend)
    assert stage.done
    summary = stage.summary()
    assert summary["retaken_lines"] >= 1 and summary["slop"]["grade"] == "A"
    scene, warnings = stage.to_scene()
    assert warnings == []
    texts = [line["text"] for line in scene["lines"]]
    assert "「謝謝你願意關心我。」" not in texts              # the rejected take is gone
    assert "「你也是來躲太陽的嗎？」" in texts and "「算了。」" in texts
    spoken = [line for line in scene["lines"] if line.get("speaker") == "林清雪"]
    assert [line.get("expression") for line in spoken] == ["shy", "shy", "shy"]
    assert scene["lines"][2] == {"speaker": "玩家", "text": "「妳在畫什麼？」"}
    assert [c["text"] for c in scene["choices"]] == ["所以妳到底在畫什麼", "我坐這邊可以嗎"]
    assert scene["choices"][0]["effects"][0]["kind"] == "affection"
    # Round-trips through the real editor and validates as a Scene.
    report = commit_stage(stage, pack, write=True)
    assert report["written"] and "park_bench" in report["diff"]
    assert cli_main(["validate", str(pack)]) == 0


def test_actors_only_see_their_own_private_information(ctx):
    stage = Stage(_brief(), ctx, seed=1)
    seen: dict[str, str] = {}

    def responder(request: StageRequest) -> str:
        seen.setdefault(request.role, _all_text(request))
        return {"director": "下一個：player\n提示：問\n節拍：stay" if "player" not in seen
                else ("下一個：narrator\n提示：湖\n節拍：stay" if "narrator" not in seen
                      else "下一個：heroine_1\n提示：反問\n節拍：stay"),
                "player": "「妳在畫什麼？」", "narrator": "湖面很平。",
                "actor": "「沒什麼。」"}.get(request.role, "")

    run_stage(stage, ScriptedBackend(responder=responder), max_requests=6)
    assert SECRET in seen["actor"] and SECRET in seen["director"]
    assert SECRET not in seen["player"] and SECRET not in seen["narrator"]
    # Objectives are private too; beats belong to the director alone.
    assert "有一點想被問" in seen["actor"] and "有一點想被問" not in seen["player"]
    assert "兩個人講的都是別的事" in seen["director"]
    for role in ("actor", "player", "narrator"):
        assert "兩個人講的都是別的事" not in seen[role]


def test_natural_order_is_seeded_and_survives_a_bad_director(ctx):
    def run(seed: int) -> list[str]:
        stage = Stage(_brief(order="natural", narration="none", choices=[],
                             max_turns=6, beats=[]), ctx, seed=seed)
        run_stage(stage, ScriptedBackend(default="「嗯。」"))
        return [turn.speaker_id for turn in stage.turns]
    assert run(3) == run(3)
    stage = Stage(_brief(choices=[], max_turns=3, beats=[]), ctx, seed=3)
    run_stage(stage, ScriptedBackend(by_role={"director": ["???"] * 9},
                                     default="「嗯。」"))
    assert stage.done and len(stage.turns) >= 3
    assert any(event["event"] == "director_unparsed" for event in stage.events)


def test_actor_cannot_put_words_in_another_mouth(ctx):
    stage = Stage(_brief(order="round_robin", narration="none", choices=[],
                         beats=[], max_turns=1, retakes=1), ctx, seed=1)
    backend = ScriptedBackend(default="「好。」\n玩家：「那我走了。」")
    run_stage(stage, backend)
    assert [turn.text for turn in stage.turns] == ["好。"]
    assert stage.turns[0].attempts == 2
    assert any("fmt.speaks_for_other" in event.get("hits", [])
               for event in stage.events)


def test_stage_serialises_mid_run_and_rejects_wrong_ids(ctx):
    stage = Stage(_brief(), ctx, seed=5)
    opening = stage.next_request()
    assert opening.role == "narrator"                        # sets the place first
    stage.submit(opening.id, "湖面沒有風。")
    first = stage.next_request()
    assert first.role == "director" and stage.next_request() is first   # idempotent
    with pytest.raises(StageError):
        stage.submit("nope", "x")
    stage.submit(first.id, "下一個：heroine_1\n提示：先講別的\n節拍：stay")
    pending = stage.next_request()
    data = json.loads(json.dumps(stage.to_dict(), ensure_ascii=False))
    resumed = Stage.from_dict(data, StageContext.from_pack("demo_pack", seed=7))
    again = resumed.next_request()
    assert again.id == pending.id and again.messages == pending.messages
    result = resumed.submit(again.id, "<sprite:害羞>\n「沒什麼。」")
    assert result["accepted"] and resumed.turns[-1].expression == "shy"
    assert resumed.turns[-1].note == "先講別的"


def test_restage_changes_wording_only_and_keeps_required_words(ctx, pack):
    scene = ctx.state.story.get("meet_heroine")
    stage = Stage.restage(scene, ctx, seed=1)
    skipped = {step.skip_reason for step in stage.brief.script if not step.rewrite}
    assert "narration not selected" in skipped
    # Line 4 must keep "素描本" (a world-book key); the rewrite drops it every time.
    assert stage.brief.script[4].keep == ["素描本"]
    requests: list[StageRequest] = []

    def responder(request: StageRequest) -> str:
        requests.append(request)
        text = request.messages[-1]["content"]
        if "弄丟了一本素描本" in text:
            return "「我的本子不見了。」"
        if "可以幫我看看" in text:
            return "「欸，可以幫我看一下這張嗎？」"
        if "少了太多" not in text:
            return "「裡面……畫了一半。」"         # natural, and half the line is gone
        return "「裡面很多都只畫了一半。」\n「你有看到的話，跟我說一聲就好。」"

    run_stage(stage, ScriptedBackend(responder=responder))
    # A rewrite that sheds most of its source is sent back with the reason.
    assert stage.turns[5].attempts == 2 and not stage.turns[5].fallback
    assert stage.turns[5].text == "裡面很多都只畫了一半。你有看到的話，跟我說一聲就好。"
    assert "這一句的原稿" in _all_text(requests[0])
    assert "必須原樣出現" in _all_text(next(
        r for r in requests if "弄丟了一本素描本" in r.messages[-1]["content"]))
    edits = stage.to_edits()
    # The line that kept losing its required word falls back to the authored text.
    assert [edit["line_index"] for edit in edits] == [2, 5]
    assert stage.summary()["restage"]["kept_original"] == 1
    assert stage.turns[4].fallback and stage.turns[4].attempts == 3
    assert len(stage.turns) == len(scene.lines)
    before = (pack / "content/scenes/10_meet_heroine.yaml").read_text(encoding="utf-8")
    report = commit_stage(stage, pack, write=True)
    after = (pack / "content/scenes/10_meet_heroine.yaml").read_text(encoding="utf-8")
    assert report["written"] and before != after
    assert "「欸，可以幫我看一下這張嗎？」" in after and "accept_quest" in after
    assert after.count("- speaker:") == before.count("- speaker:")
    assert cli_main(["validate", str(pack)]) == 0
    assert cli_main(["smoke", str(pack)]) == 0


def test_chat_mode_alternates_with_a_human_and_swipes(ctx):
    brief = SceneBrief(id="chat", cast=["heroine_1"], player={"mode": "human"},
                       narration="none")
    stage = Stage(brief, ctx, seed=2, chat=True)
    assert stage.next_request().role == "human"
    stage.submit(stage.next_request().id, "妳在畫什麼？")
    request = stage.next_request()
    assert request.role == "actor" and "妳在畫什麼？" in _all_text(request)
    stage.submit(request.id, "「沒什麼。」")
    assert stage.next_request().role == "human"
    stage._pending = None
    assert stage.swipe() and stage.next_request().role == "actor"
    assert [turn.kind for turn in stage.turns] == ["player"]


# -------------------------------------------- lessons from a live-model run


def _solo(**updates) -> SceneBrief:
    """Only the heroine speaks, in turn order, with nothing else in the way."""
    base = dict(order="round_robin", narration="none", choices=[], beats=[],
                player=PlayerSpec(mode="silent"))
    return _brief(**{**base, **updates})


def test_action_rows_are_rationed_and_given_a_subject(ctx):
    def run(**updates) -> list[Turn]:
        said = iter(["你也是來躲太陽的嗎？", "湖面今天沒什麼風。", "我等一下要回書店。",
                     "那邊的鴨子又換位置了。"])
        things = iter(["紙翻到背面", "筆收進口袋", "包包的拉鍊拉上", "手放回膝蓋上"])

        def responder(request: StageRequest) -> str:
            return f"「{next(said)}」\n（把{next(things)}）"
        stage = Stage(_solo(max_turns=4, **updates), ctx, seed=1)
        run_stage(stage, ScriptedBackend(responder=responder))
        return stage.turns

    turns = run()
    actions = [turn for turn in turns if turn.kind == "narration"]
    # Every reply offered an action; only every other turn keeps one.
    assert [turn.take for turn in actions] == [0, 2]
    assert actions[0].text == "林清雪把紙翻到背面。"            # subject and full stop
    assert len([t for t in run(action_every=1) if t.kind == "narration"]) == 4
    assert [t for t in run(action_every=0) if t.kind == "narration"] == []


def test_actor_prompt_says_whether_an_action_is_on_offer(ctx):
    stage = Stage(_solo(max_turns=2), ctx, seed=1)
    first = stage.next_request()
    assert "這一輪可以加一行動作" in first.messages[-1]["content"]
    stage.submit(first.id, "「你也是來躲太陽的嗎？」\n（林清雪把紙壓在大腿底下。）")
    second = stage.next_request()
    assert "這一輪只寫台詞，不加動作行" in second.messages[-1]["content"]


def test_a_turn_with_no_spoken_line_or_a_repeated_line_is_retaken(ctx):
    stage = Stage(_brief(order="round_robin", narration="none", choices=[], beats=[],
                         max_turns=3), ctx, seed=1)
    backend = ScriptedBackend(by_speaker={
        "heroine_1": ["「風變涼了。」", "「風變涼了。」", "「該回去了。」"],
        "player": ["（你看著遠處的水面）", "「妳在畫什麼？」\n（看向那張紙）"]})
    run_stage(stage, backend)
    assert [(t.kind, t.text, t.attempts) for t in stage.turns] == [
        ("dialogue", "風變涼了。", 1), ("player", "妳在畫什麼？", 2),
        ("narration", "你看向那張紙。", 2), ("dialogue", "該回去了。", 2)]
    retakes = [event["hits"] for event in stage.events if event["event"] == "retake"]
    assert retakes == [["fmt.no_speech"], ["repeat.line"]]


def test_when_retakes_run_out_broken_rows_are_repaired_or_dropped(ctx):
    stage = Stage(_solo(max_turns=3, retakes=0), ctx, seed=1)
    backend = ScriptedBackend(by_speaker={"heroine_1": [
        "「風變涼了。」", "「風變涼了。」\n「紙都拿不住。」", "「路过，头发乱了。」"]})
    run_stage(stage, backend)
    # No retakes left: the repeated row goes, the Simplified slip is converted.
    assert [turn.text for turn in stage.turns] == ["風變涼了。", "紙都拿不住。",
                                                   "路過，頭髮亂了。"]
    events = {event["event"]: event for event in stage.events}
    assert events["rows_dropped"]["rows"] == ["風變涼了。"]
    assert events["script_repaired"]["was"] == "路过，头发乱了。"
    assert stage.review().by_rule() == {}
    # A take that only repeats leaves nothing behind rather than a duplicate.
    stage = Stage(_solo(max_turns=2, retakes=1), ctx, seed=1)
    run_stage(stage, ScriptedBackend(default="「風變涼了。」\n（在長椅上坐下）"))
    assert [turn.text for turn in stage.turns] == ["風變涼了。", "林清雪在長椅上坐下。"]


def test_small_script_slips_are_mended_without_spending_a_retake(ctx):
    stage = Stage(_solo(max_turns=2), ctx, seed=1)
    backend = ScriptedBackend(by_speaker={"heroine_1": [
        "「是有点凉了。」",                      # two stray characters: mended in place
        "「这个地方我们以前说过的话还记得吗？」",   # drifted: written again
        "「這裡我們以前來過。」"]})
    run_stage(stage, backend)
    assert [(turn.text, turn.attempts) for turn in stage.turns] == [
        ("是有點涼了。", 1), ("這裡我們以前來過。", 2)]
    kinds = [event["event"] for event in stage.events]
    assert kinds.count("script_repaired") == 1 and kinds.count("retake") == 1


def test_a_beat_keeps_its_promise_before_the_scene_moves_on(ctx):
    stage = Stage(_brief(narration="none", choices=[]), ctx, seed=1)
    seen: list[str] = []
    lines = iter(["「你也是來躲太陽的嗎？」", "「我的素描本如果還在的話……」"])

    def responder(request: StageRequest) -> str:
        if request.role == "director":
            seen.append(request.messages[-1]["content"])
            # Jumps to the second beat, then tries to leave before anyone has
            # said what that beat is for.
            return {1: "下一個：heroine_1\n提示：先講別的\n節拍：next"}.get(
                len(seen), "下一個：end\n節拍：next")
        seen.append(request.messages[-1]["content"])
        return next(lines)

    run_stage(stage, ScriptedBackend(responder=responder))
    assert stage.done and stage.beat == 2            # both beats were played
    events = [event["event"] for event in stage.events]
    assert events.count("end_refused") == 1 and "director_end" in events
    assert "這一拍還沒有人在台詞裡說到：素描本" in seen[2]      # the director is told
    assert "［這一句要說到］素描本" in seen[3]                  # and so is the actor
    assert stage.turns[-1].text == "我的素描本如果還在的話……"


def test_one_speaker_cannot_hold_the_floor(ctx):
    stage = Stage(_brief(narration="none", choices=[], beats=[], max_turns=4), ctx,
                  seed=1)
    said = iter(["你也是來躲太陽的嗎？", "湖面今天沒什麼風。", "妳在畫什麼？",
                 "那邊的鴨子又換位置了。"])
    directed: list[str] = []

    def responder(request: StageRequest) -> str:
        if request.role == "director":
            directed.append(request.messages[-1]["content"])
            return "下一個：heroine_1\n提示：繼續躲\n節拍：stay"
        return f"「{next(said)}」"

    run_stage(stage, ScriptedBackend(responder=responder))
    assert [t.speaker_id for t in stage.turns] == ["heroine_1", "heroine_1", "player",
                                                   "heroine_1"]
    assert stage.turns[2].note == ""              # the note was meant for someone else
    assert any(e["event"] == "director_overruled" for e in stage.events)
    assert "林清雪已經連續開口 2 次" in directed[2]
    assert "玩家到現在還沒說過話" in directed[2]


def test_the_narrator_opens_and_delivers_the_written_ending(ctx):
    stage = Stage(_brief(choices=[], beats=[]), ctx, seed=1)
    cues: list[str] = []

    def responder(request: StageRequest) -> str:
        if request.role == "narrator":
            cues.append(request.messages[-1]["content"])
            return "湖面沒有風。" if len(cues) == 1 else "那張紙還是背面朝上。"
        if request.role == "director":
            return "下一個：end" if stage.takes >= 2 else "下一個：heroine_1\n提示：躲"
        return "「你也是來躲太陽的嗎？」"

    run_stage(stage, ScriptedBackend(responder=responder))
    assert [t.speaker_id for t in stage.turns] == ["narrator", "heroine_1", "narrator"]
    assert "開場" in cues[0]
    assert "這場戲的最後一個畫面：停在那張被翻到背面的紙上" in cues[1]


# -------------------------------------------------------------------- backends


def test_shape_chat_hoists_folds_and_starts_with_user():
    messages = [
        {"role": "system", "content": "S1"}, {"role": "system", "content": "S2"},
        {"role": "assistant", "content": "a0"},
        {"role": "system", "content": "mid"},          # after assistant: must fold
        {"role": "user", "content": "u1"},
        {"role": "system", "content": "tail1"}, {"role": "system", "content": "tail2"},
    ]
    system, chat = shape_chat(messages, mid_system=True)
    assert system == "S1\n\nS2"
    assert [m["role"] for m in chat] == ["user", "assistant", "user", "system"]
    assert chat[2]["content"] == "mid\nu1" and chat[3]["content"] == "tail1\n\ntail2"
    _system, folded = shape_chat(messages, mid_system=False)
    assert [m["role"] for m in folded] == ["user", "assistant", "user"]
    assert folded[-1]["content"].endswith("tail2")


class _FakeMessages:
    def __init__(self, log, fail_once=None):
        self.log, self.fail_once = log, fail_once

    def create(self, **kwargs):
        self.log.append(kwargs)
        if self.fail_once is not None:
            exc, self.fail_once = self.fail_once, None
            raise exc

        class _Block:
            type, text = "text", "「嗯。」"

        class _Usage:
            input_tokens, output_tokens, cache_read_input_tokens = 10, 3, 4

        class _Response:
            stop_reason, content, usage, stop_details = "end_turn", [_Block()], _Usage(), None
        return _Response()


class _FakeClient:
    def __init__(self, fail_once=None):
        self.stable_calls, self.beta_calls = [], []
        self.messages = _FakeMessages(self.stable_calls)
        self.beta = type("Beta", (), {"messages": _FakeMessages(self.beta_calls,
                                                                fail_once)})()


def _request() -> StageRequest:
    return StageRequest(id="r1", role="actor", speaker_id="h", messages=[
        {"role": "system", "content": "CARD"}, {"role": "user", "content": "A：「嗨」"},
        {"role": "system", "content": "DIRECTION"}],
        params={"effort": "low", "temperature": 0.9})


def test_anthropic_backend_request_shape():
    pytest.importorskip("anthropic")
    client = _FakeClient()
    backend = AnthropicBackend(client=client)
    assert backend.complete(_request()) == "「嗯。」"
    (kwargs,) = client.beta_calls
    assert kwargs["model"] == "claude-opus-5-5"
    assert kwargs["fallbacks"] == "default"
    assert kwargs["betas"] == ["server-side-fallback-2026-07-01"]
    assert kwargs["output_config"] == {"effort": "low"}
    assert kwargs["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert [m["role"] for m in kwargs["messages"]] == ["user", "system"]
    for forbidden in ("temperature", "top_p", "top_k", "thinking"):
        assert forbidden not in kwargs                     # rejected by current models
    assert backend.usage["cache_read_input_tokens"] == 4
    # A model without a server-side fallback uses the stable surface.
    plain = _FakeClient()
    AnthropicBackend(client=plain, model="claude-haiku-5-5").complete(_request())
    assert plain.stable_calls and "fallbacks" not in plain.stable_calls[0]
    assert not plain.beta_calls


def test_anthropic_backend_folds_system_when_the_model_refuses_it():
    anthropic = pytest.importorskip("anthropic")
    import httpx2 as httpx
    response = httpx.Response(400, request=httpx.Request("POST", "https://x/v1/messages"))
    error = anthropic.BadRequestError(
        "role 'system' is not supported on this model", response=response, body=None)
    client = _FakeClient(fail_once=error)
    backend = AnthropicBackend(client=client)
    assert backend.complete(_request()) == "「嗯。」"
    first, second = client.beta_calls
    assert [m["role"] for m in first["messages"]] == ["user", "system"]
    assert [m["role"] for m in second["messages"]] == ["user"]
    assert "DIRECTION" in second["messages"][0]["content"]


def test_openai_compat_backend_against_a_local_server():
    seen: dict = {}

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            seen.update(path=self.path, auth=self.headers.get("Authorization"), body=body)
            payload = json.dumps({"choices": [{"message": {"content": "「好。」"}}],
                                  "usage": {"prompt_tokens": 120, "completion_tokens": 4}})
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(payload.encode("utf-8"))

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        backend = OpenAICompatBackend(
            base_url=f"http://127.0.0.1:{server.server_port}/v1", model="local",
            api_key="k", extra_body={"chat_template_kwargs": {"enable_thinking": False}})
        assert backend.complete(_request()) == "「好。」"
    finally:
        server.shutdown()
        server.server_close()
    assert seen["path"] == "/v1/chat/completions" and seen["auth"] == "Bearer k"
    assert [m["role"] for m in seen["body"]["messages"]] == ["system", "user"]
    assert seen["body"]["temperature"] == 0.9 and seen["body"]["model"] == "local"
    # Extra fields ride along (how a local server's thinking mode is switched off),
    # and the run's cost is kept.
    assert seen["body"]["chat_template_kwargs"] == {"enable_thinking": False}
    assert backend.usage == {"requests": 1, "input_tokens": 120, "output_tokens": 4}
    with pytest.raises(BackendError):
        OpenAICompatBackend().complete(_request())


def test_make_backend_and_echo(ctx):
    stage = Stage(_brief(order="natural", choices=[], beats=[], max_turns=4), ctx,
                  seed=1)
    backend = make_backend("echo", stage=stage, config={})
    assert isinstance(backend, EchoBackend)
    run_stage(stage, backend)
    assert stage.done and any(turn.speaker == "林清雪" for turn in stage.turns)
    claude = make_backend(None, config={"anthropic": {"models": {"director": "x"}}})
    assert isinstance(claude, AnthropicBackend) and claude.model == "claude-opus-5-5"
    with pytest.raises(BackendError):
        make_backend("nope", config={})


# ------------------------------------------------------------------------- CLI


def test_cli_cards_lint_prompt(pack, capsys):
    assert cli_main(["tavern", "cards", str(pack), "--format", "json"]) == 0
    cards = json.loads(capsys.readouterr().out)
    by_id = {card["id"]: card for card in cards["cards"]}
    assert by_id["heroine_1"]["source"] == "file:heroine_1.yaml"
    assert by_id["shopkeeper_uncle"]["source"] == "npc" and cards["issues"] == []
    assert cli_main(["tavern", "lint", str(pack), "--format", "json",
                     "--max-score", "200"]) == 0
    assert "worst_score" in json.loads(capsys.readouterr().out)
    assert cli_main(["tavern", "lint", str(pack), "--scene", "nope"]) == 1
    capsys.readouterr()
    assert cli_main(["tavern", "prompt", str(pack), "--as", "heroine_1",
                     "--brief", str(BRIEF), "--full"]) == 0
    out = capsys.readouterr().out
    assert "privateKnowledge" in out and "world book" in out and SECRET in out


def test_cli_enact_echo_writes_a_playable_scene(pack, capsys):
    args = ["tavern", "enact", str(pack), "--brief", str(BRIEF), "--backend", "echo",
            "--seed", "3"]
    assert cli_main(args) == 0
    assert "dry run" in capsys.readouterr().out
    assert not (pack / "content/scenes/_generated.yaml").exists()
    assert cli_main([*args, "--write", "--into-file", "tavern_park"]) == 0
    capsys.readouterr()
    written = (pack / "content/scenes/tavern_park.yaml").read_text(encoding="utf-8")
    assert "id: park_bench" in written
    assert cli_main(["validate", str(pack)]) == 0
    assert cli_main([*args, "--write"]) == 1               # the id now exists
    assert "already exists" in capsys.readouterr().err


def test_cli_relay_step_loop(pack, tmp_path, capsys):
    state = tmp_path / "stage.json"
    assert cli_main(["tavern", "enact", str(pack), "--brief", str(BRIEF),
                     "--backend", "relay", "--state", str(state), "--seed", "1"]) == 0
    view = json.loads(capsys.readouterr().out)
    assert view["request"]["role"] == "narrator" and view["request"]["messages"]
    replies = ["湖面沒有風。",
               "下一個：heroine_1\n提示：先講別的\n節拍：stay",
               "<sprite:有點慌>\n「你也是來躲太陽的嗎？」",
               "下一個：end\n節拍：next",                    # refused: 素描本 is still owed
               "「我的素描本如果還在的話……」",
               "下一個：end\n節拍：stay",
               "那張紙還是背面朝上。",
               "ask_again｜所以妳在畫什麼\nlet_it_go｜我可以坐這嗎"]
    for reply in replies:
        assert cli_main(["tavern", "step", str(state), "--submit", reply]) == 0
        view = json.loads(capsys.readouterr().out)
    assert view["done"]
    assert cli_main(["tavern", "step", str(state), "--request-id", "x",
                     "--submit", "late"]) == 1
    capsys.readouterr()
    assert cli_main(["tavern", "step", str(state), "--write"]) == 0
    capsys.readouterr()
    scenes = (pack / "content/scenes/_generated.yaml").read_text(encoding="utf-8")
    assert "「你也是來躲太陽的嗎？」" in scenes and "expression: shy" in scenes
    memo = (pack / "content/tavern/expression_memo.yaml").read_text(encoding="utf-8")
    assert "有點慌: shy" in memo                             # remembered across steps


def test_cli_card_export_import_and_chat_import(pack, tmp_path, capsys):
    png = tmp_path / "card.png"
    assert cli_main(["tavern", "card-export", str(pack), "heroine_1", "--out",
                     str(png), "--spec", "v3"]) == 0
    (pack / "content/tavern/cards/heroine_1.yaml").unlink()
    assert cli_main(["tavern", "card-import", str(pack), str(png)]) == 0
    out = capsys.readouterr().out
    assert "bound to character" in out
    assert cli_main(["tavern", "card-import", str(pack), str(png)]) == 1   # no clobber
    capsys.readouterr()
    assert cli_main(["tavern", "cards", str(pack), "--format", "json"]) == 0
    cards = {c["id"]: c for c in json.loads(capsys.readouterr().out)["cards"]}
    assert cards["heroine_1"]["secrets"] == 2 and cards["heroine_1"]["examples"] == 8

    chat = tmp_path / "chat.jsonl"
    chat.write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in [
        {"user_name": "阿哲", "character_name": "林清雪"},
        {"name": "林清雪", "is_user": False,
         "mes": "<sprite:害羞>\n*她把紙翻到背面。*\n「你怎麼在這。」"},
        {"name": "阿哲", "is_user": True, "mes": "路過。"},
    ]), encoding="utf-8")
    assert cli_main(["tavern", "import-chat", str(pack), str(chat), "--scene-id",
                     "from_chat", "--write"]) == 0
    capsys.readouterr()
    text = (pack / "content/scenes/_generated.yaml").read_text(encoding="utf-8")
    assert "她把紙翻到背面。" in text and "expression: shy" in text
    assert "「路過。」" in text
    assert cli_main(["validate", str(pack)]) == 0

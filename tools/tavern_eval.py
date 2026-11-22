"""Measure the tavern stage against a real model.

Stages one brief several times, and (optionally) asks the same model to write
the same scene alone in one request, then prints the numbers side by side.
Nothing is written to the pack.

    uv run python tools/tavern_eval.py games/demo_pack \\
        --brief games/demo_pack/content/tavern/briefs/park_bench.yaml \\
        --backend openai --runs 5 --compare-single --out /tmp/tavern-eval

The backend comes from ``~/.world-gal-game/tavern.yaml`` (see docs/tavern.md,
"連線設定"). Every run spends real requests; ``--runs`` is yours to choose.

What it measures is structure, not taste: whether the player speaks, whether
each speech drags an action row behind it, whether a line repeats, whether a
Simplified character leaked into Traditional text, whether the words a beat
must surface were actually said, how the scene ended, and what it cost. Read
the scenes too (``--out`` keeps them): a scene can pass every one of these and
still be dull.
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from pathlib import Path
from typing import Any

from world_gal_game.tavern.backends import Backend, make_backend, run_stage, shape_chat
from world_gal_game.tavern.reply import parse_reply
from world_gal_game.tavern.slop import lines_from_scene, normalize_line
from world_gal_game.tavern.stage import (NARRATOR, PLAYER, SceneBrief, Stage,
                                         StageContext, StageRequest, _Cue)

_SINGLE = {
    "zh": {
        "system": "你是視覺小說的編劇，要一個人把下面這場戲從頭寫到尾，"
                  "每個角色的台詞和旁白都由你寫。",
        "sheet": "【角色設定：{name}（以下原本是給扮演這個角色的人看的）】",
        "ask": "寫出整場戲，{low} 到 {high} 行。格式：\n名字：「台詞」\n（旁白或動作）\n"
               "主角的台詞寫成「{user}：「……」」。不要其他說明。",
    },
    "en": {
        "system": "You are the writer of a visual novel and write this whole scene "
                  "yourself: every character's lines and the narration.",
        "sheet": "[Character sheet: {name} (originally written for whoever plays them)]",
        "ask": "Write the whole scene in {low} to {high} rows. Format:\n"
               "Name: “line”\n(narration or action)\n"
               "The protagonist's lines are written as {user}: “…”. Nothing else.",
    },
}


class Counted(Backend):
    """Wrap a backend to count requests and wall time."""

    def __init__(self, inner: Backend) -> None:
        self.inner = inner
        self.requests = 0
        self.seconds = 0.0
        self.trace: list[dict[str, Any]] = []

    def complete(self, request: StageRequest) -> str:
        started = time.time()
        reply = self.inner.complete(request)
        elapsed = time.time() - started
        self.requests += 1
        self.seconds += elapsed
        self.trace.append({"role": request.role, "speaker": request.speaker_id,
                           "seconds": round(elapsed, 2), "messages": request.messages,
                           "reply": reply})
        return reply


def _rows(stage: Stage) -> list[dict[str, Any]]:
    return [{"kind": turn.kind, "speaker_id": turn.speaker_id, "speaker": turn.speaker,
             "text": turn.text, "attempts": turn.attempts} for turn in stage.turns]


def measure(rows: list[dict[str, Any]], stage: Stage) -> dict[str, Any]:
    """Structural numbers for one scene (staged or single-author)."""
    spoken = [row for row in rows if row["kind"] != "narration"]
    seen: set[str] = set()
    repeats = 0
    for row in rows:
        key = normalize_line(row["text"])
        if len(key) >= 4 and key in seen:
            repeats += 1
        seen.add(key)
    runs = tagged = 0
    for index, row in enumerate(rows):
        if row["kind"] == "narration":
            continue
        following = rows[index + 1] if index + 1 < len(rows) else None
        if following is not None and following["kind"] != "narration" \
                and following["speaker_id"] == row["speaker_id"]:
            continue
        runs += 1
        tagged += int(following is not None and following["kind"] == "narration")
    scene = {"id": "eval", "lines": [
        {"speaker": None if row["kind"] == "narration" else row["speaker"],
         "text": row["text"] if row["kind"] == "narration"
         else f"{stage.style.quote_open}{row['text']}{stage.style.quote_close}"}
        for row in rows]}
    report = stage.linter.lint(lines_from_scene(scene))
    must = [word for beat in stage.brief.beats for word in beat.must_include]
    said = "\n".join(row["text"] for row in spoken)
    lengths = [len(normalize_line(row["text"])) for row in spoken] or [0]
    return {
        "lines": len(rows), "spoken": len(spoken),
        "player_lines": sum(1 for row in spoken if row["kind"] == "player"),
        "action_rows": len(rows) - len(spoken),
        "action_tag_rate": round(tagged / runs, 2) if runs else 0.0,
        "repeats": repeats,
        "script_slips": report.by_rule().get("script.mixed", 0)
        + report.by_rule().get("script.mixed_hans", 0),
        "must_said": f"{sum(1 for word in must if word in said)}/{len(must)}",
        "mean_len": round(statistics.fmean(lengths), 1),
        "lint": round(report.score, 1), "grade": report.grade,
        "lint_rules": report.by_rule(),
    }


def run_staged(pack: str, brief_path: Path, backend: Backend, seed: int
               ) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    ctx = StageContext.from_pack(pack, seed=seed)
    brief = SceneBrief.load(brief_path)
    ctx.apply_setup(brief.setup)
    stage = Stage(brief, ctx, seed=seed)
    counted = Counted(backend)
    run_stage(stage, counted, max_requests=brief.max_turns * 8 + 20)
    rows = _rows(stage)
    numbers = measure(rows, stage)
    events = [event["event"] for event in stage.events]
    numbers.update({
        "ended": "director" if "director_end" in events else (
            "beats" if stage.beat >= len(brief.beats) and brief.beats else "cap"),
        "retaken": sum(1 for turn in stage.turns if turn.attempts > 1),
        "held": events.count("beat_held") + events.count("end_refused"),
        "overruled": events.count("director_overruled"),
        "requests": counted.requests, "seconds": round(counted.seconds, 1),
    })
    return numbers, rows, counted.trace


def run_single(pack: str, brief_path: Path, backend: Backend, seed: int
               ) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    ctx = StageContext.from_pack(pack, seed=seed)
    brief = SceneBrief.load(brief_path)
    ctx.apply_setup(brief.setup)
    stage = Stage(brief, ctx, seed=seed)
    text = _SINGLE["zh" if stage.style.locale.startswith("zh") else "en"]
    sheets = []
    for member in brief.cast:
        system, _chat = shape_chat(stage._actor_request(_Cue(member.id)).messages,
                                   mid_system=False)
        sheets.append(text["sheet"].format(name=stage.cards[member.id].name)
                      + "\n" + system)
    director = stage._director_request().messages[0]["content"]
    brief_text = director[len(stage.T["director_system"]):].strip()
    request = StageRequest(
        id="single", role="narrator", purpose="single-author baseline",
        messages=[{"role": "system", "content": "\n\n".join(
            [text["system"], *sheets, brief_text])},
            {"role": "user", "content": text["ask"].format(
                low=brief.max_turns, high=brief.max_turns + 8, user=stage.user)}],
        params={"max_tokens": 2000})
    counted = Counted(backend)
    reply = counted.complete(request)
    names = {card.name: cid for cid, card in stage.cards.items()}
    names[stage.user] = PLAYER
    rows = []
    for line in parse_reply(reply, default_speaker=None, known_speakers=list(names),
                            style=stage.style, bare_is_speech=False).lines:
        if line.kind == "dialogue":
            speaker_id = names.get(line.speaker or "", line.speaker or "?")
            rows.append({"kind": "player" if speaker_id == PLAYER else "dialogue",
                         "speaker_id": speaker_id, "speaker": line.speaker,
                         "text": line.text, "attempts": 1})
        else:
            rows.append({"kind": "narration", "speaker_id": NARRATOR, "speaker": "",
                         "text": line.text, "attempts": 1})
    numbers = measure(rows, stage)
    numbers.update({"ended": "author", "retaken": 0, "held": 0, "overruled": 0,
                    "requests": 1, "seconds": round(counted.seconds, 1)})
    return numbers, rows, counted.trace


COLUMNS = ["lines", "spoken", "player_lines", "action_rows", "action_tag_rate",
           "repeats", "script_slips", "must_said", "mean_len", "lint", "ended",
           "retaken", "held", "overruled", "requests", "seconds", "tokens_in",
           "tokens_out"]


def _mean(label: str, runs: list[dict[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {"run": f"{label} mean ({len(runs)})"}
    for column in COLUMNS:
        values = [run.get(column) for run in runs]
        numeric = [value for value in values if isinstance(value, (int, float))]
        if numeric and len(numeric) == len(values):
            out[column] = round(statistics.fmean(numeric), 2)
        elif column == "must_said":
            got = sum(int(str(value).split("/")[0]) for value in values)
            want = sum(int(str(value).split("/")[1]) for value in values)
            out[column] = f"{got}/{want}"
        else:
            kinds = sorted({str(value) for value in values})
            out[column] = " ".join(f"{kind}:{values.count(kind)}" for kind in kinds)
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("pack")
    parser.add_argument("--brief", type=Path, required=True)
    parser.add_argument("--backend", default=None)
    parser.add_argument("--model", default=None)
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--seed", type=int, default=1, help="first seed; runs use seed, seed+1, …")
    parser.add_argument("--compare-single", action="store_true",
                        help="also have the model write the scene alone, as a baseline")
    parser.add_argument("--out", type=Path, default=None,
                        help="directory for transcripts and request traces")
    parser.add_argument("--format", choices=("text", "json"), default="text")
    args = parser.parse_args(argv)

    backend = make_backend(args.backend, model=args.model)
    if args.out is not None:
        args.out.mkdir(parents=True, exist_ok=True)
    table: list[dict[str, Any]] = []
    modes = [("stage", run_staged)] + ([("single", run_single)] if args.compare_single
                                       else [])
    for label, runner in modes:
        runs = []
        for offset in range(args.runs):
            seed = args.seed + offset
            before = dict(getattr(backend, "usage", {}) or {})
            numbers, rows, trace = runner(args.pack, args.brief, backend, seed)
            after = getattr(backend, "usage", {}) or {}
            numbers["tokens_in"] = after.get("input_tokens", 0) - before.get(
                "input_tokens", 0)
            numbers["tokens_out"] = after.get("output_tokens", 0) - before.get(
                "output_tokens", 0)
            numbers["run"] = f"{label} seed {seed}"
            runs.append(numbers)
            table.append(numbers)
            if args.out is not None:
                (args.out / f"{label}-{seed}.json").write_text(json.dumps(
                    {"numbers": numbers, "lines": rows, "trace": trace},
                    ensure_ascii=False, indent=1), encoding="utf-8")
            print(f"[{label} seed {seed}] {numbers['lines']} lines, "
                  f"{numbers['requests']} requests, {numbers['seconds']}s",
                  file=sys.stderr)
        table.append(_mean(label, runs))
    if args.format == "json":
        print(json.dumps(table, ensure_ascii=False, indent=1))
        return 0
    widths = {column: max(len(column), *(len(str(row.get(column, ""))) for row in table))
              for column in ["run", *COLUMNS]}
    print("  ".join(column.ljust(widths[column]) for column in ["run", *COLUMNS]))
    for row in table:
        print("  ".join(str(row.get(column, "")).ljust(widths[column])
                        for column in ["run", *COLUMNS]))
    return 0


if __name__ == "__main__":
    sys.exit(main())

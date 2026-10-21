# Warm NDJSON Session Protocol

The **warm session** is the engine's fast control plane for AI agents: load the
pack once, then stream newline-delimited JSON (NDJSON) ops over stdio. It is the
deliberate, faster-than-MCP path — a cold `--headless --script` invocation pays
the pack-load tax (parse YAML, build `GameState`, register plugins) on *every*
call, and a network RPC adds a per-op round-trip on top. The warm session pays
the load tax once; the marginal cost of an op is the op itself.

The op vocabulary is **identical** to `HeadlessSession.run_script` — there is no
second schema to learn and no envelope to translate.

Implementation: [`world_gal_game/dev/session_server.py`](../world_gal_game/dev/session_server.py).
A machine-readable copy of this spec ships in code via
`world_gal_game.dev.agent_bundle.session_protocol_schema()` and in the
`docs export` bundle as `session-protocol.json`, so it is available even when
the engine is pip-installed.

## Start

```bash
world-gal-game session --pack <pack> [--seed N]
# source checkout:
uv run python -m world_gal_game.cli session --pack demo_pack --seed 42
```

Pass `--seed N` to pin `GameState.rng()`; the same seed + same op stream
reproduces the run exactly.

## Line framing

- **Request:** one JSON object per non-blank line on stdin. Blank /
  whitespace-only lines are ignored (no response, no sequence advance).
- **Response:** one JSON object per request line on stdout, flushed
  immediately, carrying a monotonically increasing integer `seq` (one per
  non-blank input line, starting at 1) and an `ok` boolean.
- The server **never raises** out of its handler: JSON parse errors,
  unrecognized shapes, unknown control ops, and engine exceptions all come back
  as a JSON error envelope (below).

## Message shapes

A request line is dispatched as one of three shapes.

### 1. Control op
An object whose `op` is a `__dunder__` verb. These manage the session itself.

| op | response |
|---|---|
| `{"op":"__ping__"}` | `{"ok":true,"seq":n,"pong":true}` — liveness |
| `{"op":"__inspect__"}` | `{"ok":true,"seq":n,"snapshot":<inspect()>}` — full state view, including `locale.requested/effective/source/default` |
| `{"op":"__affordances__"}` | `{"ok":true,"seq":n,"affordances":<affordances()>}` — current action space |
| `{"op":"__reset__"}` | rebuild a fresh session: `{"ok":true,"seq":n,"reset":true}` (error if no opener configured) |
| `{"op":"__quit__"}` | `{"ok":true,"seq":n,"bye":true}`, then the serve loop stops |
| `{"op":"__begin__"}` | open a structural-edit transaction: `{"ok":true,"seq":n,"tx":"begin"}` |
| `{"op":"__commit__"}` | write all staged edits, return aggregate `impact`: `{"ok":true,"seq":n,"tx":"commit","diff":"…","impact":{…}}` |
| `{"op":"__rollback__"}` | discard staged edits: `{"ok":true,"seq":n,"tx":"rollback","discarded":<bool>}` |
| `{"op":"__reload__"}` | rebuild the in-memory pack from disk so runtime ops see committed edits: `{"ok":true,"seq":n,"reloaded":true,"scenes":<int>}` |

### 2. Batch
An object with an `ops` array. The whole list runs through
`run_script` in one shot.

```json
{"ops": [{"op": "start_scene", "scene": "prologue"}, {"op": "next", "count": 3}]}
```

Response:

```json
{"ok": true, "seq": 1,
 "results": [ {"op": "...", "ok": true, "diff": {…}}, … ],
 "transcript": [ … ordered execution trace … ]}
```

`results` has one entry per op; each carries its own `ok` and, when that op
changed state, a structured `diff`. `transcript` is the ordered execution trace
of the whole batch.

### 3. Single op
Any other object is treated as a one-op batch.

```json
{"op": "move", "location": "cafe"}
```

遠距 schedule-map 行程需明確 opt in：

```json
{"op": "move", "location": "far_library", "distant": true}
```

它會重新驗證先前 affordances／UI 所依據的路徑與抵達狀態，原子套用總
`travel_cost`，中繼地點不會變成 visited，也不會觸發中途事件。

Response: `{"ok": <bool>, "seq": n, "result": {…}, "transcript": [ … ]}` — same
as a batch but with a single `result` instead of a `results` array.

## Ops

The same vocabulary as `HeadlessSession.run_script`.

**Runtime** (act on the live `GameState`):

```
move · examine · gift · start_scene · next · choose · chat · advance_time ·
set_flag · adjust_affection · inspect · apply · check · assert ·
affordances · snapshot · restore · locale
```

`locale` takes a `locale` language id and changes only the current session's
presentation authority:

```json
{"op": "locale", "locale": "en"}
```

It succeeds only when no dialogue scene is active (`current_scene` is `null`).
On success the result contains `requested` and `effective`; subsequent scene
titles, lines, speakers, sound captions, choices/cards, and localized time or
affection labels use that effective locale. During dialogue it returns
`ok:false`, retains the previous effective locale, and never replays line
effects. Unsupported or rejected locales fall back through the manifest's
validated default/source rather than selecting a file from user input.

This op is intentionally session-local: it does not write the per-pack
`locale_preferences` entry in `settings.json`, and `__reset__` rebuilds the
session from the opener's configuration. IDs, conditions, effects,
transitions, flags, numeric state, and asset paths are source-pack authority
and are never changed by `locale`.

`examine` takes `scene` and only opens an available `trigger: examine` hook at
the current location. `gift` takes `npc`, `item`, and optional `count` (which
must equal 1), and only gives an owned, unlocked item to an NPC present at the
current location, weekday, and time. Both are player-equivalent operations:
they revalidate conditions at execution time, reject while dialogue is open,
and appear as structured rows under `affordances.examine` /
`affordances.gift`; those arrays are empty during dialogue.

`move` is player-equivalent too and rejects while dialogue is open. At that
boundary `affordances.map.available` is false, exits are marked unavailable,
and `travel_options` / `guided_destinations` are empty.

`advance_time` takes optional `phases` (default 1), advances one phase at a
time, and stops at the first eligible `enter` / `auto` hook. Its result reports
the actual `phases_advanced` and may therefore be less than requested.

`start_scene` and `apply` are developer controls: the former opens a scene
without player hook/location/time validation, and the latter runs any
registered effect directly. `set_flag` and `adjust_affection` likewise mutate
test state directly. Do not use those controls as evidence that a player can
naturally reach the same state.

`check` / `assert` evaluate conditions / expectations; `snapshot` / `restore`
checkpoint and branch. Every runtime result carries a `changed` boolean (and a
structured `diff` when it changed state).

**Structural edit** (act on the pack on disk — the warm authoring loop):

```
edit.add_scene · edit.update_scene · edit.remove_scene · edit.add_choice ·
edit.update_line · edit.add_npc · edit.update_npc · edit.remove_npc ·
edit.add_location · edit.update_location · edit.remove_location · edit.add_item ·
edit.add_quest · edit.add_clue · edit.add_achievement · edit.add_resource
begin · commit · rollback · reload      (also as __begin__/__commit__/… control ops)
```

See [ai-native-contract.md](ai-native-contract.md) for each runtime op's
argument shape, and `capabilities --schema` for the per-kind JSON Schemas.

## Editing the pack (warm authoring loop)

An `edit.*` op stages a comment-preserving change via `PackEditor`, validates it
(a bad payload returns a *structured* `error` with `field`/`hint` and writes
nothing), and — outside a transaction — **autocommits**, returning the YAML
`diff` plus an `impact` delta in the same response:

```json
→ {"op":"edit.add_scene","scene":{"id":"lake_night","title":"…","lines":[…]}}
← {"ok":true,"seq":7,"result":{
     "op":"edit.add_scene","ok":true,"changed":true,
     "diff":"--- content/scenes/_generated.yaml …",
     "files":["content/scenes/_generated.yaml"],
     "impact":{"clean":false,"scenes_added":["lake_night"],
               "regressions":[{"kind":"orphan_scenes","items":["lake_night"]},
                              {"kind":"dead_ends","items":["unreachable:lake_night"]}],
               "improvements":[],"counts_delta":{"scenes":1}}}}
```

`impact` (from `world_model.world_delta`) is the *consequence* of the edit on the
pack's static world model — `clean` is `true` iff there are no `regressions`
(newly-unreachable endings/scenes, new dead-ends, used-but-undeclared flags,
orphaned new scenes); `improvements` reports the reverse. This is the
understand → edit → **verify** loop in one round-trip — no separate `self-check`
pass, no process re-spawn.

To group edits, wrap them in `begin … commit`: each `edit.*` then only stages
(`"staged":true`, no commit, no reload) and `commit` writes them all for one
aggregate `impact`; `rollback` discards. Edits never auto-reload the live
session — send `reload` (or `__reload__`) when ready to *play* the edited pack.

## Batch atomicity & rollback

By default a batch is **best-effort sequential and NON-atomic**:

- A failing op's result is `{"ok": false, "error": "<msg>"}`, and the batch
  **continues** with the next op. There is **no automatic rollback** of ops that
  already succeeded.
- The batch-level `ok` is the AND of every op's `ok`.

For all-or-nothing semantics, add `"atomic": true` to the batch:

```json
{"ops":[ {"op":"edit.add_scene", …}, {"op":"edit.add_choice", …} ], "atomic": true}
```

The server takes a runtime `snapshot` and opens an edit transaction up front, runs
the batch, and then **commits both** on success (`"atomic":"committed"`, with the
commit `impact` folded into the response) or **discards both** on any failing op
(`"atomic":"rolled_back"`) — staged edits are dropped (nothing is written) and the
live state is restored, so the batch leaves no half-applied change behind. Within
an atomic batch edits are staged, so you cannot edit *and* play the new content in
the same batch; commit, then `reload`, then play.

The manual pattern still works for runtime-only batches — `snapshot` before,
`restore` if any result is `ok:false` — the same machinery that powers branch
exploration and player rollback.

## Error envelope

```json
{"ok": false, "seq": <int>, "error": "<message>"}
```

Returned for JSON parse errors, unrecognized message shapes, unknown control
ops, and engine exceptions alike. The `seq` still advances for any non-blank
line, so a client can always pair a response to its request by order and by
`seq`.

## Example session

```
→ {"op":"__ping__"}
← {"ok":true,"seq":1,"pong":true}
→ {"ops":[{"op":"start_scene","scene":"prologue"},{"op":"next","count":2}]}
← {"ok":true,"seq":2,"results":[…],"transcript":[…]}
→ {"op":"snapshot","name":"branch_a"}
← {"ok":true,"seq":3,"result":{…},"transcript":[…]}
→ {"op":"choose","choice":"confess"}
← {"ok":true,"seq":4,"result":{…},"transcript":[…]}
→ {"op":"restore","name":"branch_a"}
← {"ok":true,"seq":5,"result":{…},"transcript":[…]}
→ {"op":"__quit__"}
← {"ok":true,"seq":6,"bye":true}
```

# Stable line identity

Dialogue order is presentation, not identity. A production pack may insert,
move, localize, or delete a line after players already have saves. Persisting
only `scene_id + line_index` makes an ordinary script edit change what “read”,
“resume here”, and “replay this line” mean.

`Line.loc_id` is the immutable, scene-local identity for those boundaries. It
is optional so legacy packs remain loadable, but a production pack should give
every line one and enforce that policy in CI.

## Runtime contract

For a line with `loc_id`, all identity-bearing runtime paths use the stable
key. The legacy index is retained only as historical/display metadata.

| Path | Stable evidence | Legacy fallback |
|---|---|---|
| Read / Skip | `scene_id + loc_id` | `scene_id + line_index` |
| Active story cursor | `loc_id` of the next unprocessed line, or end boundary | `current_line_index` |
| Save / quicksave resume | presentation `loc_id` plus next-line/end cursor evidence | presentation and story indices |
| Backlog | row `scene_id + loc_id` | row `scene_id + line_index` |
| Scene Replay | archived presentation `scene_id + loc_id` | archived line index |
| Localization | `scene_id.line.loc_id` | `scene_id.line.line_index` |

The two read-key paths are deliberately exclusive. Runtime must never ask
“stable key OR old index” for a line that has a `loc_id`: after inserting a new
line at an old index, that OR would immediately reproduce the skip bug this
contract prevents. Conversion of legacy keys belongs in an explicit pack save
migration, where the old authored ordering is known.

`loc_id` values are unique only within their scene. They match
`[A-Za-z0-9][A-Za-z0-9_-]{0,63}` and must not be renumbered or reused after
release. A deleted id should remain reserved in the pack's identity registry.

## Edit behavior

| Authored edit | Expected result |
|---|---|
| Insert a line | The new id is unread; existing lines keep read state and resume identity. |
| Reorder lines | Existing lines keep read/history/replay identity; the active cursor follows the saved next-line id. |
| Delete an already-read line | Its inert read key may remain; it cannot mark another line read. |
| Delete the active resume target | Load fails closed instead of guessing and replaying or skipping effects. |
| Delete a replay source line | That recollection is unavailable until an authored migration supplies an intentional replacement. |
| Change translated text | Backlog/resume can rebind presentation copy by stable id without replaying effects. |
| Legacy pack with no ids | Existing positional behavior remains unchanged. |

Scene Replay preserves the evaluated first-view text and historical sequence.
Stable identity is used to authorize the current source line and its media; it
does not silently rewrite the remembered dialogue into a different playthrough.

## Pack migration checklist

Introducing stable ids to a released pack is a breaking persistence change,
even though `loc_id` is an optional model field. Bump `pack_format_version` and
ship one transactional migration that updates, together:

1. authored scene lines;
2. localization keys and catalog source digests;
3. read-log line keys;
4. the serialized story catalogue used to rebind an active cursor;
5. dialogue history rows;
6. resume presentation and next-line/end evidence;
7. Scene Replay presentations, including cross-playthrough Clear Data;
8. production metadata such as voice and SFX cue sheets.

Generate all changes in a temporary tree, validate uniqueness and catalog
parity there, then replace the destination files only after every check passes.
Keep an issued-id registry or equivalent journal so future tooling allocates a
new id above the high-water mark instead of renumbering by current position.

For localization CI, run:

```bash
uv run python tools/i18n_extract.py <pack> \
  --check <pack>/content/i18n/messages.<source-language>.yaml \
  --src-lang <source-language> \
  --require-stable-line-ids
```

The source catalog itself should pass the same gate as every complete or
preview target catalog.

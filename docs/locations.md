# Locations

`content/locations.yaml` defines the navigable world.  
It is an optional file; if absent the game starts with an empty map.

## File structure

```yaml
regions:
  - {id: campus, name: "校園", color: [120, 180, 200]}
  - {id: town,   name: "校外", color: [180, 140, 110]}

locations:
  - id: library
    name: "圖書館"
    region: campus
    description: "藏書豐富，深夜似乎有人影。"
    background: assets/backgrounds/library.png
    backgrounds:
      morning:   assets/backgrounds/library_morning.png
      night:     assets/backgrounds/library_night.png
    map_x: 200
    map_y: 150
    exits:
      - main_quad                         # shorthand: target id only
      - target: secret_stacks             # full form
        label: "進入秘密書庫"
        description: "黃昏後請勿獨自進入"
        one_way: true
        requires_time: [evening, night]
        requires_flags: [met_heroine_1]
    npcs:
      - npc_id: librarian
        times: [morning, noon, afternoon]
      - npc_id: ghost_scholar
        times: [night, midnight]
        requires_flags: [chapter2_started]
    scene_hooks:
      - scene_id: library_intro
        trigger: enter
        once: true
      - scene_id: hidden_book
        trigger: examine
        requires_time: [midnight]
        requires_flags: [has_torch]
    tags: [indoor, academic]
```

## Fields

### Region

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `id` | string | yes | Unique identifier |
| `name` | string | yes | Display name shown on map |
| `color` | [R, G, B] | no | RGB tint for region nodes on the map overlay |

### Location

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `id` | string | yes | Unique identifier used in exits and effects |
| `name` | string | yes | Display name shown in UI |
| `region` | string | no | Region id (must match a declared region) |
| `description` | string | no | Shown in exploration panel and map hover tooltip |
| `background` | string | no | Default background image path (pack-relative) |
| `backgrounds` | dict | no | Time-of-day backgrounds; keys: `morning` `noon` `afternoon` `evening` `night` `midnight` |
| `map_x` / `map_y` | int | no | Position on the map overlay (pixel offset) |
| `exits` | list | no | Exits; each entry is a string (target id) or a full Exit dict |
| `npcs` | list | no | NPC presence entries |
| `scene_hooks` | list | no | Auto-trigger or examine scenes |
| `requires_flags` | list[str] | no | Player can only enter if all flags are truthy |
| `forbids_flags` | list[str] | no | Player cannot enter if any flag is truthy |
| `tags` | list[str] | no | Arbitrary labels for querying |

### Backgrounds (multi-time-of-day)

When `backgrounds` is provided, the engine picks the matching key for the
current time of day.  Any time slot that is not in `backgrounds` falls back
to the top-level `background`.  Both can co-exist; old packs with only
`background` continue to work without changes.

Valid time-of-day keys match `TimeOfDay` values:
`morning`, `noon`, `afternoon`, `evening`, `night`, `midnight`.

### Exit (full form)

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `target` | string | required | Destination location id |
| `label` | string | `"→ 地點名"` | Override button text |
| `description` | string | `null` | Hint shown beneath the exit button |
| `one_way` | bool | `false` | When `true`, no reverse exit is created automatically |
| `requires_flags` | list[str] | `[]` | Exit only usable when all flags are truthy |
| `forbids_flags` | list[str] | `[]` | Exit blocked when any flag is truthy |
| `requires_time` | list[str] | `[]` | Exit only usable during these time slots |
| `travel_cost` | int | `0` | Time-of-day phases consumed by this trip. Default 0 keeps the clock still for in-region moves; set 1+ for long trips |

Exits that are time-restricted but currently unavailable are shown in grey in the
exploration UI rather than hidden, so players understand why they cannot proceed.
Clicking a greyed-out exit now pops a toast explaining the reason instead of
silently doing nothing.

#### Why `travel_cost` matters

By default moves are free — wandering campus all morning shouldn't auto-fast-
forward to evening. Reserve `travel_cost: 1+` for trips that *should* feel like
they take time:

```yaml
- id: main_gate
  exits:
    - player_dorm                    # local, 0 cost
    - cafeteria                      # local, 0 cost
    - target: night_market
      label: "騎車去夜市"
      requires_time: [afternoon, evening, night]
      travel_cost: 2                 # crossing town takes a chunk of the day
```

### NPCPresence

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `npc_id` | string | required | NPC id matching `characters.yaml` |
| `times` | list[str] | `[]` (anytime) | Time slots when NPC is present |
| `weekdays` | list[str] | `[]` (any day) | `mon`-`sun` filter |
| `requires_flags` | list[str] | `[]` | NPC present only when all flags truthy |
| `forbids_flags` | list[str] | `[]` | NPC hidden when any flag truthy |

### SceneHook

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `scene_id` | string | required | Scene id matching a scene in `scenes/` |
| `trigger` | string | `"examine"` | `enter`, `examine`, `auto`, `night_only` |
| `requires_flags` | list[str] | `[]` | Available only when all flags truthy |
| `forbids_flags` | list[str] | `[]` | Hidden when any flag truthy |
| `requires_time` | list[str] | `[]` | Available only during listed time slots |
| `requires` | list[Condition] | `[]` | Full condition gates; same syntax as scene lines / choices (`affection_gte`, `visited`, plugin conditions, etc.) |
| `forbids` | list[Condition] | `[]` | Hide/block when any full condition is true |
| `once` | bool | `true` | Remove after first play |

Use `requires_flags` for simple flag checks; use `requires` when a hook needs
non-flag state. Example:

```yaml
scene_hooks:
  - scene_id: heroine_lunch
    trigger: examine
    requires_flags: [met_heroine]
    requires:
      - {kind: affection_gte, target: heroine, value: 20}
      - {kind: time_in, value: [noon]}
```

## Map overlay behaviour

The map overlay groups locations by region (colored tint per region).  
Nodes are rendered as:

- **amber** — current location  
- **pink/accent** — directly reachable this time slot  
- **region tint (muted)** — visited, not reachable right now  
- **grey + "?"** — not yet visited  
- **dark grey** — locked (requires_flags not met)

Hovering a visited node shows its `description` in a tooltip.

## Effects and conditions that reference locations

Use `move_to` effect to move the player programmatically:

```yaml
effects:
  - kind: move_to
    target: library
```

Use `location` condition to branch based on current location:

```yaml
requires:
  - kind: location
    target: library
```

Full lists: [effects-reference.md](effects-reference.md) and
[conditions-reference.md](conditions-reference.md).

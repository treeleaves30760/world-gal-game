# Destination Schedule Map

> Status: P0 correctness, P1 spatial presentation, and the target-pack
> playable slice are implemented; reviewed visual baselines, real-device
> touch/fullscreen matrices, and external UX/art approval remain pending  
> Scope: engine-generic travel planning and presentation

## 1. Product decision

The primary travel surface for schedule-driven visual novels should be a
spatial destination map, not a flat list of exits. A destination is presented
as a possible **story action**:

```text
current time and place
  -> see destination, character, and event marker
  -> inspect a truthful arrival preview
  -> confirm or cancel
  -> consume the advertised time exactly once
  -> arrive
  -> play the advertised event
```

This is an opt-in engine capability. Existing RPG-like packs retain direct
one-edge movement, zero-cost local travel, and the card picker unless their
metadata selects the schedule-map policy.

The transferable design idea is the decision grammar, not another game's
visual assets: the map makes “where, with whom, and at what time” one readable
choice. Packs remain responsible for their own map art, typography, markers,
story, and pacing.

## 2. Implemented P0 foundation and remaining gap

The engine now has:

- immutable, time-dependent `TravelPlan` routes with deterministic tie breaks;
- projected-arrival NPC and hook evaluation on a detached state;
- a persistent-state revision plus exact arrival promise, so a stale
  confirmation cannot silently switch events;
- atomic `TravelService` commit shared by GUI and headless;
- opt-in graph-scope card destinations, distant confirmation, and matching
  headless affordances;
- one shared guided-destination resolver for current, future, locked, and
  neutral offers, with primary story markers and a secondary location list;
- strict `travel.spatial_map` schema, asset/reference/attribution validation,
  campus/interior/story-only hierarchy, a fixed-aspect illustrated canvas,
  and directional off-campus cards;
- keyboard, gamepad, pointer, and touch access to cards, map markers,
  confirmation actions, and header/filter chrome;
- a four-line confirmation itinerary sourced from the immutable plan:
  current place/time, arrival place/time, human-readable route, and cost;
- tutorial-scene auto-open plus authored-map catalog rebinding for old saves.

The target pack now supplies original campus art, heroine markers, guide
progress, the promise round, and a licensed directional town thumbnail. The
remaining work is product evidence: reviewed baselines, real-device touch and
fullscreen coverage, final-resolution brand/map masters, and external UX/art
review.

### Earlier gaps this design addresses

The engine already has useful pieces:

- `DestinationPickerScene` shows direct destinations, NPCs, event availability,
  travel cost, lock reasons, and a confirm/cancel preview.
- `Exit.travel_cost` is applied consistently by GUI and headless movement.
- `SceneHook` supports enter/auto events with time and state requirements.
- `MapScene` and `MapView` already understand authored `map_x` / `map_y`
  coordinates.

Before this initiative, they did not form one reliable player flow:

1. Both travel UIs only understand one-hop exits. A distant campus destination
   cannot be selected as one itinerary.
2. The card preview computes NPCs, background, and available scenes at the
   **departure** time. Movement advances the clock before arrival hooks are
   evaluated, so the preview can promise a person or event that is absent on
   arrival.
3. The spatial view is a node graph, not an authored campus map. It has no
   heroine/event markers, region paging, route preview, or confirmation.
4. The spatial view can close after a distant or locked click even when
   movement fails, leaving the player with no useful explanation.
5. NPC presence and the event that will actually fire are not explicitly
   linked. If several enter/auto hooks qualify, YAML order silently decides.
6. GUI and headless movement duplicate transaction logic and do not return the
   same structured result.
7. The picker and map lack complete keyboard/gamepad focus navigation; critical
   information relies too heavily on mouse hover.

The first correctness gate is therefore not new map art. It is a single,
truthful travel plan shared by every caller.

## 3. Experience contract

### 3.1 Information visible before commitment

Every selectable story destination must expose:

- destination name and region;
- character name and portrait marker when a character event is guaranteed;
- a non-spoiler event teaser;
- departure time;
- predicted arrival time;
- total schedule cost in named time slots;
- route summary for a distant destination;
- whether the event is new, repeated, or a fallback conversation;
- a clear lock reason when unavailable.

The preview must use projected arrival state. “She will be there when you
arrive” is a runtime promise, not decorative copy.

### 3.2 Confirmation policy

- Selecting a marker opens a detail sheet; it never commits immediately.
- Distant, cross-region, multi-hop, or non-zero-cost actions require explicit
  confirmation.
- A pack may require confirmation for every story event.
- Cancel is side-effect free: no location, clock, flag, history, audio, or
  scene change.
- A failed revalidation keeps the detail sheet open and explains the current
  reason.

### 3.3 Time policy

The UI speaks in player-facing schedule slots, not implementation phases.
The transaction has exactly one advertised total cost.

For the first implementation, a pack may treat a map selection as an atomic
“journey plus visit” and source that cost from the route/policy. Scene prose
must not charge the same slot again. If a future pack needs separate walking
and activity durations, add an explicit `activity_cost` through a
pack-format-versioned schema change; never infer duration from arbitrary scene
effects.

### 3.4 Arrival policy

Schedule-map travel is atomic:

- the route proves that the destination is reachable and calculates cost;
- only the final destination becomes visited;
- intermediate nodes do not trigger scenes or become visited;
- the selected final enter/auto hook fires once;
- legacy direct/RPG movement remains available as a separate policy.

This prevents a single map choice from being intercepted by unrelated events
at invisible intermediate nodes.

## 4. Authoritative runtime model

### 4.1 `TravelPlan`

Introduce one immutable value object consumed by the UI, app, headless session,
affordances, and tests:

```text
TravelPlan
  destination_id
  path_location_ids
  path_edges
  available
  blocked_reason
  departure_time
  arrival_time
  total_cost
  arrival_npc_ids
  arrival_hook_id
  arrival_hook_title
  event_owner_npc_id
  event_preview_text
  is_new
  is_direct
  plan_revision
```

`plan_revision` identifies the state snapshot used for the preview. Commit must
revalidate rather than trust stale UI data.

### 4.2 Route planning

`MapSystem.plan_travel(destination, state, policy)` and
`MapSystem.travel_options(state, policy)` should:

1. search the directed graph with deterministic Dijkstra;
2. evaluate every edge at the projected time at which that edge is entered;
3. respect exit flags/time, target accessibility, one-way edges, and hidden
   location policy;
4. use `(location_id, phase modulo one game week)` as the search state so a
   time-dependent route stays finite and weekday projections remain correct;
5. break ties by total cost, hop count, then stable location-id path;
6. reject negative costs at validation time;
7. never insert an invisible wait merely to make a route valid.

If no route is valid now, the destination may remain visible with a player-
facing reason and the next authored action can be “wait until …”.

### 4.3 Projected arrival state

Route previews must evaluate against a read-only projected state:

- copy the time and advance it by the plan cost;
- project the final current location;
- preserve flags, variables, affection, inventory, played scenes, and chapter;
- calculate arrival weekday, NPC presence, backgrounds, and scene conditions
  from that projection.

The event shown in the preview must be the event commit will trigger.

### 4.4 Event selection

Add optional, backward-compatible map presentation fields to `SceneHook` in a
pack-format-versioned change:

```yaml
priority: 100
preview_npc_id: ruoxue
preview_text: "她說影印室又卡紙了。"
```

Eligible enter/auto hooks are ordered by descending `priority`, then authored
YAML order. `TravelPlan.arrival_hook_id` freezes the selected event; commit
revalidates that exact hook. A heroine marker may only be shown when a matching
event or an authored fallback conversation is guaranteed.

### 4.5 `TravelService`

Centralize mutation in `TravelService.commit(plan, state) -> TravelResult`:

```text
revalidate
  -> apply total cost once
  -> move to final destination once
  -> record destination once
  -> emit time/move lifecycle hooks once
  -> trigger the planned arrival hook once
  -> return structured success or failure
```

The app and headless session must use the same service. The map closes only
after a successful result.

### 4.6 Guided destination resolver

`GuidedDestinationResolver` is a presentation layer over `TravelPlan`, not a
second travel system:

- `current`: leaving now guarantees the exact advertised enter/auto scene;
- `future`: the event's non-time conditions already hold and a bounded
  projected departure can guarantee it;
- `locked`: the story marker remains inspectable, with an authored wait/action
  reason, but cannot commit;
- `neutral`: an ordinary reachable location shown only in the secondary list.

Examine-only hooks never become guaranteed arrival markers. Later events with
unmet non-time prerequisites stay hidden from the primary layer. Future offers
ask the player to advance one explicit phase and then re-resolve; they never
commit a plan quoted from a projected future state.

## 5. Presentation model

### 5.1 Spatial map

Packs may opt into:

```yaml
exploration_guidance:
  map:
    requires_flags: [orientation_done]
    locked_reason: "先依新生指南完成集合，之後才打開校園地圖。"

travel:
  scope: graph
  minimum_cost: 1
  hidden_tags: [story_only]
  spatial_map:
    background: assets/maps/campus.png
    background_fit: cover
    attribution: "Map data © OpenStreetMap contributors"
    bounds: [0, 0, 100, 100]
    edge_destinations:
      night_market:
        direction: southwest
        thumbnail: assets/maps/night-market.jpg
        caption: "城隍廟商圈"
        attribution: "Photo: Example Author, CC BY-SA 4.0"
  guided_story:
    direct_before_graph: true
    auto_reopen: true
    future_horizon_phases: 6
    max_primary: 3
    require_marker: true
    progress:
      label: "新生指南"
      stages:
        - label: "第一輪 · 三人的普通日常"
          flags: [met_a, met_b, met_c]
    markers:
      heroine_library_event:
        npc_ids: [heroine]
        teaser: "她在總圖等你。"
```

`minimum_cost` applies once to the complete confirmed selection. It gives a
schedule-driven pack the familiar “one destination choice advances one slot”
rhythm even when its campus graph uses zero-cost edges for legacy free
exploration. A route with a larger authored edge total keeps that larger cost.

`direct_before_graph: true` is a strict opt-in for a directed opening. While
`travel.scope: graph` is declared but its `requires_flags` are not all true,
guided-story resolution may quote only an existing one-hop exit from the
current location. It cannot route through an intermediate node, and the graph
`minimum_cost` floor is not applied; the direct exit's own cost, time window,
and flag conditions remain authoritative. Once the graph gate becomes active,
the same resolver restores distant graph planning and the authored
`minimum_cost`. Omitting the field, setting it to `false`, or providing a
non-boolean value preserves the prior behavior: guided story remains disabled
until the graph gate opens.

`spatial_map` is presentation-only. Locations not listed in
`edge_destinations` use their existing `map_x` / `map_y` on the central
illustrated canvas. Fixed `bounds` (default `0,0,100,100`) keep those anchors
stable while guided-story filtering changes which markers are visible.
Configured off-campus locations become directional thumbnail cards around the
canvas perimeter; `direction` accepts N/NE/E/SE/S/SW/W/NW and full English
names. Background, thumbnail, and attribution are all pack-authored. A
top-level attribution is drawn on the central canvas; an edge destination's
own attribution is drawn inside that thumbnail card so its credit remains
visible even when the pack does not use a global credit line.
`hidden_tags` remains authoritative: edge-card metadata never makes a
`story_only` or otherwise hidden location visible.

Optional `nodes` metadata assigns `campus`, `interior`, or `story_only`
presentation layers and short map labels. Interior nodes stay out of the
whole-campus view until the player stands at their parent building, the node
has a current guided offer, or it is the current location. Story-only nodes
never become neutral exploration merely because a graph route exists.
Visible corner cards reserve one side rail rather than compressing both axes;
authored-but-hidden cards reserve no empty rail. Switching the explicit
story/ordinary layer may therefore reflow the canvas once, while hover and
focus never change geometry. When real art is available, the canvas uses the
source aspect ratio so authored coordinates and the complete background share
one transform.

The spatial map is the primary in-game travel entry. In guided mode it starts
with story markers only. Before the full graph gate opens, only the current
location and authored direct offers remain visible; unrelated edge cards and
the ordinary-destination toggle stay hidden so a tutorial has one clear next
step. Once graph travel is enabled, the 44px ordinary-exploration toggle adds
both configured off-campus cards and ordinary destinations that the exact
shared planner says are currently reachable. They are never promoted to fake
story offers or shown beside the current story decision. Selecting any marker opens one existing
destination detail sheet; only its separate confirmation can commit travel.
When `exploration_guidance.map` is present, the exploration button, shortcut,
system-menu entry, application transition guards, headless affordance, and
explicit distant-travel op share that policy. The entry stays visible and
focusable while locked, but reports the pack-authored reason instead of
opening a map that the story has not taught yet. Authored story effects and
local tutorial exits remain separate from this UI policy. While the map is
locked, every currently available adjacent exit with `travel_cost: 0` is
rendered as a direct exploration action (for example, `前往校門口`), so a
pack can keep one authored next step without exposing the whole destination
surface.

At 640p, 720p, and 1080p the scene scales panel chrome, hit targets, labelled
markers, and edge cards from one density model, while measured font heights
reserve the header and map bands. Ordinary-exploration mode uses the free
header band for a passive five-item legend covering story, current, ordinary,
off-campus, and focus states; narrow layouts fall back to the hint row. The
legend stays outside the map rectangle and never participates in marker hit
testing. Ordinary destinations remain compact visual pins with 44px-or-larger
invisible hit targets; hover or keyboard/gamepad focus reveals one full name
and description in a fixed bounded strip. Story/current markers stay labelled
and paint above ordinary pins, while the focused marker paints last.
Illustrated backgrounds suppress legacy graph connection lines. Long labels,
edge-card names, and captions are ellipsized using their actual rendered font
weight.

A guided marker may reuse the lead NPC's authored portrait and `name_color`.
The portrait becomes a clipped avatar inside the existing labelled marker,
while the route colour supplies a redundant border/rail cue; missing art falls
back to the text marker without changing its destination, status, or hit
target. Reachable off-campus cards say they can be visited now. Cards whose
route or story conditions do not hold remain visible only when their layer
allows it, are disabled, and expose the shared blocked reason.

Future story markers put the exact target first
(`等待至第 N 天某時段（共 N 時段）`) rather than using an ambiguous
“later” label beside an immediate-looking confirmation hint. One activation
advances through the declared gap by calling the existing one-phase wait
authority repeatedly. A blocked phase or an ambient dialogue interrupts the
batch immediately, so the convenience action cannot skip a story boundary.
Selecting a marker only opens its available arrangement; the detail sheet
remains authoritative about whether the next action is wait, travel, or none.

`auto_reopen: true` is an opt-in continuation policy for a finite guided
sequence. It requires authored `guided_story.progress` and applies only while
that progress is incomplete. After a dialogue closure, explicit
`spatial_map.auto_open_after_scenes`, same-location enter/auto hooks, ambient
dialogue, and modal overlays retain precedence; only an otherwise idle closure
hands control back to `MapScene`. Headless reports the same handoff as
`next_surface: map`. The option never writes progress, unlocks travel, or
changes time cost.

`guided_story.progress` is optional presentation metadata. The map resolves
the first ordered stage whose listed flags are not all true, then renders its
authored label and completed count (`n / total`) in the header. It is read-only:
scene and hook conditions remain the only progression authority.

Missing optional images fall back to a static themed canvas/card. The engine
does not substitute a filename placeholder, move the player, or alter travel
availability. The existing card picker stays available as list mode and as the
accessible fallback.

### 5.2 Marker hierarchy

The default visual hierarchy is:

1. current story stage and completion count;
2. one to three recommended story/character markers;
3. current location;
4. neutral available destinations;
5. locked or future destinations, visually quieter but explainable.

A marker includes character portrait/name, destination, event state, and
arrival slot. Completion and lock state use icon plus text, never color alone.
Critical information is visible on click/tap/focus and is not hover-only.

### 5.3 Input and accessibility

- Direction keys/D-pad select the nearest marker in that direction.
- Enter/A opens the detail sheet; Escape/B cancels.
- Tab/controller Y enters map or picker chrome; left/right selects a control,
  down returns to destinations, and Enter/A activates it.
- PageUp/PageDown or controller shoulders scroll long confirmation reasons.
- The detail sheet has a visible focus ring and a safe default focus.
- Touch targets are at least 44 logical pixels.
- List mode exposes the same information and actions.
- Layout is verified at narrow touch, 720p, and 1080p sizes.
- The spatial layer adds no ambient pan, pulse, or hover tween. Its focus and
  state changes are immediate, including when reduced motion is enabled.

### 5.4 Semantic visual regression

The generic `visual-check` default set captures three separate map contracts at
both 1280×720 and 1920×1080:

1. `spatial_map_story_priority` pins the low-cognitive-load story layer;
2. `spatial_map_all_reachable` pins the expanded ordinary/reachable layer;
3. `travel_confirmation` selects the first immediately travelable primary
   story offer in authored location order. When every primary offer is for a
   later slot, it follows the first allowed advisory wait to that departure;
   only then does it fall back to the first visible reachable marker and the
   first reachable authored location.

Before opening any of these views, the driver completes the normal title intro.
If the pack declares `auto_open_after_scenes`, it then walks real available
exits, prioritises exact plans with arrival hooks, and completes the resulting
authored dialogue until that map-entry scene has actually been played. It never
starts the target tutorial directly or synthesises its prerequisite flags.

The confirmation scenario follows the real map callback into
`DestinationPickerScene`, requires a real immutable `TravelPlan`, and asserts
that opening the sheet did not change location or calendar state. Packs without
guided destinations truthfully render their normal all-reachable fallback in
the story-priority capture. Candidates are evidence for human review; generating
them does not make them approved baselines.

## 6. Delivery sequence

### P0 — correctness and parity (implemented)

- Add `TravelPlan`, deterministic route planning, projected-arrival evaluation,
  and stale-plan validation.
- Add `TravelService` and route GUI/headless through it.
- Fix spatial-map failed-click feedback.
- Validate non-negative costs and deterministic event priority.

### P1 — spatial schedule map (implemented capability)

- Add pack-authored background, fixed anchors, and directional edge cards.
- Add heroine/event portrait markers and progress presentation.
- Add route detail/confirmation with arrival-time copy.
- Add keyboard, gamepad, touch, list-mode, and reduced-motion behavior.

### P2 — target-pack playable slice (implemented)

- Move the target pack's common-route events from a dorm text planner to
  destination arrival hooks.
- Assign schedule costs and authored previews.
- Retire duplicate time charges and route-eligibility math.
- Add organic new-game coverage through all three route choices.

## 7. Acceptance tests

1. A graph `A -> B -> C` exposes `C` as a travel option while legacy
   `can_move_to(C)` remains false.
2. One-way, flag, time, inaccessible, multi-path, deterministic tie-break, and
   negative-cost cases are covered.
3. Paths crossing phases/days predict the exact arrival time, weekday, NPCs,
   background, and hook.
4. Departure-present/arrival-absent characters are not advertised.
5. Atomic distant travel does not visit or trigger intermediate nodes.
6. Confirm advances the exact advertised cost once and triggers the exact
   advertised final hook once.
7. Cancel and stale-plan failure change no state.
8. GUI and headless produce the same `TravelResult` and state diff.
9. Locked markers remain visible with reasons; failed clicks do not close the
   map.
10. Keyboard/gamepad/touch can reach every marker and confirmation control.
11. Campus, town, and story-only coordinates do not distort one another.
12. A visible heroine marker always leads to a heroine scene or authored
    fallback conversation.

## 8. Non-goals

- No game-specific heroine rules in engine core.
- No copy of another title's map art, layout, sounds, or trade dress.
- No invisible auto-wait or automatic route optimization that makes a story
  choice for the player.
- No deletion of the card/list picker.
- No claim of completion until GUI, headless, visual, and organic pack-flow
  tests all pass.

"""Choice legibility + de-triplication (round-3 VN review).

Covers four engine changes, all additive and back-compat:

- Fix 1: a locked choice surfaces a concise human-readable *reason* (the unmet
  condition in plain Chinese) instead of a silent ghost button.
- Fix 2: an affection-affecting choice enqueues lightweight per-character
  feedback ("好感度 +N") as data, gated for display by a setting.
- Fix 3: a route/flag-aware ``play_scene_branch`` effect lets one scene branch
  its next-scene by state (so packs can collapse triplicated transition scenes).
- Fix 4: the relationship screen marks the decisive route-lock-in gate and
  collapses 0-affection non-heroines (tested via the GameDriver path).
"""
import os

import pytest

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

from world_gal_game.core.game_state import GameState
from world_gal_game.core.story_graph import Scene, Line, Choice, Effect, Condition
from world_gal_game.dialogue.dialogue_engine import DialogueEngine
from world_gal_game.dialogue.condition_text import (
    describe_condition, summarize_lock,
)


# ---------------------------------------------------------------------------
# Fix 1 — locked-choice reason text
# ---------------------------------------------------------------------------

def test_condition_text_affection_gte_is_readable():
    """affection_gte renders as a readable gate, resolving the character name
    from the NPC registry when present."""
    cond = Condition(kind="affection_gte", target="qingyi", value=40)
    # Without a registry: falls back to the raw id, but still readable.
    txt = describe_condition(cond, None)
    assert "好感度" in txt and "40" in txt and "≥" in txt

    # With a registry bridging id -> display name.
    class _NPC:
        name = "林青衣"

    class _Reg:
        def get(self, cid):
            return _NPC() if cid == "qingyi" else None

    state = GameState()
    state.meta["__npc_registry__"] = _Reg()
    txt2 = describe_condition(cond, state)
    assert "林青衣" in txt2
    assert "好感度 ≥ 40" in txt2


def test_condition_text_covers_many_kinds_without_raising():
    """Every builtin condition kind yields a non-empty readable string, and an
    unknown/plugin kind degrades to a readable generic form (never raises)."""
    samples = [
        Condition(kind="flag", target="met_her"),
        Condition(kind="not_flag", target="angered_her"),
        Condition(kind="flag_eq", target="mood", value="happy"),
        Condition(kind="has_item", target="charm", value=2),
        Condition(kind="resource_gte", target="money", value=100),
        Condition(kind="time_in", value=["night", "midnight"]),
        Condition(kind="visited", target="library"),
        Condition(kind="scene_played", target="prologue"),
        Condition(kind="in_chapter", value="c2"),
        Condition(kind="chapter_at_or_after", target="c2"),
        Condition(kind="quest_active", target="find_book"),
        Condition(kind="totally_made_up_kind", target="x", value=3),
    ]
    for c in samples:
        s = describe_condition(c)
        assert isinstance(s, str) and s.strip()


def test_summarize_lock_joins_and_truncates():
    reqs = [
        Condition(kind="affection_gte", target="a", value=40),
        Condition(kind="has_item", target="key"),
        Condition(kind="flag", target="third"),
    ]
    one = summarize_lock([reqs[0]], [], None)
    assert "好感度 ≥ 40" in one
    # more than max_reasons -> ellipsis appended
    many = summarize_lock(reqs, [], None, max_reasons=2)
    assert "…" in many
    # nothing failing -> empty
    assert summarize_lock([], [], None) == ""


def test_locked_choice_yields_reason_on_presentation():
    """A choice that fails affection_gte presents enabled=False WITH a concise
    reason (Fix 1's core), while the available sibling has an empty reason."""
    s1 = Scene(id="s1", lines=[Line(text="a")], choices=[
        Choice(id="open", text="陪她",
               requires=[Condition(kind="affection_gte",
                                   target="qingyi", value=40)]),
        Choice(id="leave", text="先走"),
    ])
    state = GameState()
    state.story.add_scene(s1)
    state.affection.register("qingyi")        # affection 0 < 40 -> locked
    eng = DialogueEngine(state)
    eng.start_scene("s1")
    pres = eng.next_line()                     # end of lines -> choice phase
    assert pres.kind == "choice"
    locked = next(c for c in pres.choices if c.id == "open")
    avail = next(c for c in pres.choices if c.id == "leave")
    assert locked.enabled is False
    assert "好感度" in locked.reason and "40" in locked.reason
    assert avail.enabled is True
    assert avail.reason == ""


def test_hidden_if_locked_choice_is_not_shown():
    """A locked choice flagged hidden_if_locked is omitted entirely (no reason
    leaks for it) — only visible-but-locked choices get reasons."""
    s1 = Scene(id="s1", lines=[Line(text="a")], choices=[
        Choice(id="secret", text="祕密", hidden_if_locked=True,
               requires=[Condition(kind="flag", target="never")]),
        Choice(id="always", text="一般"),
    ])
    state = GameState()
    state.story.add_scene(s1)
    eng = DialogueEngine(state)
    eng.start_scene("s1")
    pres = eng.next_line()
    ids = [c.id for c in pres.choices]
    assert "secret" not in ids
    assert ids == ["always"]


def test_choice_menu_widget_accepts_reason_tuple():
    """The ChoiceMenu widget accepts a 4-tuple (with reason) and a plain
    3-tuple, and draws both without raising (headless dummy surface)."""
    import pygame
    from world_gal_game.ui.fonts import FontRegistry
    from world_gal_game.ui.theme import Theme
    from world_gal_game.ui.widgets.choice_menu import ChoiceMenu

    pygame.init()
    try:
        surf = pygame.Surface((1280, 720), pygame.SRCALPHA)
        fonts = FontRegistry(candidates=())
        menu = ChoiceMenu(pygame.Rect(0, 0, 1280, 720), fonts=fonts,
                          theme=Theme(), on_choose=lambda cid: None)
        # 4-tuple (locked, with reason) + 3-tuple (enabled) mixed.
        menu.set_choices([
            ("a", "陪她", False, "需要 與林青衣的好感度 ≥ 40"),
            ("b", "先走", True),
        ])
        assert len(menu.buttons) == 2
        menu.draw(surf)   # must not raise
    finally:
        pygame.quit()


# ---------------------------------------------------------------------------
# Fix 2 — per-choice affection feedback enqueued as data
# ---------------------------------------------------------------------------

def test_affection_effect_enqueues_feedback_toast():
    """An affection effect queues an ('affection', name, delta) toast; the data
    is present regardless of any display setting (the App gates rendering, not
    the queueing). It is surfaced via the toast queue side-channel and is NOT
    appended to apply_all's per-effect result list (back-compat: a single-effect
    batch still returns exactly one result)."""
    state = GameState()
    state.affection.register("qingyi")
    out = state.apply_all([Effect(kind="affection", target="qingyi", value=5)])

    queue = state.meta.get("__pending_toasts__") or []
    aff_toasts = [t for t in queue if t[0] == "affection"]
    assert aff_toasts, "expected an affection feedback toast in the queue"
    kind, name, delta = aff_toasts[0]
    assert name == "qingyi" and delta == 5

    # apply_all's result list shape is unchanged (one row for the one effect).
    assert len(out) == 1 and out[0]["new"] == 5


def test_affection_feedback_uses_display_name_when_available():
    class _NPC:
        name = "林青衣"

    class _Reg:
        def get(self, cid):
            return _NPC() if cid == "qingyi" else None

    state = GameState()
    state.meta["__npc_registry__"] = _Reg()
    state.affection.register("qingyi")
    state.apply_all([Effect(kind="affection", target="qingyi", value=3)])
    queue = state.meta.get("__pending_toasts__") or []
    aff = next(t for t in queue if t[0] == "affection")
    assert aff[1] == "林青衣"


def test_no_affection_change_enqueues_no_feedback():
    """A batch that doesn't move affection enqueues no affection toast."""
    state = GameState()
    state.affection.register("qingyi")
    state.apply_all([Effect(kind="set_flag", target="x")])
    queue = state.meta.get("__pending_toasts__") or []
    assert not [t for t in queue if t[0] == "affection"]


# ---------------------------------------------------------------------------
# Fix 3 — conditional/route-aware scene transition
# ---------------------------------------------------------------------------

def _branch_scenes():
    opener = Scene(id="open", lines=[Line(text="hi")], on_end=[
        Effect(kind="play_scene_branch", target="def", value=[
            {"when": {"kind": "flag", "target": "route_a"}, "target": "A"},
            {"when": {"kind": "flag", "target": "route_b"}, "target": "B"},
        ]),
    ])
    return [opener, Scene(id="A", lines=[Line(text="A")]),
            Scene(id="B", lines=[Line(text="B")]),
            Scene(id="def", lines=[Line(text="default")])]


@pytest.mark.parametrize("flags,expected", [
    (["route_a"], "A"),
    (["route_b"], "B"),
    ([], "def"),          # no case matches -> default target
])
def test_play_scene_branch_routes_by_state(flags, expected):
    state = GameState()
    for sc in _branch_scenes():
        state.story.add_scene(sc)
    for f in flags:
        state.events.set_flag(f, True)
    eng = DialogueEngine(state)
    eng.start_scene("open")
    pres = eng.next_line()           # end of opener -> transition
    assert pres.kind == "transition"
    assert pres.next_scene == expected


def test_play_scene_branch_first_match_wins():
    """When multiple cases hold, the first listed wins (ordered evaluation)."""
    state = GameState()
    for sc in _branch_scenes():
        state.story.add_scene(sc)
    state.events.set_flag("route_a", True)
    state.events.set_flag("route_b", True)
    eng = DialogueEngine(state)
    eng.start_scene("open")
    assert eng.next_line().next_scene == "A"


def test_play_scene_branch_no_match_no_default_degrades_to_end():
    """No case matches and no default -> the scene simply ends (no crash)."""
    opener = Scene(id="o", lines=[Line(text="hi")], on_end=[
        Effect(kind="play_scene_branch", value=[
            {"when": {"kind": "flag", "target": "never"}, "target": "A"},
        ]),
    ])
    state = GameState()
    state.story.add_scene(opener)
    state.story.add_scene(Scene(id="A", lines=[Line(text="A")]))
    eng = DialogueEngine(state)
    eng.start_scene("o")
    pres = eng.next_line()
    assert pres.kind == "end"
    assert pres.next_scene is None


def test_play_scene_branch_malformed_case_is_skipped():
    """A malformed case (missing target / bad condition) is skipped, not fatal;
    a later valid case still resolves."""
    opener = Scene(id="o", lines=[Line(text="hi")], on_end=[
        Effect(kind="play_scene_branch", value=[
            {"when": {"kind": "flag", "target": "x"}},   # no target -> skip
            {"target": "A"},                              # no when -> skip
            {"when": {"kind": "flag", "target": "go"}, "target": "B"},
        ]),
    ])
    state = GameState()
    for sid in ("A", "B"):
        state.story.add_scene(Scene(id=sid, lines=[Line(text=sid)]))
    state.story.add_scene(opener)
    state.events.set_flag("go", True)
    eng = DialogueEngine(state)
    eng.start_scene("o")
    assert eng.next_line().next_scene == "B"


def test_play_scene_branch_registered_with_args_schema():
    """The effect is in the registry with its typed arg model, so it exports to
    the capability manifest / references like every other builtin."""
    from world_gal_game.plugins.registry import EFFECT_REGISTRY
    entry = EFFECT_REGISTRY.get("play_scene_branch")
    assert entry is not None
    assert entry.plugin_id == "builtin"
    assert entry.args_model is not None
    # JSON-Schema export must succeed (what gen_references / capabilities use).
    schema = entry.args_model.model_json_schema()
    assert "value" in schema.get("properties", {})


# ---------------------------------------------------------------------------
# Fix 4 — relationship screen gate marking + de-clutter (via GameDriver)
# ---------------------------------------------------------------------------

@pytest.fixture
def driver():
    from world_gal_game.dev.driver import GameDriver
    d = GameDriver(pack="demo_pack")
    d.app.manager.transitions_enabled = False
    yield d
    d.quit()


def test_relationship_route_gate_from_threshold(driver):
    """A heroine whose named threshold unlocks a route-shaped key surfaces that
    threshold's value as the decisive route gate in describe()."""
    from world_gal_game.core.affection import AffectionThreshold
    state = driver.app.ctx.state
    npc = driver.app.ctx.npcs.all()[0]
    npc.is_heroine = True
    npc.route_id = "heroine_x"
    state.affection.register(npc.id)
    ca = state.affection.characters[npc.id]
    ca.thresholds = [
        AffectionThreshold(name="朋友", value=25, unlocks=["heroine_x_friend"]),
        AffectionThreshold(name="戀人", value=80,
                           unlocks=["heroine_x_route_lover"]),
    ]
    ca.set_value(30)
    driver.app._open_relationships()
    driver.advance_frames(2)
    row = next(r for r in driver.app.manager.current.describe()["characters"]
               if r["character_id"] == npc.id)
    # the route-shaped unlock (value 80) is the gate, not the plain 朋友 tier
    assert row["route_gate"] == 80
    assert row["route_unlocked"] is False


def test_relationship_route_gate_from_route_choice_scene(driver):
    """The most authoritative gate: a choice that sets route_<id> AND gates on
    affection_gte is read straight from the story graph (the 40 case)."""
    from world_gal_game.core.story_graph import Scene, Line, Choice, Effect, Condition
    state = driver.app.ctx.state
    npc = driver.app.ctx.npcs.all()[0]
    npc.is_heroine = True
    npc.route_id = "heroine_x"
    state.affection.register(npc.id)
    state.affection.characters[npc.id].set_value(10)
    # Inject a route_choice-style scene that locks in route_heroine_x at >=40.
    state.story.add_scene(Scene(id="route_choice", lines=[Line(text="?")],
        choices=[Choice(id="pick", text="多陪她",
            requires=[Condition(kind="affection_gte", target=npc.id, value=40)],
            effects=[Effect(kind="set_flag", target="route_heroine_x",
                            value=True)])]))
    driver.app._open_relationships()
    driver.advance_frames(2)
    row = next(r for r in driver.app.manager.current.describe()["characters"]
               if r["character_id"] == npc.id)
    assert row["route_gate"] == 40


def test_relationship_collapses_zero_affection_non_heroines(driver):
    """0-affection non-heroines are collapsed (not drawn); heroines and engaged
    non-heroines stay visible."""
    state = driver.app.ctx.state
    npcs = driver.app.ctx.npcs.all()
    hero, ghost = npcs[0], npcs[1]
    hero.is_heroine = True
    ghost.is_heroine = False
    state.affection.characters.clear()
    state.affection.register(hero.id)
    state.affection.characters[hero.id].set_value(20)
    state.affection.register(ghost.id)           # stays at 0 -> collapsed
    driver.app._open_relationships()
    driver.advance_frames(2)
    desc = driver.app.manager.current.describe()
    assert desc["collapsed"] >= 1
    hero_row = next(r for r in desc["characters"]
                    if r["character_id"] == hero.id)
    ghost_row = next(r for r in desc["characters"]
                     if r["character_id"] == ghost.id)
    assert hero_row["visible"] is True
    assert ghost_row["visible"] is False


def test_relationship_screen_draws_with_gate(driver):
    """Drawing a heroine card that has a discoverable gate must not raise."""
    from world_gal_game.core.affection import AffectionThreshold
    state = driver.app.ctx.state
    npc = driver.app.ctx.npcs.all()[0]
    npc.is_heroine = True
    npc.route_id = "heroine_x"
    state.affection.register(npc.id)
    ca = state.affection.characters[npc.id]
    ca.thresholds = [AffectionThreshold(name="戀人", value=80,
                                        unlocks=["heroine_x_route_lover"])]
    ca.set_value(50)
    driver.app._open_relationships()
    driver.advance_frames(3)        # draws the panel each frame; must not raise
    assert type(driver.app.manager.current).__name__ == "RelationshipsScene"

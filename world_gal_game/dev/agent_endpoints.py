"""Aggregate, agent-facing endpoints: ``context``, ``impact``, onboarding bundle.

The engine already exposes every reasoning primitive an agent needs as a
*separate* tool — :class:`~world_gal_game.dev.pack_inspector.PackInspector`
(structure / reachability / dead-ends), :class:`~world_gal_game.dev.dataflow.DataflowAnalyzer`
(writers / readers / conditioned edges), :class:`~world_gal_game.dev.coverage.CoverageTracker`
(run coverage), :class:`~world_gal_game.dev.planner.Planner` (goal search), and
the capability / variable manifests. Each is one CLI call.

That granularity is precise but token-expensive: an agent priming itself on a
pack before an edit pays a process-spawn + pack-load tax per tool and then has
to stitch five JSON blobs together. This module collapses the common
"orient me on this pack" and "what does changing X break?" questions into two
aggregate endpoints that load the pack once and return a single JSON object:

- :func:`build_context` — variables + reachability + scene graph + dataflow
  digest + coverage totals (and real coverage if a script is supplied), plus
  the structural gaps (unreachable scenes / endings, dead-ends) an agent should
  know about before touching anything.
- :func:`analyze_impact` — given a symbol id (flag / scene / item / resource),
  the writers/readers that touch it, the endings and scenes *gated* on it
  (the things that can become unreachable if its writers change), the
  conditioned edges that reference it, and — when ``probe_reachability`` is on
  — a planner confirmation of which at-risk endings are reachable today, so the
  agent has a before-baseline to compare an edit against.

Both are pure read paths; neither mutates the pack.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any


# ----------------------------------------------------------------------
# wgg context


def build_context(pack_root: Path, *, script: list[dict] | None = None,
                  seed: int | None = None, full_dataflow: bool = False) -> dict:
    """Aggregate the static + (optional) dynamic view of a pack into one dict.

    Loads the pack's structural view (``PackInspector``) and typed dataflow
    view (``DataflowAnalyzer``) once and returns a single JSON-friendly dict:

    - ``pack`` — the inspector summary (title / counts / start).
    - ``variables`` — declared narrative-state variables.
    - ``reachability`` — reachable / unreachable scenes + ending reachability.
    - ``scene_graph`` — adjacency: each scene's outgoing ``(origin, target)``.
    - ``dataflow`` — a digest (per-symbol writer/reader counts, edge count,
      undeclared / unused flags); the full ``DataflowReport`` when
      ``full_dataflow`` is set.
    - ``coverage`` — pack totals; real per-dimension coverage when ``script``
      is supplied.
    - ``gaps`` — the things an agent should fix or avoid: unreachable scenes,
      unreachable endings, dead-ends, and used-but-undeclared flags.
    """
    from .pack_inspector import PackInspector
    from .dataflow import DataflowAnalyzer

    inspector = PackInspector(pack_root)
    variables = inspector.variables()
    reachability = inspector.reachability()
    dead_ends = [
        {"kind": d.kind, "target": d.target, "file": d.file, "detail": d.detail}
        for d in inspector.dead_ends()
    ]
    scene_graph = {
        s["id"]: [
            {"origin": o["origin"], "target": o["target"]} for o in s["outgoing"]
        ]
        for s in inspector.scenes()
    }

    analyzer = DataflowAnalyzer(pack_root)
    declared = {v["key"] for v in variables}
    report = analyzer.analyze(declared_flags=declared or None)

    if full_dataflow:
        dataflow: dict[str, Any] = report.model_dump()
    else:
        dataflow = {
            "flags": _usage_digest(report.flags),
            "scenes": _usage_digest(report.scenes),
            "items": _usage_digest(report.items),
            "resources": _usage_digest(report.resources),
            "edges": len(report.edges),
            "edges_guarded": sum(1 for e in report.edges if e.guard),
            "undeclared_flags": report.undeclared_flags,
            "unused_declared_flags": report.unused_declared_flags,
        }

    coverage = _coverage_section(pack_root, script=script, seed=seed)

    gaps = {
        "unreachable_scenes": reachability["unreachable"],
        "unreachable_endings": reachability["endings"]["unreachable"],
        "dead_ends": dead_ends,
        "undeclared_flags": report.undeclared_flags,
        "unused_declared_flags": report.unused_declared_flags,
    }

    return {
        "pack": inspector.summary(),
        "variables": variables,
        "reachability": reachability,
        "scene_graph": scene_graph,
        "dataflow": dataflow,
        "coverage": coverage,
        "gaps": gaps,
    }


def _usage_digest(table: dict) -> dict[str, dict]:
    """Per-symbol writer/reader counts (the cheap view of a SymbolUsage table)."""
    return {
        sid: {"writers": len(u.writers), "readers": len(u.readers)}
        for sid, u in table.items()
    }


def _coverage_section(pack_root: Path, *, script: list[dict] | None,
                      seed: int | None) -> dict:
    """Coverage totals; real per-dimension coverage when ``script`` is given."""
    from ..config import EngineConfig
    from .coverage import CoverageTracker
    from ..headless import HeadlessSession

    tracker = CoverageTracker(pack_root)
    totals = {
        "scenes": tracker.total_scenes,
        "lines": tracker.total_lines,
        "choices": tracker.total_choices,
        "endings": tracker.total_endings,
    }
    if not script:
        return {"totals": totals, "report": None}

    sess = HeadlessSession.open(EngineConfig(seed=seed), pack=str(pack_root))
    sess.run_script(script)
    return {"totals": totals, "report": tracker.report(sess).model_dump()}


# ----------------------------------------------------------------------
# wgg impact


def analyze_impact(pack_root: Path, symbol_id: str, *,
                   symbol_type: str | None = None,
                   probe_reachability: bool = True,
                   seed: int | None = 42) -> dict:
    """Pre-flight a change to ``symbol_id``: what reads it, what it can break.

    Returns a JSON-friendly dict:

    - ``symbol`` / ``symbol_type`` — the queried id and (best-guess) type.
    - ``writers`` / ``readers`` — the authoring sites that write / read it,
      across all symbol types (a flag and a scene can share an id).
    - ``at_risk_endings`` — endings whose ``requires`` / ``forbids`` gate on
      this symbol: if you remove or invert its writers, these may become
      unreachable.
    - ``at_risk_scenes`` — scenes gated on this symbol (``scene_requires`` /
      choice / line guards reference it).
    - ``edges_referencing`` — conditioned scene->scene edges whose guard names
      this symbol.
    - ``reachable_today`` — when ``probe_reachability``, a planner check of
      each at-risk ending's *current* reachability, so the agent has a
      before-baseline. ``null`` when probing is off.

    The analysis is read-only and conservative: it reports what *structurally
    depends* on the symbol. It does not simulate the edit — pair it with
    ``wgg plan`` after editing to confirm a goal still has a path.
    """
    from .dataflow import DataflowAnalyzer

    analyzer = DataflowAnalyzer(pack_root)
    report = analyzer.analyze()

    tables = {
        "flags": report.flags,
        "scenes": report.scenes,
        "items": report.items,
        "resources": report.resources,
    }

    # Resolve type if not given: first table that knows the symbol.
    resolved_type = symbol_type
    if resolved_type is None:
        for name, table in tables.items():
            if symbol_id in table:
                resolved_type = name
                break

    writers: list[dict] = []
    readers: list[dict] = []
    for name, table in tables.items():
        if symbol_type is not None and name != symbol_type:
            continue
        usage = table.get(symbol_id)
        if usage is None:
            continue
        writers += [{**r.model_dump(), "symbol_type": name} for r in usage.writers]
        readers += [{**r.model_dump(), "symbol_type": name} for r in usage.readers]

    # Sites are prefixed by holder kind; mine the reader sites for the
    # endings / scenes that gate on this symbol.
    at_risk_endings = sorted({
        r["site"].split(":", 1)[1]
        for r in readers if r["site"].startswith("ending:")
    })
    at_risk_scenes = sorted({
        _scene_of_site(r["site"]) for r in readers
        if _scene_of_site(r["site"]) is not None
    })

    def _edge_refs(edge) -> bool:
        if any(g.get("target") == symbol_id for g in edge.guard):
            return True
        logic = edge.guard_logic or {}
        return any(c.get("target") == symbol_id
                   for c in logic.get("all", []) + logic.get("none", []))

    edges_referencing = [e.model_dump() for e in report.edges if _edge_refs(e)]

    reachable_today: dict[str, Any] | None = None
    if probe_reachability and at_risk_endings:
        reachable_today = _probe_endings(pack_root, at_risk_endings, seed=seed)

    return {
        "symbol": symbol_id,
        "symbol_type": resolved_type,
        "writers": writers,
        "readers": readers,
        "at_risk_endings": at_risk_endings,
        "at_risk_scenes": at_risk_scenes,
        "edges_referencing": edges_referencing,
        "reachable_today": reachable_today,
    }


def pack_brief(pack_root: Path, *, as_text: bool = False):
    """A minimal, token-frugal orientation digest — the cheap read before edit.

    ``build_context`` is the *complete* aggregate (it can dump every writer /
    reader / full dataflow); this is the deliberately *small* one. It keeps only
    ids and counts — a compact ``scene -> [targets]`` adjacency (origin/via
    dropped), ending reachability, ``key:type`` variable one-liners, and a gap
    rollup — so an agent can fit a whole pack's shape in a fraction of the
    tokens. With ``as_text`` it renders an even terser plain-text outline (the
    lowest-token form an LLM can skim). Per-symbol detail lives in
    :func:`symbol_card`; deep change pre-flight in :func:`analyze_impact`.
    """
    from .dataflow import DataflowAnalyzer
    from .pack_inspector import PackInspector

    inspector = PackInspector(pack_root)
    summary = inspector.summary()
    reach = inspector.reachability()
    variables = inspector.variables()
    scenes = inspector.scenes()
    dead = inspector.dead_ends()
    chapters = inspector.chapters()
    declared = {row["key"] for row in variables}
    report = DataflowAnalyzer(pack_root).analyze(declared_flags=declared or None)

    adjacency = {
        s["id"]: sorted({o["target"] for o in s["outgoing"]})
        for s in scenes
    }
    brief = {
        "title": summary.get("title"),
        "counts": summary.get("counts", {}),
        "start": {"location": summary.get("start_location"),
                  "scene": summary.get("intro_scene")},
        "scenes": adjacency,
        "endings": reach["endings"],
        "variables": [f'{row["key"]}:{row["type"]}' for row in variables],
        "gaps": {
            "unreachable_scenes": reach["unreachable"],
            "unreachable_endings": reach["endings"]["unreachable"],
            "dead_ends": sorted(f"{d.kind}:{d.target}" for d in dead),
            "undeclared_flags": report.undeclared_flags,
            "unused_declared_flags": report.unused_declared_flags,
        },
    }
    # Only carry chapter structure when the pack declares it — keeps the brief
    # minimal for packs (like a tiny demo) that have no chapters.yaml.
    if chapters:
        brief["chapters"] = [
            {"id": c["id"], "route": c["route"], "scenes": c["scenes"]}
            for c in chapters
        ]
    return _brief_text(brief) if as_text else brief


def _brief_text(brief: dict) -> str:
    """Render a :func:`pack_brief` dict as a terse plain-text outline."""
    lines: list[str] = []
    counts = " ".join(f"{k}={v}" for k, v in brief["counts"].items())
    lines.append(f"# {brief['title']}  ({counts})")
    lines.append(f"start: scene={brief['start']['scene']} "
                 f"loc={brief['start']['location']}")
    endings = brief["endings"]
    reachable = set(endings["reachable"])
    marked = " ".join(f"{e}{'+' if e in reachable else '-'}"
                      for e in endings["all"])
    lines.append(f"endings: {marked or '(none)'}")
    if brief["variables"]:
        lines.append("variables: " + ", ".join(brief["variables"]))
    if brief.get("chapters"):
        lines.append("chapters:")
        for c in brief["chapters"]:
            route = f" [{c['route']}]" if c["route"] else ""
            lines.append(f"  {c['id']}{route}: {' '.join(c['scenes'])}")
    lines.append("scenes:")
    for sid, dsts in brief["scenes"].items():
        arrow = " -> " + " | ".join(dsts) if dsts else " (terminal)"
        lines.append(f"  {sid}{arrow}")
    gaps = brief["gaps"]
    nonempty = {k: v for k, v in gaps.items() if v}
    if nonempty:
        lines.append("gaps:")
        for key, items in nonempty.items():
            lines.append(f"  {key}: {', '.join(map(str, items))}")
    else:
        lines.append("gaps: none")
    return "\n".join(lines)


def symbol_card(pack_root: Path, symbol_id: str) -> dict:
    """A focused, compact view of ONE symbol — cheaper than the full dataflow.

    Classifies ``symbol_id`` (scene / flag / item / resource / npc / location)
    and returns just what an agent needs to reason about *it*: for a scene, its
    incoming/outgoing edges with structured ``guard`` logic and the flags it
    reads/writes; for a flag, whether it's declared (+ its spec), its writer /
    reader sites, and which endings/scenes gate on it. On a miss, returns
    ``{"type": "unknown", "did_you_mean": [...]}`` so a typo'd id self-corrects.
    """
    from .dataflow import DataflowAnalyzer
    from .pack_inspector import PackInspector

    inspector = PackInspector(pack_root)
    variables = {row["key"]: row for row in inspector.variables()}
    report = DataflowAnalyzer(pack_root).analyze(
        declared_flags=set(variables) or None)
    reach = inspector.reachability()
    scenes_by_id = {s["id"]: s for s in inspector.scenes()}

    # --- scene ---------------------------------------------------------
    if symbol_id in scenes_by_id:
        s = scenes_by_id[symbol_id]
        outgoing = [{"dst": e.dst, "via": e.via, "guard": e.guard_logic}
                    for e in report.edges if e.src == symbol_id]
        incoming = [{"src": e.src, "via": e.via, "guard": e.guard_logic}
                    for e in report.edges if e.dst == symbol_id]
        writes = sorted({fid for fid, u in report.flags.items()
                         if any(_scene_of_site(w.site) == symbol_id
                                for w in u.writers)})
        return {
            "symbol": symbol_id, "type": "scene", "title": s["title"],
            "file": s["file"], "is_ending": s["is_ending"],
            "reachable": symbol_id in set(reach["reachable"]),
            "incoming": incoming, "outgoing": outgoing, "writes_flags": writes,
        }

    # --- flag ----------------------------------------------------------
    if symbol_id in report.flags:
        usage = report.flags[symbol_id]
        reader_sites = [r.site for r in usage.readers]
        return {
            "symbol": symbol_id, "type": "flag",
            "declared": symbol_id in variables,
            "spec": variables.get(symbol_id),
            "writers": sorted(w.site for w in usage.writers),
            "readers": sorted(reader_sites),
            "gates_endings": sorted({s.split(":", 1)[1] for s in reader_sites
                                     if s.startswith("ending:")}),
            "gates_scenes": sorted({_scene_of_site(s) for s in reader_sites
                                    if _scene_of_site(s) is not None}),
        }

    # --- item / resource (generic dataflow tables) ---------------------
    for kind, table in (("item", report.items), ("resource", report.resources)):
        if symbol_id in table:
            usage = table[symbol_id]
            return {
                "symbol": symbol_id, "type": kind,
                "writers": sorted(w.site for w in usage.writers),
                "readers": sorted(r.site for r in usage.readers),
            }

    # --- npc / location (structural) -----------------------------------
    for row in inspector.npcs():
        if row.get("id") == symbol_id:
            return {"symbol": symbol_id, "type": "npc", **row}
    for row in inspector.locations():
        if row.get("id") == symbol_id:
            return {"symbol": symbol_id, "type": "location", **row}

    # --- miss: suggest near matches ------------------------------------
    import difflib
    known = (list(scenes_by_id) + list(report.flags) + list(report.items)
             + list(report.resources) + [r.get("id") for r in inspector.npcs()]
             + [r.get("id") for r in inspector.locations()])
    known = sorted({k for k in known if k})
    return {"symbol": symbol_id, "type": "unknown",
            "did_you_mean": difflib.get_close_matches(symbol_id, known, n=5, cutoff=0.5)}


def _scene_of_site(site: str) -> str | None:
    """Extract the scene id a reader site belongs to, or ``None``.

    Sites look like ``scene_requires:<sid>``, ``scene:<sid>#lineN``,
    ``choice:<sid>.<cid>``. Endings / achievements / locations are not scenes.
    """
    prefix, _, rest = site.partition(":")
    if prefix == "scene_requires":
        return rest or None
    if prefix == "scene":
        return rest.split("#", 1)[0] or None
    if prefix == "choice":
        return rest.split(".", 1)[0] or None
    return None


def _probe_endings(pack_root: Path, ending_ids: list[str], *,
                   seed: int | None) -> dict[str, Any]:
    """Planner check: is each ending's flag reachable from a fresh start?

    Uses ``{"flag": <ending_id>}`` as the goal — endings are conventionally
    backed by an ``ending_*`` flag. Capped tight so the probe stays cheap;
    a ``found=False`` here means "not reached within the cap", not a proof of
    unreachability.
    """
    from .planner import Planner

    planner = Planner(str(pack_root), seed=seed)
    out: dict[str, Any] = {}
    for eid in ending_ids:
        try:
            result = planner.find_path({"flag": eid}, max_depth=40, max_nodes=3000)
            out[eid] = {"found": result.found, "depth": result.depth,
                        "nodes_explored": result.nodes_explored}
        except Exception as exc:  # a bad goal must not sink the whole probe
            out[eid] = {"found": False, "error": str(exc)}
    return out

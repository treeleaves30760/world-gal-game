"""Pack-supplied credits / attribution model + loader.

This is engine infrastructure, not game content: every pack can ship its own
製作群 / 鳴謝 (attributions, licensing, staff roll) and the engine renders it in
an in-game Credits overlay. It exists chiefly so a pack whose assets carry a
licence with an attribution obligation — e.g. CC-BY music, which *requires*
in-game or store-page credit when distributed — can satisfy that obligation
from data alone, with no engine edit.

The loader is data-driven and tries several pack-supplied sources, in order:

1. ``<pack>/content/credits.yaml`` — the structured, first-class source.
2. ``meta.yaml`` ``credits:`` and/or ``attribution:`` fields.
3. A bundled plain-text ``CREDITS.md`` somewhere conventional in the pack
   (root, ``content/``, or ``assets/bgm/`` — the BGM-attribution convention).
4. Engine defaults (a graceful "powered by" block) when the pack supplies none.

Every source is normalized into the same :class:`Credits` shape — an ordered
list of :class:`CreditSection` (an optional heading + body lines) — so the UI
renders one model regardless of where the data came from. Pure-Python and
pygame-free, so it is unit-testable on its own.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


# Conventional relative locations for a bundled plain-text credits file, tried
# in order. ``assets/bgm/CREDITS.md`` is included because the BGM-attribution
# convention (e.g. CC-BY music notes) often lives beside the tracks.
_MD_CANDIDATES: tuple[str, ...] = (
    "CREDITS.md", "credits.md",
    "content/CREDITS.md", "content/credits.md",
    "assets/bgm/CREDITS.md", "assets/CREDITS.md",
)

# Engine fallback when a pack supplies no credits at all. Keeps the screen from
# ever being empty and quietly advertises the engine.
_ENGINE_DEFAULT_TITLE = "鳴謝"
_ENGINE_DEFAULT_SECTIONS = (
    (None, ("本作以 World Gal-Game 引擎製作。",
            "Powered by World Gal-Game.")),
)


@dataclass
class CreditSection:
    """One block of the credits: an optional heading plus its body lines."""

    heading: str | None = None
    body: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {"heading": self.heading, "body": list(self.body)}


@dataclass
class Credits:
    """An ordered set of credit sections plus an optional overall title.

    ``source`` records where the content came from (``"credits.yaml"`` /
    ``"meta"`` / ``"CREDITS.md"`` / ``"engine-default"``) — handy for tests and
    for an agent inspecting why a screen shows what it shows.
    """

    title: str = _ENGINE_DEFAULT_TITLE
    sections: list[CreditSection] = field(default_factory=list)
    source: str = "engine-default"

    def is_empty(self) -> bool:
        return not any(s.heading or any(line for line in s.body)
                       for s in self.sections)

    def to_dict(self) -> dict[str, Any]:
        return {"title": self.title, "source": self.source,
                "sections": [s.to_dict() for s in self.sections]}

    def plain_lines(self) -> list[str]:
        """Flatten to a list of display lines (heading rows + body rows +
        a blank between sections). Used by the renderer and by tests."""
        out: list[str] = []
        for i, sec in enumerate(self.sections):
            if i:
                out.append("")
            if sec.heading:
                out.append(sec.heading)
            out.extend(sec.body)
        return out


# --------------------------------------------------------------------------
# Normalization: turn the many shapes a pack might use into CreditSection[].
# --------------------------------------------------------------------------

def _as_lines(value: Any) -> list[str]:
    """Coerce a scalar / list / multiline string into a list of text lines."""
    if value is None:
        return []
    if isinstance(value, str):
        return [ln.rstrip() for ln in value.splitlines()] or [value]
    if isinstance(value, (list, tuple)):
        lines: list[str] = []
        for item in value:
            lines.extend(_as_lines(item))
        return lines
    return [str(value)]


def _section_from_mapping(mapping: dict) -> CreditSection:
    """A section dict like ``{heading|title|name, lines|body|entries|text}``."""
    heading = (mapping.get("heading") or mapping.get("title")
               or mapping.get("name") or mapping.get("section"))
    body_src = (mapping.get("lines") if "lines" in mapping
                else mapping.get("body") if "body" in mapping
                else mapping.get("entries") if "entries" in mapping
                else mapping.get("text"))
    return CreditSection(heading=(str(heading) if heading else None),
                         body=_as_lines(body_src))


def _normalize(value: Any, *, default_heading: str | None = None
               ) -> list[CreditSection]:
    """Normalize any supported ``credits``/``attribution`` value into sections.

    Accepts: a string / list of strings (one untitled section), a list of
    section-mappings, or a single section-mapping. Unknown shapes degrade to a
    single section carrying their stringified lines, never raising.
    """
    if value is None:
        return []
    if isinstance(value, str):
        body = _as_lines(value)
        return [CreditSection(heading=default_heading, body=body)] if body else []
    if isinstance(value, dict):
        # A mapping of {heading: lines, ...} OR a single section mapping.
        section_keys = {"heading", "title", "name", "section",
                        "lines", "body", "entries", "text"}
        if section_keys & set(value):
            sec = _section_from_mapping(value)
            return [sec] if (sec.heading or sec.body) else []
        out: list[CreditSection] = []
        for k, v in value.items():
            out.append(CreditSection(heading=str(k), body=_as_lines(v)))
        return out
    if isinstance(value, (list, tuple)):
        # Either a list of strings (one section) or a list of section mappings.
        if all(isinstance(x, str) for x in value):
            return [CreditSection(heading=default_heading,
                                  body=_as_lines(value))]
        out = []
        for item in value:
            if isinstance(item, dict):
                sec = _section_from_mapping(item)
                if sec.heading or sec.body:
                    out.append(sec)
            else:
                out.append(CreditSection(body=_as_lines(item)))
        return out
    return [CreditSection(heading=default_heading, body=_as_lines(value))]


def _from_credits_yaml(data: Any) -> Credits | None:
    """Build Credits from a parsed ``content/credits.yaml`` document.

    Supported top-level shapes::

        title: 製作群           # optional
        sections: [ {heading, lines}, ... ]

    or simply ``sections:``/``credits:`` as the list, or a bare list/string.
    """
    if data is None:
        return None
    title = _ENGINE_DEFAULT_TITLE
    body_value: Any = data
    if isinstance(data, dict):
        title = str(data.get("title") or _ENGINE_DEFAULT_TITLE)
        if "sections" in data:
            body_value = data["sections"]
        elif "credits" in data:
            body_value = data["credits"]
        else:
            # A dict of {heading: lines} with no wrapper keys.
            body_value = {k: v for k, v in data.items() if k != "title"}
    sections = _normalize(body_value)
    if not sections:
        return None
    return Credits(title=title, sections=sections, source="credits.yaml")


def _from_meta(meta: dict) -> Credits | None:
    """Build Credits from ``meta.yaml`` ``credits:`` and/or ``attribution:``."""
    if not isinstance(meta, dict):
        return None
    sections: list[CreditSection] = []
    if meta.get("credits") is not None:
        sections.extend(_normalize(meta["credits"]))
    if meta.get("attribution") is not None:
        # Give a bare attribution block a sensible default heading so it reads
        # as a licence/credit section rather than loose lines.
        sections.extend(_normalize(meta["attribution"],
                                   default_heading="授權與出處"))
    if not sections:
        return None
    return Credits(title=_ENGINE_DEFAULT_TITLE, sections=sections,
                   source="meta")


def _strip_markdown(text: str) -> list[CreditSection]:
    """Turn a plain-text/markdown CREDITS file into sections.

    Lightweight: ``#``-prefixed lines start a new section heading; everything
    else is body. Markdown table pipes / blockquote markers are kept verbatim
    (the renderer wraps them) — this is a credits screen, not a full markdown
    engine, but the raw text stays legible.
    """
    sections: list[CreditSection] = []
    cur: CreditSection | None = None
    for raw in text.splitlines():
        line = raw.rstrip()
        stripped = line.lstrip()
        if stripped.startswith("#"):
            heading = stripped.lstrip("#").strip()
            cur = CreditSection(heading=heading or None, body=[])
            sections.append(cur)
            continue
        if cur is None:
            cur = CreditSection(heading=None, body=[])
            sections.append(cur)
        cur.body.append(line)
    # Trim trailing blank body lines per section.
    for sec in sections:
        while sec.body and not sec.body[-1].strip():
            sec.body.pop()
    return [s for s in sections if s.heading or s.body]


def _from_markdown_file(pack_root: Path) -> Credits | None:
    """Find + parse a bundled plain-text credits file under the pack root."""
    for rel in _MD_CANDIDATES:
        path = pack_root / rel
        try:
            if not path.is_file():
                continue
            text = path.read_text(encoding="utf-8")
        except Exception:
            continue
        sections = _strip_markdown(text)
        if sections:
            return Credits(title=_ENGINE_DEFAULT_TITLE, sections=sections,
                           source="CREDITS.md")
    return None


def _engine_default() -> Credits:
    return Credits(
        title=_ENGINE_DEFAULT_TITLE,
        sections=[CreditSection(heading=h, body=list(b))
                  for h, b in _ENGINE_DEFAULT_SECTIONS],
        source="engine-default",
    )


def load_credits(meta: dict | None, pack_root: Path | None) -> Credits:
    """Resolve a pack's credits from its supplied sources, in precedence order.

    1. ``<pack_root>/content/credits.yaml`` (structured, first-class).
    2. ``meta.yaml`` ``credits:`` / ``attribution:`` fields.
    3. A bundled plain-text ``CREDITS.md`` in a conventional pack location.
    4. Engine defaults (never empty).

    Each step is isolated: a malformed/unreadable source is skipped rather than
    crashing the screen, so the worst case is the engine-default block.
    """
    meta = meta or {}

    # (1) content/credits.yaml
    if pack_root is not None:
        cy = Path(pack_root) / "content" / "credits.yaml"
        try:
            if cy.is_file():
                data = yaml.safe_load(cy.read_text(encoding="utf-8"))
                built = _from_credits_yaml(data)
                if built is not None and not built.is_empty():
                    return built
        except Exception:
            pass

    # (2) meta.yaml fields
    try:
        built = _from_meta(meta)
        if built is not None and not built.is_empty():
            return built
    except Exception:
        pass

    # (3) bundled CREDITS.md
    if pack_root is not None:
        try:
            built = _from_markdown_file(Path(pack_root))
            if built is not None and not built.is_empty():
                return built
        except Exception:
            pass

    # (4) engine default
    return _engine_default()

#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""Generate textbook diagrams via the codex CLI's built-in image_gen tool.

This wrapper is tuned for *instructional diagrams* (flowcharts, architectures,
data pipelines, neural-net topologies, decision trees, sequence diagrams,
training-loop schematics, ...) embedded in MDX chapters of an AI/ML textbook.

It assembles a long, opinionated prompt with a fixed didactic style guide so
output stays visually consistent across the book, then shells out to:

    codex exec --skip-git-repo-check --sandbox workspace-write --full-auto '<prompt>'

Codex (logged in via your ChatGPT subscription) will then call its built-in
image_gen tool. If you pass --reference paths, the prompt instructs codex to
first call its built-in view_image tool on each reference so they enter the
conversation context, and to use them as the *structural / layout* reference
(node positions, topology) while applying the textbook's visual style.

Recommended workflow for non-trivial diagrams:

    1. Sketch the diagram as a quick HTML/SVG mockup (positions + labels).
    2. Render that mockup to a PNG (any browser screenshot or headless tool).
    3. Pass the PNG via --reference; the model treats it as the layout truth.

Inputs are accepted either as repeated CLI flags or via a single JSON spec
file (--spec). Both modes can be combined; flags override / append to spec.

Example (CLI flags):

    uv run tools/imagegen.py \\
        --diagram-type flowchart \\
        --topic "Forward and backward pass in a 2-layer MLP" \\
        --audience "undergraduate CS students new to ML" \\
        --reading-order "left-to-right" \\
        --aspect 16:9 \\
        --element "Input x: 28x28 grayscale image flattened to 784-dim vector" \\
        --element "Hidden layer h = ReLU(W1 x + b1)" \\
        --element "Output logits z = W2 h + b2" \\
        --element "Loss L = cross_entropy(softmax(z), y)" \\
        --relationship "Input x -> Hidden layer : W1, b1 (forward)" \\
        --relationship "Hidden layer -> Output logits : W2, b2 (forward)" \\
        --relationship "Output logits -> Loss : softmax + CE (forward)" \\
        --relationship "Loss -> Output logits : dL/dz (backward)" \\
        --relationship "Output logits -> Hidden layer : dL/dh (backward)" \\
        --relationship "Hidden layer -> Input x : dL/dx (backward, dashed)" \\
        --label "Forward path = solid blue arrows" \\
        --label "Backward path = dashed coral arrows" \\
        --reference ./assets/_drafts/mlp-mockup.png \\
        --output ./assets/diagrams/mlp-forward-backward-v1.png

Example (JSON spec):

    uv run tools/imagegen.py --spec ./tools/specs/mlp.json \\
        --output ./assets/diagrams/mlp-v1.png
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

# --- prompt template ---------------------------------------------------------

PROMPT_TEMPLATE = """$imagegen

Generate this image NOW using the built-in image_gen tool.
Do NOT refine, summarize, or restructure this prompt. Do NOT ask questions.
Do NOT fall back to a CLI / shell solution. Use the built-in tool only.
{reference_block}
=== CONTEXT ===
This image will be embedded inside a chapter of a Mandarin/English AI & ML
algorithms textbook authored in MDX. It will sit next to body text, so it
must be print-clean, didactic, immediately readable, and stylistically
consistent with other diagrams in the book.

=== WHAT THIS DIAGRAM TEACHES (CONTEXT FOR YOU, NOT TEXT TO RENDER) ===
The five fields below are briefing context so you understand the goal.
They are NOT labels to draw inside the image. Do not render them as a
title, banner, footer, callout, or any other visible text on the canvas.
Diagram type    : {diagram_type}
Topic           : {topic}
Audience        : {audience}
Reading order   : {reading_order}
Key takeaway    : {takeaway}

=== ELEMENTS / NODES ===
Render each of the following as its own labeled shape. Use the exact label
text given (do not paraphrase, translate, or invent new wording).
{elements_block}

=== RELATIONSHIPS / EDGES ===
Each line below is "source -> target : edge label". Render arrows clearly
directional. Avoid arrow crossings where possible; route around nodes.
{relationships_block}

=== EXTRA LABELS / ANNOTATIONS ===
Render the following as small text annotations placed near the relevant
element or in a compact legend. Reproduce text verbatim.
{labels_block}

=== LAYOUT & SPACING ===
- Balanced whitespace; no element clipped at the canvas edges.
- Group conceptually related nodes; if helpful, enclose them in a light
  rounded panel with a thin border and a small header label.
- Arrows must be unambiguously directional (filled triangle arrowheads).
- Maintain consistent spacing between nodes; align to an implicit grid.
- Reading order should be visually obvious without reading the labels.
{layout_extra}

=== VISUAL STYLE (FIXED — APPLY ALWAYS) ===
- Clean technical infographic, vector-illustration look. Flat shapes with
  at most a 1px outline and an optional very subtle drop shadow.
- No painterly textures, no 3D / isometric, no glossy or skeuomorphic
  chrome, no hand-drawn / sketchy aesthetic, no chalkboard look.
- Shape vocabulary (use consistently across the book):
    * Rounded rectangle  -> process / computation step
    * Rectangle          -> data / tensor / variable
    * Parallelogram      -> input / output (I/O)
    * Diamond            -> decision / branch
    * Circle             -> state / scalar value
    * Cylinder           -> dataset / storage
    * Stacked rectangles -> batch / collection
- Sans-serif label font with high legibility (Inter / Helvetica / SF Pro
  feel). All text horizontal — no rotated or vertical labels.
- Designed to remain readable at ~1200px wide on a textbook page.
{style_extra}

=== COLOR PALETTE (FIXED — APPLY ALWAYS) ===
- Background: off-white (#FAFAF7) or transparent.
- Primary accent (main flow / forward path / "happy path"):
    cool indigo-blue, around #3B5BDB.
- Secondary accent (alternate flow / backward pass / contrast):
    warm coral, around #E8765A.
- Tertiary accent (highlights, current step):
    muted amber, around #E8B339, used sparingly.
- Structure / secondary text: neutral grey #4B5563.
- Body fill for nodes: very pale tint of the relevant accent (≤15% opacity)
  so labels remain crisp black/dark grey.
- No neon, no heavy gradients, no rainbow palettes.
- Never rely on color alone to convey meaning — always pair with a shape
  difference, a line style, or a textual label (accessibility).
{palette_extra}

=== HARD CONSTRAINTS ===
- Render every label EXACTLY as written above. Do not invent text. Do not
  translate between English and Chinese. Do not add Lorem-ipsum filler.
- THE ONLY TEXT ALLOWED INSIDE THE IMAGE comes from the ELEMENTS / NODES,
  RELATIONSHIPS / EDGES, and EXTRA LABELS / ANNOTATIONS sections above.
  Nothing else from this prompt may be rendered as visible text.
- DO NOT render any of the following inside the image canvas, in any
  form (chart title, banner, header strip, footer caption, callout box,
  watermark, sidebar legend description, or otherwise):
    * the Topic line
    * the Key takeaway line
    * the Audience line
    * the Reading order line
    * the Diagram type line
    * any prose that describes what the diagram is showing
      (e.g. "this diagram shows...", "畫面分為...", "上半...下半...",
       "左邊：... 右邊：...", "整張圖呈現...")
    * any sentence that summarises or concludes the diagram
      (e.g. "三種不同的解法", "差別超大", "...讓訓練穩定不崩潰")
    * any verbose colour-scheme legend that re-explains the palette
      (e.g. "indigo 表示主要流程，coral 表示回饋流程")
  These belong to the surrounding figcaption / body text, NOT to the
  image. The image must be readable as a pure diagram with no title bar
  and no concluding caption strip.
- Short panel labels for multi-panel diagrams ARE allowed when they appear
  in the ELEMENTS list (e.g. "監督式學習", "RNN 處理句子"); they must be
  short noun phrases, not descriptive sentences.
- Math notation in labels (e.g. W1, dL/dx, softmax(z)) must render
  legibly; if a glyph cannot be rendered cleanly, fall back to plain
  ASCII rather than producing garbled characters.
- Output must be a single self-contained diagram — not a grid / collage
  of multiple unrelated diagrams (unless a multi-panel layout is
  explicitly requested in the elements list).
- No watermarks, signatures, stock-image artifacts, decorative borders,
  or unrelated background imagery.
- No mascot characters, people, or hands unless explicitly requested.
{constraints_extra}

=== AVOID ===
- Overlapping arrows, illegible micro-text, color-only encoding.
- Photorealism, depth-of-field blur, lens flare, particle effects.
- AI-art tropes: glowing brain icons, generic "data" floating cubes,
  cyberpunk grids, holographic UI overlays.
- Title bars, header strips, banner text across the top of the canvas.
- Footer caption strips or rounded "takeaway" pills along the bottom.
- Meta-descriptive text that narrates the diagram's own structure
  ("上半...下半...", "畫面分為...", "左邊：...右邊：...").
- Re-stating the figcaption inside the image.
{avoid_extra}

=== OUTPUT SPEC ===
- Aspect ratio  : {aspect}
- Long edge     : at least 1536px
- Format        : PNG; background may be transparent or solid #FAFAF7
- Filename      : the model can use any temp name; the wrapper will move it.

After generating, MOVE the final image to:
    {output}
Then print the absolute path of the saved file as the final line of output.
"""

REFERENCE_BLOCK_TEMPLATE = """
=== REFERENCE IMAGES (READ FIRST) ===
Before calling image_gen, call the built-in view_image tool on EACH of the
following local files so they enter the conversation context:
{reference_paths}

Treat the reference image(s) as the STRUCTURAL / LAYOUT TRUTH:
- Preserve node positions, grouping, connection topology, and relative sizes.
- Preserve the reading order implied by the reference.
- Preserve which labels go on which element.
Then APPLY the visual style described below (colors, shapes, typography,
line weights). The reference is a wireframe, not a finished design — your
job is to redraw it cleanly in the textbook's house style.
"""

# --- spec object -------------------------------------------------------------


@dataclass
class Spec:
    diagram_type: str = ""
    topic: str = ""
    audience: str = "undergraduate CS students with limited prior ML background"
    reading_order: str = "left-to-right"
    takeaway: str = ""
    aspect: str = "16:9"
    elements: list[str] = field(default_factory=list)
    relationships: list[str] = field(default_factory=list)
    labels: list[str] = field(default_factory=list)
    references: list[str] = field(default_factory=list)
    layout_extra: str = ""
    style_extra: str = ""
    palette_extra: str = ""
    constraints_extra: str = ""
    avoid_extra: str = ""

    def merge_cli(self, args: argparse.Namespace) -> None:
        # Scalars: CLI value overrides spec only if explicitly provided.
        for name in (
            "diagram_type", "topic", "audience", "reading_order",
            "takeaway", "aspect",
            "layout_extra", "style_extra", "palette_extra",
            "constraints_extra", "avoid_extra",
        ):
            cli_val = getattr(args, name, None)
            if cli_val:
                setattr(self, name, cli_val)
        # Lists: CLI flags append to whatever the spec already had.
        if args.element:
            self.elements.extend(args.element)
        if args.relationship:
            self.relationships.extend(args.relationship)
        if args.label:
            self.labels.extend(args.label)
        if args.reference:
            self.references.extend(str(p) for p in args.reference)

    def validate(self) -> list[str]:
        errs: list[str] = []
        if not self.diagram_type:
            errs.append("--diagram-type (or spec.diagram_type) is required")
        if not self.topic:
            errs.append("--topic (or spec.topic) is required")
        if not self.elements:
            errs.append("at least one --element (or spec.elements) is required")
        return errs


# --- prompt assembly ---------------------------------------------------------


def _bullet(items: Iterable[str]) -> str:
    items = [s for s in items if s and s.strip()]
    if not items:
        return "(none)"
    return "\n".join(f"  - {s.strip()}" for s in items)


def _extra(label: str, value: str) -> str:
    if not value or not value.strip():
        return ""
    return f"\nAdditional ({label}):\n  {value.strip()}"


def build_reference_block(refs: list[Path]) -> str:
    if not refs:
        return ""
    paths = "\n".join(f"  - {p}" for p in refs)
    return REFERENCE_BLOCK_TEMPLATE.format(reference_paths=paths)


def build_prompt(spec: Spec, output_abs: Path, reference_paths: list[Path]) -> str:
    return PROMPT_TEMPLATE.format(
        reference_block=build_reference_block(reference_paths),
        diagram_type=spec.diagram_type,
        topic=spec.topic,
        audience=spec.audience,
        reading_order=spec.reading_order,
        takeaway=spec.takeaway or "(unspecified — infer from elements)",
        elements_block=_bullet(spec.elements),
        relationships_block=_bullet(spec.relationships),
        labels_block=_bullet(spec.labels),
        layout_extra=_extra("layout", spec.layout_extra),
        style_extra=_extra("style", spec.style_extra),
        palette_extra=_extra("palette", spec.palette_extra),
        constraints_extra=_extra("constraints", spec.constraints_extra),
        avoid_extra=_extra("avoid", spec.avoid_extra),
        aspect=spec.aspect,
        output=output_abs,
    )


# --- CLI ---------------------------------------------------------------------


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Generate a textbook diagram via codex + image_gen.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument("--spec", type=Path,
                   help="JSON spec file; flags override / append to its fields.")
    p.add_argument("--diagram-type",
                   help="flowchart | architecture | pipeline | neural-net | "
                        "decision-tree | sequence | training-loop | concept-map | ...")
    p.add_argument("--topic",
                   help="One-sentence description of what the diagram teaches.")
    p.add_argument("--audience",
                   help="Reader profile, e.g. 'undergrad CS, no prior ML'.")
    p.add_argument("--reading-order",
                   help="left-to-right | top-down | circular | radial | ...")
    p.add_argument("--takeaway",
                   help="The single idea a reader should walk away with.")
    p.add_argument("--aspect", help="e.g. 16:9, 4:3, 1:1, 3:4")
    p.add_argument("--element", action="append", default=[],
                   help='Repeatable. "Name: short description". '
                        'The "Name" becomes the rendered label.')
    p.add_argument("--relationship", action="append", default=[],
                   help='Repeatable. "Source -> Target : edge label".')
    p.add_argument("--label", action="append", default=[],
                   help='Repeatable. Extra annotations / legend text.')
    p.add_argument("--reference", action="append", default=[], type=Path,
                   help="Repeatable. Local PNG/JPG to use as layout reference. "
                        "Codex will view_image each one before generating.")
    p.add_argument("--layout-extra", default="",
                   help="Free-form extra layout instructions.")
    p.add_argument("--style-extra", default="",
                   help="Free-form extra style notes (in addition to house style).")
    p.add_argument("--palette-extra", default="",
                   help="Free-form palette notes (in addition to house palette).")
    p.add_argument("--constraints-extra", default="",
                   help="Free-form extra hard constraints.")
    p.add_argument("--avoid-extra", default="",
                   help="Free-form extra things to avoid.")
    p.add_argument("--output", "-o", required=True, type=Path,
                   help="Destination PNG path (relative to cwd or absolute).")
    p.add_argument("--dry-run", action="store_true",
                   help="Print the codex command and prompt, don't execute.")
    return p.parse_args(argv)


def load_spec(path: Path | None) -> Spec:
    if path is None:
        return Spec()
    data = json.loads(path.read_text())
    allowed = {f for f in Spec.__dataclass_fields__}
    unknown = set(data) - allowed
    if unknown:
        print(f"warning: ignoring unknown spec keys: {sorted(unknown)}", file=sys.stderr)
    cleaned = {k: v for k, v in data.items() if k in allowed}
    return Spec(**cleaned)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    if shutil.which("codex") is None:
        print("error: `codex` CLI not found in PATH", file=sys.stderr)
        return 127

    spec = load_spec(args.spec)
    spec.merge_cli(args)

    errs = spec.validate()
    if errs:
        for e in errs:
            print(f"error: {e}", file=sys.stderr)
        return 2

    output_abs = args.output.expanduser().resolve()
    output_abs.parent.mkdir(parents=True, exist_ok=True)

    reference_paths: list[Path] = []
    for r in spec.references:
        rp = Path(r).expanduser().resolve()
        if not rp.exists():
            print(f"error: reference image not found: {rp}", file=sys.stderr)
            return 2
        reference_paths.append(rp)

    prompt = build_prompt(spec, output_abs, reference_paths)
    cmd = [
        "codex", "exec",
        "--skip-git-repo-check",
        "--sandbox", "workspace-write",
        "--full-auto",
        prompt,
    ]

    if args.dry_run:
        print("--- codex command (prompt arg shown separately below) ---")
        print(" ".join(repr(c) for c in cmd[:-1]) + " <PROMPT>")
        print("--- prompt ---")
        print(prompt)
        return 0

    print(f"[imagegen] generating -> {output_abs}", file=sys.stderr)
    if reference_paths:
        print(f"[imagegen] references: {[str(p) for p in reference_paths]}",
              file=sys.stderr)

    # Codex's imagegen skill sometimes refuses to overwrite an existing file
    # at the destination and instead saves with a `-v2` (or similar) suffix.
    # To force a clean overwrite, remove the destination if it already exists
    # *before* invoking codex. The new file will then land at the requested path.
    if output_abs.exists():
        output_abs.unlink()

    result = subprocess.run(cmd)
    if result.returncode != 0:
        print(f"[imagegen] codex exited with {result.returncode}", file=sys.stderr)
        return result.returncode

    if not output_abs.exists():
        # If codex still wrote to a sibling path with a `-v2` / `-v3` / `-vN`
        # suffix, rescue it: take the most recently modified sibling matching
        # `<stem>-v*<ext>` and rename it to the requested name.
        stem = output_abs.stem
        ext = output_abs.suffix
        candidates = sorted(
            output_abs.parent.glob(f"{stem}-v*{ext}"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        if candidates:
            candidates[0].rename(output_abs)
            print(f"[imagegen] rescued sibling {candidates[0].name} -> {output_abs.name}",
                  file=sys.stderr)
        else:
            print(f"[imagegen] warning: expected file not found at {output_abs}",
                  file=sys.stderr)
            return 1

    print(str(output_abs))
    return 0


if __name__ == "__main__":
    sys.exit(main())

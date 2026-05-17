#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = ["pyyaml"]
# ///
"""Generate gal-game artwork via the codex CLI's built-in image_gen tool.

Sibling of `tools/imagegen.py`. While `imagegen.py` is tuned for
instructional diagrams, this tool is tuned for the visuals a Japanese
visual novel / gal-game needs:

    --kind background    : scene backdrops (campus, library, lab, ...)
    --kind portrait      : character bust shots with transparent bg
    --kind cg            : full-screen "event CG" illustrations
    --kind ui            : UI panels / iconography
    --kind title         : title-screen splash artwork

The CLI is intentionally similar to `imagegen.py`:

    uv run tools/imagegen_art.py --kind portrait \\
        --character "Lin Qing-yi (林青衣)" \\
        --description "中文系大三女學生，氣質溫柔；長髮、淺青色襯衫，米色長裙" \\
        --expression "smile" \\
        --pose "正面，胸上半身，手裡輕輕拿著一本舊書" \\
        --style "soft anime visual novel art, slight watercolor wash" \\
        --output ./games/tsinghua_strange_tales/assets/characters/qingyi_smile.png

Inputs can also come from a JSON spec via --spec (same merge rules as
the diagram tool: spec defines defaults, flags append / override).

The tool shells out to:

    codex exec --skip-git-repo-check --sandbox workspace-write --full-auto '<prompt>'

so codex (logged into your ChatGPT subscription) does the actual call to
image_gen. After generation the file is moved to --output and its path
printed.

Tip: art kinds default to --aspect:
    background -> 16:9
    title      -> 16:9
    cg         -> 16:9
    portrait   -> 3:4
    ui         -> 1:1
You can always override with --aspect.
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


# ---------------------------------------------------------------------------
# Kind-specific prompt blocks
# ---------------------------------------------------------------------------


_HOUSE_STYLE = """
=== HOUSE VISUAL STYLE (this game) ===
- Genre: 日系視覺小說 / Japanese visual-novel artwork.
- Tone: gentle, slightly melancholic, with a touch of unease — this is a
  campus ghost-story dating sim set at National Tsing Hua University.
- Brushwork: soft anime/visual-novel illustration; clean linework with a
  light watercolor wash; subtle film-grain; never flat-vector / never 3D.
- Palette: deep indigo + sakura pink + lantern amber accents; muted
  desaturated mids; warm key light + cool ambient; for haunted scenes,
  raise contrast and push shadows cooler.
- Typography: NEVER render any text, signage, watermarks, captions,
  speech bubbles, kanji/hanzi/latin glyphs, scribbles or doodles in the
  image. Signs and chalkboards may exist as shapes but their faces must
  be blank or illegible suggestion only.
- Composition: cinematic, painterly, with intentional depth-of-field and
  one clear focal subject. Avoid centred-symmetric posters.
- Mood: subtle Taiwanese campus atmosphere — old red-brick buildings,
  banyan trees, wet stone after rain, sodium-orange lamplight.
"""


_KIND_BACKGROUND = """
=== KIND: BACKGROUND ===
Compose a wide environment shot suitable for a visual-novel BACKDROP:
- No people in the frame.
- 16:9 cinematic framing, mid-foreground / middle / far background.
- Lower 25% of the image should be slightly subdued so a dialogue box
  rendered on top of it remains readable; do NOT cover the bottom with
  important detail.
- Lighting must read at a glance; pick ONE clear time of day from the
  setting below.
"""


_KIND_TITLE = """
=== KIND: TITLE SPLASH ===
Compose an evocative title-screen splash:
- Cinematic 16:9, with a clear focal motif on the right or left third.
- Centre/top area should be quieter so a title-text overlay fits.
- No text drawn in the image. The engine renders the title separately.
"""


_KIND_PORTRAIT = """
=== KIND: CHARACTER PORTRAIT ===
Render a single character bust portrait for use in a visual-novel
dialogue box:
- 3:4 aspect, character occupies roughly the centre vertical axis.
- Frame: head + shoulders + chest; arms may enter frame for the pose.
- Background MUST be fully transparent. No environment, no shadows
  on the floor, no studio backdrop. Alpha 0 outside the character's
  silhouette.
- Anatomy: anime visual-novel proportions; expressive eyes; clean line
  art with soft cel shading.
- Stand alone — the same character will be reused in many scenes, so
  pose neutrally enough to read as "this is them" rather than "this is
  them doing X".
"""


_KIND_CG = """
=== KIND: EVENT CG ===
Render a full-screen illustration for a key story beat:
- 16:9 cinematic.
- Characters and environment together; emotive composition.
- This will REPLACE the background during a scene, so it does not need
  a clear dialogue-box safe zone.
"""


_KIND_UI = """
=== KIND: UI ASSET ===
A single, isolated UI element / icon:
- Transparent background.
- Simple, recognizable silhouette at small size.
- Match the house palette.
"""


_DEFAULT_AVOID = """
=== AVOID ===
- No visible text, watermarks, signatures, lorem ipsum, kanji, hanzi
  or latin lettering of any kind drawn into the image.
- No grids, no tech-UI overlays, no holographic glow effects.
- No anachronistic objects (e.g. modern western architecture for
  a Taiwanese campus scene).
- No photorealistic faces (we want stylised anime, not CGI).
- No nudity, no sexual content, no minors in sexualised poses.
- No collage / multi-panel layouts unless explicitly asked.
"""


PROMPT_TEMPLATE = """$imagegen

Generate this image NOW using the built-in image_gen tool.
Do NOT refine or restructure this prompt. Do NOT fall back to CLI tools.
Use the built-in image_gen tool only.
{reference_block}

{house_style}

{kind_block}

=== SUBJECT ===
{subject_block}

=== ATMOSPHERE ===
{atmosphere}

=== STYLE NOTES (in addition to house style above) ===
{style_block}

=== HARD CONSTRAINTS ===
{constraints_block}

{avoid_block}

=== OUTPUT SPEC ===
- Aspect ratio  : {aspect}
- Long edge     : at least 1536px
- Format        : PNG. Backgrounds must be opaque; portraits & UI assets
                  must have a fully transparent background (alpha = 0).
- Filename      : the model can use any temp name; this wrapper will move
                  the result to the path below.

After generating, MOVE the final image to:
    {output}
Then print the absolute path of the saved file as the final line of output.
"""


REFERENCE_BLOCK_TEMPLATE = """
=== REFERENCE IMAGES (READ FIRST) ===
Before calling image_gen, call the built-in view_image tool on EACH of
the following local files so they enter the conversation context:
{reference_paths}

Treat the reference image(s) as the STRUCTURAL / LAYOUT TRUTH:
- Preserve composition, focal subject, and framing.
- Preserve which side of the frame the focal subject occupies.
- Preserve the perspective angle (eye-level / low / high).
Then APPLY the house visual style below.
"""


# ---------------------------------------------------------------------------
# Spec object
# ---------------------------------------------------------------------------


_DEFAULT_ASPECT = {
    "background": "16:9",
    "title": "16:9",
    "cg": "16:9",
    "portrait": "3:4",
    "ui": "1:1",
}


_KIND_BLOCKS = {
    "background": _KIND_BACKGROUND,
    "title": _KIND_TITLE,
    "portrait": _KIND_PORTRAIT,
    "cg": _KIND_CG,
    "ui": _KIND_UI,
}


@dataclass
class ArtSpec:
    kind: str = "background"
    subject: str = ""                 # what we're drawing (e.g. "library entrance")
    character: str = ""               # for portrait / cg
    description: str = ""             # appearance / outfit
    expression: str = ""              # smile / sad / worried (portrait)
    pose: str = ""                    # for portrait / cg
    location: str = ""                # for background / cg
    time_of_day: str = ""             # morning / dusk / midnight ...
    weather: str = ""                 # rain / clear / fog
    mood: str = ""                    # eerie / warm / hopeful ...
    style: str = ""                   # extra style notes
    palette: str = ""                 # extra palette notes
    aspect: str = ""
    references: list[str] = field(default_factory=list)
    extra_constraints: list[str] = field(default_factory=list)

    def merge_cli(self, args: argparse.Namespace) -> None:
        for name in ("kind", "subject", "character", "description",
                     "expression", "pose", "location", "time_of_day",
                     "weather", "mood", "style", "palette", "aspect"):
            v = getattr(args, name, None)
            if v:
                setattr(self, name, v)
        if args.reference:
            self.references.extend(str(p) for p in args.reference)
        if args.constraint:
            self.extra_constraints.extend(args.constraint)

    def validate(self) -> list[str]:
        errs: list[str] = []
        if self.kind not in _KIND_BLOCKS:
            errs.append(f"--kind must be one of {sorted(_KIND_BLOCKS)}")
        # subject can come from `subject` OR (character, location) depending on kind
        if self.kind in ("background", "title"):
            if not (self.subject or self.location):
                errs.append("--subject or --location required for background/title")
        if self.kind in ("portrait",):
            if not (self.character or self.subject):
                errs.append("--character or --subject required for portrait")
        return errs

    def get_aspect(self) -> str:
        return self.aspect or _DEFAULT_ASPECT.get(self.kind, "16:9")


# ---------------------------------------------------------------------------
# Prompt assembly
# ---------------------------------------------------------------------------


def _bullet(items: Iterable[str]) -> str:
    items = [s for s in items if s and s.strip()]
    if not items:
        return "(none)"
    return "\n".join(f"- {s.strip()}" for s in items)


def build_reference_block(refs: list[Path]) -> str:
    if not refs:
        return ""
    paths = "\n".join(f"  - {p}" for p in refs)
    return REFERENCE_BLOCK_TEMPLATE.format(reference_paths=paths)


def build_subject_block(spec: ArtSpec) -> str:
    lines: list[str] = []
    if spec.kind in ("portrait", "cg"):
        if spec.character:
            lines.append(f"Character: {spec.character}")
        if spec.description:
            lines.append(f"Appearance / outfit: {spec.description}")
        if spec.expression:
            lines.append(f"Expression: {spec.expression}")
        if spec.pose:
            lines.append(f"Pose / framing: {spec.pose}")
    if spec.kind in ("background", "title", "cg"):
        if spec.location:
            lines.append(f"Location: {spec.location}")
    if spec.subject:
        lines.append(f"Subject summary: {spec.subject}")
    return _bullet(lines)


def build_atmosphere_block(spec: ArtSpec) -> str:
    lines: list[str] = []
    if spec.time_of_day:
        lines.append(f"Time of day: {spec.time_of_day}")
    if spec.weather:
        lines.append(f"Weather: {spec.weather}")
    if spec.mood:
        lines.append(f"Mood: {spec.mood}")
    return _bullet(lines)


def build_style_block(spec: ArtSpec) -> str:
    lines: list[str] = []
    if spec.style:
        lines.append(spec.style)
    if spec.palette:
        lines.append(f"Palette notes: {spec.palette}")
    return _bullet(lines) if lines else "(use house style as-is)"


def build_constraints_block(spec: ArtSpec) -> str:
    base = []
    if spec.kind == "portrait":
        base.append("Background MUST be fully transparent (alpha=0).")
        base.append("Render exactly ONE character. No second figure in frame.")
    if spec.kind == "background":
        base.append("No people / characters / hands in frame.")
        base.append("Keep the bottom 25% subdued for dialogue-box overlay.")
    if spec.kind == "title":
        base.append("Leave a quieter zone for title text overlay.")
    if spec.kind == "ui":
        base.append("Transparent background; isolated single asset.")
    base.extend(spec.extra_constraints)
    return _bullet(base)


def build_prompt(spec: ArtSpec, output_abs: Path,
                 reference_paths: list[Path]) -> str:
    return PROMPT_TEMPLATE.format(
        reference_block=build_reference_block(reference_paths),
        house_style=_HOUSE_STYLE.strip(),
        kind_block=_KIND_BLOCKS[spec.kind].strip(),
        subject_block=build_subject_block(spec),
        atmosphere=build_atmosphere_block(spec),
        style_block=build_style_block(spec),
        constraints_block=build_constraints_block(spec),
        avoid_block=_DEFAULT_AVOID.strip(),
        aspect=spec.get_aspect(),
        output=output_abs,
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Generate gal-game artwork via codex + image_gen.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument("--spec", type=Path,
                   help="JSON spec file. CLI flags override / extend it.")
    p.add_argument("--kind", choices=sorted(_KIND_BLOCKS),
                   help="background | title | portrait | cg | ui")
    p.add_argument("--subject", help="one-line description of what to draw.")
    p.add_argument("--character", help="character name (portrait / cg).")
    p.add_argument("--description", help="character appearance / outfit.")
    p.add_argument("--expression", help="character expression for portrait.")
    p.add_argument("--pose", help="pose / framing for portrait or cg.")
    p.add_argument("--location", help="environment name for background / cg.")
    p.add_argument("--time-of-day", help="morning / dusk / midnight / ...")
    p.add_argument("--weather", help="rain / clear / fog / ...")
    p.add_argument("--mood", help="eerie / warm / hopeful / ...")
    p.add_argument("--style", help="extra style notes.")
    p.add_argument("--palette", help="extra palette notes.")
    p.add_argument("--aspect", help="16:9, 3:4, 1:1, ...")
    p.add_argument("--reference", action="append", default=[], type=Path,
                   help="Repeatable. Local PNG/JPG layout reference.")
    p.add_argument("--constraint", action="append", default=[],
                   help="Repeatable. Extra hard constraint sentence.")
    p.add_argument("--output", "-o", required=True, type=Path,
                   help="Destination PNG path.")
    p.add_argument("--dry-run", action="store_true",
                   help="Print the codex command + prompt, don't execute.")
    return p.parse_args(argv)


def load_spec(path: Path | None) -> ArtSpec:
    if path is None:
        return ArtSpec()
    data = json.loads(path.read_text())
    allowed = {f for f in ArtSpec.__dataclass_fields__}
    unknown = set(data) - allowed
    if unknown:
        print(f"warning: ignoring unknown spec keys: {sorted(unknown)}",
              file=sys.stderr)
    cleaned = {k: v for k, v in data.items() if k in allowed}
    return ArtSpec(**cleaned)


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

    print(f"[imagegen-art] generating ({spec.kind}) -> {output_abs}",
          file=sys.stderr)
    if reference_paths:
        print(f"[imagegen-art] references: {[str(p) for p in reference_paths]}",
              file=sys.stderr)

    if output_abs.exists():
        output_abs.unlink()

    result = subprocess.run(cmd)
    if result.returncode != 0:
        print(f"[imagegen-art] codex exited with {result.returncode}",
              file=sys.stderr)
        return result.returncode

    if not output_abs.exists():
        stem = output_abs.stem
        ext = output_abs.suffix
        candidates = sorted(
            output_abs.parent.glob(f"{stem}-v*{ext}"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        if candidates:
            candidates[0].rename(output_abs)
            print(f"[imagegen-art] rescued sibling {candidates[0].name} -> "
                  f"{output_abs.name}", file=sys.stderr)
        else:
            print(f"[imagegen-art] warning: expected file not found at "
                  f"{output_abs}", file=sys.stderr)
            return 1

    print(str(output_abs))
    return 0


if __name__ == "__main__":
    sys.exit(main())

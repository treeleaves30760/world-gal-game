#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = ["pyyaml", "openai"]
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
        --character "Heroine One (林清雪)" \\
        --description "19 歲女主角，氣質溫柔；長髮、淺色襯衫、米色長裙" \\
        --expression "smile" \\
        --pose "正面，胸上半身，手裡拿著一本素描本" \\
        --style "soft anime visual novel art, slight watercolor wash" \\
        --output ./games/demo_pack/assets/characters/heroine_1_smile.png

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

TRANSPARENT PORTRAITS / UI — how the alpha channel is produced:

  gpt-image-2 (codex's default model) CANNOT render a transparent background.
  Asking it for transparency makes it paint a grey/white checkerboard (its
  idea of "transparent") into gaps such as between hair strands, which then
  bakes into the cutout as an opaque blank patch. So:

  1. Default (codex / ChatGPT subscription): chroma-key green + cutout.
         uv run tools/imagegen_art.py --kind portrait ...
     The prompt asks codex/gpt-image-2 to draw the subject on a FLAT SOLID
     #00ff00 green screen; tools/cutout.py then keys the green out with proper
     foreground decontamination + green-spill suppression. This is automatic
     for portrait / ui (disable the auto-cutout with --no-auto-cutout to keep
     the raw green image). No API key, no per-image billing.

  2. True native transparency (needs OPENAI_API_KEY, billed per image):
         uv run tools/imagegen_art.py --kind portrait --native-transparent ...
     Calls the OpenAI Images API with gpt-image-1.5 (the only image model that
     supports background=transparent) so the subject is drawn on a real alpha
     channel — best for very fine hair / glass. Falls back to path 1 if no key.

Backgrounds / CGs / titles are opaque and ignore both flags.
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
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
- Background: a perfectly flat, solid #00ff00 chroma-key GREEN that we key
  out afterwards — NOT transparent, NO checkerboard pattern, no environment,
  no ground shadow, no studio backdrop. One uniform green behind and BETWEEN
  hair strands; crisp subject edges; generous padding.
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
- Background: flat solid #00ff00 chroma-key green (keyed out afterwards) —
  NOT transparent, NOT a checkerboard pattern.
- Simple, recognizable silhouette at small size.
- Match the house palette.
"""


# gpt-image-2 (codex's default model) CANNOT render a transparent background —
# asking for transparency makes it paint a grey/white checkerboard (its idea of
# "transparent") into gaps like hair. The supported path is to generate on a
# flat removable chroma-key colour and key it out afterwards (tools/cutout.py).
_CHROMA_KEY_HEX = "#00ff00"
_CHROMA_BG = (
    f"Paint the ENTIRE background as a perfectly FLAT, SOLID {_CHROMA_KEY_HEX} "
    "chroma-key green: one single uniform colour with NO shadows, gradients, "
    "texture, reflections, floor plane, vignette or lighting variation. This "
    "green is a removable background that gets keyed out afterwards. Do NOT "
    "make the background transparent and do NOT draw a checkerboard / "
    "transparency pattern anywhere (including between hair strands) — the model "
    "cannot render real transparency, so a flat green screen is REQUIRED. Keep "
    "the subject fully separated from the green with crisp edges and generous "
    "padding so it never touches the frame."
)


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
- For portraits / UI: NEVER a transparent or checkerboard background —
  use the flat solid green chroma-key screen described above.
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
- Format        : PNG. Backgrounds / CGs / titles are opaque. Portraits & UI
                  are drawn on a FLAT SOLID #00ff00 chroma-key green (keyed out
                  afterwards) — do NOT attempt transparency or a checkerboard.
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
    # Native transparency: generate a real alpha channel directly via the
    # OpenAI Images API (gpt-image-1, background=transparent) instead of
    # drawing on a background and stripping it later. Only meaningful for
    # portrait / ui kinds. quality is the gpt-image-1 quality tier.
    native_transparent: bool = False
    quality: str = ""

    def merge_cli(self, args: argparse.Namespace) -> None:
        for name in ("kind", "subject", "character", "description",
                     "expression", "pose", "location", "time_of_day",
                     "weather", "mood", "style", "palette", "aspect",
                     "quality"):
            v = getattr(args, name, None)
            if v:
                setattr(self, name, v)
        if getattr(args, "native_transparent", False):
            self.native_transparent = True
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
        style = spec.style
        if spec.kind in ("portrait", "ui"):
            # Drop stale "outside the subject must be alpha=0 / transparent"
            # lines so they don't contradict the chroma-key green instruction.
            style = "\n".join(ln for ln in style.splitlines()
                              if not _mentions_transparency(ln))
        lines.append(style)
    if spec.palette:
        lines.append(f"Palette notes: {spec.palette}")
    return _bullet(lines) if lines else "(use house style as-is)"


def _mentions_transparency(text: str) -> bool:
    t = text.lower()
    return ("transparent" in t or "alpha=0" in t or "alpha = 0" in t
            or "alpha 0" in t or "checkerboard" in t)


def build_constraints_block(spec: ArtSpec) -> str:
    base = []
    if spec.kind == "portrait":
        base.append(_CHROMA_BG)
        base.append("Render exactly ONE character. No second figure in frame.")
    if spec.kind == "background":
        base.append("No people / characters / hands in frame.")
        base.append("Keep the bottom 25% subdued for dialogue-box overlay.")
    if spec.kind == "title":
        base.append("Leave a quieter zone for title text overlay.")
    if spec.kind == "ui":
        base.append(_CHROMA_BG)
        base.append("Isolated single asset.")
    extras = spec.extra_constraints
    if spec.kind in ("portrait", "ui"):
        # Drop stale "background must be transparent / alpha=0" lines from older
        # specs — they contradict the chroma-key workflow and re-trigger the
        # checkerboard artifact on gpt-image-2.
        extras = [c for c in extras if not _mentions_transparency(c)]
    base.extend(extras)
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


def build_api_prompt(spec: ArtSpec) -> str:
    """A leaner prompt for the direct OpenAI Images API call.

    Same artistic content as the codex prompt, minus the codex-control text
    ("$imagegen", "MOVE the file", "print the path", view_image references) —
    transparency is enforced by the API's background=transparent parameter,
    not by asking the model in prose.
    """
    blocks = [
        _HOUSE_STYLE.strip(),
        _KIND_BLOCKS[spec.kind].strip(),
        "=== SUBJECT ===\n" + build_subject_block(spec),
        "=== ATMOSPHERE ===\n" + build_atmosphere_block(spec),
        "=== STYLE NOTES (in addition to house style) ===\n" + build_style_block(spec),
        "=== HARD CONSTRAINTS ===\n" + build_constraints_block(spec),
        _DEFAULT_AVOID.strip(),
        "Render the single subject in isolation on a FULLY TRANSPARENT "
        "background (real alpha channel). No backdrop, no ground shadow, no "
        "studio sweep — alpha = 0 everywhere outside the subject's silhouette.",
    ]
    return "\n\n".join(blocks)


# gpt-image-1 only renders a fixed set of sizes; map our kinds onto the
# closest one. Native transparency is only sensible for portrait / ui.
_API_SIZE = {
    "portrait": "1024x1536",
    "ui": "1024x1024",
}
_NATIVE_KINDS = ("portrait", "ui")


def generate_native_transparent(spec: ArtSpec, output_abs: Path) -> tuple[bool, str]:
    """Generate directly via gpt-image-1 with a true transparent background.

    Returns (ok, message). ok=False means the caller should fall back to the
    codex path (no API key, openai missing, or the API errored).
    """
    import os

    if not os.environ.get("OPENAI_API_KEY"):
        return (False, "OPENAI_API_KEY not set")
    try:
        from openai import OpenAI
    except ImportError:
        return (False, "openai package not available")

    size = _API_SIZE.get(spec.kind, "1024x1024")
    quality = spec.quality or "high"
    prompt = build_api_prompt(spec)
    try:
        client = OpenAI()
        resp = client.images.generate(
            model="gpt-image-1.5",
            prompt=prompt,
            size=size,
            background="transparent",
            output_format="png",
            quality=quality,
            n=1,
        )
        import base64
        b64 = resp.data[0].b64_json
        if not b64:
            return (False, "API returned no image data")
        output_abs.write_bytes(base64.b64decode(b64))
    except Exception as e:  # noqa: BLE001 - any API failure -> fall back
        return (False, f"API error: {e}")
    return (True, f"gpt-image-1.5 native transparent, size={size}, quality={quality}")


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
    p.add_argument("--native-transparent", action="store_true",
                   help="For portrait / ui: generate a REAL transparent "
                        "background directly via the OpenAI Images API "
                        "(gpt-image-1, background=transparent) instead of "
                        "drawing on a backdrop. Needs OPENAI_API_KEY (billed "
                        "per image). Falls back to codex if unavailable.")
    p.add_argument("--quality", choices=["low", "medium", "high"],
                   help="gpt-image-1 quality tier for --native-transparent. "
                        "Default high.")
    p.add_argument("--auto-cutout", action="store_true",
                   help="Force running tools/cutout.py after generation "
                        "(default ON for portrait / ui, since they are drawn "
                        "on a chroma-key green that must be removed).")
    p.add_argument("--no-auto-defringe", action="store_true",
                   help="Skip the post-cutout defringe step (which clears the "
                        "magenta/red edge halo). Only relevant when cutout runs.")
    p.add_argument("--no-auto-cutout", action="store_true",
                   help="Skip the cutout step and leave the raw green-screen "
                        "image (e.g. to inspect or key it out yourself).")
    p.add_argument("--dry-run", action="store_true",
                   help="Print what would run, don't execute.")
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

    spec = load_spec(args.spec)
    spec.merge_cli(args)

    errs = spec.validate()
    if errs:
        for e in errs:
            print(f"error: {e}", file=sys.stderr)
        return 2

    output_abs = args.output.expanduser().resolve()
    output_abs.parent.mkdir(parents=True, exist_ok=True)

    # portrait / ui are drawn on a chroma-key green that MUST be removed, so
    # the cutout step defaults ON for them (disable with --no-auto-cutout).
    do_cutout = (spec.kind in _NATIVE_KINDS) and not args.no_auto_cutout

    # --- Path B: native transparent via the OpenAI Images API. -------------
    # Preferred for portrait / ui when an API key is available: the model
    # draws on a real transparent background, so there is no backdrop colour
    # to contaminate hair edges in the first place.
    if spec.native_transparent:
        if spec.kind not in _NATIVE_KINDS:
            print(f"[imagegen-art] --native-transparent ignored for "
                  f"kind={spec.kind} (only {'/'.join(_NATIVE_KINDS)})",
                  file=sys.stderr)
        elif args.dry_run:
            print(f"--- native transparent (gpt-image-1.5) -> {output_abs} ---")
            print(f"size={_API_SIZE.get(spec.kind)}  "
                  f"quality={spec.quality or 'high'}  background=transparent")
            print("--- api prompt ---")
            print(build_api_prompt(spec))
            return 0
        else:
            if output_abs.exists():
                output_abs.unlink()
            ok, msg = generate_native_transparent(spec, output_abs)
            if ok:
                print(f"[imagegen-art] {msg} -> {output_abs}", file=sys.stderr)
                print(str(output_abs))
                return 0
            print(f"[imagegen-art] native transparent unavailable ({msg}); "
                  f"falling back to codex + cutout.", file=sys.stderr)
            do_cutout = True  # so the codex result still gets stripped

    # --- Path A: codex image_gen (draws on a backdrop). --------------------
    if shutil.which("codex") is None:
        print("error: `codex` CLI not found in PATH", file=sys.stderr)
        return 127

    reference_paths: list[Path] = []
    for r in spec.references:
        rp = Path(r).expanduser().resolve()
        if not rp.exists():
            print(f"error: reference image not found: {rp}", file=sys.stderr)
            return 2
        reference_paths.append(rp)

    if args.dry_run:
        prompt = build_prompt(spec, output_abs, reference_paths)
        print("--- codex command (prompt arg shown separately below) ---")
        print("'codex' 'exec' '--skip-git-repo-check' '--sandbox' "
              "'workspace-write' '--full-auto' <PROMPT>")
        print("--- prompt ---")
        print(prompt)
        if do_cutout:
            print(f"--- then: cutout {output_abs} ---")
        return 0

    # Stage references into a temp dir so that deleting the output below (or a
    # concurrent job) cannot remove a file codex is about to view_image. Many
    # specs reference <char>/normal.png — sometimes the very file we are about
    # to regenerate (self-reference), which previously broke generation.
    ref_tmpdir: Path | None = None
    staged_refs = reference_paths
    if reference_paths:
        ref_tmpdir = Path(tempfile.mkdtemp(prefix="imagegen_refs_"))
        staged_refs = []
        for i, rp in enumerate(reference_paths):
            dst = ref_tmpdir / f"{i:02d}_{rp.name}"
            shutil.copy2(rp, dst)
            staged_refs.append(dst)

    prompt = build_prompt(spec, output_abs, staged_refs)
    cmd = [
        "codex", "exec",
        "--skip-git-repo-check",
        "--sandbox", "workspace-write",
        "--full-auto",
        prompt,
    ]

    print(f"[imagegen-art] generating ({spec.kind}) -> {output_abs}",
          file=sys.stderr)
    if reference_paths:
        print(f"[imagegen-art] references: {[str(p) for p in reference_paths]} "
              f"(staged in {ref_tmpdir})", file=sys.stderr)

    if output_abs.exists():
        output_abs.unlink()

    result = subprocess.run(cmd)
    if ref_tmpdir is not None:
        shutil.rmtree(ref_tmpdir, ignore_errors=True)
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

    # --- strip the chroma-key green to a clean transparent cutout ----------
    if do_cutout:
        cutout_py = Path(__file__).resolve().parent / "cutout.py"
        if cutout_py.exists():
            print(f"[imagegen-art] auto-cutout -> {output_abs}", file=sys.stderr)
            cut = subprocess.run(["uv", "run", str(cutout_py), str(output_abs)])
            if cut.returncode != 0:
                print(f"[imagegen-art] cutout exited with {cut.returncode}; "
                      f"the raw image is still at {output_abs}", file=sys.stderr)
        else:
            print(f"[imagegen-art] --auto-cutout: cutout.py not found at "
                  f"{cutout_py}", file=sys.stderr)

        # --- defringe: clear the magenta/red edge halo cutout's green unmix
        # leaves on dark-hair edges (cutout's despill is single-channel and
        # misses the two-channel magenta cast). Cheap, idempotent, alpha-safe.
        if not args.no_auto_defringe:
            defringe_py = Path(__file__).resolve().parent / "defringe.py"
            if defringe_py.exists():
                print(f"[imagegen-art] auto-defringe -> {output_abs}", file=sys.stderr)
                dfr = subprocess.run(["uv", "run", str(defringe_py), str(output_abs)])
                if dfr.returncode != 0:
                    print(f"[imagegen-art] defringe exited with {dfr.returncode}; "
                          f"the cutout is still at {output_abs}", file=sys.stderr)

    print(str(output_abs))
    return 0


if __name__ == "__main__":
    sys.exit(main())

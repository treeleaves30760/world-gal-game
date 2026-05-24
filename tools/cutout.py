#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = ["pillow", "numpy", "scipy", "rembg[cpu]", "onnxruntime", "pymatting"]
# ///
"""Cut a subject out of its background with CLEAN, halo-free edges.

The white fringe ("白邊") you see around hair after a naive background
removal is NOT an alpha problem — it is a *colour contamination* problem.
When an image is generated on a white / near-white background, the pixels
along a hair edge are physically a blend of hair colour and white. Making
those pixels transparent and compositing them over a dark game background
lets the baked-in white show through as a halo. No segmentation model, no
matter how good, can fix this on its own: the white is in the RGB, not the
alpha.

The fix is *foreground colour estimation* (a.k.a. decontamination /
defringe): replace every edge pixel's contaminated RGB with an estimate of
its true foreground colour, computed from nearby confident-foreground
pixels. This tool does that with pymatting's multi-level foreground
estimator, layered on top of a high-quality alpha matte. It is the missing
step that turns "good mask, still has a halo" into "clean cutout".

Pipeline (segment mode):
  1. Derive an alpha matte. Default model is birefnet-portrait (precise
     edges). With --hybrid we ALSO run u2net and union in its *eroded
     interior*, so out-stretched limbs birefnet sometimes drops are rescued
     WITHOUT u2net's looser edge expanding the silhouette outward into the
     background (that expansion is what creates a white halo).
  2. Fill small interior holes (finger gaps, the strip between wrist and
     cuff) so they get an honest alpha = 255.
  3. (--refine) Optionally rebuild the alpha with closed-form matting from a
     trimap. Useful when the base mask is hard-edged (only 0 / 255).
  4. DECONTAMINATE: estimate the true foreground colour for every pixel and
     write THAT as the RGB, so no background colour is left baked into the
     edge. This is what removes the halo.
  5. Save RGBA.

Input situations:
  * Fresh generation — opaque, drawn on a white / near-white background.
    The normal case; just point the tool at it.
  * An already-cut PNG that still shows a halo — the tool detects the
    existing transparency, re-flattens it over white (reconstructing the
    original background), and runs the full pipeline so the edge is rebuilt
    correctly. (Override the assumed background with --bg-color.)
  * A natively-transparent image (gpt-image-1 background=transparent, or
    LayerDiffuse) — pass --decontaminate-only to KEEP the model's alpha and
    just scrub any residual edge colour, instead of re-segmenting.

Usage:
    # one image, in place
    uv run tools/cutout.py portrait.png

    # explicit output, keep the source untouched
    uv run tools/cutout.py raw.png -o cutout.png

    # a whole pack's character art, rescuing out-stretched limbs
    uv run tools/cutout.py --hybrid "assets/characters/**/*.png"

    # gentle pass for natively-transparent art: keep alpha, scrub edges
    uv run tools/cutout.py --decontaminate-only sprite.png

    # non-portrait subject (props / UI): use a general matting model
    uv run tools/cutout.py --model isnet-general-use icon.png
"""
from __future__ import annotations

import argparse
import glob as globlib
import sys
from pathlib import Path

DEFAULT_MODEL = "birefnet-portrait"
# rembg downloads each model once into ~/.u2net on first use.
KNOWN_MODELS = (
    "birefnet-portrait", "birefnet-general", "u2net", "u2netp",
    "isnet-general-use", "isnet-anime", "silueta",
)

_sessions: dict[str, object] = {}


def _session(model: str):
    from rembg import new_session
    if model not in _sessions:
        _sessions[model] = new_session(model)
    return _sessions[model]


def _model_alpha(rgb_img, model: str, *, post_process: bool):
    """Run rembg with a model on an RGB PIL image; return uint8 alpha (H,W)."""
    import numpy as np
    from rembg import remove
    out = remove(rgb_img, session=_session(model), post_process_mask=post_process)
    return np.array(out)[:, :, 3]


def _fill_interior_holes(alpha, max_area: int):
    """Set small fully-enclosed background pockets back to opaque.

    The real background is the connected component that touches the image
    border; anything else below the alpha threshold and smaller than
    max_area is an interior hole we want to fill (skin between fingers,
    the gap between a wrist and a cuff, ...). max_area == 0 fills every
    interior hole regardless of size.
    """
    import numpy as np
    from scipy import ndimage

    bg = alpha < 128
    labelled, n = ndimage.label(bg)
    if n == 0:
        return alpha
    h, w = bg.shape
    border = np.zeros_like(bg, dtype=bool)
    border[0, :] = border[-1, :] = border[:, 0] = border[:, -1] = True
    border_labels = set(labelled[border].tolist()) - {0}
    sizes = ndimage.sum(bg, labelled, range(1, n + 1))
    for lbl in range(1, n + 1):
        if lbl in border_labels:
            continue
        if max_area and sizes[lbl - 1] > max_area:
            continue
        alpha[labelled == lbl] = 255
    return alpha


def _refine_alpha_cf(rgb01, alpha, *, band_px: int):
    """Closed-form matting refinement.

    Treat a band of ``band_px`` pixels on either side of the silhouette
    edge as 'unknown' in a trimap, then solve for a soft alpha there. This
    turns a hard 0/255 mask into a properly anti-aliased edge.
    """
    import numpy as np
    from scipy.ndimage import binary_erosion, binary_dilation
    from pymatting import estimate_alpha_cf

    fg = alpha >= 128
    inner = binary_erosion(fg, iterations=band_px)
    outer = binary_dilation(fg, iterations=band_px)
    trimap = np.full(alpha.shape, 0.5, dtype=np.float64)
    trimap[inner] = 1.0
    trimap[~outer] = 0.0
    return np.clip(estimate_alpha_cf(rgb01, trimap), 0.0, 1.0)


def _detect_bg(rgb_u8, alpha_u8):
    """Estimate the (assumed uniform) background colour and whether it IS
    uniform. Sampled from confident-background pixels (alpha < 10); falls
    back to the image border if the matte left too little background.

    Returns (bg_rgb float[3] in 0-1, is_uniform bool).
    """
    import numpy as np
    bgmask = alpha_u8 < 10
    if int(bgmask.sum()) >= 200:
        px = rgb_u8[bgmask].reshape(-1, 3).astype(np.float64)
    else:
        b = rgb_u8
        px = np.concatenate([b[0], b[-1], b[:, 0], b[:, -1]], axis=0).astype(np.float64)
    med = np.median(px, axis=0)
    # robust spread: mean absolute deviation from the median, per channel
    mad = np.mean(np.abs(px - med), axis=0)
    is_uniform = bool(np.all(mad < 12.0))
    return med / 255.0, is_uniform


def _unmix(rgb01, alpha01, bg01):
    """Exact background unmixing.

    A generated pixel is I = a*F + (1-a)*B. When the background colour B is
    known (the model drew the subject on a flat white / green screen) we can
    recover the true foreground colour directly:  F = (I - (1-a)*B) / a.
    This removes ALL of the baked-in background tint, not just some of it,
    so there is no halo left to composite. Numerically guarded at small a
    (those pixels are nearly transparent anyway).
    """
    import numpy as np
    a = np.clip(alpha01, 0.0, 1.0)[..., None]
    B = np.asarray(bg01, dtype=np.float64).reshape(1, 1, 3)
    F = (rgb01 - (1.0 - a) * B) / np.maximum(a, 1e-3)
    return np.clip(F, 0.0, 1.0)


def _decontaminate_ml(rgb01, alpha01):
    """Blind foreground colour estimation (no known background colour)."""
    import numpy as np
    from pymatting import estimate_foreground_ml
    return np.clip(estimate_foreground_ml(rgb01, alpha01), 0.0, 1.0)


def _is_chroma(bg01) -> bool:
    """True for a saturated key colour (green / blue screen), where spill
    suppression is worth running. White / grey backgrounds are not chroma."""
    import numpy as np
    bg = np.asarray(bg01, dtype=np.float64)
    return float(bg.max() - bg.min()) > 0.25


def _disk(r: int):
    import numpy as np
    yy, xx = np.ogrid[-r:r + 1, -r:r + 1]
    return (xx * xx + yy * yy) <= r * r


def _solidify(rgb, alpha, *, thin_max: int = 6, edge_keep: int = 3,
              cover_thresh: int = 30, fill_thresh: int = 230):
    """Close THIN see-through streaks inside the silhouette (e.g. the gaps
    where a green/white background leaked between hair strands), while leaving
    WIDE intentional negative space (between spikes of tousled hair, between
    an arm and the torso) open, and keeping the soft outer edge intact.

    Returns (rgb, alpha) uint8. Gap pixels get the colour of the nearest
    confident-foreground pixel so the fill reads as hair, not a flat patch.
    """
    import numpy as np
    from scipy import ndimage

    cover = alpha > cover_thresh
    filled = ndimage.binary_fill_holes(cover)
    core = ndimage.binary_erosion(filled, iterations=edge_keep)
    gap = core & (alpha < fill_thresh)
    if not gap.any():
        return rgb, alpha
    # Opening with a disk of radius thin_max keeps only WIDE gaps; subtract
    # those (dilated a touch) to leave the thin streaks we actually want to
    # fill. So tousled-hair spike gaps and arm/torso gaps stay open.
    wide = ndimage.binary_opening(gap, structure=_disk(thin_max))
    thin = gap & ~ndimage.binary_dilation(wide, structure=_disk(2))
    if not thin.any():
        return rgb, alpha
    known = alpha > 200
    if not known.any():
        return rgb, alpha
    idx = ndimage.distance_transform_edt(~known, return_distances=False,
                                         return_indices=True)
    rgb2 = rgb.copy()
    rgb2[thin] = rgb[tuple(i[thin] for i in idx)]
    alpha2 = alpha.copy()
    alpha2[thin] = 255
    return rgb2, alpha2


def _despill(rgb01, bg01):
    """Suppress background-colour spill (e.g. green-screen fringe).

    Unmixing fixes partial-alpha edge pixels, but a chroma background also
    leaks its hue into pixels the matte keeps fully opaque (the green seen
    between hair strands). Cap the background's dominant channel at the level
    of the other two so that tint cannot survive. Neutral / skin / the white
    coat are untouched because their dominant channel is not the key colour.
    """
    import numpy as np
    bg = np.asarray(bg01, dtype=np.float64)
    dom = int(np.argmax(bg))
    others = [i for i in range(3) if i != dom]
    out = rgb01.copy()
    cap = np.maximum(rgb01[:, :, others[0]], rgb01[:, :, others[1]])
    out[:, :, dom] = np.minimum(rgb01[:, :, dom], cap)
    return out


def _apply_decon(rgb_u8, rgb01, alpha01, method: str, bg_color):
    """Dispatch decontamination. Returns (rgb01_clean, label).

    method:
      none  -> leave RGB untouched
      ml    -> blind pymatting foreground estimation
      unmix -> exact unmixing against an explicit/auto background colour
      auto  -> unmix when the background looks uniform, else ml
    """
    import numpy as np
    if method == "none":
        return rgb01, ""
    if method == "ml":
        return _decontaminate_ml(rgb01, alpha01), "decon:ml"
    # unmix / auto need a background colour
    if bg_color is not None:
        bg01 = np.asarray(bg_color, dtype=np.float64) / 255.0
        uniform = True
    else:
        bg01, uniform = _detect_bg(rgb_u8, (np.clip(alpha01, 0, 1) * 255).astype("uint8"))
    if method == "unmix" or (method == "auto" and uniform):
        bg255 = tuple((bg01 * 255).round().astype(int).tolist())
        fg = _unmix(rgb01, alpha01, bg01)
        label = f"decon:unmix{bg255}"
        if _is_chroma(bg01):
            fg = _despill(fg, bg01)
            label += "+despill"
        return fg, label
    return _decontaminate_ml(rgb01, alpha01), "decon:ml"


def cutout(
    src: Path,
    dst: Path,
    *,
    model: str = DEFAULT_MODEL,
    hybrid: bool = False,
    hybrid_erode: int = 3,
    post_process: bool = False,
    hole_fill: bool = True,
    max_hole_area: int = 12000,
    refine: bool = False,
    refine_band: int = 6,
    decon: str = "auto",
    decontaminate_only: bool = False,
    solidify: bool = True,
    solidify_thin_max: int = 6,
    solidify_only: bool = False,
    bg_color: tuple[int, int, int] = (255, 255, 255),
    erode_px: int = 0,
    feather_px: float = 0.0,
    dry_run: bool = False,
) -> str:
    """Cut ``src`` out of its background and write RGBA to ``dst``.

    Returns a short status string describing what was done.
    """
    from PIL import Image, ImageFilter
    import numpy as np

    try:
        img = Image.open(src)
    except Exception as e:  # noqa: BLE001 - report, keep batch going
        return f"error: open: {e}"

    if dry_run:
        mode = ("solidify-only" if solidify_only else
                "decontaminate-only" if decontaminate_only else "segment")
        return f"dry-run ({mode})"

    arr = np.array(img.convert("RGBA"))

    # --- Mode C: keep alpha & colour, only close thin interior streaks. -----
    if solidify_only:
        rgb_u8 = arr[:, :, :3]
        alpha = arr[:, :, 3]
        rgb2, alpha2 = _solidify(rgb_u8, alpha, thin_max=solidify_thin_max)
        Image.fromarray(np.dstack([rgb2, alpha2]), "RGBA").save(dst, "PNG", optimize=True)
        return f"solidified (thin_max={solidify_thin_max})"

    # --- Mode B: keep the existing alpha, only scrub the edge colour. ------
    if decontaminate_only:
        alpha = arr[:, :, 3]
        rgb_u8 = arr[:, :, :3]
        rgb01 = rgb_u8.astype(np.float64) / 255.0
        method = "ml" if decon in ("none", "auto") else decon
        fg01, label = _apply_decon(rgb_u8, rgb01, alpha.astype(np.float64) / 255.0,
                                   method, None)
        out = np.dstack([(fg01 * 255).round().astype(np.uint8), alpha])
        Image.fromarray(out, "RGBA").save(dst, "PNG", optimize=True)
        return f"decontaminated, alpha kept ({label})"

    # --- Mode A: segment from scratch. -------------------------------------
    # If the input already carries transparency, its raw RGB under the
    # transparent region is meaningless to a segmentation model. Re-flatten
    # over the original background colour (white by default) so the model
    # sees the image the way it was generated, then re-cut it cleanly.
    note = ""
    if arr[:, :, 3].min() < 250:
        a = arr[:, :, 3:4].astype(np.float64) / 255.0
        bg = np.array(bg_color, dtype=np.float64).reshape(1, 1, 3)
        flat = arr[:, :, :3].astype(np.float64) * a + bg * (1.0 - a)
        rgb = np.clip(flat, 0, 255).astype(np.uint8)
        note = " [repaired: flattened over bg before re-cut]"
    else:
        rgb = arr[:, :, :3].copy()

    rgb_img = Image.fromarray(rgb, "RGB")

    alpha = _model_alpha(rgb_img, model, post_process=post_process)
    if hybrid and model != "u2net":
        # Rescue out-stretched limbs the portrait model drops — but DON'T let
        # u2net's looser silhouette expand the precise birefnet edge outward
        # into the background (that is exactly what re-introduces a white
        # halo). Erode u2net first so only its confident *interior* is unioned
        # in; the outer edge stays birefnet's.
        from scipy.ndimage import grey_erosion
        au = _model_alpha(rgb_img, "u2net", post_process=post_process)
        if hybrid_erode > 0:
            k = hybrid_erode * 2 + 1
            au = grey_erosion(au, size=(k, k))
        alpha = np.maximum(alpha, au)

    if hole_fill:
        alpha = _fill_interior_holes(alpha, max_hole_area)

    rgb01 = rgb.astype(np.float64) / 255.0

    if refine:
        alpha01 = _refine_alpha_cf(rgb01, alpha, band_px=refine_band)
    else:
        alpha01 = alpha.astype(np.float64) / 255.0

    if erode_px > 0:
        from scipy.ndimage import grey_erosion
        k = erode_px * 2 + 1
        alpha01 = grey_erosion(alpha01, size=(k, k))

    if feather_px > 0:
        a_img = Image.fromarray((np.clip(alpha01, 0, 1) * 255).astype(np.uint8), "L")
        a_img = a_img.filter(ImageFilter.GaussianBlur(radius=feather_px))
        alpha01 = np.array(a_img).astype(np.float64) / 255.0

    fg01, deco_label = _apply_decon(rgb, rgb01, alpha01, decon, None)
    rgb_out = (fg01 * 255).round().astype(np.uint8)
    alpha_out = np.clip(alpha01 * 255.0, 0, 255).round().astype(np.uint8)

    solid_label = ""
    if solidify:
        rgb_out, alpha_out = _solidify(rgb_out, alpha_out,
                                       thin_max=solidify_thin_max)
        solid_label = " +solidify"

    out = np.dstack([rgb_out, alpha_out])
    Image.fromarray(out, "RGBA").save(dst, "PNG", optimize=True)

    bits = [model]
    if hybrid:
        bits.append("∪u2net")
    if refine:
        bits.append("cf-refine")
    suffix = f" {deco_label}" if deco_label else ""
    return f"cutout ({'+'.join(bits)}){suffix}{solid_label}{note}"


def _expand_inputs(patterns: list[str]) -> list[Path]:
    out: list[Path] = []
    for pat in patterns:
        p = Path(pat)
        if p.is_dir():
            out.extend(sorted(p.rglob("*.png")))
        elif any(ch in pat for ch in "*?["):
            out.extend(sorted(Path(m) for m in globlib.glob(pat, recursive=True)))
        else:
            out.append(p)
    # de-dup, keep order
    seen: set[Path] = set()
    uniq: list[Path] = []
    for p in out:
        rp = p.resolve()
        if rp not in seen:
            seen.add(rp)
            uniq.append(p)
    return uniq


def _parse_color(s: str) -> tuple[int, int, int]:
    s = s.strip().lstrip("#")
    if len(s) == 6 and all(c in "0123456789abcdefABCDEF" for c in s):
        return (int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16))
    parts = [int(x) for x in s.replace(",", " ").split()]
    if len(parts) != 3:
        raise argparse.ArgumentTypeError("color must be R,G,B or a hex string")
    return tuple(parts)  # type: ignore[return-value]


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("inputs", nargs="+",
                   help="Image files, directories (recursed for *.png), or "
                        "globs like 'assets/characters/**/*.png'.")
    p.add_argument("-o", "--output", type=Path,
                   help="Output path (only valid with a single input). "
                        "Default: overwrite each input in place.")
    p.add_argument("--model", default=DEFAULT_MODEL,
                   help=f"rembg matting model. Default {DEFAULT_MODEL}. "
                        f"Known: {', '.join(KNOWN_MODELS)}.")
    p.add_argument("--hybrid", action="store_true",
                   help="Also run u2net and union in its eroded interior, to "
                        "rescue out-stretched limbs the portrait model may "
                        "drop. Use only if a limb goes missing — plain "
                        "birefnet has tighter, cleaner edges.")
    p.add_argument("--hybrid-erode", type=int, default=3,
                   help="Px to erode the u2net mask before unioning it in, so "
                        "its loose edge can't expand the silhouette. Default 3.")
    p.add_argument("--post-process", action="store_true",
                   help="Apply rembg's morphological mask cleanup. NOTE: it "
                        "hardens edges toward 0/255, which fights the soft "
                        "matte decontamination needs — leave off unless a "
                        "model gives a speckly mask.")
    p.add_argument("--no-hole-fill", action="store_true",
                   help="Skip filling small interior holes.")
    p.add_argument("--hole-area", type=int, default=12000,
                   help="Max interior-hole size (px) to fill. 0 = fill all. "
                        "Default 12000.")
    p.add_argument("--refine", action="store_true",
                   help="Rebuild the alpha with closed-form matting (good "
                        "for hard 0/255 masks). Slower.")
    p.add_argument("--refine-band", type=int, default=6,
                   help="Trimap unknown-band half-width in px for --refine. "
                        "Default 6.")
    p.add_argument("--decon", choices=["auto", "unmix", "ml", "none"],
                   default="auto",
                   help="Edge colour decontamination — the step that removes "
                        "the white halo. 'unmix' = exact algebraic unmixing "
                        "against the (auto-detected) flat background, best for "
                        "white/green-screen generations. 'ml' = blind "
                        "pymatting foreground estimation, for non-uniform "
                        "backgrounds. 'auto' (default) picks unmix when the "
                        "background looks uniform, else ml. 'none' to skip "
                        "(not recommended).")
    p.add_argument("--no-decontaminate", dest="decon_none",
                   action="store_true", help="Alias for --decon none.")
    p.add_argument("--decontaminate-only", action="store_true",
                   help="Keep the existing alpha; only scrub edge colour. "
                        "Use on natively-transparent art (gpt-image-1 / "
                        "LayerDiffuse).")
    p.add_argument("--no-solidify", action="store_true",
                   help="Skip closing thin see-through streaks inside the "
                        "silhouette (the gaps where bg leaked between hair "
                        "strands). Wide intentional gaps are always kept.")
    p.add_argument("--solidify-thin-max", type=int, default=6,
                   help="Max half-width (px) of an interior gap to treat as a "
                        "fill-able thin streak; wider gaps stay open. Default 6.")
    p.add_argument("--solidify-only", action="store_true",
                   help="Only close thin interior streaks on an existing "
                        "cutout (keep its alpha & colour otherwise).")
    p.add_argument("--bg-color", type=_parse_color, default=(255, 255, 255),
                   help="Background colour to reconstruct when repairing an "
                        "already-cut image. 'R,G,B' or hex. Default white.")
    p.add_argument("--erode", type=int, default=0,
                   help="Inward alpha erosion in px before decontamination. "
                        "Default 0 (decontamination usually suffices).")
    p.add_argument("--feather", type=float, default=0.0,
                   help="Gaussian blur radius (px) on the final alpha. "
                        "Default 0.")
    p.add_argument("--dry-run", action="store_true",
                   help="List what would be processed without writing.")
    args = p.parse_args(argv)

    targets = _expand_inputs(args.inputs)
    if not targets:
        print("error: no input images matched", file=sys.stderr)
        return 2
    if args.output and len(targets) != 1:
        print("error: -o/--output requires exactly one input image",
              file=sys.stderr)
        return 2

    decon = "none" if args.decon_none else args.decon
    mode = ("solidify-only" if args.solidify_only else
            "decontaminate-only" if args.decontaminate_only else "segment")
    print(f"cutout: {len(targets)} image(s), mode={mode}, model={args.model}"
          f"{', hybrid' if args.hybrid else ''}, decon={decon}"
          f"{'' if args.no_solidify else ', solidify'}")

    rc = 0
    for t in targets:
        dst = args.output if args.output else t
        status = cutout(
            t, dst,
            model=args.model,
            hybrid=args.hybrid,
            hybrid_erode=args.hybrid_erode,
            post_process=args.post_process,
            hole_fill=not args.no_hole_fill,
            max_hole_area=args.hole_area,
            refine=args.refine,
            refine_band=args.refine_band,
            decon=decon,
            decontaminate_only=args.decontaminate_only,
            solidify=not args.no_solidify,
            solidify_thin_max=args.solidify_thin_max,
            solidify_only=args.solidify_only,
            bg_color=args.bg_color,
            erode_px=args.erode,
            feather_px=args.feather,
            dry_run=args.dry_run,
        )
        if status.startswith("error"):
            rc = 1
        try:
            label = t.relative_to(Path.cwd())
        except ValueError:
            label = t
        print(f"  [{status}] {label}")
    return rc


if __name__ == "__main__":
    sys.exit(main())

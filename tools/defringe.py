#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = ["pillow", "numpy", "scipy"]
# ///
"""Remove the coloured edge halo ("紅紅的 / 紫紫的邊") left on cut-out art.

This is the post-cutout companion to ``cutout.py``. After a green chroma-key
cutout, ``cutout.py``'s analytic green *unmix* OVERSHOOTS along dark-hair /
fine edges toward MAGENTA (green's complement) and sometimes plain RED, so
even a perfectly keyed image keeps a faint purple/red silhouette rim. And
when the matte left noisy alpha over the key, the transparent region keeps a
rainbow-speckle RGB that bleeds at the edge. ``cutout.py``'s built-in despill
is single-channel (it only suppresses one key colour, e.g. green/blue) and
does NOT clear a TWO-channel magenta cast — so the rim survives. That rim is
the "邊邊" people keep seeing on close zoom even though thumbnails look fine.

This tool fixes it in two content-agnostic stages, WITHOUT ever touching the
alpha channel (silhouette and feather are preserved exactly):

  1. FLOOD — every non-opaque pixel's RGB is replaced with the RGB of the
     nearest fully-opaque (true-foreground) pixel. This removes the keyed /
     speckled colour baked into transparent + anti-aliased edge pixels, so an
     edge blends clean foreground × background with no third colour. Robust to
     ANY key colour and to rainbow speckle.

  2. EDGE-BAND CHROMA DESPILL — within a thin band of the silhouette edge
     only, pull magenta and red contamination back toward neutral:
       * magenta / purple   (R>G+t and B>G+t)         -> cap R,B to G+cap
       * red on a neutral base (R>G+t, |G-B|<neutral)  -> cap R   to max(G,B)+cap
     Restricting this to the edge band PROTECTS the interior: intentionally
     violet eyes, purple hair sheen / hair-ties, lips and warm skin (warm skin
     has G clearly above B, so it never matches the magenta test) are left
     untouched.

The defaults were tuned on the "清華異聞錄" portrait set (green-key + unmix
residual). Run ``--dry-run`` first to see the per-image residual it would
clear.

Usage:
    uv run tools/defringe.py assets/characters/**/*.png        # in place
    uv run tools/defringe.py portrait.png -o cleaned.png
    uv run tools/defringe.py assets/characters --dry-run        # report only
    uv run tools/defringe.py p.png --band 8 --threshold 8       # stronger
    uv run tools/defringe.py p.png --no-flood                   # despill only
"""
from __future__ import annotations

import argparse
import glob as globlib
import sys
from pathlib import Path

import numpy as np
from PIL import Image
from scipy import ndimage


def _residual_magenta(rgba: np.ndarray, t: int = 10) -> int:
    """Count visible magenta-ish pixels (the halo metric)."""
    a = rgba.astype(np.int32)
    R, G, B, al = a[..., 0], a[..., 1], a[..., 2], a[..., 3]
    return int((((R > G + t) & (B > G + t)) & (al > 10)).sum())


def _disk(r: int) -> np.ndarray:
    y, x = np.ogrid[-r:r + 1, -r:r + 1]
    return (x * x + y * y) <= r * r


def clean_edge_alpha(
    al: np.ndarray,
    *,
    core_t: int = 140,
    open_r: int = 1,
    feather: float = 1.4,
    min_area: int = 600,
) -> np.ndarray:
    """Rebuild a crisp anti-aliased alpha from a noisy matte.

    A noisy cutout leaves scattered low-alpha 'dust' specks and a ragged,
    fuzzy feather around the silhouette — which reads as a weird blurry edge
    over a dark game background. This takes the confident solid core
    (alpha >= core_t), morphologically OPENS it to eat thin crumbs / fuzz
    protrusions, drops small DETACHED components (floating dust) while keeping
    any large legitimate separate element, fills interior holes, then lays a
    clean ~feather-px AA edge from the cleaned boundary. Held props, glasses,
    long hair and detached-but-sizable parts survive; the dust does not.
    """
    al = al.astype(np.float32)
    solid = al >= core_t
    if open_r > 0:
        solid = ndimage.binary_opening(solid, _disk(open_r))
    solid = ndimage.binary_fill_holes(solid)
    lab, n = ndimage.label(solid)
    if n > 1:
        sizes = ndimage.sum(np.ones_like(lab), lab, range(1, n + 1))
        keep = np.where(sizes >= min_area)[0] + 1
        solid[~np.isin(lab, keep)] = False
    if not solid.any():
        return al.astype(np.uint8)  # degenerate; leave original
    dist_out = ndimage.distance_transform_edt(~solid)
    a = np.clip(1.0 - dist_out / max(feather, 1e-3), 0, 1)
    a = np.maximum(a, solid.astype(np.float32))
    return (a * 255).astype(np.uint8)


def defringe(
    rgba: np.ndarray,
    *,
    opaque_t: int = 250,
    band: int = 6,
    threshold: int = 10,
    cap: int = 6,
    neutral: int = 18,
    flood: bool = True,
    clean_edge: bool = True,
    core_t: int = 140,
    open_r: int = 1,
    feather: float = 1.4,
    min_area: int = 600,
) -> np.ndarray:
    """Return a defringed copy of an RGBA uint8 array.

    Stages: (1) flood non-opaque RGB from nearest true-foreground;
    (2) optionally rebuild a crisp alpha to remove dust + fuzzy feather;
    (3) edge-band magenta/red chroma despill. With clean_edge=False the alpha
    is preserved exactly (RGB-only defringe).
    """
    a = rgba.astype(np.float32)
    rgb = a[..., :3]
    al = a[..., 3]
    opaque = al >= opaque_t

    if not opaque.any():
        return rgba  # nothing trustworthy to flood from; leave as-is

    if flood:
        # Stage 1: flood non-opaque RGB from the nearest opaque pixel.
        idx = ndimage.distance_transform_edt(
            ~opaque, return_distances=False, return_indices=True
        )
        flooded = rgb[tuple(idx)]
        rgb = np.where(opaque[..., None], rgb, flooded)

    # Stage 2: rebuild a clean alpha (kills dust + ragged fuzzy feather).
    if clean_edge:
        al = clean_edge_alpha(
            al, core_t=core_t, open_r=open_r, feather=feather, min_area=min_area
        ).astype(np.float32)
        opaque = al >= opaque_t

    # Stage 3: edge-band chroma despill (using the final alpha).
    dist = ndimage.distance_transform_edt(opaque)  # depth into opaque from edge
    edgeband = (~opaque) | (opaque & (dist <= band))
    R, G, B = rgb[..., 0], rgb[..., 1], rgb[..., 2]

    mag = edgeband & (R > G + threshold) & (B > G + threshold)
    red = (
        edgeband
        & (R > G + threshold)
        & (np.abs(G - B) < neutral)
        & (R > B + threshold)
    )

    R2, B2 = R.copy(), B.copy()
    R2[mag] = np.minimum(R[mag], G[mag] + cap)
    B2[mag] = np.minimum(B[mag], G[mag] + cap)
    cap_r = np.maximum(G, B) + cap
    R2[red] = np.minimum(R2[red], cap_r[red])

    out_rgb = np.clip(np.dstack([R2, G, B2]), 0, 255)
    return np.dstack([out_rgb, al]).astype(np.uint8)


def _iter_inputs(inputs: list[str]) -> list[Path]:
    out: list[Path] = []
    for raw in inputs:
        p = Path(raw)
        if p.is_dir():
            out.extend(sorted(p.rglob("*.png")))
        elif any(ch in raw for ch in "*?["):
            out.extend(sorted(Path(m) for m in globlib.glob(raw, recursive=True)))
        elif p.exists():
            out.append(p)
        else:
            print(f"  ! not found: {raw}", file=sys.stderr)
    # de-dup, keep order
    seen: set[Path] = set()
    uniq: list[Path] = []
    for p in out:
        if p not in seen:
            seen.add(p)
            uniq.append(p)
    return uniq


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("inputs", nargs="+",
                   help="PNG files, directories (recursed), or globs like "
                        "'assets/characters/**/*.png'.")
    p.add_argument("-o", "--output", type=Path,
                   help="Output path (single input only). Default: overwrite in place.")
    p.add_argument("--opaque-t", type=int, default=250,
                   help="Alpha >= this is trusted foreground. Default 250.")
    p.add_argument("--band", type=int, default=6,
                   help="Despill only within this many px of the silhouette edge. "
                        "Default 6 (protects interior violet/eyes/sheen).")
    p.add_argument("--threshold", type=int, default=10,
                   help="Channel margin to call a pixel magenta/red. Default 10. "
                        "Lower = more aggressive.")
    p.add_argument("--cap", type=int, default=6,
                   help="How far R/B may stay above the neutral channel. Default 6.")
    p.add_argument("--neutral", type=int, default=18,
                   help="|G-B| below this counts as a neutral (dark-hair) base for "
                        "the red test; protects warm skin. Default 18.")
    p.add_argument("--no-flood", dest="flood", action="store_false",
                   help="Skip the nearest-opaque flood; only run the edge despill.")
    p.add_argument("--no-clean-edge", dest="clean_edge", action="store_false",
                   help="Preserve the original alpha exactly (RGB-only defringe). "
                        "By default the alpha is rebuilt crisp to remove dust + the "
                        "ragged fuzzy feather.")
    p.add_argument("--core-t", type=int, default=140,
                   help="Alpha >= this is the confident solid core for the alpha "
                        "rebuild. Default 140.")
    p.add_argument("--open-r", type=int, default=1,
                   help="Morphological-open radius to eat thin edge crumbs. "
                        "Default 1 (raise to 2 for fuzzier mattes).")
    p.add_argument("--feather", type=float, default=1.4,
                   help="Anti-alias width (px) of the rebuilt edge. Default 1.4.")
    p.add_argument("--min-area", type=int, default=600,
                   help="Detached components smaller than this (px) are dropped as "
                        "dust; larger detached parts are kept. Default 600.")
    p.add_argument("--dry-run", action="store_true",
                   help="Report the magenta residual before/after; write nothing.")
    args = p.parse_args(argv)

    files = _iter_inputs(args.inputs)
    if not files:
        print("No input images found.", file=sys.stderr)
        return 1
    if args.output and len(files) != 1:
        print("-o/--output only works with a single input image.", file=sys.stderr)
        return 1

    print(f"defringe: {len(files)} image(s), band={args.band}, "
          f"threshold={args.threshold}, cap={args.cap}, flood={args.flood}"
          + (" [DRY RUN]" if args.dry_run else ""))
    touched = 0
    for f in files:
        im = Image.open(f).convert("RGBA")
        rgba = np.asarray(im)
        before = _residual_magenta(rgba)
        out = defringe(
            rgba,
            opaque_t=args.opaque_t,
            band=args.band,
            threshold=args.threshold,
            cap=args.cap,
            neutral=args.neutral,
            flood=args.flood,
            clean_edge=args.clean_edge,
            core_t=args.core_t,
            open_r=args.open_r,
            feather=args.feather,
            min_area=args.min_area,
        )
        after = _residual_magenta(out)
        cleared = before - after
        tag = "" if cleared <= 0 else f"  (-{cleared} magenta px)"
        print(f"  {f}  fringe {before} -> {after}{tag}")
        if not args.dry_run and (cleared > 0 or args.flood or args.clean_edge):
            dst = args.output or f
            Image.fromarray(out, "RGBA").save(dst)
            touched += 1
    if not args.dry_run:
        print(f"Done. Wrote {touched} image(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

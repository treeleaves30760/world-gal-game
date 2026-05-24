#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = ["pillow", "numpy", "scipy", "opencv-python-headless"]
# ///
"""Repair baked-in "transparency checkerboard" blotches inside a portrait.

When an image model is asked for a transparent background in PROSE (rather
than through a real alpha API), it sometimes *draws* a grey/white
transparency-checkerboard pattern in the gaps it considers background — e.g.
between hair strands. After cut-out that pattern is left baked into the RGB
as an opaque, light, blank-looking patch sitting inside the silhouette.
Neither alpha matting nor colour decontamination removes it, because the
pixels are fully opaque foreground as far as the matte is concerned.

This tool finds those patches by their unmistakable signature — DESATURATED
(grey) + BRIGHT + HIGH-FREQUENCY texture, surrounded by DARK hair — and
inpaints them from the surrounding hair. The signature is deliberately
narrow so it does NOT touch:
  * the face / skin (warm, saturated, smooth),
  * light clothing (large, not ringed by dark hair),
  * soft hair sheen (low frequency).

ALWAYS preview first — this is a heuristic and art varies:
    uv run tools/fix_checker.py --preview "assets/characters/**/*.png"
The preview writes <name>.checkmask.png next to each input with the detected
region tinted red. Eyeball them, then run for real:
    uv run tools/fix_checker.py "assets/characters/qingyi/worried.png"
    uv run tools/fix_checker.py "assets/characters/**/*.png"   # batch

Tunables (raise to detect less, lower to detect more):
    --min-blob 2500     ignore detected blobs smaller than this (px)
    --ring-dark 120     a blob is kept only if the median surrounding pixel's
                        min-channel is below this (i.e. it sits in dark hair)
    --sat-max 30        max (max-min) channel spread to count as "grey"
    --bright-min 125    min channel value to count as "light"
    --freq-min 16       min local std (5px window) to count as "textured"
"""
from __future__ import annotations

import argparse
import glob as globlib
import sys
from pathlib import Path


def _disk(r: int):
    import numpy as np
    yy, xx = np.ogrid[-r:r + 1, -r:r + 1]
    return (xx * xx + yy * yy) <= r * r


def detect_checker(rgb, alpha, *, sat_max: int, bright_min: int,
                   freq_min: float, min_blob: int, ring_dark: int):
    """Return a boolean mask of baked-in checkerboard / bg blotches to fix."""
    import numpy as np
    from scipy import ndimage

    rgbf = rgb.astype(np.float64)
    fg = alpha >= 128
    interior = ndimage.binary_erosion(fg, iterations=4)

    mx = rgbf.max(2)
    mn = rgbf.min(2)
    g = rgbf.mean(2)
    grey = (mx - mn) < sat_max          # desaturated (face/skin is warmer)
    bright = mn > bright_min            # light
    mean = ndimage.uniform_filter(g, 5)
    sq = ndimage.uniform_filter(g * g, 5)
    lstd = np.sqrt(np.maximum(sq - mean * mean, 0.0))
    hifreq = lstd > freq_min            # checker = high local std

    checker = interior & grey & bright & hifreq
    # merge the speckled checker squares into solid blobs
    blob = ndimage.binary_closing(checker, structure=np.ones((9, 9)))
    blob = ndimage.binary_fill_holes(blob) & interior

    lab, n = ndimage.label(blob)
    keep = np.zeros_like(blob)
    for lbl in range(1, n + 1):
        comp = lab == lbl
        if int(comp.sum()) < min_blob:
            continue
        ring = ndimage.binary_dilation(comp, structure=_disk(16)) & ~comp & fg
        if int(ring.sum()) < 80:
            continue
        if float(np.median(rgbf[ring].min(1))) < ring_dark:
            keep |= comp
    # cover the anti-aliased fringe around each blob
    if keep.any():
        keep = ndimage.binary_dilation(keep, structure=_disk(2))
    return keep


def fix_file(src: Path, dst: Path, *, preview: bool, params: dict) -> str:
    from PIL import Image
    import numpy as np
    import cv2

    try:
        img = Image.open(src).convert("RGBA")
    except Exception as e:  # noqa: BLE001
        return f"error: open: {e}"
    a = np.array(img)
    rgb = a[:, :, :3]
    alpha = a[:, :, 3]

    keep = detect_checker(rgb, alpha, **params)
    px = int(keep.sum())
    if px == 0:
        return "clean (nothing detected)"

    if preview:
        comp = rgb.copy()
        comp[keep] = (255, 0, 0)
        out = np.dstack([comp, alpha])
        pv = src.with_suffix(".checkmask.png")
        Image.fromarray(out, "RGBA").save(pv)
        return f"preview {px}px -> {pv.name}"

    mask = (keep * 255).astype(np.uint8)
    inp = cv2.inpaint(np.ascontiguousarray(rgb), mask, 5, cv2.INPAINT_TELEA)
    alpha_new = np.where(keep, 255, alpha).astype(np.uint8)
    out = np.dstack([inp, alpha_new])
    Image.fromarray(out, "RGBA").save(dst, "PNG", optimize=True)
    return f"inpainted {px}px"


def _expand(patterns: list[str]) -> list[Path]:
    out: list[Path] = []
    for pat in patterns:
        p = Path(pat)
        if p.is_dir():
            out.extend(sorted(p.rglob("*.png")))
        elif any(c in pat for c in "*?["):
            out.extend(sorted(Path(m) for m in globlib.glob(pat, recursive=True)))
        else:
            out.append(p)
    return [p for p in out if ".checkmask." not in p.name]


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("inputs", nargs="+", help="files / dirs / globs")
    p.add_argument("-o", "--output", type=Path,
                   help="output path (single input only); default in place")
    p.add_argument("--preview", action="store_true",
                   help="write <name>.checkmask.png with the detected region "
                        "tinted red instead of editing. ALWAYS do this first.")
    p.add_argument("--min-blob", type=int, default=2500)
    p.add_argument("--ring-dark", type=int, default=120)
    p.add_argument("--sat-max", type=int, default=30)
    p.add_argument("--bright-min", type=int, default=125)
    p.add_argument("--freq-min", type=float, default=16.0)
    args = p.parse_args(argv)

    targets = _expand(args.inputs)
    if not targets:
        print("error: no inputs matched", file=sys.stderr)
        return 2
    if args.output and len(targets) != 1:
        print("error: -o requires exactly one input", file=sys.stderr)
        return 2

    params = dict(sat_max=args.sat_max, bright_min=args.bright_min,
                  freq_min=args.freq_min, min_blob=args.min_blob,
                  ring_dark=args.ring_dark)
    print(f"fix_checker: {len(targets)} file(s), "
          f"{'PREVIEW' if args.preview else 'APPLY'} "
          f"(min_blob={args.min_blob} ring_dark={args.ring_dark})")
    rc = 0
    for t in targets:
        dst = args.output if args.output else t
        status = fix_file(t, dst, preview=args.preview, params=params)
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

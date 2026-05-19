"""asset_studio — thin asset-pipeline utilities for pack authors / AI tools.

Three goals:

- **placeholder_image** — generate a coloured PNG with a label, suitable
  for use as a temporary background / portrait / CG while real art is
  being commissioned. Engines that load missing assets already render
  a purple placeholder; this module gives you a *physical* file you can
  drop into ``games/<pack>/assets/...`` so paths resolve cleanly.
- **resize** — scale an image to fit inside ``max_dim`` (longest edge),
  preserving aspect ratio. Common during asset prep.
- **convert** — change a file's format (PNG ↔ JPG ↔ BMP).

Everything uses pygame-ce, which is already a hard dependency, so no
extra installs.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path

# Lazy import: importing pygame at module load forces a video init even
# in headless test runs. We touch it only inside the functions.


def _ensure_dummy_driver() -> None:
    """Force a headless SDL driver for image utilities."""
    os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
    os.environ.setdefault("SDL_AUDIODRIVER", "dummy")


def _color_for_label(label: str) -> tuple[int, int, int]:
    """Stable colour derived from the label string — same label → same hue."""
    h = hashlib.md5(label.encode("utf-8")).digest()
    r = 90 + (h[0] % 120)
    g = 90 + (h[1] % 120)
    b = 90 + (h[2] % 120)
    return r, g, b


def placeholder_image(
    *, size: tuple[int, int] = (640, 360),
    label: str = "PLACEHOLDER",
    path: Path | str,
    border: int = 4,
    text_color: tuple[int, int, int] = (255, 255, 255),
) -> Path:
    """Generate a coloured placeholder PNG with ``label`` centred on it.

    ``size`` is (width, height). The function returns the resolved path
    so callers can chain ``ed.add_npc({..., "portrait": str(p)})`` etc.
    """
    _ensure_dummy_driver()
    import pygame
    pygame.init()
    pygame.font.init()
    out_path = Path(path).resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    # Plain Surface (no .convert()): convert() requires display.set_mode,
    # which doesn't exist under SDL_VIDEODRIVER=dummy. Saving works fine
    # off a non-converted surface.
    surf = pygame.Surface(size)
    bg = _color_for_label(label)
    surf.fill(bg)
    # Border
    if border > 0:
        pygame.draw.rect(surf, (0, 0, 0), surf.get_rect(), border)
    # Centred label. Fall back to default font (works without CJK fonts).
    try:
        font_size = max(14, min(size[0] // 12, size[1] // 4, 56))
        font = pygame.font.Font(None, font_size)
        text = font.render(label, True, text_color)
        rect = text.get_rect(center=(size[0] // 2, size[1] // 2))
        surf.blit(text, rect)
    except Exception:
        # Without a usable font we still ship a coloured rectangle.
        pass
    pygame.image.save(surf, str(out_path))
    pygame.quit()
    return out_path


def resize(*, src: Path | str, dst: Path | str,
           max_dim: int, smooth: bool = True) -> Path:
    """Resize ``src`` so its longest edge equals ``max_dim``.

    Aspect ratio is preserved. Result is written to ``dst`` (which may
    be the same path as ``src`` for in-place rewrite). Returns ``dst``.
    """
    _ensure_dummy_driver()
    import pygame
    pygame.init()
    src_path = Path(src).resolve()
    dst_path = Path(dst).resolve()
    if not src_path.is_file():
        raise FileNotFoundError(src_path)
    surf = pygame.image.load(str(src_path))
    w, h = surf.get_size()
    if max(w, h) <= max_dim:
        # No scaling needed; just copy.
        import shutil
        if src_path != dst_path:
            shutil.copyfile(src_path, dst_path)
        pygame.quit()
        return dst_path
    scale = max_dim / max(w, h)
    new_size = (int(w * scale), int(h * scale))
    if smooth:
        scaled = pygame.transform.smoothscale(surf, new_size)
    else:
        scaled = pygame.transform.scale(surf, new_size)
    dst_path.parent.mkdir(parents=True, exist_ok=True)
    pygame.image.save(scaled, str(dst_path))
    pygame.quit()
    return dst_path


def convert(*, src: Path | str, dst: Path | str) -> Path:
    """Re-save ``src`` under ``dst`` with whatever format ``dst.suffix``
    indicates (``.png`` / ``.jpg`` / ``.bmp``)."""
    _ensure_dummy_driver()
    import pygame
    pygame.init()
    src_path = Path(src).resolve()
    dst_path = Path(dst).resolve()
    if not src_path.is_file():
        raise FileNotFoundError(src_path)
    surf = pygame.image.load(str(src_path))
    dst_path.parent.mkdir(parents=True, exist_ok=True)
    pygame.image.save(surf, str(dst_path))
    pygame.quit()
    return dst_path


def stock_placeholder_pack(pack_root: Path | str,
                            *, overwrite: bool = False) -> list[Path]:
    """Drop placeholder PNGs into the standard pack asset paths.

    Useful when an AI scaffolds a new pack and wants every asset path
    resolved (so the engine renders real files, not its built-in
    purple stand-ins). Returns the list of files actually written.
    """
    pack = Path(pack_root).resolve()
    out: list[Path] = []
    plan: list[tuple[str, tuple[int, int], str]] = [
        ("assets/backgrounds/title.png", (1280, 720), "Title BG"),
        ("assets/backgrounds/starting_room.png", (1280, 720), "Starting Room"),
        ("assets/cgs/example.png", (1280, 720), "CG"),
        ("assets/ui/default.png", (640, 360), "UI"),
    ]
    for rel, size, label in plan:
        target = pack / rel
        if target.is_file() and not overwrite:
            continue
        placeholder_image(size=size, label=label, path=target)
        out.append(target)
    return out

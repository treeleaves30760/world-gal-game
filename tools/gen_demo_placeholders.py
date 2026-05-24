"""Generate tasteful placeholder art for demo_pack.

demo_pack ships with no art (only .gitkeep), so the engine falls back to its
striped debug placeholder. That's fine for layout work but unconvincing in
screenshots. This script writes gradient location backgrounds and simple
character portraits at the exact paths the pack's YAML already references, so
the destination picker / exploration screens render real images.

Run:  uv run python tools/gen_demo_placeholders.py

Idempotent: re-running overwrites. These are *placeholders*, not final art —
swap in real assets at the same paths whenever they exist.
"""
from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

import pygame

PACK = Path(__file__).resolve().parent.parent / "games" / "demo_pack"
BG_DIR = PACK / "assets" / "backgrounds"
CH_DIR = PACK / "assets" / "characters"

_FONT_CANDIDATES = ("PingFang TC", "Heiti TC", "Apple LiGothic",
                    "Microsoft JhengHei", "Noto Sans CJK TC",
                    "Arial Unicode MS", "Arial")


def _font(px: int, bold: bool = False) -> pygame.font.Font:
    for name in _FONT_CANDIDATES:
        path = pygame.font.match_font(name, bold=bold)
        if path:
            f = pygame.font.Font(path, px)
            f.set_bold(bold)
            return f
    f = pygame.font.Font(None, px)
    f.set_bold(bold)
    return f


def _hsv(h: float, s: float, v: float) -> tuple[int, int, int]:
    c = pygame.Color(0, 0, 0)
    c.hsva = (h % 360, max(0.0, min(100.0, s)), max(0.0, min(100.0, v)), 100)
    return (c.r, c.g, c.b)


def _vgradient(size: tuple[int, int], top: tuple, bottom: tuple) -> pygame.Surface:
    """Smooth vertical gradient via a 1x2 surface upscaled (fast + clean)."""
    seed = pygame.Surface((1, 2))
    seed.set_at((0, 0), top)
    seed.set_at((0, 1), bottom)
    return pygame.transform.smoothscale(seed, size)


def make_background(path: Path, label: str, hue: float,
                    *, top_v: float = 52, bot_v: float = 22,
                    sat: float = 45) -> None:
    w, h = 1920, 1080
    surf = _vgradient((w, h), _hsv(hue, sat, top_v), _hsv(hue + 12, sat + 8, bot_v))
    # A couple of soft light blobs for depth.
    for (bx, by, br, bv) in ((int(w * 0.30), int(h * 0.32), 360, top_v + 16),
                             (int(w * 0.74), int(h * 0.55), 460, top_v + 6)):
        blob = pygame.Surface((br * 2, br * 2), pygame.SRCALPHA)
        pygame.draw.circle(blob, (*_hsv(hue - 18, sat, bv), 46), (br, br), br)
        surf.blit(blob, (bx - br, by - br))
    # Bottom darkening band (alpha gradient) so a name reads cleanly.
    band = pygame.Surface((w, h // 3), pygame.SRCALPHA)
    for y in range(band.get_height()):
        a = int(150 * (y / band.get_height()))
        pygame.draw.line(band, (8, 6, 16, a), (0, y), (w, y))
    surf.blit(band, (0, h - h // 3))
    # Large faint watermark + crisp label.
    big = _font(150, bold=True).render(label, True, (255, 255, 255))
    big.set_alpha(26)
    surf.blit(big, ((w - big.get_width()) // 2, (h - big.get_height()) // 2))
    name = _font(58, bold=True).render(label, True, (245, 238, 255))
    surf.blit(name, (64, h - 130))
    tag = _font(26).render("placeholder", True, (200, 190, 220))
    tag.set_alpha(150)
    surf.blit(tag, (66, h - 60))
    path.parent.mkdir(parents=True, exist_ok=True)
    pygame.image.save(surf, str(path))


def make_portrait(path: Path, name: str, hue: float,
                  *, accent_shift: float = 0.0, v: float = 60) -> None:
    w, h = 512, 768
    surf = _vgradient((w, h), _hsv(hue, 40, v), _hsv(hue + 14, 52, v - 28))
    skin = _hsv(28, 30, 92)
    hair = _hsv(hue + accent_shift, 55, 70)
    cx = w // 2
    # Shoulders.
    pygame.draw.ellipse(surf, hair, (cx - 190, h - 250, 380, 420))
    pygame.draw.ellipse(surf, skin, (cx - 120, h - 230, 240, 300))
    # Head.
    pygame.draw.circle(surf, skin, (cx, int(h * 0.42)), 120)
    # Hair cap.
    hair_cap = pygame.Surface((300, 220), pygame.SRCALPHA)
    pygame.draw.ellipse(hair_cap, hair, (0, 0, 300, 220))
    surf.blit(hair_cap, (cx - 150, int(h * 0.42) - 150))
    # Name band.
    band = pygame.Surface((w, 96), pygame.SRCALPHA)
    band.fill((8, 6, 16, 170))
    surf.blit(band, (0, h - 96))
    label = _font(46, bold=True).render(name, True, (245, 238, 255))
    surf.blit(label, ((w - label.get_width()) // 2, h - 76))
    path.parent.mkdir(parents=True, exist_ok=True)
    pygame.image.save(surf, str(path))


def main() -> None:
    pygame.init()
    pygame.font.init()

    # Location backgrounds (hue per place; starting_room gets time variants).
    make_background(BG_DIR / "starting_room.png", "出租房間", 268, top_v=44, bot_v=18)
    make_background(BG_DIR / "starting_room_morning.png", "出租房間", 38,
                    top_v=64, bot_v=34, sat=38)
    make_background(BG_DIR / "starting_room_night.png", "出租房間", 250,
                    top_v=30, bot_v=10, sat=40)
    make_background(BG_DIR / "town_square.png", "鎮中廣場", 30, top_v=56, bot_v=26)
    make_background(BG_DIR / "park.png", "湖畔公園", 150, top_v=52, bot_v=24)
    make_background(BG_DIR / "shop_alley.png", "雜貨鋪小巷", 205, top_v=46, bot_v=20)

    # Character portraits.
    make_portrait(CH_DIR / "heroine_1_normal.png", "林清雪", 330)
    make_portrait(CH_DIR / "heroine_1_smile.png", "林清雪", 330, accent_shift=8, v=66)
    make_portrait(CH_DIR / "heroine_1_shy.png", "林清雪", 345, accent_shift=-6, v=58)
    make_portrait(CH_DIR / "heroine_1_sad.png", "林清雪", 320, accent_shift=-12, v=50)
    make_portrait(CH_DIR / "shopkeeper.png", "雜貨鋪老闆", 25, v=54)

    pygame.quit()
    made = sorted(BG_DIR.glob("*.png")) + sorted(CH_DIR.glob("*.png"))
    print(f"generated {len(made)} placeholder images:")
    for p in made:
        print("  ", p.relative_to(PACK))


if __name__ == "__main__":
    main()

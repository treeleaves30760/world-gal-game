"""Asset manager: lazy-loads + caches images & sounds.

All asset paths are resolved through engine.config.resolve_asset so they
work the same in source and in a PyInstaller bundle. Missing assets fall
back to a procedurally drawn placeholder so the game keeps running while
art is still being generated.

Path conventions (in order of precedence):

1. Absolute path → used as-is.
2. Path starting with ``assets/`` (or ``./assets/``) → resolved against the
   *pack* root (set via :meth:`AssetManager.set_pack_root`). This is the
   recommended form for new packs because it keeps the pack relocatable.
3. Anything else → resolved against the project / resource root, falling
   back to legacy paths like ``games/<pack>/assets/...`` so old content
   continues to work without modification.
"""
from __future__ import annotations

from pathlib import Path

import pygame

from ..config import resolve_asset


class AssetManager:
    def __init__(self, pack_root: Path | None = None):
        self._images: dict[str, pygame.Surface] = {}
        self._image_versions: dict[str, str] = {}
        self._sounds: dict[str, pygame.mixer.Sound] = {}
        self._music_paths: dict[str, str] = {}
        self._current_music: str | None = None
        self._placeholder_cache: dict[tuple[int, int, tuple], pygame.Surface] = {}
        self._pack_root: Path | None = Path(pack_root) if pack_root else None

    def set_pack_root(self, pack_root: Path | None) -> None:
        """Tell the asset manager where to resolve pack-relative paths.

        The path is interpreted as "any time someone references
        ``assets/foo.png``, that means ``<pack_root>/assets/foo.png``".
        Setting this also clears the image cache so old placeholders for
        previously-missing assets don't stick around.
        """
        new_root = Path(pack_root) if pack_root else None
        if new_root != self._pack_root:
            self._pack_root = new_root
            # Clear so any earlier placeholders get re-resolved.
            self._images.clear()

    def _resolve(self, path: str) -> Path | None:
        """Return an absolute filesystem path for the asset, or None."""
        p = Path(path)
        if p.is_absolute():
            return p
        # Pack-relative: "assets/..." or "./assets/..."
        if self._pack_root is not None:
            parts = p.parts
            if parts and parts[0] in ("assets", "./assets"):
                return (self._pack_root / p).resolve()
            # Or "<pack-rel>/..." that happens to live inside the pack
            packed = (self._pack_root / p).resolve()
            if packed.exists():
                return packed
        # Legacy: relative to the resource root.
        return resolve_asset(path)

    # ---------- images -------------------------------------------------------

    def image(self, path: str | None, *, fallback_size: tuple[int, int] | None = None,
              fallback_color: tuple = (40, 30, 60)) -> pygame.Surface:
        """Return a Surface for the given path; cached.

        If the path is None or the file is missing, return a placeholder
        surface (a tinted rectangle so layout work isn't blocked).
        """
        if not path:
            return self._placeholder(fallback_size or (320, 320), fallback_color)
        if path in self._images:
            return self._images[path]
        abs_path = self._resolve(path)
        if abs_path is None or not Path(abs_path).exists():
            surf = self._placeholder(fallback_size or (320, 320), fallback_color,
                                     label=Path(path).name)
            self._images[path] = surf
            return surf
        try:
            surf = pygame.image.load(str(abs_path))
            if surf.get_alpha() is not None:
                surf = surf.convert_alpha()
            else:
                surf = surf.convert()
        except pygame.error:
            surf = self._placeholder(fallback_size or (320, 320), fallback_color,
                                     label=Path(path).name)
        self._images[path] = surf
        return surf

    def scaled(self, path: str | None, size: tuple[int, int],
               *, fit: str = "cover", fallback_color: tuple = (40, 30, 60)) -> pygame.Surface:
        """Return a scaled copy. fit = 'cover' | 'contain' | 'stretch'."""
        cache_key = f"{path}::{size[0]}x{size[1]}::{fit}"
        if cache_key in self._images:
            return self._images[cache_key]
        base = self.image(path, fallback_size=size, fallback_color=fallback_color)
        if fit == "stretch":
            scaled = pygame.transform.smoothscale(base, size)
        else:
            bw, bh = base.get_size()
            tw, th = size
            if bw == 0 or bh == 0:
                scaled = pygame.transform.smoothscale(base, size)
            else:
                br = bw / bh
                tr = tw / th
                if (fit == "cover") == (br > tr):
                    new_h = th
                    new_w = int(br * new_h)
                else:
                    new_w = tw
                    new_h = int(new_w / br)
                resized = pygame.transform.smoothscale(base, (max(1, new_w), max(1, new_h)))
                if fit == "cover":
                    canvas = pygame.Surface(size, pygame.SRCALPHA)
                    canvas.blit(resized, ((tw - new_w) // 2, (th - new_h) // 2))
                    scaled = canvas
                else:  # contain
                    canvas = pygame.Surface(size, pygame.SRCALPHA)
                    canvas.blit(resized, ((tw - new_w) // 2, (th - new_h) // 2))
                    scaled = canvas
        self._images[cache_key] = scaled
        return scaled

    def _placeholder(self, size: tuple[int, int], color: tuple,
                     label: str = "") -> pygame.Surface:
        key = (size[0], size[1], tuple(color))
        if key in self._placeholder_cache and not label:
            return self._placeholder_cache[key]
        s = pygame.Surface(size, pygame.SRCALPHA)
        s.fill((*color[:3], 200))
        # diagonal stripes so it's obvious this is a placeholder
        for i in range(-size[1], size[0], 20):
            pygame.draw.line(s, (255, 255, 255, 18),
                             (i, 0), (i + size[1], size[1]), 1)
        pygame.draw.rect(s, (255, 255, 255, 70), s.get_rect(), 2,
                         border_radius=8)
        if label:
            font = pygame.font.SysFont(None, 18)
            txt = font.render(label[:24], True, (255, 255, 255, 200))
            s.blit(txt, ((size[0] - txt.get_width()) // 2,
                         (size[1] - txt.get_height()) // 2))
        if not label:
            self._placeholder_cache[key] = s
        return s

    # ---------- sound --------------------------------------------------------

    def sound(self, path: str | None) -> pygame.mixer.Sound | None:
        if not path:
            return None
        if path in self._sounds:
            return self._sounds[path]
        abs_path = self._resolve(path)
        if abs_path is None or not Path(abs_path).exists():
            return None
        try:
            s = pygame.mixer.Sound(str(abs_path))
        except pygame.error:
            return None
        self._sounds[path] = s
        return s

    def play_sound(self, path: str | None, *, volume: float = 1.0) -> None:
        s = self.sound(path)
        if s is None:
            return
        s.set_volume(volume)
        s.play()

    def play_music(self, path: str | None, *, volume: float = 0.6,
                   loops: int = -1, fade_ms: int = 800) -> None:
        if path is None:
            if self._current_music is not None:
                pygame.mixer.music.fadeout(fade_ms)
                self._current_music = None
            return
        if path == self._current_music:
            return
        abs_path = self._resolve(path)
        if abs_path is None or not Path(abs_path).exists():
            return
        try:
            pygame.mixer.music.fadeout(fade_ms)
            pygame.mixer.music.load(str(abs_path))
            pygame.mixer.music.set_volume(volume)
            pygame.mixer.music.play(loops=loops, fade_ms=fade_ms)
            self._current_music = path
        except pygame.error:
            self._current_music = None

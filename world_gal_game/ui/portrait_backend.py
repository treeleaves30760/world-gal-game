"""Portrait render backends — the seam between *which* portrait and *how* it moves.

The dialogue scene resolves a :class:`~world_gal_game.core.portrait_spec.PortraitSpec`
to a still surface and stages it with :class:`~world_gal_game.ui.portrait_anim.SlotAnimation`
for enter / exit / crossfade. A **portrait backend** takes over the *steady-state*
(resting) draw so a portrait can keep moving after it settles — procedural
breathing, sprite-sheet frames, or a native Live2D/Spine rig shipped as a
desktop-only plugin. Transitions stay surface-based regardless of backend.

Backends are plugin-provided and registered by name via
:func:`world_gal_game.plugins.portrait_backend`. The built-in default is
``"static"`` — there is *no* backend instance for it; the scene blits the
resolved still exactly as it always has. An unknown backend name degrades to
that same static blit, so a missing plugin never breaks rendering.

Contract (a backend class, instantiated per slot by the dialogue scene)::

    backend = cls(spec, assets, fallback_size)        # PortraitSpec, AssetManager, (w, h)
    backend.update(dt, **ctx)                         # advance the clock; **ctx carries
                                                      #   signals from the scene
    backend.draw(surface, rect, *, flip=, alpha=)     # render the current frame into rect
    base = backend.base_surface()                     # a resting still for transitions (or None)

``**ctx`` lets the scene feed per-frame signals a backend may use; backends that
don't care ignore them. The one standard key today is ``talking: bool`` (True
while the slot's character is the active speaker and their line is still typing)
— a layered rig uses it to drive lip-sync. More keys can be added without
breaking existing backends.

A backend lives in the UI layer (it blits pygame surfaces) and holds **no game
state**. The scene isolates backend calls so a buggy backend can never crash a
frame, but backends should still be defensive on their own: a missing asset must
degrade to a placeholder (via ``assets.resolve_portrait``), never raise.
"""
from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

import pygame

from .layout import fit_rect


def blit_fitted(surface: pygame.Surface, src: pygame.Surface | None,
                rect: pygame.Rect, *, flip: bool = False,
                alpha: int = 255) -> None:
    """Blit ``src`` fit (aspect-preserving, bottom-anchored) into ``rect``.

    This is the exact geometry the dialogue scene's static-portrait path uses
    (:func:`fit_rect` + ``smoothscale`` + optional flip), factored out so any
    backend's output lines up pixel-for-pixel with the non-animated draw. A
    backend that wants procedural motion passes an *animated* rect (grown /
    nudged each frame); the fit math then keeps the portrait's aspect ratio and
    baseline intact.
    """
    if src is None:
        return
    dest = fit_rect(src.get_size(), rect)
    if dest.width <= 0 or dest.height <= 0:
        return
    img = pygame.transform.smoothscale(src, dest.size)
    if flip:
        img = pygame.transform.flip(img, True, False)
    if alpha < 255:
        # set_alpha mutates the surface; copy so the cached source is untouched.
        img = img.copy()
        img.set_alpha(max(0, min(255, alpha)))
    surface.blit(img, dest.topleft)


@runtime_checkable
class PortraitBackend(Protocol):
    """Structural type a registered backend class instance satisfies.

    Used only for documentation / optional ``isinstance`` checks — the registry
    stores the class and the scene duck-types the three methods, so a backend
    need not import or subclass anything.
    """

    def update(self, dt: float, **ctx: Any) -> None: ...

    def draw(self, surface: pygame.Surface, rect: pygame.Rect, *,
             flip: bool = False, alpha: int = 255) -> None: ...

    def base_surface(self) -> pygame.Surface | None: ...


class StaticBackend:
    """Reference backend: resolve once, blit fitted, never animate.

    Not registered (the engine treats ``"static"`` as "no backend instance"),
    but useful as a base for backends that only tweak the draw, and as the
    behavioural spec the static path matches. Construct as the registry would::

        StaticBackend(spec, assets, fallback_size)
    """

    def __init__(self, spec: Any, assets: Any,
                 fallback_size: tuple[int, int]) -> None:
        self._surf = assets.resolve_portrait(spec, fallback_size=fallback_size)

    def update(self, dt: float, **ctx: Any) -> None:  # noqa: D401 - no-op
        pass

    def base_surface(self) -> pygame.Surface | None:
        return self._surf

    def draw(self, surface: pygame.Surface, rect: pygame.Rect, *,
             flip: bool = False, alpha: int = 255) -> None:
        blit_fitted(surface, self._surf, rect, flip=flip, alpha=alpha)

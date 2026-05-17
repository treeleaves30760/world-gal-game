"""World Gal-Game engine — the pygame-based gal-game framework.

This is the *engine*. A *game* is a content pack (a directory containing
``content/meta.yaml`` + YAML scenes + assets) loaded by the engine at
runtime.

Quickstart for downstream projects:

    # Run a pack from the command line.
    $ world-gal-game --pack /path/to/my-game

    # Or programmatically.
    from world_gal_game import run
    run(pack="/path/to/my-game")

Top-level helpers re-exported here:

- :func:`run`            — start the GUI with a given pack path / name.
- :func:`headless_run`   — drive the engine without a window (returns the
                           resulting :class:`HeadlessSession`).
- :class:`EngineConfig`  — window / FPS / paths configuration.

Everything else lives under the sub-packages: ``core``, ``dialogue``,
``npc``, ``ui``, ``scenes``.
"""

__version__ = "0.1.0"


def run(*, pack: str | None = None,
        fullscreen: bool = False,
        screen_size: tuple[int, int] = (1280, 720),
        fps: int = 60,
        text_speed: float | None = None) -> int:
    """Start the engine GUI with the given pack.

    ``pack`` may be a pack name, a path, or ``None`` to use the engine
    default. Returns 0 on normal exit.
    """
    from .config import EngineConfig
    from .app import GalGameApp
    config = EngineConfig(
        screen_size=screen_size,
        fps=fps,
        fullscreen=fullscreen,
    )
    if text_speed is not None:
        config.text_speed = text_speed
    app_pack = pack or config.default_pack
    config.default_pack = app_pack
    app = GalGameApp(config=config, pack=app_pack, headless=False)
    app.run()
    return 0


def headless_run(*, pack: str | None = None,
                 script: list[dict] | None = None):
    """Open a HeadlessSession and (optionally) run a script of actions.

    Returns the :class:`HeadlessSession` so callers can inspect state.
    """
    from .config import EngineConfig
    from .headless import HeadlessSession
    config = EngineConfig()
    sess = HeadlessSession.open(config, pack=pack)
    if script:
        sess.run_script(script)
    return sess


# Public API — keep the surface small but useful.
from .config import EngineConfig  # noqa: E402

__all__ = [
    "__version__",
    "run",
    "headless_run",
    "EngineConfig",
]

"""Web (pygbag / Emscripten) entry point.

pygbag runs this module's top-level ``asyncio.run(main())`` inside the
browser event loop. The async driver yields once per frame so the WASM tab
never freezes — see :meth:`world_gal_game.app.GalGameApp.run_async`.

This file is a **template**. The desktop build never imports it; the web
build (:func:`world_gal_game.build_web.build_web`) copies it to the staging
root as ``main.py`` and rewrites the ``_PACK`` constant below to the pack
being shipped. The default value keeps the file runnable from a source
checkout (``python -m world_gal_game.web_main``) against the demo pack.

The pack name is substituted by replacing the whole ``_PACK = "..."`` line,
so the marker comment must stay put.
"""
from __future__ import annotations

import asyncio

# wgg:web-pack — build_web rewrites the next line with the shipped pack name.
_PACK = "demo_pack"


async def main() -> None:
    """Construct the app for the bundled pack and run the async loop."""
    # Imports live inside main() so importing this module is cheap and never
    # drags in pygame before pygbag has set the WASM runtime up.
    from .app import GalGameApp
    from .config import EngineConfig

    config = EngineConfig.from_env(default_pack=_PACK)
    app = GalGameApp(config=config, pack=_PACK, headless=False)
    await app.run_async()


if __name__ == "__main__":
    asyncio.run(main())

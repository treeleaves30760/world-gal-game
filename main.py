"""Dev-mode entry point for World Gal-Game.

In an installed environment (``pip install world-gal-game``), use the
``world-gal-game`` console script. This file exists only so that ``python
main.py`` continues to work when running the engine from a source
checkout — both invocations call into the same ``world_gal_game.cli``.
"""
from __future__ import annotations

import sys

from world_gal_game.cli import main

if __name__ == "__main__":
    sys.exit(main())

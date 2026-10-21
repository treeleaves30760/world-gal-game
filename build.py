"""Thin CLI wrapper around world_gal_game.build.build_pack().

Usage:
    python build.py <pack_path> [options]

Run ``python build.py --help`` for the full option list. For the registered
console entry point, use ``world-gal-game build <pack_path>``.
"""
from __future__ import annotations

import sys
from world_gal_game.build import main

if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

"""Convenience wrapper around PyInstaller.

Usage:
    uv pip install -e .[build]
    uv run python build.py

This will:
- ensure the dependencies are installed,
- clean any previous dist/ build/,
- run pyinstaller against build.spec,
- print where the final executable was written.

On macOS, the result is dist/TsinghuaStrangeTales/TsinghuaStrangeTales.app.
On Windows / Linux, dist/TsinghuaStrangeTales/TsinghuaStrangeTales(.exe).
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent


def main() -> int:
    try:
        import PyInstaller  # noqa: F401
    except ImportError:
        print("pyinstaller not installed. Run: uv pip install pyinstaller",
              file=sys.stderr)
        return 1

    for d in ("dist", "build"):
        p = ROOT / d
        if p.exists():
            print(f"[build] cleaning {p}")
            shutil.rmtree(p)

    cmd = [sys.executable, "-m", "PyInstaller", "build.spec",
           "--clean", "--noconfirm"]
    print("[build] " + " ".join(cmd))
    result = subprocess.run(cmd, cwd=str(ROOT))
    if result.returncode != 0:
        return result.returncode
    target = ROOT / "dist" / "TsinghuaStrangeTales"
    print(f"[build] done -> {target}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

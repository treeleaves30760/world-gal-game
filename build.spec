# PyInstaller spec for World Gal-Game.
#
# Usage:
#   uv pip install pyinstaller
#   pyinstaller build.spec --clean --noconfirm
#
# Outputs a one-folder distribution to dist/TsinghuaStrangeTales/. The
# folder is portable: copy it anywhere and run the executable inside.
# All YAML content, image assets and bundled fonts are pulled in via
# the `datas` list below.

from PyInstaller.utils.hooks import collect_submodules
from pathlib import Path

block_cipher = None
root = Path(SPECPATH)

datas = []

# game packs: ship the whole games/ tree so users get content + assets
games_dir = root / "games"
if games_dir.exists():
    for path in games_dir.rglob("*"):
        if path.is_file():
            rel_parent = path.parent.relative_to(root)
            datas.append((str(path), str(rel_parent)))

# engine UI assets (if/when we add bundled fonts to engine/ui/fonts/)
engine_assets = root / "engine"
for sub in ("ui/fonts",):
    p = engine_assets / sub
    if p.exists():
        for path in p.rglob("*"):
            if path.is_file():
                rel_parent = path.parent.relative_to(root)
                datas.append((str(path), str(rel_parent)))

hiddenimports = []
hiddenimports += collect_submodules("engine")
hiddenimports += collect_submodules("pydantic")
# anthropic is optional at runtime; only include if installed
try:
    hiddenimports += collect_submodules("anthropic")
except Exception:
    pass


a = Analysis(
    ["main.py"],
    pathex=[str(root)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter", "test", "unittest"],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="TsinghuaStrangeTales",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,         # set True if you want to keep a debug console
    icon=None,
    disable_windowed_traceback=False,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="TsinghuaStrangeTales",
)

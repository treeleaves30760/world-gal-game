# Distributing a Game Pack

This guide explains how to package a World Gal-Game pack into a
standalone binary that players can run without installing Python.

---

## What "distribute" means

The build command uses PyInstaller to collect:

- the World Gal-Game engine (Python code + pygame-ce)
- the Python runtime and all transitive dependencies
- the entire contents of your pack directory (YAML + assets)

The result is a single folder that a player can copy anywhere and run
directly.  No Python installation is required on their machine.

---

## Prerequisites

Install the `[build]` extras into your development environment:

```bash
uv pip install -e ".[build]"
```

This installs PyInstaller alongside the engine.  Confirm with:

```bash
pyinstaller --version
```

---

## Simplest possible build

```bash
wgg build /path/to/my-pack
```

Or, if you prefer the direct Python wrapper at the repo root:

```bash
python build.py /path/to/my-pack
```

The engine derives the app name from the `title` field in your pack's
`content/meta.yaml`.  A pack with `title: "小鎮的午後"` produces an app
name of `_` (ASCII-safe characters are kept; non-ASCII are replaced by
`_`).  Override it explicitly with `--name`:

```bash
wgg build /path/to/my-pack --name SmallTownAfternoon
```

---

## Output location

The finished build lands in `dist/<app_name>/` relative to your current
working directory.  Change the destination with `--output`:

```bash
wgg build /path/to/my-pack --output /tmp/releases
# result: /tmp/releases/<app_name>/
```

On macOS the app bundle is `dist/<app_name>/<app_name>.app`.
On Windows the executable is `dist/<app_name>/<app_name>.exe`.
On Linux it is `dist/<app_name>/<app_name>`.

---

## Cross-platform limitations

PyInstaller **cannot cross-compile**.  You must build on the target OS:

| Want to produce | Must build on |
|-----------------|---------------|
| `.exe`          | Windows       |
| `.app`          | macOS         |
| Linux binary    | Linux         |

Passing `--target windows` (or `macos`, `linux`) when you are on a
different OS prints a warning and falls back to a native build.  Use
GitHub Actions (see CI example below) to build all three from one repo.

---

## Adding an icon

```bash
# Windows
wgg build /path/to/my-pack --icon icon.ico

# macOS
wgg build /path/to/my-pack --icon icon.icns
```

PyInstaller accepts `.ico` on Windows and `.icns` on macOS.  Passing the
wrong format for the host OS is silently ignored by PyInstaller.

---

## Single-file mode

Produce a single executable instead of a folder:

```bash
wgg build /path/to/my-pack --onefile
```

Single-file builds are convenient for distribution but start more slowly
because the executable unpacks itself to a temp directory on every launch.
For games with large asset trees the folder mode is preferred.

---

## Assets and YAML paths

All paths in your pack's YAML files should be **relative to the pack
root** (e.g. `assets/backgrounds/title.png`).  The build system copies
the entire pack directory into the binary, preserving this layout, so the
engine resolves them identically at runtime.

Do not use absolute paths or paths that walk above the pack root
(`../../something`).

---

## Save-file location

Player saves are **not** stored inside the binary.  The engine writes
them to the user's application-data directory:

| OS      | Path |
|---------|------|
| macOS   | `~/Library/Application Support/<app_name>/` |
| Windows | `%APPDATA%\<app_name>\` |
| Linux   | `~/.local/share/<app_name>/` |

Pack developers do not need to configure anything; the engine handles
this automatically.

---

## macOS code signing

Pass your Developer ID to sign the output after building:

```bash
wgg build /path/to/my-pack \
    --sign-identity "Developer ID Application: My Company (XXXXXXXXXX)"
```

This runs `codesign --deep --force --sign` on the produced bundle.
Notarisation (required for distribution outside the Mac App Store) is a
separate step and must be done with `xcrun notarytool` — outside the
scope of this command.

---

## Windows code signing

Windows code signing uses Microsoft's `signtool.exe`, which is not
bundled with PyInstaller or this engine.  After building, sign with:

```bat
signtool sign /fd sha256 /tr http://timestamp.digicert.com /td sha256 ^
    /f MyCert.pfx /p MyPassword dist\MyGame\MyGame.exe
```

Refer to Microsoft's documentation for certificate acquisition and
`signtool` usage.

---

## Troubleshooting

**Missing fonts at runtime**

Add font files to `assets/fonts/` inside your pack directory and
reference them by pack-relative path in your YAML.  PyInstaller will
include them automatically.

**Missing images or audio**

Verify that every asset referenced in YAML lives inside the pack
directory.  Files outside the pack root are not bundled.

**The .exe closes immediately on Windows**

Run the binary from a command prompt (`cmd.exe`) so you can see the
error output.  Common causes: a missing DLL (usually resolved by
installing the Visual C++ Redistributable) or a YAML parse error.

**`pyinstaller` is not found**

Ensure you activated the correct virtual environment and ran
`uv pip install -e ".[build]"`.

---

## GitHub Actions CI example

The matrix below builds a binary for each platform on every tagged push:

```yaml
name: Release

on:
  push:
    tags: ["v*"]

jobs:
  build:
    strategy:
      matrix:
        include:
          - os: ubuntu-latest
            target: linux
          - os: windows-latest
            target: windows
          - os: macos-latest
            target: macos

    runs-on: ${{ matrix.os }}

    steps:
      - uses: actions/checkout@v4

      - uses: astral-sh/setup-uv@v5

      - name: Install dependencies
        run: uv pip install -e ".[build]" --system

      - name: Build pack
        run: |
          wgg build games/my-pack --name MyGame --output dist

      - name: Upload artifact
        uses: actions/upload-artifact@v4
        with:
          name: MyGame-${{ matrix.target }}
          path: dist/MyGame/
```

Adjust `games/my-pack` and `MyGame` to match your pack directory and
desired app name.  The three artifacts can then be downloaded from the
Actions run and attached to a GitHub Release.

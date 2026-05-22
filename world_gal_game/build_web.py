"""Web (pygbag / WASM) packaging for World Gal-Game packs.

The desktop builder (:mod:`world_gal_game.build`) wraps PyInstaller; this is
its browser sibling. It stages a self-contained build tree and then invokes
``python -m pygbag --build`` over it (mirroring the PyInstaller subprocess
call), producing an ``index.html`` + asset archive you can host statically.

Public API::

    build_web(pack_path, output_dir, *, app_name=None, serve=False) -> Path

The staging step is factored into :func:`stage_web_build` so it is fully
unit-testable **without** pygbag installed: it just copies files and writes
the templated ``main.py``, returning the staging directory. ``build_web``
calls it and then shells out to pygbag.

Staging layout (mirrors a source checkout so ``resource_root()`` math holds
— ``resource_root()`` is the parent of the ``world_gal_game`` package, and
``pack_content(name)`` resolves to ``<root>/games/<name>/content``)::

    <staging>/
      main.py                     # templated web_main with _PACK rewritten
      world_gal_game/             # full engine package (copied)
      games/<pack_name>/
        content/                  # pack YAML
        assets/                   # pack assets

pygbag is an **optional** dependency (the ``[web]`` extra). Nothing in the
always-imported engine path imports it; it is only referenced here, inside
the subprocess invocation that ``build_web`` runs.

Mobile reuses everything above. Android's primary path
(:func:`build_android_apk`) is pygbag's APK output mode wrapping the very same
staged web build (a WebView shell); the PWA assets written by ``build_web``
serve both the Android "Add to Home Screen" install and the iOS PWA. See
``docs/distribution-mobile.md`` for the honest feasibility notes.
"""
from __future__ import annotations

import logging
import re
import shutil
import subprocess
import sys
from pathlib import Path

_log = logging.getLogger("world_gal_game.build_web")

# pygbag's APK build flag. pygbag emits an Android APK (a WebView shell around
# the WASM bundle) when invoked with ``--build`` plus this option. The exact
# spelling is documented by pygbag and pinned here as a single named constant
# so it lives in exactly one place; if a pygbag release renames it, fix it
# here. We cannot run pygbag in this environment to confirm at build time, so
# the staging path (the testable part) is kept independent of it.
PYGBAG_APK_FLAG = "--archive"

# Files/dirs we never want to drag into a web bundle (caches, native build
# artefacts, the user-local plugin scratch root — none are web-relevant).
_ENGINE_IGNORE = shutil.ignore_patterns(
    "__pycache__", "*.pyc", "*.pyo", ".DS_Store", "*.so", "*.pyd",
)


def _read_pack_title(pack_path: Path) -> str | None:
    """Return the pack ``title`` from content/meta.yaml without importing YAML."""
    meta = pack_path / "content" / "meta.yaml"
    if not meta.exists():
        return None
    text = meta.read_text(encoding="utf-8")
    m = re.search(r"^title:\s+['\"]?(.+?)['\"]?\s*$", text, re.MULTILINE)
    return m.group(1).strip() if m else None


def _render_web_main(pack_name: str) -> str:
    """Return the contents of the staged ``main.py`` for ``pack_name``.

    Built by reading the :mod:`world_gal_game.web_main` template and
    rewriting the single ``_PACK = "..."`` line (the line tagged with the
    ``wgg:web-pack`` marker). Relative-import lines are rewritten to absolute
    so the file works as a top-level ``main.py`` at the staging root rather
    than as a package submodule.
    """
    template = Path(__file__).resolve().parent / "web_main.py"
    src = template.read_text(encoding="utf-8")

    # Rewrite the pack constant. Use a literal so an arbitrary pack name
    # can't inject code.
    src = re.sub(
        r'^_PACK = ".*?"$',
        f'_PACK = {pack_name!r}',
        src,
        count=1,
        flags=re.MULTILINE,
    )
    # As a top-level module the relative imports inside main() must become
    # absolute (`from .app` -> `from world_gal_game.app`).
    src = src.replace("from .app import", "from world_gal_game.app import")
    src = src.replace("from .config import", "from world_gal_game.config import")
    return src


def stage_web_build(
    pack_path: Path,
    staging_dir: Path,
    *,
    pack_name: str | None = None,
) -> Path:
    """Populate ``staging_dir`` with the engine + pack + templated main.py.

    Returns the staging directory. **Does not invoke pygbag** — pure file
    operations, so it's unit-testable on any machine.

    Parameters
    ----------
    pack_path:
        Pack root containing ``content/meta.yaml`` (and usually ``assets/``).
    staging_dir:
        Where to assemble the build tree. Created if missing; a pre-existing
        ``world_gal_game`` / ``games`` subtree is removed first so repeat
        builds are clean.
    pack_name:
        Directory name the pack lands under in ``games/<name>/``. Defaults to
        ``pack_path.name``. Also the value baked into the staged ``main.py``.
    """
    pack_path = Path(pack_path).resolve()
    if not (pack_path / "content" / "meta.yaml").exists():
        raise FileNotFoundError(
            f"pack_path is missing content/meta.yaml: {pack_path}"
        )

    pack_name = pack_name or pack_path.name
    staging_dir = Path(staging_dir).resolve()
    staging_dir.mkdir(parents=True, exist_ok=True)

    # 1) Copy the engine package, mirroring the source layout so
    #    resource_root() (parent of world_gal_game) resolves correctly.
    engine_src = Path(__file__).resolve().parent          # world_gal_game/
    engine_dst = staging_dir / "world_gal_game"
    if engine_dst.exists():
        shutil.rmtree(engine_dst)
    shutil.copytree(engine_src, engine_dst, ignore=_ENGINE_IGNORE)

    # 2) Copy the pack's content/ and assets/ under games/<pack_name>/.
    pack_dst = staging_dir / "games" / pack_name
    if pack_dst.exists():
        shutil.rmtree(pack_dst)
    pack_dst.mkdir(parents=True, exist_ok=True)
    shutil.copytree(pack_path / "content", pack_dst / "content",
                    ignore=_ENGINE_IGNORE)
    assets_src = pack_path / "assets"
    if assets_src.is_dir():
        shutil.copytree(assets_src, pack_dst / "assets", ignore=_ENGINE_IGNORE)
    # Pack-local plugins travel too, when present (pure-Python, web-safe).
    plugins_src = pack_path / "plugins"
    if plugins_src.is_dir():
        shutil.copytree(plugins_src, pack_dst / "plugins", ignore=_ENGINE_IGNORE)

    # 3) Write the templated entry point at the staging root.
    main_py = staging_dir / "main.py"
    main_py.write_text(_render_web_main(pack_name), encoding="utf-8")

    return staging_dir


def build_web(
    pack_path: Path,
    output_dir: Path = Path("build/web"),
    *,
    app_name: str | None = None,
    serve: bool = False,
) -> Path:
    """Stage a web build and run pygbag over it.

    Parameters
    ----------
    pack_path:
        Pack root (must contain ``content/meta.yaml``).
    output_dir:
        Where the staging tree + pygbag output land. Defaults to
        ``build/web`` relative to the current working directory.
    app_name:
        Display name passed to pygbag (``--app_name``). Derived from the
        pack title when omitted.
    serve:
        When ``True``, run pygbag *without* ``--build`` so it serves the game
        locally for testing (``http://localhost:8000``) instead of producing
        a one-shot archive.

    Returns
    -------
    Path
        The staging directory (which, after a non-serve build, also contains
        pygbag's ``build/web/`` output).

    Raises
    ------
    FileNotFoundError
        If ``pack_path`` is missing ``content/meta.yaml``.
    RuntimeError
        If pygbag exits non-zero.
    """
    pack_path = Path(pack_path).resolve()
    output_dir = Path(output_dir).resolve()

    pack_name = pack_path.name
    if app_name is None:
        title = _read_pack_title(pack_path)
        app_name = title or pack_name

    staging = stage_web_build(pack_path, output_dir, pack_name=pack_name)
    staged_main = staging / "main.py"

    # Mirror build.py's PyInstaller subprocess shape. pygbag is referenced
    # ONLY here, never at engine import time.
    cmd = [sys.executable, "-m", "pygbag"]
    if not serve:
        cmd.append("--build")
    cmd += ["--app_name", str(app_name), str(staged_main)]

    print(f"[wgg build-web] {' '.join(cmd)}", flush=True)
    result = subprocess.run(cmd)
    if result.returncode != 0:
        raise RuntimeError(f"pygbag exited with code {result.returncode}")

    # Post-build: drop the PWA manifest + service worker next to pygbag's
    # output and patch index.html so the bundle is installable / offline-
    # capable (Android "Add to Home Screen" + iOS PWA). Guarded — a missing
    # index.html logs and skips rather than crashing a good build. Skipped in
    # serve mode (nothing is emitted to disk to patch).
    if not serve:
        _emit_pwa_assets(staging, app_name)

    print(f"[wgg build-web] done -> {staging}", flush=True)
    return staging


def _pygbag_output_dir(staging: Path) -> Path:
    """Best-effort locate pygbag's emitted output (where index.html lands).

    pygbag writes its bundle to ``<staging>/build/web/`` next to the entry
    ``main.py``. We probe that first, then fall back to the staging root and
    finally to any ``index.html`` found beneath staging, so a future pygbag
    layout shift degrades gracefully instead of silently missing the patch.
    """
    candidate = staging / "build" / "web"
    if (candidate / "index.html").exists():
        return candidate
    if (staging / "index.html").exists():
        return staging
    found = sorted(staging.rglob("index.html"))
    if found:
        return found[0].parent
    # Nothing emitted yet — return the conventional path so the caller's
    # resilient writer logs a clear "index.html not found" against it.
    return candidate


def _emit_pwa_assets(staging: Path, app_name: str) -> None:
    """Write PWA assets into pygbag's output dir (resilient, never raises)."""
    # Imported lazily: web_pwa is pure but there's no reason to load it on the
    # staging-only path that unit tests exercise.
    from .web_pwa import write_pwa_assets

    out = _pygbag_output_dir(staging)
    try:
        ok = write_pwa_assets(out, app_name)
    except Exception as exc:  # pragma: no cover - defensive belt-and-braces
        _log.warning("[wgg build-web] PWA asset emit failed (non-fatal): %s", exc)
        return
    if ok:
        print(f"[wgg build-web] PWA assets written -> {out}", flush=True)
    else:
        print(
            f"[wgg build-web] PWA manifest/worker written, index.html patch "
            f"skipped (not found under {out}).",
            flush=True,
        )


def build_android_apk(
    pack_path: Path,
    output_dir: Path = Path("build/android"),
    *,
    app_name: str | None = None,
) -> Path:
    """Stage a web build and run pygbag in APK output mode (Android primary).

    Android's primary delivery is pygbag wrapping the **same** staged web
    build into an APK (a thin WebView shell around the WASM bundle). This
    reuses :func:`stage_web_build` verbatim — the staging layout is identical
    to a web build, so it stays unit-testable without pygbag — and then runs
    ``python -m pygbag --build <PYGBAG_APK_FLAG>`` over it. PWA assets are
    still emitted so the same artefact also supports "Add to Home Screen".

    Native buildozer / python-for-android SDL2 is explicitly out of scope (see
    ``docs/distribution-mobile.md``); this WebView-APK route is the supported
    path because a VN does not need native GPU access.

    Parameters mirror :func:`build_web`. Returns the staging directory.

    Raises
    ------
    FileNotFoundError
        If ``pack_path`` is missing ``content/meta.yaml``.
    RuntimeError
        If pygbag exits non-zero.
    """
    pack_path = Path(pack_path).resolve()
    output_dir = Path(output_dir).resolve()

    pack_name = pack_path.name
    if app_name is None:
        title = _read_pack_title(pack_path)
        app_name = title or pack_name

    staging = stage_web_build(pack_path, output_dir, pack_name=pack_name)
    staged_main = staging / "main.py"

    # Same shape as build_web's pygbag call, plus the APK output flag. pygbag
    # is referenced ONLY here, never at engine import time.
    cmd = [
        sys.executable, "-m", "pygbag", "--build", PYGBAG_APK_FLAG,
        "--app_name", str(app_name), str(staged_main),
    ]
    print(f"[wgg build-android] {' '.join(cmd)}", flush=True)
    result = subprocess.run(cmd)
    if result.returncode != 0:
        raise RuntimeError(f"pygbag exited with code {result.returncode}")

    # The APK wraps the web bundle, so the PWA assets are relevant here too.
    _emit_pwa_assets(staging, app_name)

    print(f"[wgg build-android] done -> {staging}", flush=True)
    return staging

"""Web (pygbag / Emscripten) platform glue.

The engine targets desktop *and* the browser (via pygbag, which compiles
CPython + pygame-ce to WebAssembly). Almost all of the engine is platform
agnostic; the one place that genuinely differs is **persistent storage**.

On the web, pygbag mounts ``/data`` as an IDBFS filesystem backed by the
browser's IndexedDB. Writes to that filesystem stay in an in-memory page
cache until something explicitly *syncs* them down to IndexedDB. If we
never sync, a hard page reload (or tab close) loses every save the player
made this session. So after each save write (and each delete) we ask the
runtime to flush.

**The exact flush incantation is version-sensitive.** Different pygbag
releases expose the IDBFS->IndexedDB sync under slightly different names
(``platform.window.…``, a ``aio``/``asyncio`` helper, or a direct
``FS.syncfs`` call). To keep that fragility in exactly one place, *all*
knowledge of how to talk to pygbag lives in :func:`flush_storage` below.
Everywhere else in the engine just calls ``flush_storage()`` and trusts it
to be a safe no-op off-web and best-effort on-web.

Nothing in this module imports pygbag at module load — importing it on the
desktop must be free of side effects and never fail.
"""
from __future__ import annotations

import logging
import sys

_log = logging.getLogger("world_gal_game.platform_web")


def is_web() -> bool:
    """Return True when running under pygbag / Emscripten (the browser).

    pygbag builds report ``sys.platform == "emscripten"``. This is the one
    check the rest of the engine uses to branch web-specific behaviour;
    keep it cheap and side-effect free.
    """
    return sys.platform == "emscripten"


def flush_storage() -> None:
    """Persist the IDBFS mount down to IndexedDB (web only; no-op elsewhere).

    Off the web this returns immediately. On the web it triggers pygbag's
    IDBFS->IndexedDB sync so saves survive a hard reload.

    The whole body is wrapped in a broad ``try/except``: a storage flush
    must never be able to crash a save. Worst case the flush silently fails
    and the save still lives in the in-memory FS for the rest of the
    session (only a hard reload would lose it), which is strictly better
    than propagating an exception out of :meth:`SaveManager.save`.

    Implementation note — this is the *single* place that knows pygbag's
    sync API. If a future pygbag release renames the call, fix it here and
    nowhere else. We try the known idioms in order of preference and stop
    at the first that exists:

    1. ``platform.window.bridge`` style flush exposed by recent pygbag, and
    2. a direct ``FS.syncfs(False, cb)`` against the Emscripten
       ``MEMFS``/``IDBFS`` module (the underlying primitive).
    """
    if not is_web():
        return
    try:
        # Preferred: pygbag ships a ``platform`` module inside the WASM
        # runtime exposing a window bridge. ``window.bridge`` / a helper
        # named ``aio`` provide the sync. We probe defensively because the
        # surface has shifted across releases.
        import platform as _pf  # pygbag's shim, not CPython's stdlib one

        window = getattr(_pf, "window", None)
        if window is not None:
            # Newer pygbag: an explicit IDBFS flush helper.
            flusher = getattr(window, "idbfs_sync", None) or getattr(
                window, "sync_storage", None
            )
            if callable(flusher):
                flusher()
                return

        # Fallback: drive Emscripten's FS.syncfs directly. ``False`` means
        # "push the in-memory FS down to the backing store (IndexedDB)".
        FS = getattr(_pf, "FS", None)
        if FS is not None and hasattr(FS, "syncfs"):
            FS.syncfs(False, lambda *_: None)
            return

        _log.debug("flush_storage: no known pygbag sync entry point found")
    except Exception as exc:  # pragma: no cover - exercised only on web
        _log.debug("flush_storage: sync failed (non-fatal): %s", exc)

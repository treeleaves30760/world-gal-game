"""Minimal Steamworks bridge via ``ctypes`` — no third-party dependency.

We bind only the handful of Steamworks C entry points a single-player VN
actually needs:

- ``SteamAPI_Init`` / ``SteamAPI_Shutdown``  — lifecycle
- ``SteamAPI_RunCallbacks``                  — pump the callback queue
- ``SteamAPI_ISteamUserStats_SetAchievement`` + ``StoreStats`` — achievements

The official ``steam_api`` redistributable (``steam_api64.dll`` /
``libsteam_api.so`` / ``libsteam_api.dylib``) ships with the developer's
Steam depot and is **deliberately not vendored** into this repo. When the
library can't be loaded — itch.io builds, dev machines, CI, the web — every
method here is a safe no-op and :meth:`SteamBridge.try_init` returns
``None`` so the calling code can simply skip Steam entirely.

Design contract (relied on by tests + ``app.py``):

- **Importing this module never fails**, even with no Steam present.
- :meth:`try_init` returns ``None`` on *any* failure (lib missing, wrong
  ABI, ``SteamAPI_Init`` returns false, ``steam_appid.txt`` missing in dev).
- Once you hold a bridge, every method is exception-safe.

The exact ctypes signatures are best-effort. Steamworks' flat C API is
stable, but ABI details (the ``ISteamUserStats`` "interface version" string,
calling conventions) vary by SDK version. Each native call is wrapped so a
signature mismatch degrades to a logged no-op rather than a crash. If you
need byte-perfect bindings, this is the one file to revise.
"""
from __future__ import annotations

import ctypes
import logging
import os
import sys
from pathlib import Path
from typing import Iterable

_log = logging.getLogger("world_gal_game.integrations.steam")

# Interface-version string Steamworks expects for the user-stats accessor.
# This is SDK-version sensitive; kept here as the single tweak point.
_STEAMUSERSTATS_INTERFACE = b"STEAMUSERSTATS_INTERFACE_VERSION013"


def _candidate_lib_names() -> list[str]:
    """Per-platform Steamworks shared-library file names to try, in order."""
    if sys.platform == "win32":
        return ["steam_api64.dll", "steam_api.dll"]
    if sys.platform == "darwin":
        return ["libsteam_api.dylib"]
    # linux / other unix
    return ["libsteam_api.so"]


def _load_steam_lib() -> ctypes.CDLL | None:
    """Try to load the Steamworks shared library; return None if absent.

    Search order: an explicit ``WGG_STEAM_LIB`` path, then the current
    working directory, then the executable's directory, then the system
    loader path (bare name). Any failure → ``None``.
    """
    names = _candidate_lib_names()
    search_dirs: list[Path] = [Path.cwd()]
    try:
        search_dirs.append(Path(sys.executable).resolve().parent)
    except Exception:
        pass

    candidates: list[str] = []
    override = os.environ.get("WGG_STEAM_LIB")
    if override:
        candidates.append(override)
    for d in search_dirs:
        for name in names:
            candidates.append(str(d / name))
    # Bare names: let the OS loader resolve via its standard search path.
    candidates.extend(names)

    for cand in candidates:
        try:
            return ctypes.CDLL(cand)
        except OSError:
            continue
    return None


class SteamBridge:
    """Thin live handle around an initialised Steamworks API.

    Construct via :meth:`try_init`; never instantiate directly in game code
    (the constructor assumes the lib is already loaded + initialised).
    """

    def __init__(self, lib: ctypes.CDLL,
                 mapping: dict[str, str] | None = None) -> None:
        self._lib = lib
        # engine achievement id -> Steam API name. Identity by default.
        self._mapping: dict[str, str] = dict(mapping or {})
        # Steam ids we've already pushed this session (SetAchievement is
        # idempotent on Steam's side, but we avoid redundant native calls).
        self._pushed: set[str] = set()
        self._dirty = False
        self._alive = True

    # ------------------------------------------------------------------
    # Construction

    @classmethod
    def try_init(cls, app_id: int | str,
                 mapping: dict[str, str] | None = None) -> "SteamBridge | None":
        """Load + initialise Steamworks. Return a bridge, or ``None``.

        Returns ``None`` (so the caller runs without Steam) when:

        - the ``steam_api`` library can't be located / loaded,
        - ``SteamAPI_Init`` returns false (Steam not running, or no
          ``steam_appid.txt`` during development), or
        - anything else goes wrong.

        ``app_id`` is recorded into ``SteamAppId`` / ``SteamGameId`` env vars
        before init (Steamworks reads them); pass the real depot app id, or
        ``480`` (Spacewar) for overlay smoke-testing.
        """
        try:
            os.environ.setdefault("SteamAppId", str(app_id))
            os.environ.setdefault("SteamGameId", str(app_id))

            lib = _load_steam_lib()
            if lib is None:
                _log.info("Steam: steam_api library not found; running without Steam.")
                return None

            init_fn = getattr(lib, "SteamAPI_Init", None)
            if init_fn is None:
                _log.info("Steam: SteamAPI_Init missing; running without Steam.")
                return None
            init_fn.restype = ctypes.c_bool
            if not bool(init_fn()):
                _log.info("Steam: SteamAPI_Init returned false; running without Steam.")
                return None

            return cls(lib, mapping=mapping)
        except Exception as exc:  # pragma: no cover - depends on native env
            _log.info("Steam: init failed (%s); running without Steam.", exc)
            return None

    # ------------------------------------------------------------------
    # Internal: resolve the ISteamUserStats interface pointer.

    def _user_stats(self):
        """Return the ISteamUserStats pointer, or None on any failure."""
        try:
            getter = getattr(self._lib, "SteamAPI_SteamUserStats_v013", None)
            if getter is not None:
                getter.restype = ctypes.c_void_p
                return getter()
            # Older flat API: SteamUserStats() via the interface-version path.
            generic = getattr(self._lib, "SteamInternal_FindOrCreateUserInterface", None)
            if generic is not None:
                generic.restype = ctypes.c_void_p
                generic.argtypes = [ctypes.c_int, ctypes.c_char_p]
                return generic(0, _STEAMUSERSTATS_INTERFACE)
        except Exception as exc:  # pragma: no cover
            _log.debug("Steam: _user_stats failed: %s", exc)
        return None

    # ------------------------------------------------------------------
    # Public API

    def steam_name(self, ach_id: str) -> str:
        """Map an engine achievement id to its Steam API name (1:1 default)."""
        return self._mapping.get(ach_id, ach_id)

    def unlock(self, ach_id: str) -> None:
        """Unlock an achievement on Steam. Idempotent + exception-safe.

        Repeated calls for the same id are no-ops (tracked in ``_pushed``).
        The flush to Steam's backend is deferred to :meth:`run_callbacks`
        (``StoreStats`` when dirty) to avoid a network round-trip per call.
        """
        if not self._alive:
            return
        name = self.steam_name(ach_id)
        if name in self._pushed:
            return
        try:
            stats = self._user_stats()
            set_fn = getattr(self._lib,
                             "SteamAPI_ISteamUserStats_SetAchievement", None)
            if stats is not None and set_fn is not None:
                set_fn.restype = ctypes.c_bool
                set_fn.argtypes = [ctypes.c_void_p, ctypes.c_char_p]
                set_fn(stats, name.encode("utf-8"))
            # Mark pushed + dirty regardless: even if the native binding was
            # unavailable we don't want to spin retrying every frame.
            self._pushed.add(name)
            self._dirty = True
        except Exception as exc:  # pragma: no cover
            _log.debug("Steam: SetAchievement(%s) failed: %s", name, exc)
            self._pushed.add(name)

    def run_callbacks(self) -> None:
        """Pump Steam callbacks; flush stored stats when dirty. Per-frame."""
        if not self._alive:
            return
        try:
            run_fn = getattr(self._lib, "SteamAPI_RunCallbacks", None)
            if run_fn is not None:
                run_fn()
            if self._dirty:
                stats = self._user_stats()
                store_fn = getattr(self._lib,
                                   "SteamAPI_ISteamUserStats_StoreStats", None)
                if stats is not None and store_fn is not None:
                    store_fn.restype = ctypes.c_bool
                    store_fn.argtypes = [ctypes.c_void_p]
                    store_fn(stats)
                self._dirty = False
        except Exception as exc:  # pragma: no cover
            _log.debug("Steam: run_callbacks failed: %s", exc)

    def shutdown(self) -> None:
        """Shut Steamworks down. Safe to call once; further calls are no-ops."""
        if not self._alive:
            return
        self._alive = False
        try:
            shut_fn = getattr(self._lib, "SteamAPI_Shutdown", None)
            if shut_fn is not None:
                shut_fn()
        except Exception as exc:  # pragma: no cover
            _log.debug("Steam: shutdown failed: %s", exc)

    # ------------------------------------------------------------------
    # Helpers used by the achievement hook + app startup pre-seed.

    def pushed_ids(self) -> set[str]:
        """Steam API names already pushed this session (read-only snapshot)."""
        return set(self._pushed)

    def push_unlocked(self, engine_ids: Iterable[str]) -> int:
        """Unlock every id in ``engine_ids`` not yet pushed. Returns count newly pushed.

        Used both by the per-frame achievement hook and the startup
        pre-seed (so saves that already unlocked things sync immediately).
        """
        before = len(self._pushed)
        for eid in engine_ids:
            self.unlock(eid)
        return len(self._pushed) - before

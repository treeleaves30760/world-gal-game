"""Exceptions raised by the plugin subsystem.

All plugin loading and dispatch goes through a fairly narrow protocol;
the engine treats third-party code as untrusted-but-not-sandboxed, so
errors are routed through these typed exceptions rather than letting
arbitrary tracebacks escape into the main loop.

The :func:`isolate` helper at the bottom of this module wraps any call
into plugin code and converts uncaught exceptions into structured
:class:`PluginRuntimeError` objects that callers can log without
crashing.
"""
from __future__ import annotations

import logging
import traceback
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Iterator


class PluginError(Exception):
    """Base class for every plugin-system error.

    All other plugin exceptions inherit from this so callers can write
    ``except PluginError`` and catch the family without enumerating
    subclasses.
    """


class ManifestError(PluginError):
    """The ``plugin.yaml`` could not be parsed or failed schema validation."""


class DuplicateKindError(PluginError):
    """Two plugins tried to register the same effect / condition kind.

    Carries ``kind`` and the conflicting ``plugin_ids`` so the engine can
    surface a useful diagnostic instead of silently overwriting.
    """

    def __init__(self, kind: str, existing_plugin: str, new_plugin: str,
                 *, category: str = "effect") -> None:
        self.kind = kind
        self.existing_plugin = existing_plugin
        self.new_plugin = new_plugin
        self.category = category
        super().__init__(
            f"{category} kind '{kind}' is already registered by "
            f"plugin '{existing_plugin}'; refusing to overwrite from "
            f"plugin '{new_plugin}'"
        )


class UnknownKindError(PluginError):
    """Lookup for an effect/condition kind that nobody registered."""

    def __init__(self, kind: str, *, category: str = "effect") -> None:
        self.kind = kind
        self.category = category
        super().__init__(f"unknown {category} kind: '{kind}'")


class DependencyError(PluginError):
    """Plugin dependency graph is unsatisfiable (missing or cyclic)."""

    def __init__(self, message: str, *, plugin_id: str | None = None,
                 cycle: list[str] | None = None) -> None:
        self.plugin_id = plugin_id
        self.cycle = cycle or []
        super().__init__(message)


class IncompatibleEngineError(PluginError):
    """Plugin's declared ``engine_version`` range does not include the running engine."""

    def __init__(self, plugin_id: str, requested: str,
                 current: str) -> None:
        self.plugin_id = plugin_id
        self.requested = requested
        self.current = current
        super().__init__(
            f"plugin '{plugin_id}' requires engine_version "
            f"'{requested}', but engine is '{current}'"
        )


class PluginLoadError(PluginError):
    """The plugin's Python entry module could not be imported."""

    def __init__(self, plugin_id: str, message: str,
                 *, cause: BaseException | None = None) -> None:
        self.plugin_id = plugin_id
        self.cause = cause
        super().__init__(f"plugin '{plugin_id}' failed to load: {message}")


@dataclass
class PluginRuntimeError:
    """Structured record of a runtime failure inside plugin code.

    Not an exception — :func:`isolate` returns this when a plugin
    handler / hook raised. The dispatcher logs it and converts it into
    a benign return value so the main loop keeps going.
    """

    plugin_id: str
    where: str            # e.g. "effect:my_kind", "hook:effect.after_apply"
    message: str
    traceback: str = ""
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "plugin_id": self.plugin_id,
            "where": self.where,
            "message": self.message,
            "traceback": self.traceback,
            **self.extra,
        }


_log = logging.getLogger("world_gal_game.plugins")


@contextmanager
def isolate(plugin_id: str, where: str,
            *, capture: list[PluginRuntimeError] | None = None,
            reraise: bool = False) -> Iterator[None]:
    """Run a block of plugin code, swallowing exceptions.

    Use:

        with isolate(plugin_id="step_counter", where="hook:foo") as guard:
            user_callback(ctx)

    On exception: logs a warning, appends a :class:`PluginRuntimeError`
    to ``capture`` if provided. Re-raises only when ``reraise=True``
    (useful in tests).
    """
    try:
        yield
    except Exception as exc:
        rec = PluginRuntimeError(
            plugin_id=plugin_id,
            where=where,
            message=str(exc),
            traceback=traceback.format_exc(),
        )
        _log.warning("plugin %s failed at %s: %s", plugin_id, where, exc)
        if capture is not None:
            capture.append(rec)
        if reraise:
            raise

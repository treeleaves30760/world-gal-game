"""Plugin manifest schema (``plugin.yaml``).

Every plugin ships a ``plugin.yaml`` next to its Python entry module.
The manifest is the **declarative half** of plugin authoring: it tells
the engine what the plugin extends, which extension points it owns,
which other plugins it depends on, and what side effects to expect.

The Python entry module then does the **imperative half**: it imports
the plugin decorators and registers handler callables.

Both halves get loaded together by :class:`PluginManager.load`.

The manifest is intentionally narrow:

- ``id`` / ``name`` / ``version`` / ``description``    — metadata
- ``engine_version``                                   — semver range against engine's __version__
- ``depends``                                          — other plugin ids
- ``entry_module``                                     — Python module path relative to plugin dir
- ``extends.{effects,conditions,hooks,inspect_fields}``— declared extension points
- ``side_effects.{reads_filesystem,network,subprocess}`` — boolean disclosure flags

Widget / scene / brain / dialogue_op handlers register in code but are
not declared here yet.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator

from .errors import IncompatibleEngineError, ManifestError


_SLUG_RE = re.compile(r"^[a-z][a-z0-9_]{1,63}$")


class ExtensionDeclaration(BaseModel):
    """One declared extension point inside ``plugin.yaml``.

    Used uniformly for effects / conditions / hooks / inspect_fields —
    the ``kind`` field is reinterpreted depending on the parent list.
    For effects/conditions ``kind`` is the kind string; for hooks it's
    the event name; for inspect_fields it's the field key.
    """

    model_config = ConfigDict(extra="forbid")

    kind: str
    description: str = ""
    # Free-form parameter signature hint, e.g.
    # {"target": "character_id", "value": "int"}. Used by Capability
    # Manifest + future PackEditor; the engine itself does not enforce
    # the signature beyond the basic Effect/Condition pydantic shape.
    signature: dict[str, Any] = Field(default_factory=dict)


class SideEffectFlags(BaseModel):
    """What the plugin admits to doing.

    Purely advisory — the engine never blocks based on these,
    but it does print a one-line summary at load time so the player /
    developer sees which plugins ask for network or filesystem access.
    """

    model_config = ConfigDict(extra="forbid")

    reads_filesystem: bool = False
    writes_filesystem: bool = False
    network: bool = False
    subprocess: bool = False
    # Catch-all for plugins that perform unusual operations not covered
    # by the structured flags above — short human-readable strings.
    other: list[str] = Field(default_factory=list)


class Extends(BaseModel):
    """Declared extension points, grouped by category."""

    model_config = ConfigDict(extra="forbid")

    effects: list[ExtensionDeclaration] = Field(default_factory=list)
    conditions: list[ExtensionDeclaration] = Field(default_factory=list)
    # Hook entries reuse ExtensionDeclaration; their ``kind`` is the
    # event name (e.g. "effect.after_apply").
    hooks: list[ExtensionDeclaration] = Field(default_factory=list)
    inspect_fields: list[ExtensionDeclaration] = Field(default_factory=list)


class PluginManifest(BaseModel):
    """Top-level schema for ``plugin.yaml``."""

    model_config = ConfigDict(extra="forbid")

    id: str
    name: str = ""
    version: str = "0.0.0"
    description: str = ""
    author: str = ""
    engine_version: str = "*"  # semver range; "*" means "any"
    depends: list[str] = Field(default_factory=list)
    entry_module: str = "plugin"  # default: load <plugin_dir>/plugin.py
    extends: Extends = Field(default_factory=Extends)
    side_effects: SideEffectFlags = Field(default_factory=SideEffectFlags)
    # Anything the plugin author wants to surface; not interpreted by the engine.
    tags: list[str] = Field(default_factory=list)

    @field_validator("id")
    @classmethod
    def _validate_id(cls, v: str) -> str:
        if not _SLUG_RE.match(v):
            raise ValueError(
                f"plugin id '{v}' must match [a-z][a-z0-9_]{{1,63}} "
                "(lowercase, starts with a letter, only letters/digits/underscore)"
            )
        return v

    @field_validator("version")
    @classmethod
    def _validate_version(cls, v: str) -> str:
        # Accept loose semver "X.Y.Z" or "X.Y" or "X" plus optional pre-release "-foo".
        if not re.match(r"^\d+(\.\d+){0,2}([\-+][a-zA-Z0-9_.]+)?$", v):
            raise ValueError(f"version '{v}' is not a valid semver-ish string")
        return v

    # ------------------------------------------------------------------
    # Construction helpers

    @classmethod
    def from_yaml(cls, path: Path) -> "PluginManifest":
        """Read + validate a ``plugin.yaml`` file."""
        try:
            with open(path, encoding="utf-8") as fh:
                data = yaml.safe_load(fh) or {}
        except yaml.YAMLError as exc:
            raise ManifestError(f"{path}: YAML parse failed: {exc}") from exc
        if not isinstance(data, dict):
            raise ManifestError(f"{path}: manifest must be a mapping at top level")
        try:
            return cls.model_validate(data)
        except Exception as exc:
            raise ManifestError(f"{path}: {exc}") from exc

    # ------------------------------------------------------------------
    # Engine compatibility check

    def check_engine_compatible(self, engine_version: str) -> None:
        """Raise :class:`IncompatibleEngineError` if engine doesn't fit ``engine_version``.

        Range syntax is intentionally narrow:

        - ``"*"``                  — any
        - ``"X"``, ``"X.Y"``, ``"X.Y.Z"`` — exact match (prefix-extended)
        - ``">=X.Y"``, ``"<=X.Y"``, ``">X.Y"``, ``"<X.Y"``  — bound
        - ``">=X,<Y"``             — comma-joined conjunction
        """
        if not _semver_matches(engine_version, self.engine_version):
            raise IncompatibleEngineError(
                plugin_id=self.id,
                requested=self.engine_version,
                current=engine_version,
            )


# ----------------------------------------------------------------------
# Semver helpers


def _parse_version(s: str) -> tuple[int, ...]:
    s = s.split("-", 1)[0].split("+", 1)[0]
    parts = s.split(".")
    out: list[int] = []
    for p in parts:
        try:
            out.append(int(p))
        except ValueError:
            out.append(0)
    while len(out) < 3:
        out.append(0)
    return tuple(out)


def _semver_matches(version: str, spec: str) -> bool:
    """Return True if *version* satisfies *spec*. See PluginManifest docstring."""
    spec = spec.strip()
    if not spec or spec == "*":
        return True
    cur = _parse_version(version)
    for clause in [c.strip() for c in spec.split(",") if c.strip()]:
        if not _clause_matches(cur, clause):
            return False
    return True


def _clause_matches(cur: tuple[int, ...], clause: str) -> bool:
    op = ""
    rest = clause
    for cand in (">=", "<=", "==", ">", "<", "~="):
        if clause.startswith(cand):
            op = cand
            rest = clause[len(cand):].strip()
            break
    target = _parse_version(rest)
    if op == "" or op == "==":
        # Prefix-style match: "0.1" matches "0.1.5".
        depth = max(1, len([p for p in rest.split(".") if p]))
        return cur[:depth] == target[:depth]
    if op == ">=":
        return cur >= target
    if op == "<=":
        return cur <= target
    if op == ">":
        return cur > target
    if op == "<":
        return cur < target
    if op == "~=":
        # ~=1.4 means >=1.4, <2.0; ~=1.4.5 means >=1.4.5, <1.5.
        if not target:
            return False
        upper = list(target)
        # Drop the last component, bump the prior one.
        depth = max(1, len([p for p in rest.split(".") if p]))
        if depth >= 2:
            upper[depth - 2] += 1
            for i in range(depth - 1, len(upper)):
                upper[i] = 0
        return cur >= target and tuple(upper) > cur
    return False

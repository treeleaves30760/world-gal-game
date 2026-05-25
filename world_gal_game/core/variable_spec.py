"""Declarative variable manifest.

Story flags are otherwise an untyped ``dict[str, Any]`` created on first write
(see ``world_gal_game/core/event_log.py``), so nothing can enumerate which
flags exist or what they mean. A variable manifest closes that gap: a pack
declares each narrative-state variable with a type, a default, a description,
and a free-form category. Tools can then list the declared variables, hand out
typed defaults, and validate that a write stays inside the declared domain.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

VariableType = Literal["bool", "int", "float", "str", "enum"]

_FALLBACK_DEFAULTS: dict[str, Any] = {
    "bool": False,
    "int": 0,
    "float": 0.0,
    "str": "",
}


class VariableSpec(BaseModel):
    """One declared narrative-state variable.

    The declared ``type`` fixes the accepted domain. ``enum`` variables draw
    their domain from ``values``; the other scalar types match the obvious
    Python type, with the deliberate exception that an ``int`` variable never
    accepts a bool (``isinstance(True, int)`` is True in Python, which we do not
    want to leak into narrative state).
    """

    model_config = ConfigDict(extra="forbid")

    key: str
    type: VariableType = "bool"
    default: Any = None
    description: str = ""
    category: str = ""
    values: list[Any] = Field(default_factory=list)

    def _coerce(self, value: Any) -> Any:
        """Coerce a non-None value into the declared scalar type."""
        if self.type == "bool":
            if isinstance(value, bool):
                return value
            if isinstance(value, str):
                lowered = value.strip().lower()
                if lowered in {"true", "1", "yes", "on"}:
                    return True
                if lowered in {"false", "0", "no", "off"}:
                    return False
                raise ValueError(f"cannot coerce {value!r} to bool")
            return bool(value)
        if self.type == "int":
            if isinstance(value, bool):
                raise ValueError("bool is not accepted as int")
            return int(value)
        if self.type == "float":
            if isinstance(value, bool):
                raise ValueError("bool is not accepted as float")
            return float(value)
        if self.type == "str":
            return value if isinstance(value, str) else str(value)
        # enum
        if value not in self.values:
            raise ValueError(
                f"value {value!r} is not in enum domain {self.values!r}"
            )
        return value

    def coerced_default(self) -> Any:
        """Return the typed default, filling in or coercing as needed."""
        if self.default is None:
            if self.type == "enum":
                return self.values[0] if self.values else None
            return _FALLBACK_DEFAULTS[self.type]
        return self._coerce(self.default)

    def accepts(self, value: Any) -> bool:
        """Return True iff ``value`` is valid for this spec."""
        if self.type == "bool":
            return isinstance(value, bool)
        if self.type == "int":
            return isinstance(value, int) and not isinstance(value, bool)
        if self.type == "float":
            return isinstance(value, float) and not isinstance(value, bool)
        if self.type == "str":
            return isinstance(value, str)
        # enum
        return value in self.values

    @model_validator(mode="after")
    def _check_domain(self) -> VariableSpec:
        if self.type == "enum":
            if not self.values:
                raise ValueError(
                    f"enum variable {self.key!r} requires a non-empty 'values' list"
                )
            if self.default is not None and self.default not in self.values:
                raise ValueError(
                    f"enum variable {self.key!r} default {self.default!r} "
                    f"is not in domain {self.values!r}"
                )
        elif self.default is not None:
            try:
                self._coerce(self.default)
            except (ValueError, TypeError) as exc:
                raise ValueError(
                    f"variable {self.key!r} default {self.default!r} "
                    f"is not coercible to {self.type}: {exc}"
                ) from exc
        return self


class VariableManifest(BaseModel):
    """The declared variables for a pack, keyed by variable key."""

    model_config = ConfigDict(extra="forbid")

    variables: dict[str, VariableSpec] = Field(default_factory=dict)

    def keys(self) -> list[str]:
        """Return the declared variable keys, sorted."""
        return sorted(self.variables)

    def get(self, key: str) -> VariableSpec | None:
        """Return the spec for ``key``, or None if undeclared."""
        return self.variables.get(key)

    def declared(self, key: str) -> bool:
        """Return True iff ``key`` is declared."""
        return key in self.variables

    def defaults(self) -> dict[str, Any]:
        """Return a mapping of key to coerced default for every variable."""
        return {key: spec.coerced_default() for key, spec in self.variables.items()}

    def validate_value(self, key: str, value: Any) -> str | None:
        """Return None if ``value`` is valid for ``key``, else a message.

        Undeclared keys and out-of-domain values both yield a human-readable
        explanation naming the expected type or domain.
        """
        spec = self.variables.get(key)
        if spec is None:
            return f"undeclared variable: {key!r}"
        if spec.accepts(value):
            return None
        if spec.type == "enum":
            return (
                f"variable {key!r} expects one of {spec.values!r}, "
                f"got {value!r}"
            )
        return (
            f"variable {key!r} expects type {spec.type}, "
            f"got {type(value).__name__} ({value!r})"
        )

    @classmethod
    def from_items(cls, items: Any) -> VariableManifest:
        """Build a manifest from a list of dicts or a {key: spec} mapping.

        List items must each carry a ``key``; mapping entries supply the key and
        a spec body without it. A duplicated key raises ValueError.
        """
        variables: dict[str, VariableSpec] = {}
        if isinstance(items, dict):
            for key, body in items.items():
                spec_body = dict(body or {})
                spec_body["key"] = key
                if key in variables:
                    raise ValueError(f"duplicate variable key: {key!r}")
                variables[key] = VariableSpec(**spec_body)
        elif isinstance(items, list):
            for index, raw in enumerate(items):
                if not isinstance(raw, dict) or "key" not in raw:
                    raise ValueError(
                        f"list item at index {index} is missing required 'key'"
                    )
                key = raw["key"]
                if key in variables:
                    raise ValueError(f"duplicate variable key: {key!r}")
                variables[key] = VariableSpec(**raw)
        else:
            raise ValueError(
                f"expected a list or mapping of variables, got {type(items).__name__}"
            )
        return cls(variables=variables)

    @classmethod
    def load(cls, path: Path) -> VariableManifest:
        """Load a manifest from a YAML file, or return an empty one.

        A missing or empty file yields an empty manifest. The file may carry a
        top-level ``variables`` list or mapping, or be a bare list of specs.
        """
        path = Path(path)
        if not path.exists():
            return cls()
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        if raw is None:
            return cls()
        if isinstance(raw, dict):
            items = raw.get("variables", {})
        else:
            items = raw
        return cls.from_items(items)

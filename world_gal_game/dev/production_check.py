"""Fail-closed aggregate source-release verification.

``SelfCheck`` is an authoring tool: optional stages may be skipped and a
missing visual baseline can be bootstrapped outside strict mode.  Likewise,
``runtime-check`` intentionally reports one host-sensitive concern in its own
artifact.  Neither report, by itself, is a production-release claim.

This module composes those capabilities without adding any skip or baseline
promotion path.  A production report is successful only when all seven
``SelfCheck`` stages ran, an explicit visual scenario matrix matched declared
approved baselines, the runtime policy enforced numeric limits, both Git
worktrees were clean, and the engine/pack inputs stayed byte-identical for the
whole run.  The resulting JSON is self-digested, time-bounded, and can be
verified again against the current checkout.

The claim remains deliberately narrow: this is automated *source* preflight
evidence.  It does not claim packaged-binary launch, target-device performance,
real audio/controller behavior, accessibility sessions, legal clearance, or
independent human review.  The approval manifest records a human declaration;
it is auditable metadata, not a cryptographic identity proof.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import platform
import stat
import struct
import subprocess
import sys
import tempfile
import time
import unicodedata
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Literal

import yaml
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
)

from .. import __version__ as engine_version
from ..core.localization import normalize_locale_tag
from .runtime_reliability import REPORT_SCHEMA as RUNTIME_REPORT_SCHEMA
from .runtime_reliability import (
    RuntimePolicyError,
    load_runtime_policy,
    run_runtime_check,
)
from .self_check import SelfCheck


REPORT_SCHEMA = "world-gal-game.production-check.v1"
POLICY_SCHEMA_VERSION = 1
APPROVAL_SCHEMA_VERSION = 1
CLAIM_SCOPE = "automated-source-release-preflight"

_SELF_CHECK_STAGES = (
    "schema",
    "refs",
    "dead_ends",
    "softlock",
    "reachability",
    "smoke",
    "visual",
)
_PRODUCTION_VIEWS = (
    "title",
    "onboarding",
    "dialogue",
    "choice",
    "exploration",
    "spatial_map_story_priority",
    "spatial_map_all_reachable",
    "travel_confirmation",
    "menu",
    "history",
    "save",
    "load",
    "settings",
    "cg_gallery",
    "music_room",
    "scene_replay",
    "flowchart",
)
_SHIPPING_SIZES = ((1280, 720), (1920, 1080))
_HASH_CHUNK_BYTES = 1024 * 1024
_RUNTIME_NUMERIC_LIMITS = {
    "cold_start_p95_ms",
    "first_frame_p95_ms",
    "current_rss_max_bytes",
    "peak_rss_max_bytes",
    "rss_growth_max_bytes",
    "frame_p95_ms",
    "frame_p99_ms",
    "frame_budget_ms",
    "max_over_budget_frames",
}
_RUNTIME_NUMERIC_CHECKS = {
    "cold_start_p95_ms",
    "first_frame_p95_ms",
    "current_rss_max_bytes",
    "peak_rss_max_bytes",
    "rss_growth_max_bytes",
    "frame_p95_ms",
    "frame_p99_ms",
    "over_budget_frames",
}
_RUNTIME_REQUIRED_INVARIANTS = {
    "require_finite_metrics",
    "require_rss",
    "require_determinism",
    "require_within_configured_cache_budgets",
    "require_plugin_errors_zero",
    "require_ui_chrome_active",
}
_RUNTIME_FINGERPRINT_FIELDS = {
    "system",
    "system_release",
    "machine",
    "processor",
    "python",
    "pygame",
    "sdl",
    "video_driver",
    "audio_driver",
    "screen_width",
    "screen_height",
    "vsync",
    "frozen",
    "memory_provider",
    "mixer_initialized",
    "user_plugin_scope",
}
_TRANSIENT_TOP_LEVEL = {
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    # ``uv`` creates this repository-local environment by default.  Its
    # ``bin/python`` and console scripts are intentionally symlinks to the
    # host interpreter, so traversing it as a release input makes every normal
    # source checkout fail before any production gate can run.  The directory
    # is tooling state (and gitignored), never engine or pack product content.
    ".venv",
    "artifacts",
    "build",
    "dist",
    "visual_candidates",
}
_TRANSIENT_ANYWHERE = {".git", ".hg", ".svn", "__pycache__"}
_REPORT_TOP_LEVEL_KEYS = {
    "schema",
    "ok",
    "status",
    "generated_at_utc",
    "finished_at_utc",
    "valid_until_utc",
    "duration_ms",
    "claim",
    "invocation",
    "environment",
    "policy",
    "subject",
    "assets",
    "visual_evidence",
    "gates",
    "failures",
    "warnings",
    "artifact",
}
_GATE_KEYS = {
    "command",
    "started_at_utc",
    "finished_at_utc",
    "duration_ms",
    "complete",
    "ok",
    "structured_evidence_sha256",
    "report",
}


class ProductionPolicyError(ValueError):
    """The production policy or one of its referenced documents is invalid."""


class _StrictSafeLoader(yaml.SafeLoader):
    pass


def _construct_unique_mapping(
    loader: _StrictSafeLoader,
    node: yaml.MappingNode,
    deep: bool = False,
) -> dict[Any, Any]:
    loader.flatten_mapping(node)
    result: dict[Any, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        try:
            duplicate = key in result
        except TypeError as exc:
            raise yaml.constructor.ConstructorError(
                "while constructing a mapping",
                node.start_mark,
                "found an unhashable mapping key",
                key_node.start_mark,
            ) from exc
        if duplicate:
            raise yaml.constructor.ConstructorError(
                "while constructing a mapping",
                node.start_mark,
                f"found duplicate key {key!r}",
                key_node.start_mark,
            )
        result[key] = loader.construct_object(value_node, deep=deep)
    return result


_StrictSafeLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    _construct_unique_mapping,
)


def _strict_yaml_load(payload: bytes | str) -> Any:
    return yaml.load(payload, Loader=_StrictSafeLoader)


def _relative_pack_path(value: str, *, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a non-empty relative path")
    candidate = Path(value)
    if candidate.is_absolute() or ".." in candidate.parts:
        raise ValueError(f"{label} must stay inside the pack")
    normalized = candidate.as_posix()
    if normalized in {"", "."}:
        raise ValueError(f"{label} must name a file or directory")
    if (
        candidate.parts[0] in _TRANSIENT_TOP_LEVEL
        or any(part in _TRANSIENT_ANYWHERE for part in candidate.parts)
    ):
        raise ValueError(f"{label} must not use an excluded transient path")
    return normalized


class ProductionVisualPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scenarios_path: str
    approvals_path: str

    @field_validator("scenarios_path", "approvals_path")
    @classmethod
    def _validate_paths(cls, value: str, info: Any) -> str:
        return _relative_pack_path(value, label=str(info.field_name))


class ProductionRuntimePolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    policy_path: str
    require_numeric_limits: Literal[True]

    @field_validator("policy_path")
    @classmethod
    def _validate_path(cls, value: str) -> str:
        return _relative_pack_path(value, label="runtime.policy_path")


class ProductionCoverageMinimums(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scenes: Literal[100.0]
    lines: Literal[100.0]
    choices: Literal[100.0]
    endings: Literal[100.0]


class ProductionCoveragePolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    script_glob: Literal["scripts/test_*.json"]
    seed: int
    minimum_percent: ProductionCoverageMinimums


class ProductionContractPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str
    seed: int

    @field_validator("path")
    @classmethod
    def _validate_path(cls, value: str) -> str:
        return _relative_pack_path(value, label="contract.path")


class ProductionPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[POLICY_SCHEMA_VERSION]
    name: str
    claim_scope: Literal[CLAIM_SCOPE]
    max_evidence_age_seconds: int = Field(ge=60, le=604_800)
    visual: ProductionVisualPolicy
    runtime: ProductionRuntimePolicy
    coverage: ProductionCoveragePolicy
    contract: ProductionContractPolicy
    required_asset_roots: list[str] = Field(min_length=1)

    @field_validator("name")
    @classmethod
    def _validate_name(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("name must be a non-empty string")
        return cleaned

    @field_validator("required_asset_roots")
    @classmethod
    def _validate_asset_roots(cls, value: list[str]) -> list[str]:
        normalized = [
            _relative_pack_path(item, label="required_asset_roots")
            for item in value
        ]
        if len(normalized) != len(set(normalized)):
            raise ValueError("required_asset_roots must not contain duplicates")
        return normalized


class ProductionVisualScenario(BaseModel):
    """Strict subset of ``VisualCheck.capture_scenario`` used for releases."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(
        min_length=1,
        max_length=128,
        pattern=r"^[a-z0-9][a-z0-9_-]{0,127}$",
    )
    view: Literal[
        "title",
        "onboarding",
        "dialogue",
        "choice",
        "exploration",
        "spatial_map_story_priority",
        "spatial_map_all_reachable",
        "travel_confirmation",
        "destination_list",
        "menu",
        "history",
        "save",
        "load",
        "settings",
        "cg_gallery",
        "music_room",
        "scene_replay",
        "flowchart",
    ]
    screen_size: tuple[int, int]
    scene_id: str | None = None
    dev_location: str | None = None
    locale: str | None = None
    text_expansion_factor: float | None = Field(
        default=None,
        ge=1.0,
        le=2.0,
    )
    dev_time: Literal[
        "morning", "noon", "afternoon", "evening", "night", "midnight"
    ] | None = None
    dev_flags: dict[str, Any] | None = None
    autoplay: float = Field(default=0.6, ge=0.0, le=60.0)
    settle_frames: int | None = Field(default=None, ge=1, le=3600)
    include_toasts: bool = False

    @field_validator("screen_size")
    @classmethod
    def _validate_screen_size(cls, value: tuple[int, int]) -> tuple[int, int]:
        if (
            len(value) != 2
            or any(isinstance(item, bool) or item <= 0 for item in value)
            or value[0] > 7680
            or value[1] > 4320
        ):
            raise ValueError("screen_size must be within 1x1 and 7680x4320")
        return value

    @field_validator("scene_id", "dev_location")
    @classmethod
    def _validate_optional_id(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("ids must be non-empty when supplied")
        return cleaned

    @field_validator("locale")
    @classmethod
    def _validate_locale(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return normalize_locale_tag(value)

    @field_validator("autoplay")
    @classmethod
    def _validate_finite_autoplay(cls, value: float) -> float:
        if not math.isfinite(value):
            raise ValueError("autoplay must be finite")
        return value


class VisualApproval(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["approved", "review_candidate", "rejected"]
    scenario_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    baseline_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    reviewer: str
    note: str
    approved_at_utc: datetime

    @field_validator("reviewer", "note")
    @classmethod
    def _validate_audit_text(cls, value: str, info: Any) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError(f"{info.field_name} must be a non-empty audit string")
        return cleaned

    @field_validator("approved_at_utc")
    @classmethod
    def _validate_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("approved_at_utc must include a timezone")
        return value.astimezone(timezone.utc)


class VisualApprovalManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[APPROVAL_SCHEMA_VERSION]
    scenario_set_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    engine_content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    pack_runtime_content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    scenarios: dict[str, VisualApproval]

    @field_validator("scenarios")
    @classmethod
    def _validate_keys(
        cls,
        value: dict[str, VisualApproval],
    ) -> dict[str, VisualApproval]:
        if any(
            not key
            or len(key) > 128
            or key.strip() != key
            or not ("a" <= key[0] <= "z" or key[0].isdigit())
            or any(
                not ("a" <= char <= "z" or char.isdigit() or char in "_-")
                for char in key
            )
            for key in value
        ):
            raise ValueError("approval scenario ids must be canonical names")
        return value


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _format_utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_utc(value: Any, *, label: str) -> datetime:
    if not isinstance(value, str):
        raise ProductionPolicyError(f"{label} must be an ISO-8601 string")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ProductionPolicyError(f"{label} is not valid ISO-8601") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ProductionPolicyError(f"{label} must include a timezone")
    return parsed.astimezone(timezone.utc)


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _stable_file_digest(
    path: Path,
    *,
    head_bytes: int = 0,
) -> tuple[str, bytes, int]:
    """Hash one regular file through a stable, non-symlink file descriptor."""
    try:
        path_before = path.stat(follow_symlinks=False)
    except OSError as exc:
        raise ProductionPolicyError(f"cannot inspect production input {path}: {exc}") from exc
    if not stat.S_ISREG(path_before.st_mode):
        raise ProductionPolicyError(f"production input is not a regular file: {path}")
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise ProductionPolicyError(f"cannot open production input {path}: {exc}") from exc
    digest = hashlib.sha256()
    head = bytearray()
    try:
        opened = os.fstat(descriptor)
        if (
            not stat.S_ISREG(opened.st_mode)
            or (opened.st_dev, opened.st_ino) != (path_before.st_dev, path_before.st_ino)
        ):
            raise ProductionPolicyError(f"production input changed before hashing: {path}")
        while True:
            chunk = os.read(descriptor, _HASH_CHUNK_BYTES)
            if not chunk:
                break
            digest.update(chunk)
            if len(head) < head_bytes:
                head.extend(chunk[: head_bytes - len(head)])
        opened_after = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    try:
        path_after = path.stat(follow_symlinks=False)
    except OSError as exc:
        raise ProductionPolicyError(f"production input changed while hashing: {path}") from exc
    identity_before = (
        path_before.st_dev,
        path_before.st_ino,
        path_before.st_size,
        path_before.st_mtime_ns,
        path_before.st_ctime_ns,
    )
    identity_opened = (
        opened.st_dev,
        opened.st_ino,
        opened.st_size,
        opened.st_mtime_ns,
        opened.st_ctime_ns,
    )
    identity_opened_after = (
        opened_after.st_dev,
        opened_after.st_ino,
        opened_after.st_size,
        opened_after.st_mtime_ns,
        opened_after.st_ctime_ns,
    )
    identity_after = (
        path_after.st_dev,
        path_after.st_ino,
        path_after.st_size,
        path_after.st_mtime_ns,
        path_after.st_ctime_ns,
    )
    if not (
        identity_before
        == identity_opened
        == identity_opened_after
        == identity_after
    ):
        raise ProductionPolicyError(f"file changed while hashing: {path}")
    return digest.hexdigest(), bytes(head), opened_after.st_size


def _sha256_file(path: Path) -> str:
    return _stable_file_digest(path)[0]


def _sha256_file_with_size(path: Path) -> tuple[str, int]:
    digest, _head, size = _stable_file_digest(path)
    return digest, size


def _png_file_evidence(path: Path) -> tuple[str, tuple[int, int]]:
    digest, header, _size = _stable_file_digest(path, head_bytes=24)
    return digest, _png_dimensions_from_header(header, path=path)


def _png_dimensions(path: Path) -> tuple[int, int]:
    """Read a PNG IHDR without importing the display/image stack."""
    _digest, header, _size = _stable_file_digest(path, head_bytes=24)
    return _png_dimensions_from_header(header, path=path)


def _png_dimensions_from_header(header: bytes, *, path: Path) -> tuple[int, int]:
    if (
        len(header) != 24
        or header[:8] != b"\x89PNG\r\n\x1a\n"
        or header[12:16] != b"IHDR"
    ):
        raise ProductionPolicyError(f"visual evidence is not a PNG: {path}")
    width, height = struct.unpack(">II", header[16:24])
    if width <= 0 or height <= 0:
        raise ProductionPolicyError(f"visual evidence has invalid dimensions: {path}")
    return width, height


def _canonical_json_bytes(payload: Any) -> bytes:
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _reject_json_constant(value: str) -> Any:
    raise ValueError(f"non-finite JSON constant is forbidden: {value}")


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _strict_json_loads(payload: str | bytes) -> Any:
    return json.loads(
        payload,
        object_pairs_hook=_strict_object,
        parse_constant=_reject_json_constant,
    )


def _canonical_digest(payload: Any) -> str:
    return _sha256_bytes(_canonical_json_bytes(payload))


def _artifact_digest(report: dict[str, Any]) -> str:
    payload = deepcopy(report)
    payload["artifact"] = {"canonical_content_sha256": None}
    return _canonical_digest(payload)


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temp_name = handle.name
            json.dump(
                payload,
                handle,
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
                allow_nan=False,
            )
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
        temp_name = None
    finally:
        if temp_name is not None:
            try:
                Path(temp_name).unlink()
            except OSError:
                pass


def _is_transient(
    relative: Path,
    *,
    excluded_top_level: set[str] | frozenset[str] = frozenset(),
) -> bool:
    if not relative.parts:
        return False
    if relative.parts[0] in _TRANSIENT_TOP_LEVEL or relative.parts[0] in excluded_top_level:
        return True
    return any(part in _TRANSIENT_ANYWHERE for part in relative.parts)


def _tree_snapshot(
    root: Path,
    *,
    excluded_top_level: set[str] | frozenset[str] = frozenset(),
) -> dict[str, Any]:
    root = root.expanduser().resolve()
    if not root.is_dir():
        raise ProductionPolicyError(f"snapshot root is not a directory: {root}")
    entries: list[tuple[str, int, str]] = []
    casefolded_paths: dict[str, str] = {}
    for path in sorted(root.rglob("*"), key=lambda item: item.as_posix()):
        relative = path.relative_to(root)
        if (
            _is_transient(relative, excluded_top_level=excluded_top_level)
            or path.name == ".DS_Store"
        ):
            continue
        if path.is_symlink():
            raise ProductionPolicyError(
                f"production inputs must not use symlinks: {relative.as_posix()}"
            )
        relative_name = relative.as_posix()
        collision_key = unicodedata.normalize("NFC", relative_name).casefold()
        previous = casefolded_paths.get(collision_key)
        if previous is not None and previous != relative_name:
            raise ProductionPolicyError(
                "production inputs contain a portable path collision: "
                f"{previous!r} and {relative_name!r}"
            )
        casefolded_paths[collision_key] = relative_name
        if path.is_dir():
            continue
        if not path.is_file():
            raise ProductionPolicyError(
                f"production input is not a regular file: {relative.as_posix()}"
            )
        digest, size = _sha256_file_with_size(path)
        entries.append((relative_name, size, digest))
    manifest = [
        {"path": relative, "bytes": size, "sha256": digest}
        for relative, size, digest in entries
    ]
    return {
        "root": str(root),
        "files": len(entries),
        "bytes": sum(size for _relative, size, _digest in entries),
        "manifest_sha256": _canonical_digest(manifest),
    }


def _untracked_input_paths(root: Path) -> list[str]:
    """Return production input files not present in the enclosing Git index."""
    root = root.resolve()
    revision = _git_revision(root)
    if revision.get("available") is not True or not revision.get("root"):
        return ["<git-unavailable>"]
    git_root = Path(revision["root"])
    try:
        prefix = root.relative_to(git_root)
    except ValueError:
        return ["<outside-git-root>"]
    try:
        completed = subprocess.run(
            [
                "git",
                "-C",
                str(git_root),
                "ls-files",
                "-z",
                "--cached",
                "--",
                prefix.as_posix(),
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            check=False,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return ["<git-ls-files-error>"]
    if completed.returncode != 0:
        return ["<git-ls-files-error>"]
    tracked = {
        item.decode("utf-8", errors="surrogateescape")
        for item in completed.stdout.split(b"\0")
        if item
    }
    missing: list[str] = []
    for path in sorted(root.rglob("*"), key=lambda item: item.as_posix()):
        relative = path.relative_to(root)
        if _is_transient(relative) or path.name == ".DS_Store" or path.is_dir():
            continue
        indexed_path = (prefix / relative).as_posix()
        if indexed_path not in tracked:
            missing.append(relative.as_posix())
    return missing


def _git_revision(path: Path) -> dict[str, Any]:
    def git(*args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["git", "-C", str(path), *args],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            check=False,
            timeout=5,
        )

    try:
        root_result = git("rev-parse", "--show-toplevel")
        if root_result.returncode != 0:
            return {
                "available": False,
                "root": None,
                "sha": None,
                "dirty": None,
                "status_sha256": None,
            }
        root = Path(root_result.stdout.strip()).resolve()
        sha_result = git("rev-parse", "HEAD")
        status_result = git("status", "--porcelain=v1", "--untracked-files=normal")
        status = status_result.stdout if status_result.returncode == 0 else None
        return {
            "available": sha_result.returncode == 0 and status is not None,
            "root": str(root),
            "sha": sha_result.stdout.strip() if sha_result.returncode == 0 else None,
            "dirty": bool(status.strip()) if status is not None else None,
            "status_sha256": (
                _sha256_bytes(status.encode("utf-8"))
                if status is not None
                else None
            ),
        }
    except (OSError, subprocess.SubprocessError):
        return {
            "available": False,
            "root": None,
            "sha": None,
            "dirty": None,
            "status_sha256": None,
        }


def _engine_source_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _report_path_conflict(
    report_path: Path,
    *,
    pack_root: Path,
) -> str | None:
    """Return the protected input root containing ``report_path``, if any."""
    roots: list[tuple[Path, str]] = [
        (pack_root, "pack input tree"),
        (_engine_source_root(), "engine package input tree"),
    ]
    for source_root, kind in tuple(roots):
        git = _git_revision(source_root)
        git_root = git.get("root") if isinstance(git, dict) else None
        if isinstance(git_root, str) and git_root:
            roots.append((Path(git_root).expanduser().resolve(), f"{kind} Git worktree"))
    seen: set[Path] = set()
    for protected_root, label in roots:
        protected_root = protected_root.expanduser().resolve()
        if protected_root in seen:
            continue
        seen.add(protected_root)
        try:
            report_path.relative_to(protected_root)
        except ValueError:
            continue
        return label
    return None


def _capture_subject(pack_root: Path) -> dict[str, Any]:
    engine_root = _engine_source_root()
    return {
        "engine": {
            "version": engine_version,
            "git": _git_revision(engine_root),
            "content": _tree_snapshot(engine_root),
        },
        "pack": {
            "root": str(pack_root),
            "git": _git_revision(pack_root),
            "content": _tree_snapshot(pack_root),
            "runtime_content": _tree_snapshot(
                pack_root,
                excluded_top_level={"visual_baselines"},
            ),
        },
    }


def _resolve_pack_path(pack_root: Path, relative: str, *, label: str) -> Path:
    normalized = _relative_pack_path(relative, label=label)
    target = (pack_root / normalized).resolve()
    try:
        target.relative_to(pack_root)
    except ValueError as exc:
        raise ProductionPolicyError(f"{label} resolves outside the pack") from exc
    return target


def load_production_policy(path: Path) -> tuple[ProductionPolicy, str]:
    try:
        raw_bytes = path.read_bytes()
    except OSError as exc:
        raise ProductionPolicyError(f"cannot read production policy {path}: {exc}") from exc
    try:
        raw = _strict_yaml_load(raw_bytes)
    except yaml.YAMLError as exc:
        raise ProductionPolicyError(f"invalid production policy YAML: {exc}") from exc
    try:
        policy = ProductionPolicy.model_validate(raw)
    except ValidationError as exc:
        raise ProductionPolicyError(f"invalid production policy: {exc}") from exc
    return policy, _sha256_bytes(raw_bytes)


def load_visual_scenarios(path: Path) -> tuple[list[dict[str, Any]], str]:
    try:
        raw_bytes = path.read_bytes()
        raw = _strict_json_loads(raw_bytes)
    except OSError as exc:
        raise ProductionPolicyError(f"cannot read visual scenarios {path}: {exc}") from exc
    except ValueError as exc:
        raise ProductionPolicyError(f"invalid visual scenario JSON: {exc}") from exc
    if not isinstance(raw, list) or not raw:
        raise ProductionPolicyError("visual scenario document must be a non-empty list")
    try:
        models = [ProductionVisualScenario.model_validate(item) for item in raw]
    except ValidationError as exc:
        raise ProductionPolicyError(f"invalid visual scenario: {exc}") from exc
    names = [model.name for model in models]
    if len(names) != len(set(names)):
        raise ProductionPolicyError("visual scenario names must be unique")
    normalized = [model.model_dump(mode="json", exclude_none=True) for model in models]
    matrix = {
        (item["view"], tuple(item["screen_size"]))
        for item in normalized
    }
    required = {
        (view, size)
        for view in _PRODUCTION_VIEWS
        for size in _SHIPPING_SIZES
    }
    missing = sorted(required - matrix)
    if missing:
        rendered = ", ".join(
            f"{view}@{size[0]}x{size[1]}" for view, size in missing
        )
        raise ProductionPolicyError(
            f"visual scenario matrix is missing required views: {rendered}"
        )
    return normalized, _sha256_bytes(raw_bytes)


def load_visual_approvals(path: Path) -> tuple[VisualApprovalManifest, str]:
    try:
        raw_bytes = path.read_bytes()
        raw = _strict_yaml_load(raw_bytes)
    except OSError as exc:
        raise ProductionPolicyError(f"cannot read visual approvals {path}: {exc}") from exc
    except yaml.YAMLError as exc:
        raise ProductionPolicyError(f"invalid visual approvals YAML: {exc}") from exc
    try:
        manifest = VisualApprovalManifest.model_validate(raw)
    except ValidationError as exc:
        raise ProductionPolicyError(f"invalid visual approvals: {exc}") from exc
    return manifest, _sha256_bytes(raw_bytes)


def _add_failure(
    report: dict[str, Any],
    code: str,
    message: str,
    **detail: Any,
) -> None:
    item: dict[str, Any] = {"code": code, "message": message}
    item.update(detail)
    report["failures"].append(item)


def _require_clean_subject(report: dict[str, Any], subject: dict[str, Any]) -> None:
    for kind in ("engine", "pack"):
        git = subject.get(kind, {}).get("git", {})
        if git.get("available") is not True:
            _add_failure(
                report,
                f"{kind}_git_unavailable",
                f"{kind} Git identity is required for production evidence",
            )
        elif git.get("dirty") is not False:
            _add_failure(
                report,
                f"{kind}_worktree_dirty",
                f"{kind} worktree must be clean for production evidence",
            )


def _self_check_failures(
    report: dict[str, Any],
    self_report: dict[str, Any],
    *,
    scenario_names: list[str],
) -> None:
    stages = self_report.get("stages")
    if not isinstance(stages, list):
        _add_failure(report, "self_check_shape", "self-check stages are missing")
        return
    names = [stage.get("name") for stage in stages if isinstance(stage, dict)]
    if names != list(_SELF_CHECK_STAGES):
        _add_failure(
            report,
            "self_check_stage_set",
            "self-check did not return the exact seven-stage production set",
            expected=list(_SELF_CHECK_STAGES),
            actual=names,
        )
    for expected in _SELF_CHECK_STAGES:
        matches = [
            stage for stage in stages
            if isinstance(stage, dict) and stage.get("name") == expected
        ]
        if len(matches) != 1:
            continue
        stage = matches[0]
        if stage.get("skipped") is not False:
            _add_failure(
                report,
                "self_check_stage_skipped",
                f"required self-check stage was skipped: {expected}",
                stage=expected,
            )
        if expected in {"schema", "refs"}:
            warnings = stage.get("details", {}).get("warnings")
            if not isinstance(warnings, list) or warnings:
                _add_failure(
                    report,
                    "self_check_warning",
                    f"production {expected} stage must contain zero warnings",
                    stage=expected,
                    warnings=warnings,
                )
        if stage.get("ok") is not True:
            _add_failure(
                report,
                "self_check_stage_failed",
                f"required self-check stage failed: {expected}",
                stage=expected,
            )
    reachability = next(
        (
            stage for stage in stages
            if isinstance(stage, dict) and stage.get("name") == "reachability"
        ),
        {},
    )
    unverified = reachability.get("details", {}).get("unverified")
    if not isinstance(unverified, list) or unverified:
        _add_failure(
            report,
            "reachability_unverified",
            "production reachability must contain zero unverified endings",
            unverified=unverified,
        )
    smoke = next(
        (
            stage for stage in stages
            if isinstance(stage, dict) and stage.get("name") == "smoke"
        ),
        {},
    )
    smoke_count = smoke.get("details", {}).get("count")
    if not isinstance(smoke_count, int) or smoke_count <= 0:
        _add_failure(
            report,
            "smoke_evidence_empty",
            "production smoke stage must execute at least one script",
        )
    visual = next(
        (
            stage for stage in stages
            if isinstance(stage, dict) and stage.get("name") == "visual"
        ),
        {},
    )
    visual_details = visual.get("details", {})
    if visual_details.get("created") != 0:
        _add_failure(
            report,
            "visual_baseline_created",
            "production-check must never create or promote a visual baseline",
        )
    visual_results = visual_details.get("results")
    actual_names = (
        [item.get("name") for item in visual_results if isinstance(item, dict)]
        if isinstance(visual_results, list)
        else []
    )
    if actual_names != scenario_names:
        _add_failure(
            report,
            "visual_scenario_result_set",
            "visual results do not match the pinned scenario document",
            expected=scenario_names,
            actual=actual_names,
        )


def _collect_visual_evidence(
    report: dict[str, Any],
    *,
    pack_root: Path,
    candidates_root: Path,
    scenarios: list[dict[str, Any]],
    approvals: VisualApprovalManifest,
    subject: dict[str, Any],
    now: datetime,
) -> dict[str, Any]:
    expected_names = [item["name"] for item in scenarios]
    scenario_set_sha = _canonical_digest(scenarios)
    engine_content_sha = subject["engine"]["content"]["manifest_sha256"]
    pack_runtime_sha = subject["pack"]["runtime_content"]["manifest_sha256"]
    binding_checks = {
        "scenario_set_sha256": (
            approvals.scenario_set_sha256,
            scenario_set_sha,
        ),
        "engine_content_sha256": (
            approvals.engine_content_sha256,
            engine_content_sha,
        ),
        "pack_runtime_content_sha256": (
            approvals.pack_runtime_content_sha256,
            pack_runtime_sha,
        ),
    }
    for field, (declared, actual) in binding_checks.items():
        if declared != actual:
            _add_failure(
                report,
                "visual_approval_source_mismatch",
                f"visual approval source binding is stale: {field}",
                field=field,
                declared=declared,
                actual=actual,
            )
    declared_names = list(approvals.scenarios)
    if set(declared_names) != set(expected_names):
        _add_failure(
            report,
            "visual_approval_set_mismatch",
            "approval manifest must exactly match the pinned scenarios",
            missing=sorted(set(expected_names) - set(declared_names)),
            stale=sorted(set(declared_names) - set(expected_names)),
        )
    rows: list[dict[str, Any]] = []
    baseline_dir = pack_root / "visual_baselines"
    authored_png_paths = {
        path.relative_to(baseline_dir).as_posix()
        for path in baseline_dir.rglob("*")
        if path.is_file() and path.suffix.lower() == ".png"
    }
    expected_png_paths = {f"{name}.png" for name in expected_names}
    unexpected_pngs = sorted(authored_png_paths - expected_png_paths)
    if unexpected_pngs:
        _add_failure(
            report,
            "visual_baseline_unlisted",
            "visual baseline directory contains unlisted PNG files",
            scenarios=unexpected_pngs,
        )
    for scenario in scenarios:
        name = scenario["name"]
        scenario_sha = _canonical_digest(scenario)
        baseline = pack_root / "visual_baselines" / f"{name}.png"
        candidate = candidates_root / f"{name}.png"
        baseline_sha: str | None = None
        candidate_sha: str | None = None
        baseline_dimensions: tuple[int, int] | None = None
        candidate_dimensions: tuple[int, int] | None = None
        if baseline.is_file():
            try:
                baseline_sha, baseline_dimensions = _png_file_evidence(baseline)
            except (OSError, ProductionPolicyError) as exc:
                _add_failure(
                    report,
                    "visual_baseline_invalid",
                    str(exc),
                    scenario=name,
                )
        if candidate.is_file():
            try:
                candidate_sha, candidate_dimensions = _png_file_evidence(candidate)
            except (OSError, ProductionPolicyError) as exc:
                _add_failure(
                    report,
                    "visual_candidate_invalid",
                    str(exc),
                    scenario=name,
                )
        approval = approvals.scenarios.get(name)
        row = {
            "name": name,
            "view": scenario["view"],
            "screen_size": scenario["screen_size"],
            "scenario_sha256": scenario_sha,
            "baseline": {
                "path": str(baseline),
                "exists": baseline_sha is not None,
                "sha256": baseline_sha,
                "dimensions": list(baseline_dimensions) if baseline_dimensions else None,
            },
            "candidate": {
                "path": f"<ephemeral>/{name}.png",
                "exists": candidate_sha is not None,
                "sha256": candidate_sha,
                "dimensions": list(candidate_dimensions) if candidate_dimensions else None,
            },
            "approval": (
                approval.model_dump(mode="json") if approval is not None else None
            ),
        }
        rows.append(row)
        if baseline_sha is None:
            _add_failure(
                report,
                "visual_baseline_missing",
                f"approved baseline PNG is missing: {name}",
                scenario=name,
            )
        if candidate_sha is None:
            _add_failure(
                report,
                "visual_candidate_missing",
                f"visual gate did not produce a candidate PNG: {name}",
                scenario=name,
            )
        if (
            baseline_sha is not None
            and candidate_sha is not None
            and baseline_sha != candidate_sha
        ):
            _add_failure(
                report,
                "visual_candidate_mismatch",
                f"candidate does not exactly match the approved baseline: {name}",
                scenario=name,
            )
        expected_dimensions = tuple(scenario["screen_size"])
        if (
            baseline_dimensions is not None
            and baseline_dimensions != expected_dimensions
        ):
            _add_failure(
                report,
                "visual_baseline_dimensions",
                f"baseline dimensions do not match the scenario: {name}",
                scenario=name,
                expected=list(expected_dimensions),
                actual=list(baseline_dimensions),
            )
        if (
            candidate_dimensions is not None
            and candidate_dimensions != expected_dimensions
        ):
            _add_failure(
                report,
                "visual_candidate_dimensions",
                f"candidate dimensions do not match the scenario: {name}",
                scenario=name,
                expected=list(expected_dimensions),
                actual=list(candidate_dimensions),
            )
        if approval is None:
            _add_failure(
                report,
                "visual_approval_missing",
                f"visual scenario has no approval record: {name}",
                scenario=name,
            )
            continue
        if approval.status != "approved":
            _add_failure(
                report,
                "visual_not_approved",
                f"visual scenario is not approved: {name}",
                scenario=name,
                status=approval.status,
            )
        if approval.scenario_sha256 != scenario_sha:
            _add_failure(
                report,
                "visual_scenario_digest_mismatch",
                f"approval refers to a different scenario definition: {name}",
                scenario=name,
            )
        if baseline_sha is not None and approval.baseline_sha256 != baseline_sha:
            _add_failure(
                report,
                "visual_baseline_digest_mismatch",
                f"approval refers to a different baseline PNG: {name}",
                scenario=name,
            )
        if approval.approved_at_utc > now + timedelta(minutes=5):
            _add_failure(
                report,
                "visual_approval_from_future",
                f"visual approval timestamp is in the future: {name}",
                scenario=name,
            )
    return {
        "bindings": {
            field: {"declared": declared, "actual": actual}
            for field, (declared, actual) in binding_checks.items()
        },
        "scenario_count": len(scenarios),
        "approved_count": sum(
            1
            for item in rows
            if item["approval"] is not None
            and item["approval"].get("status") == "approved"
        ),
        "scenarios": rows,
    }


def _verify_runtime_report(
    runtime_report: Any,
    *,
    expected_subject: dict[str, Any],
    expected_policy_sha256: str,
    expected_policy_document: dict[str, Any],
    started_at: datetime,
    finished_at: datetime,
) -> list[dict[str, Any]]:
    failures: list[dict[str, Any]] = []

    def fail(code: str, message: str, **detail: Any) -> None:
        item = {"code": code, "message": message}
        item.update(detail)
        failures.append(item)

    if not isinstance(runtime_report, dict):
        fail("runtime_report_shape", "runtime evidence is not a JSON object")
        return failures
    if runtime_report.get("schema") != RUNTIME_REPORT_SCHEMA:
        fail("runtime_report_schema", "runtime evidence schema is unsupported")
    artifact = runtime_report.get("artifact")
    if (
        not isinstance(artifact, dict)
        or set(artifact) != {"canonical_content_sha256"}
        or artifact.get("canonical_content_sha256") != _artifact_digest(runtime_report)
    ):
        fail("runtime_report_digest", "runtime evidence digest is missing or invalid")
    if runtime_report.get("ok") is not True or runtime_report.get("status") != "pass":
        fail("runtime_gate_failed", "runtime reliability gate did not pass")
    if runtime_report.get("failures") != []:
        fail("runtime_contains_failures", "passing runtime evidence contains failures")
    if runtime_report.get("warnings") != []:
        fail("runtime_contains_warnings", "passing runtime evidence contains warnings")
    claim = runtime_report.get("claim")
    if not isinstance(claim, dict):
        claim = {}
        fail("runtime_claim_shape", "runtime claim is missing or malformed")
    policy_info = runtime_report.get("policy")
    if not isinstance(policy_info, dict):
        policy_info = {}
        fail("runtime_policy_shape", "runtime policy evidence is missing or malformed")
    if policy_info.get("sha256") != expected_policy_sha256:
        fail("runtime_policy_mismatch", "runtime evidence used a different policy")
    if _canonical_digest(policy_info.get("document")) != _canonical_digest(
        expected_policy_document
    ):
        fail(
            "runtime_policy_document_mismatch",
            "runtime evidence does not contain the exact current normalized policy",
        )
    if claim.get("numeric_limits_enforced") is not True:
        fail(
            "runtime_numeric_limits_unenforced",
            "production runtime evidence must enforce calibrated numeric limits",
        )
    policy_document = policy_info.get("document")
    limits = (
        policy_document.get("limits")
        if isinstance(policy_document, dict)
        else None
    )
    if not isinstance(limits, dict):
        fail("runtime_policy_shape", "runtime policy limits are missing")
    else:
        disabled = sorted(
            key for key in _RUNTIME_REQUIRED_INVARIANTS
            if limits.get(key) is not True
        )
        if disabled:
            fail(
                "runtime_invariants_disabled",
                "production runtime policy disabled required invariants",
                fields=disabled,
            )
        unbounded = sorted(
            key for key in _RUNTIME_NUMERIC_LIMITS
            if limits.get(key) is None
        )
        if unbounded:
            fail(
                "runtime_numeric_limits_missing",
                "every production runtime numeric limit must be configured",
                fields=unbounded,
            )
        invalid_numeric = sorted(
            key
            for key in _RUNTIME_NUMERIC_LIMITS
            if (
                isinstance(limits.get(key), bool)
                or not isinstance(limits.get(key), (int, float))
                or not math.isfinite(float(limits[key]))
                or float(limits[key]) < 0
            )
        )
        if invalid_numeric:
            fail(
                "runtime_numeric_limits_invalid",
                "production runtime numeric limits must be finite non-negative numbers",
                fields=invalid_numeric,
            )
        max_bgm_slots = limits.get("max_bgm_slots")
        if (
            isinstance(max_bgm_slots, bool)
            or not isinstance(max_bgm_slots, int)
            or max_bgm_slots < 0
            or max_bgm_slots > 2
        ):
            fail(
                "runtime_bgm_limit_invalid",
                "production max_bgm_slots must be an integer between 0 and 2",
            )
    expected_fingerprint = (
        policy_document.get("calibrated_fingerprint")
        if isinstance(policy_document, dict)
        else None
    )
    if not isinstance(expected_fingerprint, dict) or not expected_fingerprint:
        fail(
            "runtime_fingerprint_missing",
            "production runtime policy requires a calibrated fingerprint",
        )
    else:
        fingerprint_fields = set(expected_fingerprint)
        missing_fingerprint = sorted(_RUNTIME_FINGERPRINT_FIELDS - fingerprint_fields)
        unexpected_fingerprint = sorted(fingerprint_fields - _RUNTIME_FINGERPRINT_FIELDS)
        if missing_fingerprint or unexpected_fingerprint:
            fail(
                "runtime_fingerprint_incomplete",
                "calibrated runtime fingerprint must contain the exact field set",
                missing=missing_fingerprint,
                unexpected=unexpected_fingerprint,
            )
    fingerprint_result = policy_info.get("fingerprint")
    if (
        not isinstance(fingerprint_result, dict)
        or _canonical_digest(fingerprint_result.get("expected"))
        != _canonical_digest(expected_fingerprint)
        or _canonical_digest(fingerprint_result.get("actual"))
        != _canonical_digest(expected_fingerprint)
        or fingerprint_result.get("matches") is not True
        or fingerprint_result.get("mismatches") != []
    ):
        fail(
            "runtime_fingerprint_mismatch",
            "runtime fingerprint did not exactly match its calibration",
        )
    checks = runtime_report.get("checks")
    if not isinstance(checks, list):
        fail("runtime_checks_missing", "runtime numeric checks are missing")
    else:
        check_ids = [
            item.get("id")
            for item in checks
            if isinstance(item, dict) and isinstance(item.get("id"), str)
        ]
        by_id = {
            item["id"]: item
            for item in checks
            if isinstance(item, dict) and isinstance(item.get("id"), str)
        }
        if (
            len(check_ids) != len(checks)
            or len(check_ids) != len(set(check_ids))
            or set(by_id) != _RUNTIME_NUMERIC_CHECKS
        ):
            fail(
                "runtime_check_set",
                "runtime numeric check set is incomplete or unexpected",
                expected=sorted(_RUNTIME_NUMERIC_CHECKS),
                actual=sorted(check_ids),
            )
        for check_id, check in by_id.items():
            if check.get("status") != "pass" or check.get("ok") is not True:
                fail(
                    "runtime_numeric_check_failed",
                    f"runtime numeric check did not pass: {check_id}",
                    check=check_id,
                )
            actual = check.get("actual")
            limit = check.get("limit")
            if (
                isinstance(actual, bool)
                or not isinstance(actual, (int, float))
                or not math.isfinite(float(actual))
                or float(actual) < 0
                or isinstance(limit, bool)
                or not isinstance(limit, (int, float))
                or not math.isfinite(float(limit))
                or float(limit) < 0
                or float(actual) > float(limit)
            ):
                fail(
                    "runtime_numeric_check_invalid",
                    f"runtime numeric check evidence is invalid: {check_id}",
                    check=check_id,
                )
    child_subject = runtime_report.get("subject")
    if not isinstance(child_subject, dict):
        child_subject = {}
        fail("runtime_subject_shape", "runtime subject is missing or malformed")
    for kind, child_key in (("engine", "engine_revision"), ("pack", "pack_revision")):
        expected_git = expected_subject.get(kind, {}).get("git", {})
        child_git = child_subject.get(child_key, {})
        if not isinstance(child_git, dict):
            child_git = {}
        for field in ("available", "root", "sha", "dirty"):
            if child_git.get(field) != expected_git.get(field):
                fail(
                    "runtime_subject_mismatch",
                    f"runtime {kind} identity differs from production-check",
                    subject=kind,
                    field=field,
                )
    try:
        generated = _parse_utc(
            runtime_report.get("generated_at_utc"),
            label="runtime generated_at_utc",
        )
    except ProductionPolicyError as exc:
        fail("runtime_timestamp_invalid", str(exc))
    else:
        if (
            generated < started_at - timedelta(seconds=5)
            or generated > finished_at + timedelta(seconds=5)
        ):
            fail("runtime_report_stale", "runtime evidence was not generated by this run")
    runs = runtime_report.get("runs")
    if not isinstance(runs, list) or not runs:
        fail("runtime_runs_missing", "runtime evidence contains no probe runs")
    else:
        profile = (
            policy_document.get("profile")
            if isinstance(policy_document, dict)
            else None
        )
        expected_runs = None
        cold_runs: Any = None
        soak_runs: Any = None
        if isinstance(profile, dict):
            cold_runs = profile.get("cold_runs")
            soak_runs = profile.get("soak_runs")
            if (
                isinstance(cold_runs, int)
                and not isinstance(cold_runs, bool)
                and isinstance(soak_runs, int)
                and not isinstance(soak_runs, bool)
            ):
                expected_runs = max(cold_runs, soak_runs)
        if expected_runs is None or len(runs) != expected_runs:
            fail(
                "runtime_run_count",
                "runtime probe count does not match the calibrated policy",
                expected=expected_runs,
                actual=len(runs),
            )
        for index, run in enumerate(runs):
            if not isinstance(run, dict):
                fail("runtime_run_shape", "runtime probe row is invalid", run=index)
                continue
            expected_mode = (
                "soak"
                if isinstance(soak_runs, int) and index < soak_runs
                else "startup_only"
            )
            if (
                run.get("run_index") != index + 1
                or run.get("ok") is not True
                or run.get("status") != "ok"
                or run.get("mode") != expected_mode
                or run.get("returncode") != 0
                or run.get("timed_out") is not False
                or run.get("spawn_error") is not None
                or run.get("controller_error") is not None
                or run.get("result_error") is not None
                or run.get("protocol_errors") != []
            ):
                fail(
                    "runtime_run_failed",
                    "runtime probe row is incomplete or unsuccessful",
                    run=index,
                )
            for stream_name in ("stdout", "stderr"):
                stream = run.get(stream_name)
                required_stream_fields = {
                    "bytes",
                    "sha256",
                    "tail",
                    "truncated",
                    "complete",
                    "termination",
                    "error",
                }
                if (
                    not isinstance(stream, dict)
                    or set(stream) != required_stream_fields
                    or isinstance(stream.get("bytes"), bool)
                    or not isinstance(stream.get("bytes"), int)
                    or stream.get("bytes", -1) < 0
                    or not isinstance(stream.get("sha256"), str)
                    or len(stream.get("sha256", "")) != 64
                    or any(
                        char not in "0123456789abcdef"
                        for char in stream.get("sha256", "")
                    )
                    or not isinstance(stream.get("tail"), str)
                    or not isinstance(stream.get("truncated"), bool)
                    or stream.get("complete") is not True
                    or stream.get("termination") != "eof"
                    or stream.get("error") is not None
                ):
                    fail(
                        "runtime_log_incomplete",
                        f"runtime {stream_name} evidence is incomplete",
                        run=index,
                        stream=stream_name,
                    )
    return failures


def _run_coverage_union(pack_root: Path, *, seed: int) -> dict[str, Any]:
    """Union observations from every production smoke script."""
    from ..config import EngineConfig
    from ..headless import HeadlessSession
    from .coverage import CoverageTracker

    tracker = CoverageTracker(pack_root)
    scripts = sorted((pack_root / "scripts").glob("test_*.json"))
    played: set[str] = set()
    transcript: list[dict[str, Any]] = []
    endings: set[str] = set()
    rows: list[dict[str, Any]] = []
    for script in scripts:
        raw_bytes = script.read_bytes()
        data = _strict_json_loads(raw_bytes)
        if isinstance(data, list):
            commands = data
        elif (
            isinstance(data, dict)
            and "commands" in data
            and set(data) <= {"commands", "_comment"}
            and ("_comment" not in data or isinstance(data["_comment"], str))
        ):
            commands = data["commands"]
        else:
            raise ProductionPolicyError(
                f"coverage script must be a command list or {{commands: [...]}}: {script}"
            )
        if not isinstance(commands, list) or not commands:
            raise ProductionPolicyError(f"coverage script has no commands: {script}")
        session = HeadlessSession.open(
            EngineConfig(seed=seed),
            pack=str(pack_root),
        )
        results = session.run_script(commands)
        errors: list[str] = []
        if len(results) != len(commands):
            errors.append(
                f"script produced {len(results)} results for {len(commands)} commands"
            )
        for index, result in enumerate(results):
            if not isinstance(result, dict):
                errors.append(f"command #{index} returned a non-object result")
            elif "error" in result:
                errors.append(f"command #{index}: {result['error']}")
            elif result.get("ok") is False:
                errors.append(f"command #{index} returned ok=false")
        played.update(session.state.story.played)
        transcript.extend(
            event for event in session.transcript if isinstance(event, dict)
        )
        endings.update(tracker._reached_endings(session))
        rows.append({
            "script": script.relative_to(pack_root).as_posix(),
            "sha256": _sha256_bytes(raw_bytes),
            "commands": len(commands),
            "ok": not errors,
            "errors": errors,
        })
    coverage = tracker.report_from(
        played=played,
        transcript=transcript,
        ending_ids=endings,
    ).model_dump(mode="json")
    return {
        "ok": bool(rows) and all(row["ok"] for row in rows),
        "seed": seed,
        "scripts": rows,
        "coverage": coverage,
    }


def _coverage_failures(
    report: dict[str, Any],
    coverage_report: dict[str, Any],
) -> None:
    if coverage_report.get("ok") is not True:
        _add_failure(
            report,
            "coverage_script_failed",
            "one or more coverage scripts failed or no scripts were found",
        )
    buckets = coverage_report.get("coverage")
    if not isinstance(buckets, dict):
        _add_failure(report, "coverage_shape", "coverage buckets are missing")
        return
    for name in ("scenes", "lines", "choices", "endings"):
        bucket = buckets.get(name)
        if not isinstance(bucket, dict):
            _add_failure(
                report,
                "coverage_bucket_missing",
                f"coverage bucket is missing: {name}",
                bucket=name,
            )
            continue
        if bucket.get("total", 0) <= 0:
            _add_failure(
                report,
                "coverage_empty_dimension",
                f"production pack declares no {name}",
                bucket=name,
            )
        if bucket.get("pct") != 100.0 or bucket.get("missing") != []:
            _add_failure(
                report,
                "coverage_below_threshold",
                f"production {name} coverage must be 100%",
                bucket=name,
                actual=bucket.get("pct"),
                missing=bucket.get("missing"),
            )


def _run_narrative_contract(
    pack_root: Path,
    *,
    contract_path: Path,
    seed: int,
) -> dict[str, Any]:
    from .contract import ContractChecker, Expectation, NarrativeContract

    raw = _strict_yaml_load(contract_path.read_bytes())
    if isinstance(raw, dict):
        if set(raw) != {"expectations"}:
            raise ProductionPolicyError(
                "production narrative contract must contain only expectations"
            )
        raw = raw["expectations"]
    if not isinstance(raw, list):
        raise ProductionPolicyError(
            "production narrative contract must be a list of expectations"
        )
    contract = NarrativeContract(
        expectations=[Expectation.model_validate(item) for item in raw]
    )
    if not contract.expectations:
        raise ProductionPolicyError(
            f"production narrative contract is empty: {contract_path}"
        )
    result = ContractChecker(str(pack_root), seed=seed).check(contract)
    result["contract"] = contract_path.relative_to(pack_root).as_posix()
    result["sha256"] = _sha256_file(contract_path)
    result["seed"] = seed
    return result


def _visual_evidence_failures(
    evidence: Any,
    *,
    scenarios: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    failures: list[dict[str, Any]] = []

    def fail(code: str, message: str, **detail: Any) -> None:
        item = {"code": code, "message": message}
        item.update(detail)
        failures.append(item)

    if not isinstance(evidence, dict):
        fail("visual_evidence_shape", "visual approval evidence is missing")
        return failures
    bindings = evidence.get("bindings")
    required_bindings = {
        "scenario_set_sha256",
        "engine_content_sha256",
        "pack_runtime_content_sha256",
    }
    if (
        not isinstance(bindings, dict)
        or set(bindings) != required_bindings
        or any(
            not isinstance(item, dict)
            or set(item) != {"declared", "actual"}
            or item.get("declared") != item.get("actual")
            for item in bindings.values()
        )
    ):
        fail("visual_source_binding", "visual approval source bindings do not match")
    rows = evidence.get("scenarios")
    if not isinstance(rows, list) or not rows:
        fail("visual_rows_missing", "visual approval evidence has no scenarios")
        return failures
    names = [row.get("name") for row in rows if isinstance(row, dict)]
    if len(names) != len(rows) or len(names) != len(set(names)):
        fail("visual_rows_duplicate", "visual evidence scenario names are invalid or duplicated")
    if evidence.get("scenario_count") != len(rows):
        fail("visual_count_mismatch", "visual evidence scenario count is inconsistent")
    if evidence.get("approved_count") != len(rows):
        fail("visual_approval_count", "not every visual scenario is approved")
    matrix: set[tuple[str, tuple[int, int]]] = set()
    expected_by_name = {
        item["name"]: item for item in (scenarios or [])
    }
    for row in rows:
        if not isinstance(row, dict):
            continue
        name = row.get("name")
        view = row.get("view")
        size = row.get("screen_size")
        if (
            not isinstance(view, str)
            or not isinstance(size, list)
            or len(size) != 2
            or any(isinstance(value, bool) or not isinstance(value, int) for value in size)
        ):
            fail("visual_row_shape", "visual scenario view or screen size is invalid", scenario=name)
            continue
        matrix.add((view, (size[0], size[1])))
        baseline = row.get("baseline")
        candidate = row.get("candidate")
        approval = row.get("approval")
        if (
            not isinstance(baseline, dict)
            or baseline.get("exists") is not True
            or not isinstance(candidate, dict)
            or candidate.get("exists") is not True
            or baseline.get("sha256") != candidate.get("sha256")
            or baseline.get("dimensions") != size
            or candidate.get("dimensions") != size
        ):
            fail(
                "visual_pixel_evidence",
                "baseline/candidate digest or dimensions are inconsistent",
                scenario=name,
            )
        if (
            not isinstance(approval, dict)
            or approval.get("status") != "approved"
            or approval.get("scenario_sha256") != row.get("scenario_sha256")
            or approval.get("baseline_sha256") != (
                baseline.get("sha256") if isinstance(baseline, dict) else None
            )
        ):
            fail(
                "visual_approval_semantics",
                "visual approval does not bind the scenario and baseline",
                scenario=name,
            )
        expected = expected_by_name.get(name)
        if expected is not None and (
            row.get("scenario_sha256") != _canonical_digest(expected)
            or view != expected["view"]
            or size != expected["screen_size"]
        ):
            fail(
                "visual_scenario_current_mismatch",
                "visual evidence differs from the current pinned scenario",
                scenario=name,
            )
    required = {
        (view, size)
        for view in _PRODUCTION_VIEWS
        for size in _SHIPPING_SIZES
    }
    if not required.issubset(matrix):
        fail(
            "visual_required_matrix",
            "visual evidence omits a required view or shipping resolution",
            missing=[
                {"view": view, "screen_size": list(size)}
                for view, size in sorted(required - matrix)
            ],
        )
    if scenarios is not None and set(names) != set(expected_by_name):
        fail(
            "visual_current_set_mismatch",
            "visual evidence does not match the current scenario document",
        )
    return failures


def _base_report(
    *,
    pack_root: Path,
    policy_path: Path,
    report_path: Path,
    generated_at: datetime,
) -> dict[str, Any]:
    return {
        "schema": REPORT_SCHEMA,
        "ok": False,
        "status": "error",
        "generated_at_utc": _format_utc(generated_at),
        "finished_at_utc": None,
        "valid_until_utc": None,
        "duration_ms": None,
        "claim": {
            "scope": CLAIM_SCOPE,
            "statement": (
                "Automated source-release preflight only; excludes packaged launch, "
                "target-device performance, real peripherals/audio, accessibility "
                "sessions, legal clearance, and independent reviewer identity proof."
            ),
        },
        "invocation": {
            "pack_root": str(pack_root),
            "policy_path": str(policy_path),
            "report_path": str(report_path),
            "commands": [],
        },
        "environment": {
            "system": platform.system(),
            "release": platform.release(),
            "machine": platform.machine(),
            "python": platform.python_version(),
            "executable": sys.executable,
        },
        "policy": {
            "relative_path": None,
            "sha256": None,
            "document": None,
            "visual_scenarios_sha256": None,
            "visual_approvals_sha256": None,
            "runtime_policy_sha256": None,
        },
        "subject": {"before": None, "after": None},
        "assets": [],
        "visual_evidence": {},
        "gates": {},
        "failures": [],
        "warnings": [],
        "artifact": {"canonical_content_sha256": None},
    }


def run_production_check(
    pack_root: Path,
    *,
    policy_path: Path,
    report_path: Path,
    _now: Callable[[], datetime] = _utc_now,
) -> dict[str, Any]:
    """Run every source-release gate and always attempt one atomic report."""

    started_ns = time.monotonic_ns()
    generated_at = _now().astimezone(timezone.utc)
    pack_root = pack_root.expanduser().resolve()
    policy_path = policy_path.expanduser().resolve()
    report_path = report_path.expanduser().resolve()
    report = _base_report(
        pack_root=pack_root,
        policy_path=policy_path,
        report_path=report_path,
        generated_at=generated_at,
    )
    controller_error = False
    unsafe_report_label = _report_path_conflict(
        report_path,
        pack_root=pack_root,
    )
    if unsafe_report_label is not None:
        _add_failure(
            report,
            "unsafe_report_path",
            f"production report must be outside the {unsafe_report_label}",
        )
        _add_failure(
            report,
            "required_gate_set",
            "production-check did not execute the required gates",
        )
        report["duration_ms"] = (time.monotonic_ns() - started_ns) / 1_000_000
        report["finished_at_utc"] = _format_utc(_now().astimezone(timezone.utc))
        report["status"] = "fail"
        report["artifact"]["canonical_content_sha256"] = _artifact_digest(report)
        return report
    try:
        if not (pack_root / "content").is_dir():
            raise ProductionPolicyError(f"pack has no content directory: {pack_root}")
        try:
            policy_relative = policy_path.relative_to(pack_root).as_posix()
        except ValueError as exc:
            raise ProductionPolicyError(
                "production policy must be stored inside the pack"
            ) from exc
        before = _capture_subject(pack_root)
        report["subject"]["before"] = before
        _require_clean_subject(report, before)
        for kind, root in (
            ("engine", _engine_source_root()),
            ("pack", pack_root),
        ):
            untracked = _untracked_input_paths(root)
            if untracked:
                _add_failure(
                    report,
                    f"{kind}_inputs_untracked",
                    f"{kind} production inputs must all be Git-tracked",
                    paths=untracked[:100],
                    omitted=max(0, len(untracked) - 100),
                )
        policy, policy_sha = load_production_policy(policy_path)
        report["policy"].update(
            {
                "relative_path": policy_relative,
                "sha256": policy_sha,
                "document": policy.model_dump(mode="json"),
            }
        )
        report["valid_until_utc"] = _format_utc(
            generated_at + timedelta(seconds=policy.max_evidence_age_seconds)
        )

        scenarios_path = _resolve_pack_path(
            pack_root,
            policy.visual.scenarios_path,
            label="visual.scenarios_path",
        )
        approvals_path = _resolve_pack_path(
            pack_root,
            policy.visual.approvals_path,
            label="visual.approvals_path",
        )
        runtime_policy_path = _resolve_pack_path(
            pack_root,
            policy.runtime.policy_path,
            label="runtime.policy_path",
        )
        contract_path = _resolve_pack_path(
            pack_root,
            policy.contract.path,
            label="contract.path",
        )
        scenarios, scenarios_sha = load_visual_scenarios(scenarios_path)
        approvals, approvals_sha = load_visual_approvals(approvals_path)
        try:
            runtime_policy_document, runtime_policy_sha = load_runtime_policy(
                runtime_policy_path
            )
        except RuntimePolicyError as exc:
            raise ProductionPolicyError(
                f"invalid runtime policy {runtime_policy_path}: {exc}"
            ) from exc
        report["policy"].update(
            {
                "visual_scenarios_sha256": scenarios_sha,
                "visual_approvals_sha256": approvals_sha,
                "runtime_policy_sha256": runtime_policy_sha,
            }
        )

        for relative in policy.required_asset_roots:
            asset_root = _resolve_pack_path(
                pack_root,
                relative,
                label="required_asset_roots",
            )
            try:
                snapshot = _tree_snapshot(asset_root)
            except ProductionPolicyError as exc:
                _add_failure(report, "asset_root_invalid", str(exc), root=relative)
                continue
            snapshot["relative_path"] = relative
            report["assets"].append(snapshot)
            if snapshot["files"] == 0:
                _add_failure(
                    report,
                    "asset_root_empty",
                    f"required asset root contains no files: {relative}",
                    root=relative,
                )

        scenario_names = [item["name"] for item in scenarios]
        self_command = [
            "world-gal-game",
            "self-check",
            str(pack_root),
            "--no-stop-on-failure",
            "--strict-visual-baselines",
            "--visual-scenarios",
            str(scenarios_path),
            "--smoke-seed",
            str(policy.coverage.seed),
            "--format",
            "json",
        ]
        report["invocation"]["commands"].append(self_command)
        self_failure_start = len(report["failures"])
        self_started_at = _now().astimezone(timezone.utc)
        self_started = time.monotonic_ns()
        with tempfile.TemporaryDirectory(prefix="wgg-production-visual-") as visual_raw:
            candidates_root = Path(visual_raw) / "candidates"
            try:
                self_result = SelfCheck(
                    pack_root,
                    stop_on_failure=False,
                    skip_smoke=False,
                    skip_visual=False,
                    strict_visual_baselines=True,
                    visual_scenarios=scenarios,
                    visual_baselines_dir=pack_root / "visual_baselines",
                    visual_candidates_dir=candidates_root,
                    smoke_seed=policy.coverage.seed,
                    skip_softlock=False,
                    skip_reachability=False,
                ).run().to_dict()
                self_complete = True
            except BaseException as exc:  # noqa: BLE001 - report must survive gate bugs
                self_result = {"error": f"{type(exc).__name__}: {exc}"}
                self_complete = False
                _add_failure(
                    report,
                    "self_check_error",
                    f"self-check raised {type(exc).__name__}: {exc}",
                )
            self_finished_at = _now().astimezone(timezone.utc)
            report["gates"]["self_check"] = {
                "command": self_command,
                "started_at_utc": _format_utc(self_started_at),
                "finished_at_utc": _format_utc(self_finished_at),
                "duration_ms": (time.monotonic_ns() - self_started) / 1_000_000,
                "complete": self_complete,
                "ok": bool(self_complete and self_result.get("ok") is True),
                "structured_evidence_sha256": _canonical_digest(self_result),
                "report": self_result,
            }
            if self_complete:
                _self_check_failures(
                    report,
                    self_result,
                    scenario_names=scenario_names,
                )
            report["gates"]["self_check"]["ok"] = bool(
                self_complete
                and self_result.get("ok") is True
                and len(report["failures"]) == self_failure_start
            )

            visual_failure_start = len(report["failures"])
            visual_started_ns = time.monotonic_ns()
            report["visual_evidence"] = _collect_visual_evidence(
                report,
                pack_root=pack_root,
                candidates_root=candidates_root,
                scenarios=scenarios,
                approvals=approvals,
                subject=before,
                now=self_finished_at,
            )
            visual_approval_command = [
                "world-gal-game",
                "production-check",
                str(pack_root),
                "--policy",
                str(policy_path),
                "--report",
                str(report_path),
            ]
            report["invocation"]["commands"].append(visual_approval_command)
            report["gates"]["visual_approval"] = {
                "command": visual_approval_command,
                "started_at_utc": _format_utc(self_finished_at),
                "finished_at_utc": _format_utc(_now().astimezone(timezone.utc)),
                "duration_ms": (time.monotonic_ns() - visual_started_ns) / 1_000_000,
                "complete": True,
                "ok": len(report["failures"]) == visual_failure_start,
                "structured_evidence_sha256": _canonical_digest(
                    report["visual_evidence"]
                ),
                "report": report["visual_evidence"],
            }

        coverage_command = [
            "world-gal-game",
            "coverage",
            str(pack_root),
            "--all-scripts",
            "--seed",
            str(policy.coverage.seed),
            "--format",
            "json",
        ]
        report["invocation"]["commands"].append(coverage_command)
        coverage_started_at = _now().astimezone(timezone.utc)
        coverage_started_ns = time.monotonic_ns()
        coverage_failure_start = len(report["failures"])
        try:
            coverage_result = _run_coverage_union(
                pack_root,
                seed=policy.coverage.seed,
            )
            coverage_complete = True
            _coverage_failures(report, coverage_result)
        except BaseException as exc:  # noqa: BLE001 - preserve release evidence
            coverage_result = {"error": f"{type(exc).__name__}: {exc}"}
            coverage_complete = False
            _add_failure(
                report,
                "coverage_error",
                f"coverage union raised {type(exc).__name__}: {exc}",
            )
        coverage_finished_at = _now().astimezone(timezone.utc)
        report["gates"]["coverage"] = {
            "command": coverage_command,
            "started_at_utc": _format_utc(coverage_started_at),
            "finished_at_utc": _format_utc(coverage_finished_at),
            "duration_ms": (time.monotonic_ns() - coverage_started_ns) / 1_000_000,
            "complete": coverage_complete,
            "ok": bool(
                coverage_complete
                and coverage_result.get("ok") is True
                and len(report["failures"]) == coverage_failure_start
            ),
            "structured_evidence_sha256": _canonical_digest(coverage_result),
            "report": coverage_result,
        }

        contract_command = [
            "world-gal-game",
            "contract",
            str(pack_root),
            "--contract",
            str(contract_path),
            "--seed",
            str(policy.contract.seed),
            "--format",
            "json",
        ]
        report["invocation"]["commands"].append(contract_command)
        contract_started_at = _now().astimezone(timezone.utc)
        contract_started_ns = time.monotonic_ns()
        try:
            contract_result = _run_narrative_contract(
                pack_root,
                contract_path=contract_path,
                seed=policy.contract.seed,
            )
            contract_complete = True
            if contract_result.get("ok") is not True or contract_result.get("total", 0) <= 0:
                _add_failure(
                    report,
                    "narrative_contract_failed",
                    "production narrative contract is empty or did not pass",
                )
        except BaseException as exc:  # noqa: BLE001 - preserve release evidence
            contract_result = {"error": f"{type(exc).__name__}: {exc}"}
            contract_complete = False
            _add_failure(
                report,
                "narrative_contract_error",
                f"narrative contract raised {type(exc).__name__}: {exc}",
            )
        contract_finished_at = _now().astimezone(timezone.utc)
        report["gates"]["contract"] = {
            "command": contract_command,
            "started_at_utc": _format_utc(contract_started_at),
            "finished_at_utc": _format_utc(contract_finished_at),
            "duration_ms": (time.monotonic_ns() - contract_started_ns) / 1_000_000,
            "complete": contract_complete,
            "ok": bool(
                contract_complete
                and contract_result.get("ok") is True
                and contract_result.get("total", 0) > 0
            ),
            "structured_evidence_sha256": _canonical_digest(contract_result),
            "report": contract_result,
        }

        runtime_command = [
            "world-gal-game",
            "runtime-check",
            str(pack_root),
            "--policy",
            str(runtime_policy_path),
            "--report",
            "<aggregate-temporary-runtime-report>",
            "--format",
            "json",
        ]
        report["invocation"]["commands"].append(runtime_command)
        runtime_failure_start = len(report["failures"])
        runtime_started_at = _now().astimezone(timezone.utc)
        runtime_started_ns = time.monotonic_ns()
        runtime_evidence: dict[str, Any] | None = None
        with tempfile.TemporaryDirectory(prefix="wgg-production-check-") as temp_raw:
            runtime_report_path = Path(temp_raw) / "runtime-report.json"
            try:
                run_runtime_check(
                    pack_root,
                    policy_path=runtime_policy_path,
                    report_path=runtime_report_path,
                )
                runtime_evidence = _strict_json_loads(
                    runtime_report_path.read_text(encoding="utf-8")
                )
                runtime_complete = True
            except BaseException as exc:  # noqa: BLE001 - report must survive gate bugs
                runtime_complete = False
                _add_failure(
                    report,
                    "runtime_check_error",
                    f"runtime-check evidence unavailable: {type(exc).__name__}: {exc}",
                )
        runtime_finished_at = _now().astimezone(timezone.utc)
        report["gates"]["runtime_check"] = {
            "command": runtime_command,
            "started_at_utc": _format_utc(runtime_started_at),
            "finished_at_utc": _format_utc(runtime_finished_at),
            "duration_ms": (time.monotonic_ns() - runtime_started_ns) / 1_000_000,
            "complete": runtime_complete,
            "ok": bool(
                runtime_complete
                and isinstance(runtime_evidence, dict)
                and runtime_evidence.get("ok") is True
            ),
            "structured_evidence_sha256": (
                _canonical_digest(runtime_evidence)
                if runtime_evidence is not None
                else None
            ),
            "report": runtime_evidence,
        }
        if runtime_complete:
            for failure in _verify_runtime_report(
                runtime_evidence,
                expected_subject=before,
                expected_policy_sha256=runtime_policy_sha,
                expected_policy_document=runtime_policy_document,
                started_at=runtime_started_at,
                finished_at=runtime_finished_at,
            ):
                _add_failure(
                    report,
                    failure.pop("code"),
                    failure.pop("message"),
                    **failure,
                )
        report["gates"]["runtime_check"]["ok"] = bool(
            runtime_complete
            and isinstance(runtime_evidence, dict)
            and runtime_evidence.get("ok") is True
            and len(report["failures"]) == runtime_failure_start
        )

        verification_inputs = {
            "production_policy": (policy_path, policy_sha),
            "visual_scenarios": (scenarios_path, scenarios_sha),
            "visual_approvals": (approvals_path, approvals_sha),
            "runtime_policy": (runtime_policy_path, runtime_policy_sha),
            "narrative_contract": (
                contract_path,
                contract_result.get("sha256")
                if isinstance(contract_result, dict)
                else None,
            ),
        }
        for label, (path, expected_digest) in verification_inputs.items():
            try:
                current_digest = _sha256_file(path)
            except ProductionPolicyError as exc:
                _add_failure(
                    report,
                    "verification_input_changed",
                    str(exc),
                    input=label,
                )
                continue
            if (
                not isinstance(expected_digest, str)
                or current_digest != expected_digest
            ):
                _add_failure(
                    report,
                    "verification_input_changed",
                    f"verification input changed during production-check: {label}",
                    input=label,
                )

        after = _capture_subject(pack_root)
        report["subject"]["after"] = after
        if after != before:
            _add_failure(
                report,
                "subject_changed_during_run",
                "engine or pack inputs changed while production-check was running",
            )
    except ProductionPolicyError as exc:
        _add_failure(report, "production_policy_error", str(exc))
    except BaseException as exc:  # noqa: BLE001 - artifact must survive controller bugs
        controller_error = True
        _add_failure(
            report,
            "controller_error",
            f"{type(exc).__name__}: {exc}",
        )

    required_gates = {
        "self_check",
        "visual_approval",
        "coverage",
        "contract",
        "runtime_check",
    }
    if set(report["gates"]) != required_gates:
        _add_failure(
            report,
            "required_gate_set",
            "production-check did not execute the exact required gate set",
            expected=sorted(required_gates),
            actual=sorted(report["gates"]),
        )
    report["duration_ms"] = (time.monotonic_ns() - started_ns) / 1_000_000
    report["finished_at_utc"] = _format_utc(_now().astimezone(timezone.utc))
    report["ok"] = not report["failures"]
    report["status"] = (
        "pass" if report["ok"] else "error" if controller_error else "fail"
    )
    try:
        report["artifact"]["canonical_content_sha256"] = _artifact_digest(report)
        _atomic_write_json(report_path, report)
    except (OSError, TypeError, ValueError) as exc:
        report["ok"] = False
        report["status"] = "error"
        _add_failure(
            report,
            "artifact_write_error",
            f"cannot write production report {report_path}: {exc}",
        )
        report["artifact"]["canonical_content_sha256"] = _artifact_digest(report)
    return report


def _verify_production_report_impl(
    report_path: Path,
    *,
    pack_root: Path,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Verify one saved production report against the current source inputs."""

    report_path = report_path.expanduser().resolve()
    pack_root = pack_root.expanduser().resolve()
    failures: list[dict[str, Any]] = []

    def fail(code: str, message: str, **detail: Any) -> None:
        item = {"code": code, "message": message}
        item.update(detail)
        failures.append(item)

    try:
        raw_bytes = report_path.read_bytes()
        report = _strict_json_loads(raw_bytes)
    except (OSError, ValueError) as exc:
        return {
            "ok": False,
            "failures": [{
                "code": "report_unreadable",
                "message": f"cannot read production report: {exc}",
            }],
        }
    if not isinstance(report, dict):
        return {
            "ok": False,
            "failures": [{
                "code": "report_shape",
                "message": "production report must be a JSON object",
            }],
        }
    unknown = sorted(set(report) - _REPORT_TOP_LEVEL_KEYS)
    missing = sorted(_REPORT_TOP_LEVEL_KEYS - set(report))
    if unknown or missing:
        fail(
            "report_top_level_shape",
            "production report top-level fields do not match schema v1",
            missing=missing,
            unknown=unknown,
        )
    if report.get("schema") != REPORT_SCHEMA:
        fail("report_schema", "production report schema is unsupported")
    artifact = report.get("artifact")
    if (
        not isinstance(artifact, dict)
        or set(artifact) != {"canonical_content_sha256"}
        or artifact.get("canonical_content_sha256") != _artifact_digest(report)
    ):
        fail("report_digest", "production report digest is missing or invalid")
    if report.get("ok") is not True or report.get("status") != "pass":
        fail("report_not_pass", "production report does not contain a passing claim")
    if report.get("failures") != []:
        fail("report_contains_failures", "passing report contains failure entries")
    if report.get("warnings") != []:
        fail("report_contains_warnings", "passing production report contains warnings")
    claim = report.get("claim")
    if not isinstance(claim, dict) or claim.get("scope") != CLAIM_SCOPE:
        fail("report_claim_scope", "production report claim scope is invalid")
    report_duration = report.get("duration_ms")
    if (
        isinstance(report_duration, bool)
        or not isinstance(report_duration, (int, float))
        or not math.isfinite(float(report_duration))
        or report_duration < 0
    ):
        fail("report_duration", "production report duration is invalid")

    current_time = (now or _utc_now()).astimezone(timezone.utc)
    try:
        generated_at = _parse_utc(
            report.get("generated_at_utc"),
            label="generated_at_utc",
        )
        finished_at = _parse_utc(
            report.get("finished_at_utc"),
            label="finished_at_utc",
        )
        valid_until = _parse_utc(
            report.get("valid_until_utc"),
            label="valid_until_utc",
        )
    except ProductionPolicyError as exc:
        fail("report_timestamp", str(exc))
        generated_at = finished_at = valid_until = current_time
    if generated_at > current_time + timedelta(minutes=5):
        fail("report_from_future", "production report timestamp is in the future")
    if current_time > valid_until:
        fail("report_stale", "production report has expired")
    if finished_at < generated_at or finished_at > current_time + timedelta(minutes=5):
        fail("report_finish_time", "production report finish timestamp is invalid")

    required_gates = {
        "self_check",
        "visual_approval",
        "coverage",
        "contract",
        "runtime_check",
    }
    gates = report.get("gates")
    invocation = report.get("invocation")
    command_ledger = (
        invocation.get("commands")
        if isinstance(invocation, dict)
        else None
    )
    if not isinstance(command_ledger, list):
        fail("invocation_shape", "production invocation command ledger is malformed")
        command_ledger = []
    if not isinstance(gates, dict) or set(gates) != required_gates:
        fail("required_gate_set", "production report is missing a required gate")
    else:
        for name in sorted(required_gates):
            gate = gates[name]
            if (
                not isinstance(gate, dict)
                or gate.get("complete") is not True
                or gate.get("ok") is not True
            ):
                fail("gate_not_pass", f"production gate is incomplete or failed: {name}")
                continue
            if set(gate) != _GATE_KEYS:
                fail(
                    "gate_shape",
                    f"gate fields do not match schema v1: {name}",
                    missing=sorted(_GATE_KEYS - set(gate)),
                    unknown=sorted(set(gate) - _GATE_KEYS),
                )
            try:
                gate_started = _parse_utc(
                    gate.get("started_at_utc"),
                    label=f"{name}.started_at_utc",
                )
                gate_finished = _parse_utc(
                    gate.get("finished_at_utc"),
                    label=f"{name}.finished_at_utc",
                )
            except ProductionPolicyError as exc:
                fail("gate_timestamp", str(exc), gate=name)
            else:
                if (
                    gate_started < generated_at - timedelta(seconds=5)
                    or gate_finished < gate_started
                    or gate_finished > finished_at + timedelta(seconds=5)
                ):
                    fail(
                        "gate_timestamp_range",
                        f"gate timestamps are outside the production run: {name}",
                    )
            duration = gate.get("duration_ms")
            if (
                isinstance(duration, bool)
                or not isinstance(duration, (int, float))
                or not math.isfinite(float(duration))
                or duration < 0
            ):
                fail("gate_duration", f"gate duration is invalid: {name}")
            gate_report = gate.get("report")
            if gate.get("structured_evidence_sha256") != _canonical_digest(gate_report):
                fail(
                    "gate_evidence_digest",
                    f"structured gate evidence digest is invalid: {name}",
                )
            if gate.get("command") not in command_ledger:
                fail(
                    "gate_command_missing",
                    f"gate command is absent from the invocation ledger: {name}",
                )
        expected_commands = [
            gates[name].get("command")
            for name in (
                "self_check",
                "visual_approval",
                "coverage",
                "contract",
                "runtime_check",
            )
            if isinstance(gates.get(name), dict)
        ]
        if command_ledger != expected_commands:
            fail(
                "command_ledger_mismatch",
                "production invocation ledger does not exactly match the five gates",
            )
        visual_gate = gates.get("visual_approval")
        visual_gate_report = (
            visual_gate.get("report")
            if isinstance(visual_gate, dict)
            else None
        )
        if visual_gate_report != report.get("visual_evidence"):
            fail(
                "visual_gate_evidence_mismatch",
                "visual gate evidence differs from the aggregate visual evidence",
            )
        self_gate = gates.get("self_check", {})
        self_payload = self_gate.get("report")
        if isinstance(self_payload, dict):
            temp = {"failures": []}
            visual_rows = report.get("visual_evidence", {}).get("scenarios", [])
            scenario_names = [
                row.get("name") for row in visual_rows if isinstance(row, dict)
            ]
            _self_check_failures(
                temp,
                self_payload,
                scenario_names=scenario_names,
            )
            for item in temp["failures"]:
                fail(
                    "self_check_not_strict",
                    item["message"],
                    source_code=item["code"],
                )
        else:
            fail("self_check_shape", "saved self-check evidence is missing")
        coverage_payload = gates.get("coverage", {}).get("report")
        if isinstance(coverage_payload, dict):
            temp = {"failures": []}
            _coverage_failures(temp, coverage_payload)
            for item in temp["failures"]:
                fail(
                    "coverage_not_strict",
                    item["message"],
                    source_code=item["code"],
                )
        else:
            fail("coverage_shape", "saved coverage evidence is missing")
        contract_payload = gates.get("contract", {}).get("report")
        if (
            not isinstance(contract_payload, dict)
            or contract_payload.get("ok") is not True
            or not isinstance(contract_payload.get("total"), int)
            or contract_payload["total"] <= 0
            or contract_payload.get("passed") != contract_payload.get("total")
        ):
            fail("contract_not_strict", "saved narrative contract evidence is invalid")
        failures.extend(
            _visual_evidence_failures(report.get("visual_evidence"))
        )

    subject = report.get("subject", {})
    before = subject.get("before") if isinstance(subject, dict) else None
    after = subject.get("after") if isinstance(subject, dict) else None
    current_subject: dict[str, Any] | None = None
    if not isinstance(before, dict) or before != after:
        fail("subject_transition", "production subject was absent or changed during the run")
    else:
        _require_clean_subject({"failures": failures}, before)
        try:
            current_subject = _capture_subject(pack_root)
        except ProductionPolicyError as exc:
            fail("current_subject_error", str(exc))
        else:
            if current_subject != before:
                fail(
                    "current_subject_mismatch",
                    "current engine or pack inputs differ from the production report",
                )
        for kind, root in (
            ("engine", _engine_source_root()),
            ("pack", pack_root),
        ):
            untracked = _untracked_input_paths(root)
            if untracked:
                fail(
                    f"current_{kind}_inputs_untracked",
                    f"current {kind} production inputs are not all Git-tracked",
                    paths=untracked[:100],
                )

    policy_info = report.get("policy", {})
    current_policy: ProductionPolicy | None = None
    current_scenarios: list[dict[str, Any]] | None = None
    current_runtime_policy_document: dict[str, Any] | None = None
    if isinstance(policy_info, dict) and isinstance(policy_info.get("relative_path"), str):
        try:
            current_policy_path = _resolve_pack_path(
                pack_root,
                policy_info["relative_path"],
                label="policy.relative_path",
            )
            current_policy, current_policy_sha = load_production_policy(
                current_policy_path
            )
        except (OSError, ProductionPolicyError) as exc:
            fail("current_policy_error", str(exc))
        else:
            if current_policy_sha != policy_info.get("sha256"):
                fail("current_policy_mismatch", "production policy changed after the run")
            if current_policy.model_dump(mode="json") != policy_info.get("document"):
                fail(
                    "current_policy_document_mismatch",
                    "normalized production policy differs from the saved report",
                )
            expected_valid_until = generated_at + timedelta(
                seconds=current_policy.max_evidence_age_seconds
            )
            if valid_until != expected_valid_until:
                fail(
                    "report_freshness_policy_mismatch",
                    "report validity window differs from the production policy",
                )
            try:
                scenarios_path = _resolve_pack_path(
                    pack_root,
                    current_policy.visual.scenarios_path,
                    label="visual.scenarios_path",
                )
                approvals_path = _resolve_pack_path(
                    pack_root,
                    current_policy.visual.approvals_path,
                    label="visual.approvals_path",
                )
                runtime_policy_path = _resolve_pack_path(
                    pack_root,
                    current_policy.runtime.policy_path,
                    label="runtime.policy_path",
                )
                contract_path = _resolve_pack_path(
                    pack_root,
                    current_policy.contract.path,
                    label="contract.path",
                )
                current_scenarios, scenarios_sha = load_visual_scenarios(
                    scenarios_path
                )
                approvals, approvals_sha = load_visual_approvals(approvals_path)
                (
                    current_runtime_policy_document,
                    runtime_policy_sha,
                ) = load_runtime_policy(runtime_policy_path)
                contract_sha = _sha256_file(contract_path)
            except (OSError, ProductionPolicyError, RuntimePolicyError) as exc:
                fail("current_verification_input_error", str(exc))
            else:
                for key, actual in (
                    ("visual_scenarios_sha256", scenarios_sha),
                    ("visual_approvals_sha256", approvals_sha),
                    ("runtime_policy_sha256", runtime_policy_sha),
                ):
                    if policy_info.get(key) != actual:
                        fail(
                            "current_verification_input_mismatch",
                            f"current verification input digest changed: {key}",
                        )
                if isinstance(current_subject, dict):
                    expected_bindings = {
                        "scenario_set_sha256": _canonical_digest(current_scenarios),
                        "engine_content_sha256": current_subject["engine"]["content"]["manifest_sha256"],
                        "pack_runtime_content_sha256": current_subject["pack"]["runtime_content"]["manifest_sha256"],
                    }
                    for field, expected in expected_bindings.items():
                        if getattr(approvals, field) != expected:
                            fail(
                                "current_visual_approval_stale",
                                f"current visual approval binding is stale: {field}",
                            )
                evidence_rows = report.get("visual_evidence", {}).get(
                    "scenarios", []
                )
                evidence_by_name = {
                    row.get("name"): row
                    for row in evidence_rows
                    if isinstance(row, dict) and isinstance(row.get("name"), str)
                }
                current_names = {item["name"] for item in current_scenarios}
                if set(approvals.scenarios) != current_names:
                    fail(
                        "current_visual_approval_set",
                        "current visual approval manifest does not exactly match scenarios",
                    )
                baseline_dir = pack_root / "visual_baselines"
                baseline_paths = {
                    path.relative_to(baseline_dir).as_posix()
                    for path in baseline_dir.rglob("*")
                    if path.is_file() and path.suffix.lower() == ".png"
                }
                expected_baseline_paths = {
                    f"{name}.png" for name in current_names
                }
                if baseline_paths != expected_baseline_paths:
                    fail(
                        "current_visual_baseline_set",
                        "current visual baseline PNG set does not exactly match scenarios",
                    )
                for scenario in current_scenarios:
                    name = scenario["name"]
                    approval = approvals.scenarios.get(name)
                    row = evidence_by_name.get(name)
                    if approval is None or approval.status != "approved":
                        fail(
                            "current_visual_not_approved",
                            f"current visual scenario is not approved: {name}",
                        )
                        continue
                    if approval.approved_at_utc > current_time + timedelta(minutes=5):
                        fail(
                            "current_visual_approval_future",
                            f"current visual approval timestamp is in the future: {name}",
                        )
                    if (
                        not isinstance(row, dict)
                        or row.get("approval") != approval.model_dump(mode="json")
                    ):
                        fail(
                            "current_visual_approval_mismatch",
                            f"saved visual approval differs from current manifest: {name}",
                        )
                        continue
                    baseline = pack_root / "visual_baselines" / f"{name}.png"
                    try:
                        baseline_sha, dimensions = _png_file_evidence(baseline)
                        baseline_dimensions = list(dimensions)
                    except (OSError, ProductionPolicyError) as exc:
                        fail("current_visual_baseline_error", str(exc), scenario=name)
                    else:
                        baseline_row = row.get("baseline", {})
                        if (
                            baseline_row.get("path") != str(baseline)
                            or baseline_row.get("sha256") != baseline_sha
                            or baseline_row.get("dimensions") != baseline_dimensions
                        ):
                            fail(
                                "current_visual_baseline_mismatch",
                                f"saved visual baseline evidence differs from current PNG: {name}",
                            )
                contract_payload = (
                    gates.get("contract", {}).get("report")
                    if isinstance(gates, dict)
                    else None
                )
                if (
                    not isinstance(contract_payload, dict)
                    or contract_payload.get("sha256") != contract_sha
                    or contract_payload.get("seed") != current_policy.contract.seed
                ):
                    fail(
                        "current_contract_mismatch",
                        "saved narrative contract evidence differs from current policy/input",
                    )
                coverage_payload = (
                    gates.get("coverage", {}).get("report")
                    if isinstance(gates, dict)
                    else None
                )
                if (
                    not isinstance(coverage_payload, dict)
                    or coverage_payload.get("seed") != current_policy.coverage.seed
                ):
                    fail(
                        "current_coverage_policy_mismatch",
                        "saved coverage seed differs from current production policy",
                    )
                failures.extend(
                    _visual_evidence_failures(
                        report.get("visual_evidence"),
                        scenarios=current_scenarios,
                    )
                )
    else:
        fail("policy_shape", "production policy identity is missing")

    if (
        isinstance(gates, dict)
        and isinstance(before, dict)
        and isinstance(current_runtime_policy_document, dict)
    ):
        runtime_payload = gates.get("runtime_check", {}).get("report")
        runtime_gate = gates.get("runtime_check", {})
        try:
            runtime_started = _parse_utc(
                runtime_gate.get("started_at_utc"),
                label="runtime gate started_at_utc",
            )
            runtime_finished = _parse_utc(
                runtime_gate.get("finished_at_utc"),
                label="runtime gate finished_at_utc",
            )
        except ProductionPolicyError as exc:
            fail("runtime_gate_timestamp", str(exc))
            runtime_started = generated_at
            runtime_finished = valid_until
        runtime_failures = _verify_runtime_report(
            runtime_payload,
            expected_subject=before,
            expected_policy_sha256=policy_info.get("runtime_policy_sha256"),
            expected_policy_document=current_runtime_policy_document,
            started_at=runtime_started,
            finished_at=runtime_finished,
        )
        failures.extend(runtime_failures)
    elif isinstance(gates, dict) and isinstance(before, dict):
        fail(
            "runtime_policy_unavailable",
            "current normalized runtime policy is unavailable for verification",
        )

    return {
        "ok": not failures,
        "report": str(report_path),
        "pack_root": str(pack_root),
        "failures": failures,
    }


def verify_production_report(
    report_path: Path,
    *,
    pack_root: Path,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Fail closed while verifying hostile or corrupted saved evidence."""
    resolved_report = report_path.expanduser().resolve()
    resolved_pack = pack_root.expanduser().resolve()
    conflict = _report_path_conflict(
        resolved_report,
        pack_root=resolved_pack,
    )
    if conflict is not None:
        return {
            "ok": False,
            "report": str(resolved_report),
            "pack_root": str(resolved_pack),
            "failures": [{
                "code": "unsafe_report_path",
                "message": f"production report must be outside the {conflict}",
            }],
        }
    try:
        return _verify_production_report_impl(
            resolved_report,
            pack_root=resolved_pack,
            now=now,
        )
    except Exception as exc:  # noqa: BLE001 - malformed evidence must fail, never crash
        return {
            "ok": False,
            "report": str(resolved_report),
            "pack_root": str(resolved_pack),
            "failures": [{
                "code": "verifier_error",
                "message": (
                    "production evidence could not be verified safely: "
                    f"{type(exc).__name__}: {exc}"
                ),
            }],
        }


__all__ = [
    "APPROVAL_SCHEMA_VERSION",
    "CLAIM_SCOPE",
    "POLICY_SCHEMA_VERSION",
    "REPORT_SCHEMA",
    "ProductionPolicy",
    "ProductionPolicyError",
    "ProductionVisualScenario",
    "VisualApprovalManifest",
    "load_production_policy",
    "load_visual_approvals",
    "load_visual_scenarios",
    "run_production_check",
    "verify_production_report",
]

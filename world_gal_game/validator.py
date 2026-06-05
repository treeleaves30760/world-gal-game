"""YAML content-pack validator.

Walk a pack's content directory, parse every YAML file, run pydantic
schema validation, then do cross-file reference checks.  Collects all
findings as :class:`ValidationIssue` objects so callers can decide how
to present them.
"""
from __future__ import annotations

import difflib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import yaml


# ---------------------------------------------------------------------------
# Public API types
# ---------------------------------------------------------------------------

@dataclass
class ValidationIssue:
    severity: Literal["error", "warning"]
    file: str            # relative path within the pack
    path: str            # JSON-pointer-ish path to the offending node
    message: str         # user-facing message (Chinese ok)
    hint: str | None = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _suggest(bad: str, choices: list[str], n: int = 1) -> str | None:
    """Return the closest match for *bad* among *choices*, or None."""
    matches = difflib.get_close_matches(bad, choices, n=n, cutoff=0.6)
    return matches[0] if matches else None


def _rel(pack_root: Path, path: Path) -> str:
    try:
        return str(path.relative_to(pack_root))
    except ValueError:
        return str(path)


def _resolve_asset_path(ref: str, *, pack_root: Path) -> Path | None:
    """Resolve a pack asset reference to a filesystem path, mirroring
    :meth:`world_gal_game.ui.assets.AssetManager._resolve` for the static
    case (no display needed).

    - An absolute path is used as-is.
    - An ``assets/...`` (or ``./assets/...``) path resolves against the pack
      root — the recommended relocatable form.
    - Anything else is resolved against the pack root too (best-effort; the
      validator can't see the engine resource root, and pack-relative is the
      only thing it can verify deterministically).

    Returns the resolved path (which may or may not exist) or ``None`` for an
    empty reference.
    """
    if not ref or not isinstance(ref, str):
        return None
    p = Path(ref)
    if p.is_absolute():
        return p
    parts = p.parts
    if parts and parts[0] in ("assets", "./assets"):
        return (pack_root / p)
    return (pack_root / p)


def _check_line_assets(raw_line: dict, *, file: str, path: str,
                       pack_root: Path) -> list[ValidationIssue]:
    """Warn about referenced line assets that don't exist on disk.

    Covers ``voice`` / ``cg`` / ``sfx`` / ``bgm`` (direct path each) and every
    ``portraits[*]`` entry (via :meth:`PortraitSpec.candidate_paths`). Missing
    files are *warnings*, not errors: the engine renders placeholders for
    images and silently no-ops missing audio, so a pack still runs while art /
    voice is in progress.
    """
    from world_gal_game.core.portrait_spec import PortraitSpec

    issues: list[ValidationIssue] = []

    # Direct single-path fields.
    for fld, label in (("voice", "語音"), ("cg", "CG"),
                       ("sfx", "音效"), ("bgm", "背景音樂")):
        ref = raw_line.get(fld)
        if isinstance(ref, str) and ref:
            # ``bgm: none`` / ``silence`` / ``-`` is the cut-to-silence sentinel
            # (routes to stop_music), not a file — don't flag it as missing.
            if fld == "bgm" and ref in ("none", "silence", "-"):
                continue
            resolved = _resolve_asset_path(ref, pack_root=pack_root)
            if resolved is not None and not resolved.exists():
                issues.append(ValidationIssue(
                    severity="warning", file=file, path=f"{path}.{fld}",
                    message=f"{label}檔案不存在：{ref}",
                    hint="引擎會以 placeholder/靜音降級；補上檔案或修正路徑。",
                ))

    # Multi-slot portraits: only warn if *all* candidate paths are missing.
    raw_ports = raw_line.get("portraits")
    if isinstance(raw_ports, list):
        for pi, raw_p in enumerate(raw_ports):
            if not isinstance(raw_p, dict):
                continue
            try:
                spec = PortraitSpec.model_validate(raw_p)
            except Exception:
                # Schema problems are reported elsewhere; skip asset check.
                continue
            candidates = spec.candidate_paths()
            if not candidates:
                continue
            any_exists = False
            for cand in candidates:
                resolved = _resolve_asset_path(cand, pack_root=pack_root)
                if resolved is not None and resolved.exists():
                    any_exists = True
                    break
            if not any_exists:
                issues.append(ValidationIssue(
                    severity="warning", file=file,
                    path=f"{path}.portraits[{pi}]",
                    message=(f"立繪 '{spec.character}' 找不到任何候選圖檔"
                             f"（{candidates[0]} 等）。"),
                    hint="引擎會畫紫色 placeholder；補上立繪圖檔或修正 character。",
                ))
    return issues


def _check_line_markup(raw_line: dict, *, file: str,
                       path: str) -> list[ValidationIssue]:
    """Lint a line's rich-text ``text`` for unbalanced / unknown / malformed
    markup. Each problem is an *error* with a closest-match hint for unknown
    tags (the parser tolerates these at runtime, but they're almost always a
    typo the author wants to know about).
    """
    from world_gal_game.dialogue.richtext import lint_markup, RICHTEXT_TAGS

    issues: list[ValidationIssue] = []
    text = raw_line.get("text")
    if not isinstance(text, str) or not text:
        return issues
    tags = sorted(RICHTEXT_TAGS)
    for problem in lint_markup(text):
        hint = None
        # "未知標記 [name]。" -> suggest closest known tag.
        if problem.startswith("未知標記 ["):
            bad = problem[len("未知標記 ["):].rstrip("。]")
            suggestion = _suggest(bad, tags)
            if suggestion:
                hint = f"最接近的標記是 [{suggestion}]。"
        issues.append(ValidationIssue(
            severity="error", file=file, path=f"{path}.text",
            message=f"富文本標記問題：{problem}",
            hint=hint,
        ))
    return issues


def _check_line_animations(raw_line: dict, *, file: str,
                           path: str) -> list[ValidationIssue]:
    """Validate ``portraits[*].enter`` / ``.exit`` against
    :data:`PORTRAIT_ANIMATIONS` and any ``.easing`` against
    :data:`EASING_NAMES`. Unknown names are errors with a closest-match hint
    (mirrors the effect-kind membership check).
    """
    from world_gal_game.core.portrait_spec import PORTRAIT_ANIMATIONS
    from world_gal_game.ui.easing import EASING_NAMES

    issues: list[ValidationIssue] = []
    raw_ports = raw_line.get("portraits")
    if not isinstance(raw_ports, list):
        return issues
    anims = sorted(PORTRAIT_ANIMATIONS)
    easings = list(EASING_NAMES)
    for pi, raw_p in enumerate(raw_ports):
        if not isinstance(raw_p, dict):
            continue
        pp = f"{path}.portraits[{pi}]"
        for fld in ("enter", "exit"):
            val = raw_p.get(fld)
            if isinstance(val, str) and val and val not in PORTRAIT_ANIMATIONS:
                suggestion = _suggest(val, anims)
                hint = (f"最接近的動畫是 '{suggestion}'。"
                        if suggestion else
                        f"可用動畫：{anims}")
                issues.append(ValidationIssue(
                    severity="error", file=file, path=f"{pp}.{fld}",
                    message=f"立繪動畫 '{val}' 不存在。", hint=hint,
                ))
        easing = raw_p.get("easing")
        if isinstance(easing, str) and easing and easing not in EASING_NAMES:
            suggestion = _suggest(easing, easings)
            hint = (f"最接近的 easing 是 '{suggestion}'。"
                    if suggestion else f"可用 easing：{easings}")
            issues.append(ValidationIssue(
                severity="error", file=file, path=f"{pp}.easing",
                message=f"easing '{easing}' 不存在。", hint=hint,
            ))
    return issues


# ---------------------------------------------------------------------------
# Per-file YAML parse
# ---------------------------------------------------------------------------

def _parse_yaml_file(path: Path) -> tuple[Any, list[ValidationIssue]]:
    """Return (parsed_data, issues).  issues non-empty means parse failure."""
    rel = str(path)
    try:
        with open(path, encoding="utf-8") as fh:
            data = yaml.safe_load(fh)
        return data, []
    except yaml.YAMLError as exc:
        return None, [ValidationIssue(
            severity="error",
            file=rel,
            path="",
            message=f"YAML 解析失敗：{exc}",
        )]


# ---------------------------------------------------------------------------
# Pydantic model validation wrappers
# ---------------------------------------------------------------------------

def _wrap_pydantic_error(exc, *, file: str, path_prefix: str,
                         model_fields: list[str]) -> list[ValidationIssue]:
    """Convert a pydantic ValidationError into friendly ValidationIssues."""
    from pydantic import ValidationError
    issues: list[ValidationIssue] = []
    for err in exc.errors():
        loc_parts = [str(p) for p in err["loc"]]
        loc = ".".join(loc_parts) if loc_parts else ""
        full_path = f"{path_prefix}.{loc}" if (path_prefix and loc) else (path_prefix or loc)

        raw_msg = err["msg"]
        err_type = err.get("type", "")

        # Extra field → suggest closest known field name
        if err_type == "extra_forbidden" or "Extra inputs are not permitted" in raw_msg:
            bad_field = loc_parts[-1] if loc_parts else "?"
            suggestion = _suggest(bad_field, model_fields)
            hint = f"你是不是想拼 '{suggestion}'？" if suggestion else None
            issues.append(ValidationIssue(
                severity="error",
                file=file,
                path=full_path,
                message=f"未知欄位 '{bad_field}'。",
                hint=hint,
            ))
        else:
            issues.append(ValidationIssue(
                severity="error",
                file=file,
                path=full_path,
                message=raw_msg,
            ))
    return issues


# ---------------------------------------------------------------------------
# Schema validation per content type
# ---------------------------------------------------------------------------

def _scene_fields() -> list[str]:
    from world_gal_game.core.story_graph import Scene
    return list(Scene.model_fields.keys())

def _line_fields() -> list[str]:
    from world_gal_game.core.story_graph import Line
    return list(Line.model_fields.keys())

def _choice_fields() -> list[str]:
    from world_gal_game.core.story_graph import Choice
    return list(Choice.model_fields.keys())

def _effect_fields() -> list[str]:
    from world_gal_game.core.story_graph import Effect
    return list(Effect.model_fields.keys())

def _condition_fields() -> list[str]:
    from world_gal_game.core.story_graph import Condition
    return list(Condition.model_fields.keys())

def _location_fields() -> list[str]:
    from world_gal_game.core.map_system import Location
    return list(Location.model_fields.keys())

def _scene_hook_fields() -> list[str]:
    from world_gal_game.core.map_system import SceneHook
    return list(SceneHook.model_fields.keys())

def _npc_presence_fields() -> list[str]:
    from world_gal_game.core.map_system import NPCPresence
    return list(NPCPresence.model_fields.keys())

def _npc_fields() -> list[str]:
    from world_gal_game.npc.npc_base import NPC
    return list(NPC.model_fields.keys())

def _item_fields() -> list[str]:
    from world_gal_game.core.inventory import Item
    return list(Item.model_fields.keys())

def _resource_fields() -> list[str]:
    from world_gal_game.core.resources import Resource
    return list(Resource.model_fields.keys())

def _shop_fields() -> list[str]:
    from world_gal_game.core.shop import Shop
    return list(Shop.model_fields.keys())

def _shop_listing_fields() -> list[str]:
    from world_gal_game.core.shop import ShopListing
    return list(ShopListing.model_fields.keys())

def _achievement_fields() -> list[str]:
    from world_gal_game.core.achievements import Achievement
    return list(Achievement.model_fields.keys())


# Valid effect / condition kinds are now sourced live from the plugin
# registry. The lookup is wrapped in functions so import order remains
# flexible (the registry is populated lazily on first use of the
# plugins package).

def _known_effect_kinds() -> list[str]:
    from world_gal_game.plugins.registry import EFFECT_REGISTRY
    return EFFECT_REGISTRY.list_kinds()


def _known_condition_kinds() -> list[str]:
    from world_gal_game.plugins.registry import CONDITION_REGISTRY
    return CONDITION_REGISTRY.list_kinds()


# Back-compat shims for any external code that imported these constants.
# Each is a module-level proxy that re-resolves on every access so the
# values stay in sync if plugins load mid-process.
class _DynamicKindList:
    """List-like proxy that delegates to a callable returning a fresh list."""

    def __init__(self, source) -> None:
        self._source = source

    def __iter__(self):
        return iter(self._source())

    def __contains__(self, item):
        return item in self._source()

    def __len__(self):
        return len(self._source())

    def __getitem__(self, idx):
        return self._source()[idx]

    def __repr__(self):
        return repr(self._source())


_EFFECT_KINDS = _DynamicKindList(_known_effect_kinds)
_CONDITION_KINDS = _DynamicKindList(_known_condition_kinds)


def _validate_args_model(raw: dict, kind: str, entry: Any, *,
                         file: str, path: str,
                         category: str) -> list[ValidationIssue]:
    """Validate a kind's (target/value/stat) triple against its typed arg model.

    Emits **warning**-level issues only. The runtime ``apply``/``evaluate`` path
    is deliberately tolerant (a defensive handler casts ``int(eff.value or N)``
    and degrades to an error dict, never crashing), so a hard error here would
    wrongly reject content the engine actually runs. We surface a hint instead.
    """
    from pydantic import ValidationError

    args_model = getattr(entry, "args_model", None) if entry is not None else None
    if args_model is None:
        return []
    # Feed only the meaningful subset; the arg model ignores extras (extra=
    # 'ignore') and fills defaults for absent optional fields.
    payload = {k: raw[k] for k in ("target", "value", "stat")
               if isinstance(raw, dict) and k in raw}
    issues: list[ValidationIssue] = []
    try:
        args_model.model_validate(payload)
    except ValidationError as exc:
        for err in exc.errors():
            loc = ".".join(str(p) for p in err["loc"])
            full_path = f"{path}.{loc}" if loc else path
            issues.append(ValidationIssue(
                severity="warning", file=file, path=full_path,
                message=f"{category} '{kind}' 參數可能有誤：{err['msg']}",
                hint=f"見 `wgg capabilities --schema` 中 {kind} 的參數 schema。",
            ))
    return issues


def _validate_effect_raw(raw: dict, *, file: str, path: str) -> list[ValidationIssue]:
    """Validate a single raw effect dict.

    Since ``Effect.kind`` is now an open string (any plugin can register
    new kinds), we do the kind-membership check *here* explicitly,
    before falling through to pydantic for the rest of the schema.
    """
    from pydantic import ValidationError
    from world_gal_game.core.story_graph import Effect

    issues: list[ValidationIssue] = []
    kind_val = raw.get("kind", "") if isinstance(raw, dict) else ""
    known = _known_effect_kinds()

    # 1) Explicit kind-membership: unknown kind → friendly error + hint.
    if kind_val and kind_val not in known:
        suggestion = _suggest(kind_val, known)
        hint = (f"最接近的 effect kind 是 '{suggestion}'。"
                if suggestion else None)
        issues.append(ValidationIssue(
            severity="error", file=file, path=f"{path}.kind",
            message=f"effect kind '{kind_val}' 不存在。",
            hint=hint,
        ))

    # 2) Pydantic schema check for remaining fields (target/value/stat
    #    types, extra-forbidden, ...). We don't gate this on the kind
    #    check above because we want to surface both class of issues.
    try:
        Effect.model_validate(raw)
    except ValidationError as exc:
        for err in exc.errors():
            loc_parts = [str(p) for p in err["loc"]]
            loc = ".".join(loc_parts)
            full_path = f"{path}.{loc}" if loc else path
            err_type = err.get("type", "")
            raw_msg = err["msg"]

            # Skip the kind validator (it was an empty / wrong-type kind);
            # we already issued a friendly diagnostic above if applicable.
            if loc == "kind":
                continue
            if err_type == "extra_forbidden" or "Extra inputs are not permitted" in raw_msg:
                bad_field = loc_parts[-1] if loc_parts else "?"
                suggestion = _suggest(bad_field, _effect_fields())
                hint = f"你是不是想拼 '{suggestion}'？" if suggestion else None
                issues.append(ValidationIssue(
                    severity="error", file=file, path=full_path,
                    message=f"未知欄位 '{bad_field}'。",
                    hint=hint,
                ))
            else:
                issues.append(ValidationIssue(
                    severity="error", file=file, path=full_path,
                    message=raw_msg,
                ))

    # 3) Typed arg-model check (warning-level) for known kinds.
    if kind_val and kind_val in known:
        from world_gal_game.plugins import EFFECT_REGISTRY
        issues += _validate_args_model(
            raw, kind_val, EFFECT_REGISTRY.get(kind_val),
            file=file, path=path, category="effect")
    return issues


def _validate_condition_raw(raw: dict, *, file: str, path: str) -> list[ValidationIssue]:
    from pydantic import ValidationError
    from world_gal_game.core.story_graph import Condition

    issues: list[ValidationIssue] = []
    kind_val = raw.get("kind", "") if isinstance(raw, dict) else ""
    known = _known_condition_kinds()

    if kind_val and kind_val not in known:
        suggestion = _suggest(kind_val, known)
        hint = (f"最接近的 condition kind 是 '{suggestion}'。"
                if suggestion else None)
        issues.append(ValidationIssue(
            severity="error", file=file, path=f"{path}.kind",
            message=f"condition kind '{kind_val}' 不存在。",
            hint=hint,
        ))

    try:
        Condition.model_validate(raw)
    except ValidationError as exc:
        for err in exc.errors():
            loc_parts = [str(p) for p in err["loc"]]
            loc = ".".join(loc_parts)
            full_path = f"{path}.{loc}" if loc else path
            err_type = err.get("type", "")
            raw_msg = err["msg"]

            if loc == "kind":
                continue
            if err_type == "extra_forbidden" or "Extra inputs are not permitted" in raw_msg:
                bad_field = loc_parts[-1] if loc_parts else "?"
                suggestion = _suggest(bad_field, _condition_fields())
                hint = f"你是不是想拼 '{suggestion}'？" if suggestion else None
                issues.append(ValidationIssue(
                    severity="error", file=file, path=full_path,
                    message=f"未知欄位 '{bad_field}'。",
                    hint=hint,
                ))
            else:
                issues.append(ValidationIssue(
                    severity="error", file=file, path=full_path,
                    message=raw_msg,
                ))

    # 3) Typed arg-model check (warning-level) for known kinds.
    if kind_val and kind_val in known:
        from world_gal_game.plugins import CONDITION_REGISTRY
        issues += _validate_args_model(
            raw, kind_val, CONDITION_REGISTRY.get(kind_val),
            file=file, path=path, category="condition")
    return issues


def _validate_scenes_yaml(data: Any, *, file: str,
                          pack_root: Path | None = None) -> list[ValidationIssue]:
    """Validate a scenes YAML (list or {scenes:[...]} wrapper).

    ``pack_root`` enables the asset-existence checks (resolving pack-relative
    ``assets/...`` references). When ``None`` (rare standalone call) those
    checks are skipped; markup + animation/easing checks still run.
    """
    from pydantic import ValidationError
    from world_gal_game.core.story_graph import Scene, Line, Choice, Effect, Condition

    issues: list[ValidationIssue] = []

    raw_scenes: list[Any] = []
    if isinstance(data, dict) and "scenes" in data:
        raw_scenes = data.get("scenes") or []
    elif isinstance(data, list):
        raw_scenes = data
    elif isinstance(data, dict):
        raw_scenes = [data]

    scene_f = _scene_fields()
    line_f = _line_fields()
    choice_f = _choice_fields()

    for si, raw_scene in enumerate(raw_scenes):
        if not isinstance(raw_scene, dict):
            continue
        sp = f"scenes[{si}]"

        # Validate lines
        for li, raw_line in enumerate(raw_scene.get("lines") or []):
            if not isinstance(raw_line, dict):
                continue
            lp = f"{sp}.lines[{li}]"
            for k in raw_line:
                if k not in line_f and k not in ("effects", "requires"):
                    suggestion = _suggest(k, line_f)
                    hint = f"你是不是想拼 '{suggestion}'？" if suggestion else None
                    issues.append(ValidationIssue(
                        severity="error", file=file, path=f"{lp}.{k}",
                        message=f"未知欄位 '{k}'。", hint=hint,
                    ))
            for ei, raw_eff in enumerate((raw_line.get("effects") or [])):
                if isinstance(raw_eff, dict):
                    issues.extend(_validate_effect_raw(
                        raw_eff, file=file, path=f"{lp}.effects[{ei}]"))
            for ci2, raw_cond in enumerate((raw_line.get("requires") or [])):
                if isinstance(raw_cond, dict):
                    issues.extend(_validate_condition_raw(
                        raw_cond, file=file, path=f"{lp}.requires[{ci2}]"))
            # New field-aware checks: asset existence (warning), rich-text
            # well-formedness (error), animation/easing names (error).
            if pack_root is not None:
                issues.extend(_check_line_assets(
                    raw_line, file=file, path=lp, pack_root=pack_root))
            issues.extend(_check_line_markup(raw_line, file=file, path=lp))
            issues.extend(_check_line_animations(raw_line, file=file, path=lp))

        # Validate choices
        for ci, raw_choice in enumerate(raw_scene.get("choices") or []):
            if not isinstance(raw_choice, dict):
                continue
            cp = f"{sp}.choices[{ci}]"
            for k in raw_choice:
                if k not in choice_f and k not in ("effects", "requires", "forbids"):
                    suggestion = _suggest(k, choice_f)
                    hint = f"你是不是想拼 '{suggestion}'？" if suggestion else None
                    issues.append(ValidationIssue(
                        severity="error", file=file, path=f"{cp}.{k}",
                        message=f"未知欄位 '{k}'。", hint=hint,
                    ))
            for ei, raw_eff in enumerate((raw_choice.get("effects") or [])):
                if isinstance(raw_eff, dict):
                    issues.extend(_validate_effect_raw(
                        raw_eff, file=file, path=f"{cp}.effects[{ei}]"))
            for ri, raw_cond in enumerate((raw_choice.get("requires") or [])):
                if isinstance(raw_cond, dict):
                    issues.extend(_validate_condition_raw(
                        raw_cond, file=file, path=f"{cp}.requires[{ri}]"))
            for ri, raw_cond in enumerate((raw_choice.get("forbids") or [])):
                if isinstance(raw_cond, dict):
                    issues.extend(_validate_condition_raw(
                        raw_cond, file=file, path=f"{cp}.forbids[{ri}]"))

        # Validate on_end effects
        for ei, raw_eff in enumerate((raw_scene.get("on_end") or [])):
            if isinstance(raw_eff, dict):
                issues.extend(_validate_effect_raw(
                    raw_eff, file=file, path=f"{sp}.on_end[{ei}]"))

        # Validate top-level scene fields
        for k in raw_scene:
            if k not in scene_f and k not in ("lines", "choices", "on_end", "requires"):
                suggestion = _suggest(k, scene_f)
                hint = f"你是不是想拼 '{suggestion}'？" if suggestion else None
                issues.append(ValidationIssue(
                    severity="error", file=file, path=f"{sp}.{k}",
                    message=f"未知欄位 '{k}'。", hint=hint,
                ))

        # Validate scene-level requires
        for ri, raw_cond in enumerate((raw_scene.get("requires") or [])):
            if isinstance(raw_cond, dict):
                issues.extend(_validate_condition_raw(
                    raw_cond, file=file, path=f"{sp}.requires[{ri}]"))

        # Sanity warnings
        if not raw_scene.get("lines"):
            issues.append(ValidationIssue(
                severity="warning", file=file, path=sp,
                message="場景沒有任何 lines。",
            ))
        for ci, raw_choice in enumerate(raw_scene.get("choices") or []):
            if isinstance(raw_choice, dict):
                if not raw_choice.get("next_scene") and not raw_choice.get("effects"):
                    issues.append(ValidationIssue(
                        severity="warning",
                        file=file,
                        path=f"{sp}.choices[{ci}]",
                        message="選項沒有 next_scene 也沒有 effects，選了不會有任何效果。",
                    ))

    return issues


def _validate_locations_yaml(data: Any, *, file: str) -> list[ValidationIssue]:
    from pydantic import ValidationError
    from world_gal_game.core.map_system import Location, NPCPresence, SceneHook

    issues: list[ValidationIssue] = []
    raw_locs: list[Any] = []
    if isinstance(data, dict) and "locations" in data:
        raw_locs = data["locations"] or []
    elif isinstance(data, list):
        raw_locs = data

    loc_f = _location_fields()
    npc_pres_f = _npc_presence_fields()
    hook_f = _scene_hook_fields()

    for li, raw_loc in enumerate(raw_locs):
        if not isinstance(raw_loc, dict):
            continue
        lp = f"locations[{li}]"
        for k in raw_loc:
            if k not in loc_f and k not in ("npcs", "scene_hooks"):
                suggestion = _suggest(k, loc_f)
                hint = f"你是不是想拼 '{suggestion}'？" if suggestion else None
                issues.append(ValidationIssue(
                    severity="error", file=file, path=f"{lp}.{k}",
                    message=f"未知欄位 '{k}'。", hint=hint,
                ))
        for ni, raw_npc_p in enumerate((raw_loc.get("npcs") or [])):
            if isinstance(raw_npc_p, dict):
                for k in raw_npc_p:
                    if k not in npc_pres_f:
                        suggestion = _suggest(k, npc_pres_f)
                        hint = f"你是不是想拼 '{suggestion}'？" if suggestion else None
                        issues.append(ValidationIssue(
                            severity="error", file=file,
                            path=f"{lp}.npcs[{ni}].{k}",
                            message=f"未知欄位 '{k}'。", hint=hint,
                        ))
        for hi, raw_hook in enumerate((raw_loc.get("scene_hooks") or [])):
            if isinstance(raw_hook, dict):
                for k in raw_hook:
                    if k not in hook_f:
                        suggestion = _suggest(k, hook_f)
                        hint = f"你是不是想拼 '{suggestion}'？" if suggestion else None
                        issues.append(ValidationIssue(
                            severity="error", file=file,
                            path=f"{lp}.scene_hooks[{hi}].{k}",
                            message=f"未知欄位 '{k}'。", hint=hint,
                        ))
    return issues


def _validate_characters_yaml(data: Any, *, file: str) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    raw_chars: list[Any] = []
    if isinstance(data, dict) and "characters" in data:
        raw_chars = data["characters"] or []
    elif isinstance(data, list):
        raw_chars = data

    npc_f = _npc_fields() + ["thresholds"]  # thresholds stripped by loader
    shop_f = _shop_fields()
    listing_f = _shop_listing_fields()

    for ci, raw_char in enumerate(raw_chars):
        if not isinstance(raw_char, dict):
            continue
        cp = f"characters[{ci}]"
        for k in raw_char:
            if k not in npc_f:
                suggestion = _suggest(k, npc_f)
                hint = f"你是不是想拼 '{suggestion}'？" if suggestion else None
                issues.append(ValidationIssue(
                    severity="error", file=file, path=f"{cp}.{k}",
                    message=f"未知欄位 '{k}'。", hint=hint,
                ))
        raw_shop = raw_char.get("shop")
        if isinstance(raw_shop, dict):
            for k in raw_shop:
                if k not in shop_f and k not in ("listings",):
                    suggestion = _suggest(k, shop_f)
                    hint = f"你是不是想拼 '{suggestion}'？" if suggestion else None
                    issues.append(ValidationIssue(
                        severity="error", file=file,
                        path=f"{cp}.shop.{k}",
                        message=f"未知欄位 '{k}'。", hint=hint,
                    ))
            for li2, raw_listing in enumerate((raw_shop.get("listings") or [])):
                if isinstance(raw_listing, dict):
                    for k in raw_listing:
                        if k not in listing_f:
                            suggestion = _suggest(k, listing_f)
                            hint = f"你是不是想拼 '{suggestion}'？" if suggestion else None
                            issues.append(ValidationIssue(
                                severity="error", file=file,
                                path=f"{cp}.shop.listings[{li2}].{k}",
                                message=f"未知欄位 '{k}'。", hint=hint,
                            ))
    return issues


def _validate_items_yaml(data: Any, *, file: str) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    raw_items: list[Any] = []
    if isinstance(data, dict) and "items" in data:
        raw_items = data["items"] or []
    elif isinstance(data, list):
        raw_items = data

    item_f = _item_fields()
    for ii, raw_item in enumerate(raw_items):
        if not isinstance(raw_item, dict):
            continue
        ip = f"items[{ii}]"
        for k in raw_item:
            if k not in item_f and k != "use_effects":
                suggestion = _suggest(k, item_f)
                hint = f"你是不是想拼 '{suggestion}'？" if suggestion else None
                issues.append(ValidationIssue(
                    severity="error", file=file, path=f"{ip}.{k}",
                    message=f"未知欄位 '{k}'。", hint=hint,
                ))
        for ei, raw_eff in enumerate((raw_item.get("use_effects") or [])):
            if isinstance(raw_eff, dict):
                issues.extend(_validate_effect_raw(
                    raw_eff, file=file, path=f"{ip}.use_effects[{ei}]"))
    return issues


def _validate_resources_yaml(data: Any, *, file: str) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    raw_res: list[Any] = []
    if isinstance(data, dict) and "resources" in data:
        raw_res = data["resources"] or []
    elif isinstance(data, list):
        raw_res = data

    res_f = _resource_fields()
    for ri, raw_r in enumerate(raw_res):
        if not isinstance(raw_r, dict):
            continue
        rp = f"resources[{ri}]"
        for k in raw_r:
            if k not in res_f:
                suggestion = _suggest(k, res_f)
                hint = f"你是不是想拼 '{suggestion}'？" if suggestion else None
                issues.append(ValidationIssue(
                    severity="error", file=file, path=f"{rp}.{k}",
                    message=f"未知欄位 '{k}'。", hint=hint,
                ))
    return issues


def _validate_achievements_yaml(data: Any, *, file: str) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    raw_achs: list[Any] = []
    if isinstance(data, dict) and "achievements" in data:
        raw_achs = data["achievements"] or []
    elif isinstance(data, list):
        raw_achs = data

    ach_f = _achievement_fields()
    for ai, raw_ach in enumerate(raw_achs):
        if not isinstance(raw_ach, dict):
            continue
        ap = f"achievements[{ai}]"
        for k in raw_ach:
            if k not in ach_f and k not in ("requires", "forbids"):
                suggestion = _suggest(k, ach_f)
                hint = f"你是不是想拼 '{suggestion}'？" if suggestion else None
                issues.append(ValidationIssue(
                    severity="error", file=file, path=f"{ap}.{k}",
                    message=f"未知欄位 '{k}'。", hint=hint,
                ))
        for ri, raw_cond in enumerate((raw_ach.get("requires") or [])):
            if isinstance(raw_cond, dict):
                issues.extend(_validate_condition_raw(
                    raw_cond, file=file, path=f"{ap}.requires[{ri}]"))
        for ri, raw_cond in enumerate((raw_ach.get("forbids") or [])):
            if isinstance(raw_cond, dict):
                issues.extend(_validate_condition_raw(
                    raw_cond, file=file, path=f"{ap}.forbids[{ri}]"))
        # Warning: achievement with no requires
        if not raw_ach.get("requires"):
            issues.append(ValidationIssue(
                severity="warning",
                file=file,
                path=ap,
                message=f"成就 '{raw_ach.get('id', '?')}' 沒有任何 requires，遊戲一開始就會解鎖。",
            ))
    return issues


# ---------------------------------------------------------------------------
# Cross-file reference collection
# ---------------------------------------------------------------------------

@dataclass
class _RefIndex:
    """Holds all known ids gathered from the whole pack."""
    scene_ids: set[str] = field(default_factory=set)
    character_ids: set[str] = field(default_factory=set)
    location_ids: set[str] = field(default_factory=set)
    item_ids: set[str] = field(default_factory=set)
    resource_ids: set[str] = field(default_factory=set)


def _collect_scene_ids(data: Any) -> set[str]:
    ids: set[str] = set()
    raw_scenes: list[Any] = []
    if isinstance(data, dict) and "scenes" in data:
        raw_scenes = data["scenes"] or []
    elif isinstance(data, list):
        raw_scenes = data
    elif isinstance(data, dict) and "id" in data:
        raw_scenes = [data]
    for s in raw_scenes:
        if isinstance(s, dict) and "id" in s:
            ids.add(s["id"])
    return ids


def _collect_simple_ids(data: Any, key: str) -> set[str]:
    ids: set[str] = set()
    items: list[Any] = []
    if isinstance(data, dict) and key in data:
        items = data[key] or []
    elif isinstance(data, list):
        items = data
    for item in items:
        if isinstance(item, dict) and "id" in item:
            ids.add(item["id"])
    return ids


# ---------------------------------------------------------------------------
# Cross-reference checks
# ---------------------------------------------------------------------------

def _check_refs_in_effects(
    effects: list[Any],
    *,
    file: str,
    path_prefix: str,
    index: _RefIndex,
) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    for ei, eff in enumerate(effects):
        if not isinstance(eff, dict):
            continue
        ep = f"{path_prefix}[{ei}]"
        kind = eff.get("kind", "")
        target = eff.get("target", "")

        if kind == "affection" and target and target not in index.character_ids:
            suggestion = _suggest(target, list(index.character_ids))
            hint = f"最接近的角色 id 是 '{suggestion}'。" if suggestion else None
            issues.append(ValidationIssue(
                severity="error", file=file, path=f"{ep}.target",
                message=f"effect target '{target}' 不是已知角色 id。",
                hint=hint,
            ))
        elif kind in ("move_to", "unlock_location") and target and target not in index.location_ids:
            suggestion = _suggest(target, list(index.location_ids))
            hint = f"最接近的地點 id 是 '{suggestion}'。" if suggestion else None
            issues.append(ValidationIssue(
                severity="error", file=file, path=f"{ep}.target",
                message=f"effect target '{target}' 不是已知地點 id。",
                hint=hint,
            ))
        elif kind == "play_scene":
            ref = eff.get("target") or (eff.get("value") if isinstance(eff.get("value"), str) else None)
            if ref and ref not in index.scene_ids:
                suggestion = _suggest(ref, list(index.scene_ids))
                hint = f"最接近的 scene id 是 '{suggestion}'。" if suggestion else None
                issues.append(ValidationIssue(
                    severity="error", file=file, path=f"{ep}.target",
                    message=f"play_scene target '{ref}' 不是已知 scene id。",
                    hint=hint,
                ))
        elif kind in ("give_item", "take_item", "use_item") and target and target not in index.item_ids:
            suggestion = _suggest(target, list(index.item_ids))
            hint = f"最接近的 item id 是 '{suggestion}'。" if suggestion else None
            issues.append(ValidationIssue(
                severity="error", file=file, path=f"{ep}.target",
                message=f"effect target '{target}' 不是已知 item id。",
                hint=hint,
            ))
        elif kind == "gift" and target and target not in index.character_ids:
            # gift target = character; item is in stat field
            suggestion = _suggest(target, list(index.character_ids))
            hint = f"最接近的角色 id 是 '{suggestion}'。" if suggestion else None
            issues.append(ValidationIssue(
                severity="error", file=file, path=f"{ep}.target",
                message=f"gift target '{target}' 不是已知角色 id。",
                hint=hint,
            ))
        elif kind in ("gain_resource", "spend_resource", "set_resource") and target and target not in index.resource_ids:
            # Only warn; resources may be declared inline
            suggestion = _suggest(target, list(index.resource_ids))
            hint = f"最接近的 resource id 是 '{suggestion}'。" if suggestion else None
            issues.append(ValidationIssue(
                severity="warning", file=file, path=f"{ep}.target",
                message=f"effect target '{target}' 不在已知 resource id 中（可能是動態宣告）。",
                hint=hint,
            ))
    return issues


def _check_refs_scenes(data: Any, *, file: str, index: _RefIndex) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    raw_scenes: list[Any] = []
    if isinstance(data, dict) and "scenes" in data:
        raw_scenes = data["scenes"] or []
    elif isinstance(data, list):
        raw_scenes = data
    elif isinstance(data, dict) and "id" in data:
        raw_scenes = [data]

    for si, raw_scene in enumerate(raw_scenes):
        if not isinstance(raw_scene, dict):
            continue
        sp = f"scenes[{si}]"

        # on_end effects
        issues.extend(_check_refs_in_effects(
            raw_scene.get("on_end") or [],
            file=file, path_prefix=f"{sp}.on_end",
            index=index,
        ))

        # line effects
        for li, raw_line in enumerate(raw_scene.get("lines") or []):
            if not isinstance(raw_line, dict):
                continue
            issues.extend(_check_refs_in_effects(
                raw_line.get("effects") or [],
                file=file, path_prefix=f"{sp}.lines[{li}].effects",
                index=index,
            ))

        # choice effects + next_scene
        for ci, raw_choice in enumerate(raw_scene.get("choices") or []):
            if not isinstance(raw_choice, dict):
                continue
            cp = f"{sp}.choices[{ci}]"
            issues.extend(_check_refs_in_effects(
                raw_choice.get("effects") or [],
                file=file, path_prefix=f"{cp}.effects",
                index=index,
            ))
            ns = raw_choice.get("next_scene")
            if ns and ns not in index.scene_ids:
                suggestion = _suggest(ns, list(index.scene_ids))
                hint = f"最接近的 scene id 是 '{suggestion}'。" if suggestion else None
                issues.append(ValidationIssue(
                    severity="error", file=file, path=f"{cp}.next_scene",
                    message=f"next_scene '{ns}' 不是已知 scene id。",
                    hint=hint,
                ))

    return issues


def _check_refs_locations(data: Any, *, file: str, index: _RefIndex) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    raw_locs: list[Any] = []
    if isinstance(data, dict) and "locations" in data:
        raw_locs = data["locations"] or []
    elif isinstance(data, list):
        raw_locs = data

    for li, raw_loc in enumerate(raw_locs):
        if not isinstance(raw_loc, dict):
            continue
        lp = f"locations[{li}]"

        # exits reference other locations
        for ei, exit_id in enumerate((raw_loc.get("exits") or [])):
            if isinstance(exit_id, str) and exit_id not in index.location_ids:
                suggestion = _suggest(exit_id, list(index.location_ids))
                hint = f"最接近的地點 id 是 '{suggestion}'。" if suggestion else None
                issues.append(ValidationIssue(
                    severity="error", file=file, path=f"{lp}.exits[{ei}]",
                    message=f"exit '{exit_id}' 不是已知地點 id。",
                    hint=hint,
                ))

        # scene_hooks reference scenes
        for hi, raw_hook in enumerate((raw_loc.get("scene_hooks") or [])):
            if not isinstance(raw_hook, dict):
                continue
            sid = raw_hook.get("scene_id", "")
            if sid and sid not in index.scene_ids:
                suggestion = _suggest(sid, list(index.scene_ids))
                hint = f"最接近的 scene id 是 '{suggestion}'。" if suggestion else None
                issues.append(ValidationIssue(
                    severity="error", file=file,
                    path=f"{lp}.scene_hooks[{hi}].scene_id",
                    message=f"scene_hook.scene_id '{sid}' 不是已知 scene id。",
                    hint=hint,
                ))

        # npcs reference character ids
        for ni, raw_npc_p in enumerate((raw_loc.get("npcs") or [])):
            if not isinstance(raw_npc_p, dict):
                continue
            nid = raw_npc_p.get("npc_id", "")
            if nid and nid not in index.character_ids:
                suggestion = _suggest(nid, list(index.character_ids))
                hint = f"最接近的角色 id 是 '{suggestion}'。" if suggestion else None
                issues.append(ValidationIssue(
                    severity="error", file=file,
                    path=f"{lp}.npcs[{ni}].npc_id",
                    message=f"npc_id '{nid}' 不是已知角色 id。",
                    hint=hint,
                ))
    return issues


# ---------------------------------------------------------------------------
# Flag-manifest cross-check helpers
# ---------------------------------------------------------------------------

_FLAG_WRITE_KINDS = frozenset({"set_flag", "set_flag_if_unset", "increment_flag"})
_FLAG_READ_KINDS = frozenset({"flag", "not_flag", "flag_eq"})


def _collect_flag_usage(obj: Any, writes: set[str], reads: set[str]) -> None:
    """Recursively collect flag writes / reads from parsed pack YAML.

    Pure structural walk: any ``{kind, target}`` dict whose ``kind`` is a known
    flag-writing / flag-reading effect or condition contributes its ``target``
    to the respective set, at any nesting depth (line effects, choice
    ``requires``, ``on_end``, scene hooks, ending ``requires`` …). A
    ``requires_flags`` / ``forbids_flags`` list counts as reads. Raw-YAML only,
    so the validator stays free of any engine load.
    """
    if isinstance(obj, dict):
        kind = obj.get("kind")
        target = obj.get("target")
        if isinstance(kind, str) and isinstance(target, str) and target:
            if kind in _FLAG_WRITE_KINDS:
                writes.add(target)
            elif kind in _FLAG_READ_KINDS:
                reads.add(target)
        for fld in ("requires_flags", "forbids_flags"):
            lst = obj.get(fld)
            if isinstance(lst, list):
                reads.update(f for f in lst if isinstance(f, str) and f)
        for value in obj.values():
            _collect_flag_usage(value, writes, reads)
    elif isinstance(obj, list):
        for item in obj:
            _collect_flag_usage(item, writes, reads)


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def validate_pack(pack_root: Path) -> list[ValidationIssue]:
    """Walk a pack directory, parse every YAML, return all issues found.

    Strategy:
    1. For each *.yaml file, try YAML parse.
    2. For each YAML, validate schema (extra fields, type mismatches).
    3. Cross-file reference check.
    4. Sanity warnings.
    """
    pack_root = Path(pack_root).resolve()
    # Support packs that have a content/ subdirectory or are the content dir
    content_root = pack_root / "content" if (pack_root / "content").is_dir() else pack_root

    issues: list[ValidationIssue] = []
    parsed: dict[Path, Any] = {}

    # --- Step 1 & 2: parse + schema validate each file ---

    all_yamls = sorted(content_root.rglob("*.yaml")) + sorted(content_root.rglob("*.yml"))
    # deduplicate (rglob may overlap)
    seen_paths: set[Path] = set()
    yaml_files: list[Path] = []
    for p in all_yamls:
        if p not in seen_paths:
            seen_paths.add(p)
            yaml_files.append(p)

    for yaml_path in yaml_files:
        rel = _rel(pack_root, yaml_path)
        data, parse_issues = _parse_yaml_file(yaml_path)
        for iss in parse_issues:
            iss.file = rel
        issues.extend(parse_issues)
        if data is None:
            continue
        parsed[yaml_path] = data

        name = yaml_path.name.lower()
        stem = yaml_path.stem.lower()

        if name == "locations.yaml":
            issues.extend(_validate_locations_yaml(data, file=rel))
        elif name == "characters.yaml":
            issues.extend(_validate_characters_yaml(data, file=rel))
        elif name == "items.yaml":
            issues.extend(_validate_items_yaml(data, file=rel))
        elif name == "resources.yaml":
            issues.extend(_validate_resources_yaml(data, file=rel))
        elif name == "achievements.yaml":
            issues.extend(_validate_achievements_yaml(data, file=rel))
        elif yaml_path.parent.name == "scenes" or "scene" in stem:
            issues.extend(_validate_scenes_yaml(
                data, file=rel, pack_root=pack_root))
        else:
            # Unknown YAML — at least it parsed; no schema to check
            pass

    # --- Step 3: build reference index ---
    index = _RefIndex()

    for yaml_path, data in parsed.items():
        name = yaml_path.name.lower()
        stem = yaml_path.stem.lower()

        if yaml_path.parent.name == "scenes" or "scene" in stem:
            index.scene_ids |= _collect_scene_ids(data)
        elif name == "characters.yaml":
            index.character_ids |= _collect_simple_ids(data, "characters")
        elif name == "locations.yaml":
            index.location_ids |= _collect_simple_ids(data, "locations")
        elif name == "items.yaml":
            index.item_ids |= _collect_simple_ids(data, "items")
        elif name == "resources.yaml":
            if isinstance(data, dict) and "resources" in data:
                for r in (data["resources"] or []):
                    if isinstance(r, dict) and "id" in r:
                        index.resource_ids.add(r["id"])
            elif isinstance(data, list):
                for r in data:
                    if isinstance(r, dict) and "id" in r:
                        index.resource_ids.add(r["id"])

    # Also collect resources from meta.yaml
    meta_path = content_root / "meta.yaml"
    if meta_path in parsed:
        meta = parsed[meta_path]
        if isinstance(meta, dict):
            for r in (meta.get("resources") or []):
                if isinstance(r, dict) and "id" in r:
                    index.resource_ids.add(r["id"])

    # --- Step 4: cross-file reference checks ---
    for yaml_path, data in parsed.items():
        rel = _rel(pack_root, yaml_path)
        name = yaml_path.name.lower()
        stem = yaml_path.stem.lower()

        if yaml_path.parent.name == "scenes" or "scene" in stem:
            issues.extend(_check_refs_scenes(data, file=rel, index=index))
        elif name == "locations.yaml":
            issues.extend(_check_refs_locations(data, file=rel, index=index))

    # --- Step 4.5: meta.yaml advisory checks (pack_format_version) ---
    meta_path = content_root / "meta.yaml"
    if meta_path in parsed:
        meta = parsed[meta_path]
        if isinstance(meta, dict):
            if "pack_format_version" not in meta:
                issues.append(ValidationIssue(
                    severity="warning",
                    file=_rel(pack_root, meta_path),
                    path="pack_format_version",
                    message="meta.yaml 沒有 pack_format_version 欄位。",
                    hint='加上 `pack_format_version: "0.1"` 以鎖定 schema 版本。',
                ))

    # --- Step 4.6: variable-manifest cross-check ---
    # Only runs when content/variables.yaml exists, so packs without a
    # declared manifest are unaffected (backward compatible). Pure raw-YAML:
    # a used-but-undeclared flag gets a "did you mean" warning (the typo guard
    # that makes flag edits safe for an agent); a declared-but-unused variable
    # gets an advisory warning.
    variables_path = content_root / "variables.yaml"
    if variables_path.is_file():
        try:
            from .core.variable_spec import VariableManifest
            manifest = VariableManifest.load(variables_path)
            declared = set(manifest.keys())
            writes: set[str] = set()
            reads: set[str] = set()
            for data in parsed.values():
                _collect_flag_usage(data, writes, reads)
            used = writes | reads
            rel_vars = _rel(pack_root, variables_path)
            for flag in sorted(used - declared):
                guess = _suggest(flag, sorted(declared))
                issues.append(ValidationIssue(
                    severity="warning", file=rel_vars,
                    path=f"variables.{flag}",
                    message=f"旗標 '{flag}' 在內容中被使用，但未在 variables.yaml 宣告。",
                    hint=(f"你是指 '{guess}' 嗎？" if guess
                          else "在 variables.yaml 補上宣告，或修正拼字。"),
                ))
            for flag in sorted(declared - used):
                issues.append(ValidationIssue(
                    severity="warning", file=rel_vars,
                    path=f"variables.{flag}",
                    message=f"變數 '{flag}' 已宣告，但內容中從未讀取或寫入。",
                    hint="移除未使用的宣告，或確認是否漏接此旗標。",
                ))
        except Exception as exc:
            issues.append(ValidationIssue(
                severity="warning", file="(pack)", path="",
                message=f"variable-manifest check failed: {exc}",
                hint="best-effort；修正前面的錯誤後重跑。",
            ))

    # --- Step 5: dead-end + reachability warnings via PackInspector. ---
    # Wrapped in try/except so a misformed pack that crashes the inspector
    # still gets the previous diagnostics back to the caller.
    try:
        from .dev.pack_inspector import PackInspector
        inspector = PackInspector(pack_root)
        for de in inspector.dead_ends():
            issues.append(ValidationIssue(
                severity="warning",
                file=de.file or "(pack)",
                path=f"{de.kind}:{de.target}",
                message=de.detail,
                hint=_dead_end_hint(de.kind),
            ))
    except Exception as exc:
        issues.append(ValidationIssue(
            severity="warning", file="(pack)", path="",
            message=f"dead-end analysis failed: {exc}",
            hint="this is best-effort; fix earlier errors and rerun",
        ))

    return issues


def validate_for_web(pack_root: Path,
                     meta: dict[str, Any] | None = None) -> list[ValidationIssue]:
    """Web-target-only gate. **Opt-in** — ``validate_pack`` never calls this.

    The browser has two failure modes the desktop doesn't:

    - **No system fonts.** ``pygame.font.match_font`` finds nothing under
      Emscripten, so a pack relying on a system CJK face renders tofu
      (missing-glyph boxes). The pack therefore *must* ship a
      ``bundled_font`` in ``meta.yaml``. Missing → **error**.
    - **Limited audio codecs.** ``.mp3`` decoding is patchy and ``.wav`` is
      huge over the wire; OGG/Vorbis is the reliable, compact choice.
      Any ``.mp3`` / ``.wav`` asset under the pack → **warning**.

    Parameters
    ----------
    pack_root:
        The pack root (the dir containing ``content/`` and ``assets/``).
    meta:
        Already-parsed ``content/meta.yaml`` dict. When ``None`` it is read
        from disk (best-effort; an unreadable meta yields the font error).
    """
    pack_root = Path(pack_root).resolve()
    issues: list[ValidationIssue] = []

    content_root = (pack_root / "content"
                    if (pack_root / "content").is_dir() else pack_root)

    # Resolve meta if not supplied.
    if meta is None:
        meta_path = content_root / "meta.yaml"
        meta = {}
        if meta_path.exists():
            try:
                with open(meta_path, encoding="utf-8") as fh:
                    loaded = yaml.safe_load(fh)
                if isinstance(loaded, dict):
                    meta = loaded
            except yaml.YAMLError:
                meta = {}

    # 1) bundled_font is mandatory on the web (CJK tofu risk otherwise).
    if not (isinstance(meta, dict) and meta.get("bundled_font")):
        issues.append(ValidationIssue(
            severity="error",
            file="content/meta.yaml",
            path="bundled_font",
            message="Web 目標必須在 meta.yaml 指定 bundled_font；"
                    "瀏覽器沒有系統字型，CJK 會變成豆腐方塊。",
            hint='加上 `bundled_font: assets/fonts/YourFont.ttf`（建議用 '
                 'subset 後的 CJK 字型以縮小首載體積）。',
        ))

    # 2) Audio assets in mp3/wav -> warning (prefer ogg on the web).
    for audio in sorted(pack_root.rglob("*.mp3")) + sorted(pack_root.rglob("*.wav")):
        issues.append(ValidationIssue(
            severity="warning",
            file=_rel(pack_root, audio),
            path="",
            message=f"音訊 '{audio.name}' 在 Web 上相容性不佳；建議改用 OGG。",
            hint="瀏覽器對 .mp3 解碼不一致、.wav 體積大；轉成 .ogg 最穩定。",
        ))

    return issues


def _dead_end_hint(kind: str) -> str | None:
    if kind == "scene":
        return ("scenes should either have a next_scene / play_scene out, "
                "or be marked as an ending (id 'ending_*' or tag 'ending')")
    if kind == "location":
        return "give the location at least one exit or scene_hook"
    if kind == "unreachable":
        return ("either reference this scene via play_scene / next_scene / "
                "scene_hook, or delete it")
    return None

"""YAML content-pack validator.

Walk a pack's content directory, parse every YAML file, run pydantic
schema validation, then do cross-file reference checks.  Collects all
findings as :class:`ValidationIssue` objects so callers can decide how
to present them.
"""
from __future__ import annotations

import difflib
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any, Literal

import yaml

from .dev.scene_transitions import iter_scene_transitions


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


@dataclass(frozen=True)
class _SpatialLocationRecord:
    """Raw authored location plus its diagnostic source coordinates."""

    raw: dict[str, Any]
    file: str
    path: str


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _suggest(bad: str, choices: list[str], n: int = 1) -> str | None:
    """Return the closest match for *bad* among *choices*, or None."""
    if not isinstance(bad, str):
        return None
    string_choices = [
        choice for choice in choices
        if isinstance(choice, str)
    ]
    matches = difflib.get_close_matches(
        bad,
        string_choices,
        n=n,
        cutoff=0.6,
    )
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


def _safe_relative_asset_path(
    ref: str,
    *,
    pack_root: Path,
) -> tuple[Path | None, str | None]:
    """Resolve a portable pack-relative asset path without allowing escape.

    The runtime still supports historical absolute paths in a few places, but
    title presentation is distributable pack metadata.  New title assets must
    therefore remain within the pack on every supported desktop platform.
    The second tuple member is a user-facing reason when the reference is not
    safe.
    """
    if not ref.strip():
        return None, "路徑不能是空字串。"
    if ref != ref.strip():
        return None, "路徑前後不能有空白。"
    if "\x00" in ref:
        return None, "路徑不能包含 NUL 字元。"
    if "\\" in ref:
        return None, "pack 路徑必須使用 '/'，不能使用反斜線。"
    if "://" in ref:
        return None, "只接受 pack 內的相對路徑，不能使用 URL。"

    portable = PurePosixPath(ref)
    windows = PureWindowsPath(ref)
    if portable.is_absolute() or windows.is_absolute() or windows.drive:
        return None, "只接受 pack 內的相對路徑，不能使用絕對路徑。"
    if ".." in portable.parts:
        return None, "相對路徑不能包含 '..' 或跳出 pack。"

    root = pack_root.resolve()
    candidate = (root / Path(*portable.parts)).resolve()
    try:
        candidate.relative_to(root)
    except ValueError:
        return None, "資產解析後位於 pack 外，不能使用此路徑。"
    return candidate, None


def _image_decodes(path: Path) -> bool | None:
    """Return whether pygame can decode *path*, or ``None`` if unavailable."""
    try:
        import pygame
    except ImportError:  # pragma: no cover - pygame is a required dependency
        return None
    try:
        pygame.image.load(str(path))
    except (OSError, pygame.error):
        return False
    return True


def _check_pack_asset(
    ref: str,
    *,
    field_path: str,
    label: str,
    file: str,
    pack_root: Path,
    image: bool,
) -> list[ValidationIssue]:
    """Validate one non-empty, portable pack asset reference."""
    resolved, unsafe_reason = _safe_relative_asset_path(
        ref,
        pack_root=pack_root,
    )
    if unsafe_reason is not None:
        return [ValidationIssue(
            severity="error",
            file=file,
            path=field_path,
            message=f"{label}不是安全的 pack 相對路徑：{ref}",
            hint=unsafe_reason,
        )]
    if resolved is None or not resolved.is_file():
        return [ValidationIssue(
            severity="warning",
            file=file,
            path=field_path,
            message=f"{label}檔案不存在：{ref}",
            hint="補上 pack 內的檔案，或修正相對路徑。",
        )]
    if image and _image_decodes(resolved) is False:
        return [ValidationIssue(
            severity="warning",
            file=file,
            path=field_path,
            message=f"{label}無法解碼為圖片：{ref}",
            hint="重新輸出為 pygame 支援的 PNG、JPEG 或 WebP 圖片。",
        )]
    return []


def _validate_title_metadata(
    meta: dict[str, Any],
    *,
    file: str,
    pack_root: Path,
) -> list[ValidationIssue]:
    """Validate strict ``title_screen`` and legacy title asset fields."""
    from pydantic import ValidationError

    from .core.title_screen_spec import (
        TitleScreenSpec,
        parse_title_screen_spec,
    )

    issues: list[ValidationIssue] = []
    try:
        parse_title_screen_spec(meta)
    except ValidationError as exc:
        issues.extend(_wrap_pydantic_error(
            exc,
            file=file,
            path_prefix="title_screen",
            model_fields=list(TitleScreenSpec.model_fields),
        ))

    raw_title = meta.get("title_screen")
    if isinstance(raw_title, dict):
        logo_image = raw_title.get("logo_image")
        if isinstance(logo_image, str) and logo_image:
            issues.extend(_check_pack_asset(
                logo_image,
                field_path="title_screen.logo_image",
                label="標題 Logo",
                file=file,
                pack_root=pack_root,
                image=True,
            ))

    for field_name, label, image in (
        ("title_bg", "標題背景", True),
        ("title_bgm", "標題背景音樂", False),
    ):
        if field_name not in meta:
            continue
        ref = meta[field_name]
        if not isinstance(ref, str):
            issues.append(ValidationIssue(
                severity="error",
                file=file,
                path=field_name,
                message=f"{label}路徑必須是字串。",
                hint=f"例如：`{field_name}: assets/{'backgrounds/title.png' if image else 'bgm/title.ogg'}`",
            ))
            continue
        if not ref:
            issues.append(ValidationIssue(
                severity="error",
                file=file,
                path=field_name,
                message=f"{label}路徑不能是空字串。",
                hint=f"不需要{label}時請移除 `{field_name}` 欄位。",
            ))
            continue
        issues.extend(_check_pack_asset(
            ref,
            field_path=field_name,
            label=label,
            file=file,
            pack_root=pack_root,
            image=image,
        ))
    return issues


def _validate_locale_metadata(
    meta: dict[str, Any],
    *,
    file: str,
    pack_root: Path,
) -> list[ValidationIssue]:
    """Load the runtime locale authority in strict, pack-contained mode."""
    if "locale" not in meta:
        return []
    from .core.localization import Localization, LocalizationError

    runtime_root = (
        pack_root.parent if pack_root.name == "content" else pack_root
    )
    try:
        Localization.from_meta(
            meta,
            pack_root=runtime_root,
            strict=True,
        )
    except (LocalizationError, TypeError, ValueError) as exc:
        return [ValidationIssue(
            severity="error",
            file=file,
            path="locale",
            message=f"locale manifest 或 catalog 無效：{exc}",
            hint=(
                "修正語系代碼、明示的 pack 相對 catalog/font 路徑、"
                "catalog envelope 與 source digest。"
            ),
        )]
    return []


def _validate_ui_skin_metadata(
    meta: dict[str, Any],
    *,
    file: str,
) -> list[ValidationIssue]:
    """Validate the generic selector and its plugin-owned option schema."""
    from pydantic import BaseModel, ValidationError

    from .core.ui_skin_spec import UISkinSpec, parse_ui_skin_spec
    from .plugins import WIDGET_REGISTRY
    from .ui.system_chrome import UI_CHROME_PROTOCOL

    try:
        spec = parse_ui_skin_spec(meta)
    except ValidationError as exc:
        return _wrap_pydantic_error(
            exc,
            file=file,
            path_prefix="ui_skin",
            model_fields=list(UISkinSpec.model_fields),
        )
    if spec is None:
        return []

    entry = WIDGET_REGISTRY.get(spec.renderer)
    if entry is None:
        return [ValidationIssue(
            severity="error",
            file=file,
            path="ui_skin.renderer",
            message=(
                f"UI skin renderer '{spec.renderer}' 沒有由任何已載入的 "
                "pack plugin 註冊。"
            ),
            hint=(
                "以 @widget 註冊同名 renderer，並在 plugin.yaml 的 "
                "extends.widgets 宣告它。"
            ),
        )]

    protocol = (entry.signature or {}).get("protocol")
    if protocol != UI_CHROME_PROTOCOL:
        return [ValidationIssue(
            severity="error",
            file=file,
            path="ui_skin.renderer",
            message=(
                f"UI skin renderer '{spec.renderer}' 宣告 protocol "
                f"{protocol!r}，需要 {UI_CHROME_PROTOCOL!r}。"
            ),
            hint=(
                "在 @widget(..., signature={\"protocol\": "
                "\"ui_chrome/v1\"}) 中宣告相容協定。"
            ),
        )]

    try:
        options_model = getattr(entry.cls, "OptionsModel", None)
    except Exception as exc:
        return [ValidationIssue(
            severity="error",
            file=file,
            path="ui_skin.options",
            message=(
                f"UI skin renderer '{spec.renderer}' 的 OptionsModel "
                f"無法讀取：{type(exc).__name__}: {exc}"
            ),
            hint="修正 plugin 的 OptionsModel 宣告後再驗證 pack。",
        )]
    try:
        is_pydantic_model = (
            isinstance(options_model, type)
            and issubclass(options_model, BaseModel)
        )
    except Exception as exc:
        return [ValidationIssue(
            severity="error",
            file=file,
            path="ui_skin.options",
            message=(
                f"UI skin renderer '{spec.renderer}' 的 OptionsModel "
                f"無法檢查：{type(exc).__name__}: {exc}"
            ),
            hint="修正 plugin 的 OptionsModel 宣告後再驗證 pack。",
        )]
    if not is_pydantic_model:
        return []
    try:
        options_model.model_validate(spec.options)
    except ValidationError as exc:
        try:
            model_fields = list(options_model.model_fields)
        except Exception:
            # Field reflection is only used for typo suggestions. Preserve the
            # detailed pydantic issues even when plugin metadata reflection is
            # faulty.
            model_fields = []
        return _wrap_pydantic_error(
            exc,
            file=file,
            path_prefix="ui_skin.options",
            model_fields=model_fields,
        )
    except Exception as exc:
        return [ValidationIssue(
            severity="error",
            file=file,
            path="ui_skin.options",
            message=(
                f"UI skin renderer '{spec.renderer}' 的 OptionsModel "
                f"驗證失敗：{type(exc).__name__}: {exc}"
            ),
            hint=(
                "plugin-defined validator 不應拋出 ValidationError 以外的"
                "例外；修正 plugin 後再驗證 pack。"
            ),
        )]
    return []


def _validate_spatial_map_metadata(
    meta: dict[str, Any],
    *,
    file: str,
    pack_root: Path,
    locations: dict[str, _SpatialLocationRecord],
    scene_ids: set[str],
) -> list[ValidationIssue]:
    """Validate the strict spatial-map block and its pack-wide references."""
    import math

    from pydantic import ValidationError

    from .core.spatial_map import (
        SpatialMapSpec,
        parse_spatial_map_spec,
    )

    issues: list[ValidationIssue] = []
    try:
        spec = parse_spatial_map_spec(meta)
    except ValidationError as exc:
        return _wrap_pydantic_error(
            exc,
            file=file,
            path_prefix="travel.spatial_map",
            model_fields=list(SpatialMapSpec.model_fields),
        )
    if spec is None:
        return issues

    if spec.background is not None:
        issues.extend(_check_pack_asset(
            spec.background,
            field_path="travel.spatial_map.background",
            label="空間地圖背景",
            file=file,
            pack_root=pack_root,
            image=True,
        ))

    known_location_ids = set(locations)
    edge_ids = set(spec.edge_destinations)
    node_ids = set(spec.nodes)

    for index, scene_id in enumerate(spec.auto_open_after_scenes):
        if scene_id in scene_ids:
            continue
        suggestion = _suggest(scene_id, sorted(scene_ids))
        issues.append(ValidationIssue(
            severity="error",
            file=file,
            path=(
                "travel.spatial_map.auto_open_after_scenes"
                f"[{index}]"
            ),
            message=f"自動開啟地圖的 scene '{scene_id}' 不存在。",
            hint=(
                f"最接近的 scene id 是 '{suggestion}'。"
                if suggestion else None
            ),
        ))

    for location_id, edge in spec.edge_destinations.items():
        edge_path = (
            "travel.spatial_map.edge_destinations."
            f"{location_id}"
        )
        if location_id not in known_location_ids:
            suggestion = _suggest(location_id, sorted(known_location_ids))
            issues.append(ValidationIssue(
                severity="error",
                file=file,
                path=edge_path,
                message=(
                    f"校外縮圖地點 '{location_id}' 不是已知 location id。"
                ),
                hint=(
                    f"最接近的地點 id 是 '{suggestion}'。"
                    if suggestion else
                    "在 locations.yaml 宣告地點，或修正此 mapping key。"
                ),
            ))
        if edge.thumbnail is not None:
            issues.extend(_check_pack_asset(
                edge.thumbnail,
                field_path=f"{edge_path}.thumbnail",
                label=f"校外地點 '{location_id}' 的縮圖",
                file=file,
                pack_root=pack_root,
                image=True,
            ))
            if not (edge.attribution or spec.attribution):
                issues.append(ValidationIssue(
                    severity="error",
                    file=file,
                    path=f"{edge_path}.attribution",
                    message=(
                        f"校外地點 '{location_id}' 使用縮圖但沒有署名資訊。"
                    ),
                    hint=(
                        "在此 edge destination 加上 `attribution`，或在 "
                        "`travel.spatial_map.attribution` 提供涵蓋所有圖像"
                        "的署名。原創圖片也應明確標示為原創。"
                    ),
                ))

    for location_id, node in spec.nodes.items():
        node_path = f"travel.spatial_map.nodes.{location_id}"
        if location_id not in known_location_ids:
            suggestion = _suggest(location_id, sorted(known_location_ids))
            issues.append(ValidationIssue(
                severity="error",
                file=file,
                path=node_path,
                message=(
                    f"空間地圖節點 '{location_id}' 不是已知 location id。"
                ),
                hint=(
                    f"最接近的地點 id 是 '{suggestion}'。"
                    if suggestion else
                    "在 locations.yaml 宣告地點，或修正此 mapping key。"
                ),
            ))
        if node.layer == "interior" and node.parent is None:
            issues.append(ValidationIssue(
                severity="error",
                file=file,
                path=f"{node_path}.parent",
                message=f"interior 節點 '{location_id}' 必須指定 parent。",
                hint="parent 必須是已存在的 location id。",
            ))
        if node.parent is not None:
            if node.parent == location_id:
                issues.append(ValidationIssue(
                    severity="error",
                    file=file,
                    path=f"{node_path}.parent",
                    message=f"節點 '{location_id}' 不能以自己作為 parent。",
                ))
            elif node.parent not in known_location_ids:
                suggestion = _suggest(
                    node.parent,
                    sorted(known_location_ids),
                )
                issues.append(ValidationIssue(
                    severity="error",
                    file=file,
                    path=f"{node_path}.parent",
                    message=(
                        f"parent '{node.parent}' 不是已知 location id。"
                    ),
                    hint=(
                        f"最接近的地點 id 是 '{suggestion}'。"
                        if suggestion else None
                    ),
                ))

    for location_id in sorted(edge_ids & node_ids):
        issues.append(ValidationIssue(
            severity="error",
            file=file,
            path=f"travel.spatial_map.nodes.{location_id}",
            message=(
                f"地點 '{location_id}' 不能同時是中央節點與校外縮圖。"
            ),
            hint=(
                "從 `nodes` 或 `edge_destinations` 移除其中一個設定；"
                "旅行可達性不受此呈現選擇影響。"
            ),
        ))

    # A parent may point at an unannotated location, which is an implicit
    # campus node. Only authored node-to-node links can form a cycle.
    parent_by_node = {
        location_id: node.parent
        for location_id, node in spec.nodes.items()
        if (
            node.parent is not None
            and node.parent in spec.nodes
            and node.parent != location_id
        )
    }
    visited: set[str] = set()
    active: list[str] = []
    active_index: dict[str, int] = {}
    reported_cycles: set[frozenset[str]] = set()

    def visit(location_id: str) -> None:
        if location_id in visited:
            return
        if location_id in active_index:
            cycle_nodes = active[active_index[location_id]:]
            cycle_key = frozenset(cycle_nodes)
            if cycle_nodes and cycle_key not in reported_cycles:
                reported_cycles.add(cycle_key)
                source = cycle_nodes[-1]
                cycle = " -> ".join([*cycle_nodes, cycle_nodes[0]])
                issues.append(ValidationIssue(
                    severity="error",
                    file=file,
                    path=(
                        f"travel.spatial_map.nodes.{source}.parent"
                    ),
                    message=f"空間地圖 parent 形成循環：{cycle}。",
                    hint="每個 interior 階層最終必須回到無 parent 的根節點。",
                ))
            return

        active_index[location_id] = len(active)
        active.append(location_id)
        parent = parent_by_node.get(location_id)
        if parent is not None:
            visit(parent)
        active.pop()
        active_index.pop(location_id, None)
        visited.add(location_id)

    for location_id in sorted(parent_by_node):
        visit(location_id)

    min_x, min_y, max_x, max_y = spec.bounds
    for location_id, record in locations.items():
        # Edge cards are positioned by compass direction. Interior nodes use a
        # child canvas whose own bounds may be introduced independently later.
        node = spec.nodes.get(location_id)
        is_central = (
            location_id not in edge_ids
            and (node is None or node.layer != "interior")
        )
        values: dict[str, float] = {}
        for field_name in ("map_x", "map_y"):
            field_path = f"{record.path}.{field_name}"
            if field_name not in record.raw:
                if is_central:
                    issues.append(ValidationIssue(
                        severity="warning",
                        file=record.file,
                        path=field_path,
                        message=(
                            f"中央地圖節點 '{location_id}' 沒有明確指定 "
                            f"{field_name}，會使用 Location 預設值 0。"
                        ),
                        hint=(
                            "在 locations.yaml 明確填寫 map_x 與 map_y，"
                            "讓標記穩定對齊插畫。"
                        ),
                    ))
                continue
            value = record.raw[field_name]
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(float(value))
            ):
                issues.append(ValidationIssue(
                    severity="error",
                    file=record.file,
                    path=field_path,
                    message=(
                        f"地點 '{location_id}' 的 {field_name} "
                        "必須是有限數值。"
                    ),
                ))
                continue
            values[field_name] = float(value)

        if not is_central or set(values) != {"map_x", "map_y"}:
            continue
        x = values["map_x"]
        y = values["map_y"]
        if not (min_x <= x <= max_x and min_y <= y <= max_y):
            issues.append(ValidationIssue(
                severity="warning",
                file=record.file,
                path=record.path,
                message=(
                    f"中央地圖節點 '{location_id}' 的座標 "
                    f"({x:g}, {y:g}) 超出 spatial_map.bounds。"
                ),
                hint=(
                    f"座標需位於 x={min_x:g}..{max_x:g}、"
                    f"y={min_y:g}..{max_y:g}，否則 UI 會將標記限制"
                    "在畫布邊緣。"
                ),
            ))

    return issues


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


def _validate_choice_card(
    raw_choice: dict,
    *,
    file: str,
    path: str,
    pack_root: Path | None,
) -> list[ValidationIssue]:
    """Validate strict visual-choice metadata and its optional image."""
    if "card" not in raw_choice:
        return []

    from pydantic import ValidationError
    from world_gal_game.core.story_graph import (
        ChoiceCardDetail,
        ChoiceCardSpec,
    )

    raw_card = raw_choice.get("card")
    if raw_card is None:
        return []
    try:
        card = ChoiceCardSpec.model_validate(raw_card)
    except ValidationError as exc:
        return _wrap_pydantic_error(
            exc,
            file=file,
            path_prefix=f"{path}.card",
            model_fields=(
                list(ChoiceCardSpec.model_fields)
                + list(ChoiceCardDetail.model_fields)
            ),
        )

    if pack_root is None or card.image is None:
        return []
    return _check_pack_asset(
        card.image,
        field_path=f"{path}.card.image",
        label="選項卡圖片",
        file=file,
        pack_root=pack_root,
        image=True,
    )


def _check_cg_reference_asset(
    ref: str,
    *,
    file: str,
    path: str,
    pack_root: Path,
) -> list[ValidationIssue]:
    """Warn when a legacy scene/effect CG reference is missing or unreadable.

    Scene and effect asset fields predate the portable-path contract, so this
    intentionally mirrors the existing line-asset policy instead of turning
    historical absolute paths into new validation errors.
    """
    from .core.cg_gallery import canonical_cg_path

    ref = canonical_cg_path(ref)
    resolved = _resolve_asset_path(ref, pack_root=pack_root)
    if resolved is None or not resolved.is_file():
        return [ValidationIssue(
            severity="warning",
            file=file,
            path=path,
            message=f"CG 檔案不存在：{ref}",
            hint="引擎會以 placeholder 降級；補上檔案或修正路徑。",
        )]
    if _image_decodes(resolved) is False:
        return [ValidationIssue(
            severity="warning",
            file=file,
            path=path,
            message=f"CG 無法解碼為圖片：{ref}",
            hint="重新輸出為 pygame 支援的 PNG、JPEG 或 WebP 圖片。",
        )]
    return []


def _check_show_cg_effect_assets(
    effects: list[Any],
    *,
    file: str,
    path_prefix: str,
    pack_root: Path,
) -> list[ValidationIssue]:
    """Validate the image target of every built-in ``show_cg`` effect."""
    issues: list[ValidationIssue] = []
    for index, raw_effect in enumerate(effects):
        if not isinstance(raw_effect, dict):
            continue
        if raw_effect.get("kind") != "show_cg":
            continue
        ref = raw_effect.get("target")
        field_path = f"{path_prefix}[{index}].target"
        if not isinstance(ref, str) or not ref.strip():
            issues.append(ValidationIssue(
                severity="error",
                file=file,
                path=field_path,
                message="show_cg 必須提供非空白的 CG 圖片 target。",
                hint="設定 pack-relative 圖片路徑，例如 assets/cgs/memory.png。",
            ))
            continue
        issues.extend(_check_cg_reference_asset(
            ref.strip(),
            file=file,
            path=field_path,
            pack_root=pack_root,
        ))
    return issues


def _cg_manifest_from_data(data: Any):
    """Parse either supported raw ``cg_gallery.yaml`` shape."""
    from .core.cg_gallery_spec import CGGalleryManifest

    if isinstance(data, dict) and "entries" in data:
        return CGGalleryManifest.model_validate(data)
    return CGGalleryManifest.from_items(data)


def _cg_entry_field_path(
    data: Any,
    index: int,
    asset: str,
    field: str,
) -> str:
    """Return a source-accurate path for every supported catalog shape."""
    if isinstance(data, list):
        return f"[{index}].{field}"
    if isinstance(data, dict) and "entries" in data:
        return f"entries[{index}].{field}"
    # In compact mapping form the asset is the key, not an authored child
    # field. Point asset errors at that key and metadata errors below it.
    key_path = f"[{asset!r}]"
    return key_path if field == "asset" else f"{key_path}.{field}"


def _remap_cg_schema_issue_paths(
    issues: list[ValidationIssue],
    data: Any,
) -> list[ValidationIssue]:
    """Restore list indices or compact mapping keys to model error paths."""
    if not isinstance(data, (list, dict)) or (
        isinstance(data, dict) and "entries" in data
    ):
        return issues
    mapping_keys = list(data) if isinstance(data, dict) else []
    for issue in issues:
        head, separator, tail = issue.path.partition(".")
        if not head.isdigit():
            continue
        index = int(head)
        if isinstance(data, list):
            prefix = f"[{index}]"
        elif index < len(mapping_keys):
            prefix = f"[{mapping_keys[index]!r}]"
        else:
            continue
        issue.path = f"{prefix}.{tail}" if separator else prefix
    return issues


def _validate_cg_gallery_yaml(
    data: Any,
    *,
    file: str,
    pack_root: Path,
) -> list[ValidationIssue]:
    """Validate the optional authored CG roster and its image assets."""
    from pydantic import ValidationError

    from .core.cg_gallery_spec import CGGalleryEntrySpec, CGGalleryManifest

    try:
        manifest = _cg_manifest_from_data(data)
    except ValidationError as exc:
        return _remap_cg_schema_issue_paths(_wrap_pydantic_error(
            exc,
            file=file,
            path_prefix="",
            model_fields=(
                list(CGGalleryManifest.model_fields)
                + list(CGGalleryEntrySpec.model_fields)
            ),
        ), data)
    except ValueError as exc:
        return [ValidationIssue(
            severity="error",
            file=file,
            path="",
            message=f"CG gallery manifest 無效：{exc}",
        )]

    issues: list[ValidationIssue] = []
    for index, entry in enumerate(manifest.entries):
        issues.extend(_check_pack_asset(
            entry.asset,
            field_path=_cg_entry_field_path(
                data,
                index,
                entry.asset,
                "asset",
            ),
            label="CG 原圖",
            file=file,
            pack_root=pack_root,
            image=True,
        ))
        if entry.thumbnail:
            issues.extend(_check_pack_asset(
                entry.thumbnail,
                field_path=_cg_entry_field_path(
                    data,
                    index,
                    entry.asset,
                    "thumbnail",
                ),
                label="CG 縮圖",
                file=file,
                pack_root=pack_root,
                image=True,
            ))
    return issues


def _validate_cg_gallery_refs(
    data: Any,
    *,
    file: str,
    character_ids: set[str],
    chapter_ids: set[str],
) -> list[ValidationIssue]:
    """Cross-check catalog context ids after the whole pack is indexed."""
    try:
        manifest = _cg_manifest_from_data(data)
    except (TypeError, ValueError):
        return []

    issues: list[ValidationIssue] = []
    for index, entry in enumerate(manifest.entries):
        for character_id in entry.character_ids:
            if character_id in character_ids:
                continue
            suggestion = _suggest(character_id, sorted(character_ids))
            issues.append(ValidationIssue(
                severity="error",
                file=file,
                path=_cg_entry_field_path(
                    data,
                    index,
                    entry.asset,
                    "character_ids",
                ),
                message=(
                    f"CG character id '{character_id}' "
                    "不是已知角色 id。"
                ),
                hint=(
                    f"最接近的角色 id 是 '{suggestion}'。"
                    if suggestion
                    else "在 characters.yaml 宣告角色，或修正 id。"
                ),
            ))
        if entry.chapter_id and entry.chapter_id not in chapter_ids:
            suggestion = _suggest(entry.chapter_id, sorted(chapter_ids))
            issues.append(ValidationIssue(
                severity="error",
                file=file,
                path=_cg_entry_field_path(
                    data,
                    index,
                    entry.asset,
                    "chapter_id",
                ),
                message=(
                    f"CG chapter id '{entry.chapter_id}' "
                    "不是 chapters.yaml 中的章節。"
                ),
                hint=(
                    f"最接近的 chapter id 是 '{suggestion}'。"
                    if suggestion
                    else "在 chapters.yaml 宣告章節，或修正 id。"
                ),
            ))
    return issues


def _music_manifest_from_data(data: Any):
    """Parse every runtime-supported ``music_room.yaml`` authoring shape."""
    from .core.music_room_spec import MusicRoomManifest

    if isinstance(data, dict) and "entries" in data:
        return MusicRoomManifest.model_validate(data)
    return MusicRoomManifest.from_items(data)


def _music_entry_field_path(
    data: Any,
    index: int,
    asset: str,
    field: str,
) -> str:
    if isinstance(data, list):
        return f"[{index}].{field}"
    if isinstance(data, dict) and "entries" in data:
        return f"entries[{index}].{field}"
    key_path = f"[{asset!r}]"
    return key_path if field == "asset" else f"{key_path}.{field}"


def _remap_music_schema_issue_paths(
    issues: list[ValidationIssue],
    data: Any,
) -> list[ValidationIssue]:
    if not isinstance(data, (list, dict)) or (
        isinstance(data, dict) and "entries" in data
    ):
        return issues
    mapping_keys = list(data) if isinstance(data, dict) else []
    for issue in issues:
        head, separator, tail = issue.path.partition(".")
        if not head.isdigit():
            continue
        index = int(head)
        if isinstance(data, list):
            prefix = f"[{index}]"
        elif index < len(mapping_keys):
            prefix = f"[{mapping_keys[index]!r}]"
        else:
            continue
        issue.path = f"{prefix}.{tail}" if separator else prefix
    return issues


def _validate_music_room_yaml(
    data: Any,
    *,
    file: str,
    pack_root: Path,
) -> list[ValidationIssue]:
    """Validate Music Room schema plus every decoder-facing audio asset."""
    from pydantic import ValidationError

    from .core.music_room_spec import MusicRoomEntrySpec, MusicRoomManifest

    try:
        manifest = _music_manifest_from_data(data)
    except ValidationError as exc:
        return _remap_music_schema_issue_paths(_wrap_pydantic_error(
            exc,
            file=file,
            path_prefix="",
            model_fields=(
                list(MusicRoomManifest.model_fields)
                + list(MusicRoomEntrySpec.model_fields)
            ),
        ), data)
    except ValueError as exc:
        return [ValidationIssue(
            severity="error",
            file=file,
            path="",
            message=f"Music Room manifest 無效：{exc}",
        )]

    issues: list[ValidationIssue] = []
    for index, entry in enumerate(manifest.entries):
        issues.extend(_check_pack_asset(
            entry.asset,
            field_path=_music_entry_field_path(
                data,
                index,
                entry.asset,
                "asset",
            ),
            label="Music Room 音訊",
            file=file,
            pack_root=pack_root,
            image=False,
        ))
    return issues


def _collect_music_references(data: Any) -> set[str]:
    """Collect executable scene/line BGM identities, excluding silence."""
    from .core.music_room import canonical_music_path

    raw_scenes: list[Any] = []
    if isinstance(data, dict) and "scenes" in data:
        raw_scenes = data.get("scenes") or []
    elif isinstance(data, list):
        raw_scenes = data
    elif isinstance(data, dict) and "id" in data:
        raw_scenes = [data]

    references: set[str] = set()

    def add(value: Any) -> None:
        if not isinstance(value, str):
            return
        if value.strip().lower() in {"none", "silence", "-"}:
            return
        canonical = canonical_music_path(value)
        if canonical:
            references.add(canonical)

    for scene in raw_scenes:
        if not isinstance(scene, dict):
            continue
        add(scene.get("bgm"))
        for line in scene.get("lines") or []:
            if isinstance(line, dict):
                add(line.get("bgm"))
    return references


def _collect_cg_references(data: Any) -> set[str]:
    """Collect CG paths from executable scene and item authoring surfaces."""
    from .core.cg_gallery import canonical_cg_path

    raw_scenes: list[Any] = []
    if isinstance(data, dict) and "scenes" in data:
        raw_scenes = data.get("scenes") or []
    elif isinstance(data, list):
        raw_scenes = data
    elif isinstance(data, dict) and "id" in data:
        raw_scenes = [data]

    references: set[str] = set()

    def add_reference(value: Any) -> None:
        if not isinstance(value, str):
            return
        canonical = canonical_cg_path(value)
        if canonical:
            references.add(canonical)

    def collect_effects(effects: Any) -> None:
        if not isinstance(effects, list):
            return
        for effect in effects:
            if not isinstance(effect, dict):
                continue
            if effect.get("kind") != "show_cg":
                continue
            add_reference(effect.get("target"))

    raw_items: list[Any] = []
    if isinstance(data, dict) and "items" in data:
        raw_items = data.get("items") or []
    for item in raw_items:
        if isinstance(item, dict):
            collect_effects(item.get("use_effects"))

    for scene in raw_scenes:
        if not isinstance(scene, dict):
            continue
        # A top-level list is a supported shape for both scenes.yaml and
        # items.yaml. Collecting this optional field is harmless for scenes
        # and preserves item-triggered CG coverage without guessing file type.
        collect_effects(scene.get("use_effects"))
        add_reference(scene.get("cg"))
        collect_effects(scene.get("on_end"))
        for line in scene.get("lines") or []:
            if not isinstance(line, dict):
                continue
            add_reference(line.get("cg"))
            collect_effects(line.get("effects"))
        for choice in scene.get("choices") or []:
            if isinstance(choice, dict):
                collect_effects(choice.get("effects"))
    return references


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

def _parse_yaml_file(
    path: Path,
    *,
    strict: bool = False,
) -> tuple[Any, list[ValidationIssue]]:
    """Return (parsed_data, issues).  issues non-empty means parse failure."""
    rel = str(path)
    if strict:
        from .core.localization import load_yaml_strict

        try:
            return load_yaml_strict(path, max_bytes=2 * 1024 * 1024), []
        except ValueError as exc:
            return None, [ValidationIssue(
                severity="error",
                file=rel,
                path="",
                message=f"YAML 解析失敗：{exc}",
            )]
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
                hint=(
                    "見 `world-gal-game capabilities --schema` 中 "
                    f"{kind} 的參數 schema。"
                ),
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

    # ``play_scene_branch.value[*].when`` is a real runtime Condition nested
    # inside an Effect's open ``value`` slot. Validate its kind explicitly;
    # Effect/arg-model validation checks the container shape but intentionally
    # does not know the condition registry.
    if kind_val == "play_scene_branch":
        cases = raw.get("value")
        if isinstance(cases, list):
            for case_index, case in enumerate(cases):
                if not isinstance(case, dict):
                    continue
                when = case.get("when")
                if isinstance(when, dict):
                    issues += _validate_condition_raw(
                        when,
                        file=file,
                        path=f"{path}.value[{case_index}].when",
                    )
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
        scene_cg = raw_scene.get("cg")
        if (
            pack_root is not None
            and isinstance(scene_cg, str)
            and scene_cg
        ):
            issues.extend(_check_cg_reference_asset(
                scene_cg,
                file=file,
                path=f"{sp}.cg",
                pack_root=pack_root,
            ))

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
            if pack_root is not None:
                issues.extend(_check_show_cg_effect_assets(
                    raw_line.get("effects") or [],
                    file=file,
                    path_prefix=f"{lp}.effects",
                    pack_root=pack_root,
                ))
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
            issues.extend(_validate_choice_card(
                raw_choice,
                file=file,
                path=cp,
                pack_root=pack_root,
            ))
            for ei, raw_eff in enumerate((raw_choice.get("effects") or [])):
                if isinstance(raw_eff, dict):
                    issues.extend(_validate_effect_raw(
                        raw_eff, file=file, path=f"{cp}.effects[{ei}]"))
            if pack_root is not None:
                issues.extend(_check_show_cg_effect_assets(
                    raw_choice.get("effects") or [],
                    file=file,
                    path_prefix=f"{cp}.effects",
                    pack_root=pack_root,
                ))
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
        if pack_root is not None:
            issues.extend(_check_show_cg_effect_assets(
                raw_scene.get("on_end") or [],
                file=file,
                path_prefix=f"{sp}.on_end",
                pack_root=pack_root,
            ))

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


def _validate_items_yaml(
    data: Any,
    *,
    file: str,
    pack_root: Path,
) -> list[ValidationIssue]:
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
        use_effects = raw_item.get("use_effects") or []
        for ei, raw_eff in enumerate(use_effects):
            if isinstance(raw_eff, dict):
                issues.extend(_validate_effect_raw(
                    raw_eff, file=file, path=f"{ip}.use_effects[{ei}]"))
        issues.extend(_check_show_cg_effect_assets(
            use_effects,
            file=file,
            path_prefix=f"{ip}.use_effects",
            pack_root=pack_root,
        ))
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
    ending_ids: set[str] = field(default_factory=set)
    character_ids: set[str] = field(default_factory=set)
    location_ids: set[str] = field(default_factory=set)
    item_ids: set[str] = field(default_factory=set)
    resource_ids: set[str] = field(default_factory=set)
    chapter_ids: set[str] = field(default_factory=set)
    # character id -> declared portrait_set expression keys (for the
    # expression-reference check). Empty/absent set = character uses pure
    # convention resolution, so we can't know its expressions → no check.
    character_portraits: dict[str, set[str]] = field(default_factory=dict)
    # display name -> character id, so a line's ``speaker`` (a display name)
    # can be resolved back to the character whose ``expression`` it sets.
    character_names: dict[str, str] = field(default_factory=dict)
    # character ids flagged ``is_heroine: true`` — used by the speaker↔portrait
    # mismatch check to recognise the flagship "incidental NPC carrying a
    # heroine's portrait" bug with high confidence.
    heroine_ids: set[str] = field(default_factory=set)


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
        if isinstance(s, dict):
            scene_id = s.get("id")
            if isinstance(scene_id, str) and scene_id.strip():
                ids.add(scene_id)
    return ids


def _collect_simple_ids(data: Any, key: str) -> set[str]:
    ids: set[str] = set()
    items: list[Any] = []
    if isinstance(data, dict) and key in data:
        items = data[key] or []
    elif isinstance(data, list):
        items = data
    for item in items:
        if isinstance(item, dict):
            item_id = item.get("id")
            if isinstance(item_id, str) and item_id.strip():
                ids.add(item_id)
    return ids


def _collect_spatial_location_records(
    parsed: dict[Path, Any],
    *,
    pack_root: Path,
) -> dict[str, _SpatialLocationRecord]:
    """Index raw location coordinates for spatial-map diagnostics."""
    records: dict[str, _SpatialLocationRecord] = {}
    for yaml_path, data in parsed.items():
        if yaml_path.name.lower() != "locations.yaml":
            continue
        raw_locations: list[Any] = []
        if isinstance(data, dict) and "locations" in data:
            raw_locations = data["locations"] or []
        elif isinstance(data, list):
            raw_locations = data
        for index, raw_location in enumerate(raw_locations):
            if not isinstance(raw_location, dict):
                continue
            location_id = raw_location.get("id")
            if not isinstance(location_id, str) or not location_id:
                continue
            records[location_id] = _SpatialLocationRecord(
                raw=raw_location,
                file=_rel(pack_root, yaml_path),
                path=f"locations[{index}]",
            )
    return records


def _collect_character_portraits(
    data: Any,
) -> tuple[dict[str, set[str]], dict[str, str], set[str]]:
    """Return (id -> declared portrait expression keys, display name -> id,
    heroine ids).

    Used by the expression-reference check: a scene that names an
    ``expression`` not present in the character's declared ``portrait_set``
    silently falls back to the default face, which is almost always a bug.
    The heroine-id set additionally feeds the speaker↔portrait mismatch check.

    The per-character key set is the ``portrait_set`` keys *plus* the stem of
    the default ``portrait`` file (e.g. ``normal`` for ``…/normal.png``). The
    engine's ``portrait_for`` falls back to that default portrait for an
    unknown expression, so naming it explicitly resolves to the intended face
    and must not be flagged.
    """
    from pathlib import Path as _Path

    portraits: dict[str, set[str]] = {}
    names: dict[str, str] = {}
    heroines: set[str] = set()
    raw_chars: list[Any] = []
    if isinstance(data, dict) and "characters" in data:
        raw_chars = data["characters"] or []
    elif isinstance(data, list):
        raw_chars = data
    for raw in raw_chars:
        if not isinstance(raw, dict) or "id" not in raw:
            continue
        cid = raw["id"]
        if not isinstance(cid, str) or not cid.strip():
            continue
        ps = raw.get("portrait_set")
        if isinstance(ps, dict):
            keys = {str(k) for k in ps.keys()}
            default = raw.get("portrait")
            if isinstance(default, str) and default:
                keys.add(_Path(default).stem)
            portraits[cid] = keys
        name = raw.get("name")
        if isinstance(name, str) and name:
            names[name] = cid
        if raw.get("is_heroine") is True:
            heroines.add(cid)
    return portraits, names, heroines


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
        elif kind in ("play_scene", "play_scene_branch"):
            for transition in iter_scene_transitions(eff):
                ref = transition.target
                if ref in index.scene_ids:
                    continue
                suggestion = _suggest(ref, list(index.scene_ids))
                hint = f"最接近的 scene id 是 '{suggestion}'。" if suggestion else None
                issues.append(ValidationIssue(
                    severity="error",
                    file=file,
                    path=f"{ep}.{transition.path}",
                    message=f"{kind} target '{ref}' 不是已知 scene id。",
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


def _check_expression_ref(
    character_id: str | None, expression: str | None, *,
    file: str, path: str, index: _RefIndex,
) -> ValidationIssue | None:
    """Warn if ``expression`` is referenced for a character whose declared
    ``portrait_set`` lacks it (silent fallback to the default face).

    Returns a warning-level issue, or ``None`` when nothing is wrong / the
    check doesn't apply (no character, no declared portrait_set, or the
    always-valid ``default`` expression).
    """
    if not character_id or not expression or expression == "default":
        return None
    declared = index.character_portraits.get(character_id)
    # No declared portrait_set → the character resolves portraits purely by
    # naming convention, so we can't know which expressions exist. Skip.
    if not declared:
        return None
    if expression in declared:
        return None
    suggestion = _suggest(expression, sorted(declared))
    options = "、".join(sorted(declared))
    hint = (f"你是不是想用 '{suggestion}'？" if suggestion
            else f"已宣告的表情：{options}。在角色的 portrait_set 補上 "
                 f"'{expression}'，或修正拼字。")
    return ValidationIssue(
        severity="warning", file=file, path=path,
        message=(f"表情 '{expression}' 不在角色 '{character_id}' 的 "
                 f"portrait_set 中，會靜默退回預設立繪。"),
        hint=hint,
    )


def _check_speaker_portrait_match(
    raw_line: dict, *, file: str, path: str, index: _RefIndex,
) -> ValidationIssue | None:
    """Warn when a line's speaker and its single portrait belong to different
    characters — the recurring "wrong face on screen" bug.

    At runtime an explicit ``portrait: {character: X}`` (or a single
    ``portraits: [{character: X}]``) shows X's face regardless of who speaks, so
    a line whose ``speaker`` is character A but whose lone portrait is character
    B renders B's face under A's name. The latest instance was a male NPC's
    lines carrying a heroine's portrait.

    The rule is deliberately narrow to stay low-false-positive — *legitimate*
    VN reaction shots (showing someone else's face while a character speaks) are
    common, so we only flag the two high-confidence shapes:

    1. The line has a non-empty ``speaker`` that is **not** an interpolation
       token (``{player_name}`` etc.) — the protagonist routinely has no own
       portrait and legitimately keeps the listener's face on screen, so a
       ``{...}`` speaker is never flagged. Narration (no speaker) is never
       flagged.
    2. The line has **exactly one** explicit portrait carrying a ``character``
       id — a single ``portraits`` entry or the dict form of ``portrait``.
       Multi-portrait staging is a deliberate multi-character composition where
       reaction shots are expected, so it is never flagged.
    3. That portrait ``character`` is a **declared** character id (a typo'd id
       is caught by the asset-existence check instead).
    4. The portrait ``character`` differs from the speaker's resolved character
       id (the speaker display name resolved via the name index; an unknown
       speaker name resolves to itself and cannot equal a declared id).
    5. AND one of two high-confidence signals holds:
       (a) the speaker is **also a declared character** (both sides are real
           characters with their own faces — a lone non-speaker portrait is
           almost always a wrong-id copy/paste), or
       (b) the portrait ``character`` is a **heroine** while the speaker is a
           different identity (the flagship "incidental / male NPC carrying a
           heroine's portrait" case).

    Returns a warning-level issue, or ``None`` when the rule does not apply.
    """
    speaker = raw_line.get("speaker")
    if not isinstance(speaker, str) or not speaker:
        return None  # narration — no speaker to mismatch
    # A ``{...}`` interpolation-token speaker is the protagonist / a dynamic
    # name; the player character has no own portrait and legitimately keeps the
    # listener's face on screen, so never flag these.
    if "{" in speaker and "}" in speaker:
        return None

    # Gather explicit portrait specs that carry a character id. The dict form of
    # ``portrait`` and each ``portraits`` entry qualify; a bare string portrait
    # or expression-only line has no explicit character and is out of scope.
    specs: list[dict] = []
    raw_ports = raw_line.get("portraits")
    if isinstance(raw_ports, list):
        specs.extend(p for p in raw_ports if isinstance(p, dict))
    if isinstance(raw_line.get("portrait"), dict):
        specs.append(raw_line["portrait"])
    with_char = [p for p in specs if isinstance(p.get("character"), str)
                 and p.get("character")]
    # Exactly one portrait character on the line, else it is a multi-character
    # composition (reaction shots expected) — don't flag.
    if len(with_char) != 1:
        return None
    port_char = with_char[0]["character"]

    # The portrait character must be a real declared character.
    if port_char not in index.character_ids:
        return None

    # Resolve the speaker (a display name) to its character id; an unknown name
    # resolves to itself, which won't equal any declared id.
    speaker_id = index.character_names.get(speaker, speaker)
    if speaker_id == port_char:
        return None  # speaker and portrait agree — fine

    speaker_is_char = speaker in index.character_names
    port_is_heroine = port_char in index.heroine_ids
    if not (speaker_is_char or port_is_heroine):
        return None  # neither high-confidence signal → likely a reaction shot

    hint = ("把 portrait.character 改成說話者本人，或移除 portrait 讓引擎"
            "依說話者解析立繪；若這是刻意的『反應鏡頭』（顯示他人立繪），"
            "可忽略此警告。")
    return ValidationIssue(
        severity="warning", file=file, path=path,
        message=(f"說話者 '{speaker}' 的這句台詞，立繪卻是另一個角色 "
                 f"'{port_char}'，畫面會顯示錯誤的臉。"),
        hint=hint,
    )


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
            lp = f"{sp}.lines[{li}]"
            # Expression references → must exist in the character's portrait_set.
            # (a) Multi-slot portraits + the dict form of ``portrait`` carry an
            #     explicit ``character`` id and ``expression``.
            port_specs: list[Any] = list(raw_line.get("portraits") or [])
            if isinstance(raw_line.get("portrait"), dict):
                port_specs.append(raw_line["portrait"])
            for pi, raw_p in enumerate(port_specs):
                if not isinstance(raw_p, dict):
                    continue
                iss = _check_expression_ref(
                    raw_p.get("character"), raw_p.get("expression"),
                    file=file, path=f"{lp}.portraits[{pi}].expression",
                    index=index)
                if iss is not None:
                    issues.append(iss)
            # (b) The line-level ``expression`` applies to the line's speaker
            #     (a display name); resolve it back to the character id.
            line_expr = raw_line.get("expression")
            speaker = raw_line.get("speaker")
            if isinstance(line_expr, str) and isinstance(speaker, str):
                cid = index.character_names.get(speaker, speaker)
                iss = _check_expression_ref(
                    cid, line_expr,
                    file=file, path=f"{lp}.expression", index=index)
                if iss is not None:
                    issues.append(iss)
            # (c) Speaker↔portrait-character mismatch (wrong face on screen).
            iss = _check_speaker_portrait_match(
                raw_line, file=file, path=lp, index=index)
            if iss is not None:
                issues.append(iss)

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


def _plugin_pack_root(validation_root: Path) -> Path:
    """Return the runtime pack root for a validator input path.

    ``validate_pack`` accepts either ``<pack>/`` or ``<pack>/content/``.
    Plugin discovery always starts at ``<pack>/plugins/``, matching
    :func:`world_gal_game.content_loader.load_pack`.
    """
    if validation_root.name == "content":
        return validation_root.parent
    return validation_root


def _plugin_failure_issues(manager: Any, *,
                           pack_root: Path) -> list[ValidationIssue]:
    """Convert failed pack-local plugin records into validation errors."""
    from world_gal_game.plugins.errors import PluginLoadError

    issues: list[ValidationIssue] = []
    for record in manager.failed():
        # A broken user/global plugin is not a defect in the pack being
        # validated. Pack-local failures, including malformed manifests,
        # belong in the pack's validation report.
        if record.source != "pack":
            continue

        manifest_path = record.root / "plugin.yaml"
        issue_path = "plugin"
        issue_file = _rel(pack_root, manifest_path)
        if isinstance(record.error, PluginLoadError):
            entry_name = record.manifest.entry_module or "plugin"
            if "/" in entry_name or "\\" in entry_name:
                entry_path = record.root / entry_name
            else:
                entry_path = record.root / f"{entry_name}.py"
            issue_file = _rel(pack_root, entry_path)
            issue_path = "entry_module"

        detail = str(record.error) if record.error else "unknown load failure"
        issues.append(ValidationIssue(
            severity="error",
            file=issue_file,
            path=issue_path,
            message=f"pack plugin '{record.id}' 載入失敗：{detail}",
            hint=(
                "檢查 plugin.yaml 的相容性與相依項目，並確認 entry_module "
                "存在且可正常匯入。"
            ),
        ))
    return issues


def _is_runtime_scene_yaml(path: Path, content_root: Path) -> bool:
    """Whether the runtime scene loader will execute this YAML file."""
    try:
        path.relative_to(content_root / "scenes")
    except ValueError:
        return False
    return True


def validate_pack(pack_root: Path) -> list[ValidationIssue]:
    """Validate a pack with its plugin-provided schema kinds active.

    The effect/condition registries are process-global. Capture them before
    loading this pack's plugins and restore them in ``finally`` so validating
    one pack cannot change the result of a later validation.
    """
    from world_gal_game import __version__ as engine_version
    from world_gal_game.plugins import (
        PluginContext,
        PluginManager,
        restore,
        snapshot,
    )

    validation_root = Path(pack_root).resolve()
    runtime_pack_root = _plugin_pack_root(validation_root)
    registry_snapshot = snapshot()
    plugin_issues: list[ValidationIssue] = []

    try:
        manager = PluginManager(
            pack_root=runtime_pack_root,
            engine_version=engine_version,
        )
        try:
            manager.discover()
            manager.activate(context=PluginContext(
                state=None,
                meta={},
                pack_root=runtime_pack_root,
            ))
        except Exception as exc:
            # PluginManager normally records per-plugin failures. Keep a
            # defensive issue here for discovery/filesystem failures that
            # happen outside that isolation boundary.
            plugin_issues.append(ValidationIssue(
                severity="error",
                file="plugins",
                path="",
                message=f"pack plugin discovery/activation failed: {exc}",
                hint="檢查 plugins/ 目錄權限、plugin.yaml 與 entry_module。",
            ))
        plugin_issues.extend(_plugin_failure_issues(
            manager, pack_root=runtime_pack_root))
        return plugin_issues + _validate_pack_content(validation_root)
    finally:
        restore(registry_snapshot)


def _validate_pack_content(pack_root: Path) -> list[ValidationIssue]:
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

    runtime_yamls = [
        path
        for path in (
            sorted(content_root.rglob("*.yaml"))
            + sorted(content_root.rglob("*.yml"))
        )
        if path.suffix in {".yaml", ".yml"}
    ]
    cg_gallery_case_aliases = sorted(
        path
        for path in content_root.iterdir()
        if (
            path.is_file()
            and path.name.casefold() == "cg_gallery.yaml"
            and path.name != "cg_gallery.yaml"
        )
    )
    music_room_case_aliases = sorted(
        path
        for path in content_root.iterdir()
        if (
            path.is_file()
            and path.name.casefold() == "music_room.yaml"
            and path.name != "music_room.yaml"
        )
    )
    # Match the runtime loader's case-sensitive YAML patterns everywhere
    # except for the one root catalog alias we explicitly diagnose. This
    # avoids validating uppercase scene extensions that Linux runtime ignores.
    all_yamls = [
        *runtime_yamls,
        *cg_gallery_case_aliases,
        *music_room_case_aliases,
    ]
    # Keep explicit deduplication for unusual filesystem aliases.
    seen_paths: set[Path] = set()
    yaml_files: list[Path] = []
    for p in all_yamls:
        if p not in seen_paths:
            seen_paths.add(p)
            yaml_files.append(p)

    canonical_cg_gallery_path = content_root / "cg_gallery.yaml"
    cg_gallery_aliases = [
        path
        for path in yaml_files
        if (
            path.parent == content_root
            and path.name.casefold() == "cg_gallery.yaml"
            and path != canonical_cg_gallery_path
        )
    ]
    for alias in cg_gallery_aliases:
        issues.append(ValidationIssue(
            severity="error",
            file=_rel(pack_root, alias),
            path="filename",
            message=(
                "CG gallery 檔名必須精確為 "
                "'content/cg_gallery.yaml'（含大小寫）。"
            ),
            hint=(
                "重新命名為 cg_gallery.yaml，避免 macOS 與 Linux "
                "對大小寫敏感度不同而產生跨平台行為差異。"
            ),
        ))
    cg_gallery_source_path = (
        canonical_cg_gallery_path
        if canonical_cg_gallery_path in yaml_files
        else (cg_gallery_aliases[0] if cg_gallery_aliases else None)
    )
    canonical_music_room_path = content_root / "music_room.yaml"
    music_room_aliases = [
        path
        for path in yaml_files
        if (
            path.parent == content_root
            and path.name.casefold() == "music_room.yaml"
            and path != canonical_music_room_path
        )
    ]
    for alias in music_room_aliases:
        issues.append(ValidationIssue(
            severity="error",
            file=_rel(pack_root, alias),
            path="filename",
            message=(
                "Music Room 檔名必須精確為 "
                "'content/music_room.yaml'（含大小寫）。"
            ),
            hint=(
                "重新命名為 music_room.yaml，避免 macOS 與 Linux "
                "對大小寫敏感度不同而產生跨平台行為差異。"
            ),
        ))
    music_room_source_path = (
        canonical_music_room_path
        if canonical_music_room_path in yaml_files
        else (music_room_aliases[0] if music_room_aliases else None)
    )

    for yaml_path in yaml_files:
        rel = _rel(pack_root, yaml_path)
        data, parse_issues = _parse_yaml_file(
            yaml_path,
            strict=yaml_path == music_room_source_path,
        )
        for iss in parse_issues:
            iss.file = rel
        issues.extend(parse_issues)
        if data is None:
            if (
                yaml_path in {
                    cg_gallery_source_path,
                    music_room_source_path,
                }
                and not parse_issues
            ):
                # Runtime treats a physically present blank catalog as an
                # explicitly authored empty roster, not as a missing legacy
                # catalog. Preserve that distinction so coverage warnings
                # match what players will see.
                data = {"entries": []}
            else:
                continue
        parsed[yaml_path] = data

        is_scene_yaml = _is_runtime_scene_yaml(yaml_path, content_root)
        if is_scene_yaml:
            issues.extend(_validate_scenes_yaml(
                data, file=rel, pack_root=pack_root))
        elif yaml_path == content_root / "locations.yaml":
            issues.extend(_validate_locations_yaml(data, file=rel))
        elif yaml_path == content_root / "characters.yaml":
            issues.extend(_validate_characters_yaml(data, file=rel))
        elif yaml_path == content_root / "items.yaml":
            issues.extend(_validate_items_yaml(
                data,
                file=rel,
                pack_root=pack_root,
            ))
        elif yaml_path == content_root / "resources.yaml":
            issues.extend(_validate_resources_yaml(data, file=rel))
        elif yaml_path == content_root / "achievements.yaml":
            issues.extend(_validate_achievements_yaml(data, file=rel))
        elif yaml_path == cg_gallery_source_path:
            issues.extend(_validate_cg_gallery_yaml(
                data,
                file=rel,
                pack_root=pack_root,
            ))
        elif yaml_path == music_room_source_path:
            issues.extend(_validate_music_room_yaml(
                data,
                file=rel,
                pack_root=pack_root,
            ))
        else:
            # Unknown YAML — at least it parsed; no schema to check
            pass

    # --- Step 3: build reference index ---
    index = _RefIndex()

    for yaml_path, data in parsed.items():
        if _is_runtime_scene_yaml(yaml_path, content_root):
            index.scene_ids |= _collect_scene_ids(data)
        elif yaml_path == content_root / "characters.yaml":
            index.character_ids |= _collect_simple_ids(data, "characters")
            ps, nm, heroines = _collect_character_portraits(data)
            index.character_portraits.update(ps)
            index.character_names.update(nm)
            index.heroine_ids |= heroines
        elif yaml_path == content_root / "locations.yaml":
            index.location_ids |= _collect_simple_ids(data, "locations")
        elif yaml_path == content_root / "items.yaml":
            index.item_ids |= _collect_simple_ids(data, "items")
        elif yaml_path == content_root / "resources.yaml":
            if isinstance(data, dict) and "resources" in data:
                for r in (data["resources"] or []):
                    if isinstance(r, dict) and "id" in r:
                        index.resource_ids.add(r["id"])
            elif isinstance(data, list):
                for r in data:
                    if isinstance(r, dict) and "id" in r:
                        index.resource_ids.add(r["id"])
        elif yaml_path == content_root / "endings.yaml":
            index.ending_ids |= _collect_simple_ids(data, "endings")
        elif yaml_path == content_root / "chapters.yaml":
            index.chapter_ids |= _collect_simple_ids(data, "chapters")

    # Also collect resources from meta.yaml
    meta_path = content_root / "meta.yaml"
    if meta_path in parsed:
        meta = parsed[meta_path]
        if isinstance(meta, dict):
            for r in (meta.get("resources") or []):
                if isinstance(r, dict) and "id" in r:
                    index.resource_ids.add(r["id"])

    spatial_locations = _collect_spatial_location_records(
        parsed,
        pack_root=pack_root,
    )

    # --- Step 4: cross-file reference checks ---
    for yaml_path, data in parsed.items():
        rel = _rel(pack_root, yaml_path)
        if _is_runtime_scene_yaml(yaml_path, content_root):
            issues.extend(_check_refs_scenes(data, file=rel, index=index))
        elif yaml_path == content_root / "locations.yaml":
            issues.extend(_check_refs_locations(data, file=rel, index=index))

    if (
        cg_gallery_source_path is not None
        and cg_gallery_source_path in parsed
    ):
        cg_file = _rel(pack_root, cg_gallery_source_path)
        cg_data = parsed[cg_gallery_source_path]
        issues.extend(_validate_cg_gallery_refs(
            cg_data,
            file=cg_file,
            character_ids=index.character_ids,
            chapter_ids=index.chapter_ids,
        ))
        try:
            catalog_assets = {
                entry.asset
                for entry in _cg_manifest_from_data(cg_data).entries
            }
            catalog_valid = True
        except (TypeError, ValueError):
            catalog_assets = set()
            catalog_valid = False
        if catalog_valid:
            referenced_assets: set[str] = set()
            for source_path, source_data in parsed.items():
                if (
                    _is_runtime_scene_yaml(source_path, content_root)
                    or source_path == content_root / "items.yaml"
                ):
                    referenced_assets.update(
                        _collect_cg_references(source_data)
                    )
            for asset in sorted(referenced_assets - catalog_assets):
                issues.append(ValidationIssue(
                    severity="warning",
                    file=cg_file,
                    path="entries",
                    message=(
                        f"劇情使用的 CG '{asset}' "
                        "未在 cg_gallery.yaml 宣告。"
                    ),
                    hint=(
                        "補上 catalog entry；未宣告 CG 只有在舊存檔或"
                        "執行期已解鎖時才會以檔名 fallback 顯示。"
                    ),
                ))

    if (
        music_room_source_path is not None
        and music_room_source_path in parsed
    ):
        music_file = _rel(pack_root, music_room_source_path)
        music_data = parsed[music_room_source_path]
        try:
            catalog_assets = {
                entry.asset
                for entry in _music_manifest_from_data(music_data).entries
            }
            catalog_valid = True
        except (TypeError, ValueError):
            catalog_assets = set()
            catalog_valid = False
        if catalog_valid:
            referenced_assets: set[str] = set()
            for source_path, source_data in parsed.items():
                if _is_runtime_scene_yaml(source_path, content_root):
                    referenced_assets.update(
                        _collect_music_references(source_data)
                    )
            meta_data = parsed.get(content_root / "meta.yaml")
            if isinstance(meta_data, dict):
                from .core.music_room import canonical_music_path

                title_bgm = meta_data.get("title_bgm")
                if isinstance(title_bgm, str) and title_bgm.strip():
                    referenced_assets.add(canonical_music_path(title_bgm))

            bgm_root = pack_root / "assets" / "bgm"
            try:
                referenced_assets.update(
                    f"assets/bgm/{entry.name}"
                    for entry in bgm_root.iterdir()
                    if entry.is_file()
                    and entry.suffix.lower() in {".ogg", ".mp3", ".wav"}
                )
            except OSError:
                pass

            for asset in sorted(referenced_assets - catalog_assets):
                issues.append(ValidationIssue(
                    severity="warning",
                    file=music_file,
                    path="entries",
                    message=(
                        f"遊戲使用或收錄的 BGM '{asset}' "
                        "未在 music_room.yaml 宣告。"
                    ),
                    hint=(
                        "補上 catalog entry；未宣告曲目只有在舊存檔或"
                        "執行期已解鎖時才會以檔名 fallback 顯示。"
                    ),
                ))

    # --- Step 4.5: meta.yaml advisory checks (pack_format_version) ---
    meta_path = content_root / "meta.yaml"
    if meta_path in parsed:
        meta = parsed[meta_path]
        if isinstance(meta, dict):
            meta_file = _rel(pack_root, meta_path)
            if "pack_format_version" not in meta:
                issues.append(ValidationIssue(
                    severity="warning",
                    file=meta_file,
                    path="pack_format_version",
                    message="meta.yaml 沒有 pack_format_version 欄位。",
                    hint='加上 `pack_format_version: "0.1"` 以鎖定 schema 版本。',
                ))
            issues.extend(_validate_title_metadata(
                meta,
                file=meta_file,
                pack_root=pack_root,
            ))
            issues.extend(_validate_locale_metadata(
                meta,
                file=meta_file,
                pack_root=pack_root,
            ))
            issues.extend(_validate_ui_skin_metadata(
                meta,
                file=meta_file,
            ))
            issues.extend(_validate_spatial_map_metadata(
                meta,
                file=meta_file,
                pack_root=pack_root,
                locations=spatial_locations,
                scene_ids=index.scene_ids,
            ))
            from .dev.verification_contract import audit_expected_unreachable
            verification_audit = audit_expected_unreachable(
                meta,
                scene_ids=index.scene_ids,
                ending_ids=index.ending_ids,
            )
            for problem in verification_audit.issues:
                issues.append(ValidationIssue(
                    severity=problem.severity,
                    file=meta_file,
                    path=problem.path,
                    message=problem.message,
                    hint=problem.hint,
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
        from .dev.verification_contract import audit_expected_unreachable
        inspector = PackInspector(pack_root)
        reachability = inspector.reachability()
        verification_audit = audit_expected_unreachable(
            inspector.meta,
            scene_ids=set(inspector.scenes_by_id),
            ending_ids={
                str(ending["id"])
                for ending in inspector.endings
                if isinstance(ending, dict) and ending.get("id")
            },
            unreachable_scene_ids=set(reachability["unreachable"]),
        )
        for problem in verification_audit.issues:
            if problem.code != "stale_scene":
                continue
            issues.append(ValidationIssue(
                severity=problem.severity,
                file=_rel(pack_root, content_root / "meta.yaml"),
                path=problem.path,
                message=problem.message,
                hint=problem.hint,
            ))
        for de in inspector.dead_ends():
            if (
                de.kind == "unreachable"
                and de.target in verification_audit.active_scene_ids
            ):
                continue
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

"""Auditable exceptions for deliberately unreachable legacy content.

Some packs must retain a retired scene or ending so an old save, gallery
replay, or explicit ``start_scene`` operation can still load it.  Such content
must remain unreachable during an ordinary new game, which conflicts with the
default ``SelfCheck`` dead-end and ending-strand gates.

The opt-in contract lives in ``content/meta.yaml``::

    verification:
      expected_unreachable:
        legacy_no_one:
          scenes: [ending_no_one_scene]
          endings: [ending_no_one]
          reason: "Retained for old saves; the current route choice is forced."

The contract is deliberately strict and fail-closed:

* every case has a non-empty audit reason and at least one target;
* the only target kinds are ``scenes`` and ``endings``;
* duplicate, empty, and unknown ids are rejected;
* a target is ignored only while the corresponding analysis still proves it
  unreachable (scene) or stranded (ending);
* if a target becomes reachable, the declaration is stale and verification
  fails until the obsolete exception is removed.

``PackInspector`` itself does not consume this contract.  Its raw reachability
and dead-end output therefore remains an objective world-model result.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)


class ExpectedUnreachableCase(BaseModel):
    """One documented compatibility case and all of its affected targets."""

    model_config = ConfigDict(extra="forbid")

    scenes: list[str] = Field(default_factory=list)
    endings: list[str] = Field(default_factory=list)
    reason: str

    @field_validator("scenes", "endings")
    @classmethod
    def _validate_target_ids(cls, value: list[str]) -> list[str]:
        cleaned = [target.strip() for target in value]
        if any(not target for target in cleaned):
            raise ValueError("target ids must be non-empty strings")
        if len(cleaned) != len(set(cleaned)):
            raise ValueError("target ids must not contain duplicates")
        return cleaned

    @field_validator("reason")
    @classmethod
    def _validate_reason(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("reason must be a non-empty string")
        return cleaned

    @model_validator(mode="after")
    def _require_a_target(self) -> "ExpectedUnreachableCase":
        if not self.scenes and not self.endings:
            raise ValueError("at least one scene or ending is required")
        return self


class VerificationConfig(BaseModel):
    """Strict schema for the ``meta.yaml`` ``verification`` block."""

    model_config = ConfigDict(extra="forbid")

    expected_unreachable: dict[str, ExpectedUnreachableCase] = Field(
        default_factory=dict,
    )

    @field_validator("expected_unreachable")
    @classmethod
    def _validate_case_ids(
        cls,
        value: dict[str, ExpectedUnreachableCase],
    ) -> dict[str, ExpectedUnreachableCase]:
        if any(
            not case_id.strip() or case_id != case_id.strip()
            for case_id in value
        ):
            raise ValueError(
                "case ids must be non-empty strings without outer whitespace"
            )
        return value


@dataclass(frozen=True)
class VerificationContractIssue:
    """One structural, reference, or stale-declaration finding."""

    severity: Literal["error", "warning"]
    code: str
    path: str
    message: str
    hint: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "severity": self.severity,
            "code": self.code,
            "path": self.path,
            "message": self.message,
            "hint": self.hint,
        }


@dataclass(frozen=True)
class ExpectedUnreachableRef:
    """A scene or ending reference resolved from one compatibility case."""

    case_id: str
    kind: Literal["scene", "ending"]
    target: str
    reason: str

    def to_dict(self, *, status: str | None = None) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "case_id": self.case_id,
            "kind": self.kind,
            "target": self.target,
            "reason": self.reason,
        }
        if status is not None:
            payload["status"] = status
        return payload


@dataclass
class ExpectedUnreachableAudit:
    """Resolved verification contract plus its current analysis status."""

    declared_scenes: list[ExpectedUnreachableRef] = field(default_factory=list)
    declared_endings: list[ExpectedUnreachableRef] = field(default_factory=list)
    active_scenes: list[ExpectedUnreachableRef] = field(default_factory=list)
    active_endings: list[ExpectedUnreachableRef] = field(default_factory=list)
    stale_scenes: list[ExpectedUnreachableRef] = field(default_factory=list)
    stale_endings: list[ExpectedUnreachableRef] = field(default_factory=list)
    issues: list[VerificationContractIssue] = field(default_factory=list)

    @property
    def active_scene_ids(self) -> set[str]:
        return {ref.target for ref in self.active_scenes}

    @property
    def active_ending_ids(self) -> set[str]:
        return {ref.target for ref in self.active_endings}

    def scene_ref(self, scene_id: str) -> ExpectedUnreachableRef | None:
        return next(
            (ref for ref in self.active_scenes if ref.target == scene_id),
            None,
        )

    def ending_ref(self, ending_id: str) -> ExpectedUnreachableRef | None:
        return next(
            (ref for ref in self.active_endings if ref.target == ending_id),
            None,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "declared": [
                ref.to_dict()
                for ref in self.declared_scenes + self.declared_endings
            ],
            "active": [
                ref.to_dict(status="unreachable")
                for ref in self.active_scenes
            ] + [
                ref.to_dict(status="strand")
                for ref in self.active_endings
            ],
            "stale": [
                ref.to_dict(status="reachable")
                for ref in self.stale_scenes + self.stale_endings
            ],
            "issues": [issue.to_dict() for issue in self.issues],
        }


def _validation_error_issues(
    exc: ValidationError,
) -> list[VerificationContractIssue]:
    issues: list[VerificationContractIssue] = []
    for error in exc.errors():
        loc = ".".join(str(part) for part in error["loc"])
        path = "verification" + (f".{loc}" if loc else "")
        issues.append(VerificationContractIssue(
            severity="error",
            code="invalid_contract",
            path=path,
            message=f"verification.expected_unreachable 格式無效：{error['msg']}",
            hint=(
                "每個 case 必須是 {scenes: [...], endings: [...], "
                "reason: 非空字串}，且至少列出一個 target。"
            ),
        ))
    return issues


def audit_expected_unreachable(
    meta: dict[str, Any] | Any,
    *,
    scene_ids: set[str] | None = None,
    ending_ids: set[str] | None = None,
    unreachable_scene_ids: set[str] | None = None,
    ending_statuses: dict[str, str] | None = None,
) -> ExpectedUnreachableAudit:
    """Parse and, where data is supplied, resolve the verification contract.

    ``scene_ids`` / ``ending_ids`` enable typo and wrong-kind checks.
    ``unreachable_scene_ids`` enables current scene-status auditing.
    ``ending_statuses`` maps ending ids to the reachability checker's
    ``ok`` / ``strand`` / ``unverified`` verdicts.

    Omitted analysis inputs do not imply a verdict; they simply leave the
    corresponding references in ``declared_*`` only.
    """

    audit = ExpectedUnreachableAudit()
    if not isinstance(meta, dict) or "verification" not in meta:
        return audit

    try:
        config = VerificationConfig.model_validate(meta["verification"])
    except ValidationError as exc:
        audit.issues.extend(_validation_error_issues(exc))
        return audit

    seen_scenes: dict[str, str] = {}
    seen_endings: dict[str, str] = {}
    for case_id, case in config.expected_unreachable.items():
        for index, scene_id in enumerate(case.scenes):
            ref = ExpectedUnreachableRef(
                case_id=case_id,
                kind="scene",
                target=scene_id,
                reason=case.reason,
            )
            audit.declared_scenes.append(ref)
            path = (
                "verification.expected_unreachable."
                f"{case_id}.scenes[{index}]"
            )
            previous_case = seen_scenes.get(scene_id)
            if previous_case is not None:
                audit.issues.append(VerificationContractIssue(
                    severity="error",
                    code="duplicate_scene",
                    path=path,
                    message=(
                        f"scene '{scene_id}' 已由 case '{previous_case}' 宣告；"
                        "同一 target 不可跨 case 重複。"
                    ),
                    hint="合併兩個 compatibility case，保留一份明確理由。",
                ))
                continue
            seen_scenes[scene_id] = case_id
            if scene_ids is not None and scene_id not in scene_ids:
                audit.issues.append(VerificationContractIssue(
                    severity="error",
                    code="unknown_scene",
                    path=path,
                    message=(
                        f"預期不可達 target '{scene_id}' 不是已知 scene id。"
                    ),
                    hint="修正 scene id；location、chapter 或檔名不能代替 scene id。",
                ))
                continue
            if unreachable_scene_ids is not None:
                if scene_id in unreachable_scene_ids:
                    audit.active_scenes.append(ref)
                else:
                    audit.stale_scenes.append(ref)
                    audit.issues.append(VerificationContractIssue(
                        severity="warning",
                        code="stale_scene",
                        path=path,
                        message=(
                            f"預期不可達 scene '{scene_id}' 現在已可達；"
                            "exception 已過期。"
                        ),
                        hint="移除這筆 target，或修正意外重新接回的故事入口。",
                    ))

        for index, ending_id in enumerate(case.endings):
            ref = ExpectedUnreachableRef(
                case_id=case_id,
                kind="ending",
                target=ending_id,
                reason=case.reason,
            )
            audit.declared_endings.append(ref)
            path = (
                "verification.expected_unreachable."
                f"{case_id}.endings[{index}]"
            )
            previous_case = seen_endings.get(ending_id)
            if previous_case is not None:
                audit.issues.append(VerificationContractIssue(
                    severity="error",
                    code="duplicate_ending",
                    path=path,
                    message=(
                        f"ending '{ending_id}' 已由 case '{previous_case}' 宣告；"
                        "同一 target 不可跨 case 重複。"
                    ),
                    hint="合併兩個 compatibility case，保留一份明確理由。",
                ))
                continue
            seen_endings[ending_id] = case_id
            if ending_ids is not None and ending_id not in ending_ids:
                audit.issues.append(VerificationContractIssue(
                    severity="error",
                    code="unknown_ending",
                    path=path,
                    message=(
                        f"預期不可達 target '{ending_id}' 不是已知 ending id。"
                    ),
                    hint="修正 endings.yaml 中的 ending id；scene id 不能代替 ending id。",
                ))
                continue
            if ending_statuses is not None:
                status = ending_statuses.get(ending_id)
                if status == "strand":
                    audit.active_endings.append(ref)
                else:
                    audit.stale_endings.append(ref)
                    audit.issues.append(VerificationContractIssue(
                        severity="warning",
                        code="stale_ending",
                        path=path,
                        message=(
                            f"預期不可達 ending '{ending_id}' 現在的 verdict "
                            f"是 '{status or 'missing'}'，不是 strand；"
                            "exception 已過期。"
                        ),
                        hint="移除這筆 target，或修正意外重新開放的結局入口。",
                    ))

    return audit

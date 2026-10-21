"""Stable, player-facing keyboard action bindings.

The runtime deals in semantic actions (``confirm_advance``, ``open_map``, ...)
instead of making every scene know about SDL key constants.  This module owns
the default keyboard profile, robust settings-file normalisation, conflict
queries, and the pure rebind operation used by the controls UI.

Only keyboard bindings live here for now.  Controller input is translated
directly to the same actions by :mod:`world_gal_game.ui.input`, so changing a
keyboard binding can never strand a connected gamepad.
"""
from __future__ import annotations

import warnings
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

import pygame


class InputAction:
    """Stable string constants persisted in ``settings.json``."""

    CONFIRM_ADVANCE = "confirm_advance"
    BACK = "back"
    OPEN_MENU = "open_menu"
    NAV_UP = "nav_up"
    NAV_DOWN = "nav_down"
    NAV_LEFT = "nav_left"
    NAV_RIGHT = "nav_right"
    FOCUS_NEXT = "focus_next"
    FOCUS_PREVIOUS = "focus_previous"
    PAGE_UP = "page_up"
    PAGE_DOWN = "page_down"
    TOGGLE_AUTO = "toggle_auto"
    HOLD_SKIP = "hold_skip"
    OPEN_HISTORY = "open_history"
    ROLLBACK = "rollback"
    HIDE_UI = "hide_ui"
    OPEN_MAP = "open_map"
    OPEN_AFFECTION = "open_affection"
    OPEN_EVENT_LOG = "open_event_log"
    OPEN_SAVE = "open_save"
    OPEN_ACHIEVEMENTS = "open_achievements"
    OPEN_INVENTORY = "open_inventory"
    OPEN_CLUES = "open_clues"
    QUICKSAVE = "quicksave"
    QUICKLOAD = "quickload"
    SCREENSHOT = "screenshot"
    TOGGLE_FULLSCREEN = "toggle_fullscreen"
    MENU_SECTION_PREVIOUS = "menu_section_previous"
    MENU_SECTION_NEXT = "menu_section_next"


@dataclass(frozen=True)
class ActionSpec:
    """Metadata needed by routing, settings UI, and conflict detection."""

    id: str
    label: str
    group: str
    defaults: tuple[str | int, ...]
    contexts: frozenset[str]
    required: bool = False


def _spec(
    action: str,
    label: str,
    group: str,
    defaults: Sequence[str | int],
    contexts: Sequence[str],
    *,
    required: bool = False,
) -> ActionSpec:
    return ActionSpec(
        id=action,
        label=label,
        group=group,
        defaults=tuple(defaults),
        contexts=frozenset(contexts),
        required=required,
    )


# Contexts are interaction layers, not merely scene class names.  In
# particular, navigation and exploration shortcuts intentionally do not
# overlap: A/S are established, context-sensitive defaults (left/down in a
# grid; affection/save on the exploration screen) and must not be reported as
# conflicts.
ACTION_SPECS: dict[str, ActionSpec] = {
    InputAction.CONFIRM_ADVANCE: _spec(
        InputAction.CONFIRM_ADVANCE,
        "確認／推進",
        "基本",
        ("return", "enter", "space", "z"),
        (
            "title",
            "reading",
            "choice",
            "exploration",
            "navigation",
            "overlay",
            "settings",
        ),
        required=True,
    ),
    InputAction.BACK: _spec(
        InputAction.BACK,
        "返回／取消",
        "基本",
        ("escape", "x"),
        ("title", "navigation", "overlay", "settings"),
        required=True,
    ),
    InputAction.OPEN_MENU: _spec(
        InputAction.OPEN_MENU,
        "開啟選單",
        "基本",
        ("escape", "x"),
        ("reading", "exploration"),
    ),
    InputAction.NAV_UP: _spec(
        InputAction.NAV_UP,
        "向上移動",
        "導覽",
        ("up", "w"),
        ("title", "choice", "navigation", "settings", "map", "gallery"),
        required=True,
    ),
    InputAction.NAV_DOWN: _spec(
        InputAction.NAV_DOWN,
        "向下移動",
        "導覽",
        ("down", "s"),
        ("title", "choice", "navigation", "settings", "map", "gallery"),
        required=True,
    ),
    InputAction.NAV_LEFT: _spec(
        InputAction.NAV_LEFT,
        "向左移動",
        "導覽",
        ("left", "a"),
        ("choice", "navigation", "settings", "map", "gallery"),
        required=True,
    ),
    InputAction.NAV_RIGHT: _spec(
        InputAction.NAV_RIGHT,
        "向右移動",
        "導覽",
        ("right", "d"),
        ("choice", "navigation", "settings", "map", "gallery"),
        required=True,
    ),
    InputAction.FOCUS_NEXT: _spec(
        InputAction.FOCUS_NEXT,
        "下一個控制項",
        "導覽",
        ("tab",),
        ("reading", "exploration", "settings", "map", "navigation"),
    ),
    InputAction.FOCUS_PREVIOUS: _spec(
        InputAction.FOCUS_PREVIOUS,
        "上一個控制項",
        "導覽",
        (),
        ("reading", "exploration", "settings", "map", "navigation"),
    ),
    InputAction.PAGE_UP: _spec(
        InputAction.PAGE_UP,
        "上一頁",
        "導覽",
        ("page up",),
        ("overlay", "settings", "map", "navigation"),
    ),
    InputAction.PAGE_DOWN: _spec(
        InputAction.PAGE_DOWN,
        "下一頁",
        "導覽",
        ("page down",),
        ("overlay", "settings", "map", "navigation"),
    ),
    InputAction.TOGGLE_AUTO: _spec(
        InputAction.TOGGLE_AUTO,
        "切換自動播放",
        "閱讀",
        ("a",),
        ("reading",),
    ),
    InputAction.HOLD_SKIP: _spec(
        InputAction.HOLD_SKIP,
        "按住快進",
        "閱讀",
        ("left ctrl", "right ctrl"),
        ("reading",),
    ),
    InputAction.OPEN_HISTORY: _spec(
        InputAction.OPEN_HISTORY,
        "開啟閱讀記錄",
        "閱讀",
        ("b",),
        ("reading",),
    ),
    InputAction.ROLLBACK: _spec(
        InputAction.ROLLBACK,
        "回溯上一句",
        "閱讀",
        ("backspace",),
        ("reading",),
    ),
    InputAction.HIDE_UI: _spec(
        InputAction.HIDE_UI,
        "隱藏介面",
        "閱讀",
        ("h",),
        ("reading", "exploration"),
    ),
    InputAction.OPEN_MAP: _spec(
        InputAction.OPEN_MAP,
        "開啟地圖",
        "探索",
        ("m",),
        ("exploration",),
    ),
    InputAction.OPEN_AFFECTION: _spec(
        InputAction.OPEN_AFFECTION,
        "開啟好感資訊",
        "探索",
        ("a",),
        ("exploration",),
    ),
    InputAction.OPEN_EVENT_LOG: _spec(
        InputAction.OPEN_EVENT_LOG,
        "開啟事件記錄",
        "探索",
        ("l",),
        ("exploration",),
    ),
    InputAction.OPEN_SAVE: _spec(
        InputAction.OPEN_SAVE,
        "開啟存檔",
        "探索",
        ("s",),
        ("exploration",),
    ),
    InputAction.OPEN_ACHIEVEMENTS: _spec(
        InputAction.OPEN_ACHIEVEMENTS,
        "開啟成就",
        "探索",
        ("t",),
        ("exploration",),
    ),
    InputAction.OPEN_INVENTORY: _spec(
        InputAction.OPEN_INVENTORY,
        "開啟物品",
        "探索",
        ("i",),
        ("exploration",),
    ),
    InputAction.OPEN_CLUES: _spec(
        InputAction.OPEN_CLUES,
        "開啟線索",
        "探索",
        ("j",),
        ("exploration",),
    ),
    InputAction.QUICKSAVE: _spec(
        InputAction.QUICKSAVE,
        "快速存檔",
        "系統",
        ("f6",),
        ("*",),
    ),
    InputAction.QUICKLOAD: _spec(
        InputAction.QUICKLOAD,
        "快速讀取",
        "系統",
        ("f9",),
        ("*",),
    ),
    InputAction.SCREENSHOT: _spec(
        InputAction.SCREENSHOT,
        "擷取畫面",
        "系統",
        ("f12",),
        ("*",),
    ),
    InputAction.TOGGLE_FULLSCREEN: _spec(
        InputAction.TOGGLE_FULLSCREEN,
        "切換全螢幕",
        "系統",
        ("f",),
        ("settings",),
    ),
    InputAction.MENU_SECTION_PREVIOUS: _spec(
        InputAction.MENU_SECTION_PREVIOUS,
        "上一個選單分頁",
        "導覽",
        ("q",),
        ("navigation", "settings"),
    ),
    InputAction.MENU_SECTION_NEXT: _spec(
        InputAction.MENU_SECTION_NEXT,
        "下一個選單分頁",
        "導覽",
        ("e",),
        ("navigation", "settings"),
    ),
}


_ALIASES = {
    "esc": "escape",
    "kp enter": "enter",
    "keypad enter": "enter",
    "pgup": "page up",
    "pgdn": "page down",
    "pageup": "page up",
    "pagedown": "page down",
    "lctrl": "left ctrl",
    "rctrl": "right ctrl",
}

_DISPLAY_NAMES = {
    "return": "Enter",
    "enter": "數字鍵 Enter",
    "escape": "Esc",
    "space": "Space",
    "tab": "Tab",
    "backspace": "Backspace",
    "delete": "Delete",
    "left ctrl": "左 Ctrl",
    "right ctrl": "右 Ctrl",
    "page up": "Page Up",
    "page down": "Page Down",
    "up": "↑",
    "down": "↓",
    "left": "←",
    "right": "→",
}

_RESERVED_KEY_CODES = frozenset({
    pygame.K_F1,
    pygame.K_F5,
    pygame.K_F11,
})


def normalize_key_name(value: Any) -> str | None:
    """Return a portable SDL key name, or ``None`` for malformed input."""

    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        key_code = value
    elif isinstance(value, str):
        raw = value.strip().lower()
        if raw.startswith("key:"):
            raw = raw[4:].strip()
        raw = _ALIASES.get(raw, raw)
        if not raw:
            return None
        if raw.lstrip("-").isdigit():
            try:
                key_code = int(raw)
            except ValueError:
                return None
        else:
            try:
                with warnings.catch_warnings():
                    # SDL key-name conversion is deterministic before video
                    # startup, but pygame emits a misleading init warning.
                    # Settings loading must remain quiet and side-effect free.
                    warnings.filterwarnings(
                        "ignore",
                        message=r"pygame\.init\(\) has not been called.*",
                    )
                    key_code = pygame.key.key_code(raw)
            except (TypeError, ValueError):
                return None
    else:
        return None
    try:
        with warnings.catch_warnings():
            warnings.filterwarnings(
                "ignore",
                message=r"pygame\.init\(\) has not been called.*",
            )
            name = pygame.key.name(int(key_code)).strip().lower()
    except (TypeError, ValueError):
        return None
    if not name or name == "unknown key":
        return None
    return _ALIASES.get(name, name)


def key_code(value: Any) -> int | None:
    """Resolve a supported key name/code to its pygame integer code."""

    name = normalize_key_name(value)
    if name is None:
        return None
    try:
        with warnings.catch_warnings():
            warnings.filterwarnings(
                "ignore",
                message=r"pygame\.init\(\) has not been called.*",
            )
            return int(pygame.key.key_code(name))
    except (TypeError, ValueError):
        return None


def is_reserved_key(value: Any) -> bool:
    resolved = key_code(value)
    return resolved in _RESERVED_KEY_CODES if resolved is not None else False


def _default_names(action: str) -> tuple[str, ...]:
    spec = ACTION_SPECS[action]
    out: list[str] = []
    for raw in spec.defaults:
        name = normalize_key_name(raw)
        if name is not None and name not in out:
            out.append(name)
    return tuple(out)


def normalize_binding_overrides(raw: Any) -> dict[str, list[str]]:
    """Sanitise untrusted settings JSON into a minimal override mapping.

    Unknown actions and malformed values are ignored.  An explicit empty list
    unbinds an optional action, while a required action always falls back to
    its defaults.  A non-empty list containing only invalid/reserved values is
    treated as corrupt input rather than an intentional unbind.
    """

    if not isinstance(raw, Mapping):
        return {}
    out: dict[str, list[str]] = {}
    for action, values in raw.items():
        if action not in ACTION_SPECS or not isinstance(values, (list, tuple)):
            continue
        names: list[str] = []
        for value in values:
            name = normalize_key_name(value)
            if (
                name is not None
                and not is_reserved_key(name)
                and name not in names
            ):
                names.append(name)
        spec = ACTION_SPECS[action]
        if not names:
            if spec.required:
                continue
            if len(values) == 0:
                out[action] = []
            # A non-empty but wholly invalid value is ignored.
            continue
        if tuple(names) != _default_names(action):
            out[action] = names
    return out


def _contexts_overlap(left: ActionSpec, right: ActionSpec) -> bool:
    return (
        "*" in left.contexts
        or "*" in right.contexts
        or bool(left.contexts & right.contexts)
    )


class BindingProfile:
    """Validated effective bindings derived from optional user overrides."""

    def __init__(self, overrides: Any = None):
        self._overrides = normalize_binding_overrides(overrides)

    @property
    def overrides(self) -> dict[str, list[str]]:
        return {action: list(keys) for action, keys in self._overrides.items()}

    normalized_overrides = overrides

    def names_for(self, action: str) -> tuple[str, ...]:
        if action not in ACTION_SPECS:
            return ()
        if action in self._overrides:
            return tuple(self._overrides[action])
        return _default_names(action)

    def keys_for(self, action: str) -> tuple[int, ...]:
        keys: list[int] = []
        for name in self.names_for(action):
            resolved = key_code(name)
            if resolved is not None and resolved not in keys:
                keys.append(resolved)
        return tuple(keys)

    def actions_for_key(
        self,
        key: str | int,
        *,
        context: str | None = None,
    ) -> tuple[str, ...]:
        resolved = key_code(key)
        if resolved is None:
            return ()
        actions: list[str] = []
        for action, spec in ACTION_SPECS.items():
            if context is not None and (
                "*" not in spec.contexts and context not in spec.contexts
            ):
                continue
            if resolved in self.keys_for(action):
                actions.append(action)
        return tuple(actions)

    def conflicts_for(self, action: str, key: str | int) -> tuple[str, ...]:
        spec = ACTION_SPECS.get(action)
        if spec is None:
            return ()
        return tuple(
            other
            for other in self.actions_for_key(key)
            if other != action
            and _contexts_overlap(spec, ACTION_SPECS[other])
        )

    # Explicit alias used by settings/QA code.
    context_conflicts = conflicts_for

    def display(self, action: str, *, concise: bool = False) -> str:
        """Return dynamic player-facing keys, optionally only the primary."""

        names = self.names_for(action)
        if not names:
            return "未設定"
        if concise:
            names = names[:1]
        return " / ".join(
            _DISPLAY_NAMES.get(name, name.upper() if len(name) == 1 else name.title())
            for name in names
        )

    def primary_display(self, action: str) -> str:
        """Compact label suitable for hints, buttons, and narrow cards."""

        return self.display(action, concise=True)

    # Discoverable spelling for callers that search for ``display_*`` APIs.
    display_primary = primary_display


@dataclass(frozen=True)
class RebindResult:
    success: bool
    reason: str | None
    action: str
    key_name: str | None
    swapped_with: tuple[str, ...]
    overrides: dict[str, list[str]]


def _minimal_overrides(
    effective: Mapping[str, Sequence[str]],
) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for action in ACTION_SPECS:
        names = list(dict.fromkeys(effective.get(action, ())))
        if not names and ACTION_SPECS[action].required:
            names = list(_default_names(action))
        if tuple(names) != _default_names(action):
            out[action] = names
    return out


def rebind_action(
    overrides: Any,
    action: str,
    key: str | int,
    *,
    resolution: str = "swap",
) -> RebindResult:
    """Return a new binding mapping without mutating the caller's data.

    ``swap`` replaces the target action's primary key and gives that previous
    primary key to every overlapping action that owned the new key. ``replace``
    simply removes the new key from those actions. ``reject`` reports a
    conflict without changing anything.
    """

    profile = BindingProfile(overrides)

    def fail(reason: str, key_name: str | None = None) -> RebindResult:
        return RebindResult(
            success=False,
            reason=reason,
            action=action,
            key_name=key_name,
            swapped_with=(),
            overrides=profile.overrides,
        )

    if action not in ACTION_SPECS:
        return fail("unknown_action")
    name = normalize_key_name(key)
    if name is None:
        return fail("invalid_key")
    if is_reserved_key(name):
        return fail("reserved_key", name)
    if resolution not in {"swap", "replace", "reject"}:
        return fail("invalid_resolution", name)

    conflicts = profile.conflicts_for(action, name)
    if conflicts and resolution == "reject":
        return RebindResult(
            success=False,
            reason="binding_conflict",
            action=action,
            key_name=name,
            swapped_with=conflicts,
            overrides=profile.overrides,
        )

    effective = {
        item: list(profile.names_for(item))
        for item in ACTION_SPECS
    }
    current = effective[action]
    old_primary = current[0] if current else None
    effective[action] = [
        name,
        *(
            existing
            for existing in current
            if existing not in {name, old_primary}
        ),
    ]

    for other in conflicts:
        other_keys = effective[other]
        replacement: list[str] = []
        for existing in other_keys:
            if existing != name:
                if existing not in replacement:
                    replacement.append(existing)
            elif (
                resolution == "swap"
                and old_primary is not None
                and old_primary != name
                and old_primary not in replacement
            ):
                replacement.append(old_primary)
        if not replacement and ACTION_SPECS[other].required:
            return fail("required_action_would_be_unbound", name)
        effective[other] = replacement

    normalized = normalize_binding_overrides(_minimal_overrides(effective))
    return RebindResult(
        success=True,
        reason=None,
        action=action,
        key_name=name,
        swapped_with=conflicts,
        overrides=normalized,
    )


__all__ = [
    "ACTION_SPECS",
    "ActionSpec",
    "BindingProfile",
    "InputAction",
    "RebindResult",
    "is_reserved_key",
    "key_code",
    "normalize_binding_overrides",
    "normalize_key_name",
    "rebind_action",
]

"""Session-scoped runtime localization and legacy visual-copy hooks.

The engine ships with sensible Chinese-language defaults for everything
the UI displays (affection labels, time-of-day labels, etc.). Legacy packs can
override any subset through ``meta.locale`` exactly as before. Multilingual
packs additionally declare explicit language/catalog/font entries; catalogs are
bounded, validated, preloaded, and applied only to presentation copy.

Example ``meta.yaml`` block::

    locale:
      affection_levels:
        - {min: 0,   label: "Stranger"}
        - {min: 25,  label: "Friend"}
        - {min: 50,  label: "Close Friend"}
        - {min: 100, label: "Lover"}
      time_of_day:
        morning:   "Morning"
        noon:      "Noon"
        afternoon: "Afternoon"
        evening:   "Evening"
        night:     "Night"
        midnight:  "Witching Hour"
      day_of_week:
        mon: "Mon"
        # ... etc
      ui:
        new_game: "New Game"
        load_game: "Continue"
        quit: "Quit"
        map: "Map"
        affection: "Bonds"
        log: "Journal"
        save: "Save"
        settings: "Settings"
        leave: "Leave"
        advance_time: "Wait"
        close: "Close"
        continue_hint: "Click / Space to continue"

The point is: nothing in the engine is hard-coded to any specific
game pack. The defaults below are sensible Chinese-language strings
the engine falls back to when a pack does not override them.
"""
from __future__ import annotations

from collections import Counter
from hashlib import sha256
import json
from pathlib import Path, PurePosixPath, PureWindowsPath
import re
import string
from typing import Any, Literal, Mapping

import yaml
from pydantic import BaseModel, ConfigDict, Field, PrivateAttr
from yaml.constructor import ConstructorError
from yaml.nodes import MappingNode


# Hard-coded fallbacks — used when no pack/locale overrides exist.
DEFAULT_AFFECTION_LEVELS: list[dict[str, Any]] = [
    {"min": -999, "label": "敵意"},
    {"min": 0,    "label": "陌生"},
    {"min": 10,   "label": "認識"},
    {"min": 25,   "label": "朋友"},
    {"min": 50,   "label": "好友"},
    {"min": 80,   "label": "心動"},
    {"min": 120,  "label": "戀人"},
]

DEFAULT_TIME_OF_DAY: dict[str, str] = {
    "morning":   "早晨",
    "noon":      "中午",
    "afternoon": "下午",
    "evening":   "傍晚",
    "night":     "夜晚",
    "midnight":  "深夜",
}

DEFAULT_DAY_OF_WEEK: dict[str, str] = {
    "mon": "週一", "tue": "週二", "wed": "週三",
    "thu": "週四", "fri": "週五", "sat": "週六", "sun": "週日",
}

DEFAULT_UI: dict[str, str] = {
    "new_game":       "開始新遊戲",
    "load_game":      "載入存檔",
    "quit":           "離開遊戲",
    "name_prompt":    "姓名",
    "name_placeholder": "輸入主角的名字…",
    "map":            "地圖",
    "affection":      "好感",
    "log":            "事件",
    "save":           "存檔",
    "settings":       "設定",
    "settings_tab_reading": "閱讀",
    "settings_tab_audio": "音訊",
    "settings_tab_accessibility": "輔助使用",
    "settings_tab_controls_display": "操作與顯示",
    "leave":          "離開",
    "advance_time":   "等下個時段",
    "close":          "關閉",
    "continue_hint":  "按 Space / 點擊 繼續",
    "no_one_here":    "這裡沒有人。",
    "no_save":        "目前還沒有任何存檔。",
    "no_events":      "（事件記錄是空的。去校園裡走走吧。）",
    "no_chars":       "（還沒有任何角色被記錄。先在校園裡逛逛吧。）",
    "day_format":     "第 {day} 天 · {weekday} · {time_of_day}",
    "leave_confirm":  "離開遊戲回到標題畫面？尚未存檔的進度將會丟失。",
    # Presentation / extras: gallery, music room, scene
    # replay, endings, plus playback + quick/autosave labels.
    "extras":         "鑑賞模式",
    "cg_gallery":     "CG鑑賞",
    "music_room":     "音樂室",
    "scene_replay":   "場景重溫",
    "endings":        "結局",
    "credits":        "鳴謝",
    "auto":           "自動",
    "skip":           "快進",
    "history":        "記錄",
    "quick_save":     "快存",
    "quick_load":     "快讀",
    "load":           "讀取",
    "hide_ui":        "隱藏",
    "more":           "更多",
    "menu":           "完整選單",
    "menu_short":     "選單",
    "accessibility":  "輔助使用",
    "sound_captions": "聲音字幕",
    "sound_captions_help": "以文字顯示環境音、音效、音樂與重要的寂靜",
    "high_contrast": "高對比介面",
    "high_contrast_help": "提高文字、控制邊界與焦點對比；不改變背景、CG 或角色立繪",
    "sound_caption_ambient": "環境音",
    "sound_caption_sfx":     "音效",
    "sound_caption_music":   "音樂",
    "sound_caption_silence": "寂靜",
    "quicksave":      "快速存檔",
    "quickload":      "快速載入",
    "autosave":       "自動存檔",
    # Every literal runtime ``t(key, source)`` call belongs in this inventory.
    # The extractor binds these source strings into the catalog digest, so a
    # newly-keyed control cannot silently become untranslatable.
    "achievements": "成就",
    "affection_feedback": "好感變化提示",
    "auto_emote": "立繪情緒反應",
    "auto_play_speed": "自動播放速度",
    "back_arrow": "← 返回",
    "cancel": "取消",
    "cg_gallery_empty": "（這個遊戲沒有設定 CG。）",
    "cg_locked": "未解鎖",
    "chapter_card_hint": "點擊 / 空白鍵 繼續",
    "chapter_card_new": "新章節",
    "character_voice_volume": "角色語音音量",
    "characters": "角色檔案",
    "clue_journal": "線索筆記",
    "clues": "線索",
    "choice_card_prompt": "請選擇接下來的方向",
    "choice_controls_hint": "{left}／{right}／滑鼠選擇 · {confirm} 確認",
    "choice_locked": "尚未開放",
    "choice_prompt": "請選擇",
    "choice_scroll_hint": "↑／↓／滾輪閱讀",
    "confirm": "確認",
    "confirm_enrollment": "確認入學",
    "continue_game": "繼續遊戲",
    "complete_current_objective_first": "先完成目前的目標。",
    "control_settings": "操作設定",
    "control_settings_help": "完整清單、衝突處理與重設請進入「操作設定」。",
    "controls": "操作",
    "current_bindings": "目前配置",
    "custom_bindings_count": "已自訂 {count} 項操作",
    "default_bindings": "使用預設按鍵",
    "default_player_name": "玩家",
    "dialogue_continue_hint": "按 {binding} / 點擊 繼續",
    "dialogue_history": "對話回顧",
    "dialogue_next_page_hint": "按 {binding} / 點擊 下一頁",
    "destination_arrangement_hint_reason": "安排提示：{reason}",
    "destination_arrival_available": "抵達後可以進行",
    "destination_arrival_characters_heading": "預計抵達時在這裡",
    "destination_arrival_starts": "抵達後將開始",
    "destination_arrive_immediately": "即時抵達",
    "destination_badge": "● {label}",
    "destination_cannot_plan": "目前無法安排",
    "destination_cannot_travel_reason": "無法前往：{reason}",
    "destination_character_affection": "{name}  好感 {level}·{value}",
    "destination_current_location": "你在：{location}",
    "destination_empty_exits": "這裡沒有其他出口",
    "destination_empty_filtered": "目前沒有可前往的地點",
    "destination_expand_all_regions": "展開全部",
    "destination_inspect_conditions": "查看開放條件",
    "destination_inspect_details": "查看詳情",
    "destination_itinerary_arrival": "預計抵達：{location} · {time}",
    "destination_itinerary_cost": "消耗：{cost}",
    "destination_itinerary_now": "現在：{location} · {time}",
    "destination_itinerary_route": "路線：{route}",
    "destination_later_event": "稍後事件",
    "destination_location_info": "地點情報",
    "destination_new_event": "新事件",
    "destination_no_characters_here": "目前沒有角色在這裡",
    "destination_no_triggerable_events": "目前沒有可觸發的事件",
    "destination_other_region": "其他",
    "destination_reachable_only": "只看現在可去",
    "destination_title": "要去哪裡？",
    "destination_travel": "前往",
    "destination_trip_time_slots": "行程 {count} 時段",
    "destination_unset": "未設定",
    "destination_wait_one_slot": "等待一個時段",
    "destination_wait_until": "等待至第 {day} 天 {time_of_day}（共 {count} 時段）",
    "destination_with_characters_only": "只看有角色",
    "destination_world_map": "世界地圖",
    "destination_cost_future": "等待 {wait} 個時段後可出發；行程消耗 {cost} 時段",
    "destination_cost_instant": "即時，不消耗時段",
    "destination_cost_slots": "{count} 個時段",
    "dim_inactive_speakers": "立繪壓暗非說話者",
    "display": "顯示",
    "enrollment_heading": "新生指南 · 入學登記",
    "extra_large": "特大",
    "fast": "快",
    "flowchart": "流程圖",
    "flowchart_extras": "篇章 / 流程圖",
    "go_to_location": "前往{location}",
    "history_empty": "（還沒有任何對話。）",
    "instant": "瞬間",
    "language": "語言",
    "language_title_only_help": "為保留目前台詞與存檔狀態，請回到標題畫面切換語言。",
    "large": "大",
    "load_action": "載入",
    "load_confirm_detail": "目前尚未存檔的進度將不會保留；取消會留在現在的遊戲狀態。",
    "load_confirm_label": "載入確認",
    "load_confirm_title": "要載入「{label}」嗎？",
    "load_succeeded": "載入完成：{label}",
    "medium": "中",
    "map_available_now": "現在可前往",
    "map_current_location": "目前位置",
    "map_depart_after_time_slots": "等待 {count} 時段後可出發",
    "map_depart_after_wait": "需等待後出發",
    "map_depart_now": "現在可出發",
    "map_focus": "操作焦點",
    "map_hint_guided_all": "圖例說明故事、目前位置與探索地點；選擇後查看安排 · {focus}/{controller} 上方控制",
    "map_hint_guided_story": "故事地點優先；等待時段標記不能立即出發，可先查看安排 · {focus}/{controller} 上方控制",
    "map_hint_progress": "{label} · {stage} {status} · 標記會顯示出發時機；選擇後查看安排 · {focus}/{controller} 上方控制",
    "map_hint_standard": "圖例說明地點狀態；{nav} 選擇，{confirm} 查看安排 · {focus}/{controller} 上方控制",
    "map_marker_status": "{status} · {location}",
    "map_no_viable_route": "目前沒有可行路線",
    "map_not_yet_available": "尚未開放",
    "map_off_campus_location": "校外地點",
    "map_ordinary_exploration": "一般探索",
    "map_ordinary_location": "一般地點",
    "map_people_count": "{name}等 {count} 人",
    "map_progress_complete": "已完成",
    "map_progress_count": "{completed} / {total}",
    "map_progress_pending_wrap": "{completed} / {total} · 尚待本輪收束",
    "map_show_ordinary_exploration": "顯示一般探索",
    "map_story_location": "故事地點",
    "map_story_locations_only": "只看故事地點",
    "menu_extras": "鑑賞",
    "menu_footer": "{left}／{right} 分頁　{up}／{down} 選擇　{confirm} 確認　{back} 返回",
    "menu_item_with_binding": "{label} ({binding})",
    "menu_item_with_binding_unread": "{label} ({binding}) · 新 {count}",
    "menu_records": "紀錄",
    "menu_system": "系統",
    "music_locked": "尚未解鎖",
    "music_locked_title": "？？？",
    "music_now_playing": "正在播放",
    "music_room_empty": "（這個遊戲沒有設定音樂鑑賞曲目。）",
    "music_stop": "停止播放",
    "music_tap_to_play": "點擊播放",
    "music_catalog_legacy": "舊版曲目資料",
    "music_confirm_to_play": "按確認或點擊播放",
    "music_hear_to_unlock": "在故事中聽過後解鎖",
    "music_locked_explanation": "這首曲目尚未解鎖；在故事中聽過後即可播放。",
    "music_unavailable": "這首曲目目前無法播放。",
    "music_playback_failed": "無法載入這首曲目，請檢查音訊檔案。",
    "music_now_playing_named": "正在播放：{title}",
    "music_playback_stopped": "已停止試聽；離開時會恢復原本的音訊。",
    "music_room_footer": (
        "{up}／{down} 選曲　{confirm} 播放　{page_up}／{page_down} 翻頁"
    ),
    "name_prompt_instruction": "請輸入姓名",
    "nvl_mode": "NVL 模式",
    "off": "關",
    "on": "開",
    "onboarding_advance_desc": "{confirm}／點擊：顯示全文，再前往下一句",
    "onboarding_advance_title": "推進文字",
    "onboarding_choice_desc": "{nav} 選取；{confirm} 確認",
    "onboarding_choice_title": "做出選擇",
    "onboarding_intro": "現在只要記住三件事；完整操作隨時可在設定查看。",
    "onboarding_menu_desc": "{menu} 開啟；其餘操作可在設定查看",
    "onboarding_menu_title": "開啟選單",
    "onboarding_start": "開始閱讀",
    "onboarding_title": "閱讀入門",
    "playback": "播放",
    "actions_heading": "動作",
    "affection_summary": "好感 {level} · {value}",
    "current_objective": "目前目標：{objective}",
    "inventory": "物品",
    "new_discovery": "● 有新發現",
    "quest_log": "任務記錄",
    "reduce_motion": "減少動態效果",
    "reduce_motion_help": "停止位移／閃光／脈動與待機動畫；保留靜態狀態提示",
    "relationships": "關係狀態",
    "return_to_title": "回標題畫面",
    "save_action_cancelled": "已取消，現有存檔與目前進度均未變更。",
    "save_delete": "刪除",
    "save_delete_cancelled": "已取消刪除，現有存檔未變更。",
    "save_delete_confirm": "確認刪除",
    "save_delete_confirm_detail": "這項操作無法復原；預設會取消，現有遊戲進度不受影響。",
    "save_delete_confirm_label": "刪除存檔",
    "save_delete_confirm_title": "要刪除「{label}」嗎？",
    "save_delete_failed": "刪除失敗：{error}",
    "save_delete_missing": "刪除失敗：找不到「{label}」，清單未變更。",
    "save_delete_succeeded": "已刪除：{label}",
    "save_empty": "（沒有存檔。）",
    "save_failed": "儲存失敗：{error}",
    "save_new": "新增",
    "save_new_hint": "（建立新存檔）",
    "save_new_label": "+ 新增存檔",
    "save_overwrite": "覆寫",
    "save_overwrite_confirm_detail": "原本的存檔內容會被目前進度取代；取消不會更動資料。",
    "save_overwrite_confirm_label": "覆寫確認",
    "save_overwrite_confirm_title": "要覆寫「{label}」嗎？",
    "save_readonly": "唯讀",
    "save_readonly_hint": "自動存檔為唯讀，無法在此覆寫。",
    "save_succeeded": "儲存完成：{label}",
    "scene_replay_common_route": "共通線",
    "scene_replay_empty": "尚無可回想的場景",
    "scene_replay_untitled": "未命名回憶 {index:02d}",
    "settings_footer": "{confirm} 選擇　{back} 返回　{up} / {down} 移動　滾輪 / 拖曳捲動",
    "settings_footer_tabs": (
        "{left} / {right} 分頁　{up} / {down} 選項　"
        "{confirm} 變更　{back} 返回"
    ),
    "sfx": "音效",
    "skip_read_only": "僅快進已讀",
    "slow": "慢",
    "standard": "標準",
    "status_error": "錯誤｜",
    "status_hud": "章節／日期提示",
    "status_success": "完成｜",
    "show_ui_hint": "點擊 / 按 {binding} 顯示介面",
    "text_size": "文字大小",
    "text_size_help": "這段文字會立即依所選大小顯示。",
    "text_speed": "文字速度",
    "toggle_format": "{label}：{state}",
    "toggle_fullscreen": "切換全螢幕　{binding}",
    "go_out": "外出",
    "travel_destinations_count": "{label}（{count} 個地點）",
    "travel_map": "行程地圖",
    "travel_heading": "移動",
    "travel_no_new_stories": "行程地圖（目前無新故事）",
    "travel_stories_count": "行程地圖（{count} 個故事）",
    "typewriter_sound": "打字音效",
    "ui_sound": "介面音效",
    "very_fast": "極快",
    "voice": "語音",
    "voice_replay": "VOICE",
    "volume": "音量",
    "wait_for_voice": "等待語音",
}


MAX_CATALOG_BYTES = 8 * 1024 * 1024
MAX_CATALOG_MESSAGES = 50_000
MAX_MESSAGE_KEY_CHARS = 512
MAX_MESSAGE_CHARS = 100_000
MAX_LOCALE_LANGUAGES = 64

_LOCALE_TAG_RE = re.compile(r"^[A-Za-z]{2,8}(?:-[A-Za-z0-9]{1,8})*$")
_MESSAGE_KEY_RE = re.compile(r"^[^\s\x00-\x1f\x7f]{1,512}$")
_DIALOGUE_OP_RE = re.compile(
    r"\[\[([a-zA-Z_][a-zA-Z0-9_]*)(?::([^\]]*))?\]\]"
)
_MARKUP_RE = re.compile(
    r"(?<!\\)\[(/?)(b|color|size|w|s|ruby|fx)(?:=([^\]]*))?\]"
)


def _has_control(value: str) -> bool:
    return any(ord(char) < 32 or ord(char) == 127 for char in value)


class LocalizationError(ValueError):
    """Base error for invalid locale manifests and translation catalogs."""


class LocaleManifestError(LocalizationError):
    """Raised by strict callers when ``meta.locale`` is not safe to use."""


class TranslationCatalogError(LocalizationError):
    """Raised when one catalog is malformed, unsafe, or internally inconsistent."""


class _UniqueKeySafeLoader(yaml.SafeLoader):
    """Safe YAML loader that rejects duplicate keys at every mapping level."""


def _construct_unique_mapping(
    loader: _UniqueKeySafeLoader,
    node: MappingNode,
    deep: bool = False,
) -> dict[Any, Any]:
    if not isinstance(node, MappingNode):  # pragma: no cover - PyYAML contract
        raise ConstructorError(None, None, "expected a mapping node", node.start_mark)
    loader.flatten_mapping(node)
    out: dict[Any, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        try:
            duplicate = key in out
        except TypeError as exc:
            raise ConstructorError(
                "while constructing a mapping",
                node.start_mark,
                "found an unhashable mapping key",
                key_node.start_mark,
            ) from exc
        if duplicate:
            raise ConstructorError(
                "while constructing a mapping",
                node.start_mark,
                f"found duplicate key {key!r}",
                key_node.start_mark,
            )
        out[key] = loader.construct_object(value_node, deep=deep)
    return out


_UniqueKeySafeLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    _construct_unique_mapping,
)


def load_yaml_strict(path: Path, *, max_bytes: int = MAX_CATALOG_BYTES) -> Any:
    """Load bounded UTF-8 YAML without constructors or duplicate-key overwrite.

    This helper is public so the extraction/checking tool and runtime enforce the
    same input boundary.  The byte cap is checked before parsing, which prevents a
    catalog from becoming an unbounded startup allocation.
    """

    source = Path(path)
    try:
        size = source.stat().st_size
    except OSError as exc:
        raise TranslationCatalogError(f"cannot stat catalog {source}: {exc}") from exc
    if size > max_bytes:
        raise TranslationCatalogError(
            f"catalog {source} is {size} bytes; limit is {max_bytes}"
        )
    try:
        raw = source.read_bytes()
        text = raw.decode("utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise TranslationCatalogError(f"cannot read UTF-8 catalog {source}: {exc}") from exc
    try:
        return yaml.load(text, Loader=_UniqueKeySafeLoader)
    except yaml.YAMLError as exc:
        raise TranslationCatalogError(f"invalid YAML in {source}: {exc}") from exc


def normalize_locale_tag(value: object) -> str:
    """Return one canonical, path-safe BCP-47-shaped locale identifier.

    The accepted grammar intentionally covers common language/script/region tags
    without attempting to be a complete BCP-47 registry.  Runtime file paths are
    *never* derived from this value; catalogs must be named explicitly in the
    manifest.
    """

    if not isinstance(value, str) or value != value.strip() or len(value) > 64:
        raise LocaleManifestError("locale id must be a trimmed string up to 64 characters")
    if _LOCALE_TAG_RE.fullmatch(value) is None:
        raise LocaleManifestError(f"invalid locale id {value!r}")
    parts = value.split("-")
    canonical = [parts[0].lower()]
    for part in parts[1:]:
        if len(part) == 4 and part.isalpha():
            canonical.append(part.title())
        elif (len(part) == 2 and part.isalpha()) or (
            len(part) == 3 and part.isdigit()
        ):
            canonical.append(part.upper())
        else:
            canonical.append(part.lower())
    return "-".join(canonical)


def message_table_digest(messages: Mapping[str, str]) -> str:
    """Canonical SHA-256 of a source message table, independent of YAML layout."""

    payload = json.dumps(
        dict(messages),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return sha256(payload).hexdigest()


# Friendly aliases for authoring integrations that use either spelling.
compute_source_digest = message_table_digest
catalog_source_digest = message_table_digest


def _format_fields(text: str) -> tuple[str, ...] | None:
    try:
        fields = [
            field_name
            for _literal, field_name, _format_spec, _conversion
            in string.Formatter().parse(text)
            if field_name is not None
        ]
    except ValueError:
        return None
    return tuple(fields)


def _markup_signature(text: str) -> tuple[tuple[str, str, str], ...]:
    return tuple(
        (match.group(1), match.group(2), match.group(3) or "")
        for match in _MARKUP_RE.finditer(text)
    )


def _dialogue_op_signature(text: str) -> tuple[tuple[str, str], ...]:
    return tuple(
        (match.group(1), match.group(2) or "")
        for match in _DIALOGUE_OP_RE.finditer(text)
    )


def message_parity_issues(source: str, translated: str) -> tuple[str, ...]:
    """Describe unsafe structural drift between source and translated copy.

    Translators may freely change prose, but formatter/interpolation fields,
    rich-text/control tags, and executable ``[[op:...]]`` directives must remain
    structurally identical.  Exact directive order is intentional: moving two
    side-effecting operations can change gameplay.
    """

    issues: list[str] = []
    source_fields = _format_fields(source)
    target_fields = _format_fields(translated)
    if source_fields is None:
        issues.append("source has malformed brace placeholders")
    elif target_fields is None:
        issues.append("translation has malformed brace placeholders")
    elif Counter(source_fields) != Counter(target_fields):
        issues.append("placeholder set/count differs from source")

    if _markup_signature(source) != _markup_signature(translated):
        issues.append("rich-text/BBCode tag sequence differs from source")
    else:
        # The tag signature catches most faults; lint also catches malformed or
        # unbalanced target markup that contains no source counterpart.
        try:
            from ..dialogue.richtext import lint_markup

            if lint_markup(translated):
                issues.append("translation contains invalid rich-text markup")
        except Exception:
            # Localization remains usable in minimal tooling environments where
            # the presentation module cannot be imported.
            pass

    if _dialogue_op_signature(source) != _dialogue_op_signature(translated):
        issues.append("dialogue-op sequence differs from source")
    return tuple(issues)


class TranslationCatalog(BaseModel):
    """Strict on-disk catalog envelope (format version 1)."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    format_version: Literal[1]
    language: str
    source_language: str
    source_digest: str
    messages: dict[str, str]


class LocaleFontSources(BaseModel):
    """Validated font inputs selected for one effective locale."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    bundled: str | None = None
    candidates: tuple[str, ...] = ()


class LocaleLanguage(BaseModel):
    """One validated language entry retained by the runtime authority."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str
    label: str
    status: Literal["complete", "preview"]
    catalog: str | None = None
    font: LocaleFontSources = Field(default_factory=LocaleFontSources)
    # None means all scenes.  An empty tuple means UI/meta-only.
    story_scenes: tuple[str, ...] | None = None


class LocaleOption(BaseModel):
    """Stable Settings-facing language-button payload."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str
    label: str
    status: Literal["complete", "preview"]
    selected: bool


def _validate_catalog_document(data: Any, *, origin: str = "catalog") -> TranslationCatalog:
    if not isinstance(data, dict):
        raise TranslationCatalogError(f"{origin} must be a mapping")
    expected = {
        "format_version",
        "language",
        "source_language",
        "source_digest",
        "messages",
    }
    missing = expected - set(data)
    extra = set(data) - expected
    if missing:
        raise TranslationCatalogError(f"{origin} is missing fields: {sorted(missing)}")
    if extra:
        raise TranslationCatalogError(f"{origin} has unknown fields: {sorted(extra)}")
    if type(data.get("format_version")) is not int or data["format_version"] != 1:
        raise TranslationCatalogError(f"{origin}.format_version must be integer 1")

    language = normalize_locale_tag(data.get("language"))
    source_language = normalize_locale_tag(data.get("source_language"))
    digest = data.get("source_digest")
    if not isinstance(digest, str) or re.fullmatch(r"[0-9a-f]{64}", digest) is None:
        raise TranslationCatalogError(f"{origin}.source_digest must be lowercase SHA-256")
    raw_messages = data.get("messages")
    if not isinstance(raw_messages, dict):
        raise TranslationCatalogError(f"{origin}.messages must be a mapping")
    if len(raw_messages) > MAX_CATALOG_MESSAGES:
        raise TranslationCatalogError(
            f"{origin}.messages exceeds {MAX_CATALOG_MESSAGES} entries"
        )
    messages: dict[str, str] = {}
    for key, value in raw_messages.items():
        if not isinstance(key, str) or _MESSAGE_KEY_RE.fullmatch(key) is None:
            raise TranslationCatalogError(f"{origin} contains an invalid message key")
        if not isinstance(value, str):
            raise TranslationCatalogError(f"{origin}.messages[{key!r}] must be a string")
        if len(value) > MAX_MESSAGE_CHARS or "\x00" in value:
            raise TranslationCatalogError(
                f"{origin}.messages[{key!r}] exceeds limits or contains NUL"
            )
        messages[key] = value
    try:
        return TranslationCatalog.model_validate({
            "format_version": 1,
            "language": language,
            "source_language": source_language,
            "source_digest": digest,
            "messages": messages,
        })
    except Exception as exc:  # pragma: no cover - manual checks own public errors
        raise TranslationCatalogError(f"{origin} is invalid: {exc}") from exc


def load_translation_catalog(path: Path) -> TranslationCatalog:
    """Read and validate one bounded, duplicate-free v1 catalog."""

    source = Path(path)
    return _validate_catalog_document(load_yaml_strict(source), origin=str(source))


def parse_translation_catalog(
    data: Any,
    *,
    origin: str = "catalog",
) -> TranslationCatalog:
    """Validate an already parsed catalog document with the runtime contract."""

    return _validate_catalog_document(data, origin=origin)


def _safe_pack_path(
    ref: object,
    *,
    pack_root: Path | None,
    label: str,
    must_exist: bool,
) -> tuple[str, Path | None]:
    if not isinstance(ref, str) or not ref or ref != ref.strip():
        raise LocaleManifestError(f"{label} must be a non-empty trimmed string")
    if len(ref) > 512 or _has_control(ref) or "\\" in ref or "://" in ref:
        raise LocaleManifestError(f"{label} is not a portable pack-relative path")
    portable = PurePosixPath(ref)
    windows = PureWindowsPath(ref)
    if portable.is_absolute() or windows.is_absolute() or windows.drive:
        raise LocaleManifestError(f"{label} must be pack-relative")
    if ".." in portable.parts or not portable.parts:
        raise LocaleManifestError(f"{label} cannot escape the pack")
    if pack_root is None:
        return ref, None
    try:
        root = Path(pack_root).resolve()
        unresolved = root / Path(*portable.parts)
        cursor = unresolved
        while cursor != root:
            if cursor.is_symlink():
                raise LocaleManifestError(f"{label} cannot traverse a symlink")
            cursor = cursor.parent
        candidate = unresolved.resolve()
    except (OSError, RuntimeError) as exc:
        raise LocaleManifestError(f"{label} cannot be resolved safely") from exc
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise LocaleManifestError(f"{label} resolves outside the pack") from exc
    if must_exist and not candidate.is_file():
        raise LocaleManifestError(f"{label} must name a regular, non-symlink pack file")
    return ref, candidate


def _parse_font_sources(
    raw: object,
    *,
    pack_root: Path | None,
    label: str,
) -> LocaleFontSources:
    if raw is None:
        return LocaleFontSources()
    if not isinstance(raw, dict) or set(raw) - {"bundled", "candidates"}:
        raise LocaleManifestError(f"{label} must contain only bundled/candidates")
    bundled = raw.get("bundled")
    if bundled is not None:
        bundled, _ = _safe_pack_path(
            bundled,
            pack_root=pack_root,
            label=f"{label}.bundled",
            must_exist=pack_root is not None,
        )
        if PurePosixPath(bundled).suffix.lower() not in {".ttf", ".otf"}:
            raise LocaleManifestError(f"{label}.bundled must be a .ttf or .otf file")
    candidates_raw = raw.get("candidates", [])
    if not isinstance(candidates_raw, (list, tuple)) or len(candidates_raw) > 16:
        raise LocaleManifestError(f"{label}.candidates must be a list of at most 16")
    candidates: list[str] = []
    for value in candidates_raw:
        if (
            not isinstance(value, str)
            or not value.strip()
            or value != value.strip()
            or len(value) > 128
            or _has_control(value)
        ):
            raise LocaleManifestError(f"{label}.candidates contains an invalid name")
        if value not in candidates:
            candidates.append(value)
    return LocaleFontSources(bundled=bundled, candidates=tuple(candidates))


def _parse_story_scenes(
    raw: object,
    *,
    status: Literal["complete", "preview"],
    label: str,
) -> tuple[str, ...] | None:
    if raw is None:
        return None if status == "complete" else ()
    if raw in ("*", "all"):
        return None
    if not isinstance(raw, (list, tuple)) or len(raw) > 20_000:
        raise LocaleManifestError(f"{label} must be '*', 'all', or a scene-id list")
    scenes: list[str] = []
    for value in raw:
        if (
            not isinstance(value, str)
            or not value
            or value != value.strip()
            or len(value) > 128
            or _has_control(value)
        ):
            raise LocaleManifestError(f"{label} contains an invalid scene id")
        if value in scenes:
            raise LocaleManifestError(f"{label} contains duplicate scene id {value!r}")
        scenes.append(value)
    return tuple(scenes)


class Localization(BaseModel):
    """Session-scoped locale authority with source-safe catalog fallback.

    Packs without the new ``source/default/languages`` manifest use the exact
    historical merge behaviour and expose no language options.  A multilingual
    pack preloads and validates every selectable target; ``activate`` therefore
    swaps one complete in-memory catalog atomically and never performs I/O from a
    user-provided locale id.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    affection_levels: list[dict[str, Any]] = Field(
        default_factory=lambda: list(DEFAULT_AFFECTION_LEVELS))
    time_of_day: dict[str, str] = Field(
        default_factory=lambda: dict(DEFAULT_TIME_OF_DAY))
    day_of_week: dict[str, str] = Field(
        default_factory=lambda: dict(DEFAULT_DAY_OF_WEEK))
    ui: dict[str, str] = Field(default_factory=lambda: dict(DEFAULT_UI))

    source: str = ""
    default: str = ""
    requested: str = ""
    effective: str = ""

    _languages: dict[str, LocaleLanguage] = PrivateAttr(default_factory=dict)
    _language_order: list[str] = PrivateAttr(default_factory=list)
    _catalogs: dict[str, TranslationCatalog] = PrivateAttr(default_factory=dict)
    _errors: list[str] = PrivateAttr(default_factory=list)
    _source_digest: str = PrivateAttr(default="")

    @classmethod
    def from_meta(
        cls,
        meta: Mapping[str, Any],
        *,
        pack_root: Path | None = None,
        requested: str | None = None,
        strict: bool = False,
    ) -> "Localization":
        """Build a Localization from a meta.yaml dict, merging on top of
        the engine defaults.

        ``pack_root`` is required only when an entry declares a catalog or
        bundled locale font.  Bad target entries are excluded wholesale and the
        effective language falls back to the declared default/source.  Set
        ``strict=True`` in validators/CI to raise the first recorded problem.
        """
        out = cls()
        raw_loc = meta.get("locale") or {}
        if not isinstance(raw_loc, Mapping):
            out._errors.append("locale must be a mapping")
            loc_block: Mapping[str, Any] = {}
        else:
            loc_block = raw_loc

        levels = loc_block.get("affection_levels")
        if levels is not None:
            if (
                not isinstance(levels, list)
                or len(levels) > 1_024
                or any(
                    not isinstance(level, Mapping)
                    or set(level) - {"min", "label"}
                    or type(level.get("min")) not in (int, float)
                    or not isinstance(level.get("label"), str)
                    or not level["label"].strip()
                    for level in levels
                )
            ):
                out._errors.append(
                    "locale.affection_levels must be a list of min/label mappings"
                )
            elif levels:
                out.affection_levels = [dict(level) for level in levels]

        for block_name, destination in (
            ("time_of_day", out.time_of_day),
            ("day_of_week", out.day_of_week),
            ("ui", out.ui),
        ):
            if block_name not in loc_block:
                continue
            block = loc_block[block_name]
            if (
                not isinstance(block, Mapping)
                or any(
                    not isinstance(key, str) or not isinstance(value, str)
                    for key, value in block.items()
                )
            ):
                out._errors.append(
                    f"locale.{block_name} must be a string mapping"
                )
                continue
            destination.update(block)

        manifest_present = any(
            key in loc_block for key in ("source", "default", "languages")
        )
        if manifest_present:
            try:
                out._install_manifest(loc_block, pack_root=pack_root)
            except LocalizationError as exc:
                out._errors.append(str(exc))
                out._languages.clear()
                out._language_order.clear()
                out._catalogs.clear()
                out.source = out.default = out.effective = ""
        out.activate(requested)
        if strict and out._errors:
            raise LocaleManifestError(out._errors[0])
        return out

    def _install_manifest(
        self,
        loc_block: Mapping[str, Any],
        *,
        pack_root: Path | None,
    ) -> None:
        if not all(key in loc_block for key in ("source", "default", "languages")):
            raise LocaleManifestError(
                "locale manifest requires source, default, and languages"
            )
        source = normalize_locale_tag(loc_block.get("source"))
        default = normalize_locale_tag(loc_block.get("default"))
        raw_languages = loc_block.get("languages")
        if not isinstance(raw_languages, Mapping) or not raw_languages:
            raise LocaleManifestError("locale.languages must be a non-empty mapping")
        if len(raw_languages) > MAX_LOCALE_LANGUAGES:
            raise LocaleManifestError(
                f"locale.languages exceeds {MAX_LOCALE_LANGUAGES} entries"
            )

        canonical_raw: dict[str, Mapping[str, Any]] = {}
        order: list[str] = []
        for raw_id, entry in raw_languages.items():
            locale_id = normalize_locale_tag(raw_id)
            if locale_id in canonical_raw:
                raise LocaleManifestError(
                    f"locale.languages contains canonical duplicate {locale_id!r}"
                )
            if not isinstance(entry, Mapping):
                raise LocaleManifestError(
                    f"locale.languages[{locale_id!r}] must be a mapping"
                )
            canonical_raw[locale_id] = entry
            order.append(locale_id)
        if source not in canonical_raw or default not in canonical_raw:
            raise LocaleManifestError(
                "locale.source and locale.default must both exist in languages"
            )

        self.source = source
        self.default = default
        parsed: dict[str, LocaleLanguage] = {}
        catalog_paths: dict[str, Path] = {}
        for locale_id in order:
            raw = canonical_raw[locale_id]
            try:
                unknown = set(raw) - {
                    "label", "status", "catalog", "font", "story_scenes"
                }
                if unknown:
                    raise LocaleManifestError(
                        f"language {locale_id!r} has unknown fields: {sorted(unknown)}"
                    )
                label = raw.get("label")
                if (
                    not isinstance(label, str)
                    or not label.strip()
                    or label != label.strip()
                    or len(label) > 80
                    or _has_control(label)
                ):
                    raise LocaleManifestError(
                        f"language {locale_id!r}.label is invalid"
                    )
                status = raw.get("status")
                if status not in ("complete", "preview"):
                    raise LocaleManifestError(
                        f"language {locale_id!r}.status must be complete or preview"
                    )
                catalog_ref = raw.get("catalog")
                resolved_catalog: Path | None = None
                if catalog_ref is not None:
                    catalog_ref, resolved_catalog = _safe_pack_path(
                        catalog_ref,
                        pack_root=pack_root,
                        label=f"language {locale_id!r}.catalog",
                        must_exist=True,
                    )
                    if PurePosixPath(catalog_ref).suffix.lower() not in {".yaml", ".yml"}:
                        raise LocaleManifestError(
                            f"language {locale_id!r}.catalog must be YAML"
                        )
                    if resolved_catalog is None:
                        raise LocaleManifestError(
                            f"language {locale_id!r}.catalog requires pack_root"
                        )
                elif locale_id != source:
                    raise LocaleManifestError(
                        f"target language {locale_id!r} requires an explicit catalog"
                    )
                font = _parse_font_sources(
                    raw.get("font"),
                    pack_root=pack_root,
                    label=f"language {locale_id!r}.font",
                )
                story_scenes = _parse_story_scenes(
                    raw.get("story_scenes"),
                    status=status,
                    label=f"language {locale_id!r}.story_scenes",
                )
                parsed[locale_id] = LocaleLanguage(
                    id=locale_id,
                    label=label,
                    status=status,
                    catalog=catalog_ref,
                    font=font,
                    story_scenes=story_scenes,
                )
                if resolved_catalog is not None:
                    catalog_paths[locale_id] = resolved_catalog
            except LocalizationError as exc:
                self._errors.append(str(exc))

        # The source entry is the non-negotiable fallback.  If its metadata is
        # unsafe, reject the multilingual manifest rather than presenting a
        # target language without a trustworthy way back.
        if source not in parsed:
            raise LocaleManifestError(
                f"source language {source!r} is invalid; multilingual mode disabled"
            )

        loaded: dict[str, TranslationCatalog] = {}
        source_catalog_declared = source in catalog_paths
        source_catalog_valid = not source_catalog_declared
        for locale_id in [source, *[lang for lang in order if lang != source]]:
            path = catalog_paths.get(locale_id)
            if path is None or locale_id not in parsed:
                continue
            try:
                catalog = load_translation_catalog(path)
                if catalog.language != locale_id:
                    raise TranslationCatalogError(
                        f"catalog {path} language {catalog.language!r} does not match {locale_id!r}"
                    )
                if catalog.source_language != source:
                    raise TranslationCatalogError(
                        f"catalog {path} source_language does not match {source!r}"
                    )
                if locale_id == source:
                    actual = message_table_digest(catalog.messages)
                    if catalog.source_digest != actual:
                        raise TranslationCatalogError(
                            f"source catalog {path} digest does not match its messages"
                        )
                    self._source_digest = actual
                    source_catalog_valid = True
                loaded[locale_id] = catalog
            except LocalizationError as exc:
                self._errors.append(str(exc))

        if source_catalog_declared and not source_catalog_valid:
            loaded.clear()
        else:
            target_digests = {
                catalog.source_digest
                for locale_id, catalog in loaded.items()
                if locale_id != source
            }
            if self._source_digest:
                for locale_id in list(loaded):
                    if (
                        locale_id != source
                        and loaded[locale_id].source_digest != self._source_digest
                    ):
                        self._errors.append(
                            f"catalog for {locale_id!r} targets a different source digest"
                        )
                        loaded.pop(locale_id, None)
            elif len(target_digests) > 1:
                self._errors.append(
                    "target catalogs disagree on source_digest; all targets disabled"
                )
                loaded = {
                    locale_id: catalog
                    for locale_id, catalog in loaded.items()
                    if locale_id == source
                }
            elif target_digests:
                self._source_digest = next(iter(target_digests))

        # A target is selectable only after its entire envelope was loaded and
        # validated.  Source remains selectable without a redundant catalog.
        retained = {
            locale_id: language
            for locale_id, language in parsed.items()
            if locale_id == source or locale_id in loaded
        }
        self._languages = retained
        self._language_order = [lang for lang in order if lang in retained]
        self._catalogs = {
            locale_id: catalog
            for locale_id, catalog in loaded.items()
            if locale_id in retained
        }

    # ----- helpers ------------------------------------------------------

    def affection_label(self, value: int) -> str:
        # The levels list is sorted ascending; the chosen label is the
        # one with the largest min that's still <= value.
        chosen = self.affection_levels[0]["label"]
        chosen_min = self.affection_levels[0]["min"]
        for lvl in self.affection_levels:
            if value >= lvl["min"]:
                chosen = lvl["label"]
                chosen_min = lvl["min"]
        return self.t(f"affection_level.{chosen_min}", chosen)

    def time_label(self, key: str) -> str:
        source = self.time_of_day.get(key, key)
        return self.t(f"time_of_day.{key}", source)

    def weekday_label(self, key: str) -> str:
        source = self.day_of_week.get(key, key)
        return self.t(f"day_of_week.{key}", source)

    @property
    def active(self) -> str:
        """Alias for the language actually serving the current presentation."""

        return self.effective

    @property
    def options(self) -> tuple[LocaleOption, ...]:
        """Settings-facing immutable options in author-declared order."""

        return tuple(
            LocaleOption(
                id=locale_id,
                label=self._languages[locale_id].label,
                status=self._languages[locale_id].status,
                selected=locale_id == self.effective,
            )
            for locale_id in self._language_order
        )

    @property
    def errors(self) -> tuple[str, ...]:
        """Non-fatal entries/catalogs rejected while building this authority."""

        return tuple(self._errors)

    @property
    def source_digest(self) -> str:
        return self._source_digest

    @property
    def font_sources(self) -> LocaleFontSources:
        """Effective locale fonts, falling back to source candidates."""

        effective = self._languages.get(self.effective)
        source = self._languages.get(self.source)
        active_font = effective.font if effective else LocaleFontSources()
        source_font = source.font if source else LocaleFontSources()
        bundled = active_font.bundled or source_font.bundled
        candidates: list[str] = []
        for name in (*active_font.candidates, *source_font.candidates):
            if name not in candidates:
                candidates.append(name)
        return LocaleFontSources(bundled=bundled, candidates=tuple(candidates))

    def activate(self, language: str | None) -> str:
        """Atomically activate one prevalidated catalog or the safe fallback.

        Invalid/unsupported requests never retain a previous target catalog.
        They resolve to declared default when valid, otherwise source.  No path
        or I/O operation uses ``language``.
        """

        normalized = ""
        if language:
            try:
                normalized = normalize_locale_tag(language)
            except LocalizationError:
                normalized = ""
        self.requested = normalized
        fallback = (
            self.default
            if self.default in self._languages
            else self.source if self.source in self._languages else ""
        )
        self.effective = normalized if normalized in self._languages else fallback
        return self.effective

    def _target_message(self, keys: tuple[str, ...]) -> str | None:
        if not self.effective or self.effective == self.source:
            return None
        catalog = self._catalogs.get(self.effective)
        if catalog is None:
            return None
        for key in keys:
            value = catalog.messages.get(key)
            if isinstance(value, str) and value.strip():
                return value
        return None

    def _source_catalog_matches(
        self,
        keys: tuple[str, ...],
        current_source: str,
    ) -> bool:
        """Prove this key still equals the digest-bound source catalog copy.

        A declared source catalog is the production drift authority. Its whole
        table digest proves which extraction a target was translated from; this
        per-key comparison proves the live authored/default source has not since
        changed. Missing or stale keys fail closed to current source copy. Legacy
        manifests without a source catalog retain their historical behaviour.
        """

        source_catalog = self._catalogs.get(self.source)
        if source_catalog is None:
            return True
        for key in keys:
            catalog_source = source_catalog.messages.get(key)
            if catalog_source is not None:
                # The extractor historically strips only terminal YAML block
                # newlines. Preserve that compatibility while detecting every
                # meaningful text/markup change.
                return catalog_source.rstrip("\n") == current_source.rstrip("\n")
        return False

    def _story_allowed(self, key: str, scene_id: str | None) -> bool:
        language = self._languages.get(self.effective)
        if language is None or language.story_scenes is None:
            return True
        # Static presentation catalogs are independent of a preview's story
        # slice.  Music Room metadata describes the title-screen extra rather
        # than granting access to any narrative scene.
        if key.startswith(("meta.", "ui.", "music_room.")):
            return True
        allowed = language.story_scenes
        if scene_id is not None:
            return scene_id in allowed
        return any(key == sid or key.startswith(f"{sid}.") for sid in allowed)

    @staticmethod
    def _format(text: str, fmt: Mapping[str, Any]) -> str:
        if not fmt:
            return text
        try:
            return text.format(**fmt)
        except Exception:
            return text

    def t(self, key: str, default: str | None = None, **fmt) -> str:
        source = self.ui.get(key, default if default is not None else key)
        lookup_keys = (f"ui.{key}", key)
        target = (
            self._target_message(lookup_keys)
            if self._source_catalog_matches(lookup_keys, source)
            else None
        )
        text = (
            target
            if target is not None and not message_parity_issues(source, target)
            else source
        )
        return self._format(text, fmt)

    def story(
        self,
        key: str,
        source_text: str,
        *,
        scene_id: str | None = None,
        **fmt: Any,
    ) -> str:
        """Resolve authored presentation copy without mutating story authority.

        ``source_text`` remains the final fallback and the structural parity
        authority.  IDs, conditions, effects, transitions, and asset references
        are outside this API and therefore cannot be changed by a catalog.
        """

        if not isinstance(source_text, str):
            raise TypeError("story source_text must be a string")
        target = None
        lookup_keys = (key, f"story.{key}")
        if (
            self._story_allowed(key, scene_id)
            and self._source_catalog_matches(lookup_keys, source_text)
        ):
            target = self._target_message(lookup_keys)
        text = (
            target
            if target is not None and not message_parity_issues(source_text, target)
            else source_text
        )
        return self._format(text, fmt)

"""Per-pack localization & visual identity hooks.

The engine ships with sensible Chinese-language defaults for everything
the UI displays (affection labels, time-of-day labels, etc.). A pack can
override any subset of those defaults through ``meta.yaml`` and they
will be merged on top at startup.

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

from typing import Any, Mapping
from pydantic import BaseModel, Field


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
    "quicksave":      "快速存檔",
    "quickload":      "快速載入",
    "autosave":       "自動存檔",
}


class Localization(BaseModel):
    affection_levels: list[dict[str, Any]] = Field(
        default_factory=lambda: list(DEFAULT_AFFECTION_LEVELS))
    time_of_day: dict[str, str] = Field(
        default_factory=lambda: dict(DEFAULT_TIME_OF_DAY))
    day_of_week: dict[str, str] = Field(
        default_factory=lambda: dict(DEFAULT_DAY_OF_WEEK))
    ui: dict[str, str] = Field(default_factory=lambda: dict(DEFAULT_UI))

    @classmethod
    def from_meta(cls, meta: Mapping[str, Any]) -> "Localization":
        """Build a Localization from a meta.yaml dict, merging on top of
        the engine defaults."""
        loc_block = meta.get("locale") or {}
        out = cls()
        levels = loc_block.get("affection_levels")
        if levels:
            out.affection_levels = list(levels)
        if "time_of_day" in loc_block:
            out.time_of_day.update(loc_block["time_of_day"])
        if "day_of_week" in loc_block:
            out.day_of_week.update(loc_block["day_of_week"])
        if "ui" in loc_block:
            out.ui.update(loc_block["ui"])
        return out

    # ----- helpers ------------------------------------------------------

    def affection_label(self, value: int) -> str:
        # The levels list is sorted ascending; the chosen label is the
        # one with the largest min that's still <= value.
        chosen = self.affection_levels[0]["label"]
        for lvl in self.affection_levels:
            if value >= lvl["min"]:
                chosen = lvl["label"]
        return chosen

    def time_label(self, key: str) -> str:
        return self.time_of_day.get(key, key)

    def weekday_label(self, key: str) -> str:
        return self.day_of_week.get(key, key)

    def t(self, key: str, default: str | None = None, **fmt) -> str:
        text = self.ui.get(key, default if default is not None else key)
        if fmt:
            try:
                text = text.format(**fmt)
            except Exception:
                pass
        return text

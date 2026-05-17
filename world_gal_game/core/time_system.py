"""In-game time and calendar system.

Tracks day number, day-of-week, and time-of-day. Many gal-game mechanics
(NPC schedules, events that only happen at night, etc.) depend on this.
"""
from __future__ import annotations

from enum import Enum
from pydantic import BaseModel


# A Localization can be bound at module level (by the App at startup)
# so that .label returns the pack's chosen string. If unbound, the bundled
# Chinese defaults below are used.
_LOCALIZATION = None


def bind_localization(loc) -> None:
    """Bind a Localization so TimeOfDay.label / DayOfWeek.label honour it."""
    global _LOCALIZATION
    _LOCALIZATION = loc


_DEFAULT_TIME_LABELS = {
    "morning":   "早晨",
    "noon":      "中午",
    "afternoon": "下午",
    "evening":   "傍晚",
    "night":     "夜晚",
    "midnight":  "深夜",
}
_DEFAULT_DAY_LABELS = {
    "mon": "週一", "tue": "週二", "wed": "週三",
    "thu": "週四", "fri": "週五", "sat": "週六", "sun": "週日",
}


class TimeOfDay(str, Enum):
    MORNING = "morning"       # 06:00 - 11:00
    NOON = "noon"             # 11:00 - 14:00
    AFTERNOON = "afternoon"   # 14:00 - 18:00
    EVENING = "evening"       # 18:00 - 21:00
    NIGHT = "night"           # 21:00 - 02:00
    MIDNIGHT = "midnight"     # 02:00 - 06:00 (haunting hour)

    @property
    def label(self) -> str:
        if _LOCALIZATION is not None:
            return _LOCALIZATION.time_label(self.value)
        return _DEFAULT_TIME_LABELS[self.value]


class DayOfWeek(str, Enum):
    MON = "mon"
    TUE = "tue"
    WED = "wed"
    THU = "thu"
    FRI = "fri"
    SAT = "sat"
    SUN = "sun"

    @property
    def label(self) -> str:
        if _LOCALIZATION is not None:
            return _LOCALIZATION.day_of_week.get(self.value,
                                                  _DEFAULT_DAY_LABELS[self.value])
        return _DEFAULT_DAY_LABELS[self.value]


_PHASE_ORDER = [
    TimeOfDay.MORNING, TimeOfDay.NOON, TimeOfDay.AFTERNOON,
    TimeOfDay.EVENING, TimeOfDay.NIGHT, TimeOfDay.MIDNIGHT,
]
_DAY_ORDER = [DayOfWeek.MON, DayOfWeek.TUE, DayOfWeek.WED, DayOfWeek.THU,
              DayOfWeek.FRI, DayOfWeek.SAT, DayOfWeek.SUN]


class TimeSystem(BaseModel):
    day: int = 1
    weekday_index: int = 0
    phase_index: int = 0

    @property
    def time_of_day(self) -> TimeOfDay:
        return _PHASE_ORDER[self.phase_index]

    @property
    def day_of_week(self) -> DayOfWeek:
        return _DAY_ORDER[self.weekday_index]

    def advance(self, phases: int = 1) -> None:
        """Advance time by N phases (e.g. morning -> noon)."""
        for _ in range(phases):
            self.phase_index += 1
            if self.phase_index >= len(_PHASE_ORDER):
                self.phase_index = 0
                self.day += 1
                self.weekday_index = (self.weekday_index + 1) % 7

    def set_phase(self, phase: TimeOfDay) -> None:
        self.phase_index = _PHASE_ORDER.index(phase)

    def label(self) -> str:
        if _LOCALIZATION is not None:
            return _LOCALIZATION.t(
                "day_format", "第 {day} 天 · {weekday} · {time_of_day}",
                day=self.day,
                weekday=self.day_of_week.label,
                time_of_day=self.time_of_day.label,
            )
        return (f"第 {self.day} 天 · {self.day_of_week.label}"
                f" · {self.time_of_day.label}")

    def is_night(self) -> bool:
        return self.time_of_day in (TimeOfDay.NIGHT, TimeOfDay.MIDNIGHT)

    def is_haunting_hour(self) -> bool:
        return self.time_of_day == TimeOfDay.MIDNIGHT

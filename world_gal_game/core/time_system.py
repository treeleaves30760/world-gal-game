"""In-game time and calendar system.

Tracks day number, day-of-week, and time-of-day. Many gal-game mechanics
(NPC schedules, events that only happen at night, etc.) depend on this.
"""
from __future__ import annotations

from enum import Enum
from pydantic import BaseModel, PrivateAttr


# Compatibility-only localization for the historical enum ``.label`` API.
# The live engine no longer binds or reads this slot: every TimeSystem owns its
# own localization service.  Keeping the old function/property behaviour avoids
# breaking embedders that still format a bare TimeOfDay/DayOfWeek value.
_LOCALIZATION = None


def bind_localization(loc) -> None:
    """Compatibility binding for bare enum ``.label`` access.

    New runtime code must call :meth:`TimeSystem.bind_localization` and format
    through that instance.  This process-global seam remains only for older
    integrations that do not have a TimeSystem instance available.
    """
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
    # Presentation-only service, deliberately absent from model_dump/save data.
    _localization: object | None = PrivateAttr(default=None)

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

    def bind_localization(self, localization) -> None:
        """Bind labels to this clock only, without mutating process globals."""
        self._localization = localization

    def time_label(self, value: TimeOfDay | str | None = None) -> str:
        """Localized label for ``value`` (or the current time of day)."""
        raw = self.time_of_day if value is None else value
        key = raw.value if isinstance(raw, TimeOfDay) else str(raw)
        if self._localization is not None:
            translate = getattr(self._localization, "time_label", None)
            if callable(translate):
                return str(translate(key))
        return _DEFAULT_TIME_LABELS.get(key, key)

    def weekday_label(self, value: DayOfWeek | str | None = None) -> str:
        """Localized label for ``value`` (or the current weekday)."""
        raw = self.day_of_week if value is None else value
        key = raw.value if isinstance(raw, DayOfWeek) else str(raw)
        if self._localization is not None:
            translate = getattr(self._localization, "weekday_label", None)
            if callable(translate):
                return str(translate(key))
        return _DEFAULT_DAY_LABELS.get(key, key)

    def label(self) -> str:
        if self._localization is not None:
            translate = getattr(self._localization, "t", None)
            if callable(translate):
                return str(translate(
                    "day_format", "第 {day} 天 · {weekday} · {time_of_day}",
                    day=self.day,
                    weekday=self.weekday_label(),
                    time_of_day=self.time_label(),
                ))
        return (f"第 {self.day} 天 · {self.weekday_label()}"
                f" · {self.time_label()}")

    def is_night(self) -> bool:
        return self.time_of_day in (TimeOfDay.NIGHT, TimeOfDay.MIDNIGHT)

    def is_haunting_hour(self) -> bool:
        return self.time_of_day == TimeOfDay.MIDNIGHT

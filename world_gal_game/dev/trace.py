"""Execution-trace recorder for headless / dev sessions.

Records an ordered, structured trace of what *actually happened* during a run:
which effects fired (and their result dicts), which lines and choices were
presented, player moves, and time advances. This is the data that lets an agent
*see* what a script changed, beyond the final state — the anti-MCP "rich
one-shot batch" payload.

It is an engine-internal listener, **not** a user plugin: it registers
:class:`HookEntry` objects directly into ``HOOK_REGISTRY`` under the reserved
owner id ``__trace__`` and removes them with ``unregister_plugin('__trace__')``.
``HookRegistry.fire`` wraps every handler in ``isolate(...)``, so a recorder
error can never crash the engine.
"""
from __future__ import annotations

from typing import Any

from ..plugins import HOOK_REGISTRY
from ..plugins.context import HookEvent
from ..plugins.registry import HookEntry

OWNER = "__trace__"


class TraceRecorder:
    """Collects trace entries by subscribing to engine lifecycle hooks.

    Typical use (done for you by :class:`HeadlessSession`)::

        rec = TraceRecorder().attach()
        ...  # run a script
        entries = rec.drain()   # list of JSON-friendly dicts, in order
    """

    def __init__(self) -> None:
        self.entries: list[dict[str, Any]] = []
        self._seq = 0

    # -- lifecycle -----------------------------------------------------
    def attach(self) -> "TraceRecorder":
        """Register hook handlers (idempotent — re-claims ownership)."""
        self.detach()
        subs = [
            (HookEvent.EFFECT_AFTER_APPLY, self._on_effect),
            (HookEvent.DIALOGUE_AFTER_LINE, self._on_line),
            (HookEvent.DIALOGUE_CHOICE_MADE, self._on_choice),
            (HookEvent.PLAYER_MOVE, self._on_move),
            (HookEvent.TIME_ADVANCE, self._on_time),
        ]
        for event, fn in subs:
            HOOK_REGISTRY.register(HookEntry(
                event=event, fn=fn, plugin_id=OWNER,
                description="execution trace recorder", priority=1000))
        return self

    def detach(self) -> None:
        HOOK_REGISTRY.unregister_plugin(OWNER)

    def clear(self) -> None:
        self.entries = []
        self._seq = 0

    def drain(self) -> list[dict[str, Any]]:
        """Return collected entries and reset the buffer."""
        out = self.entries
        self.entries = []
        return out

    # -- recording -----------------------------------------------------
    def _add(self, event: str, **fields: Any) -> None:
        self._seq += 1
        self.entries.append({"seq": self._seq, "event": event, **fields})

    def _on_effect(self, ctx: Any, *, eff: Any = None,
                   result: Any = None, **_: Any) -> None:
        if eff is None:
            return
        self._add("effect",
                  kind=getattr(eff, "kind", None),
                  target=getattr(eff, "target", "") or None,
                  value=getattr(eff, "value", None),
                  stat=getattr(eff, "stat", None),
                  result=result if isinstance(result, dict) else None)

    def _on_line(self, ctx: Any, *, scene_id: Any = None,
                 line_index: Any = None, line: Any = None, **_: Any) -> None:
        text = getattr(line, "plain_text", None) or getattr(line, "text", None)
        self._add("line", scene_id=scene_id, line_index=line_index,
                  speaker=getattr(line, "speaker", None), text=text)

    def _on_choice(self, ctx: Any, *, scene_id: Any = None,
                   choice_id: Any = None, **_: Any) -> None:
        self._add("choice", scene_id=scene_id, choice_id=choice_id)

    def _on_move(self, ctx: Any, *, from_location: Any = None,
                 to_location: Any = None, **_: Any) -> None:
        # "from" is a keyword, so build the dict explicitly.
        self._add("move", **{"from": from_location, "to": to_location})

    def _on_time(self, ctx: Any, *, phases: Any = None, day: Any = None,
                 time_of_day: Any = None, **_: Any) -> None:
        self._add("time", phases=phases, day=day, time_of_day=time_of_day)

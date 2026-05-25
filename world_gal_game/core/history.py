"""Player-facing rollback buffer.

A bounded stack of (state snapshot, display payload) checkpoints, powering
Ren'Py-style rollback: the player rewinds the game state one displayed step at
a time. It is built on the very same deterministic, serializable
snapshot/restore in ``dev/diff`` that the agent layer uses for branch
exploration — one mechanism, two audiences. The agent explores a tree; the
player walks backwards down a single trunk.

Each checkpoint pairs a JSON snapshot of the game state with an opaque
*payload* — the dialogue presentation that was on screen at that point. A
rewind therefore restores both the state and what to redraw, WITHOUT re-running
the engine. That matters: re-running the engine to re-show a line would re-fire
its effects, dialogue ops, read-log marks, and hooks. By storing the
already-rendered presentation, rollback is a pure visual + state restore — the
effects were applied once, when the line first played, and the snapshot already
captures their result.

The top of the stack is the *current* display. :meth:`rewind` drops it and
returns to the one before, so it needs at least two entries to move. This
buffer holds large snapshot dicts and is pure runtime machinery — never
serialized. The dialogue scene keeps it per-scene, so rollback stays within the
current scene.
"""
from __future__ import annotations

from typing import Any


class StateHistory:
    """A bounded stack of (snapshot, payload) checkpoints for rollback.

    :meth:`record` the current display after each line / choice is shown;
    :meth:`rewind` drops the current entry, restores the previous one into the
    live state, and returns its payload (the presentation to redraw). At most
    ``max_entries`` checkpoints are retained — older ones are dropped — so the
    player can rewind at most that many steps.

    Not a pydantic model: it carries un-serializable snapshot payloads and is
    never persisted.
    """

    def __init__(self, max_entries: int = 50) -> None:
        self.max_entries = max_entries
        self._stack: list[tuple[dict, Any]] = []

    def record(self, state, payload: Any = None) -> None:
        """Snapshot ``state`` paired with ``payload`` as the current checkpoint.

        Call this *after* each displayed step (a line or a choice menu). The
        ``payload`` is whatever the caller needs to redraw that step (the
        engine's presentation). If the stack would exceed ``max_entries`` the
        oldest entries are dropped.

        Defensive: a serialization hiccup degrades to silently skipping the
        push rather than raising into the game loop.
        """
        try:
            from ..dev.diff import snapshot
            snap = snapshot(state)
        except Exception:
            return
        self._stack.append((snap, payload))
        if len(self._stack) > self.max_entries:
            self._stack = self._stack[-self.max_entries:]

    def can_rewind(self) -> bool:
        """True iff there is a previous display to rewind to.

        Needs at least two entries: the current display and the one before it.
        """
        return len(self._stack) >= 2

    def rewind(self, state) -> Any | None:
        """Drop the current display, restore the previous one, return its payload.

        Pops the top (the current display), restores the new top's snapshot
        into ``state`` in place, and returns that entry's payload so the caller
        can redraw it. The restored entry stays on the stack as the new current
        display, so a subsequent :meth:`rewind` steps back again. Returns
        ``None`` (and leaves state untouched) when there is nothing to rewind to
        or the restore degrades to a no-op.

        Worked example: ``record(P0) -> record(P1) -> record(P2)`` then two
        rewinds returns the live state + payload to P1, then to P0.
        """
        if len(self._stack) < 2:
            return None
        self._stack.pop()                       # discard the current display
        snap, payload = self._stack[-1]         # previous becomes current
        try:
            from ..dev.diff import restore
            restore(state, snap)
        except Exception:
            return None
        return payload

    def depth(self) -> int:
        """Number of stored checkpoints."""
        return len(self._stack)

    def clear(self) -> None:
        """Discard every stored checkpoint."""
        self._stack.clear()

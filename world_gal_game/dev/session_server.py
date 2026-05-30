"""Warm headless session server: load the pack once, stream NDJSON ops forever.

This is the engine's *fast control plane* — the deliberate answer to "why not
just expose this over MCP / a JSON-RPC server?" A cold ``wgg --headless
--script`` invocation pays the pack-load tax (parse YAML, build the
:class:`~world_gal_game.core.game_state.GameState`, register plugins) on every
call, and a network RPC adds a per-op round-trip on top. For an agent that
wants to fire *thousands* of small ops — set a flag, check a condition,
snapshot, branch, restore — that overhead dominates the work.

A :class:`SessionServer` instead keeps one live
:class:`~world_gal_game.headless.HeadlessSession` resident in a single process
and speaks newline-delimited JSON (NDJSON) over stdin/stdout: one JSON object
per input line, one JSON object per output line. The pack is loaded exactly
once; the marginal cost of an op is the op itself, not a process spawn or an
RPC hop. The op vocabulary is *identical* to
:meth:`HeadlessSession.run_script` — there is no second schema to learn and no
envelope to translate, so anything an agent can script in a batch it can also
drive incrementally over the wire.

Line protocol
-------------

Each non-blank input line is one JSON value. Blank / whitespace-only lines are
ignored (no response, no sequence advance). Every response is one JSON object
on its own line, carrying a monotonically increasing ``"seq"`` (one per
non-blank input line) and an ``"ok"`` boolean. The server never raises out of
:meth:`SessionServer.handle`; engine errors and malformed input both come back
as ``{"ok": false, "seq": n, "error": "<msg>"}``.

A line is dispatched as one of three shapes:

- **Control op** — a dict whose ``"op"`` is one of the dunder control verbs
  below. These manage the session itself rather than game state.
- **Batch** — ``{"ops": [ ... ]}`` runs the whole list through
  :meth:`HeadlessSession.run_script` in one shot and returns ``"results"``
  (one per op) plus the run's ``"transcript"``. Add ``"atomic": true`` to make
  the batch all-or-nothing across *both* live state and pack edits: a runtime
  snapshot + edit transaction are taken up front, and on any failing op the
  staged edits are discarded and the state restored (``"atomic":
  "rolled_back"``); on success the edits commit and the commit ``"impact"`` is
  folded in (``"atomic": "committed"``).
- **Single op** — any other dict is treated as one op, run as a one-element
  batch, and returned as ``"result"`` (the single result dict) plus the
  ``"transcript"``.

The op vocabulary includes the warm structural-edit loop: ``edit.*`` ops
(add_scene / add_choice / update_line / add_npc / add_location / ...) autocommit
and return a YAML ``diff`` plus an ``impact`` delta (new dead-ends / unreachable
endings / undeclared flags), so an agent can *understand, edit, and verify* a
pack without leaving the warm process.

Control ops
-----------

- ``{"op": "__ping__"}`` -> ``{"ok": true, "seq": n, "pong": true}`` — liveness.
- ``{"op": "__inspect__"}`` -> ``{..., "snapshot": <inspect()>}`` — full state
  view (same payload as the ``inspect`` op / ``HeadlessSession.inspect``).
- ``{"op": "__affordances__"}`` -> ``{..., "affordances": <affordances()>}`` —
  the current action space.
- ``{"op": "__reset__"}`` -> rebuild a fresh session from the configured
  ``opener`` (``{..., "reset": true}``), or ``error`` if none was configured.
- ``{"op": "__quit__"}`` -> ``{..., "bye": true}`` and sets
  :attr:`SessionServer.should_quit`, which ends :meth:`SessionServer.serve`
  after the response is written.
- ``{"op": "__begin__"}`` / ``{"op": "__commit__"}`` / ``{"op": "__rollback__"}``
  -> manage a structural-edit transaction (stage many ``edit.*`` ops, then
  commit once for one aggregate ``impact`` or roll them all back).
- ``{"op": "__reload__"}`` -> rebuild the in-memory pack from disk so runtime
  ops (play / plan) see committed edits; resets the runtime position.
"""
from __future__ import annotations

import json
import sys
from typing import Any, Callable

from ..config import EngineConfig
from ..headless import HeadlessSession


class SessionServer:
    """A long-lived NDJSON command processor wrapped around a live session.

    Holds one resident :class:`HeadlessSession` so an agent can stream many
    ops without re-loading the pack. :meth:`handle` turns a single input line
    into a single response string; :meth:`serve` drives the stdin/stdout loop.
    An optional ``opener`` (a zero-arg callable returning a fresh session) lets
    the ``__reset__`` control op rebuild the session from scratch.
    """

    def __init__(self, session: HeadlessSession, *,
                 opener: Callable[[], HeadlessSession] | None = None) -> None:
        self.session = session
        self._opener = opener
        self._seq = 0
        # Public flag the serve loop polls; flipped true by the __quit__ op.
        self.should_quit = False

    # ----- single-line dispatch -----------------------------------------

    def handle(self, line: str) -> str | None:
        """Process one input line; return one JSON response string or ``None``.

        Returns ``None`` for a blank / whitespace-only line (no sequence
        advance). Otherwise increments :attr:`_seq`, parses the line as JSON,
        dispatches it (control op / batch / single op), and serializes the
        response. Never raises: parse errors and engine exceptions alike are
        reported as ``{"ok": false, "seq": n, "error": "<msg>"}``.
        """
        if not line.strip():
            return None
        self._seq += 1
        seq = self._seq
        try:
            obj = json.loads(line)
        except (ValueError, TypeError) as exc:
            return self._dump({"ok": False, "seq": seq, "error": str(exc)})

        try:
            resp = self._dispatch(obj, seq)
        except Exception as exc:  # engine errors degrade to a JSON error reply
            resp = {"ok": False, "seq": seq, "error": str(exc)}
        return self._dump(resp)

    def _dispatch(self, obj: Any, seq: int) -> dict:
        if isinstance(obj, dict):
            op = obj.get("op")
            if isinstance(op, str) and op.startswith("__") and op.endswith("__"):
                return self._control(op, seq)
            if "ops" in obj:
                return self._batch(obj["ops"], seq,
                                   atomic=bool(obj.get("atomic", False)))
            return self._single(obj, seq)
        # A bare scalar / list with no recognizable shape is an error, not a crash.
        return {"ok": False, "seq": seq, "error": f"unhandled message: {obj!r}"}

    def _control(self, op: str, seq: int) -> dict:
        if op == "__ping__":
            return {"ok": True, "seq": seq, "pong": True}
        if op == "__inspect__":
            return {"ok": True, "seq": seq, "snapshot": self.session.inspect()}
        if op == "__affordances__":
            return {"ok": True, "seq": seq, "affordances": self.session.affordances()}
        if op == "__reset__":
            if self._opener is None:
                return {"ok": False, "seq": seq, "error": "no opener configured"}
            self.session = self._opener()
            return {"ok": True, "seq": seq, "reset": True}
        if op == "__quit__":
            self.should_quit = True
            return {"ok": True, "seq": seq, "bye": True}
        # Structural-edit transaction controls — thin aliases over the session's
        # warm-edit API, so an agent can manage a transaction without wrapping
        # each control in a one-op batch.
        if op == "__begin__":
            return {"seq": seq, **self.session.begin_edit()}
        if op == "__commit__":
            return {"seq": seq, **self.session.commit_edit()}
        if op == "__rollback__":
            return {"seq": seq, **self.session.rollback_edit()}
        if op == "__reload__":
            return {"seq": seq, **self.session.reload_content()}
        return {"ok": False, "seq": seq, "error": f"unknown control op: {op}"}

    def _batch(self, ops: Any, seq: int, *, atomic: bool = False) -> dict:
        if atomic:
            return self._atomic_batch(list(ops), seq)
        results = self.session.run_script(list(ops))
        ok = all(r.get("ok", True) for r in results)
        return {"ok": ok, "seq": seq, "results": results,
                "transcript": self.session.transcript}

    def _atomic_batch(self, ops: list, seq: int) -> dict:
        """Run a batch all-or-nothing across *both* state and pack edits.

        A runtime snapshot is taken and a structural-edit transaction opened up
        front; staged edits are only written on success. If any op reports
        ``ok: false`` the staged edits are discarded and the live state is
        restored, so the batch leaves no half-applied change behind. On success
        the edits are committed and the commit's aggregate ``impact`` is folded
        into the response.
        """
        sess = self.session
        guard = "__atomic__"
        sess.take_snapshot(guard)
        sess.begin_edit()
        results = sess.run_script(ops)
        ok = all(r.get("ok", True) for r in results)
        resp: dict = {"seq": seq, "results": results}
        if ok:
            commit = sess.commit_edit()
            resp["atomic"] = "committed"
            if commit.get("impact") is not None:
                resp["impact"] = commit["impact"]
            if commit.get("files"):
                resp["files"] = commit["files"]
        else:
            sess.rollback_edit()
            sess.restore_snapshot(guard)
            resp["atomic"] = "rolled_back"
        sess._snapshots.pop(guard, None)
        resp["ok"] = ok
        resp["transcript"] = sess.transcript
        return resp

    def _single(self, obj: dict, seq: int) -> dict:
        results = self.session.run_script([obj])
        result = results[0]
        return {"ok": result.get("ok", True), "seq": seq, "result": result,
                "transcript": self.session.transcript}

    @staticmethod
    def _dump(resp: dict) -> str:
        return json.dumps(resp, ensure_ascii=False)

    # ----- stdin / stdout loop ------------------------------------------

    def serve(self, stdin, stdout) -> None:
        """Read lines from ``stdin``, write one NDJSON response per line.

        Skips blank lines silently. Flushes after every response so a piped
        consumer sees each result immediately. Stops after writing the
        response to a ``__quit__`` op (when :attr:`should_quit` flips true) and
        returns cleanly on EOF.
        """
        for line in stdin:
            resp = self.handle(line)
            if resp is not None:
                stdout.write(resp + "\n")
                stdout.flush()
            if self.should_quit:
                break


def run_session(*, pack: str, seed: int | None = None,
                stdin=None, stdout=None) -> None:
    """Open a warm session for ``pack`` and serve NDJSON on stdin/stdout.

    Builds an ``opener`` closure so the ``__reset__`` control op can rebuild a
    fresh session with the same pack + seed, opens the first session from it,
    and runs the :meth:`SessionServer.serve` loop. Defaults to the process's
    real ``sys.stdin`` / ``sys.stdout`` when no streams are supplied.
    """
    def opener() -> HeadlessSession:
        return HeadlessSession.open(EngineConfig(seed=seed), pack=pack)

    server = SessionServer(opener(), opener=opener)
    server.serve(stdin or sys.stdin, stdout or sys.stdout)

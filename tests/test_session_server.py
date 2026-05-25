"""Warm NDJSON session server: line protocol, control ops, and the serve loop.

Exercises :class:`~world_gal_game.dev.session_server.SessionServer` over a real
demo_pack session, confirming each line shape (control op / batch / single op /
malformed) and that the stdin/stdout loop emits one NDJSON response per line.
"""
from __future__ import annotations

import io
import json

import pytest

from world_gal_game.config import EngineConfig
from world_gal_game.dev.session_server import SessionServer, run_session
from world_gal_game.headless import HeadlessSession


def _open() -> HeadlessSession:
    return HeadlessSession.open(EngineConfig(seed=1), pack="demo_pack")


@pytest.fixture
def server() -> SessionServer:
    return SessionServer(_open(), opener=_open)


def _parse(resp: str) -> dict:
    return json.loads(resp)


# ----- control ops + sequencing -------------------------------------------

def test_ping_returns_pong_and_first_seq(server):
    resp = _parse(server.handle('{"op":"__ping__"}'))
    assert resp["pong"] is True
    assert resp["ok"] is True
    assert resp["seq"] == 1


def test_blank_line_returns_none_and_does_not_advance_seq(server):
    assert server.handle("   ") is None
    # The next real line is seq 2 (the ping below already took seq 1).
    server.handle('{"op":"__ping__"}')        # seq 1
    assert server.handle("\t\n") is None       # blank: no advance
    resp = _parse(server.handle('{"op":"__ping__"}'))  # seq 2
    assert resp["seq"] == 2


def test_quit_sets_should_quit_and_reports_bye(server):
    resp = _parse(server.handle('{"op":"__quit__"}'))
    assert resp["bye"] is True
    assert server.should_quit is True


def test_inspect_control_op_returns_snapshot_with_flags(server):
    resp = _parse(server.handle('{"op":"__inspect__"}'))
    assert resp["ok"] is True
    assert isinstance(resp["snapshot"], dict)
    assert "flags" in resp["snapshot"]


def test_affordances_control_op(server):
    resp = _parse(server.handle('{"op":"__affordances__"}'))
    assert resp["ok"] is True
    assert isinstance(resp["affordances"], dict)


# ----- single op dispatch -------------------------------------------------

def test_set_flag_op_reports_diff_and_transcript(server):
    resp = _parse(server.handle('{"op":"set_flag","key":"probe"}'))
    assert resp["ok"] is True
    diff = resp["result"]["diff"]
    assert any("probe" in path for path in diff)
    assert isinstance(resp["transcript"], list)


def test_apply_then_check_round_trips(server):
    applied = _parse(
        server.handle('{"op":"apply","effect":{"kind":"set_flag","target":"a2"}}'))
    assert applied["ok"] is True
    checked = _parse(
        server.handle('{"op":"check","condition":{"kind":"flag","target":"a2"}}'))
    assert checked["result"]["result"] is True


# ----- batch dispatch -----------------------------------------------------

def test_batch_runs_all_ops_and_reports_each_result(server):
    resp = _parse(server.handle(
        '{"ops":[{"op":"set_flag","key":"b1"},'
        '{"op":"check","condition":{"kind":"flag","target":"b1"}}]}'))
    assert resp["ok"] is True
    assert len(resp["results"]) == 2
    assert resp["results"][1]["result"] is True


# ----- robustness ---------------------------------------------------------

def test_malformed_json_reports_error_without_raising(server):
    resp = _parse(server.handle('{not json'))
    assert resp["ok"] is False
    assert "error" in resp


# ----- reset rebuilds a fresh session -------------------------------------

def test_reset_rebuilds_fresh_session(server):
    server.handle('{"op":"set_flag","key":"gone_after_reset"}')
    before = _parse(server.handle('{"op":"__inspect__"}'))
    assert "gone_after_reset" in before["snapshot"]["flags"]

    reset = _parse(server.handle('{"op":"__reset__"}'))
    assert reset["reset"] is True

    after = _parse(server.handle('{"op":"__inspect__"}'))
    assert "gone_after_reset" not in after["snapshot"]["flags"]


def test_reset_without_opener_reports_error():
    server = SessionServer(_open())  # no opener configured
    resp = _parse(server.handle('{"op":"__reset__"}'))
    assert resp["ok"] is False
    assert "error" in resp


# ----- serve loop ---------------------------------------------------------

def test_serve_loop_emits_one_ndjson_line_per_input(server):
    stdin = io.StringIO('{"op":"__ping__"}\n{"op":"__ping__"}\n')
    stdout = io.StringIO()
    server.serve(stdin, stdout)
    lines = [ln for ln in stdout.getvalue().splitlines() if ln.strip()]
    assert len(lines) == 2
    for ln in lines:
        assert _parse(ln)["pong"] is True


def test_serve_loop_stops_after_quit(server):
    stdin = io.StringIO('{"op":"__quit__"}\n{"op":"__ping__"}\n')
    stdout = io.StringIO()
    server.serve(stdin, stdout)
    lines = [ln for ln in stdout.getvalue().splitlines() if ln.strip()]
    # Only the __quit__ response is written; the trailing ping is never read.
    assert len(lines) == 1
    assert _parse(lines[0])["bye"] is True


def test_run_session_drives_serve_over_supplied_streams():
    stdin = io.StringIO('{"op":"__ping__"}\n{"op":"__quit__"}\n')
    stdout = io.StringIO()
    run_session(pack="demo_pack", seed=1, stdin=stdin, stdout=stdout)
    lines = [ln for ln in stdout.getvalue().splitlines() if ln.strip()]
    assert len(lines) == 2
    assert _parse(lines[0])["pong"] is True
    assert _parse(lines[1])["bye"] is True

"""Fix 4: ``--headless --script`` must exit non-zero when an assert fails.

A script whose ``assert`` op fails (or whose op errors) previously still exited
0, so ``cmd && echo OK`` shells and CI gates silently missed regressions — this
is partly how a route-strand once stayed hidden. The module-level
:func:`world_gal_game.headless.run_script` now returns a process exit code: 1 on
any failing assert / errored op, 0 otherwise. The CLI ``--headless --script``
path propagates that code.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

from world_gal_game.config import EngineConfig
from world_gal_game.headless import run_script


def _write(tmp_path: Path, commands: list[dict]) -> str:
    p = tmp_path / "script.json"
    p.write_text(json.dumps({"commands": commands}), encoding="utf-8")
    return str(p)


def test_passing_asserts_exit_zero(tmp_path: Path, capsys):
    """A script whose asserts all pass exits 0."""
    script = _write(tmp_path, [
        {"op": "set_flag", "key": "probe", "value": True},
        {"op": "assert", "flag": "probe"},
        {"op": "assert", "flag": "probe", "equals": True},
    ])
    code = run_script(EngineConfig(seed=1), script, pack="demo_pack",
                      inspect_after=False)
    assert code == 0


def test_no_asserts_exit_zero(tmp_path: Path):
    """A script with no asserts and no errored ops exits 0 (back-compat)."""
    script = _write(tmp_path, [
        {"op": "set_flag", "key": "x", "value": True},
        {"op": "inspect"},
    ])
    code = run_script(EngineConfig(seed=1), script, pack="demo_pack",
                      inspect_after=False)
    assert code == 0


def test_failing_assert_exits_nonzero(tmp_path: Path, capsys):
    """A failing assert makes the run exit 1 and print a per-assert summary to
    stderr (while the JSON results still go to stdout)."""
    script = _write(tmp_path, [
        {"op": "assert", "flag": "never_set_flag"},      # falsy -> fails
    ])
    code = run_script(EngineConfig(seed=1), script, pack="demo_pack",
                      inspect_after=False)
    assert code == 1
    captured = capsys.readouterr()
    # stdout still carries the JSON envelope ...
    assert '"results"' in captured.out
    # ... and stderr carries a concise failure summary naming the failing assert.
    assert "assert" in captured.err.lower()
    assert "never_set_flag" in captured.err


def test_errored_op_exits_nonzero(tmp_path: Path):
    """An op that errors (e.g. an unknown op) also forces a non-zero exit."""
    script = _write(tmp_path, [
        {"op": "totally_unknown_op"},
    ])
    code = run_script(EngineConfig(seed=1), script, pack="demo_pack",
                      inspect_after=False)
    assert code == 1


def test_check_op_is_not_a_gate(tmp_path: Path):
    """A ``check`` op reports a boolean *result* and is NOT an expectation gate —
    a false check does not by itself fail the run (only ``assert`` does)."""
    script = _write(tmp_path, [
        {"op": "check", "condition": {"kind": "flag", "target": "nope"}},
    ])
    code = run_script(EngineConfig(seed=1), script, pack="demo_pack",
                      inspect_after=False)
    assert code == 0


# ---------------------------------------------------------------------------
# Fix 1: a MALFORMED assert (typo'd / unrecognized shape) must FAIL, not
# silently no-op as exit 0 — closing the CI hole where a typo'd assertion
# passes.
# ---------------------------------------------------------------------------

def test_malformed_assert_unknown_form_exits_nonzero(tmp_path: Path, capsys):
    """An assert matching NO recognized form (e.g. ``conditions`` plural for
    ``condition``) is a hard failure with a message naming the bad key(s)."""
    script = _write(tmp_path, [
        {"op": "assert", "conditions": {"kind": "flag", "target": "x"}},
    ])
    code = run_script(EngineConfig(seed=1), script, pack="demo_pack",
                      inspect_after=False)
    assert code == 1
    err = capsys.readouterr().err
    assert "malformed" in err.lower() or "invalid assert" in err.lower()
    assert "conditions" in err            # names the unrecognized key


def test_malformed_assert_typo_operator_does_not_silently_pass(tmp_path: Path,
                                                               capsys):
    """The core hole: ``{affection, gtee: N}`` (typo'd ``gte``) previously
    degraded to a mere presence check and passed (exit 0). It must now FAIL as a
    malformed assert naming the stray key."""
    script = _write(tmp_path, [
        {"op": "assert", "affection": "qingyi", "gtee": 9999},
    ])
    code = run_script(EngineConfig(seed=1), script, pack="demo_pack",
                      inspect_after=False)
    assert code == 1
    err = capsys.readouterr().err
    assert "gtee" in err
    assert "invalid assert" in err.lower() or "malformed" in err.lower()


def test_empty_assert_exits_nonzero(tmp_path: Path):
    """An assert op with no recognized key at all is malformed -> non-zero."""
    script = _write(tmp_path, [{"op": "assert"}])
    code = run_script(EngineConfig(seed=1), script, pack="demo_pack",
                      inspect_after=False)
    assert code == 1


def test_valid_asserts_with_extra_op_key_still_pass(tmp_path: Path):
    """Back-compat: the universal ``op`` key is allowed on every form, so a
    well-formed assert with a comparison still passes (exit 0)."""
    script = _write(tmp_path, [
        {"op": "set_flag", "key": "probe", "value": True},
        {"op": "assert", "flag": "probe", "equals": True},
        {"op": "assert", "affection": "qingyi", "gte": 0},
    ])
    code = run_script(EngineConfig(seed=1), script, pack="demo_pack",
                      inspect_after=False)
    assert code == 0


def test_ending_assert_form_is_recognized(tmp_path: Path):
    """The ``ending`` assert form is recognized: it fails when the ending is not
    reached and passes once its ``ending_<id>`` flag is set."""
    not_reached = _write(tmp_path, [{"op": "assert", "ending": "lover"}])
    assert run_script(EngineConfig(seed=1), not_reached, pack="demo_pack",
                      inspect_after=False) == 1
    reached = _write(tmp_path, [
        {"op": "set_flag", "key": "ending_lover", "value": True},
        {"op": "assert", "ending": "lover"},
    ])
    assert run_script(EngineConfig(seed=1), reached, pack="demo_pack",
                      inspect_after=False) == 0

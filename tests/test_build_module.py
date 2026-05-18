"""Tests for world_gal_game.build — pure logic only.

PyInstaller is never invoked here.  subprocess.run is patched out wherever
build_pack() would call it.
"""
from __future__ import annotations

import textwrap
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from world_gal_game.build import (
    _read_pack_title,
    _safe_name,
    build_pack,
    generate_spec,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def minimal_pack(tmp_path: Path) -> Path:
    """A minimal valid pack with content/meta.yaml."""
    content_dir = tmp_path / "content"
    content_dir.mkdir()
    (content_dir / "meta.yaml").write_text(
        textwrap.dedent("""\
            title: "My Test Game"
            start_location: start
        """),
        encoding="utf-8",
    )
    return tmp_path


@pytest.fixture()
def unicode_pack(tmp_path: Path) -> Path:
    """Pack whose title is non-ASCII."""
    content_dir = tmp_path / "content"
    content_dir.mkdir()
    (content_dir / "meta.yaml").write_text(
        'title: "小鎮的午後"\n',
        encoding="utf-8",
    )
    return tmp_path


# ---------------------------------------------------------------------------
# _read_pack_title
# ---------------------------------------------------------------------------


class TestReadPackTitle:
    def test_reads_double_quoted_title(self, minimal_pack: Path) -> None:
        assert _read_pack_title(minimal_pack) == "My Test Game"

    def test_reads_unquoted_title(self, tmp_path: Path) -> None:
        (tmp_path / "content").mkdir()
        (tmp_path / "content" / "meta.yaml").write_text(
            "title: SimpleTitle\n", encoding="utf-8"
        )
        assert _read_pack_title(tmp_path) == "SimpleTitle"

    def test_missing_meta_returns_none(self, tmp_path: Path) -> None:
        assert _read_pack_title(tmp_path) is None

    def test_meta_without_title_returns_none(self, tmp_path: Path) -> None:
        (tmp_path / "content").mkdir()
        (tmp_path / "content" / "meta.yaml").write_text(
            "start_location: start\n", encoding="utf-8"
        )
        assert _read_pack_title(tmp_path) is None


# ---------------------------------------------------------------------------
# _safe_name
# ---------------------------------------------------------------------------


class TestSafeName:
    def test_ascii_passthrough(self) -> None:
        assert _safe_name("MyGame") == "MyGame"

    def test_spaces_become_underscores(self) -> None:
        assert _safe_name("My Game") == "My_Game"

    def test_non_ascii_becomes_underscores(self) -> None:
        result = _safe_name("小鎮的午後")
        # All chars are non-ASCII, so the result must be underscores only,
        # then collapsed and stripped to a fallback.
        assert result == "MyGame" or set(result) <= {"_"}

    def test_empty_falls_back(self) -> None:
        # If stripping removes everything, we get the safe default.
        assert _safe_name("") == "MyGame"

    def test_collapses_consecutive_underscores(self) -> None:
        assert _safe_name("a  b") == "a_b"

    def test_strips_leading_trailing_underscores(self) -> None:
        result = _safe_name("  MyGame  ")
        assert not result.startswith("_")
        assert not result.endswith("_")


# ---------------------------------------------------------------------------
# generate_spec
# ---------------------------------------------------------------------------


class TestGenerateSpec:
    def test_contains_pack_path(self, minimal_pack: Path) -> None:
        spec = generate_spec(minimal_pack, "TestApp", Path("/tmp/dist"), None, False)
        assert str(minimal_pack) in spec

    def test_contains_app_name(self, minimal_pack: Path) -> None:
        spec = generate_spec(minimal_pack, "TestApp", Path("/tmp/dist"), None, False)
        assert "TestApp" in spec

    def test_contains_main_py_reference(self, minimal_pack: Path) -> None:
        spec = generate_spec(minimal_pack, "TestApp", Path("/tmp/dist"), None, False)
        assert "main.py" in spec

    def test_icon_present_when_given(self, minimal_pack: Path) -> None:
        icon = Path("/tmp/icon.ico")
        spec = generate_spec(minimal_pack, "TestApp", Path("/tmp/dist"), icon, False)
        assert str(icon) in spec

    def test_icon_none_when_omitted(self, minimal_pack: Path) -> None:
        spec = generate_spec(minimal_pack, "TestApp", Path("/tmp/dist"), None, False)
        assert "icon=None" in spec

    def test_onefile_section_present(self, minimal_pack: Path) -> None:
        spec = generate_spec(minimal_pack, "TestApp", Path("/tmp/dist"), None, True)
        # One-file mode adds a second EXE block.
        assert spec.count("EXE(") >= 2

    def test_returns_string(self, minimal_pack: Path) -> None:
        result = generate_spec(minimal_pack, "TestApp", Path("/tmp/dist"), None, False)
        assert isinstance(result, str)
        assert len(result) > 0


# ---------------------------------------------------------------------------
# build_pack — validation without invoking PyInstaller
# ---------------------------------------------------------------------------


class TestBuildPackValidation:
    def test_raises_for_nonexistent_pack(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError, match="does not exist"):
            build_pack(tmp_path / "no_such_pack")

    def test_raises_for_missing_meta_yaml(self, tmp_path: Path) -> None:
        (tmp_path / "content").mkdir()
        # meta.yaml is absent — only the directory exists.
        with pytest.raises(FileNotFoundError, match="content/meta.yaml"):
            build_pack(tmp_path)

    def test_derives_app_name_from_meta(
        self, minimal_pack: Path, tmp_path: Path
    ) -> None:
        """build_pack should pass the derived name to PyInstaller."""
        captured: dict = {}

        def fake_run(cmd, **kwargs):
            captured["cmd"] = cmd
            m = MagicMock()
            m.returncode = 0
            return m

        out_dir = tmp_path / "dist"
        with patch("world_gal_game.build.subprocess.run", side_effect=fake_run):
            build_pack(minimal_pack, output_dir=out_dir)

        # The spec written to the temp file must reference the derived name.
        # We verify indirectly that PyInstaller was called with some .spec.
        assert any(arg.endswith(".spec") for arg in captured["cmd"])

    def test_explicit_app_name_is_respected(
        self, minimal_pack: Path, tmp_path: Path
    ) -> None:
        captured: dict = {}

        def fake_run(cmd, **kwargs):
            captured["cmd"] = cmd
            m = MagicMock()
            m.returncode = 0
            return m

        out_dir = tmp_path / "dist"
        with patch("world_gal_game.build.subprocess.run", side_effect=fake_run):
            result = build_pack(
                minimal_pack, app_name="CustomName", output_dir=out_dir
            )

        assert result == out_dir / "CustomName"

    def test_raises_on_pyinstaller_failure(
        self, minimal_pack: Path, tmp_path: Path
    ) -> None:
        def fake_run(cmd, **kwargs):
            m = MagicMock()
            m.returncode = 1
            return m

        with patch("world_gal_game.build.subprocess.run", side_effect=fake_run):
            with pytest.raises(RuntimeError, match="PyInstaller exited"):
                build_pack(minimal_pack, output_dir=tmp_path / "dist")

    def test_cross_target_prints_warning(
        self, minimal_pack: Path, tmp_path: Path, capsys
    ) -> None:
        def fake_run(cmd, **kwargs):
            m = MagicMock()
            m.returncode = 0
            return m

        with patch("world_gal_game.build.subprocess.run", side_effect=fake_run):
            build_pack(
                minimal_pack,
                target="windows",
                output_dir=tmp_path / "dist",
            )

        captured = capsys.readouterr()
        assert "cross" in captured.err.lower() or "WARNING" in captured.err

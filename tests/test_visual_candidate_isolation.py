"""Candidate-output isolation for the built-in VisualCheck matrix."""

from pathlib import Path


def test_default_strict_matrix_does_not_overwrite_curated_root_candidate(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from world_gal_game.dev.visual_check import VisualCheck

    pack = tmp_path / "pack"
    curated_root = pack / "visual_candidates"
    curated_root.mkdir(parents=True)
    sentinel = curated_root / "title_720p.png"
    sentinel_bytes = b"curated-source-bound-candidate"
    sentinel.write_bytes(sentinel_bytes)

    check = VisualCheck(pack, strict_baselines=True)
    scenarios = VisualCheck.default_scenarios()

    def capture_without_pygame(**kwargs: object) -> Path:
        target = check.candidates_dir / f"{kwargs['name']}.png"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(f"visual-check:{kwargs['name']}".encode())
        return target

    monkeypatch.setattr(check, "capture", capture_without_pygame)
    report = check.run(scenarios)

    expected_names = [scenario["name"] for scenario in scenarios]
    assert len(expected_names) == 17 * 2
    assert len(expected_names) == len(set(expected_names))
    assert check.candidates_dir == curated_root / "visual_check"
    assert sentinel.read_bytes() == sentinel_bytes
    assert sorted(path.stem for path in check.candidates_dir.glob("*.png")) == (
        sorted(expected_names)
    )

    # Strict comparison remains fail-closed: captures are review candidates,
    # never implicit baseline promotions, and baseline names stay compatible.
    assert report.ok is False
    assert len(report.results) == 34
    assert all(result.created_baseline is False for result in report.results)
    assert all(
        Path(result.candidate_path)
        == check.candidates_dir / f"{result.name}.png"
        for result in report.results
    )
    assert all(
        Path(result.baseline_path)
        == pack / "visual_baselines" / f"{result.name}.png"
        for result in report.results
    )
    assert not (pack / "visual_baselines").exists()


def test_explicit_visual_candidate_directory_is_used_exactly(
    tmp_path: Path,
) -> None:
    from world_gal_game.dev.visual_check import VisualCheck

    explicit = tmp_path / "temporary-production-candidates"
    check = VisualCheck(tmp_path / "pack", candidates_dir=explicit)

    assert check.candidates_dir == explicit.resolve()

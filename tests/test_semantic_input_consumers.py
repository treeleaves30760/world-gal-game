"""Guard the semantic-input boundary for player-facing scene consumers."""
from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CONSUMER_ROOTS = (
    ROOT / "world_gal_game" / "scenes",
    ROOT / "world_gal_game" / "ui" / "widgets",
)

# These keys are deliberately physical rather than player actions: text
# editing, an always-available capture cancel, and a modifier-only gesture.
ALLOWED_PHYSICAL_KEYS = {
    "world_gal_game/scenes/controls_scene.py": {"K_ESCAPE"},
    "world_gal_game/scenes/title.py": {"K_ESCAPE"},
    "world_gal_game/scenes/flowchart_scene.py": {
        "K_LSHIFT",
        "K_RSHIFT",
    },
    "world_gal_game/ui/widgets/text_input.py": {
        "K_BACKSPACE",
        "K_DELETE",
        "K_LEFT",
        "K_RIGHT",
        "K_HOME",
        "K_END",
        "K_RETURN",
        "K_KP_ENTER",
    },
}


def _pygame_key_attributes(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    return {
        node.attr
        for node in ast.walk(tree)
        if (
            isinstance(node, ast.Attribute)
            and node.attr.startswith("K_")
            and isinstance(node.value, ast.Name)
            and node.value.id == "pygame"
        )
    }


def test_player_scenes_do_not_reintroduce_hard_coded_action_keys() -> None:
    violations: dict[str, list[str]] = {}
    for root in CONSUMER_ROOTS:
        for path in root.rglob("*.py"):
            relative = path.relative_to(ROOT).as_posix()
            unexpected = (
                _pygame_key_attributes(path)
                - ALLOWED_PHYSICAL_KEYS.get(relative, set())
            )
            if unexpected:
                violations[relative] = sorted(unexpected)

    assert violations == {}

"""Load a pack's tavern content: cards, persona, world book, style, presets.

Everything lives under ``<pack>/content/tavern/`` and every file is optional::

    content/tavern/
        cards/<npc_id>.yaml|.json|.png   character cards (tavern cards drop in)
        persona.yaml                     the player's side — who {{user}} is
        lorebook.yaml|.json              the pack's world book
        style.yaml                       style-guide overrides
        presets/<name>.yaml|.json        prompt presets (default.* is the default)
        expression_memo.yaml             remembered emotion-word resolutions
        briefs/<scene_id>.yaml           scene briefs for the stage

A pack with none of this still works: every NPC gets a card derived from its
``characters.yaml`` entry, and the built-in preset and style guide for the
pack's language are used.

Loading never raises for a single bad file. Problems are collected in
:attr:`TavernPack.issues` so one broken card cannot take a pack's tooling down.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field

from .card import CharacterCard, Lorebook, SpeechProfile, card_from_npc
from .expressions import ExpressionMemo
from .preset import Preset
from .style import StyleGuide, detect_locale, normalize_locale

CARD_SUFFIXES = (".yaml", ".yml", ".json", ".png")


class Persona(BaseModel):
    """The player's character: who ``{{user}}`` is."""

    model_config = ConfigDict(extra="forbid")

    name: str = ""                 # defaults to the pack's player name
    description: str = ""          # what others can see and know
    speech: SpeechProfile = Field(default_factory=SpeechProfile)
    backstory: str = ""            # private to whoever plays the protagonist
    goals: list[str] = Field(default_factory=list)
    secrets: list[str] = Field(default_factory=list)
    #: How the player's lines are attributed in scenes the stage writes.
    speaker: str = "{player_name}"
    #: Write the protagonist's speech as narration instead of speaker lines.
    as_narration: bool = False

    def to_card(self, player_name: str) -> CharacterCard:
        card = CharacterCard(
            id="player", name=self.name or player_name,
            description=self.description, backstory=self.backstory,
            goals=list(self.goals), secrets=list(self.secrets),
            speech=self.speech.model_copy(),
        )
        card.source = "persona"
        return card


@dataclass
class TavernPack:
    """A pack's tavern content, with every NPC's effective card resolved."""

    pack_root: Path
    root: Path
    cards: dict[str, CharacterCard] = field(default_factory=dict)
    persona: Persona = field(default_factory=Persona)
    lorebook: Lorebook | None = None
    style: StyleGuide = field(default_factory=StyleGuide.builtin)
    presets: dict[str, Preset] = field(default_factory=dict)
    memo: ExpressionMemo = field(default_factory=ExpressionMemo)
    issues: list[dict[str, str]] = field(default_factory=list)
    player_name: str = ""
    locale: str = "zh-Hant"

    # ------------------------------------------------------------------

    @classmethod
    def load(cls, pack_root: Path, npcs: Any = None, *, state: Any = None,
             meta: dict[str, Any] | None = None) -> "TavernPack":
        pack_root = Path(pack_root)
        content = pack_root / "content" if (pack_root / "content").is_dir() else pack_root
        root = content / "tavern"
        meta = meta or {}
        pack = cls(pack_root=pack_root, root=root)

        def issue(path: Path | str, message: str) -> None:
            name = path.name if isinstance(path, Path) else str(path)
            pack.issues.append({"file": name, "message": message})

        player = getattr(state, "player", None)
        pack.player_name = getattr(player, "name", "") or str(
            (meta.get("player") or {}).get("name", "") if isinstance(meta.get("player"),
                                                                     dict) else "")

        # Persona
        persona_path = root / "persona.yaml"
        if persona_path.exists():
            try:
                raw = yaml.safe_load(persona_path.read_text(encoding="utf-8")) or {}
                pack.persona = Persona.model_validate(raw)
            except Exception as exc:
                issue(persona_path, f"persona not loaded: {exc}")
        if not pack.persona.name:
            pack.persona.name = pack.player_name
        pack.player_name = pack.persona.name or pack.player_name

        # Cards: NPC-derived first, authored files layered on top.
        registry = list(npcs.all()) if npcs is not None else []
        by_name = {npc.name: npc for npc in registry}
        for npc in registry:
            pack.cards[npc.id] = card_from_npc(npc)
        cards_dir = root / "cards"
        if cards_dir.is_dir():
            for path in sorted(cards_dir.iterdir()):
                if path.suffix.lower() not in CARD_SUFFIXES or path.name.startswith("."):
                    continue
                try:
                    card = CharacterCard.load(path)
                except Exception as exc:
                    issue(path, f"card not loaded: {exc}")
                    continue
                if card.id not in pack.cards and card.name in by_name:
                    card.id = by_name[card.name].id     # bind by display name
                base = pack.cards.get(card.id)
                if base is not None:
                    try:
                        pack.cards[card.id] = card.merged_over(base)
                    except Exception as exc:
                        issue(path, f"card could not be merged over the NPC: {exc}")
                else:
                    pack.cards[card.id] = card
                    issue(path, f"card '{card.id}' has no matching character in "
                                "characters.yaml (usable in chat, not in scenes)")

        # World book
        for name in ("lorebook.yaml", "lorebook.yml", "lorebook.json"):
            path = root / name
            if path.exists():
                try:
                    pack.lorebook = Lorebook.load(path)
                    if not pack.lorebook.name:
                        pack.lorebook.name = "pack"
                except Exception as exc:
                    issue(path, f"world book not loaded: {exc}")
                break

        # Language, then the style guide for it.
        style_path = root / "style.yaml"
        declared = ""
        if style_path.exists():
            try:
                declared = str((yaml.safe_load(style_path.read_text(encoding="utf-8"))
                                or {}).get("locale") or "")
            except Exception:
                declared = ""
        tavern_meta = meta.get("tavern") if isinstance(meta.get("tavern"), dict) else {}
        locale = declared or str(tavern_meta.get("locale") or "")
        if not locale:
            locale = detect_locale(_sample_text(state, pack.cards))
        pack.locale = normalize_locale(locale)
        try:
            pack.style = StyleGuide.load(style_path if style_path.exists() else None,
                                         locale=pack.locale)
        except Exception as exc:
            issue(style_path, f"style guide not loaded: {exc}")
            pack.style = StyleGuide.builtin(pack.locale)

        # Presets
        presets_dir = root / "presets"
        if presets_dir.is_dir():
            for path in sorted(presets_dir.iterdir()):
                if path.suffix.lower() not in (".yaml", ".yml", ".json"):
                    continue
                try:
                    preset = Preset.load(path)
                    pack.presets[path.stem] = preset
                except Exception as exc:
                    issue(path, f"preset not loaded: {exc}")

        # Remembered expression words
        memo_path = root / "expression_memo.yaml"
        try:
            pack.memo = ExpressionMemo.load(memo_path)
        except Exception as exc:
            issue(memo_path, f"expression memo not loaded: {exc}")
            pack.memo = ExpressionMemo(path=memo_path)
        return pack

    # ------------------------------------------------------------------

    def card(self, key: str) -> CharacterCard | None:
        """Look a card up by character id, then by display name / nickname."""
        if key in self.cards:
            return self.cards[key]
        for card in self.cards.values():
            if card.name == key or (card.nickname and card.nickname == key):
                return card
        return None

    def preset(self, name: str | None = None) -> Preset:
        """The named preset, the pack's ``default``, or the built-in one."""
        if name:
            if name in self.presets:
                return self.presets[name]
            if name in ("galgame", "builtin"):
                return Preset.builtin(self.locale)
            raise KeyError(f"unknown preset '{name}' "
                           f"(have: {', '.join(sorted(self.presets)) or 'galgame'})")
        return self.presets.get("default") or Preset.builtin(self.locale)

    def player_card(self) -> CharacterCard:
        return self.persona.to_card(self.player_name or "Player")

    def never_says(self) -> dict[str, list[str]]:
        """``{display name: phrases}`` for the linter."""
        out: dict[str, list[str]] = {}
        for card in self.cards.values():
            if card.speech.never_says:
                out[card.name] = list(card.speech.never_says)
        return out

    def summary(self) -> dict[str, Any]:
        cards = []
        for card_id, card in sorted(self.cards.items()):
            cards.append({
                "id": card_id,
                "name": card.name,
                "source": card.source,
                "examples": len(card.example_lines()),
                "speech_profile": not card.speech.is_empty(),
                "secrets": len(card.secrets),
                "lore_entries": len(card.character_book.entries)
                if card.character_book else 0,
            })
        return {
            "root": str(self.root),
            "locale": self.locale,
            "player": self.player_name,
            "cards": cards,
            "lorebook_entries": len(self.lorebook.entries) if self.lorebook else 0,
            "presets": sorted(self.presets) or ["galgame (built-in)"],
            "style": {"region": self.style.region,
                      "line_max_chars": self.style.line_max_chars,
                      "patterns": len(self.style.active_patterns())},
            "issues": list(self.issues),
        }


def _sample_text(state: Any, cards: dict[str, CharacterCard]) -> str:
    """A bounded sample of the pack's prose, for language detection."""
    parts: list[str] = []
    budget = 4000
    story = getattr(state, "story", None)
    for scene in getattr(story, "scenes", {}).values():
        for line in scene.lines:
            parts.append(line.text)
            budget -= len(line.text)
            if budget <= 0:
                return "".join(parts)
    for card in cards.values():
        parts.append(card.description)
        parts.append(card.personality)
    return "".join(parts)


def tavern_for(session_or_state: Any, pack_root: Path, npcs: Any,
               meta: dict[str, Any] | None = None, *, refresh: bool = False
               ) -> TavernPack:
    """Load (once) and cache the pack's tavern content on the game state.

    The cache sits on the private ``__tavern__`` meta key, which the save
    system strips, so it is never written into a save file.
    """
    state = getattr(session_or_state, "state", session_or_state)
    store = getattr(state, "meta", None)
    if isinstance(store, dict) and not refresh:
        cached = store.get("__tavern__")
        if isinstance(cached, TavernPack):
            return cached
    pack = TavernPack.load(pack_root, npcs, state=state, meta=meta)
    if isinstance(store, dict):
        store["__tavern__"] = pack
    return pack

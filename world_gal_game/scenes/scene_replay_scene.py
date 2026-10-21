"""Scene replay overlay.

Lists completed scenes backed by an exact first-view presentation trace and
lets the player re-watch one in a **read-only sandbox**. A throwaway copy of
the live ``GameState`` and :class:`PresentationTraceDialogueEngine` feed the
archived, already-evaluated payloads through the regular
:class:`DialogueScene`. Recollection therefore neither re-evaluates current
conditions nor applies effects, follows another branch, or touches the live
save.

How the sandbox is isolated
---------------------------
``build_sandbox_context`` rebuilds the state by round-tripping it through
JSON: ``GameState(**ctx.state.model_dump(mode="json"))``. ``model_dump``
runs ``GameState._serialize_meta``, which drops every transient ``__``
meta bridge — including ``__plugin_manager__`` and ``__autosave_config__``.
So the sandbox state has no plugin manager or autosave bridge and owns copies
of every subsystem. More importantly, player-facing recollection uses the
trace engine, which never dispatches an authored effect at all. The isolation
still protects lower-level callers that explicitly request the ordinary-engine
fallback.

A trace player is bound to that sandbox state, and a sandbox
:class:`SceneContext` is produced with ``dataclasses.replace`` so the
heavyweight, read-only services (assets, fonts, theme, localization, npcs,
config) are shared while ``state`` and ``dialogue`` point at the sandbox.
The live context is never mutated. ``build_sandbox_context`` retains its
ordinary-engine fallback for lower-level tooling, but this player-facing scene
always supplies a completed archive entry and fails closed when none exists.

Rather than pushing the child dialogue onto the global
:class:`SceneManager`, this overlay *owns* the replay
:class:`DialogueScene`: it instantiates it on the sandbox context, drives its
``enter`` / ``update`` / ``draw`` directly, and drops it when the scene signals
completion. The semantic Back action cancels the owned child without ever
reaching live dialogue navigation.

The sandbox shares the live :class:`~world_gal_game.ui.assets.AssetManager` so
images and authored audio still render normally. Audio is the one shared
mutable presentation service: its BGM / ambient / voice ownership is captured
before replay and restored when replay completes, is cancelled, or the overlay
is otherwise removed. One-shot SFX use a narrower ownership token: teardown
stops only sounds started by the replay and leaves pre-existing live SFX alone.
"""
from __future__ import annotations

import dataclasses
import re
from bisect import bisect_right
from dataclasses import dataclass
from typing import Callable

import pygame

from .base import Scene, SceneContext
from .dialogue_scene import DialogueScene
from ..core.chapter_spec import ChapterManifest, ChapterSpec
from ..core.game_state import GameState
from ..core.map_system import MapSystem
from ..core.read_log import ReplayArchiveEntry
from ..core.story_graph import StoryGraph
from ..dialogue.dialogue_engine import DialogueEngine
from ..dialogue.replay_engine import PresentationTraceDialogueEngine
from ..dialogue.replay_engine import rebind_authored_replay_visual_fx
from ..ui.assets import AudioPlaybackSnapshot, SFXPlaybackOwnership
from ..ui.input_bindings import BindingProfile, InputAction
from ..ui.system_chrome import ChromePart, ChromeRequest
from ..ui.widgets import Button, Panel


_MIN_TARGET = 44
_OVERSCAN_ROWS = 1
_COMMON_ROUTE_IDS = frozenset({"common", "main"})
_SNAKE_CASE_TOKEN_RE = re.compile(
    r"(?<![A-Za-z0-9_])[A-Za-z0-9]+(?:_[A-Za-z0-9]+)+(?![A-Za-z0-9_])",
)
_DOTTED_SLUG_RE = re.compile(
    r"^[A-Za-z0-9_-]+(?:\.[A-Za-z0-9_-]+)+$",
)
_ASSET_EXTENSION_RE = re.compile(
    r"\.(?:bmp|flac|gif|jpeg|jpg|json|mp3|ogg|opus|png|svg|toml|wav|webm|"
    r"webp|yaml|yml)(?:\b|[?#])",
    re.IGNORECASE,
)


def _clean_single_line(value: object, *, limit: int = 512) -> str:
    return " ".join(str(value or "").split())[:limit]


def _ellipsize(text: str, font: pygame.font.Font, width: int) -> str:
    """Return one measured line that fits ``width`` (CJK included)."""
    value = _clean_single_line(text)
    if not value or width <= 0:
        return ""
    if font.size(value)[0] <= width:
        return value
    ellipsis = "…"
    if font.size(ellipsis)[0] > width:
        return ""
    low, high = 0, len(value)
    while low < high:
        middle = (low + high + 1) // 2
        if font.size(value[:middle] + ellipsis)[0] <= width:
            low = middle
        else:
            high = middle - 1
    return value[:low].rstrip() + ellipsis


@dataclass(frozen=True, slots=True)
class _ReplayRow:
    index: int
    scene_id: str
    title: str
    metadata: tuple[str, ...]
    top: int
    height: int

    @property
    def bottom(self) -> int:
        return self.top + self.height

    @property
    def metadata_text(self) -> str:
        return " · ".join(self.metadata)


def build_sandbox_context(
    ctx: SceneContext,
    *,
    replay_entry: ReplayArchiveEntry | None = None,
) -> SceneContext:
    """Return a read-only sandbox :class:`SceneContext` for replay.

    The returned context shares ``ctx``'s assets / fonts / theme /
    localization / npcs / config but carries a deep, isolated copy of the
    game state and a fresh dialogue engine bound to it. Mutating the sandbox
    state can never touch the live save — see the module docstring for why
    the JSON round-trip is the isolation boundary.
    """
    # Round-trip through JSON. model_dump strips the transient "__" meta
    # bridges (plugin manager, autosave config, ...), so the sandbox can
    # neither fire lifecycle hooks nor reach the live autosave machinery.
    sandbox_state = GameState(**ctx.state.model_dump(mode="json"))
    sandbox_state.time.bind_localization(ctx.localization)
    sandbox_state.affection.bind_localization(ctx.localization)
    if replay_entry is not None:
        authored_story = ctx.state.meta.get("__pack_story_catalog__")
        if not isinstance(authored_story, StoryGraph):
            authored_story = ctx.state.story
        authored_map = ctx.state.meta.get("__pack_map_catalog__")
        if authored_map is None:
            authored_map = ctx.state.map
        safe_read_log = rebind_authored_replay_visual_fx(
            ctx.state.read_log,
            authored_story,
        )
        safe_entry = safe_read_log.replay_archive.get(replay_entry.scene_id)
        if safe_entry is None:
            raise ValueError("replay trace is no longer available")
        sandbox_engine = PresentationTraceDialogueEngine(
            sandbox_state,
            safe_entry,
            authored_story=authored_story,
            authored_map=authored_map,
            authored_npcs=ctx.npcs,
        )
    else:
        # Lower-level callers may still request a general isolated state. The
        # player-facing replay scene never takes this fallback branch.
        llm_provider = getattr(
            ctx.dialogue,
            "llm_provider",
            getattr(ctx.brain, "llm_provider", None),
        )
        sandbox_engine = DialogueEngine(
            sandbox_state,
            llm_provider=llm_provider,
            speaker_id_resolver=getattr(
                ctx.dialogue,
                "speaker_id_resolver",
                None,
            ),
        )
    return dataclasses.replace(
        ctx, state=sandbox_state, dialogue=sandbox_engine,
    )


class SceneReplayScene(Scene):
    """Responsive, virtualized gallery of safely replayable scenes."""

    def __init__(self, ctx: SceneContext):
        super().__init__(ctx)
        self.is_overlay = True
        self.on_close: Callable[[], None] | None = None
        self._rows: list[_ReplayRow] = []
        self._row_tops: list[int] = []
        # Compatibility seam used by GameDriver and integrations. Only rows
        # intersecting the viewport are exposed; off-screen content can never
        # steal a pointer event from the header or footer.
        self._row_rects: list[tuple[pygame.Rect, str]] = []
        self._content_height = 0
        self._scroll_y = 0
        self._focus = -1
        self._focus_close = False
        self._hover = -1
        self._last_visible_range = (0, 0)
        self._last_drawn_rows = 0
        self._touch_dragging = False
        self._touch_moved = False
        self._touch_distance = 0
        self._touch_net_delta = 0
        self._stock_card_cache: dict[
            tuple[tuple[int, int], str], pygame.Surface
        ] = {}
        self._chip_surface: pygame.Surface | None = None
        self._invalid_trace_ids: set[str] = set()

        # The child DialogueScene playing the sandbox, plus its context. Both
        # are None when we're showing the list; set while replaying.
        self._replay_scene: DialogueScene | None = None
        self._replay_ctx: SceneContext | None = None
        self._replay_scene_id: str | None = None
        self._audio_snapshot: AudioPlaybackSnapshot | None = None
        self._sfx_ownership: SFXPlaybackOwnership | None = None
        # Flowchart replay is a one-shot detail view: completion / Back returns
        # to the chart. The normal replay gallery instead returns to its list.
        self._close_after_replay = False

    # ---- layout / lifecycle --------------------------------------------
    def _sp(self, value: int) -> int:
        return max(1, int(round(value * self._density)))

    def _font_size(self, base: int, minimum: int) -> int:
        # FontRegistry already applies the high-resolution canvas scale. Only
        # compact canvases need a lower requested size here.
        return max(minimum, int(round(base * min(1.0, self._density))))

    def _bindings(self) -> BindingProfile:
        return BindingProfile(
            getattr(self.ctx.config, "input_bindings", {}) or {},
        )

    @staticmethod
    def _pressed(inp, action: str) -> bool:
        pressed = getattr(inp, "pressed", None)
        return bool(callable(pressed) and pressed(action))

    def _configure_layout(self) -> None:
        sw, sh = (max(1, int(value)) for value in self.ctx.screen_size)
        self._density = max(0.65, sh / 720.0)
        self._minimum_target = max(_MIN_TARGET, self._sp(_MIN_TARGET))
        text_scale = max(
            1.0,
            min(1.4, float(getattr(self.ctx.config, "text_scale", 1.0))),
        )
        self._title_size = self._font_size(
            self.ctx.config.font_size_header,
            22,
        )
        self._row_title_size = max(
            15,
            int(round(self._font_size(19, 15) * text_scale)),
        )
        self._row_meta_size = max(
            11,
            int(round(self._font_size(13, 11) * text_scale)),
        )
        self._count_size = self._font_size(16, 12)
        self._close_size = self._font_size(15, 13)
        self._footer_size = self._font_size(13, 11)

        title_h = self.ctx.fonts.get(
            self._title_size,
            bold=True,
        ).get_linesize()
        close_label = (
            f"{self.ctx.localization.t('close', '關閉')}　"
            f"{self._bindings().primary_display(InputAction.BACK)}"
        )
        close_font = self.ctx.fonts.get(self._close_size, bold=True)
        close_h = max(
            self._minimum_target,
            close_font.get_linesize() + self._sp(14),
        )
        close_w = max(
            self._minimum_target,
            self._sp(120),
            close_font.size(close_label)[0] + self._sp(24),
        )

        margin_x = max(12, min(self._sp(80), sw // 12))
        margin_y = max(12, min(self._sp(40), sh // 14))
        self._panel_rect = pygame.Rect(
            margin_x,
            margin_y,
            max(1, sw - margin_x * 2),
            max(1, sh - margin_y * 2),
        )
        inner = max(12, self._sp(28))
        header_pad = max(8, self._sp(12))
        header_h = max(title_h, close_h) + header_pad * 2
        footer_h = max(
            self._minimum_target,
            self.ctx.fonts.get(self._footer_size).get_linesize()
            + self._sp(16),
        )
        self._header_rect = pygame.Rect(
            self._panel_rect.x + inner,
            self._panel_rect.y + self._sp(6),
            max(1, self._panel_rect.width - inner * 2),
            header_h,
        )
        close_w = min(close_w, self._header_rect.width)
        self._close_rect = pygame.Rect(
            self._header_rect.right - close_w,
            self._header_rect.y
            + (self._header_rect.height - close_h) // 2,
            close_w,
            close_h,
        )
        self._title_rect = pygame.Rect(
            self._header_rect.x,
            self._header_rect.y,
            max(
                1,
                self._close_rect.x
                - self._header_rect.x
                - self._sp(12),
            ),
            self._header_rect.height,
        )
        self._footer_rect = pygame.Rect(
            self._panel_rect.x + inner,
            self._panel_rect.bottom - self._sp(6) - footer_h,
            max(1, self._panel_rect.width - inner * 2),
            footer_h,
        )
        viewport_top = self._header_rect.bottom + self._sp(4)
        viewport_bottom = self._footer_rect.top - self._sp(4)
        self._viewport = pygame.Rect(
            self._panel_rect.x + inner,
            viewport_top,
            max(1, self._panel_rect.width - inner * 2),
            max(1, viewport_bottom - viewport_top),
        )

        chrome = getattr(self.ctx, "ui_chrome", None)
        self._panel = Panel(
            self._panel_rect,
            self.ctx.theme,
            fill=(*self.ctx.theme.bg_overlay[:3], 244),
            border=self.ctx.theme.border_strong,
            radius=self.ctx.theme.radius_l,
            border_width=max(1, self._sp(2)),
            chrome=chrome,
            screen="scene_replay",
            role="main",
            density=self._density,
        )
        self.close_btn = Button(
            self._close_rect,
            close_label,
            fonts=self.ctx.fonts,
            theme=self.ctx.theme,
            font_size=self._close_size,
            style="ghost",
            on_click=self._request_close,
            chrome=chrome,
            screen="scene_replay",
            role="close",
            motion=self.ctx.motion,
        )

        self._row_pad_x = max(10, self._sp(16))
        self._row_pad_y = max(8, self._sp(10))
        self._row_gap = max(6, self._sp(8))
        self._text_gap = max(2, self._sp(4))
        self._chip_gap = max(8, self._sp(14))
        self._chip_size = max(34, self._sp(40))
        self._row_title_line_h = self.ctx.fonts.get(
            self._row_title_size,
            bold=True,
        ).get_linesize()
        self._row_meta_line_h = self.ctx.fonts.get(
            self._row_meta_size,
        ).get_linesize()
        self._row_height = max(
            self._minimum_target,
            self._chip_size + self._row_pad_y * 2,
            self._row_pad_y * 2
            + self._row_title_line_h
            + self._text_gap
            + self._row_meta_line_h,
        )
        self._scroll_step = max(
            self._row_height + self._row_gap,
            self._row_height * 3,
        )
        self._stock_card_cache.clear()
        self._build_chip()

    def _build_chip(self) -> None:
        chip = pygame.Surface(
            (self._chip_size, self._chip_size),
            pygame.SRCALPHA,
        )
        pygame.draw.rect(
            chip,
            (*self.ctx.theme.accent[:3], 70),
            chip.get_rect(),
            border_radius=max(2, self._sp(5)),
        )
        pygame.draw.rect(
            chip,
            (*self.ctx.theme.accent[:3], 220),
            chip.get_rect(),
            width=max(2, self._sp(2)),
            border_radius=max(2, self._sp(5)),
        )
        glyph_size = self._font_size(20, 16)
        glyph = self.ctx.fonts.render(
            "回",
            glyph_size,
            self.ctx.theme.accent,
            bold=True,
        )
        chip.blit(
            glyph,
            (
                (chip.get_width() - glyph.get_width()) // 2,
                (chip.get_height() - glyph.get_height()) // 2,
            ),
        )
        self._chip_surface = chip

    def enter(
        self,
        *,
        on_close=None,
        initial_scene_id: str | None = None,
        close_after_replay: bool = False,
        **_,
    ) -> None:
        self.on_close = on_close
        self._close_after_replay = bool(close_after_replay)
        self._configure_layout()
        self._build_rows()
        if self._rows:
            self._focus = 0
            self._focus_close = False
        else:
            self._focus = -1
            self._focus_close = True
        self._scroll_y = 0
        self._sync_focus_state()
        if initial_scene_id is not None:
            self._start_replay(initial_scene_id)

    def resume(self) -> None:
        """Reflow live binding/text-scale changes without losing identity."""
        focused_id = (
            self._rows[self._focus].scene_id
            if 0 <= self._focus < len(self._rows)
            else None
        )
        focus_close = self._focus_close
        self._configure_layout()
        self._build_rows()
        self._focus = next(
            (
                row.index
                for row in self._rows
                if row.scene_id == focused_id
            ),
            0 if self._rows else -1,
        )
        self._focus_close = focus_close or not self._rows
        self._ensure_focus_visible()
        self._sync_focus_state()

    def exit(self) -> None:
        # Tear down any in-flight replay so its sandbox can be collected.
        self._end_replay()

    def _request_close(self) -> None:
        if self.on_close is not None:
            self.on_close()

    # ---- replayable-scene model / virtualization -----------------------
    def _authoritative_map(self) -> MapSystem:
        authored = self.ctx.state.meta.get("__pack_map_catalog__")
        if isinstance(authored, MapSystem):
            return authored
        return self.ctx.state.map

    def _chapter_for_scene(self, scene_id: str) -> ChapterSpec | None:
        manifest = self.ctx.state.meta.get("__chapters__")
        if not isinstance(manifest, ChapterManifest):
            return None
        for chapter in manifest.ordered():
            if scene_id == chapter.entry_scene or scene_id in chapter.scenes:
                return chapter
        return None

    def _internal_identifiers(self) -> frozenset[str]:
        """Return current pack identities that must never become UI copy."""
        identifiers: set[str] = set()

        def add(value: object) -> None:
            if isinstance(value, str) and value.strip():
                identifiers.add(value.strip().casefold())

        for scene_id, scene in self.ctx.state.story.scenes.items():
            add(scene_id)
            add(scene.location)
            add(scene.route)
            for line in scene.lines:
                add(line.loc_id)

        authored_map = self._authoritative_map()
        for location_id in authored_map.locations:
            add(location_id)

        manifest = self.ctx.state.meta.get("__chapters__")
        if isinstance(manifest, ChapterManifest):
            for chapter in manifest.chapters:
                add(chapter.id)
                add(chapter.route)
                add(chapter.act)
                add(chapter.entry_scene)
                for scene_id in chapter.scenes:
                    add(scene_id)
                for ending_id in chapter.endings:
                    add(ending_id)

        try:
            npcs = self.ctx.npcs.all()
        except Exception:
            npcs = []
        for npc in npcs:
            add(npc.id)
            add(npc.route_id)
            add(npc.affiliated_location)

        return frozenset(identifiers)

    def _safe_player_label(
        self,
        value: object,
        *,
        internal_ids: frozenset[str],
    ) -> str:
        """Accept human copy only; reject IDs, slugs, URIs, and asset paths."""
        text = _clean_single_line(value)
        if not text or text.casefold() in internal_ids:
            return ""
        if (
            _SNAKE_CASE_TOKEN_RE.search(text)
            or _DOTTED_SLUG_RE.fullmatch(text)
            or _ASSET_EXTENSION_RE.search(text)
            or "/" in text
            or "\\" in text
            or "://" in text
        ):
            return ""
        try:
            text = self.ctx.presentation_text.apply(text)
        except Exception:
            return ""
        return _clean_single_line(text)

    def _scene_title(self, scene) -> str:
        if not scene.title:
            return ""
        try:
            return self.ctx.localization.story(
                f"{scene.id}.title",
                scene.title,
                scene_id=scene.id,
            )
        except Exception:
            return scene.title

    def _untitled_label(
        self,
        ordinal: int,
        *,
        internal_ids: frozenset[str],
    ) -> str:
        raw = self.ctx.localization.t(
            "scene_replay_untitled",
            "未命名回憶 {index:02d}",
            index=ordinal,
        )
        label = self._safe_player_label(raw, internal_ids=internal_ids)
        return label or f"未命名回憶 {ordinal:02d}"

    def _route_label(
        self,
        route_id: str | None,
        *,
        internal_ids: frozenset[str],
    ) -> str:
        route = (route_id or "").strip()
        if not route:
            return ""
        if route.casefold() in _COMMON_ROUTE_IDS:
            raw = self.ctx.localization.t(
                "scene_replay_common_route",
                "共通線",
            )
            return self._safe_player_label(raw, internal_ids=internal_ids)
        try:
            npcs = self.ctx.npcs.all()
        except Exception:
            npcs = []
        for npc in npcs:
            if npc.route_id == route:
                return self._safe_player_label(
                    npc.name,
                    internal_ids=internal_ids,
                )
        return ""

    def _row_metadata(
        self,
        scene,
        chapter: ChapterSpec | None,
        *,
        internal_ids: frozenset[str],
    ) -> tuple[str, ...]:
        labels: list[str] = []

        def append(value: object) -> None:
            label = self._safe_player_label(
                value,
                internal_ids=internal_ids,
            )
            if label and label not in labels:
                labels.append(label)

        if chapter is not None:
            append(chapter.title)

        location_id = getattr(scene, "location", None)
        location = (
            self._authoritative_map().get(location_id)
            if isinstance(location_id, str) and location_id
            else None
        )
        if location is not None:
            append(location.name)

        route_id = getattr(scene, "route", None)
        if not route_id and chapter is not None:
            route_id = chapter.route
        route_label = self._route_label(
            route_id,
            internal_ids=internal_ids,
        )
        if route_label and route_label not in labels:
            labels.append(route_label)
        return tuple(labels)

    def _replayable(self) -> list[tuple[str, str, tuple[str, ...]]]:
        """Completed known scenes in authoritative story insertion order."""
        read_log = self.ctx.state.read_log
        replayable = read_log.replayable_scene_ids()
        rows: list[tuple[str, str, tuple[str, ...]]] = []
        internal_ids = self._internal_identifiers()
        for ordinal, (scene_id, scene) in enumerate(
            self.ctx.state.story.scenes.items(),
            start=1,
        ):
            if scene_id not in replayable or scene_id in self._invalid_trace_ids:
                continue
            entry = read_log.replay_entry(scene_id)
            if entry is None:
                continue
            title = ""
            for candidate in (entry.title, self._scene_title(scene)):
                title = self._safe_player_label(
                    candidate,
                    internal_ids=internal_ids,
                )
                if title:
                    break
            if not title:
                title = self._untitled_label(
                    ordinal,
                    internal_ids=internal_ids,
                )
            chapter = self._chapter_for_scene(scene_id)
            rows.append((
                scene_id,
                title,
                self._row_metadata(
                    scene,
                    chapter,
                    internal_ids=internal_ids,
                ),
            ))
        return rows

    def _build_rows(self) -> None:
        rows: list[_ReplayRow] = []
        tops: list[int] = []
        top = 0
        for index, (scene_id, title, metadata) in enumerate(self._replayable()):
            tops.append(top)
            rows.append(
                _ReplayRow(
                    index=index,
                    scene_id=scene_id,
                    title=title,
                    metadata=metadata,
                    top=top,
                    height=self._row_height,
                ),
            )
            top += self._row_height + self._row_gap
        self._rows = rows
        self._row_tops = tops
        self._content_height = max(
            0,
            top - (self._row_gap if rows else 0),
        )
        self._clamp_scroll()

    def _max_scroll(self) -> int:
        return max(0, self._content_height - self._viewport.height)

    def _clamp_scroll(self) -> None:
        self._scroll_y = max(0, min(int(self._scroll_y), self._max_scroll()))

    def _visible_range(self) -> tuple[int, int]:
        if not self._rows:
            return (0, 0)
        first = max(0, bisect_right(self._row_tops, self._scroll_y) - 1)
        while (
            first < len(self._rows)
            and self._rows[first].bottom <= self._scroll_y
        ):
            first += 1
        first = max(0, first - _OVERSCAN_ROWS)
        limit = self._scroll_y + self._viewport.height
        last = first
        while last < len(self._rows) and self._rows[last].top < limit:
            last += 1
        return first, min(len(self._rows), last + _OVERSCAN_ROWS)

    def _row_screen_rect(self, row: _ReplayRow) -> pygame.Rect:
        scrollbar_space = max(8, self._sp(12))
        return pygame.Rect(
            self._viewport.x,
            self._viewport.y + row.top - self._scroll_y,
            max(1, self._viewport.width - scrollbar_space),
            row.height,
        )

    def _index_at_content_y(self, content_y: int) -> int:
        if not self._rows or content_y < 0:
            return -1
        index = bisect_right(self._row_tops, int(content_y)) - 1
        if 0 <= index < len(self._rows):
            row = self._rows[index]
            if row.top <= content_y < row.bottom:
                return index
        return -1

    def _index_at_pointer(self, point: tuple[int, int]) -> int:
        if not self._viewport.collidepoint(point):
            return -1
        row_width = self._row_screen_rect(
            self._rows[0],
        ).width if self._rows else 0
        if point[0] >= self._viewport.x + row_width:
            return -1
        index = self._index_at_content_y(
            point[1] - self._viewport.y + self._scroll_y,
        )
        if index < 0:
            return -1
        # A clipped sliver at a viewport edge is visual context, not a tiny
        # accidental tap target. Keyboard focus will auto-scroll it fully into
        # view; pointer/touch activation waits until at least the minimum
        # target height is visible.
        visible = self._row_screen_rect(self._rows[index]).clip(self._viewport)
        if (
            visible.width < _MIN_TARGET
            or visible.height < self._minimum_target
        ):
            return -1
        return index

    # ---- focus / scrolling ---------------------------------------------
    def _sync_focus_state(self) -> None:
        self.close_btn.focused = self._focus_close

    def _set_close_focus(self) -> None:
        self._focus_close = True
        self._sync_focus_state()

    def _set_row_focus(self, index: int, *, ensure: bool = True) -> bool:
        if not 0 <= index < len(self._rows):
            return False
        self._focus = index
        self._focus_close = False
        if ensure:
            self._ensure_focus_visible()
        self._sync_focus_state()
        return True

    def _cycle_focus(self, step: int) -> None:
        if not self._rows:
            self._set_close_focus()
            return
        targets: list[int | None] = [*range(len(self._rows)), None]
        current: int | None = None if self._focus_close else self._focus
        try:
            position = targets.index(current)
        except ValueError:
            position = len(targets) - 1
        target = targets[(position + step) % len(targets)]
        if target is None:
            self._set_close_focus()
        else:
            self._set_row_focus(target)

    def _move_focus(self, delta: int) -> None:
        if not self._rows:
            self._set_close_focus()
            return
        if self._focus_close or not 0 <= self._focus < len(self._rows):
            self._set_row_focus(0 if delta > 0 else len(self._rows) - 1)
            return
        target = self._focus + delta
        if target < 0:
            self._set_close_focus()
        else:
            self._set_row_focus(min(len(self._rows) - 1, target))

    def _ensure_focus_visible(self) -> None:
        if self._focus_close or not 0 <= self._focus < len(self._rows):
            self._clamp_scroll()
            return
        row = self._rows[self._focus]
        pad = max(2, self._sp(6))
        if row.top - pad < self._scroll_y:
            self._scroll_y = row.top - pad
        elif row.bottom + pad > self._scroll_y + self._viewport.height:
            self._scroll_y = row.bottom + pad - self._viewport.height
        self._clamp_scroll()

    def _focus_near_viewport(self, *, bottom: bool) -> None:
        if not self._rows:
            return
        content_y = self._scroll_y + (
            self._viewport.height - 1 if bottom else 0
        )
        index = self._index_at_content_y(content_y)
        if index < 0:
            index = min(
                len(self._rows) - 1,
                max(0, bisect_right(self._row_tops, content_y) - 1),
            )
        self._set_row_focus(index)

    def _page(self, direction: int) -> None:
        amount = max(
            self._minimum_target,
            int(self._viewport.height * 0.86),
        )
        self._scroll_y += direction * amount
        self._clamp_scroll()
        self._focus_near_viewport(bottom=direction > 0)

    # ---- replay ---------------------------------------------------------
    def _end_replay(self) -> bool:
        """Discard the sandbox and restore live presentation ownership."""
        was_replaying = self._replay_scene is not None
        replay_scene = self._replay_scene
        audio_snapshot = self._audio_snapshot
        sfx_ownership = self._sfx_ownership
        self._replay_scene = None
        self._replay_ctx = None
        self._replay_scene_id = None
        self._audio_snapshot = None
        self._sfx_ownership = None
        try:
            if replay_scene is not None:
                # The owned child must not call back into an overlay that is
                # being torn down by a SceneManager pop.
                replay_scene.on_done = None
                replay_scene.exit()
        finally:
            try:
                if sfx_ownership is not None:
                    self.ctx.assets.end_sfx_ownership(sfx_ownership)
            finally:
                if audio_snapshot is not None:
                    self.ctx.assets.restore_audio_playback(audio_snapshot)
        return was_replaying

    def _finish_replay(self) -> None:
        """Finish/cancel the child and optionally return to its caller."""
        if not self._end_replay():
            return
        if self._close_after_replay and self.on_close is not None:
            self.on_close()

    def _start_replay(self, scene_id: str) -> bool:
        """Play ``scene_id`` against a sandbox state, in an owned child scene.

        Nothing on the live ``ctx.state`` changes: the child DialogueScene
        runs on an isolated sandbox context (see the module docstring). When
        the scene ends it calls our ``on_done`` callback, which clears the
        replay and discards the sandbox.
        """
        entry = self.ctx.state.read_log.replay_entry(scene_id)
        if self.ctx.state.story.get(scene_id) is None or entry is None:
            return False  # fail closed on stale, legacy, or incomplete traces
        # A second start can only come from programmatic callers. End the old
        # child first so snapshots cannot nest and restore out of order.
        self._end_replay()
        self._audio_snapshot = self.ctx.assets.capture_audio_playback()
        try:
            self._sfx_ownership = self.ctx.assets.begin_sfx_ownership()
            sandbox_ctx = build_sandbox_context(
                self.ctx,
                replay_entry=entry,
            )
            scene = DialogueScene(sandbox_ctx)
            self._replay_ctx = sandbox_ctx
            self._replay_scene = scene
            self._replay_scene_id = scene_id
            # DialogueScene.enter(scene_id=..., on_done=...) starts the scene
            # and invokes on_done when it ends. We drive the owned child from
            # this overlay without exposing it to the live SceneManager.
            scene.enter(scene_id=scene_id, on_done=self._finish_replay)
        except Exception:
            self._end_replay()
            self._invalid_trace_ids.add(scene_id)
            self._build_rows()
            if self._rows:
                self._focus = min(max(0, self._focus), len(self._rows) - 1)
                self._focus_close = False
            else:
                self._focus = -1
                self._focus_close = True
            self._sync_focus_state()
            return False
        return True

    def _activate_index(self, index: int) -> bool:
        if not self._set_row_focus(index):
            return False
        return self._start_replay(self._rows[index].scene_id)

    # ---- input ----------------------------------------------------------
    @staticmethod
    def _pointer_moved(inp) -> bool:
        return any(
            event.type == pygame.MOUSEMOTION
            for event in getattr(inp, "events", ())
        )

    def update(self, dt: float, inp) -> None:
        # Back belongs to the replay owner, not to live dialogue navigation.
        if self._replay_scene is not None:
            if getattr(inp, "cancel", False) or self._pressed(
                inp,
                InputAction.BACK,
            ):
                self._finish_replay()
                return
            self._replay_scene.update(dt, inp)
            return

        if getattr(inp, "cancel", False) or self._pressed(
            inp,
            InputAction.BACK,
        ):
            self._request_close()
            return

        pointer_click = bool(getattr(inp, "mouse_clicked", False))
        close_clicked = (
            pointer_click
            and self._close_rect.collidepoint(inp.mouse_pos)
        )
        # Button owns its pointer activation and click sound. Consume the
        # event before any row logic so Close can never also start a replay.
        self.close_btn.update(dt, inp)
        if close_clicked:
            return

        self._hover = self._index_at_pointer(inp.mouse_pos)
        if self._pointer_moved(inp):
            if self._close_rect.collidepoint(inp.mouse_pos):
                self._set_close_focus()
            elif self._hover >= 0:
                self._set_row_focus(self._hover, ensure=False)

        if self._pressed(inp, InputAction.FOCUS_NEXT):
            self._cycle_focus(1)
        elif self._pressed(inp, InputAction.FOCUS_PREVIOUS):
            self._cycle_focus(-1)
        elif self._pressed(inp, InputAction.NAV_UP):
            self._move_focus(-1)
        elif self._pressed(inp, InputAction.NAV_DOWN):
            self._move_focus(1)
        elif self._pressed(inp, InputAction.PAGE_UP) or self._pressed(
            inp,
            InputAction.NAV_LEFT,
        ):
            self._page(-1)
        elif self._pressed(inp, InputAction.PAGE_DOWN) or self._pressed(
            inp,
            InputAction.NAV_RIGHT,
        ):
            self._page(1)

        if self._pressed(inp, InputAction.CONFIRM_ADVANCE) or getattr(
            inp,
            "confirm",
            False,
        ):
            if self._focus_close:
                self.close_btn.activate()
                return
            if self._activate_index(self._focus):
                return

        wheel = int(getattr(inp, "mouse_wheel", 0) or 0)
        if wheel and self._viewport.collidepoint(inp.mouse_pos):
            self._scroll_y -= wheel * self._scroll_step
            self._clamp_scroll()
            self._focus_near_viewport(bottom=wheel < 0)

        if getattr(inp, "touch_started", False):
            self._touch_dragging = self._viewport.collidepoint(inp.mouse_pos)
            self._touch_moved = False
            self._touch_distance = 0
            self._touch_net_delta = 0
        if self._touch_dragging:
            delta_y = int(getattr(inp, "touch_delta", (0, 0))[1])
            if delta_y:
                self._scroll_y -= delta_y
                self._touch_distance += abs(delta_y)
                self._touch_net_delta += delta_y
                self._touch_moved = self._touch_distance > self._sp(10)
                self._clamp_scroll()

        touch_released = bool(getattr(inp, "touch_released", False))
        if touch_released:
            captured = self._touch_dragging
            should_tap = (
                (captured and not self._touch_moved)
                or (
                    not captured
                    and bool(getattr(inp, "touch_mode", False))
                    and pointer_click
                )
            )
            self._touch_dragging = False
            if should_tap:
                index = self._index_at_pointer(inp.mouse_pos)
                if index >= 0 and self._activate_index(index):
                    return
            elif captured and self._rows:
                self._focus_near_viewport(bottom=self._touch_net_delta < 0)

        # Runtime touch taps also surface as mouse_clicked. The release branch
        # above consumes that duplicate while direct pointer clicks land here.
        if pointer_click and not touch_released:
            index = self._index_at_pointer(inp.mouse_pos)
            if index >= 0:
                self._activate_index(index)

    # ---- drawing --------------------------------------------------------
    def _paint_chrome(
        self,
        surface: pygame.Surface,
        rect: pygame.Rect,
        *,
        part: ChromePart,
        variant: str,
        hovered: bool = False,
        focused: bool = False,
        selected: bool = False,
        emphasis: int = 0,
    ) -> bool:
        chrome = getattr(self.ctx, "ui_chrome", None)
        if chrome is None or rect.width <= 0 or rect.height <= 0:
            return False
        return chrome.paint(
            surface,
            rect,
            ChromeRequest(
                part=part,
                screen="scene_replay",
                variant=variant,
                size=rect.size,
                density=self._density,
                hovered=hovered,
                focused=focused,
                selected=selected,
                emphasis=max(0, min(3, int(emphasis))),
            ),
        )

    def _stock_card(self, size: tuple[int, int], state: str) -> pygame.Surface:
        key = (size, state)
        cached = self._stock_card_cache.get(key)
        if cached is not None:
            return cached
        card = pygame.Surface(size, pygame.SRCALPHA)
        alpha = {"normal": 50, "hover": 76, "focus": 96}[state]
        pygame.draw.rect(
            card,
            (*self.ctx.theme.accent[:3], alpha),
            card.get_rect(),
            border_radius=max(2, self._sp(8)),
        )
        pygame.draw.rect(
            card,
            self.ctx.theme.border,
            card.get_rect(),
            width=max(1, self._sp(1)),
            border_radius=max(2, self._sp(8)),
        )
        self._stock_card_cache[key] = card
        return card

    def _draw_row(self, surface: pygame.Surface, row: _ReplayRow) -> None:
        rect = self._row_screen_rect(row)
        visible = rect.clip(self._viewport)
        if visible.width <= 0 or visible.height <= 0:
            return
        focused = not self._focus_close and row.index == self._focus
        hovered = row.index == self._hover
        emphasis = 3 if focused else 2 if hovered else 0
        if not self._paint_chrome(
            surface,
            rect,
            part="card",
            variant="entry",
            hovered=hovered,
            focused=focused,
            selected=focused,
            emphasis=emphasis,
        ):
            state = "focus" if focused else "hover" if hovered else "normal"
            surface.blit(self._stock_card(rect.size, state), rect.topleft)

        if hovered and not focused:
            pygame.draw.rect(
                surface,
                self.ctx.theme.border_strong,
                rect,
                width=max(2, self._sp(2)),
                border_radius=max(2, self._sp(8)),
            )
        if focused:
            self._paint_chrome(
                surface,
                rect,
                part="focus",
                variant="entry",
                hovered=hovered,
                focused=True,
                selected=True,
                emphasis=3,
            )
            # Accessibility cannot depend on a pack renderer or animation.
            pygame.draw.rect(
                surface,
                self.ctx.theme.accent_warm,
                rect,
                width=max(3, self._sp(3)),
                border_radius=max(2, self._sp(8)),
            )

        chip_x = rect.x + self._row_pad_x
        chip_y = rect.centery - self._chip_size // 2
        if self._chip_surface is not None:
            surface.blit(self._chip_surface, (chip_x, chip_y))
        text_x = chip_x + self._chip_size + self._chip_gap
        text_right = rect.right - self._row_pad_x
        available = max(1, text_right - text_x)
        title_font = self.ctx.fonts.get(self._row_title_size, bold=True)
        title = _ellipsize(row.title, title_font, available)
        title_y = rect.y + self._row_pad_y
        if title:
            surface.blit(
                self.ctx.fonts.render(
                    title,
                    self._row_title_size,
                    self.ctx.theme.text,
                    bold=True,
                ),
                (text_x, title_y),
            )
        meta_font = self.ctx.fonts.get(self._row_meta_size)
        metadata = _ellipsize(row.metadata_text, meta_font, available)
        meta_y = title_y + self._row_title_line_h + self._text_gap
        if metadata:
            surface.blit(
                self.ctx.fonts.render(
                    metadata,
                    self._row_meta_size,
                    self.ctx.theme.text_mute,
                ),
                (text_x, meta_y),
            )

        if (
            visible.width >= _MIN_TARGET
            and visible.height >= self._minimum_target
        ):
            self._row_rects.append((visible, row.scene_id))

    def _draw_scrollbar(self, surface: pygame.Surface) -> None:
        if self._content_height <= self._viewport.height:
            return
        track_w = max(4, self._sp(4))
        track = pygame.Rect(
            self._viewport.right - track_w,
            self._viewport.y,
            track_w,
            self._viewport.height,
        )
        pygame.draw.rect(
            surface,
            (*self.ctx.theme.border_soft[:3], 100),
            track,
            border_radius=max(1, track_w // 2),
        )
        knob_h = min(
            track.height,
            max(
                self._sp(28),
                int(
                    track.height
                    * self._viewport.height
                    / self._content_height
                ),
            ),
        )
        travel = max(0, track.height - knob_h)
        knob_y = (
            int(self._scroll_y / self._max_scroll() * travel)
            if self._max_scroll() > 0
            else 0
        )
        pygame.draw.rect(
            surface,
            self.ctx.theme.accent,
            (track.x, track.y + knob_y, track.width, knob_h),
            border_radius=max(1, track_w // 2),
        )

    def _draw_footer(self, surface: pygame.Surface) -> None:
        if not self._paint_chrome(
            surface,
            self._footer_rect,
            part="footer",
            variant="navigation",
        ):
            pygame.draw.line(
                surface,
                self.ctx.theme.border_soft,
                self._footer_rect.topleft,
                self._footer_rect.topright,
                width=max(1, self._sp(1)),
            )
        bindings = self._bindings()
        status = "　".join([
            f"{bindings.primary_display(InputAction.NAV_UP)}／"
            f"{bindings.primary_display(InputAction.NAV_DOWN)} 選擇",
            f"{bindings.primary_display(InputAction.PAGE_UP)}／"
            f"{bindings.primary_display(InputAction.PAGE_DOWN)} 翻頁",
            f"{bindings.primary_display(InputAction.CONFIRM_ADVANCE)} 重溫",
            f"{bindings.primary_display(InputAction.BACK)} 關閉",
        ])
        font = self.ctx.fonts.get(self._footer_size)
        status = _ellipsize(
            status,
            font,
            max(1, self._footer_rect.width - self._sp(12)),
        )
        hint = self.ctx.fonts.render(
            status,
            self._footer_size,
            self.ctx.theme.text_mute,
        )
        surface.blit(
            hint,
            (
                self._footer_rect.x + self._sp(6),
                self._footer_rect.centery - hint.get_height() // 2,
            ),
        )

    def draw(self, surface: pygame.Surface) -> None:
        if self._replay_scene is not None:
            # The child scene paints the full frame (bg/CG/portraits/box).
            self._replay_scene.draw(surface)
            return

        full = surface.get_rect()
        if not self._paint_chrome(
            surface,
            full,
            part="backdrop",
            variant="overlay",
        ):
            veil = pygame.Surface(surface.get_size(), pygame.SRCALPHA)
            veil.fill((0, 0, 0, 176))
            surface.blit(veil, (0, 0))
        self._panel.draw(surface)

        title = self.ctx.fonts.render(
            self.ctx.localization.t("scene_replay", "場景重溫"),
            self._title_size,
            self.ctx.theme.accent,
            bold=True,
        )
        title_y = self._title_rect.centery - title.get_height() // 2
        surface.blit(title, (self._title_rect.x, title_y))
        count = self.ctx.fonts.render(
            str(len(self._rows)),
            self._count_size,
            self.ctx.theme.accent_warm,
        )
        count_x = title.get_width() + self._title_rect.x + self._sp(12)
        if count_x + count.get_width() <= self._title_rect.right:
            surface.blit(
                count,
                (
                    count_x,
                    self._title_rect.centery - count.get_height() // 2,
                ),
            )
        self.close_btn.draw(surface)

        divider = pygame.Rect(
            self._viewport.x,
            self._viewport.y - max(1, self._sp(3)),
            self._viewport.width,
            max(1, self._sp(2)),
        )
        if not self._paint_chrome(
            surface,
            divider,
            part="divider",
            variant="header",
        ):
            pygame.draw.line(
                surface,
                self.ctx.theme.border_soft,
                divider.topleft,
                divider.topright,
                width=max(1, self._sp(1)),
            )

        self._row_rects = []
        old_clip = surface.get_clip()
        surface.set_clip(self._viewport.clip(old_clip))
        first, last = self._visible_range()
        self._last_visible_range = (first, last)
        self._last_drawn_rows = 0
        for row in self._rows[first:last]:
            self._draw_row(surface, row)
            self._last_drawn_rows += 1
        if not self._rows:
            font = self.ctx.fonts.get(self._row_title_size)
            message = _ellipsize(
                self.ctx.localization.t(
                    "scene_replay_empty",
                    "尚無可回想的場景",
                ),
                font,
                max(1, self._viewport.width - self._sp(20)),
            )
            empty = self.ctx.fonts.render(
                message,
                self._row_title_size,
                self.ctx.theme.text_mute,
            )
            surface.blit(
                empty,
                (
                    self._viewport.centerx - empty.get_width() // 2,
                    self._viewport.centery - empty.get_height() // 2,
                ),
            )
        surface.set_clip(old_clip)
        self._draw_scrollbar(surface)
        self._draw_footer(surface)

    def describe(self) -> dict:
        first, last = self._visible_range()
        visible_indices = [
            row.index
            for row in self._rows[first:last]
            if self._row_screen_rect(row).colliderect(self._viewport)
        ]
        visible_scene_ids = [
            self._rows[index].scene_id
            for index in visible_indices
        ]
        if self._focus_close:
            focus = {"target": "close"}
        elif 0 <= self._focus < len(self._rows):
            focus = {
                "target": "scene",
                "index": self._focus,
                "scene_id": self._rows[self._focus].scene_id,
                "visible": self._focus in visible_indices,
            }
        else:
            focus = {"target": "none"}
        player_text: list[str] = []
        metadata: list[dict[str, object]] = []
        for row in self._rows:
            player_text.append(row.title)
            if row.metadata_text:
                player_text.append(row.metadata_text)
            metadata.append({
                "index": row.index,
                "scene_id": row.scene_id,
                "labels": list(row.metadata),
                "text": row.metadata_text,
            })
        return {
            "scene": "SceneReplayScene",
            "replayable": [row.scene_id for row in self._rows],
            # Player copy and internal replay identity deliberately travel in
            # separate fields.  Visual audits can inspect ``player_text``
            # without accidentally treating a debug scene id as rendered UI.
            "player_text": player_text,
            "metadata": metadata,
            "total": len(self._rows),
            "replaying": self._replay_scene is not None,
            "replay_scene_id": self._replay_scene_id,
            "focus": focus,
            "focused_index": (
                None if self._focus_close else self._focus
            ),
            "visible_range": [first, last],
            "visible_scene_ids": visible_scene_ids,
            "drawn_rows": self._last_drawn_rows,
            "scroll": {
                "y": self._scroll_y,
                "limit": self._max_scroll(),
            },
            "viewport": {
                "x": self._viewport.x,
                "y": self._viewport.y,
                "width": self._viewport.width,
                "height": self._viewport.height,
            },
            "content_height": self._content_height,
        }

"""LLM free-chat overlay with an NPC.

Sends each player message through the configured Brain and displays the
NPC's reply. Player messages and replies both feed back into the game
state (event log, affection nudges, NPC memory).
"""
from __future__ import annotations

from typing import Callable
from dataclasses import dataclass

import threading
import pygame

from .base import Scene, SceneContext
from ..ui.widgets import Button, Panel, TextInput, ScrollArea


@dataclass
class _Msg:
    who: str    # "player" | "npc" | "system"
    speaker: str
    text: str
    affection_label: str | None = None
    affection: int | None = None
    pending: bool = False


class ChatScene(Scene):
    def __init__(self, ctx: SceneContext):
        super().__init__(ctx)
        self.is_overlay = True
        self.npc_id: str | None = None
        self.messages: list[_Msg] = []
        self.input: TextInput | None = None
        self.send_btn: Button | None = None
        self.gift_btn: Button | None = None
        self.shop_btn: Button | None = None
        self.close_btn: Button | None = None
        self.on_close: Callable[[], None] | None = None
        self.on_request_gift: Callable | None = None
        self.on_open_shop: Callable[[str], None] | None = None

    def enter(self, *, npc_id: str, on_close=None,
              on_request_gift=None, on_open_shop=None, **_) -> None:
        self.npc_id = npc_id
        self.on_close = on_close
        self.on_request_gift = on_request_gift
        self.messages = []
        sw, sh = self.ctx.screen_size
        self._panel_rect = pygame.Rect(160, 60, sw - 320, sh - 120)
        self._panel = Panel(self._panel_rect, self.ctx.theme,
                            fill=(*self.ctx.theme.bg_overlay[:3], 240),
                            border=self.ctx.theme.border_strong,
                            radius=self.ctx.theme.radius_l, border_width=2)
        self.input = TextInput(
            pygame.Rect(self._panel_rect.x + 30,
                        self._panel_rect.bottom - 70,
                        self._panel_rect.width - 160, 48),
            fonts=self.ctx.fonts, theme=self.ctx.theme,
            placeholder="想對她說什麼？(由 LLM 驅動，Enter 送出)",
            on_submit=lambda t: self._send(t),
        )
        self.input.focus()
        self.send_btn = Button(
            pygame.Rect(self._panel_rect.right - 130 - 16,
                        self._panel_rect.bottom - 70, 110, 48),
            "送出", fonts=self.ctx.fonts, theme=self.ctx.theme,
            on_click=lambda: self._send(self.input.text),
        )
        self.gift_btn = Button(
            pygame.Rect(self._panel_rect.right - 250 - 16,
                        self._panel_rect.y + 16, 130, 36),
            "送禮", fonts=self.ctx.fonts, theme=self.ctx.theme,
            font_size=15, style="primary",
            on_click=self._open_gift_picker,
        )
        self.close_btn = Button(
            pygame.Rect(self._panel_rect.right - 120 - 16,
                        self._panel_rect.y + 16, 120, 36),
            "結束對話", fonts=self.ctx.fonts, theme=self.ctx.theme,
            font_size=15, style="ghost",
            on_click=(lambda: on_close() if on_close else None),
        )
        self._scroll = ScrollArea(
            pygame.Rect(self._panel_rect.x + 30, self._panel_rect.y + 80,
                        self._panel_rect.width - 60,
                        self._panel_rect.height - 80 - 100),
            fonts=self.ctx.fonts, theme=self.ctx.theme,
        )
        self._scroll.set_drawer(self._draw_chat)

    def exit(self) -> None:
        if self.input:
            self.input.unfocus()

    def _open_gift_picker(self) -> None:
        if self.on_request_gift is None or self.npc_id is None:
            return
        self.on_request_gift(self.npc_id, self._after_gift_picked)

    def _after_gift_picked(self, item_id: str) -> None:
        """Called when the player has chosen an item to gift."""
        from ..core.story_graph import Effect
        if self.npc_id is None:
            return
        npc = self.ctx.npcs.get(self.npc_id)
        item = self.ctx.state.items.get(item_id)
        if npc is None or item is None:
            return
        # Apply the gift effect through the standard pipeline so
        # achievements + event log + inventory consumption fire as one.
        results = self.ctx.state.apply_all([
            Effect(kind="gift", target=self.npc_id, stat=item_id),
        ])
        # Pull the gift result back out so we can show a chat bubble.
        gift_r = next((r for r in results if r.get("kind") == "gift"), None)
        if gift_r is None or gift_r.get("error"):
            self.messages.append(_Msg(
                who="system", speaker="系統",
                text="這份禮物無法送出。",
            ))
            return
        delta = gift_r["delta"]
        flavor = ("（{n}收下了禮物，露出了滿意的微笑。）".format(n=npc.name)
                  if delta >= 5 else
                  "（{n}收下了禮物，但表情有點微妙。）".format(n=npc.name)
                  if delta >= 0 else
                  "（{n}皺起眉頭——看來不太喜歡這份禮物。）".format(n=npc.name))
        self.messages.append(_Msg(
            who="system", speaker="送禮",
            text=f"你把「{item.name}」送給了 {npc.name}。{flavor}",
            affection=gift_r["new"],
            affection_label=self.ctx.state.affection.level_label(self.npc_id),
        ))
        # Remember it on the NPC and in the event log (gift effect did
        # the event log already).
        npc.append_memory(
            f"玩家送了我「{item.name}」({'+' if delta >= 0 else ''}{delta})"
        )

    def _send(self, text: str) -> None:
        text = text.strip()
        if not text or self.npc_id is None:
            return
        npc = self.ctx.npcs.get(self.npc_id)
        if npc is None:
            return
        self.input.text = ""
        self.input._caret = 0
        self.messages.append(_Msg(who="player",
                                  speaker=self.ctx.state.player.name,
                                  text=text))
        placeholder = _Msg(who="npc", speaker=npc.name,
                           text="（思考中…）", pending=True)
        self.messages.append(placeholder)
        # auto-scroll to bottom on next frame
        self._scroll.scroll_y = 10**9

        def worker(target=placeholder):
            try:
                loc = self.ctx.state.map.current
                loc_label = loc.name if loc else "（不明）"
                recent = [f"[{e.kind}] {e.title}"
                          for e in self.ctx.state.events.recent(8)]
                system = npc.system_prompt(
                    player_name=self.ctx.state.player.name,
                    affection=self.ctx.state.affection.get(npc.id),
                    location=loc_label,
                    time_of_day=self.ctx.state.time.time_of_day.label,
                    recent_events=recent,
                )
                user = (
                    f"場景：自由對話\n地點：{loc_label}\n"
                    f"時刻：{self.ctx.state.time.time_of_day.label}\n"
                    f"玩家對你說：「{text}」\n"
                    f"請以 {npc.name} 的身份回覆，1~3 句中文對白。"
                )
                reply = self.ctx.brain.respond(npc=npc, system_prompt=system,
                                               user_context=user, history=None)
            except Exception as e:
                reply = f"（{npc.name} 沒有開口。）[brain-error: {e}]"
            new_val, unlocked = self.ctx.state.affection.adjust(npc.id, 1)
            npc.append_memory(
                f"玩家對我說：「{text}」，我回：「{reply}」"
            )
            self.ctx.state.events.record(
                kind="dialogue",
                title=f"{self.ctx.state.player.name}: {text[:30]}",
                location=(self.ctx.state.map.current_location_id),
                actors=[npc.id], data={"speaker": "player",
                                       "to": npc.id, "message": text},
            )
            self.ctx.state.events.record(
                kind="dialogue",
                title=f"{npc.name}: {reply[:30]}",
                location=(self.ctx.state.map.current_location_id),
                actors=[npc.id], data={"speaker": npc.id, "reply": reply},
            )
            target.text = reply
            target.affection = new_val
            target.affection_label = self.ctx.state.affection.level_label(npc.id)
            target.pending = False
            if unlocked:
                self.messages.append(_Msg(who="system",
                                          speaker="系統",
                                          text=f"解鎖：{ '、'.join(unlocked) }"))

        threading.Thread(target=worker, daemon=True).start()

    def _draw_chat(self, surface: pygame.Surface) -> int:
        y = 0
        pad = 12
        for m in self.messages:
            color_bg = {
                "player": (*self.ctx.theme.accent_alt[:3], 60),
                "npc": (*self.ctx.theme.accent[:3], 60),
                "system": (*self.ctx.theme.accent_warm[:3], 60),
            }.get(m.who, (255, 255, 255, 30))
            font_body = self.ctx.fonts.get(18)
            max_w = int(surface.get_width() * 0.7)
            # Crude wrap by char-width
            from ..ui.widgets.label import _wrap_lines
            lines = _wrap_lines(m.text, font_body, max_w - 24)
            text_h = font_body.get_linesize() * len(lines) + 16
            header_h = 22
            bubble_h = header_h + text_h
            bubble_w = max_w
            bx = surface.get_width() - bubble_w if m.who == "player" else 0
            bubble = pygame.Surface((bubble_w, bubble_h), pygame.SRCALPHA)
            pygame.draw.rect(bubble, color_bg, bubble.get_rect(),
                             border_radius=self.ctx.theme.radius_m)
            pygame.draw.rect(bubble, self.ctx.theme.border_soft,
                             bubble.get_rect(), width=1,
                             border_radius=self.ctx.theme.radius_m)
            header = self.ctx.fonts.render(
                m.speaker + (f" · 好感 {m.affection_label} ({m.affection})"
                              if m.affection is not None else ""),
                14, self.ctx.theme.text_mute, bold=True,
            )
            bubble.blit(header, (12, 4))
            ty = header_h
            for line in lines:
                ls = font_body.render(line, True, self.ctx.theme.text)
                bubble.blit(ls, (12, ty))
                ty += font_body.get_linesize()
            surface.blit(bubble, (bx, y))
            y += bubble_h + 8
        return y

    def update(self, dt: float, inp) -> None:
        if inp.cancel and self.on_close:
            self.on_close()
            return
        if self.input:
            self.input.update(dt, inp)
        if self.send_btn:
            self.send_btn.update(dt, inp)
        if self.gift_btn:
            self.gift_btn.update(dt, inp)
        if self.close_btn:
            self.close_btn.update(dt, inp)
        if self._scroll:
            self._scroll.update(dt, inp)

    def draw(self, surface: pygame.Surface) -> None:
        veil = pygame.Surface(surface.get_size(), pygame.SRCALPHA)
        veil.fill((0, 0, 0, 170))
        surface.blit(veil, (0, 0))
        self._panel.draw(surface)
        npc = self.ctx.npcs.get(self.npc_id) if self.npc_id else None
        title = self.ctx.fonts.render(
            f"與 {npc.name if npc else self.npc_id} 聊聊",
            self.ctx.config.font_size_header,
            self.ctx.theme.accent, bold=True,
        )
        surface.blit(title, (self._panel_rect.x + 32, self._panel_rect.y + 28))
        if self.close_btn: self.close_btn.draw(surface)
        if self.gift_btn: self.gift_btn.draw(surface)
        if self._scroll: self._scroll.draw(surface)
        if self.input: self.input.draw(surface)
        if self.send_btn: self.send_btn.draw(surface)

    def describe(self) -> dict:
        return {"scene": "ChatScene",
                "npc": self.npc_id,
                "message_count": len(self.messages)}

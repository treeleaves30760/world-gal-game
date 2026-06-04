"""Title screen: name entry + main menu."""
from __future__ import annotations

import pygame

from .base import Scene, SceneContext
from ..ui.widgets import Button, Panel, TextInput, MenuList
from ..ui.widgets.menu_list import MenuItem


class TitleScene(Scene):
    def __init__(self, ctx: SceneContext):
        super().__init__(ctx)
        self.is_overlay = False
        self.name_input: TextInput | None = None
        self.menu: MenuList | None = None
        self.bg_path: str | None = None
        self.title_text: str = ctx.config.title
        self.subtitle_text: str = ctx.config.subtitle
        self.version_text: str | None = None
        self.on_settings = None

    def enter(self, *, bg: str | None = None, title: str | None = None,
              subtitle: str | None = None, version: str | None = None,
              bgm: str | None = None, on_continue=None, on_new_game=None,
              on_load=None, on_quit=None, on_cg_gallery=None,
              on_music_room=None, on_endings=None, on_settings=None,
              **_) -> None:
        self.bg_path = bg
        self.title_text = title or self.title_text
        self.subtitle_text = subtitle or self.subtitle_text
        self.version_text = version
        # Title BGM: play the pack's title track if one is set. A missing file
        # or an uninitialised mixer (headless) degrades to silence inside
        # play_music, so this is always safe to call.
        if bgm:
            self.ctx.assets.play_music(bgm, volume=self.ctx.config.bgm_volume)
        sw, sh = self.ctx.screen_size
        panel_w, panel_h = 420, 240
        cx = sw // 2
        # Name input
        self.name_input = TextInput(
            pygame.Rect(cx - panel_w // 2, sh // 2 + 20, panel_w, 50),
            fonts=self.ctx.fonts, theme=self.ctx.theme,
            placeholder="輸入主角的名字…",
            initial=self.ctx.state.player.name if self.ctx.state.player.name != "玩家" else "",
            max_length=12,
        )
        # Menu
        self.on_continue = on_continue
        self.on_new_game = on_new_game
        self.on_load = on_load
        self.on_quit = on_quit
        self.on_cg_gallery = on_cg_gallery
        self.on_music_room = on_music_room
        self.on_endings = on_endings
        self.on_settings = on_settings
        self._menu_origin = (cx - panel_w // 2, sh // 2 + 90, panel_w)
        self._extras_mode = False
        self._build_menu()

    def _start_new(self) -> None:
        name = (self.name_input.text or "玩家").strip()
        self.ctx.state.player.name = name or "玩家"
        if self.on_new_game:
            self.on_new_game()

    def _build_menu(self) -> None:
        """(Re)build the title menu for the current mode (main vs extras)."""
        ox, oy, pw = self._menu_origin
        row_h = 52
        items = []
        if self._extras_mode:
            if self.on_cg_gallery:
                items.append(MenuItem(self.ctx.t("cg_gallery", "CG鑑賞"),
                                      self.on_cg_gallery))
            if self.on_music_room:
                items.append(MenuItem(self.ctx.t("music_room", "音樂室"),
                                      self.on_music_room))
            if self.on_endings:
                items.append(MenuItem(self.ctx.t("endings", "結局"),
                                      self.on_endings))
            items.append(MenuItem("← 返回", self._close_extras))
        else:
            if self.on_continue:
                items.append(MenuItem("繼續遊戲", lambda: self.on_continue()))
            items.append(MenuItem("開始新遊戲", self._start_new))
            items.append(MenuItem("載入存檔",
                                  lambda: self.on_load and self.on_load()))
            if any((self.on_cg_gallery, self.on_music_room, self.on_endings)):
                items.append(MenuItem(self.ctx.t("extras", "鑑賞模式"),
                                      self._open_extras))
            if self.on_settings:
                items.append(MenuItem(self.ctx.t("settings", "設定"),
                                      lambda: self.on_settings()))
            items.append(MenuItem("離開遊戲",
                                  lambda: self.on_quit and self.on_quit()))
        h = len(items) * row_h + 12
        self.menu = MenuList(
            pygame.Rect(ox, oy, pw, h), items,
            fonts=self.ctx.fonts, theme=self.ctx.theme, row_h=row_h,
        )

    def _open_extras(self) -> None:
        """Swap the title menu to the extras (鑑賞模式) submenu."""
        self._extras_mode = True
        self._build_menu()

    def _close_extras(self) -> None:
        self._extras_mode = False
        self._build_menu()

    def update(self, dt: float, inp) -> None:
        if self.name_input:
            self.name_input.update(dt, inp)
        if self.menu:
            # While the player is typing their name, keyboard nav on the
            # menu would consume W/S/Enter and either jump the selection
            # or fire a menu item. Disable keyboard nav for the menu in
            # that case; mouse clicks still work.
            self.menu.keyboard_nav = not (self.name_input
                                          and self.name_input.focused)
            self.menu.update(dt, inp)

    def draw(self, surface: pygame.Surface) -> None:
        sw, sh = surface.get_size()
        # Background gradient + optional art.
        bg = pygame.Surface(surface.get_size())
        bg.fill(self.ctx.theme.bg_deep)
        if self.bg_path:
            img = self.ctx.assets.scaled(self.bg_path, surface.get_size(),
                                         fit="cover")
            img.set_alpha(140)
            bg.blit(img, (0, 0))
        # Sakura-pink radial fade overlay
        veil = pygame.Surface(surface.get_size(), pygame.SRCALPHA)
        for r, alpha in [(420, 10), (340, 18), (240, 28)]:
            pygame.draw.circle(veil,
                               (*self.ctx.theme.accent[:3], alpha),
                               (sw // 2, sh // 3), r)
        bg.blit(veil, (0, 0))
        surface.blit(bg, (0, 0))

        # Title text
        title = self.ctx.fonts.render(self.title_text,
                                      self.ctx.config.font_size_header + 20,
                                      self.ctx.theme.accent, bold=True)
        surface.blit(title, ((sw - title.get_width()) // 2, sh // 6))
        if self.subtitle_text:
            sub = self.ctx.fonts.render(self.subtitle_text,
                                        self.ctx.config.font_size_menu,
                                        self.ctx.theme.text_mute)
            surface.blit(sub, ((sw - sub.get_width()) // 2,
                               sh // 6 + title.get_height() + 6))
        # Byline
        byline = self.ctx.fonts.render("Powered by World Gal-Game",
                                       self.ctx.config.font_size_small,
                                       (*self.ctx.theme.text_dim, 200))
        surface.blit(byline, ((sw - byline.get_width()) // 2, sh - 40))
        # Version, bottom-right corner (the build/release a player is on).
        if self.version_text:
            ver = self.ctx.fonts.render(f"v{self.version_text}",
                                        self.ctx.config.font_size_small,
                                        (*self.ctx.theme.text_dim, 200))
            surface.blit(ver, (sw - ver.get_width() - 16,
                               sh - ver.get_height() - 14))

        # Name prompt
        prompt = self.ctx.fonts.render("姓名",
                                       self.ctx.config.font_size_small,
                                       self.ctx.theme.text_mute)
        if self.name_input:
            surface.blit(prompt, (self.name_input.rect.x,
                                  self.name_input.rect.y - prompt.get_height() - 4))
            self.name_input.draw(surface)
        if self.menu:
            self.menu.draw(surface)

    def describe(self) -> dict:
        return {
            "scene": "TitleScene",
            "name": self.name_input.text if self.name_input else "",
        }

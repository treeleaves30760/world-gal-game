# Getting Started

> **學習路徑**：軌道 1 · 第一次來  
> **前置條件**：Python 3.10+、`uv` 或 `pip`  
> **下一步**：[tutorial-build-a-game.md](tutorial-build-a-game.md)  
> **完整索引**：[docs/README.md](README.md)

---

本章教你從零安裝 World Gal-Game 引擎、產生第一個遊戲 pack、跑起來。
全程不需要寫任何 Python — 引擎能載入純 YAML 內容。

## 1. 安裝引擎

### Option A：從 PyPI（未來）

```bash
uv pip install world-gal-game
```

### Option B：從原始碼（開發 / 客製化引擎時）

```bash
git clone <world-gal-game-repo-url> World-Gal-Game
cd World-Gal-Game
uv venv
uv pip install -e .
```

安裝完成後，console 會出現 `world-gal-game` 命令。
跑 `world-gal-game --help` 確認。

## 2. 產生第一個 pack

從引擎倉跑：

```bash
uv run python tools/scaffold_pack.py \
    --pack my_first_game \
    --title "我的第一款遊戲" \
    --subtitle "練習用"
```

scaffold 工具會在 `games/my_first_game/` 底下生成：

```
games/my_first_game/
├── content/
│   ├── meta.yaml              # 標題、起始場景、theme/locale 覆寫
│   ├── locations.yaml         # 3 個範例地點
│   ├── characters.yaml        # 1 位女主角 + 1 位側 NPC
│   └── scenes/
│       ├── 00_prologue.yaml   # 序章
│       └── 10_meet_heroine.yaml
└── assets/                    # 空目錄 — 引擎用 placeholder 填
    ├── backgrounds/
    ├── characters/
    ├── cgs/
    ├── ui/
    ├── fonts/
    └── bgm/
```

> 第一次寫 pack 的話，建議先讀
> [**tutorial-build-a-game.md**](tutorial-build-a-game.md)，
> 那是從 0 到一個可玩 demo 的完整 walk-through。

## 3. 跑起來

```bash
uv run python main.py --pack my_first_game
# 或安裝過 console script 後：
uv run world-gal-game --pack my_first_game
```

第一次跑會看到：
- 標題畫面（pack 的 `title` / `subtitle`）
- 輸入名字 → 開始
- 序章 → 探索畫面（看得到女主角、可以走來走去）

沒有任何美術圖檔時，引擎會用「條紋方框 + 檔名」的 placeholder，
所以**遊戲完全可玩**。

## 4. 把 pack 移到引擎倉外面（建議）

為了讓引擎和遊戲各自獨立 git，建議把 pack 搬到引擎倉的兄弟資料夾：

```bash
mv games/my_first_game ../My-First-Game
uv run python main.py --pack my_first_game   # 自動找到 sibling
```

引擎會用 name variants（snake_case ↔ kebab-case ↔ Title-Case）自動匹配。
完整路徑也可以：

```bash
uv run python main.py --pack /Users/me/Code/My-First-Game
```

或讓你的 pack 變成 standalone Python 專案（有自己的 `pyproject.toml`，
依賴 `world-gal-game`），詳見 [pack-format.md](pack-format.md) 的最後一段。

## 5. 下一步

- 修改 `content/scenes/00_prologue.yaml`，改寫故事 → 看 [scenes.md](scenes.md)
- 加新角色 → 看 [characters.md](characters.md)
- 加金錢 / 體力 / 學分系統 → 看 [resources.md](resources.md)
- 加店家、物品 → 看 [items.md](items.md) + [shops.md](shops.md)
- 加成就 → 看 [achievements.md](achievements.md)
- 換配色、改 UI 文字 → 看 [theme-and-locale.md](theme-and-locale.md)

## 開發小工具

| 動作 | 指令 |
|---|---|
| 列出所有 pack | `uv run world-gal-game --list-packs` |
| 全螢幕 | `--fullscreen` |
| 看狀態 (JSON) | `--headless --inspect` |
| 跑腳本 | `--headless --script script.json` |
| 截圖 | `--screenshot out.png --autoplay 1.0` |
| 開特定畫面截圖 | `--dev-start map` 等等（見 `--help`） |
| 內建快捷鍵 | F12 截圖、F11 印狀態、Esc 開選單 |

## 自動化測試你的 pack

引擎的 headless 模式可以驗證劇情邏輯：

```bash
uv run python main.py --headless --script my_route_test.json
```

腳本格式詳見 [headless.md](headless.md)。寫一個就能讓 CI 守住「主路線可以
通關」「壞結局還是 reachable」這種大規則。

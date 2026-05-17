# World Gal-Game · 開發者文檔

本文件夾是 **引擎的開發者文檔**。如果你想用 World Gal-Game 引擎做出自己的
Gal-Game / 視覺小說，從這裡開始。

## 閱讀順序

新手請按順序：

1. [**getting-started.md**](getting-started.md) — 安裝、產生第一個 pack、跑起來
2. [**tutorial-build-a-game.md**](tutorial-build-a-game.md) — 從 0 到可玩 demo 的完整 walk-through（**新手必讀**）
3. [**pack-format.md**](pack-format.md) — pack 的目錄結構與 meta.yaml
4. [**scenes.md**](scenes.md) — 場景 YAML、對話、選項、條件鎖
5. [**characters.md**](characters.md) — 角色、立繪、送禮、商店
6. [**affection.md**](affection.md) — 好感度、多軸 stat、門檻解鎖

擴充功能：

- [**resources.md**](resources.md) — 自訂資源（金錢、體力、學分…）
- [**items.md**](items.md) — 物品、消耗、送禮、分類
- [**shops.md**](shops.md) — 商店、買賣、回收
- [**achievements.md**](achievements.md) — 成就（含隱藏成就）
- [**theme-and-locale.md**](theme-and-locale.md) — 換色 / 換語系

進階：

- [**headless.md**](headless.md) — 無視窗驅動，給 CI / AI agent 用
- [**effects-reference.md**](effects-reference.md) — 全部 effect kind 完整參考
- [**conditions-reference.md**](conditions-reference.md) — 全部 condition kind 完整參考
- [**cookbook.md**](cookbook.md) — 常見模式（鎖路線、店家折扣、時段限定…）
- [**architecture.md**](architecture.md) — 引擎內部結構（給想擴充引擎核心的人）

## TL;DR 三步快速建一個遊戲

```bash
# 1) 裝引擎
uv pip install world-gal-game           # （未來上 PyPI 後）
# 或者開發中：
uv pip install -e /path/to/World-Gal-Game

# 2) 用 scaffold 工具產生新 pack
uv run python /path/to/World-Gal-Game/tools/scaffold_pack.py \
    --pack my_game --title "我的遊戲"

# 3) 跑起來
uv run world-gal-game --pack my_game
```

之後就是編 `games/my_game/content/*.yaml`（或在外部目錄）+
丟 `games/my_game/assets/*.png` 進去；引擎熱載所有內容，
不用改一行 Python。

## 文檔慣例

- 程式碼區塊裡的 `yaml` 是直接放進 `content/*.yaml` 的內容。
- `python` 是用程式驅動引擎的範例（很少需要）。
- 「**effect**」「**condition**」這兩個詞特指場景內可宣告的 kind
  — 完整列表在 [effects-reference.md](effects-reference.md) /
  [conditions-reference.md](conditions-reference.md)。
- 「**pack**」= 一款遊戲的內容包（一個目錄裡放 content + assets）。

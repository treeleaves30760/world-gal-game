# World Gal-Game · 文件總覽

完整的「從零做出一款 Gal-Game」教學指引。引擎本身（`world_gal_game/`）只負責跑
遊戲；遊戲內容打包成 **pack**，全部走 YAML。**99% 的遊戲開發不用碰一行 Python**。

需要更高層的方向感 / 路線圖請看 [../ROADMAP.md](../ROADMAP.md)；
AI 協作者進場速查 [../CLAUDE.md](../CLAUDE.md)。

---

## 學習路徑（建議閱讀順序）

四條軌道，從上到下難度遞增。第一次來只需要走完軌道 1 + 2，就能做出一款完整可玩
的遊戲。

### 軌道 1 · 第一次來

| 順序 | 文件 | 你會學到 |
|---|---|---|
| ① | [getting-started.md](getting-started.md) | 裝引擎、scaffold 出第一個 pack、跑起來 |
| ② | [tutorial-build-a-game.md](tutorial-build-a-game.md) | 跟著一步一步把 demo_pack 從零做出來 |
| ③ | [pack-format.md](pack-format.md) | pack 的整體結構與 `meta.yaml` 完整欄位 |

讀完這三份你就有一個基本可玩、能跳轉場景、能做選擇的 Gal-Game。

### 軌道 2 · 製作 pack 內容

各子系統的撰寫指南。順序按依賴度排，但**可以跳著讀**。

| 文件 | 你會學到 |
|---|---|
| [scenes.md](scenes.md) | 場景 YAML、對話、選項、`on_end` 跳轉 |
| [characters.md](characters.md) | 角色、立繪、人設、`PortraitSpec` 多角色站位 |
| [locations.md](locations.md) | 地圖節點、Region 分群、`scene_hooks` 自動觸發場景 |
| [affection.md](affection.md) | 多軸好感度、門檻解鎖、自訂等級標籤 |
| [items.md](items.md) | 物品、消耗、`use_effects`、送禮魅力值 |
| [shops.md](shops.md) | 商店、買賣回收、貨幣繫結資源 |
| [resources.md](resources.md) | 自訂資源（金錢、體力、學分…）、`gain_resource` |
| [quests.md](quests.md) | 任務系統、目標追蹤、Quest Log UI |
| [achievements.md](achievements.md) | 成就、隱藏成就、觸發條件 |
| [presentation-and-extras.md](presentation-and-extras.md) | CG鑑賞 / 音樂室 / 場景重溫 / 結局 + 完成度、Auto / Skip、NVL、鏡頭 / 畫面特效、角色語音、快速 / 自動存檔 |
| [theme-and-locale.md](theme-and-locale.md) | 換色、換語系、`affection_levels` 在地化 |
| [cookbook.md](cookbook.md) | 常見模式食譜（鎖路線、限時事件、商店折扣…） |

### 軌道 3 · 完整 effect / condition 參考

寫到一半發現「我要哪個 kind」就翻：

| 文件 | 內容 |
|---|---|
| [effects-reference.md](effects-reference.md) | 全部 23 個內建 effect kind + 範例 + 邊界情況 |
| [conditions-reference.md](conditions-reference.md) | 全部 16 個內建 condition kind |

引擎還支援**外掛擴充更多 kind** — 跑 `wgg capabilities --pack <p>` 拿到該 pack
實際可用的全套清單。

### 軌道 4 · 進階

#### 4a. 用 AI 工具當開發者（headless 操作）

| 文件 | 用途 |
|---|---|
| [ai-developer-guide.md](ai-developer-guide.md) | **AI 協作者入門**：所有可用 API + CLI |
| [headless.md](headless.md) | `HeadlessSession`：無視窗跑遊戲、抓玩家狀態 |
| [ai-debug.md](ai-debug.md) | `wgg debug`：注入點擊、截圖、widget 查找 |

#### 4b. 寫插件擴充引擎本體

| 文件 | 用途 |
|---|---|
| [plugins.md](plugins.md) | 用 `@effect` / `@condition` / `@hook` / `@scene` / `@widget` / `@brain` / `@dialogue_op` 加新功能 |

#### 4c. 引擎內部 / 發布

| 文件 | 用途 |
|---|---|
| [architecture.md](architecture.md) | 模組分層、資料流、加新 widget / scene 的範本 |
| [distribution.md](distribution.md) | 把 pack 打包成可執行檔、上 PyPI、發佈 |

---

## 快速上手（30 秒版本）

```bash
# 1) 裝引擎（pip 或 uv pip）
uv pip install -e .

# 2) 產生新 pack
uv run python tools/scaffold_pack.py --pack my_game --title "我的遊戲"

# 3) 跑起來
uv run world-gal-game --pack my_game
```

之後就是編 `games/my_game/content/*.yaml`、丟 `games/my_game/assets/*.png` 進去。
引擎熱載所有內容，**不用改一行 Python**。

要驗證 pack 沒寫錯：

```bash
uv run world-gal-game self-check my_game        # 跑完整 5 階段驗證
uv run world-gal-game inspect-pack my_game      # 看 pack 結構分析
```

---

## 工具鏈速查表

| 你想做的事 | CLI | Python API |
|---|---|---|
| 啟動遊戲（GUI） | `wgg --pack <p>` | `world_gal_game.run(pack=...)` |
| 看玩家狀態 JSON | `wgg --headless --inspect --pack <p>` | `HeadlessSession.inspect()` |
| 跑腳本通關 | `wgg --headless --script s.json --pack <p>` | `HeadlessSession.run_script(...)` |
| 截圖 | `wgg --screenshot out.png --pack <p>` | (內含於 CLI) |
| 注入點擊 / debug UI | `wgg debug repro.json` | `dev.driver.GameDriver` |
| 整套驗證（5 階段） | `wgg self-check <p>` | `dev.self_check.SelfCheck` |
| YAML schema + refs 驗證 | `wgg check <p>` | `validator.validate_pack` |
| pack 拓樸分析 | `wgg inspect-pack <p>` | `dev.pack_inspector.PackInspector` |
| 結構化編輯 pack | `wgg edit <p> add-scene ...` | `dev.pack_editor.PackEditor` |
| 跑全 smoke 腳本 | `wgg smoke <p>` | `dev.smoke_runner.SmokeRunner` |
| 視覺回歸測試 | `wgg visual-check <p>` | `dev.visual_check.VisualCheck` |
| 看引擎能力清單 | `wgg capabilities [--pack <p>]` | `dev.capability_manifest.build_manifest()` |
| 產生 placeholder 圖 | (內含於 Python API) | `dev.asset_studio.placeholder_image` |

完整 CLI 列表執行 `wgg --help`。

---

## 文檔慣例

- **YAML 區塊**直接放進 `content/*.yaml`。
- **Python 區塊**是程式驅動或寫插件的範例。
- **effect / condition** 特指場景內可宣告的 kind — 完整列表在
  [effects-reference.md](effects-reference.md) / [conditions-reference.md](conditions-reference.md)。
- **pack** = 一款遊戲的內容包（一個目錄裡放 content + assets + plugins）。
- **插件** = 一個目錄裡含 `plugin.yaml` + Python 進入點的擴充模組，放在
  `<pack>/plugins/<id>/` 內。

---

## 路線

階段路線、缺口、未完成功能：[../ROADMAP.md](../ROADMAP.md)。

當前進度：
- Phase 1：插件系統 MVP + Capability Manifest + PackEditor + dead-end 偵測 — ✅ 完成
- Phase 2：擴大插件擴充點（widget/scene/brain/dialogue_op）+ AI 端對端開發 loop
  （smoke / visual / self-check / asset_studio）— ✅ 完成
- Phase 3：pack marketplace、LLM NPC v2、AI 自主從 spec 產 pack — 待開工

# World Gal-Game · 文件總覽

World Gal-Game 是一個**讓 AI 成為一等開發者**的 Gal-Game / 視覺小說引擎：人類用
YAML 撰寫遊戲，AI agent 能無視窗地遊玩、檢查、結構化修改並驗證同一款遊戲，第三方
則以外掛擴充引擎。引擎本身（`world_gal_game/`）只負責跑遊戲；遊戲內容打包成
**pack**，全部走 YAML。**99% 的遊戲開發不用碰一行 Python**。

這個資料夾是所有文件的唯一來源；文件網站
（[World-Gal-Game-Web](https://github.com/treeleaves30760/World-Gal-Game-Web)）
在建置時直接同步這裡的 Markdown。

需要更高層的方向感 / 路線圖請看 [../ROADMAP.md](https://github.com/treeleaves30760/world-gal-game/blob/main/ROADMAP.md)；
AI 協作者進場速查 [../CLAUDE.md](https://github.com/treeleaves30760/world-gal-game/blob/main/CLAUDE.md)。

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
| [presentation-and-extras.md](presentation-and-extras.md) | CG鑑賞 / 音樂室 / 場景重溫 / 結局 + 完成度、Auto / Skip、NVL、鏡頭 / 畫面特效、角色語音、精確續讀、快速 / 自動存檔 |
| [theme-and-locale.md](theme-and-locale.md) | 換色、換語系、`affection_levels` 在地化 |
| [cookbook.md](cookbook.md) | 常見模式食譜（鎖路線、限時事件、商店折扣…） |

### 軌道 3 · 完整 effect / condition 參考

寫到一半發現「我要哪個 kind」就翻：

| 文件 | 內容 |
|---|---|
| [effects-reference.md](effects-reference.md) | 自動生成的全部內建 effect kind + 範例 + 邊界情況 |
| [conditions-reference.md](conditions-reference.md) | 自動生成的全部內建 condition kind |

引擎還支援**外掛擴充更多 kind** — 跑 `world-gal-game capabilities --pack <p>` 拿到該 pack
實際可用的全套清單。

### 軌道 4 · 進階

#### 4a. 用 AI 工具當開發者（headless 操作）

| 文件 | 用途 |
|---|---|
| [../AGENTS.md](https://github.com/treeleaves30760/world-gal-game/blob/main/AGENTS.md) | **agent-中立進場速查**（任何 AI 工具）；機器地圖見 [../llms.txt](https://github.com/treeleaves30760/world-gal-game/blob/main/llms.txt) |
| [ai-developer-guide.md](ai-developer-guide.md) | **AI 協作者入門**：所有可用 API + CLI |
| [ai-native-contract.md](ai-native-contract.md) | 動詞 / 參數 schema / trace / diff / 決定性 |
| [ai-native-world-model.md](ai-native-world-model.md) | variables / dataflow / planner / coverage / rollback；`context` / `impact` 聚合端點 |
| [session-protocol.md](session-protocol.md) | 暖 NDJSON session：行框、訊息形狀、batch 原子性、錯誤格式 |
| [headless.md](headless.md) | `HeadlessSession`：無視窗跑遊戲、抓玩家狀態 |
| [ai-debug.md](ai-debug.md) | `world-gal-game debug`：注入點擊、截圖、widget 查找 |

#### 4b. 寫插件擴充引擎本體

| 文件 | 用途 |
|---|---|
| [plugins.md](plugins.md) | 用十類擴充點（含 `@portrait_backend` / `@ambient_backend`）加新功能 |

#### 4c. 美術素材生成

| 文件 | 用途 |
|---|---|
| [art-transparency.md](art-transparency.md) | 生成透明立繪 / UI、用 `cutout.py` 去背與**修白邊**（前景顏色去汙染） |

#### 4d. 引擎內部 / 發布

| 文件 | 用途 |
|---|---|
| [architecture.md](architecture.md) | 模組分層、資料流、加新 widget / scene 的範本 |
| [destination-schedule-map.md](destination-schedule-map.md) | 行程型空間地圖的 `TravelPlan`、遠距確認、抵達事件與 GUI/headless 契約 |
| [distribution.md](distribution.md) | 把 pack 打包成可執行檔、上 PyPI、發佈 |
| [stable-line-identity.md](stable-line-identity.md) | 以 `Line.loc_id` 綁定已讀／續讀／Backlog／在地化的台詞 identity 規格 |
| [commercial-quality-benchmark.md](commercial-quality-benchmark.md) | 對照商業 GalGame 的能力、缺口、證據與下一階段 production gates |

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
uv run world-gal-game self-check my_game        # 跑 7 階段管線；visual 預設需另行啟用
uv run world-gal-game inspect-pack my_game      # 看 pack 結構分析
```

---

## 工具鏈速查表

| 你想做的事 | CLI | Python API |
|---|---|---|
| 啟動遊戲（GUI） | `world-gal-game --pack <p>` | `world_gal_game.run(pack=...)` |
| 看玩家狀態 JSON | `world-gal-game --headless --inspect --pack <p>` | `HeadlessSession.inspect()` |
| 跑腳本通關 | `world-gal-game --headless --script s.json --pack <p>` | `HeadlessSession.run_script(...)` |
| 截圖 | `world-gal-game --screenshot out.png --pack <p>` | (內含於 CLI) |
| 注入點擊 / debug UI | `world-gal-game debug repro.json` | `dev.driver.GameDriver` |
| 整套驗證（7 階段，visual 預設 opt-in） | `world-gal-game self-check <p>` | `dev.self_check.SelfCheck` |
| YAML schema + refs 驗證 | `world-gal-game check <p>` | `validator.validate_pack` |
| pack 拓樸分析 | `world-gal-game inspect-pack <p>` | `dev.pack_inspector.PackInspector` |
| 結構化編輯 pack | `world-gal-game edit <p> add-scene ...` | `dev.pack_editor.PackEditor` |
| 跑全 smoke 腳本 | `world-gal-game smoke <p>` | `dev.smoke_runner.SmokeRunner` |
| 視覺回歸測試 | `world-gal-game visual-check <p> [--strict-baselines]` | `dev.visual_check.VisualCheck` |
| Fail-closed 來源發行預檢 | `world-gal-game production-check <p>` | `dev.production_check.run_production_check` |
| 重驗 production artifact | `world-gal-game production-verify <report> --pack <p>` | `dev.production_check.verify_production_report` |
| 看引擎能力清單 | `world-gal-game capabilities [--pack <p>]` | `dev.capability_manifest.build_manifest()` |
| 產生 placeholder 圖 | (內含於 Python API) | `dev.asset_studio.placeholder_image` |

完整 CLI 列表執行 `world-gal-game --help`。

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

階段路線、缺口、未完成功能：[../ROADMAP.md](https://github.com/treeleaves30760/world-gal-game/blob/main/ROADMAP.md)。

當前進度：

- 引擎能力：資料驅動核心、十類插件擴充、AI 開發工具鏈、主要 VN 呈現 /
  extras、影片路徑，以及 exact dialogue resume 已實作。
- 2026-07-26 玩家可靠度：選擇肢鍵盤 / 手把導覽、1280×720 /
  1920×1080 標題版面，以及 UI presentation 完成後才觸發的安全 autosave
  邊界已加入測試。
- 2026-07-31 語意輸入里程碑：完整自動化鍵盤 / 手把 action path、persisted
  Controls 重綁與衝突交換、動態提示及 capture no-through 已完成；地圖局部實測為
  MapScene 20.75 ms、detail 16.56 ms、0 full clone。實體控制器、不同鍵盤
  配置、身障玩家與人工核准 baseline 仍是外部 release gate。
- 2026-08-02 Music Room v1：選填 `content/music_room.yaml` 提供正式標題、credit、
  使用情境、`loc_id`、排序與 spoiler-safe `hidden` roster；缺檔時保留 legacy
  `assets/bgm/` discovery。已聽進度會經 current-pack media authority 安全地併入
  ClearData，UI 支援語意鍵盤 / 手把、滑鼠 / 觸控、可見焦點與音訊 snapshot restore。
  格式與玩家契約分別見 [pack-format.md](pack-format.md#music_roomyaml選填) 與
  [presentation-and-extras.md](presentation-and-extras.md#音樂室music-room)。
- 台詞持久化 identity：production pack 以 immutable `Line.loc_id` 綁定 Read／Skip、
  active cursor、resume、Backlog、Scene Replay 與 localization；舊 index 只作 legacy
  fallback。導入與跨版本遷移規格見
  [stable-line-identity.md](stable-line-identity.md)。
- 來源碼層的 fail-closed `production-check` 已實作：它要求完整七階段
  self-check、17 個語意 view × 720p/1080p 的 34 張人工核准 baseline、全腳本聯集
  coverage、narrative contract、具完整數值上限的 runtime gate，以及 clean、全追蹤
  且執行前後不變的 Git 來源。仍待各 pack 的正式 baselines、真實打包目標、效能 /
  accessibility / localization / 實素材音影片證明。

「引擎有這項能力」不代表「商業發行品質已證明」；後者只有在對應 production
gate 於實際目標與商業規模 pack 上通過後才能宣稱。

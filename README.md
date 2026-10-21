# World Gal-Game

**讓 AI 成為一等開發者的 Gal-Game / 視覺小說引擎。**
*The Gal-Game engine where AI is a first-class developer.*

World Gal-Game 是一個以 Python + pygame 寫成的視覺小說引擎。它的目標不只是讓人類
用 YAML 寫出完整的 Gal-Game，而是讓 **AI agent 能和人類一起參與製作的每一步**：
讀懂一款遊戲的結構、無視窗地把它玩一遍、做結構化修改、再用可重跑的檢查證明
修改沒有弄壞任何路線。

- 文件網站：<https://treeleaves30760.github.io/World-Gal-Game-Web/>（原始文件就在本 repo 的 [`docs/`](docs/README.md)）
- 路線圖：[ROADMAP.md](ROADMAP.md)
- 給 AI 協作者的入口：[AGENTS.md](AGENTS.md)（任何 agent）、[CLAUDE.md](CLAUDE.md)（Claude Code）、[llms.txt](llms.txt)（機器可讀地圖）

---

## 三個支柱

| | 誰 | 怎麼做 |
|---|---|---|
| **A. 人類寫遊戲** | 編劇、美術、企劃 | 遊戲是一個 YAML **pack**：場景、角色、地點、物品、結局全部宣告式撰寫；`validate` 會給出「did you mean」提示。99% 的內容不需要寫 Python。 |
| **B. AI 開發遊戲** | Claude Code、Codex 或任何 agent | Headless 遊玩、狀態快照/還原、結構化編輯、資料流世界模型、目標導向 planner、coverage、七階段 self-check——全部有 JSON 輸出與 JSON-Schema。 |
| **C. 外掛擴充引擎** | 第三方開發者 | 十種 decorator（`@effect` `@condition` `@hook` `@inspect_field` `@widget` `@scene` `@brain` `@dialogue_op` `@portrait_backend` `@ambient_backend`），不用改引擎核心。 |

## 引擎，不是遊戲

本 repo 只有引擎（`world_gal_game/`），**核心不含任何特定遊戲的邏輯**。每款遊戲都是
獨立的 pack，可以放在：

- `games/<pack>/` —— 隨引擎附帶（範例 `games/demo_pack/`，5–10 分鐘可玩完）
- `../<pack>/` —— 引擎旁的兄弟資料夾（有自己 git repo 的長期專案）
- `~/.world-gal-game/packs/<pack>/` —— 使用者本地
- 任何以 `--pack <path>` 指定的路徑

Pack 可以採用與引擎不同的授權，也可以是閉源的；引擎以 MIT 授權釋出。

## 快速開始

```bash
uv sync                                   # 安裝相依套件
uv run python main.py                     # 玩內附的 demo_pack
uv run python main.py --list-packs        # 列出找得到的 pack

# 產生你自己的 pack 骨架並執行
uv run python tools/scaffold_pack.py --pack my_game --title "我的遊戲"
uv run python main.py --pack my_game
```

需要 Python 3.10+ 與 [uv](https://docs.astral.sh/uv/)。完整教學見
[getting-started](docs/getting-started.md) 與 [tutorial-build-a-game](docs/tutorial-build-a-game.md)。

## AI 開發迴圈：理解 → 修改 → 驗證

```bash
# 1. 理解：最省 token 的整體結構，再放大到單一符號
uv run world-gal-game brief games/demo_pack --format text
uv run world-gal-game card games/demo_pack --symbol heroine_1
uv run world-gal-game impact games/demo_pack --symbol <flag_or_scene>

# 2. 修改：結構化、保留註解的 YAML 編輯（可 dry-run）
uv run world-gal-game edit games/demo_pack add-scene \
    --payload '{"id":"sketch_demo","title":"...","lines":[]}' --dry-run

# 3. 驗證：無視窗通關、七階段自檢、敘事契約
uv run python main.py --headless --pack demo_pack \
    --script games/demo_pack/scripts/test_lover_route.json
uv run world-gal-game self-check games/demo_pack
uv run world-gal-game contract games/demo_pack
```

長時間協作時，`world-gal-game session --pack <pack>` 開啟一個常駐的 NDJSON
session：pack 只載入一次，agent 透過 stdin/stdout 串流 `next`／`choose`／
`snapshot`／`restore`／`edit.*` 等操作，每次編輯都會回傳 YAML diff 與世界模型的
impact（新的死路、不可達結局、未宣告旗標）。細節見
[ai-developer-guide](docs/ai-developer-guide.md)、[ai-native-contract](docs/ai-native-contract.md)、
[ai-native-world-model](docs/ai-native-world-model.md)、[session-protocol](docs/session-protocol.md)。

## 引擎能力一覽

- **閱讀體驗**：ADV／NVL、Auto／Skip（只略過已讀）、Backlog、Rollback、快速／自動存檔、
  逐行續讀、語音與字幕、富文本與行內等待
- **Gal-Game 外圍**：CG 鑑賞、音樂室、場景重溫、結局與完成度、流程圖、成就、Credits
- **遊戲系統**：多軸好感度、地點／時段地圖與目的地選單、任務、物品與送禮、商店、資源、章節
- **演出**：立繪站位與說話者變暗、呼吸／序列幀／分層立繪動畫、鏡頭與畫面特效、天氣
- **在地化與無障礙**：runtime 多語系、字型 fallback、高對比、減少動態、按鍵重綁、觸控
- **發行**：PyInstaller 桌面版、pygbag/WASM 網頁版與 PWA、Steam、Android
- **品質閘門**：self-check、smoke、visual-check、runtime-check、fail-closed production-check

## Repo 結構

```
World-Gal-Game/
├── world_gal_game/        # 引擎本體（唯一會被打包進 wheel 的目錄）
│   ├── core/              #   純 pydantic 資料模型：GameState、StoryGraph、存檔、在地化…
│   ├── dialogue/          #   對白引擎、條件文字、腳本載入
│   ├── scenes/            #   pygame 畫面狀態機（標題、對話、地圖、CG 鑑賞…）
│   ├── ui/                #   主題、字型、輸入、動態、widgets
│   ├── plugins/           #   外掛註冊表、管理器、內建 effect / condition 與其 JSON-Schema
│   ├── plugins_user/      #   隨引擎附帶的外掛（animated_portraits、ambient_weather…）
│   ├── dev/               #   AI／開發者工具：inspector、editor、dataflow、planner、
│   │                      #   coverage、session server、self-check、production-check…
│   ├── npc/               #   NPC 與 Brain 介面
│   ├── integrations/      #   Steam 等平台整合
│   ├── headless.py        #   無視窗驅動（HeadlessSession）
│   └── cli.py             #   `world-gal-game` 指令
├── games/demo_pack/       # 內附範例 pack（含 step_counter 外掛範例與通關腳本）
├── docs/                  # 所有文件的唯一來源（文件網站由 World-Gal-Game-Web 同步建置）
├── tests/                 # pytest 測試
├── tools/                 # 開發輔助腳本（scaffold、文件產生、美術管線、i18n）
├── main.py / build.py     # 開發入口 / 打包入口
├── AGENTS.md CLAUDE.md llms.txt   # AI 協作者入口
└── ROADMAP.md
```

## 測試與檢查

```bash
uv run pytest tests/                          # 單元與整合測試
uv run world-gal-game smoke games/demo_pack   # 執行 pack 的所有 scripts/test_*.json
uv run world-gal-game self-check games/demo_pack
```

任何引擎改動都應該保持 demo_pack 的 smoke 全綠。發行前的 runtime 與
production 閘門見 [distribution](docs/distribution.md)。

## 打包

```bash
uv run world-gal-game build games/demo_pack   # 桌面版（PyInstaller）
```

網頁、Steam 與行動版見 [distribution-web](docs/distribution-web.md)、
[distribution-steam](docs/distribution-steam.md)、[distribution-mobile](docs/distribution-mobile.md)。

## 貢獻

- 新的遊戲主題（恐怖、養成、戀愛…）請寫成外掛，不要改 `world_gal_game/core/`。
- 狀態變更一律經過 `GameState.apply` / `evaluate` 的 registry dispatch。
- 改動 pack schema 時需要對應的 `pack_format_version` migration。
- 不使用裝飾性 emoji（UI、文件、commit 訊息皆同）。

開始改程式前請先讀 [docs/architecture.md](docs/architecture.md)。

## 授權

引擎以 [MIT](LICENSE) 授權釋出。外部 game pack 各自擁有自己的授權；重新散佈
任何 pack 的劇本或美術前，請先確認該 pack 的授權條款。

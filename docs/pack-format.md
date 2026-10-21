# Pack Format

> **學習路徑**：軌道 1（最後一站）  
> **前置條件**：讀完 [tutorial-build-a-game.md](tutorial-build-a-game.md)  
> **下一步**：軌道 2 — 各子系統指南（[scenes.md](scenes.md) / [characters.md](characters.md) / [locations.md](locations.md) / …）  
> **完整索引**：[docs/README.md](README.md)

---

一個 *pack* 就是一個目錄；放進引擎找得到的地方，
`world-gal-game --pack <name>` 就能玩。

## 完整目錄結構

```
my_game/
├── pyproject.toml          # 選填，把 pack 包成 pip-installable
├── README.md
├── main.py                 # 選填，當 standalone 跑時的入口
├── content/                # 必要 — 引擎只認這個資料夾
│   ├── meta.yaml           # 必要 — 沒有就不算 pack
│   ├── locations.yaml      # 選填
│   ├── characters.yaml     # 選填
│   ├── items.yaml          # 選填
│   ├── resources.yaml      # 選填（或寫在 meta.yaml）
│   ├── achievements.yaml   # 選填
│   ├── endings.yaml        # 選填 — 結局與完成度
│   ├── variables.yaml      # 選填 — typed narrative state
│   ├── chapters.yaml       # 選填 — 章／幕／路線結構
│   ├── cg_gallery.yaml     # 選填 — CG 標題、排序與鑑賞 roster
│   ├── music_room.yaml     # 選填 — BGM 標題、credit、排序與鑑賞 roster
│   ├── i18n/              # 選填 — versioned source / target locale catalogs
│   │   ├── messages.zh-Hant-TW.yaml
│   │   └── en.yaml
│   └── scenes/             # 選填，多檔散裝
│       ├── 00_*.yaml
│       └── ...
└── assets/                 # 選填 — 不存在時引擎用 placeholder
    ├── backgrounds/
    ├── characters/
    ├── cgs/
    ├── ui/
    ├── fonts/
    └── bgm/
```

**所有 YAML 內的資產路徑寫 `assets/...`（pack-相對）**，例如
`assets/backgrounds/library.png`。引擎在載入時會自動把它對應到這個
pack 的根目錄。

## meta.yaml（必要）

最小可行：

```yaml
pack_format_version: "0.1"       # 引擎用來判斷 schema 版本（Phase 1 起建議）
title: "我的遊戲"
subtitle: "副標題"
start_location: starting_room    # 開新遊戲時把玩家放在這個地點
intro_scene: prologue            # 開新遊戲後第一個自動播的場景
```

### pack_format_version

從 Phase 1 開始引擎讀這個欄位，未來 schema 變動會在這裡 bump。Phase 1
只是「記錄」，不做 migration；缺欄位只會出 warning，不會拒絕載入。新建 pack
請寫 `"0.1"`。

完整可選欄位：

```yaml
title: "我的遊戲"
subtitle: "副標題"

# 文字顯示速度 (chars/sec)。0 = 瞬間。
text_speed: 60

# 內嵌字型；CJK 一律建議內嵌避免使用者系統沒有字型。
bundled_font: assets/fonts/MyFont.ttf

# 舊版／共用標題畫面背景圖與音樂；路徑必須留在 pack 內。
title_bg: assets/backgrounds/title.png
title_bgm: assets/bgm/title.ogg

# 選填：pack-driven 標題畫面構圖。整個 block 缺省時完全沿用既有
# centered + glass + inline 姓名輸入；未知欄位與錯誤型別會被 validator
# 拒絕，不會靜默套用另一種版面。
title_screen:
  layout: left_rail              # centered | left_rail | right_rail
  panel_style: rail              # glass | rail
  name_entry: on_new_game        # inline | on_new_game
  logo_image: assets/ui/logo.png # 選填；安全的 pack 相對圖片路徑
  wordmark_style: editorial      # classic | editorial
  tagline: "在日常的縫隙裡，故事開始了。"
  background_opacity: 0.8039     # 0.0–1.0；預設 205/255
  show_engine_credit: true
  footer_text: "© 2026 Example Circle"

# 選填：由 pack plugin 擁有的系統 UI chrome。renderer 必須是已註冊
# @widget，且 signature.protocol 為 ui_chrome/v1。整個 block 缺省時
# 使用當版引擎內建的 stock fallback 行為。
ui_skin:
  renderer: my_pack_system_chrome
  options:
    line_weight: 2
    corner_length: 14

# 起始地點 / 場景。
start_location: starting_room
intro_scene: prologue

# 選填：只供舊存檔／explicit replay 的退役內容。
# 這不是任意 suppress；每個 id 與目前 verdict 都會重新驗證。
verification:
  expected_unreachable:
    retired_alone_route:
      scenes: [ending_alone_legacy_scene]
      endings: [ending_alone_legacy]
      reason: "保留舊存檔相容；目前新遊戲已移除此分支。"

# 選填：在有明確敘事目標的階段鎖住探索畫面的系統動作。
# 按鈕仍顯示；滑鼠 hover／點擊、快捷鍵、app guard 與 headless
# affordances 都會回報同一個 authored 原因；等待規則也供 guided
# destination resolver 判斷未來時段。
# 劇情 YAML 內 authored 的 effect 不受這個 UI policy 影響。地圖鎖定時，
# 目前可用且 travel_cost: 0 的相鄰出口會以「前往地點」的直接動作保留，
# 可用來安排不會被整體地圖 gate 鎖死的一步教學。
exploration_guidance:
  advance_time:
    requires_flags: [orientation_done]
    forbids_flags: []
    locked_reason: "先完成目前的目標，再推進時間。"
  map:
    requires_flags: [orientation_done]
    forbids_flags: []
    locked_reason: "先依新生指南完成集合，之後才打開校園地圖。"

# 選填：新存檔的目的地選擇器預設只列出現在能去的地點。
# 玩家仍可關掉「只看現在可去」，之後以該存檔的選擇為準。
travel_default_reachable_only: true

# 選填：啟用可從地圖確認的原子遠距行程。未設定時仍是既有的一步 exit。
# requires_flags 可讓教學開場先維持單一路徑，完成後才打開全圖。
travel:
  scope: graph
  requires_flags: [map_tutorial_done]
  minimum_cost: 1
  hidden_tags: [story_only]
  spatial_map:
    # 選填：pack 自己繪製的校園／城鎮總覽。位置標記沿用
    # locations.yaml 的 map_x / map_y；bounds 讓標記不因顯示／隱藏而漂移。
    background: assets/maps/campus.png
    background_fit: cover       # cover | contain | stretch
    bounds: [0, 0, 100, 100]
    attribution: "Map data © OpenStreetMap contributors"
    # 選填：指定 scene 正常結束後自動開圖；每個值必須是既有 scene id。
    auto_open_after_scenes: [map_tutorial]
    # 選填：為既有 location 描述呈現階層與短標籤。未列出的地點預設
    # 為 campus；interior 只在 parent、當前故事或目前位置出現，
    # story_only 不混入一般探索；label 只改地圖短名。
    nodes:
      library:
        layer: campus             # campus | interior | story_only
        label: "總圖"
      library_second_floor:
        layer: interior
        parent: library
        label: "二樓閱覽區"
      hidden_garden_event:
        layer: story_only
        parent: library
    # 列在這裡的地點不放在中央畫布，而成為對應方位的校外縮圖卡。
    # 其餘可見地點仍是中央 campus canvas nodes；hidden_tags 仍優先，
    # 加入縮圖設定不會提早曝光 story_only 等隱藏地點。
    edge_destinations:
      night_market:
        direction: southwest    # N/NE/E/SE/S/SW/W/NW 或完整英文方位
        thumbnail: assets/maps/night-market.jpg
        caption: "城隍廟商圈"
        attribution: "Photo: Example Author, CC BY-SA 4.0"
  guided_story:
    # 選填：graph requires_flags 尚未滿足時，仍顯示既有單段 exit 上的
    # 故事指引；不會開放遠距路徑，也不套用 graph 的 minimum_cost。
    direct_before_graph: true
    # 選填：有 progress 且當前 stage 尚未完成時，普通對話 closure
    # 在沒有 ambient 劇情／modal 接手時自動回到地圖。
    auto_reopen: true
    future_horizon_phases: 6
    max_primary: 3
    require_marker: true
    progress:
      label: "新生指南"
      stages:
        - label: "第一輪 · 三人的普通日常"
          flags: [met_a, met_b, met_c]
        - label: "第二輪 · 三人的核心矛盾"
          flags: [core_a, core_b, core_c]
    markers:
      heroine_library_event:
        npc_ids: [heroine]
        teaser: "她在總圖等你，桌上還壓著一張沒署名的稿紙。"

# 玩家預設名字、第一人稱代詞（標題畫面可以改）。
player:
  name: "玩家"
  pronouns: "他"

# 起始持有物品（key = item id, value = count）。
starting_inventory:
  jasmine_tea: 1
  espresso: 1

# 起始資源值（key = resource id, value = amount）。
# 也可以直接在 resources 定義 starting；兩個都會被讀。
starting_resources:
  money: 500
  energy: 100

# Theme 覆寫（不寫的欄位用引擎預設）。
theme:
  accent:      [216, 80, 143]    # RGB or RGBA
  accent_alt:  [107, 107, 255]
  accent_warm: [240, 198, 116]
  bg_deep:     [13, 10, 20]
  pad_m: 14
  radius_m: 10

# 在地化：舊式 source 覆寫 + 選填的 runtime catalog manifest。
locale:
  affection_levels:
    - {min: 0, label: "陌生"}
    - {min: 25, label: "朋友"}
  time_of_day:
    morning: "早晨"
    midnight: "深夜"
  ui:
    new_game: "開始新遊戲"
    map: "地圖"

  source: zh-Hant-TW
  default: zh-Hant-TW
  languages:
    zh-Hant-TW:
      label: "繁體中文"
      status: complete
      catalog: content/i18n/messages.zh-Hant-TW.yaml
      story_scenes: all
      font:
        bundled: assets/fonts/NotoSansTC.otf
        candidates: ["Noto Sans TC"]
    en:
      label: "English Preview"
      status: preview
      catalog: content/i18n/en.yaml
      story_scenes: [prologue, first_day_tour]
      font:
        candidates: ["Noto Sans", "Arial"]

# 資源宣告 — 也可以寫在 content/resources.yaml。
resources:
  - id: money
    name: "錢包"
    symbol: "$"
    starting: 500
    min: 0
```

### Runtime localization v1

只要 `locale` 出現 `source`、`default` 或 `languages` 其中之一，就必須
提供完整的三欄 manifest。`source` 與 `default` 必須是
`languages` 的鍵；每個 language entry 只接受下列欄位：

| 欄位 | 規則 |
|---|---|
| `label` | 必填的玩家可見語系名稱 |
| `status` | `complete` 或 `preview` |
| `catalog` | 明示的 pack-relative `.yaml` / `.yml`；target 必填，source 可為 legacy 相容省略 |
| `story_scenes` | `all`、`"*"` 或 scene-id list；complete 預設全部，preview 預設為空（僅 meta/UI） |
| `font.bundled` | 選填 pack-relative `.ttf` / `.otf` |
| `font.candidates` | 選填、有序的系統字型名稱清單 |

Canonical catalog 放在 `content/i18n/`，source 與 target 都使用固定 v1
envelope：

```yaml
format_version: 1
language: en
source_language: zh-Hant-TW
source_digest: 0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef
messages:
  meta.title: "An Afternoon in Town"
  ui.new_game: "New Game"
  prologue.line.arrival_gate: "I looked up at the campus gate."
```

`source_digest` 綁定全部抽取後的 source message table。生產 pack 應宣告
versioned source catalog，讓 runtime 可以發現 authored source 漂移；錯誤或過期的
target 鍵會 fail closed 回到 source。Catalog 不能翻譯 ids、conditions、
effects、transitions、flags 或 asset paths，只能改呈現文案。

Locale id 只是受限的 BCP-47-shaped token，runtime 不會用它組路徑。
Catalog/font 路徑必須由 manifest 明示提供且不得離開 pack；strict
YAML loader 會拒絕 duplicate keys 與超出上限的 catalog。若一個 target
無法完整驗證，整個 language option 會移除並回退 `default` / `source`。

玩家選擇存在 `settings.json` 的 per-pack `locale_preferences`。GUI v1 只在
標題畫面開啟的 Settings 允許切換，避免 active dialogue 的頁碼、指令、
history 與 save/resume 呈現被半途改寫。選中後會更新呈現文案及語系字型。
細節、抽取指令、digest/parity 規則與目前限制見
[theme-and-locale.md](theme-and-locale.md#locale在地化)。

詳細欄位：

- `title_screen` 是嚴格 schema（`extra: forbid`）。`layout`、
  `panel_style`、`name_entry` 與 `wordmark_style` 只能使用上列值；
  `background_opacity` 必須介於 `0.0` 和 `1.0`。
- 未提供 `logo_image` 時，標題畫面會以 `title`、`subtitle` 和
  `wordmark_style` 排出文字標誌。
- `ui_skin` 是嚴格 selector schema（`extra: forbid`）；未知 renderer、
  缺少 `ui_chrome/v1` protocol 宣告都會是 validator error。`options`
  由 renderer plugin 擁有；若該 class 公開 Pydantic `OptionsModel`，
  validator 也會檢查 plugin-specific 欄位。
- UI skin 只替換 backdrop、panel、divider、button、card、footer 與 focus
  chrome。場景幾何、functional/control/status 文案、localization、disabled、
  最小操作區、焦點與輸入順序仍由引擎控制。Pack renderer 可畫靜態裝飾微文案，
  但它不得是操作、狀態或錯誤資訊的唯一來源。
- 共用 `Button` 的 `variant` 固定為 `<role>:<style>`。Save/Load 捲動列的
  `button/action` 是手動 hit-test action chip，不是共用 `Button`；
  `card/new|slot|special`、`footer/hint|success|error`、
  `panel/confirmation`、`backdrop/modal` 分別表達存檔列、狀態與確認層。
  Menu、Settings、Gallery 的提示底板使用 `footer/hint`；Map 與 Travel
  也透過各自的 `screen` 共用同一個 pack chrome。完整穩定矩陣與
  相容政策見
  [theme-and-locale.md](theme-and-locale.md#ui_chromev1-request-matrix)。
- v1 內既有 matrix tuple 的名稱與語意保持相容，但可能增加新的 screen、part、
  variant、Button role/style 或狀態組合；renderer 對未知組合應回傳 `None`。
  缺少 `ui_skin`、renderer 對某個 part 回傳 `None`，或 renderer 被故障隔離時，
  對應 UI 走當版 engine 的 stock fallback 行為。這不承諾跨版本、平台、字型、
  locale 或解析度 byte-for-byte 相同。
- `logo_image`、`title_bg` 與 `title_bgm` 都必須是 pack 內的安全相對
  路徑，不能使用絕對路徑、URL、反斜線或 `..` 跳出 pack。validator
  會回報缺檔；圖片還會檢查 pygame 能否解碼。
- `travel.scope: graph` 啟用確認後才提交的原子遠距行程。
- `travel.minimum_cost` 是整次地圖選擇的最低時段成本，不會對每個中繼點重複收費。
- `travel.spatial_map` 是嚴格 Pydantic schema（`extra: forbid`），只控制呈現，
  不改變世界圖、可達性、預覽或確認語意。缺少整個 block 時會完整保留舊版地圖。
  Authoring 工具可從 `capabilities --schema` 的 `SpatialMapSpec`，或 capability
  manifest 的 `metadata_schema["travel.spatial_map"]` 取得機器可讀 JSON Schema。
- 遊戲內的地圖操作會先開啟 `MapScene`。啟用 `guided_story` 時預設只顯示
  故事標記，玩家可用至少 44px 的「顯示一般探索」切換目前可達的普通地點。
- 地圖 chrome、故事標記、校外卡與命中框會依 720p 基準密度縮放；標題、提示、
  按鈕與地圖區域使用實際字型高度排版，720p／1080p 不會互相覆蓋。
- 普通地點使用小型 pin 降低密集地圖雜訊，但保留至少 44×44 的透明命中框。
  完整名稱與說明只在滑鼠 hover 或鍵盤／手把 focus 時，顯示於固定且有界的資訊列；
  故事與目前位置則保留有文字的高層級標記。
- `spatial_map.bounds` 為 `[min_x, min_y, max_x, max_y]`，預設
  `[0, 0, 100, 100]`。啟用插畫地圖後，未列入 `edge_destinations` 的可見地點
  會依固定 bounds 放到中央畫布，因此 guided story 標記增減時不會重新縮放漂移。
- `nodes` 以既有 location id 為 key，補充校園總覽的呈現階層。
  `layer: campus` 是預設中央層；`interior` 必須有一個存在的 `parent`，平時不會
  塞進全校總覽，只有玩家站在該 parent、該室內地點正好有故事，或它是目前位置時
  才顯示。`story_only` 只在目前位置或有正式 guided offer 時出現，不會混成一般
  探索。`parent` 不可指向自己或形成循環；`label` 會覆寫地圖上的短標籤，但不會
  改寫 `locations.yaml` 的正式名稱。這些欄位本身永遠不能解鎖地點、繞過條件，
  也不取代 `hidden_tags`。
- `auto_open_after_scenes` 是 scene id 清單；只有對話正常結束時才排入一次自動開圖，
  不會改變該 scene 的效果、旅行成本或抵達事件。validator 會檢查 id 與重複值。
- `edge_destinations` 以 location id 為 key；每張卡至少 44px，可用滑鼠、觸控、
  方向鍵／WASD 與手把聚焦。點擊或 Enter 仍只開啟既有行程預覽，確認前不會移動
  或消耗時間。校外卡在故事優先模式仍會顯示，但不會因此被偽裝成主線事件；
  可達狀態仍由同一個 `TravelService` 精確計算。
- `background`、`thumbnail` 缺檔時會退化成靜態主題底板，不會中斷遊戲。
  validator 會拒絕絕對路徑、URL、反斜線與 `..`，並檢查檔案是否存在及能否解碼。
  top-level `attribution` 會固定顯示於中央畫布右側並避開資訊列；edge 自己的
  `attribution` 則會在對應縮圖卡內獨立顯示。每個有 `thumbnail` 的校外目的地
  必須提供自己的 `attribution`，或由 top-level `attribution` 統一涵蓋。
  原創圖片也應明確標示為原創。
- validator 會確認 `nodes`、`edge_destinations`、`parent` 都指向真實 location，
  並檢查中央節點有明確、有限且位於 `bounds` 內的 `map_x`／`map_y`。缺少座標或
  超界是可降級的 warning；不安全資產路徑、未知 id、parent cycle 與 schema
  錯誤則是 error。
- `travel.guided_story` 啟用故事優先的目的地層：目前／稍後可觸發的 `enter`、`auto`
  scene hook 進入主層，一般地點留在「顯示一般地點」次層。
- `auto_reopen: true` 只在同一 `guided_story` 宣告了有限 `progress`、
  且當前進度尚未完成時生效。對話正常結束後，應用會先尊重明示
  `spatial_map.auto_open_after_scenes`、同地點 `enter` / `auto` hook 與
  ambient 對話；只有都沒有接手、也沒有 modal overlay 時才回 `MapScene`。
  這是呈現交接，不設旗標、不改變可達性或旅行成本。
- `direct_before_graph: true` 是嚴格 opt-in。當 `travel.scope: graph` 已宣告、但
  `requires_flags` 尚未全數成立時，resolver 只能為當前地點既有的單段 exit
  建立指引，不會穿越中繼節點；此時 `minimum_cost` 固定不套用，但該 exit 自己的
  `travel_cost`、時間與旗標條件仍完整生效。旗標全數成立後會自動恢復完整 graph
  路徑與 authored `minimum_cost`。省略此欄位或填入非布林值時維持舊行為：
  graph gate 打開前不啟用 guided story。
- `future_horizon_phases` 只產生等待建議，不會把等待偷偷塞進 `TravelPlan`。
  Destination detail 會把完整差距顯示成「等待至第 N 天某時段（共 N 時段）」；
  玩家只確認一次，但引擎仍逐格呼叫原有單時段等待。若某格觸發 ambient
  對話或等待被禁止，批次立即中斷，不會跳過劇情邊界。
- `max_primary` 是 authoring budget；runtime 不會為了湊上限而隱藏必要事件。
- `require_marker: true` 會把 `markers` 當成主層 allowlist；未列出的支線仍可在
  次層地點列表正確觸發，但不會淹沒本輪 1–3 個主線選項。
- `progress.stages` 是純呈現用的有序指南進度。`counted_flags` 決定畫面上的
  分子／分母（例如 `1 / 3`），`completion_flags` 則讓階段等到 authored
  wrap／aggregate 真正完成後才切到下一輪；舊 pack 只寫 `flags` 時，兩者都會
  以 `flags` 相容處理。進度本身不會設定旗標、解鎖地點或取代 scene/hook 的
  正式條件。
- `markers` 以既有 `scene_id` 覆寫非劇透 teaser 與角色標記，不複製流程條件。

## scenes：敘事日期 HUD

回憶、遠景或後日談有時需要顯示「一年後」，但不應為了畫面文字而真的推進
`GameState.time`、污染存檔與地圖排程。可在既有 scene `tags` 使用下列純呈現契約：

```yaml
- id: one_year_epilogue
  title: "後日談"
  tags:
    - "hud-date:畢業後約一年 · 初夏午後"
  lines:
    - text: "【畢業後約一年】"
```

- `hud-date:<label>`：以非空的 `<label>` 取代 dialogue 左上角的 live calendar；
  不推進日期、時段、weekday 或地圖狀態。
- `hud-hide-date`：只隱藏日期列；章節標題若存在仍會顯示。
- scene 沒有上述 tag 時維持既有 `TimeSystem.label()`。空白 `hud-date:` 也安全
  回退 live calendar。

這些 tag 不改 headless transcript、條件、effect、route 或 save schema；舊存檔
載入後會依其保存的 scene tags 呈現。需要真正改變世界時間時仍應使用正式 time
effect，不可把 HUD label 當成狀態。

## scenes lines：穩定在地化 identity

Line 可選擇宣告 `loc_id`：

```yaml
lines:
  - loc_id: arrival_gate
    speaker: "{player_name}"
    text: "我抬頭看著校門。"
```

`loc_id` 必須符合 `[A-Za-z0-9][A-Za-z0-9_-]{0,63}`，且同一 scene 內不得
重複。它是 line 在 translation catalog、Read／Skip、Backlog、resume 與
Scene Replay 中的穩定 identity；不會改變 authored line 次序、effects 或
gameplay。上例在 scene `prologue` 的文案鍵是
`prologue.line.arrival_gate`；若省略，舊 pack 會以 zero-based index 作為
fallback，例如 `prologue.line.0`。Index 鍵可正常執行，但在前方插入一行
會使翻譯、已讀與持久化位置一起漂移；正式 pack 應逐行補上 `loc_id`。

已經發行的 pack 不能只在 YAML 補欄位：必須 bump `pack_format_version`，並以
pack save migration 把既有 read keys、serialized story cursor、history、resume
與 replay 一次轉成相同 identity。具有 `loc_id` 的 runtime line 只認 stable key，
不會再以舊 index 做 OR fallback；否則新插入行仍會誤繼承原位置的已讀狀態。完整
規格與 edit matrix 見 [stable-line-identity.md](stable-line-identity.md)。

## scenes lines：語意聲音字幕

重要的環境聲、音效、音樂變化或刻意留白不能只存在於音檔。可在一行加上
`sound_captions`，讓靜音、聽覺受限或當下沒聽清楚的玩家仍能取得同一份敘事資訊：

```yaml
lines:
  - text: "我翻到新生手冊的下一頁。"
    sfx: assets/audio/sfx/page_turn.ogg
    sound_captions:
      - {kind: sfx, text: "紙頁翻動。"}
  - text: "門後，什麼都沒有。"
    sound_captions:
      - {kind: silence, text: "門後沒有鋼琴聲。"}
```

每行最多四筆；每筆只接受以下嚴格欄位：

| 欄位 | 規則 |
|---|---|
| `kind` | `ambient`、`sfx`、`music` 或 `silence` |
| `text` | 必填純文字；去除首尾空白並正規化行內空白後須為 1–240 字，不接受控制字元 |

240 字是 persistence／hostile-data 的上限，不是建議的畫面文案長度。Live overlay 應以
「類型標籤加一個短句可在單行讀完」為目標；在四筆 cue 與 360p 同時出現時，過長文案
會保留開頭並省略尾端，完整 typed 內容仍留在 Backlog、Replay 與 headless。必要資訊請
先寫，不能簡短表達的分支條件或操作說明應放回正文。

字幕是靜態、作者確認過的語意，不會從檔名自動生成，也不做 `{variable}` 插值。
它可以和 `sfx`／`ambient`／`bgm` 同時使用，也可獨立描述「音樂突然停止」或有意義的
沉默；反過來，它不會播放音檔、改變 mixer 狀態或證明資產真的存在。請只描述玩家
在這一刻實際應聽見或明確沒聽見的內容，不要先替尚未交付的配音與 Foley 寫成已發生。

字幕跟隨同一個 logical line，不增加對話頁或多吃一次 Enter。ADV、NVL、存讀檔、
rollback、Backlog、場景重溫與 headless presentation 都保存同一份 typed 資料；舊 pack、
舊存檔與沒有字幕的行維持空清單。玩家可在設定的「聲音字幕」關閉顯示，author 不應
用字幕取代正文中必須讓所有玩家知道的分支條件或操作指示。

存檔中的 resume／Replay 是可由玩家修改的資料，因此有欄位時必須與對應 authored
line 的正規化字幕完全相同；替換、刪除、重排或非 canonical 空白都會 fail closed。
早期 v1 resume 若完全沒有 `sound_captions` key，載入時會由該存檔的 story line 補回，
維持舊存檔相容而不讓缺欄位變成關閉 accessibility cue 的手段。

## scenes choices：可比較的 visual card

`Choice.text` 永遠是功能語意、headless、無障礙與舊版列表 renderer 的權威標籤。
需要在重大決策同時比較人物、方案或目的地時，可額外 author 嚴格 typed 的
`card` presentation：

```yaml
choices:
  - id: follow_archivist
    text: "翻到檔案員那一頁：陪她查完沒有署名的紀錄。"
    card:
      title: "檔案員"
      subtitle: "校史館志工 · 還有一個學年"
      badge: "時間有限"
      image: assets/characters/archivist/smile.png
      image_alt: "檔案員抱著紅色精裝書的半身立繪"
      image_fit: cover
      image_focus_y: 0.34
      image_zoom: 1.1
      accent: [74, 150, 176]
      details:
        - {label: "日常", text: "整理校刊與修理影印機。"}
        - {label: "異常", text: "書架後的歌聲會在你靠近時停下。"}
        - {label: "承諾", text: "陪她把自己的名字寫進故事。"}
      tags: ["校刊", "無名筆記本", "畢業以前"]
      action: "翻到她那一頁"
    next_scene: confirm_archivist
```

| 欄位 | 規則 |
|---|---|
| `title` | 必要，1–64 字；卡片主標題 |
| `subtitle` / `badge` | 選填，分別為次標與短時間／狀態標籤 |
| `image` / `image_alt` | 圖片選填；有圖片時替代說明必要。圖片必須是安全的 pack 相對路徑 |
| `image_fit` | `cover`（預設）或 `contain` |
| `image_focus_y` | `0.0..1.0` 的有限數值；`0` 靠頂、`0.5` 中央、`1` 靠底 |
| `image_zoom` | `1.0..2.0` 的有限數值；只用來收緊 card cover crop |
| `accent` | 選填 RGB 三整數；只作身份裝飾，不能成為唯一的狀態提示 |
| `details` | 最多三列，每列是短 `label`＋可換行 `text` |
| `tags` | 最多三個不重複短標籤 |
| `action` | 選填短動作；省略時 UI 回退到 `Choice.text` |

卡片版型是明確的集合合約：同一 choice point 必須有 2–4 個可見選項，而且每個都
提供 `card`，才使用等高 comparison cards。mixed、單選、五個以上或完全沒有
`card` 時走既有直向列表，不會因「剛好三個選項」猜測作者意圖。720p／1080p 使用
橫向等寬卡；短畫面使用 compact 卡。整張卡都是至少 44px 的 hit target，順序仍是
YAML authored order；多選初始不聚焦，方向鍵／WASD／手把／滑鼠指定後才可確認，
disabled 卡永遠不可聚焦或提交，並保留可見原因。

`card` 只改 presentation，不會設定 flag、消耗資源、套用 effect 或跳過原有
`next_scene`。重大不可逆選擇仍應像上例先進確認 scene，只有確認 choice 才寫入
狀態。Renderer 只畫 `choice/card/option_card` chrome；圖片、文字、disabled veil、
焦點框與 hit-test 都由 core 擁有。

Validator 會嚴格拒絕 nested typo、超長／空白文案、錯誤 RGB、URL、絕對路徑、
反斜線及 `..`；crop 數值不接受 bool、數字字串、NaN 或 infinity。缺圖或無法
解碼是可見 warning，runtime 以 placeholder 降級。
`ChoiceOption`、rollback、save resume、headless presentation 與
`affordances()` 都攜帶同一份 card JSON。存檔雖保存完整 `StoryGraph` 供 schema
round-trip，但載入時 scene／line／choice／條件／effect／轉場與 card 都以目前 pack
或本次 runtime 明確註冊的 authored catalogue 為準；存檔只帶回已知 scene 的
`played` 與經驗證的 `current_scene + current_line_index`。退役 `played` id 會移除；
active scene 已刪除或 cursor 超界時拒絕整次載入，pack migration 必須先更新 id／
cursor。Backlog 保存的 voice 也只保留目前 authored line voice allowlist 內的路徑。

### verification.expected_unreachable

只有在舊存檔、鑑賞重播或 agent 明確 `start_scene` 仍需要一段退役內容，而正常
新遊戲刻意不應再到達它時，才使用這個 SelfCheck 例外。它不影響 runtime，
也不改寫 `PackInspector.dead_ends()`／`reachability()` 的原始輸出。

`expected_unreachable` 是以可讀 case id 為 key 的 mapping。每個 case 的 schema
固定為：

| 欄位 | 型別 | 規則 |
|---|---|---|
| `scenes` | `list[str]` | 選填；每個值必須是 `scenes/*.yaml` 中存在的 scene id |
| `endings` | `list[str]` | 選填；每個值必須是 `endings.yaml` 中存在的 ending id |
| `reason` | `str` | 必要；trim 後不可為空，說明為何必須保留且不可由新遊戲到達 |

`scenes` 與 `endings` 至少要有一個非空清單；target 不可空白或重複，case 不接受
其他欄位。SelfCheck 採 fail-closed：

- scene 只有在 `PackInspector` 目前仍判定為 unreachable 時，才從
  `dead_ends` 的 blocking items 移到 `expected`／`ignored`。
- ending 只有在 `EndingReachabilityChecker` 目前仍判定為 `strand` 時，才從
  blocking strands 移到 `expected`／`ignored`。
- 拼錯 id、把 location id 填進 `scenes`、把 scene id 填進 `endings`、空 reason
  或錯誤形狀都是 validator error。
- 原本退役的 target 若重新變成 reachable／`ok`／`unverified`，就是 stale
  exception；validator 會警告，對應 SelfCheck stage 會失敗，要求移除例外或修正
  意外接回的入口。
- 例外只忽略同 target 的 `unreachable`／`strand` finding；同一 scene 若另有
  no-op choices 等真實 dead-end，仍會阻擋驗證。

- [theme-and-locale.md](theme-and-locale.md) — theme / locale 區塊
- [resources.md](resources.md) — resources 區塊
- [locations.md](locations.md) — locations / regions / exits 完整 schema

## cg_gallery.yaml（選填）

正式作品可用靜態 manifest 把 CG 的人讀名稱、排序與敘事關聯從檔名中抽離：

```yaml
entries:
  - asset: assets/cgs/library_encounter.png
    title: "最上層的紅皮書"
    subtitle: "初遇 · 總圖書館"
    description: "她替你扶住差點落下來的書。"
    order: 110
    hidden: false
    thumbnail: assets/cgs/thumbnails/library_encounter.webp
    character_ids: [librarian]
    chapter_id: ch1_arrival
```

也支援下列兩種等價 authoring shape：

```yaml
# top-level list
- asset: assets/cgs/library_encounter.png
  title: "最上層的紅皮書"
```

```yaml
# compact asset-keyed mapping
assets/cgs/library_encounter.png:
  title: "最上層的紅皮書"
```

離線工具應讀 `capabilities --schema` 的
`content_files["content/cg_gallery.yaml"]`；該 union schema 同時描述 wrapper、
list 與 compact mapping。正式檔名必須精確為小寫
`content/cg_gallery.yaml`，避免不同平台的大小寫行為不一致。

| 欄位 | 型別 | 說明 |
|---|---|---|
| `asset` | str（必要） | 安全的 pack 相對圖片路徑；也是存檔內既有的 CG unlock key |
| `title` | str（必要） | 解鎖後顯示的人讀標題；不可空白 |
| `subtitle` | str | 卡片與 viewer 的短情境 |
| `description` | str | viewer 的回憶說明；UI 會依解析度限行、省略 |
| `order` | int | 排序鍵，預設 0；同值保留 YAML 宣告順序 |
| `hidden` | bool | 預設 false；true 時未解鎖不顯示、不計入可見總數 |
| `thumbnail` | str / null | 選填的 grid 圖片；全螢幕仍使用 `asset` |
| `character_ids` | list[str] | 選填；每個值必須存在於 `characters.yaml` |
| `chapter_id` | str | 選填；非空時必須存在於 `chapters.yaml` |

這是純靜態 metadata，不進存檔，也不需要 pack migration。`asset` 仍是 identity，
但會先正規化首尾空白、反斜線與 `.` 路徑段，因此等價 alias 會被視為 duplicate：
scene / line 的 `cg` 和 `show_cg.target` 顯示到它時，既有
`GameState.cg_gallery.unlocked` 只記該路徑。

- 檔案缺省：維持 legacy `assets/cgs/` 第一層掃描與 filename caption。
- 檔案存在（即使 `entries: []`）：manifest 是正式 roster；未宣告的磁碟檔不進圖鑑。
- 舊存檔／plugin 已解鎖但 manifest 未宣告的 path 仍附加成 fallback entry。
- 鎖定卡不會透過 UI 或 runtime `describe()` 洩漏 title、角色、章節或描述。
- 存檔載入會重新綁定目前 pack 的 ending definitions；已讀、ending unlock 與 CG
  unlock 仍保留玩家進度，退役 id 不會把完成度推到 100% 以上。

完整的玩家操作、fullscreen metadata 與向下相容規則見
[presentation-and-extras.md](presentation-and-extras.md#cg-鑑賞cg-gallery)。

## music_room.yaml（選填）

正式作品可用靜態 manifest 把 BGM 的標題、作者 credit、劇情用途、排序與
spoiler 可見性從檔名中抽離：

```yaml
entries:
  - asset: assets/bgm/library_theme.ogg
    loc_id: library_theme
    title: "The Book on the Highest Shelf"
    artist: "Composer Name"
    usage: "圖書館 · 共通線"
    order: 110
    hidden: false
```

和 CG catalog 相同，也支援 top-level list 與 compact asset-keyed mapping：

```yaml
# top-level list
- asset: assets/bgm/library_theme.ogg
  title: "The Book on the Highest Shelf"
```

```yaml
# compact asset-keyed mapping
assets/bgm/library_theme.ogg:
  title: "The Book on the Highest Shelf"
  artist: "Composer Name"
```

離線工具應讀 `capabilities --schema` 的
`content_files["content/music_room.yaml"]`；該 union schema 同時描述 wrapper、
list 與 compact mapping。正式檔名必須精確為小寫
`content/music_room.yaml`，避免不同平台的大小寫行為不一致。檔案不得超過 2 MiB，
最多 4096 筆，未知欄位會被拒絕。

| 欄位 | 型別 | 說明 |
|---|---|---|
| `asset` | str（必要） | 安全的 pack 相對 `.ogg`／`.mp3`／`.wav` 路徑；也是存檔與 ClearData 的解鎖 identity |
| `loc_id` | str / null | 選填的穩定翻譯鍵片段；須符合 `[A-Za-z0-9][A-Za-z0-9_-]{0,63}`，且不可重複 |
| `title` | str（必要） | 解鎖後顯示的人讀標題；trim 後不可空白，最多 160 字 |
| `artist` | str | 作者／演奏者 credit，最多 160 字 |
| `usage` | str | 簡短的劇情使用情境，最多 240 字 |
| `order` | int | 排序鍵，預設 0；同值保留 YAML 宣告順序 |
| `hidden` | bool | 預設 false；true 時未解鎖不顯示、不計入可見總數 |

`asset` 會正規化反斜線與 `.` 路徑段；絕對路徑、`..` traversal、控制字元、
不支援的副檔名及正規化後重複的 asset 都是 error。`loc_id` 是產品文案的穩定鍵，
不是另一個解鎖 id：runtime 查詢
`music_room.<loc_id>.title`、`.artist`、`.usage`。省略時會由 canonical asset 的
SHA-256 摘要產生穩定 fallback segment，舊 catalog 仍可翻譯；若預期日後改檔名，
應明寫 `loc_id`，避免翻譯 key 隨 identity 改變。

這是靜態 metadata，不進存檔，也不需要為標題或 credit 變更做 save migration。
`GameState.music_room.unlocked` 與跨周目的 `clear_data.json` 只保存已聽過的 canonical
asset identity：

- 檔案缺省：維持 legacy `assets/bgm/` 第一層掃描、路徑排序與 filename label。
- 檔案存在（即使 `entries: []`）：manifest 是鎖定曲目的正式 roster；未宣告的磁碟檔
  不會因目錄掃描自動變成鎖定列。
- `hidden: false` 的未解鎖曲目顯示「？？？」與解鎖提示，不洩漏 title／artist／usage；
  `hidden: true` 的未解鎖曲目完全不顯示，聽過後才按原排序加入。
- 目前 pack 仍授權、但 catalog 未宣告的已解鎖曲目會附加成 filename fallback，避免
  採用 manifest 或調整 roster 時抹掉合法舊進度。
- load、quickload、新周目與 ClearData 套用都會先和目前 StoryGraph、manifest、
  `assets/bgm/` 及啟動時明確註冊項目建立的 media authority 取交集；退役、手改、
  絕對或 traversal path 不能藉存檔／ClearData 取得 decoder 權限。

`world-gal-game validate <pack>` 會驗證 schema、大小寫、duplicate `asset`／`loc_id`、
安全相對路徑、音訊檔存在與副檔名，並警告 story、`title_bgm` 或 `assets/bgm/` 已使用／
收錄但未進 catalog 的曲目。完整玩家操作與音訊 ownership 契約見
[presentation-and-extras.md](presentation-and-extras.md#音樂室music-room)。

## endings.yaml（選填）

宣告遊戲的結局；顯示在「結局與完成度」鑑賞 overlay 裡，依 `route_id` 分組。
每個結局靠 `requires` 條件解鎖 —— 慣例是綁一個在路線收尾場景 `on_end` set 的
`ending_*` flag。檔案結構與 `achievements.yaml` 對齊（`requires` / `forbids` 走標準
condition 載入器，任何 condition kind 都能用）。

```yaml
endings:
  - id: ending_lover
    title: "結局 · 戀人"
    description: "與林清雪的故事，走到了戀人結局。"
    route_id: heroine_1          # 用 heroine 的 route_id 分組（可選）
    requires:
      - {kind: flag, target: ending_lover}

  - id: ending_secret
    title: "隱藏結局"
    hidden: true                 # 未解鎖時不出現在清單（解鎖後才顯示）
    requires:
      - {kind: flag, target: ending_secret}

  - id: ending_alone
    title: "結局 · 一個人"
    description: "離開了廣場，沒有再回頭。"
    # 沒有 route_id → 落在「其他」分組
    requires:
      - {kind: flag, target: ending_alone}
```

每個 ending 的欄位：

| 欄位 | 型別 | 說明 |
|---|---|---|
| `id` | str（必要） | 唯一識別 |
| `title` | str | 顯示名稱 |
| `description` | str | 解鎖後顯示的描述 |
| `icon` | str | 選填圖示路徑（`assets/...`） |
| `route_id` | str | 分組用；對應標了 `is_heroine` + `route_id` 的 NPC 名字 |
| `hidden` | bool | `true` = 未解鎖時不進清單 |
| `requires` | condition list | 全部成立才解鎖（典型是 `ending_*` flag） |
| `forbids` | condition list | 任何一個成立就不解鎖 |

路線收尾場景照常 set flag：

```yaml
on_end:
  - kind: set_flag
    target: ending_lover
    value: true
  - kind: end_scene
```

完成度（劇情已讀 / 結局 / CG）由引擎自動計算，作者不用做任何事。玩家視角、分組與
完成度的細節見 [presentation-and-extras.md](presentation-and-extras.md)。

## Line 演出 effect（鏡頭 / 畫面特效）

除了一般 effect，引擎內建五個**演出 effect**，寫在某一行 line 的 `effects:` 裡，
line 演到時觸發。它們不碰存檔、不碰 pygame，只把指令排進場景的視覺佇列：

| kind | `value`（dict） |
|---|---|
| `camera_pan` | `{x, y, duration?, easing?}` |
| `camera_zoom` | `{scale, duration?, easing?}` |
| `screen_shake` | `{intensity?, duration?, easing?}` |
| `screen_flash` | `{color:[r,g,b]?, duration?, max_alpha?, easing?}` |
| `screen_tint` | `{color:[r,g,b]?, duration?, max_alpha?, persist?, clear?, easing?}` |

```yaml
- speaker: "林清雪"
  text: "「下次。」"
  effects:
    - kind: camera_zoom
      value: {scale: 1.12, duration: 0.9}
```

完整簽章、預設值、`screen_tint` 的 clear 用法見
[presentation-and-extras.md](presentation-and-extras.md)。權威清單（含外掛新增的）跑
`world-gal-game capabilities --pack <pack>`。

## 引擎在哪裡找 pack？

```
1. <engine-repo>/games/<name>/           — 隨引擎散布的內建 pack
2. <engine-repo>/../<name>/              — 兄弟資料夾（建議）
3. ~/.world-gal-game/packs/<name>/        — 使用者本機 pack 緩存
4. --pack <abs/rel-path>                 — 直接指定
```

`<name>` 會自動嘗試 snake_case、kebab-case、Title-Case 變體。
所以 `--pack my_game_pack` 也會找到 `My-Game-Pack/`。

## 讓 pack 變成 pip-installable

如果你想把 pack 也包成可 `uv add` 的東西（給玩家或 CI 用）：

`pyproject.toml`：

```toml
[project]
name = "my-game"
version = "0.1.0"
requires-python = ">=3.10"
dependencies = ["world-gal-game>=0.1.0"]

[project.scripts]
my-game = "my_game.entry:main"

[tool.uv.sources]
# 本地開發指向 sibling 引擎源碼；未來上 PyPI 後可拿掉這段。
world-gal-game = { path = "../World-Gal-Game", editable = true }

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["my_game"]
include = ["content/**/*", "assets/**/*"]

[tool.hatch.build.targets.wheel.force-include]
"content" = "my_game/content"
"assets" = "my_game/assets"
```

`my_game/__init__.py` + `my_game/entry.py`（薄殼）：

```python
# my_game/entry.py
from pathlib import Path
import sys


def _pack_root() -> Path:
    here = Path(__file__).resolve().parent
    if (here / "content" / "meta.yaml").exists():
        return here          # wheel install
    return here.parent       # source checkout


def main(argv: list[str] | None = None) -> int:
    pack_root = _pack_root()
    forwarded = list(argv) if argv is not None else sys.argv[1:]
    if "--pack" not in forwarded:
        forwarded = ["--pack", str(pack_root)] + forwarded
    from world_gal_game.cli import main as engine_main
    return engine_main(forwarded)


if __name__ == "__main__":
    sys.exit(main())
```

之後使用者只要：

```bash
uv pip install my-game
my-game             # console script 直接啟動，引擎自動載這個 pack
```

範例見 repo 內建的 `games/demo_pack/`，或任何放在 sibling 目錄的外部 pack。

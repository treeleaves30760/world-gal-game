# Theme & Locale

每個 pack 可以在 `meta.yaml` 蓋掉引擎的預設配色、字型、UI 文字、
好感度等級命名。不蓋的欄位用引擎內建中文預設。

## Theme（配色 / 間距 / 圓角）

`meta.yaml`：

```yaml
theme:
  # 主色 — 標題、選中按鈕、強調文字
  accent:      [216, 80, 143]      # RGB 或 RGBA
  accent_alt:  [107, 107, 255]
  accent_warm: [240, 198, 116]     # 時間、好感、資源數字

  # 背景 / panel
  bg_deep:    [13, 10, 20]
  bg_panel:   [20, 16, 36, 235]
  bg_overlay: [8, 4, 18, 240]

  # 文字
  text:      [243, 233, 255]
  text_mute: [179, 159, 204]
  text_dim:  [110, 100, 130]
  good:      [110, 215, 154]
  warn:      [255, 118, 118]

  # 框線
  border:        [216, 80, 143, 130]
  border_soft:   [255, 255, 255, 40]
  border_strong: [216, 80, 143, 220]

  # 間距與圓角（整數，像素）
  pad_xs: 4
  pad_s:  8
  pad_m:  14
  pad_l:  22
  pad_xl: 32
  radius_s: 6
  radius_m: 10
  radius_l: 16
```

不寫的鍵保留引擎預設。

預設配色是「sakura pink + spectral indigo + lantern amber on deep purple」—
適合夜晚 / 校園鬼故事氛圍。你的遊戲是冷酷反烏托邦？把 `accent` 改成
`[80, 200, 255]`、`accent_warm` 改成 `[255, 90, 90]`、`bg_deep`
改成 `[5, 5, 8]` 就行。

## UI skin（pack plugin 系統框架）

`theme` 只適合共用色彩、間距與圓角。需要更換系統畫面的框線、卡片、按鈕底板
或遮罩時，不要把新主題寫進 engine；由 pack plugin 註冊一個
`ui_chrome/v1` widget，再從 `meta.yaml` 選取：

```yaml
ui_skin:
  renderer: my_pack_system_chrome
  options:
    line_weight: 2
    corner_length: 14
```

```python
import pygame
from pydantic import BaseModel, ConfigDict

from world_gal_game.plugins import widget


class ChromeOptions(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    line_weight: int = 2
    corner_length: int = 14


@widget(
    "my_pack_system_chrome",
    description="Pack-owned system chrome.",
    signature={"protocol": "ui_chrome/v1"},
)
class MyPackSystemChrome:
    OptionsModel = ChromeOptions

    def __init__(self, *, options, assets, fonts, theme):
        self.options = self.OptionsModel.model_validate(options)
        self.theme = theme

    def render(self, request):
        out = pygame.Surface(request.size, pygame.SRCALPHA)
        # 只畫 request 指定的 backdrop / panel / divider / button /
        # card / footer / focus；不改幾何或輸入。
        return out
```

未宣告 profile 的 renderer 只支援 `standard`，因此既有插件不需要修改，
也不會在玩家開啟高對比介面時意外重用標準模式的半透明素材。若 renderer 已實作
兩種視覺語意，必須明確宣告：

```python
@widget(
    "my_pack_system_chrome",
    description="Pack-owned system chrome.",
    signature={
        "protocol": "ui_chrome/v1",
        "visual_profiles": ["standard", "high_contrast"],
    },
)
class MyPackSystemChrome:
    def render(self, request):
        if request.visual_profile == "high_contrast":
            # 使用不透明文字平面、至少 3:1 的控制邊界，並以形狀／線條／
            # 紋理重複 focused、selected、disabled 等狀態。
            ...
```

`ChromeRequest.visual_profile` 是有限值 `standard | high_contrast`，也是
surface cache identity 的一部分。玩家在設定頁切換後，既有 scene 下一幀就會
送出新 profile；切回標準模式時可安全重用原本的標準 cache。renderer 未宣告
高對比、針對該 profile 回傳 `None`，或隔離層捕捉到錯誤時，core 會使用 stock
高對比 chrome；不會停用仍然有效的標準 profile，也不會對背景、CG 或立繪套
濾鏡。標準模式仍使用 pack 原始 Theme 與歷史 render path。

高對比模式的最低契約是：一般必要文字至少 4.5:1、閱讀正文目標 7:1、focus／
控制邊界至少 3:1（皆以實際 alpha compositing 後結果計算），且 focused、
selected、disabled、good／warn、地圖方向與路線身分不能只靠色相。任意 authored
rich-text 色彩會在 ADV／NVL 高對比閱讀平面上暫時收斂為白字；切回標準模式即
恢復，不會重設頁碼、打字游標或 inline wait。

### ui_chrome/v1 request matrix

`part`、`screen`、`variant` 是語意識別，不是版面座標，也不是要求 renderer
重做整個 scene。以下是 v1 目前穩定、由 system screens 與 reading loop 發出的
組合：

| `screen` | `part` | 穩定的 `variant` | 語意 |
|---|---|---|---|
| `onboarding` | `backdrop` | `overlay` | 首次閱讀入門卡後方遮罩 |
| `onboarding` | `panel` | `main` | 入門卡主容器 |
| `onboarding` | `button` / `focus` | `start:primary` | 唯一的「開始閱讀」動作；進場即由 core 聚焦 |
| `dialogue` | `panel` | `main_adv`、`main_nvl` | ADV 對話框與 NVL 閱讀面板 |
| `dialogue` | `panel` | `sound_caption` | 目前台詞的高對比聲音字幕底板；文字與可見性仍由 core 控制 |
| `dialogue` | `card` | `nameplate`、`status`、`mode` | 姓名牌、章節／時間狀態與 AUTO／SKIP 狀態底板 |
| `dialogue` | `panel` | `quick_primary`、`quick_overflow` | 主要閱讀工具列與「更多」展開列 |
| `dialogue` | `button` | `quick_primary:<style>`、`quick_overflow:<style>` | 工具列動作；目前使用 `primary` / `ghost` 表達 active 與一般狀態 |
| `dialogue` | `footer` | `advance` | 文字完整顯示後的繼續提示底板 |
| `choice` | `backdrop` | `overlay` | 選項出現時的閱讀畫面遮罩 |
| `choice` | `panel` | `main`、`route_cards` | 直向選項與 visual-card 比較主容器 |
| `choice` | `button` / `focus` | `option:primary`、`option:ghost` | 可用／不可用選項與條件式 focus chrome |
| `choice` | `card` / `focus` | `option_card` | 2–4 個 typed choice cards 的單卡底板與條件式 focus chrome |
| `menu` | `backdrop` | `overlay` | 遊戲內選單後方遮罩 |
| `menu` | `panel` | `main` | 選單主容器 |
| `menu` | `divider` | `header` | 標題區分隔線 |
| `menu` | `button` / `focus` | `close:ghost`、`tab:primary`、`tab:ghost`、`action:primary`、`action:ghost`、`action:danger` | 關閉、分類頁籤與選單動作 |
| `menu` | `footer` | `hint` | 控制提示的底板；提示文字仍由 core 畫 |
| `save` / `load` | `backdrop` | `overlay`、`modal` | 主畫面遮罩與確認層遮罩 |
| `save` / `load` | `panel` | `main`、`confirmation` | 主容器與確認對話框 |
| `save` / `load` | `divider` | `header` | 標題區分隔線 |
| `save` / `load` | `card` | `new`、`slot`、`special` | 新存檔列、一般存檔列、quick/autosave 列 |
| `save` / `load` | `button` | `action`；`close:ghost`；確認層的 `action:danger` / `action:primary` / `action:ghost` | `action` 是捲動列內手動 hit-test 的動作 chip；其餘是共用 `Button` |
| `save` / `load` | `focus` | 與被聚焦的共用 `Button` 相同 | 可選的背景 focus chrome；core 仍會畫可見 focus ring |
| `save` / `load` | `footer` | `hint`、`success`、`error` | 操作提示或存讀檔狀態底板 |
| `settings` | `backdrop` | `overlay` | 設定頁後方遮罩 |
| `settings` | `panel` | `main` | 設定頁主容器 |
| `settings` | `divider` | `header` | 標題區分隔線 |
| `settings` | `button` / `focus` | `close:ghost`；`choice:<style>`、`stepper:<style>`、`toggle:<style>`、`action:<style>` | 關閉、選項、增減、切換與一般動作 |
| `settings` | `footer` | `hint` | 控制提示底板 |
| `gallery` | `backdrop` | `overlay` | CG Gallery 後方遮罩 |
| `gallery` | `panel` | `main` | Gallery 主容器 |
| `gallery` | `divider` | `header` | 標題區分隔線 |
| `gallery` | `button` / `focus` | `close:ghost` | 關閉動作 |
| `gallery` | `card` | `unlocked`、`locked` | 已解鎖與未解鎖 CG 卡 |
| `gallery` | `footer` | `hint` | 控制提示底板 |
| `scene_replay` | `backdrop` | `overlay` | 場景重溫後方遮罩 |
| `scene_replay` | `panel` | `main` | 虛擬化回想清單主容器 |
| `scene_replay` | `divider` | `header` | 標題區分隔線 |
| `scene_replay` | `button` / `focus` | `close:ghost` | 關閉動作 |
| `scene_replay` | `card` / `focus` | `entry` | 可重播場景列與目前焦點 |
| `scene_replay` | `footer` | `navigation` | 操作提示底板 |
| `map` | `backdrop` | `overlay` | 校園／世界地圖後方遮罩 |
| `map` | `panel` | `main` | 地圖主容器 |
| `map` | `button` / `focus` | `close:ghost`、`filter:ghost`、`filter:primary` | 關閉與故事／一般地點切換 |
| `travel` | `backdrop` | `overlay`、`modal` | 目的地瀏覽遮罩與確認層遮罩 |
| `travel` | `panel` | `main`、`confirmation` | 目的地瀏覽主容器與確認面板 |
| `travel` | `button` / `focus` | `close:ghost`、`map:ghost`、`filter:<style>`、`confirm:primary`、`cancel:ghost` | 關閉、返回地圖、篩選與保守確認流程 |
| `travel` | `card` | `destination`、`destination:new` | 一般目的地與有新事件的目的地卡 |

共用 `Button` widget 一律把 `variant` 編碼成 `<role>:<style>`；目前的
`style` vocabulary 是 `primary`、`ghost`、`danger`。Save/Load 的
`button/action` 是 scene 自行繪製、手動 hit-test 的 action chip，不是
`Button` widget，因此是這條文法的刻意例外。`enabled`、`hovered`、
`focused`、`selected` 與 `emphasis` 應從 request 欄位讀取，不應再塞進
`variant`。

`focus` request 是條件式的，不代表 renderer 可以決定焦點。Onboarding 只有一個
動作，所以 core 進場即聚焦 `start`；Choice 只有一個 enabled option 時也會立即
聚焦，兩個以上的 enabled options 則保持未聚焦，直到鍵盤／手把導覽或滑鼠移動
指定目標。導覽會跳過 disabled options。Renderer 可以替當前目標補背景 halo 或
角標，但 core 仍會在其後畫高對比 focus ring，並擁有 confirm、click 與 hit-test。

這份矩陣保證的是識別字的語意，不保證每幀一定發送、發送順序、呼叫次數、
像素座標或尺寸。v1 內不會移除、改名或改寫上表既有組合的語意；引擎可以增添
新 screen、part、variant、Button role/style 或新的有限狀態組合。Renderer
應對未知組合回傳 `None`，不可因未知值拋出例外。若需要移除／重新解釋既有值，
或改變 `Button` 的 variant 文法，必須使用新的 protocol version。

### 文案與互動 ownership

Renderer 只可畫 chrome（底色、框線、角飾、分隔、裝飾紋理）與不承載操作或
狀態的靜態裝飾微文案。按鈕標籤、頁面標題、快捷鍵提示、存檔名稱與時間、
Gallery 計數、disabled 原因、成功／錯誤／確認訊息等 functional、control、
status 文案，連同 localization、截斷與可見性，都由 core 擁有並在 chrome
之後繪製。裝飾微文案即使完全不畫，也不得讓玩家失去操作、狀態或錯誤資訊。

在 reading loop 中，這包含 onboarding 的標題、三條操作說明與開始按鈕，
ADV／NVL 正文、說話者姓名、聲音字幕及其類型標籤、章節／時間、AUTO／SKIP、
繼續提示與 quick-menu 標籤，以及 choice 的「請選擇」、選項文字與 disabled 原因。Renderer 只接管上表
列出的底板；不能改變單選自動聚焦、多選等待玩家指定、disabled 遮罩、選項順序、
閱讀推進或 onboarding 的 Space／Enter／Esc 關閉行為。

當 `Choice.card` 啟用 comparison-card 版型時，人物／方案圖片、替代說明、標題、
次標、badge、三列比較資訊、tags、action 與 accent rail 也都由 core 按 strict
presentation model 繪製。Pack renderer 只會收到
`choice/card/option_card`（以及聚焦時的 `choice/focus/option_card`），不得依
choice id 猜角色、改寫內容、隱藏 disabled 原因或擴張 hit target。

Renderer 必須回傳與 `request.size` 完全相同的 `pygame.Surface`，不處理某個
part 時回傳 `None`。引擎的 LRU 快取同時限制為 128 筆與 128 MiB（以 surface
pitch × height 計算）；此上限可容納完整 4K choice working set，選擇畫面真正
離場時則只釋放其 `screen=choice` entries。單張超過 byte budget 的 surface
仍會繪製，但不會快取。
constructor、render、回傳型別或尺寸出錯時會停用該 instance，當次 session
回到內建 UI。Renderer 只負責 chrome；
文字、disabled 狀態、可見焦點、44px 操作區、hit-test 與鍵盤／手把順序仍由
引擎負責。

`options` 的內容由 plugin 擁有。Renderer 若公開 Pydantic `OptionsModel`，
`validate_pack` 會用它檢查未知欄位與錯誤型別；Capability Manifest 也會列出
widget 的 `signature` 與 `options_schema`。無 schema 時會以
`{"unavailable": "<reason>"}` 明確標示；plugin reflection 失敗不會中止整份
manifest。沒有 `ui_skin` 時不會建立 renderer，system screens 走 engine 的
stock fallback 行為；renderer 對某個 request 回傳 `None` 時該部件也走 stock，
而 renderer 拋錯、回傳錯誤型別或錯誤尺寸時會隔離並停用該 instance，session
其餘畫面繼續使用 stock。這是互動與可用性的 fallback 承諾，不是跨 engine
版本、平台、字型、locale 或解析度的 byte-for-byte 畫面快照保證。

## 字型

`meta.yaml`：

```yaml
bundled_font: assets/fonts/MyFont.ttf
```

引擎優先用 pack 內嵌的字型。沒設的話，引擎會在系統字型中按以下順序
嘗試（針對 CJK 友善）：

```
PingFang TC → Heiti TC → Microsoft JhengHei → Noto Sans CJK TC
→ Noto Sans TC → Hiragino Sans GB → Source Han Sans TC
→ Arial Unicode MS
```

都找不到才回到 pygame 預設（CJK 會出豆腐方塊）。

**強烈建議內嵌字型**，特別是要打包 .exe / .app 給其他使用者跑時。
多語 pack 可再為每個 language 宣告 `font.bundled` 與
`font.candidates`；runtime 會在語系生效時重新綁定共用的 font registry。
語系沒有完整宣告時會依序回退 source language 的來源，再回退上面的
legacy `bundled_font` / 系統 candidates。這是「每語系選一組字型來源」，
還不是同一行文字的 true per-glyph fallback；混排缺字仍需額外測試。

## Locale（在地化）

Runtime localization v1 讓一個 pack 同時帶 source 與多個 target catalog。
舊 pack 只有 `locale.ui` / `time_of_day` / `day_of_week` /
`affection_levels` 的單語覆寫仍完全相容；只有同時宣告
`source`、`default` 與 `languages` 才會開啟多語選擇器。

### Manifest

`content/meta.yaml`：

```yaml
locale:
  # 以下舊式 source 覆寫仍可使用；沒有 target 時也是 fallback。
  affection_levels:
    - {min: -999, label: "敵意"}
    - {min: 0, label: "陌生"}
  time_of_day:
    morning: "早晨"
    midnight: "深夜"
  day_of_week:
    mon: "週一"
  ui:
    new_game: "開始新遊戲"
    day_format: "第 {day} 天 · {weekday} · {time_of_day}"

  source: zh-Hant-TW
  default: zh-Hant-TW
  languages:
    zh-Hant-TW:
      label: "繁體中文"
      status: complete
      # source catalog 對生產流程強烈建議；它讓 runtime 可偵測 source drift。
      catalog: content/i18n/messages.zh-Hant-TW.yaml
      story_scenes: all
      font:
        bundled: assets/fonts/NotoSansTC.otf
        candidates: ["Noto Sans TC", "Source Han Sans TC"]

    en:
      label: "English Preview"
      status: preview
      catalog: content/i18n/en.yaml
      # Preview 只套用到這個已完整翻譯的閉合場景集。
      story_scenes: [prologue, first_day_tour]
      font:
        candidates: ["Noto Sans", "Arial"]
```

`source` 與 `default` 都必須存在於 `languages`。Target language 必須明示
`catalog`；source catalog 為了舊 pack 相容可省略，但生產 pack 應一併宣告。
`status: complete` 省略 `story_scenes` 時代表所有 scene；
`status: preview` 省略時只翻 meta/UI，應明列已翻完的 scene id。
`story_scenes: all` 或 `"*"` 可明示表示全場景。Preview 名單之外的
劇情文案會回退 source；`meta.*` 與 `ui.*` 不受該名單限制。

Language id 是受限、正規化的 BCP-47-shaped token，但 runtime 絕不會把
使用者輸入的 id 拼成路徑。Catalog 與 bundled font 都必須由 manifest
明示指向 pack 內的相對路徑；絕對路徑、URL、反斜線、`..`、
symlink escape、缺檔或錯誤副檔名都會被拒絕。某個 target 的資料不安全時，
它會整個從可選清單移除，不會留下半套 target copy。

### Catalog v1

Canonical 位置是 `<pack>/content/i18n/`。Source 與 target 都使用相同的
versioned envelope：

```yaml
format_version: 1
language: en
source_language: zh-Hant-TW
source_digest: 0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef
messages:
  meta.title: "An Afternoon in Town"
  ui.new_game: "New Game"
  prologue.title: "Prologue · Arrival"
  prologue.line.arrival_gate: "I looked up at the campus gate."
  prologue.line.arrival_gate.speaker: "Player"
  prologue.line.arrival_gate.sound_caption.0: "A bus pulls away."
  prologue.choice.open_guide: "Open the freshman guide"
```

`source_digest` 是「全部抽取後 source message table」的 canonical SHA-256，
不是 YAML bytes 的 hash。Source catalog 的 digest 必須與自己的
`messages` 相符；每個 target 的 digest 必須指向同一份 source。
有宣告 source catalog 時，runtime 還會逐鍵比對現行 authored source；對白
已改但翻譯尚未重做時，該鍵 fail closed 回到新 source，不會顯示過期翻譯。

Catalog 只接受受限大小的 UTF-8 safe YAML，拒絕 duplicate keys、未知
envelope 欄位、過大 table/key/value 與 NUL。檢查器與 runtime 都會保守
placeholder 數量、rich-text/BBCode 序列及 `[[dialogue_op:...]]` 序列；
不安全的單鍵翻譯回退 source。

### 劇情鍵與呈現邊界

抽取器會收錄 `meta.*`、全部 canonical `ui.*`、scene title、line
text/speaker/sound captions、choice text 與 choice-card 內的玩家可見文字。
對白鍵預設是 `<scene>.line.<index>`。正式翻譯建議在 line 宣告
穩定 identity：

```yaml
lines:
  - loc_id: arrival_gate
    speaker: "{player_name}"
    text: "我抬頭看著校門。"
```

這行的 key 會是 `prologue.line.arrival_gate`；插入前一行不會讓它與之後所有
翻譯變號。`loc_id` 可省略，舊 pack 繼續用 index fallback；CI 可用
`--require-stable-line-ids` 將這種易漂移的行當成失敗。

同一個 identity 也供 Read／Skip、Backlog、resume 與 Scene Replay 使用；它不只是
翻譯便利欄位。已發行 pack 的全量導入與舊存檔轉換規則見
[stable-line-identity.md](stable-line-identity.md)。

Localization 只在 presentation 層解析這些 copy。Scene/choice/NPC id、條件、
effects、transitions、flags、數值狀態與 asset paths 仍完全來自 source pack；
target catalog 不能改變遊戲邏輯。插值在取得安全翻譯後才執行。

### 抽取與檢查

```bash
# 列出 source table（legacy flat YAML stdout）
uv run python tools/i18n_extract.py /path/to/pack

# 寫出 versioned source catalog
uv run python tools/i18n_extract.py /path/to/pack \
    --out --src-lang zh-Hant-TW

# 寫出 digest-bound 空 target template
uv run python tools/i18n_extract.py /path/to/pack \
    --template en --src-lang zh-Hant-TW

# 檢查 digest、coverage、空值、多餘鍵與結構 parity
uv run python tools/i18n_extract.py /path/to/pack \
    --check /path/to/pack/content/i18n/en.yaml --src-lang zh-Hant-TW \
    --require-stable-line-ids
```

`complete` 的 check 要求整張 source table。`preview` 依 manifest
`story_scenes` 只要求 `meta.*`、`ui.*` 與指定 scenes；空值、多餘鍵、
digest 不同或結構 parity 不安全仍會失敗。可用 `--status complete|preview`
在 CI 明示覆寫 manifest policy。

`validate` 也會以 strict runtime loader 檢查 manifest、catalog 與 font 路徑。
Web staging 會連同 `content/` 複製 canonical `content/i18n/`；若舊 manifest
明示使用 pack-root `i18n/`，web builder 也會保留該目錄。

### 顯示層文字擴張檢查

`VisualCheck` scenario 可同時指定真實 target locale 與 deterministic pseudo
expansion：

```json
{
  "name": "settings_en_expand_720p",
  "view": "settings",
  "locale": "en",
  "text_expansion_factor": 1.35,
  "screen_size": [1280, 720]
}
```

`text_expansion_factor` 限 1.0–2.0，使用版本化的
`pseudo-expand-v1`。它在 canonical localization 與插值完成後、widget 量測
之前擴張顯示字串，並保留 rich-text tags、inline wait、placeholder 與
dialogue-op syntax。Policy 是 session-scoped、display-only：不會加入語言
選單、不寫入 `settings.json`／save／history，也不會進入 effects、hooks 或
GameState。正式語言仍應保留 exact visual baseline；pseudo capture 用來找出
固定寬按鈕、必要文字 ellipsis、重疊與 reflow 問題，不能取代母語人工審稿。

### 玩家選擇、存檔與目前限制

GUI 的選擇會以 pack id 分開存在 `settings.json` 的
`locale_preferences`；一個 pack 的 `en` 不會影響另一個 pack。
為避免在 active dialogue 中重建頁碼、執行指令或改寫玩家正在看的存檔呈現，
v1 GUI **只允許在標題畫面開啟的設定頁切換語系**。遊戲中的設定頁會顯示
目前語系與「請回標題畫面切換」的原因。切換會同步更新標題文案、
UI/time/weekday/affection labels 與語系字型，不觸發劇情效果。

Headless 也只能在 `current_scene == null` 時執行 `locale`；見
[headless.md](headless.md) 與 [session-protocol.md](session-protocol.md)。

載入存檔時，具有 scene/line identity 的 resume 與 history 可用當前 catalog
重建呈現，但這條路徑絕不重跑 effects、conditions、hooks、read-log/history
writes 或 dialogue-op handlers。已註冊的 `[[op:...]]` 在重建文案中會被移除而
不執行；未知 directive 仍保留於文字中作為 authoring diagnostic。
LLM-generated line 則保留已產生的文字，不假裝可由 authored catalog 重現。

Runtime v1 已能套用劇情、部分 system UI 鍵、時間／好感標籤與語系字型，
但這仍不是「完成商業在地化」：

- 尚未將每一個舊的 raw system string 都收進 `DEFAULT_UI` / `ui.*`。
- 新版具有 scene/line identity 的 history row 可保守地重綁定，但 legacy history row
  與 LLM 即時產生的文字不一定可重新在地化。
- 已有 deterministic display-only pseudo expansion 與 per-locale visual
  scenario，但全 screen／720p、1080p、4K／text scale 1.0、1.4 的 geometry
  fail-closed gate 尚未完成。
- 真正 per-glyph font fallback、完整遊戲翻譯、母語人工語言與文化 QA
  仍是 production work。

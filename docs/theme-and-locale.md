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

## Locale（在地化）

`meta.yaml`：

```yaml
locale:
  # 好感度等級對應表（min 大的優先）
  affection_levels:
    - {min: -999, label: "Hatred"}
    - {min: 0,    label: "Stranger"}
    - {min: 25,   label: "Friend"}
    - {min: 50,   label: "Close"}
    - {min: 100,  label: "Lover"}

  # 時段名稱
  time_of_day:
    morning:   "Morning"
    noon:      "Noon"
    afternoon: "Afternoon"
    evening:   "Evening"
    night:     "Night"
    midnight:  "Witching Hour"

  # 星期
  day_of_week:
    mon: "Mon"
    tue: "Tue"
    wed: "Wed"
    thu: "Thu"
    fri: "Fri"
    sat: "Sat"
    sun: "Sun"

  # 通用 UI 字串（不寫的鍵保留引擎中文預設）
  ui:
    new_game:       "New Game"
    load_game:      "Continue"
    quit:           "Quit"
    name_prompt:    "Name"
    name_placeholder: "Enter your name…"
    map:            "Map"
    affection:      "Bonds"
    log:            "Journal"
    save:           "Save"
    settings:       "Settings"
    leave:          "Leave"
    advance_time:   "Wait"
    close:          "Close"
    continue_hint:  "Click / Space to continue"
    day_format:     "Day {day} · {weekday} · {time_of_day}"
    leave_confirm:  "Return to title? Unsaved progress will be lost."
    achievements:   "Achievements"
```

`day_format` 是格式化字串，支援 `{day}` `{weekday}` `{time_of_day}` 三個變數。

## 哪些 UI 字串可以蓋？

完整鍵清單見 `world_gal_game/core/localization.py` 的 `DEFAULT_UI`。
目前有 20+ 個。如果你發現某個 UI 字串還是寫死中文沒辦法蓋，請開 issue —
那是 bug，不是 feature。

## 範例：把清華異聞錄翻成英文

```yaml
# meta.yaml
locale:
  affection_levels:
    - {min: -999, label: "Hostile"}
    - {min: 0,    label: "Stranger"}
    - {min: 25,   label: "Acquaintance"}
    - {min: 50,   label: "Friend"}
    - {min: 100,  label: "Beloved"}
  time_of_day:
    morning:   "Morning"
    noon:      "Noon"
    afternoon: "Afternoon"
    evening:   "Evening"
    night:     "Night"
    midnight:  "Witching Hour"
  ui:
    new_game:  "New Game"
    load_game: "Continue"
    quit:      "Quit"
    map:       "Campus"
    affection: "Hearts"
    log:       "Diary"
    save:      "Save"
    achievements: "Lore"
    inventory: "Inventory"
    settings:  "Settings"
    day_format: "Day {day} · {weekday} · {time_of_day}"
```

劇情本身（場景 YAML 內的對白）目前沒有 i18n 機制 — 多語版本要分 pack
（例如 `Tsinghua-Strange-Tales` + `Tsinghua-Strange-Tales-EN`）。
未來可能加 `lang:` field 讓同一個 pack 帶多語對白。

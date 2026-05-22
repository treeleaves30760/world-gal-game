# Presentation & Extras

> **學習路徑**：軌道 2 — 各子系統指南  
> **前置條件**：讀完 [scenes.md](scenes.md)（line / effects 寫法）  
> **相關**：[pack-format.md](pack-format.md)（`endings.yaml` schema）、`wgg capabilities`（演出 effect 權威清單）  
> **完整索引**：[docs/README.md](README.md)

---

這一章涵蓋讓 pack 「像一款成熟 Gal-Game」的演出與鑑賞功能：CG 鑑賞、音樂室、
場景重溫、結局與完成度、Auto / Skip、NVL 模式、鏡頭 / 畫面特效、角色語音音量、
以及快速存檔 / 自動存檔。

每個功能都從兩個角度寫：

- **玩家做什麼**（哪個鍵、哪個選單）。
- **Pack 作者做什麼**（要寫什麼 config / `endings.yaml` / line `cg` / line `bgm` /
  line `effects` 才能啟用或編排它）。

大多數功能**不用寫一行 Python** —— 媒體在劇情演到時自動解鎖，特效寫在 line 的
`effects:` 裡，偏好走玩家設定。

---

## 鑑賞模式（Extras）總覽

引擎內建四個鑑賞 overlay。兩個入口：

- **遊戲內選單**（Esc 開）：四個都在 —— CG鑑賞、音樂室、結局、場景重溫。
- **標題畫面 → 「鑑賞模式」**：開 CG鑑賞 / 音樂室 / 結局（不含場景重溫，因為它要
  讀目前存檔的已讀場景）。當 pack 沒有任何可鑑賞內容時這個入口仍會出現，進去是
  空清單。

> 鑑賞 overlay 全部是**唯讀**的：開啟、瀏覽、播放都不會改動存檔。

| Overlay | 顯示什麼 | 解鎖條件 |
|---|---|---|
| CG 鑑賞（CGGalleryScene） | `assets/cgs/` 的縮圖牆 | 演到含 `cg` 的 line 或場景時自動解鎖 |
| 音樂室（MusicRoomScene） | `assets/bgm/` 的曲目清單 | 播放含 `bgm` 的 line 或場景時自動解鎖 |
| 結局（EndingsScene） | 依 `route_id` 分組的結局 + 完成度 | `endings.yaml` 的 `requires` 成立時 |
| 場景重溫（SceneReplayScene） | 已讀過的場景清單 | 場景被讀過（記在 `read_log.scenes`） |

---

## CG 鑑賞（CG Gallery）

### 玩家

從選單或標題的「鑑賞模式」進入。畫面是一面縮圖牆：

- **已解鎖**的 CG 顯示縮圖（cover 裁切）+ 檔名；點一下進**全螢幕**檢視
  （contain 完整顯示）。全螢幕時點任意處或按 Esc 返回牆面。
- **未解鎖**的格子顯示暗底 + 「?」+「未解鎖」字樣。
- 標題下方有「已解鎖 / 總數」計數。

### Pack 作者

把 CG 圖放進 `assets/cgs/`，然後在某一行對白上掛 `cg:`：

```yaml
- text: "這個小鎮的故事，從這裡開始有了不一樣的形狀。"
  cg: assets/cgs/lover_lakeside.png
```

那一行**演到時**，引擎自動把該 CG 標記為已解鎖（記在存檔的 `cg_gallery`）。你不用
寫任何解鎖 effect。鑑賞牆會列出 `assets/cgs/` 內找得到的所有圖（`.png` / `.jpg` /
`.jpeg` / `.webp` / `.bmp` / `.gif`），所以還沒解鎖的也會以「?」佔位呈現，玩家看得到
還缺幾張。

> 場景層級也有 `cg:`（整個場景的滿版底圖）；單行的 `line.cg` 會覆寫它顯示。
> **`line.cg` 與場景層級的 `cg` 都會自動解鎖鑑賞牆**（前者在演到該行時解鎖、後者在
> 進場時解鎖），你不用寫任何解鎖 effect。

---

## 音樂室（Music Room）

### 玩家

一個點唱機：列出 pack 收錄的所有 BGM 曲目。

- 只有**聽過**（在遊戲中播放過）的曲目可以播放；點該列即播放（淡入、自動去重）。
- 沒聽過的曲目顯示「？？？」+「尚未解鎖」，不可點。
- 右上有一顆**停止**鈕，會停掉目前播放的音樂。
- 關閉音樂室時，**只會**停掉它自己起的試聽 —— 如果你沒在這裡播任何東西，底下場景
  原本的 BGM 不受影響。

### Pack 作者

把音樂放進 `assets/bgm/`（`.ogg` / `.mp3` / `.wav`），在 line 或場景掛 `bgm:`：

```yaml
# 場景層級：進場切到這首
- id: lakeside_evening
  bgm: assets/bgm/lakeside.ogg
  lines:
    - text: "湖面映著最後一點橘色。"
    # 單行也能換 BGM
    - text: "她輕輕哼起一段旋律。"
      bgm: assets/bgm/her_theme.ogg
```

**場景層級的 `bgm`（進場切歌）和單行的 `line.bgm` 都會自動解鎖**：播放到時該曲目即標
記為已解鎖（記在存檔的 `music_room`）。由於多數 pack 的 BGM 設在場景層級，音樂室因此
能列齊大部分曲目。音樂室列出 `assets/bgm/` 內所有檔，未解鎖的以「？？？」佔位。

---

## 結局與完成度（Endings & Completion）

### 玩家

結局 overlay 依**路線**（heroine）分組顯示：

- 每條 heroine 路線用該角色名字當分組標題（來自 NPC 的 `is_heroine` / `route_id`）；
  沒有 `route_id` 的結局落在「其他」分組。
- **已解鎖**的結局顯示標題、描述、解鎖時間。
- **未解鎖**但非隱藏的結局：標題以灰字顯示（看得到名字，知道還有這個結局）。
- **隱藏且未解鎖**的結局：根本不進清單；萬一漏網則顯示為「？？？」。
- 頂端有一條**完成度**：劇情（已讀場景 / 總場景）、結局（已解鎖 / 總數）、CG，
  再加一個總完成度百分比。

### Pack 作者

結局寫在 `content/endings.yaml`（選填檔，schema 見下方與
[pack-format.md](pack-format.md)）。每個結局用 `requires` 條件決定何時解鎖 ——
慣例是綁一個在路線收尾時 set 的 `ending_*` flag：

```yaml
# content/endings.yaml
endings:
  - id: ending_lover
    title: "結局 · 戀人"
    description: "與林清雪的故事，走到了戀人結局。湖畔的那一頁，被留了下來。"
    route_id: heroine_1          # 用 heroine 的 route_id 分組
    requires:
      - {kind: flag, target: ending_lover}

  - id: ending_alone
    title: "結局 · 一個人"
    description: "離開了廣場，沒有再回頭。"
    # 沒有 route_id → 落在「其他」分組
    requires:
      - {kind: flag, target: ending_alone}
```

然後在路線收尾的場景 `on_end` set 對應 flag：

```yaml
on_end:
  - kind: set_flag
    target: ending_lover
    value: true
  - kind: end_scene
```

結局的 `requires` / `forbids` 走標準 condition 載入器，所以任何 condition kind
（`flag` / `scene_played` / `affection_gte` …）都能拿來當解鎖門檻。引擎在每次
`apply` 後重新檢查所有結局，flag 一 set 結局就解鎖。

**完成度**自動算，作者不用做任何事：劇情看 `read_log.scenes` / 場景總數，結局看
已解鎖 / 總數，CG 看 `cg_gallery` 解鎖數。

> `route_id` 的分組標題來自 `characters.yaml` 裡標了 `is_heroine: true` 並設了
> `route_id` 的 NPC（見 [characters.md](characters.md)）。沒有對應 NPC 時，直接用
> `route_id` 字串當標題。

---

## 場景重溫（Scene Replay）

### 玩家

從**遊戲內選單**進入。列出你**已讀過**的場景；點一個就在一個**唯讀沙盒**裡重新播放。
重播時的畫面與第一次看時逐格相同，但**重播不會改動正式存檔** —— flag、好感度、
已讀紀錄、自動存檔都不受影響，效果（含特效、跳轉）也不會持久化。重播結束後沙盒
直接丟棄，回到清單。

### Pack 作者

不用做任何事。任何被玩家讀過的場景（記在 `read_log.scenes`）都自動可重溫；目前
pack 已不存在的場景 id（例如改過內容後）會被自動跳過。沙盒是把 `GameState` 透過
JSON round-trip 深拷貝出來播的，所以你寫的場景照常運作，只是改不到正式進度。

---

## 對話演出：Auto / Skip（ADV 模式）

引擎預設是 **ADV 模式**（底部一條對話框）。

### 玩家

| 操作 | 行為 |
|---|---|
| 點擊 / Space / Enter / Z | 文字打字機未跑完 → 立刻顯示全文；已顯示完 → 推進到下一行 |
| `A` | 切換 **Auto**（自動播放）。到選項會自動關閉 |
| 按住 `Ctrl` | **Skip**（快進）。放開即停；遇到選項一定停 |

右上角會顯示 **AUTO** / **SKIP** 狀態徽章（功能性指示，不是裝飾）；兩者可同時亮。

**Auto** 的節奏由玩家設定 `auto_play_speed` 縮放（越大越快）；當
`auto_play_wait_voice` 開啟時，會等該行的語音播完才推進下一行。

**Skip** 是兩段式，由 `skip_unread_only` 決定：

- `True`（僅快進已讀，預設）：只快進**已讀過**的行，碰到第一行未讀或任何選項就停。
- `False`（全部快進）：連未讀的行也一路快進，直到選項或場景結束。

### Pack 作者

這些都是**玩家偏好**，不是 pack 內容；作者通常不用碰。相關 config 欄位（皆可在
設定畫面調整、自動持久化，見下方「設定持久化」）：

| 欄位 | 預設 | 作用 |
|---|---|---|
| `auto_play_delay` | `2.5` | Auto 每行之間的基礎秒數 |
| `auto_play_speed` | `1.0` | 縮放 `auto_play_delay`（越大越快） |
| `auto_play_wait_voice` | `True` | Auto 時等語音播完才推進 |
| `skip_unread_only` | `True` | Skip 只快進已讀 vs. 連未讀也快進 |

語音來自 line 的 `voice:`（per-line 語音 clip）；要讓「等待語音」有意義，在有配音的
行掛 `voice: assets/voice/....ogg`。

---

## NVL 模式

### 玩家

NVL 模式把底部 ADV 對話框換成**全螢幕累積式文字稿**：同一場景的對白逐行累積在
一張大面板上，換場景時清空。適合大量旁白 / 信件 / 內心獨白的段落。

在**設定**畫面切換「NVL 模式：開 / 關」。

### Pack 作者

NVL 是引擎層的呈現切換（`config.nvl_mode`），對 pack 的 YAML **完全透明** —— 同一份
場景內容在 ADV 與 NVL 下都能跑，推進 / Auto / Skip 行為一致。作者不用為 NVL 寫
任何特別的東西。

---

## 鏡頭 / 畫面特效（Presentation Effects）

引擎內建五個演出 effect。它們是 **builtin**、**不碰 pygame**：handler 只把一筆指令
排進場景的 per-frame 視覺佇列（`state.meta["__visual_fx__"]`，`__` 前綴所以不會寫進
存檔），由 DialogueScene 取出後動畫化並繪製。

### 玩家

無操作 —— 由劇情編排自動觸發。

### Pack 作者

寫在某一行的 `effects:` 裡（和任何其他 effect 一樣，line 演到時套用）：

```yaml
- speaker: "林清雪"
  text: "「下次。」"
  expression: smile
  effects:
    - kind: camera_zoom
      value: {scale: 1.12, duration: 0.9}
```

五個 effect 與其 `value` 簽章：

| Effect kind | `value`（dict） | 預設 / 說明 |
|---|---|---|
| `camera_pan` | `{x, y, duration?, easing?}` | 鏡頭平移到偏移量（來源像素）。`duration` 預設 0.6 |
| `camera_zoom` | `{scale, duration?, easing?}` | 縮放到 `scale`（1.0 = 中性）。`duration` 預設 0.6 |
| `screen_shake` | `{intensity?, duration?, easing?}` | 整個畫面衰減式抖動。`intensity` 預設 12.0、`duration` 0.4 |
| `screen_flash` | `{color:[r,g,b]?, duration?, max_alpha?, easing?}` | 一閃即逝的顏色覆蓋並淡出。`color` 預設白、`duration` 0.3、`max_alpha` 255 |
| `screen_tint` | `{color:[r,g,b]?, duration?, max_alpha?, persist?, clear?, easing?}` | 顏色色調覆蓋；淡入（`duration<=0` 即時）。`color` 預設黑、`duration` 0.5、`max_alpha` 120 |

說明：

- `easing` 是選填的緩動曲線名（見 `ui/easing.py`；不寫用內建預設）。
- `screen_tint` 會**持續**留在畫面上。要移除目前的色調，丟一個 `clear` 旗標
  （或把 `color` 設為 null）：

  ```yaml
  effects:
    - kind: screen_tint
      value: {clear: true}
  ```

- `screen_tint` 的 `persist` 旗標 / `duration<=0` 表示「出現後保持」；色調一旦建立
  就不會自己消失，要靠上面的 clear。

### 完整 / 權威清單

上表是寫作當下的 builtin 演出 effect。**權威清單**（含外掛新增的 effect）永遠以
這個指令為準：

```bash
uv run wgg capabilities --pack <pack>
```

通用 effect / condition 參考見 [effects-reference.md](effects-reference.md) /
[conditions-reference.md](conditions-reference.md)。

> 一個完整、現成可參考的範例是 demo_pack 的
> `games/demo_pack/content/scenes/90_ending_lover.yaml` —— 它同時用到 line 層級的
> `cg`、`camera_zoom` 與 `screen_tint`（含 `clear`）。

---

## 角色語音音量（Per-Character Voice Volume）

### 玩家

在**設定**畫面，每位角色一列 `-` / `+` 可單獨調整其語音音量；沒調過的角色沿用全域
「語音音量」。（面板空間有限，UI 最多列出前幾位角色；未列出的角色其設定值不受影響。）

### Pack 作者

不用做任何事 —— 引擎播放 line 的 `voice:` 時，依說話者查
`config.per_character_voice_volume`，查不到就 fall back 到 `voice_volume`。你只要在
有配音的行掛 `voice:` 與正確的 `speaker:` 即可。

```yaml
- speaker: heroine_1
  text: "「下次見面之前，我會把那一頁留著。」"
  voice: assets/voice/heroine_1/lakeside_07.ogg
```

> `speaker` 用 NPC id 時，引擎能把它對應到角色；per-character 音量也以這個 id 為 key。

---

## 存檔：快速存檔 / 自動存檔 / 存檔畫面

### 玩家

| 鍵 | 行為 |
|---|---|
| `F6` | **快速存檔** —— 寫到 `quicksave` 槽 |
| `F9` | **快速載入** —— 從 `quicksave` 槽載入（沒有就什麼都不做，遊戲照常） |

> `F5` 維持是開發模式的**熱重載**（dev hot-reload），不是快速存檔；`F11` 印狀態、
> `F12` 截圖。`F6` / `F9` 刻意挑選以避免和這些衝突。

**自動存檔**會在玩家做對話選擇 / 時間推進時，安靜地輪流寫入 `autosave_1..N` 槽
（`N` = `autosave_slot_count`）。

**存檔 / 載入畫面**是可捲動的卡片清單：

- 每張卡顯示**縮圖**（存檔當下的畫面截圖）、標題、時間。
- `quicksave` 與 `autosave_*` 槽會被**置頂**、上色並加徽章標籤，和手動存檔區分開。
- 在**存檔**模式下，`autosave_*` 槽是**唯讀**的（引擎管理，不給手動覆寫）；
  `quicksave` 仍可從畫面手動覆寫。載入模式下兩者都可載。

### Pack 作者

存檔系統是引擎能力，pack 通常不用碰。相關 config：

| 欄位 | 預設 | 作用 |
|---|---|---|
| `autosave_enabled` | `True` | 關掉就完全不自動存檔 |
| `autosave_slot_count` | `3` | 輪流的自動存檔槽數（`autosave_1..N`） |
| `quicksave_slot` | `"quicksave"` | 快速存檔用的槽名 |

> 自動存檔是一個**內建外掛**（`plugins_user/autosave/`），掛在 `dialogue.choice_made`
> 與 `time.advance` 兩個 hook 上，且全程 `isolate()` 保護 —— 存檔失敗（磁碟滿等）
> 絕不會打斷遊戲。要做不一樣的自動存檔策略，可以參考它寫自己的外掛
> （見 [plugins.md](plugins.md)）。

---

## 設定持久化

上面提到的所有玩家偏好（音量、文字速度、`auto_play_*`、`skip_unread_only`、
`nvl_mode`、`per_character_voice_volume`、`autosave_*`、`quicksave_slot` …）都會寫到
**`<writable_root>/settings.json`**，每次在設定畫面改動即時存檔
（`config.save_to_disk()`），下次啟動自動載回（`config.load_from_disk()`）。

`settings.json` 是一份**純玩家偏好**文件：pack / 路徑 / dev 欄位刻意不寫進去，所以
它在不同 pack 之間可攜。檔案缺失、損毀或含未知 key 都會被容錯處理（用預設值，
不會崩）。

---

## 一行檢查表（Pack 作者）

| 想要 | 怎麼做 |
|---|---|
| CG 進鑑賞牆 | 把圖放 `assets/cgs/`，在某行掛 `cg: assets/cgs/x.png` |
| 曲目進音樂室 | 把音樂放 `assets/bgm/`，在 line / 場景掛 `bgm:` |
| 新增一個結局 | 在 `content/endings.yaml` 加一筆 + 在路線收尾 `set_flag` |
| 結局依角色分組 | 結局寫 `route_id`，對應 NPC 標 `is_heroine` + `route_id` |
| 鏡頭推近 / 抖動 / 染色 | 在 line 的 `effects:` 用 `camera_*` / `screen_*` |
| 角色語音可單獨調 | 在有配音的行掛 `voice:` 與正確 `speaker:` |
| 場景可重溫 | 自動 —— 玩家讀過就會出現 |

# Presentation & Extras

> **學習路徑**：軌道 2 — 各子系統指南  
> **前置條件**：讀完 [scenes.md](scenes.md)（line / effects 寫法）  
> **相關**：[pack-format.md](pack-format.md)（`endings.yaml` schema）、`world-gal-game capabilities`（演出 effect 權威清單）
> **完整索引**：[docs/README.md](README.md)

---

這一章涵蓋讓 pack 「像一款成熟 Gal-Game」的演出與鑑賞功能：CG 鑑賞、音樂室、
場景重溫、結局與完成度、Auto / Skip、NVL 模式、鏡頭 / 畫面特效、角色語音音量、
以及快速存檔 / 自動存檔。

每個功能都從兩個角度寫：

- **玩家做什麼**（哪個語意操作、哪個選單；文中的實體鍵只代表預設綁定）。
- **Pack 作者做什麼**（要寫什麼 config / `endings.yaml` / line `cg` / line `bgm` /
  line `effects` 才能啟用或編排它）。

大多數功能**不用寫一行 Python** —— 媒體在劇情演到時自動解鎖，特效寫在 line 的
`effects:` 裡，偏好走玩家設定。

---

## 鑑賞模式（Extras）總覽

引擎內建四個鑑賞 overlay。兩個入口：

- **遊戲內選單**（`open_menu`，預設 Esc / X）：四個都在 —— CG鑑賞、音樂室、
  結局、場景重溫。
- **標題畫面 → 「鑑賞模式」**：四個鑑賞 overlay 都能進入，另有篇章流程圖與鳴謝；
  場景重溫讀取跨周目的 clear data，因此不必先載入某一個存檔。當 pack 沒有任何可
  鑑賞內容時對應畫面會誠實顯示空清單。

> 鑑賞 overlay 全部是**唯讀**的：開啟、瀏覽、播放都不會改動存檔。

| Overlay | 顯示什麼 | 解鎖條件 |
|---|---|---|
| CG 鑑賞（CGGalleryScene） | `cg_gallery.yaml` 目錄或 legacy `assets/cgs/` 縮圖牆 | 演到 scene/line `cg` 或 `show_cg` 時自動解鎖 |
| 音樂室（MusicRoomScene） | `music_room.yaml` 目錄或 legacy `assets/bgm/` 曲目清單 | 播放含 `bgm` 的 line 或場景時自動解鎖 |
| 結局（EndingsScene） | 依 `route_id` 分組的結局 + 完成度 | `endings.yaml` 的 `requires` 成立時 |
| 場景重溫（SceneReplayScene） | 第一次完整觀看時留下的實際演出路徑 | 場景具有完整、可驗證的 `read_log.replay_archive` trace |

---

## CG 鑑賞（CG Gallery）

### 玩家

從選單或標題的「鑑賞模式」進入。畫面是一面縮圖牆：

- **已解鎖**的 CG 顯示縮圖（cover 裁切）+ 作者標題／副標；沒有 catalog 的
  legacy pack 才退回檔名。點一下進**全螢幕**檢視（contain 完整顯示），下方顯示
  作者寫的標題、情境與回憶說明。全螢幕時點任意處，或使用 `confirm_advance`
  （鍵盤預設 Enter / Space / Z；手把 A）／`back`
  （鍵盤預設 Esc / X；手把 B），都會返回牆面。
- **未解鎖**的格子顯示暗底 + 「?」+「未解鎖」字樣。
- 標題下方有「已解鎖 / 總數」計數。
- `nav_*`（鍵盤預設方向鍵 / WASD；手把 D-pad）移動焦點，
  `confirm_advance` 開啟；卡片連同文字區都是可點擊範圍。

### Pack 作者

把 CG 圖放進 `assets/cgs/`，然後在某一行對白上掛 `cg:`：

```yaml
- text: "這個小鎮的故事，從這裡開始有了不一樣的形狀。"
  cg: assets/cgs/lover_lakeside.png
```

那一行**演到時**，引擎自動把該 CG 的 asset path 標記為已解鎖（記在存檔的
`cg_gallery`）。場景層級 `cg` 與 `show_cg` effect 也遵守同一規則，不需要額外的
unlock effect。

> 場景層級也有 `cg:`（整個場景的滿版底圖）；單行的 `line.cg` 會覆寫它顯示。
> `line.cg` 在演到該行時解鎖、場景 `cg` 在進場時解鎖、`show_cg` 在 effect
> 執行時解鎖。條件不成立而跳過的 line 不會提早解鎖。

正式作品建議另外寫選填的 `content/cg_gallery.yaml`：

```yaml
entries:
  - asset: assets/cgs/lover_lakeside.png
    title: "湖畔留下的那一頁"
    subtitle: "林清雪 · 第二章"
    description: "黃昏以前，她第一次把沒有說完的話留給你。"
    character_ids: [heroine_1]
    chapter_id: ch2_lakeside
    order: 210
    thumbnail: assets/cgs/thumbnails/lover_lakeside.webp  # 選填
    hidden: false
```

同一份資料也可寫成 top-level list，或以 asset path 為 key 的 compact mapping；
`entries:` wrapper 最適合文件與大型作品。三種 shape 的完整 JSON Schema 位於
`capabilities --schema` 的
`content_files["content/cg_gallery.yaml"]`。檔名大小寫必須精確。

契約如下：

- `asset` 是唯一解鎖鍵。引擎會移除首尾空白、統一 `/` 並正規化 `.` 路徑段；
  作者仍應一律寫 canonical path。新增 catalog 不會改寫既有存檔；不要為了改標題
  而改 asset path。
- `title` 必填；`subtitle`、`description`、`character_ids`、`chapter_id` 與
  `thumbnail` 選填。格狀牆優先讀 thumbnail，全螢幕永遠讀原始 asset。
- `order` 由小到大排序；相同值保留 YAML 宣告順序。
- `hidden: false` 的鎖定項目保留未知卡槽，但不洩漏任何 metadata。
  `hidden: true` 的鎖定項目完全不顯示、也不納入當下可見總數；解鎖後才按原順序加入。
- catalog 存在時是正式 roster，資料夾內未宣告的草稿／變體不會自動出現。仍由目前
  story、`show_cg` 或 pack 啟動時明確註冊的 plugin unlock 授權、但未列入 manifest
  的已解鎖項目，會以 filename fallback 附加；只有舊存檔自行帶入、目前 pack 已不再
  授權的 path 會在 load 時移除，不能直接送進圖片 decoder。
- catalog 缺省時完整保留 legacy 行為：掃描 `assets/cgs/` 第一層（PNG / JPEG /
  WebP / BMP / GIF）、依路徑排序，並以檔名當標題。

`world-gal-game validate <pack>` 會檢查 duplicate／alias asset、安全相對路徑、
圖片能否解碼、角色／章節 id、canonical filename，以及 scene、line、choice、
scene `on_end` 或 item `use_effects` 有使用但未收入 catalog 的 CG。空白
`show_cg.target` 會是 error；舊版曾寫入存檔／clear data 的空白鍵會在載入時清理。

存檔中的 `cg_gallery` 只記錄進度，不是資產目錄。正式 load／quickload 會以目前
StoryGraph、CG manifest 與 pack 啟動時明確註冊的額外項目重建權威集合，再把存檔與
`clear_data.json` 的 CG unlock 取交集；絕對路徑、`..` traversal、退役項目與手改 path
都不會進 Gallery 或 decoder。

---

## 音樂室（Music Room）

### 玩家

這是一個只讀、可完整聚焦的點唱機。正式 catalog 會顯示已解鎖曲目的作者標題、
artist credit 與劇情用途；legacy pack 則以檔名產生標題。

- 只有**在故事中聽過**的曲目可以播放。未解鎖列會以「？？？」、「尚未解鎖」及
  「在故事中聽過後解鎖」三個非顏色線索說明狀態；按確認或點擊只顯示原因，不會播放。
- `hidden: true` 的未解鎖 spoiler 曲目完全不顯示，也不納入當下的總數；解鎖後才依
  作者排序插回清單。
- `nav_up`／`nav_down`（方向鍵／WASD、手把 D-pad）逐列移動；`page_up`／
  `page_down` 翻頁；`focus_next`／`focus_previous` 可在曲目、停止與關閉間循環；
  `confirm_advance` 播放焦點曲目，`back` 從任何焦點關閉。畫面會把焦點列捲回可見區，
  並用明確邊框呈現 keyboard／gamepad focus。
- 滑鼠可點列、停止或關閉，滾輪與觸控拖曳共用同一個 bounded scroll；按鈕與列的
  最小互動目標為 44 px，視窗或字級改變時會重新排版而不丟失曲目焦點。
- 正在播放、可播放、鎖定與錯誤都有文字／符號提示，不只依賴顏色。右上「停止播放」
  只停止目前試聽；離開時仍會恢復進入音樂室前的狀態。

音樂室在進入時擷取 BGM、ambient 與 voice 三個 bus 的 playback snapshot。第一次成功
試聽才暫停底下場景的 ambient／voice；停止、返回、關閉按鈕或 scene exit 最後都只會
restore 同一份 snapshot 一次。因此無論玩家有沒有試聽／停止，回到原場景後的聲音
ownership 都與進入前一致，不會留下鑑賞 BGM，也不會永久吃掉被中斷的語音。

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

**場景層級的 `bgm`（進場切歌）和單行的 `line.bgm` 都會自動解鎖**：播放到真實曲目時
即標記為已解鎖（記在存檔的 `music_room`）。正式作品建議另外寫選填的
`content/music_room.yaml`：

```yaml
entries:
  - asset: assets/bgm/library_theme.ogg
    loc_id: library_theme
    title: "The Book on the Highest Shelf"
    artist: "Composer Name"
    usage: "圖書館 · 共通線"
    order: 110
    hidden: false

  - asset: assets/bgm/true_ending.ogg
    loc_id: true_ending
    title: "A Morning We Chose"
    artist: "Composer Name"
    usage: "真結局"
    order: 900
    hidden: true
```

三種 authoring shape（`entries:` wrapper、top-level list、asset-keyed compact
mapping）與完整欄位限制見 [pack-format.md](pack-format.md#music_roomyaml選填)；機器可從
`capabilities --schema` 的 `content_files["content/music_room.yaml"]` 取得同一份
JSON Schema。核心契約如下：

- `asset` 是 canonical 解鎖 identity，不要為了改標題而改檔名；`title` 必填，
  `artist`／`usage` 選填，`order` 由小到大排序。
- `loc_id` 是穩定的翻譯 key segment。Runtime 會解析
  `music_room.<loc_id>.title`、`.artist`、`.usage`；省略時使用 asset SHA-256 摘要的
  deterministic fallback。若曲目 path 可能重整，應明寫 `loc_id`，讓翻譯 key 不漂移。
- `hidden: false` 的未解鎖項目保留未知列；`hidden: true` 的未解鎖項目完全隱藏。
  兩者在解鎖前都不會把 title、artist 或 usage 暴露給 UI。
- manifest 存在（包括明確空清單）時，是鎖定 roster 的權威來源；資料夾內未宣告的
  草稿不會自動出現。仍由目前 pack 授權且已解鎖、但 catalog 未宣告的項目會以
  filename fallback 附加，避免採用 catalog 後抹掉合法舊進度。
- manifest 缺省時完整保留 legacy 行為：掃描 `assets/bgm/` 第一層的 `.ogg`／`.mp3`／
  `.wav`、依 path 排序，並以去副檔名、底線換空白的 filename label 顯示。

和 CG 相同，`music_room` 只保存已聽過的 canonical identity，metadata 不進 save。
`ClearData.music_seen` 會把合法進度帶到新周目，也會在載入較舊 save 時取聯集；寫入與
套用兩端都和目前 `MediaAuthority.music_ids` 取交集。該 authority 只由目前
StoryGraph 的 scene／line BGM、Music Room manifest、`assets/bgm/` 掃描、`title_bgm`
及啟動時明確註冊的項目建立，save candidate 不參與。因此刪除／退役曲目、絕對路徑、
`..` traversal 或手改 ClearData 不會取得 decoder 權限，也不能跨周目洗回清單。

`world-gal-game validate <pack>` 會拒絕錯誤大小寫、duplicate asset／`loc_id`、不安全
或不存在的音訊路徑及未知欄位，並警告遊戲有使用／收錄、但未收入 catalog 的 BGM。
預設 visual-check／production-check 另在 720p 與 1080p 捕捉 Music Room，驗證正式
catalog 的已解鎖、鎖定、焦點與捲動版面；source production gate 必須有這兩張人工
核准 baseline。這仍不能替代真實音訊、loudness／loop seam、實體控制器、觸控與
身障玩家驗收。

需要把既有音軌切成全靜時，在 scene 或 line 使用 `bgm: none`；環境床則使用
`ambient: none`。`none`、`silence`、`-` 都是保留的靜音 sentinel，不是資產路徑：

```yaml
- text: "翻頁聲停了。房間裡什麼也沒有。"
  bgm: none
  ambient: none
```

靜音狀態會和其他演出資料一起寫入存檔；讀檔只恢復靜音，不會重新啟動上一個 room
tone。`none`、`silence`、`-` 永遠不會被當成曲目解鎖進 Music Room。若只停 BGM 而
保留雨聲／室內底噪，只設定 `bgm: none` 即可。

---

## 聲音字幕（Sound Captions）

### 玩家

有作者字幕的台詞會在穩定、高對比的區域顯示「環境音／音效／音樂／寂靜」與描述；
畫面抖動不會讓字幕一起位移。它屬於目前台詞，不會新增一頁或改變 Auto／Skip 的
推進時間。版面會依 360p 至 4K 與 1.0–1.4 倍文字比例重算：必要時下移狀態 HUD，
NVL 則正式保留字幕 header，不讓字幕蓋住正文、AUTO／SKIP 或 quick menu。歷史紀錄
與場景重溫會保留相同描述，設定中的「聲音字幕」可持久關閉。

### Pack 作者

在 line 使用嚴格 typed `sound_captions`；不要把開發檔名或尚未交付的 cue 當成玩家
已聽見的內容：

```yaml
- text: "走廊盡頭的門自己闔上。"
  sfx: assets/audio/sfx/door_close.ogg
  sound_captions:
    - kind: sfx
      text: "走廊盡頭傳來關門聲。"
```

每行最多四筆，`kind` 只接受 `ambient`、`sfx`、`music`、`silence`，單筆 `text`
最多 240 字。字幕可描述有意義的沉默，所以不強制綁資產；它也不會替代 asset
validator、混音、loop seam、loudness 或實機聽測。完整 schema 與存檔／Replay
契約見 [pack-format.md](pack-format.md#scenes-lines語意聲音字幕)。

---

## 結局與完成度（Endings & Completion）

### 玩家

結局 overlay 依**路線**（heroine）分組顯示：

- 每條 heroine 路線用該角色名字當分組標題（來自 NPC 的 `is_heroine` / `route_id`）；
  沒有 `route_id` 的結局落在「其他」分組。
- **已解鎖**的結局顯示標題、描述、解鎖時間。
- **未解鎖**但非隱藏的結局：標題以灰字顯示（看得到名字，知道還有這個結局）。
- **隱藏且未解鎖**的結局：根本不進清單；萬一漏網則顯示為「？？？」。
- 頂端有一條**完成度**：劇情（已讀場景 / 總場景）、結局（已解鎖 / 總數），以及
  manifest 能精確計數時的 CG，再加一個總完成度百分比。

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

**完成度**自動算：劇情看 `read_log.scenes` / 場景總數，結局看已解鎖 / 總數；
有 `cg_gallery.yaml` 時，CG 以當下可見 roster 計算（locked hidden 不暴露，
已解鎖 hidden 與 old-save fallback 納入）。沒有 catalog 的 legacy pack 無法只從
runtime state 得知完整 CG 總數，因此完成度列省略 CG，而不是顯示虛假的分母。

> `route_id` 的分組標題來自 `characters.yaml` 裡標了 `is_heroine: true` 並設了
> `route_id` 的 NPC（見 [characters.md](characters.md)）。沒有對應 NPC 時，直接用
> `route_id` 字串當標題。

---

## 場景重溫（Scene Replay）

### 玩家

從**標題的鑑賞模式**或**遊戲內選單**進入。清單只列出具有完整 first-view
presentation trace 的場景；
方向鍵／WASD、Tab、Page Up／Down、滾輪、觸控拖曳與手把都可瀏覽，確認後在
**唯讀沙盒**重播。

清單的第一行只顯示玩家文案：優先採用 archive 當時保存的人類標題，其次使用目前
scene 的在地化標題；兩者缺漏或看起來是 `scene_id`、`loc_id`、snake_case slug、URI
或 asset path 時，改用穩定序號的「未命名回憶 01」。第二行只會組合目前
`ChapterManifest.title`、scene `location` 對到目前 pack map 的 `location.name`，以及
scene／chapter route 對到的 NPC 名字；`common`／`main` 顯示在地化的「共通線」。
無法從這些權威資料得到安全名稱時就省略，不會退回 raw id。`describe()` 會把
`player_text` 與帶 `scene_id` 的 debug `metadata` 分開；點擊與重播仍以 `scene_id`
維持唯一 identity，但玩家畫面不顯示它。

Trace 保存玩家第一次完整觀看時真正出現在畫面上的條件分支、插值文字、選項邊界、
實際選擇、HUD 時間／章節與受支援的內建視覺特效。播放端只逐格解碼這份紀錄：不重新
判斷目前 flag、不呼叫 LLM、不 dispatch authored effects，也不跟隨 `next_scene`。
當時可見但沒有選擇的選項仍可辨認，卻會鎖定，不能藉回想走進沒選過的分支。

第一次完成的 trace 會進入 `clear_data.json`，因此 New Game、舊存檔載入與下一周目
仍共享同一份已解鎖回想；自然重看不會用後來的狀態覆寫它。BGM、ambient、voice 在
進入前以 revision-aware snapshot 保存，回想結束／Back／例外時還原；一次性 SFX
只停止回想期間實際啟動而且尚未被 mixer channel 重用的聲音。正式存檔的 flag、
好感度、時間、物品、已讀紀錄、事件與自動存檔都不會被重播改動。

### Pack 作者

不用另外列 catalog。`DialogueEngine` 會在正常閱讀時自動建立有上限的 compact trace；
只有完整看完且選擇證據齊全的場景才會解鎖。中途存檔會保留 incomplete trace 供續讀，
但不會提早出現在回想庫；超出資源上限或格式無效時只會放棄該回想，不會阻止正文播放。

正式 pack 應替 scene 寫人類可讀的 `title`，並讓 `chapters.yaml` 的 `title`、map 的
`location.name` 與 heroine NPC 的 `route_id`／`name` 保持完整；這些欄位只改善回想庫
的玩家文案，不會改變 replay identity、解鎖證據或存檔格式。

舊版存檔只有 `read_log.scenes`、沒有 trace，會 **fail closed**：流程圖仍可顯示「已讀」，
但不提供可點的空白重播。玩家在新版從場景開頭完整看過一次後才會建立證據。Pack 更新
後若場景已刪除，或 archived portrait、音訊、CG、背景、line cursor、choice id／順序／
card image 已不再符合目前 authored graph，播放也會隱藏該列，而不是信任可手改的存檔
內容。

重播的 transient FX、音訊與 speaker 也有各自的 provenance 檢查。只有目前 builtin
handler 能從該 line 或合法 inbound transition 產生的精確 FX dictionary 會進 renderer；
save-only decoder path 或 ambient plugin payload 會被剝除。BGM／ambient 依目前
StoryGraph 與 authored map ambient 驗證 scene 初值、line override 和歷史 carry；SFX、
voice、portrait 與 CG 必須符合當前 authored line。已存的顯示名稱可以保留玩家姓名等
非 NPC 歷史文案，但不能改成另一個目前 NPC、藉此選到不同 portrait backend。這些檢查
發生在 decoder 或 plugin backend 之前。

Archive 描述的是 DialogueScene 真正畫出的 presentation frame。`play_movie` 與
`chapter_card` 這類外部 overlay 不會嵌進場景 trace；只在場景結束後執行、沒有任何
後續 draw frame 的 terminal FX 也不會憑空新增一格。若下一個場景的第一格實際承接
choice／`on_end` transition FX，該 FX 會歸到下一場景的第一個 presentation。

---

## 對話演出：Auto / Skip（ADV 模式）

引擎預設是 **ADV 模式**（底部一條對話框）。

### 玩家

下表的實體鍵都是預設值；玩家可在「設定 → 操作設定」重綁，畫面提示會立即顯示目前
綁定。

| 語意操作 | 鍵盤預設／其他輸入 | 行為 |
|---|---|---|
| `confirm_advance` | Space / Enter / Z、點擊／觸控 | 當頁打字機未跑完 → 立刻顯示當頁；還有視覺分頁 → 翻到下一頁；最後一頁已顯示完 → 推進到下一行 |
| `toggle_auto` | A | 切換 **Auto**（自動播放）。到選項會自動關閉 |
| `hold_skip` | 按住左／右 Ctrl | **Skip**（快進）。放開即停；遇到選項一定停 |
| `open_history` | B；滾輪向上也可開啟 | 開啟唯讀的對話記錄 |
| `hide_ui` | H | 隱藏／恢復對話框與閱讀控制 |
| `back` / `open_menu` | Esc / X；手把 B／右鍵依情境解析 | 關閉「更多」；未展開時開啟完整選單 |

右上角會顯示 **AUTO** / **SKIP** 狀態徽章（功能性指示，不是裝飾）；兩者可同時亮。

對話框上方的快速列採漸進揭露，避免把閱讀、存讀檔與系統導航十個動作永久放在
同一層：

- 主列：**自動、快進、記錄、快存、隱藏、更多**。
- 「更多」：**快讀、存檔、讀取、設定、完整選單**。

`快讀` 會立即替換目前進度，因此放在第二層，不和日常閱讀控制並列。展開後按
`back`（預設 Esc / X）或點列外空白會先收起，而且該次輸入不會穿透去推進台詞；
切換到新台詞、選項或其他 modal 畫面也會自動收起。`touch_mode` 下每顆按鈕至少
44 px；過長的 pack 翻譯會在安全寬度內省略，滑鼠停留時顯示完整文字。鍵盤可用
`focus_next`／`focus_previous`（預設 Tab／Shift+Tab）或 `nav_*`（預設方向鍵 /
WASD）移動焦點，`confirm_advance`（預設 Space / Enter / Z）啟動；控制器使用方向
輸入與確認鍵。快速列取得焦點後會吃掉該次確認輸入，不會同時推進台詞。

**Auto** 的節奏由玩家設定 `auto_play_speed` 縮放（越大越快）；當
`auto_play_wait_voice` 開啟時，會等該行的語音播完才離開最後一頁。長台詞的每一頁
各自完成打字與 Auto 延遲；中間頁不會被仍在播放的同一句語音卡住。

**Skip** 是兩段式，由 `skip_unread_only` 決定：

- `True`（僅快進已讀，預設）：只快進**已讀過**的行，碰到第一行未讀或任何選項就停。
- `False`（全部快進）：連未讀的行也一路快進，直到選項或場景結束。

在進入上述 logical-line 快進前，視覺層固定依序執行：補完目前頁、折疊到同一句的
最後一頁、再快進台詞。翻頁不會重新套用 effects、不會新增對話記錄／已讀紀錄，也
不會建立額外 rollback checkpoint；選項仍必須由玩家另一次輸入確認。

對話框會依實際字型 metrics、解析度 density 與文字倍率量測三行（短畫面為兩行）
正文空間。單一 authored line 太長時會保留同一份語音、effects 與 story cursor，
只在閱讀面上切成多頁；頁尾顯示 `目前頁 / 總頁數`。手動存檔會保存頁碼、reveal
cursor 與 authored-text cursor，所以改用不同解析度或文字倍率讀檔也不會多顯示或
漏掉文字。

YAML literal block 的 authored 尾換行仍保留在 plain／reveal cursor，確保存檔與 wait
位置不漂移；pagination 只排除最後連續的空 layout rows 與 Unicode whitespace。剛好
填滿三列的 `a\nb\nc\n` 因此仍是一頁，下一次確認直接進下一句；正文中間的空白列
不會被刪除。LF／CRLF、全形空白、NBSP 與短畫面真正需要翻頁的案例都有回歸。

### Pack 作者

這些都是**玩家偏好**，不是 pack 內容；作者通常不用碰。相關 config 欄位（皆可在
設定畫面調整、自動持久化，見下方「設定持久化」）：

行內節奏可用 `[w]`（預設 0.4 秒）或 `[w=N]` 在下一個 authored character 前停頓。
`N` 必須是有限正數且不超過 86400；同一文字位置連續 wait 的合計也受相同上限約束。
這是防止錯字破壞 runtime／存檔的安全上限，不是建議把玩家停住一天。validator 會把
非有限、負值、零、超限或累積超限視為 error；容錯 runtime 則把無效單筆降級為 0.4 秒
並限制合計，確保即使繞過 validator 也能存讀檔。

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
uv run world-gal-game capabilities --pack <pack>
```

通用 effect / condition 參考見 [effects-reference.md](effects-reference.md) /
[conditions-reference.md](conditions-reference.md)。

> 一個完整、現成可參考的範例是 demo_pack 的
> `games/demo_pack/content/scenes/90_ending_lover.yaml` —— 它同時用到 line 層級的
> `cg`、`camera_zoom` 與 `screen_tint`（含 `clear`）。

### 中央減動政策

`EngineConfig.reduce_motion` 是即時生效的玩家偏好。所有 engine scene、共用 widget
與動畫 backend 都由同一個 `SceneContext.motion` 解讀，不應在建構時複製一份 bool。
玩家在設定 overlay 中切換後，底下已存在的對話、地圖與提示會在下一幀採用新政策：

| 動態種類 | 開啟「減少動態」後 |
|---|---|
| 焦點／hover／選取回饋 | 保留狀態，但立即就位，不跑 easing |
| 背景、CG、立繪、提示的 opacity 交接 | 保留，單段最多 0.12 秒 |
| pan、zoom、slide、rise、pop、bounce、shake、emote | 不做位移；需要的最終構圖直接就位 |
| flash、刺激性 blur | 不生成；既有 flash／blur 在切換時清除 |
| 粒子、呼吸、眨眼、脈動、文字 wave／shake | 凍結為可讀的靜態畫面 |
| typewriter、wait、語音、影片 | 視為敘事內容，保留原本進度 |

這個降級只改變表演方式，不改 story cursor、flag、好感度、時間或 callback。重設鏡頭、
清除色調／blur／天氣等指令仍會執行，避免停在錯誤的中間狀態。

---

## 場景轉場：背景 / CG 切換與轉場（Scene Transitions）

商業 VN 的「換景／換 CG」幾乎不會瞬切，而是 dissolve / 淡入黑 / wipe / 推格。引擎把
這套做成**一級轉場系統**：和上面的 camera/screen FX 同一條接縫（handler 只排指令、
不碰 pygame），由 DialogueScene 擷取「上一幀世界畫面」的快照，在新畫面之上把舊畫面
依風格「退場」，逐步揭露新畫面。文字框始終穩定地畫在最上層、不受轉場影響。

### 玩家

無操作 —— 由劇情編排自動觸發。

### Pack 作者

四個 builtin effect，都接受一個選填的 `transition` 子 dict（沒寫就是 0.6 秒 dissolve）：

| Effect kind | `target` / `value` | 說明 |
|---|---|---|
| `set_background` | `target`=背景圖路徑；`value`=轉場 dict | **場景中途換背景**（過去做不到：背景只能來自 scene.background）。會接管背景直到場景切換。 |
| `show_cg` | `target`=CG 圖路徑；`value`=轉場 dict | 帶轉場顯示全螢幕 CG。接管 CG 層（蓋過 per-line `cg`）直到場景切換。 |
| `hide_cg` | `value`=轉場 dict | 帶轉場移除目前 CG。 |
| `transition` | `value`=轉場 dict | 在目前畫面上跑一個獨立轉場 beat（如淡入黑再淡出），不改變場景內容。 |

轉場 `value` dict 的欄位：

| 欄位 | 預設 | 說明 |
|---|---|---|
| `style` | `dissolve` | 見下方風格清單 |
| `duration` | `0.6` | 秒 |
| `easing` | （無） | 緩動曲線名（見 `ui/easing.py`） |
| `color` | `[0,0,0]` | 僅 `fade` 風格用的幕色 |
| `mask` | （無） | 僅 `mask` 風格用的灰階遮罩圖路徑 |

轉場風格（`SCENE_TRANSITION_STYLES`）：

`cut`（瞬切）、`dissolve`（淡溶，預設）、`fade`（淡入幕色再淡出，經典換景 beat）、
`wipe_left/right/up/down`（硬邊掃過）、`slide_left/right/up/down`（舊畫面滑出露出新畫面）、
`iris_in/iris_out`（圓形光圈收／放）、`blinds_h/blinds_v`（百葉）、`pixellate`（馬賽克化淡出）、
`mask`（影像遮罩 dissolve，依灰階由暗到亮揭露）。

```yaml
- speaker: "林清雪"
  text: "「我們走吧。」"
  effects:
    - kind: set_background
      target: backgrounds/street_night.png
      value: {style: fade, duration: 1.2, color: [0, 0, 0]}
- text: "（畫面淡入新場景……）"
  effects:
    - kind: show_cg
      target: cg/confession.png
      value: {style: dissolve, duration: 0.8}
```

說明與注意事項：

- 不用任何新 effect 時，行為**與過去逐位元相同**（背景仍走 scene.background 的隱式
  0.6 秒淡溶、CG 仍走 per-line `cg` 瞬切）。轉場是**加分項、選擇性接管**。
- `set_background` / `show_cg` / `hide_cg` 會**接管**對應的層，直到 story scene 切換才
  交還給 scene 資料。所以同一場景請固定用 effect 或固定用 scene.background / line `cg`，
  不要兩者交錯。
- `cut` 與「沒有上一幀可轉」時直接瞬切（不動畫）。
- 開啟 `reduce_motion` 時，`cut` 維持瞬切，其餘所有風格統一退化為最多 0.12 秒
  dissolve；中途切換偏好時，正在執行的 wipe／slide／mask 也會立即改走這條安全路徑。
- `mask` 風格需要 **numpy**（桌面通常有；pygbag/WASM 與未安裝 numpy 的環境會**優雅降級
  為 dissolve**）。要啟用：`pip install numpy`。
- 轉場狀態會出現在 headless `describe()` / `inspect()` 的 `fx_active.transition`，以及
  `background` / `cg` 兩個層欄位，供 AI 檢視。

---

## 環境 / 天氣（Ambient / Weather）

雨、雪、花瓣、光點、螢火等**全螢幕氛圍層**，畫在世界層之上、文字框之下，跨行持續直到
被替換或清除。它們是第 10 個擴充類別 `@ambient_backend`，引擎內建一個 `ambient_weather`
插件提供五種 web-safe、**確定性**（存檔重播可重現）的天氣。

### 玩家

無操作 —— 由劇情編排自動觸發。

### Pack 作者

兩個 builtin effect：

| Effect kind | `target` / `value` | 說明 |
|---|---|---|
| `set_weather` | `target`=後端名（rain/snow/petals/sparkles/fireflies）；`value`=參數 | 開啟天氣。`value.fade`=淡入秒數。其餘參數（count/seed/alpha/color + 各後端自有鍵）直接傳給後端。 |
| `clear_weather` | `value`=`{fade?}` | 關閉目前天氣；`fade`=淡出秒數。 |

```yaml
- text: "（窗外下起了雨。）"
  effects:
    - kind: set_weather
      target: rain
      value: {count: 200, wind: -240, fade: 1.5}
- text: "（雨停了。）"
  effects:
    - kind: clear_weather
      value: {fade: 1.0}
```

五個內建天氣與常用參數（完整鍵見各後端 docstring / `world-gal-game capabilities`）：

| 後端 | 效果 | 常用參數 |
|---|---|---|
| `rain` | 斜向雨絲 | `count`、`speed`、`wind`、`length`、`color`、`alpha` |
| `snow` | 飄雪 + 橫向擺動 | `count`、`speed`、`sway`、`size`、`color` |
| `petals` | 櫻花瓣翻轉 | `count`、`speed`、`wind`、`size`、`color` |
| `sparkles` | 原地閃爍光點 | `count`、`size`、`speed`（閃爍率）、`color` |
| `fireflies` | 漫遊脈動的螢火 | `count`、`speed`、`size`、`color` |

說明：

- 共通參數：`count`（粒子數）、`seed`（RNG 種子，決定散佈）、`alpha`（0-255 整體不透明度）。
- 天氣**跨行 / 跨場景持續**（不會自己消失），要靠 `clear_weather` 或另一個 `set_weather`
  替換。新開的對話場景從無天氣開始。
- 開啟 `reduce_motion` 時，引擎停止後端的每幀 `update()`，但保留 seed 決定的靜態初始
  散佈，讓「正在下雨」等敘事資訊仍可見；淡入／淡出最多 0.12 秒，`clear_weather`
  仍會確實移除 overlay。關閉偏好後，同一後端可繼續更新。
- 未註冊的後端名會優雅退回「無 overlay」（不崩、不報錯）。
- 第三方可用 `@ambient_backend` 加自己的天氣（見 [plugins.md](plugins.md)）。
- 目前天氣名會出現在 headless `describe()` 的 `fx_active.weather`，供 AI 檢視。

---

## 立繪定位與兩人同框（Portrait Staging）

立繪可放在 **`left` / `center` / `right`** 三個定點（slot），同一行也可同時顯示多位
角色（兩人同框、三人交會）。沒有指定時一律置中，**所有舊場景維持原樣**。

### 玩家

無操作 —— 由劇情編排決定。可在設定關閉「非說話者變暗」(`dim_inactive_speakers`)。

### Pack 作者

定位三種寫法（由簡到繁，皆向後相容）：

```yaml
# 1) 最簡：純表情 / 字串路徑 + portrait_pos（速記，不必寫完整 spec）
- speaker: "白若雪"
  text: "「我在左邊。」"
  expression: smile
  portrait_pos: left          # left / center / right（不寫 = center）

# 2) spec + position 別名（id = expression、position = slot 的友善拼法）
- speaker: "白若雪"
  text: "「我在右邊。」"
  portrait: {character: ruoxue, id: smile, position: right}

# 3) 完整 spec 的 slot 欄
- speaker: "白若雪"
  text: "「我在左邊。」"
  portrait: {character: ruoxue, expression: smile, slot: left}
```

兩人 / 多人同框 —— 用 `portraits:` 串列，各自帶 `slot`：

```yaml
- speaker: "白若雪"             # 說話者全亮，另一位自動變暗
  text: "「小鈴，妳找的是樂譜館藏。」"
  portraits:
    - {character: ruoxue,     expression: smile, slot: left, enter: fade}
    - {character: suxiaoling, expression: shy,   slot: right, enter: fade}
```

說明：

- **單一立繪會就位到自己的 `slot`**（含上面的 `position` / `portrait_pos`）；先前一律
  置中，現在 `slot: left` 就真的在左。純字串路徑或純 `expression:`（無 `portrait_pos`）
  仍置中，與舊內容**逐像素相同**。
- **說話者強調**：多人同框時，目前說話者全亮，其餘 slot 變暗（去飽和 + 冷調壓暗）。
  旁白行（無 speaker）不會壓暗任何人。可用 `dim_inactive_speakers: false` 關閉。
- **入場演出**：角色第一次出現 / 換 slot 時會淡入並輕微上飄（`rise`）。`portraits` 的
  每個 spec 也能指定 `enter`（`fade` / `slide_left` / `slide_right` / `bounce` / `pop`）
  與 `exit`、`offset` / `scale` / `flip`。
- **無障礙**：開啟 `reduce_motion` 時，入場的滑動 / 上飄 / 彈跳一律退化為最多
  0.12 秒淡入（位置仍正確），不做位移；待機 backend 停止更新並繪製
  `base_surface()` 靜止幀。
- **CG 抑制**：整頁 CG（`show_cg` 或 scene/line 的 `cg`）顯示時，**站立立繪不另外繪製**
  （避免 CG 已含該角色又重疊一張的「雙重描繪」）。立繪狀態保留，CG 收掉即恢復。
- `portrait` 為 spec 時其自帶 `slot` 優先；`portrait_pos` 只在 spec 仍為預設 `center`
  時補上定位。

---

## 立繪定點 emote（Portrait Emotes）

讓**已就位的立繪**做一個一次性的強調動作 —— 跳一下、搖頭、點頭、彈跳 —— 然後回到
待機。它不改變立繪本身（只暫時位移 / 擠壓繪製框），所以和任何 portrait backend
（breath / layered…）並存。

### 玩家

無操作 —— 由劇情編排自動觸發。

### Pack 作者

一個 builtin effect `portrait_emote`：

```yaml
- speaker: "林清雪"
  text: "「才、才不是那樣呢！」"
  portraits:
    - character: qingxue
      slot: center
  effects:
    - kind: portrait_emote
      target: center            # slot（left/center/right）或角色名
      value: {emote: shake, duration: 0.45}
```

| `value` 欄位 | 預設 | 說明 |
|---|---|---|
| `emote` | `jump` | `jump`（跳）/ `shake`（搖頭）/ `nod`（點頭）/ `bounce`（彈跳擠壓） |
| `duration` | `0.45` | 秒 |
| `intensity` | 各 emote 自有 | px 振幅 |

說明：

- `target` 可給 slot 名，或角色名（自動找到該角色所在的 slot；找不到則退回 center）。
- emote 縮放以**底邊中心**為錨（腳不動）；動作結束自動回到待機。
- 開啟 `reduce_motion` 時，新的 emote 不啟動；正在執行的 emote 立即回到正確的靜態
  立繪框，不改變角色／表情／slot。
- 未知 target / emote 名安全退化（不崩）。
- 適合搭配台詞情緒：害羞 `shake`、贊同 `nod`、驚訝 `jump`、活潑 `bounce`。

---

## 影片播放（Movies：OP / ED / 過場）

全螢幕影片 overlay，播畢或被跳過後自動回到劇情。引擎內建一個 **web-safe 的 image-
sequence 播放器**（一資料夾的連號影格,純 pygame、零外部依賴、桌面/web 一致;代價是
檔案大、無內嵌音軌 —— 搭配 `bgm` 即可）。真 video（.mp4/.webm,含音軌）走**桌面插件**
`desktop_video`（pyvidplayer2,需 ffmpeg）。

### 玩家

播放中點擊，或使用 `confirm_advance`（鍵盤預設 Enter / Space / Z）／`back`
（鍵盤預設 Esc / X），可跳過允許略過的影片。開啟 `reduce_motion` 時，即使作者設成
`skippable: false` 或 `loop: true` 也一定保留這條退出路徑；跳過仍走正常完成
callback，不會略過劇情狀態。

### Pack 作者

一個 builtin effect `play_movie`：

```yaml
- text: "（片頭動畫播放……）"
  effects:
    - kind: play_movie
      target: assets/movies/op        # image-sequence：影格資料夾
      value: {kind: image_sequence, fps: 24, skippable: true}
- text: "（END。）"
  effects:
    - kind: play_movie
      target: assets/movies/ending.mp4  # 真 video：需 desktop_video 插件
      value: {kind: video}
```

| `value` 欄位 | 預設 | 說明 |
|---|---|---|
| `kind` | `auto` | `auto`（依路徑判斷）/ `image_sequence` / `video` / 插件註冊的名字 |
| `fps` | `24` | image-sequence 影格率 |
| `loop` | `false` | 循環（靠跳過才結束） |
| `skippable` | `true` | 是否可跳過 |

說明：

- **image-sequence**：`target` 指向一個資料夾,內含連號影格（`.png`/`.jpg`/…,依檔名
  排序）。純 pygame、web-safe。
- **真 video**：`kind: video`（或 `auto` + `.mp4`/`.webm` 副檔名）。需安裝
  `desktop_video` 依賴：`pip install "world-gal-game[video]"`（需 ffmpeg）。缺依賴 /
  缺檔案時**優雅降級**（瞬間跳過,不崩,不卡黑畫面）。
- 第三方可用 `register_movie_player(name, factory)` 加自己的播放器後端（見
  [plugins.md](plugins.md)）。`world-gal-game capabilities` 的 `markup.movie_players` 列出已註冊
  的播放器。

---

## 角色語音音量（Per-Character Voice Volume）

### 玩家

在**設定 → 音訊**中，有實際 authored `voice:` cue 的角色會各有一列 `-` / `+` 與
slider，可單獨調整語音音量；沒調過的角色沿用全域「語音音量」。若舊版玩家偏好中已有
某角色的明確 override，即使新版 pack 移除了他的 cue，該列仍會保留，讓玩家能看見並修改
既有設定。既沒有 voice cue、也沒有 override 的 NPC 不會顯示，以免大型角色表變成一面
沒有作用的 slider 牆。

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

| 語意操作 | 鍵盤預設 | 行為 |
|---|---|---|
| `quicksave` | F6 | **快速存檔** —— 寫到 `quicksave` 槽 |
| `quickload` | F9 | **快速載入** —— 從 `quicksave` 槽載入（沒有就什麼都不做，遊戲照常） |
| `screenshot` | F12 | **擷取畫面** |

F6、F9、F12 只是上述玩家操作的**預設鍵**，都可在「設定 → 操作設定」重綁；重新綁定
後舊鍵立即停止觸發該操作。F1、F5、F11 則保留給開發模式的 debug overlay、熱重載與
狀態輸出，不屬於玩家 profile，也不能指定為玩家操作。

**自動存檔**會在玩家做對話選擇 / 時間推進時，安靜地輪流寫入 `autosave_1..N` 槽
（`N` = `autosave_slot_count`）。

**存檔 / 載入畫面**是可捲動的卡片清單：

- 每張卡顯示**縮圖**（存檔當下的畫面截圖）、標題、時間。
- `quicksave` 與 `autosave_*` 槽會被**置頂**、上色並加徽章標籤，和手動存檔區分開。
- 在**存檔**模式下，`autosave_*` 槽是**唯讀**的（引擎管理，不給手動覆寫）；
  `quicksave` 仍可從畫面手動覆寫。載入模式下兩者都可載。
- 每個已存在的手動、快速與自動存檔都有獨立的「刪除」危險動作；新增存檔列沒有。
  刪除不會和載入／覆寫共用 hit target，也不會由普通確認鍵直接觸發。
- `nav_up` / `nav_down`（鍵盤預設方向鍵 / W / S；手把方向輸入）移動存檔列；每次
  換列都把焦點重設在載入／覆寫等主要動作。`nav_left` / `nav_right` 才在主要動作與
  刪除間切換，`confirm_advance`（預設 Enter / Space / Z；手把確認鍵）執行，`back`
  （預設 Esc / X；手把返回鍵）返回；焦點會自動捲入可視區。360p、720p 與 1080p
  會依字型行高與可用空間重新排版，兩個操作目標都至少 44 px。
- 覆寫既有存檔或載入遊戲前會開啟確認層，預設焦點在「取消」。取消不改 state、
  不改存檔；成功、取消與錯誤都有畫面內狀態回饋。
- 刪除使用文案與顏色都獨立的 danger confirmation，預設同樣停在「取消」；取消是
  零副作用，返回清單時回到主要動作。成功後焦點移到原位置的下一筆（末筆則前一筆）、
  清除該槽縮圖快取並保留完成訊息；找不到檔案或 I/O 例外會保留原清單、焦點與捲動位置。
- 縮圖先寫到獨立暫存 PNG，JSON 完整序列化與同步後才提交；若最後 JSON replace
  失敗，舊 JSON 與舊縮圖會維持成對。以無縮圖覆寫時會移除舊 PNG，PNG 編碼失敗則
  降級成沒有縮圖的有效存檔，不會阻止 state 保存。
- 刪除也以 JSON 作為 commit point：先把縮圖移到隱藏 tombstone，最後才原子移走 JSON；
  JSON commit 失敗會復原縮圖。啟動時若看見「JSON 仍在、縮圖已暫存」的中斷狀態，會
  自動還原縮圖；JSON 已移走則視為刪除已提交，只清理殘留 tombstone，不會復活存檔。
- 長清單的 offscreen buffer 依實際內容高度成長並跨幀重用；超過 9000 px 的內容仍可
  捲到最底，不再受固定 8000 px 截斷。

只有具正式 save payload 結構的 JSON 才會出現在 Continue 與存讀檔清單。
`clear_data.json`、設定、備註或 metadata-only JSON 會被排除；舊版 v0 raw
`GameState` 存檔仍可列出並在載入時遷移。

對話中的手動、快速與自動存檔會保存同一份 versioned presentation resume。外層
`_resume.version: 1` 保存目前行／選項、ADV 視覺頁、raw 與 authored-text cursor、NVL
transcript；voice checkpoint 與 visual timeline 各自有可演進的內層版本。Pack migration
會在 `_resume` 尚未從 save payload 分離前一起看到它，因此 scene rename、cursor shift 與
presentation metadata 可和 `GameState` 同批遷移。讀檔會在第一次繪製前還原，不會重跑
line effects、hooks 或 read-log 寫入；解析度、字型或文字倍率改變時，wait 仍以 authored
plain-text position 對齊。早期 v1 resume 沒有 additive wait timeline 仍可載入，但因沒有
舊進度證據，只能沿用從該 wait 邊界重新計時的相容行為。玩家可修改的 corrupt／不一致
timeline 會在替換 live state 前 fail closed。

同一份 resume 也保存目前這一次 voice playback 的 finite position／duration 與
playing／completed 狀態。Voice checkpoint v2 另帶 decoded PCM SHA-256 與 mixer format；
新存檔的 playing voice 只有在素材身份完全相同時才以 PCM frame 對齊續播。已播完、主動
停止、同長度內容遭替換或後端不支援定位時保持安靜，不會從頭重播已聽過的台詞。沒有素材
身份的內層 v1 checkpoint 採 silent fallback；只有完全沒有 `voice_playback` 欄位的舊外層
resume，因缺乏「當時仍在播或已播完」的證據，維持原本從頭播放的相容行為。
Backlog、Replay、Music Room 等暫時接管聲音的畫面使用同一 checkpoint，未替換 voice 時
保留原 channel，替換後則從被中斷位置續播。

可語意重建的背景 crossfade、camera、tint、background blur 與 weather opacity handoff
會保存 endpoints、elapsed、duration 與 easing。讀檔時依目前解析度重新載入素材；玩家已
開啟 reduced motion 時，以目前 accessibility policy 完成到穩定目標。依賴舊 framebuffer
的 composed scene transition、shake／flash／emote 與任意 plugin particle phase 不會把
Surface 寫進 JSON，也不會重跑 gameplay effect；它們明確 settle／drop，weather backend
則依 authored seed 重新開始內部 phase。載入畫面本身採 hard cut，不會從載入前的畫面
產生一個無關 dissolve。背景來源與目的地也會在任何 asset access 前綁定到目前 pack 的
`scene.background`／`set_background` authored refs；被修改的存檔不能要求讀取任意路徑。

讀檔把 save 視為進度候選，不視為內容來源：目前 pack 的 StoryGraph 會完整取代存檔中的
line、condition、effect、choice、transition 與 card，只保留有效 cursor／played ids；map
會取目前 location／exit／hook／ambient catalog，只搬回仍存在的 current／visited 進度；
NPCRegistry 永遠使用目前 pack 的 NPC 與 portrait backend。resume 裡的背景、CG、portrait、
voice、SFX、BGM、ambient、weather 與 choice image 必須再對這三個 authority 精確驗證，
而存檔自己夾帶的 StoryGraph 不能擴張 allowlist。退役 active story scene 或越界 cursor
必須先由 pack migration 修正，否則整個 load 候選被拒絕。

Quickload、Continue 與正式 load 先在 detached `GameState` 完成 migration、catalog/media
rebind 與 resume preflight；失敗時 live state、scene stack 與 load overlay 都不變。直接對
已存在 `DialogueScene` 做 restore 時，則先取得 audio rollback checkpoint，在全新的 widget
state 重建成功後才提交；checkpoint 取不到會在任何 mixer／UI mutation 前停止，backend
例外會復原舊 widget 與可恢復的 audio ownership。這個交易保證不包含無法復原的外部 plugin
side effect，因此 plugin 仍不得在 presentation restore 中做不可逆 I/O。

載入是 audio replacement boundary：voice 一律先停止；若 saved BGM／ambient 和目前不同，
引擎會以 `fade_ms=0` hard-cut 所有 outgoing channel，包括 metadata 已是 `None`、但 mixer
仍在 fade tail 的 untracked audio，再嘗試播放目前 authored path。替代檔缺失或損毀時結果
是安靜，不會讓載入前的曲目漏進新畫面；同一 tracked path 則保留原 playback point。

圖片 decode／scale 的 `OSError`、`MemoryError` 與 `pygame.error` 會降級為符合目的尺寸且可
cache 的 placeholder；短音效／voice decode 失敗則保持 silent 且不污染 sound cache，exact
voice restore 回報明確 silent fallback。Plugin handler、system chrome、portrait／ambient
backend 呼叫由 `isolate()` 各自包住，單一 `Exception` 會記錄後降級，不會終止其他 handler
或主迴圈。這不是 plugin sandbox，也不吞 `KeyboardInterrupt`／`SystemExit`；fallback 自身
若已無法配置記憶體，仍可能向上拋出。

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

## 設定畫面（Settings）

Settings 是完整選單「系統」頁底下的 subordinate overlay，分成四個固定分頁：**閱讀**、
**音訊**、**輔助使用**、**操作／顯示**。分頁列固定在可捲動 body 外；寬畫面以單列四欄
顯示，窄直向畫面改排成 2 × 2，翻譯後的長標籤仍保有完整文字與至少 44 px 的操作目標。
各頁只顯示自己的控制項，不再把所有偏好堆成同一面長清單。

- **輔助使用優先**：每次新建 Settings 都先開啟輔助使用頁，初始焦點依序放在高對比、
  減少動態效果、文字大小。因此從剛開啟的畫面計算，高對比只需一次
  `confirm_advance`；減少動態效果需一次 `nav_down` 加確認；文字大小需兩次
  `nav_down` 加確認，分別是 1／2／3 次語意操作。
- **分頁狀態穩定**：每個分頁各自以 stable control id 保存焦點與 bounded scroll。
  切到別頁、因設定改變而 rebuild，再切回時會回到原控制項並捲入可見範圍。
- **響應式音訊列**：空間足夠時，名稱、百分比、`-`／`+` 與 slider 共用一列；窄直向
  畫面把 slider 疊到第二列，仍保留完整寬度與觸控 ownership。角色音量只列出有
  authored voice cue 或既有明確 override 的 NPC。
- **返回不遺失脈絡**：從完整選單進入 Settings 時，原本的 `MenuScene` 留在 stack
  下層。`back` 關閉 Settings 後會恢復同一個 Menu 物件、系統分頁與「設定」焦點，
  不會直接掉回遊戲或重建一份選單。
- **全螢幕是即時玩家偏好**：操作／顯示頁切換 `fullscreen` 後，桌面 App 立即把新值
  套到 window，再寫入 `settings.json`；headless 執行不建立視窗，但仍保存下次真實啟動
  要採用的值。若即時 display 切換拋出 `pygame.error`，控制項會回復原值而不寫入失敗狀態。

以上是自動化結構、語意輸入與 App 接線契約；候選截圖仍須經人工檢視與核准，實體觸控、
控制器、作業系統全螢幕及 disabled-player usability 也仍是正式發行 gate。

---

## 操作設定與語意輸入

玩家從**完整選單 → 設定（Settings）→ 操作設定（Controls）**進入按鍵編輯器。引擎把
「要做什麼」表示成穩定的 `InputAction`，把「按了哪個鍵」留在玩家 profile；因此本文
提到的 Esc、F6、F9、F12 等都只是預設鍵，不是場景契約。

常見對應如下：

| `InputAction` 值 | 鍵盤預設 | 用途 |
|---|---|---|
| `confirm_advance` | Enter / 數字鍵 Enter / Space / Z | 確認、啟動控制項、推進對話 |
| `back` | Esc / X | 返回或取消 overlay |
| `open_menu` | Esc / X | 在閱讀／探索情境開啟選單 |
| `nav_up` / `nav_down` | ↑ / W、↓ / S | 焦點垂直移動 |
| `nav_left` / `nav_right` | ← / A、→ / D | 焦點水平移動 |
| `toggle_auto` / `hold_skip` | A、左／右 Ctrl | Auto 與按住快進 |
| `quicksave` / `quickload` | F6 / F9 | 快速存檔與快速讀取 |
| `screenshot` | F12 | 擷取畫面 |

### 綁定、衝突與持久化

- Controls 顯示每個 action 的**有效綁定**；選一列後才進入 capture。成功的變更寫入
  `EngineConfig.input_bindings`，並立即透過 `config.save_to_disk()` 持久化到
  `<writable_root>/settings.json`。重設操作採二段確認，完成後移除 overrides、回到
  預設 profile。
- 新鍵若已被**相同操作情境**的另一個 action 使用，編輯器交換兩者的 primary binding，
  避免同一次輸入靜默觸發兩個動作；同時撞到多個 action 時拒絕套用並留在 capture。
  不會在同一情境並存的 action 可以共用實體鍵，例如閱讀與探索各自使用 A。
- `confirm_advance`、`back` 與四向 `nav_*` 是必要操作。正規化設定與衝突交換都不允許
  它們完全失去綁定；缺失、損毀或只含無效鍵的設定會回退到預設救援綁定。
- F1、F5、F11 保留給 dev mode 的 debug overlay、hot reload 與 state dump。它們不進
  玩家 profile，Controls 也拒絕綁定。F12 則是可重綁的玩家 `screenshot` action，
  不屬於保留鍵。

Capture 是一個 modal 輸入狀態。捕捉到的實體鍵只供 Controls 嘗試綁定：同一幀即使
也對應 `quicksave`、`quickload`、`screenshot` 或其他全域 action，都不會向 App、
目前頁面或底下場景穿透。Esc、以滑鼠／觸控選擇標題列的「取消設定」、手把返回與右鍵
都只會取消 capture，不會順便關閉 Controls 或改動綁定。錯誤後仍留在 capture，讓玩家
直接再按另一個鍵。

所有按鍵提示都從目前的 `BindingProfile` 動態產生，包括頁尾的移動／確認／返回、
標題列關閉鍵、對話／選項的閱讀提示與影片跳過提示。玩家重綁後，UI 在 rebuild／
下一幀顯示新鍵；pack 文案不應自行寫死舊預設。

### 鍵盤、手把、右鍵與觸控

`InputRouter` 將不同裝置正規化成同一份每幀輸入契約：

- 鍵盤依 `input_bindings` 產生 `pressed` / `held` / `released` 的語意 action。
- 手把 A／B、D-pad、肩鍵與選單鍵直接映射到確認、返回、導覽與翻頁等同一組 action；
  鍵盤重綁不會讓手把失效。
- 右鍵同時送出 `back` 與 `open_menu`，由目前 overlay／閱讀／探索情境決定是返回還是
  開啟選單，不由 widget 猜實體按鈕。
- 觸控 tap 在放開時才成為 click／推進；拖曳則提供 `touch_delta`，並保留 primary
  contact ownership 到放開。場景用和滑鼠相同的 enable guard 與 callback，因此停用
  項目不會因裝置不同而被繞過。

Settings 與 Controls 的長內容都支援滑鼠滾輪、語意導覽與**觸控垂直拖曳**。手指從
可捲動 body 開始後，移動超過 tap 門檻才改變 scroll，捲動值固定 clamp 在內容上下界；
已成為 drag 的 release 會被消耗，不會誤點放開位置的按鈕。Controls 的焦點捲動以完整
操作列為範圍，所以在 360p 也不會只露出按鈕、裁掉操作名稱。

Settings 的 slider 有更高的手勢 ownership：touch 若從可見 slider 的加大 hit area
開始，body 不會搶成垂直捲動；slider 會持續追蹤 primary contact 的水平位置直到放開，
即使手指暫時離開細軌道也一樣。從 slider 以外開始的手勢才由頁面捲動接管。

### 場景與插件作者

場景、widget 與插件應查詢 `InputAction`，不要硬編碼玩家實體鍵：

```python
from world_gal_game.ui.input_bindings import InputAction

if inp.pressed(InputAction.OPEN_MENU):
    open_menu()
if inp.held(InputAction.HOLD_SKIP):
    continue_skipping()
```

使用 `inp.pressed()` / `held()` / `released()` 可同時取得自訂鍵盤與手把語意，也能保留
右鍵與觸控的場景級處理。只有 Controls 的 raw-key capture，以及 F1/F5/F11 的 dev-only
診斷路徑，才應直接查看實體 key code。

---

## 設定持久化

上面提到的所有玩家偏好（音量、文字速度、`auto_play_*`、`skip_unread_only`、
`nvl_mode`、`fullscreen`、`visual_accessibility_mode`、`reduce_motion`、`text_scale`、
`show_sound_captions`、`per_character_voice_volume`、`input_bindings`、`autosave_*`、
`quicksave_slot` …）都會寫到 **`<writable_root>/settings.json`**，每次在設定畫面改動即時存檔
（`config.save_to_disk()`），下次啟動自動載回（`config.load_from_disk()`）。

`settings.json` 是一份**純玩家偏好**文件：pack / 路徑 / dev 欄位刻意不寫進去，所以
它在不同 pack 之間可攜。檔案缺失、損毀或含未知 key 都會被容錯處理（用預設值，
不會崩）。

這個檔案同時被視為**不可信輸入**。布林欄位只接受真正的 JSON boolean，不會把
`"false"` 或 `1` 以 truthiness 轉型；音量、文字／Auto 速度、延遲與文字倍率只接受
有限且在支援範圍內的 JSON number，numeric string、boolean、`NaN`、Infinity 與越界值
都會被拒絕。角色音量 mapping 另限制總量、角色 id shape 與每個 0–1 音量值；壞掉的
top-level payload 不會抹除目前有效狀態，混合 mapping 則只保留合法項目。寫入前也走同一
套 canonicalization，避免 embedding client 直接改 dataclass 後把污染值留到下次啟動。

跨周目的 `clear_data.json` 另保存全域已讀 scene、CG、結局與已通路線。引擎在初啟、
New Game、quickload、Continue 與正式 load 都把它與當前 state 做聯集；只回填目前
pack 仍註冊的結局，且標為已看過，避免舊內容復活或重播「新結局」提示。CG 在寫入與
套用 clear data 兩端都會和目前 media authority 取交集，所以舊檔或手改檔不能把任意
image path 洗進下一周目。返回標題、選單／OS 退出，以及同步／非同步 loop 的例外清理
都會嘗試原子落盤。

Settings 與 Controls 也使用同一套響應式密度與焦點規則：360p／450p／720p／1080p
皆以實際字型行高排版，鍵盤／手把焦點可見並會自動捲動；滑鼠與觸控路徑維持相同的
enable guard、音效與 callback 語意。兩頁的觸控捲動與 Settings slider ownership
契約見上節。

---

## 一行檢查表（Pack 作者）

| 想要 | 怎麼做 |
|---|---|
| CG 進鑑賞牆 | 把圖放 `assets/cgs/`，在某行掛 `cg: assets/cgs/x.png` |
| 曲目進音樂室 | 把音樂放 `assets/bgm/`，在 line / 場景掛 `bgm:` |
| 新增一個結局 | 在 `content/endings.yaml` 加一筆 + 在路線收尾 `set_flag` |
| 結局依角色分組 | 結局寫 `route_id`，對應 NPC 標 `is_heroine` + `route_id` |
| 鏡頭推近 / 抖動 / 染色 | 在 line 的 `effects:` 用 `camera_*` / `screen_*` |
| 場景中途換背景 | 在 line 的 `effects:` 用 `set_background`（帶 `transition`） |
| 帶轉場顯示 / 隱藏 CG | 用 `show_cg` / `hide_cg`（帶 `transition`） |
| 淡入黑再淡出的換景 beat | 用 `transition` + `value: {style: fade}` |
| 下雨 / 下雪 / 花瓣等氛圍 | 用 `set_weather` / `clear_weather`（帶 `fade`） |
| 立繪跳動 / 搖頭 / 點頭 | 在 line 的 `effects:` 用 `portrait_emote` |
| OP / ED / 過場影片 | 在 line 的 `effects:` 用 `play_movie`（影格資料夾或 .mp4） |
| 角色語音可單獨調 | 在有配音的行掛 `voice:` 與正確 `speaker:` |
| 重要聲音有文字替代 | 在同一行加 typed `sound_captions`；不要從檔名生成或預告未交付 cue |
| 場景可重溫 | 自動 —— 玩家讀過就會出現 |

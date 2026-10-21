# 商業 GalGame 品質基準與差距矩陣

基準日期：2026-08-01

## 結論先行

World Gal-Game 已經是能力面相當完整、AI 開發介面突出的 GalGame 引擎；它還不是
「已證明可發行」的商業引擎。用來壓力測試引擎的長篇 reference pack 已結構完整、
可通關、資產量可觀；但它目前也還不是《白色相簿 2》《千戀＊萬花》同級商品。

差距主要不在 scene、ending 或 effect 的數量，而在下列可由玩家直接感受到的結果：

- 每一場戲的寫作密度、角色一致性與數十小時後仍能回收的選擇後果。
- 原創音樂身份、聲優表演、逐場 cue sheet、混音與安靜場景的環境聲。
- 本作專用 key art、產品 UI、角色表演與關鍵 CG，而不是只有「圖片能顯示」。
- 存讀檔、鍵盤／手把／觸控、720p／1080p、長時間執行與平台打包的阻斷式 QA。
- 外部編輯、目標玩家、聲音與平台 QA 的驗收證據。作者或 AI 不能自行宣布自己已
  達到名作水準。

因此本文件把「同品質」定義成同樣可靠的單場寫作、角色弧、演出、閱讀體驗與發行
可靠度；不是複製他作內容，也不是用規模數字代替品質。

## 方法與限制

本基準使用官方產品／支援頁、平台商店頁與少量專業評論，提取可觀察的產品行為；
對敘事感染力的評價只作質性方向，不把口碑當成客觀規格。作品版本、平台與地區不同
時，功能也可能不同，故不以單一版本的功能缺失推論整個系列。

評估分成兩個對象：

1. **Engine**：評的是「能否讓任意 pack 實現並可靠驗證這項品質」，不是引擎自己有
   沒有劇情、美術或聲優。
2. **Demo pack**：評的是玩家實際會收到的內容與資產；引擎有能力但 pack 沒有使用，
   不能算完成。

所有內部分數都只是 triage。正式評分必須由專案外的編劇／編輯、目標玩家、視覺、
聲音、無障礙與平台 QA 重新驗收。

## 市場作品提供的可驗證基準

### 《白色相簿 2》：情緒因果與閱讀連續性

官方產品頁列出 Motion Portrait、新增／改寫劇情、追加 CG，以及通關後解鎖的 voice
drama、digital novel 等內容；這顯示完成一條主線後，產品仍用不同媒介補足角色與
餘韻。[WHITE ALBUM2 官方產品頁](https://aquaplus.jp/wa2/product.html)

官方系統頁把 Auto、Skip、已讀標記與已選選項標記當作基本閱讀工具；另一頁也顯示
選項會依進度出現。這些不是附加功能，而是長篇敘事能讓玩家放心閱讀、重跑與辨識
新內容的基礎。[Auto／Skip／已讀系統](https://aquaplus.jp/wa2/system02.html)、
[進度條件選項](https://aquaplus.jp/wa2/system03.html)、
[Motion Portrait](https://aquaplus.jp/wa2/system.html)

本專案採用的設計推論是：選擇不能只在同一場加減好感度；重要承諾應在後續日常、
路線衝突與結局被角色記住。這是對作品結構的質性 benchmark，不是官方宣稱的量化
指標。可作為補充口碑樣本的 Famitsu 頁面不應取代實際盲測。
[Famitsu 使用者評論頁](https://www.famitsu.com/game/title/28316/reviews)

對 World Gal-Game 的要求：

- 重要選擇要有 delayed callback、跨章 reader 與契約測試。
- Save/load、rollback、skip 與 read-log 不得重播 effects 或弄丟當前選項。
- 未選角色仍須繼續生活，讓「沒選」成為可見的代價，而不是角色從世界消失。

### 《千戀＊萬花》：閱讀友善度與角色品牌呈現

官方規格同時列出 1920×1080 與 1280×720；Steam 頁列出日語語音、英文／日文／
簡中／繁中與 Steam Cloud。這使解析度、語言、語音與跨裝置存檔成為可驗證的產品
承諾，而不是開發機上的最好情況。
[官方規格](https://www.yuzu-soft.com/products/senren/spec.html)、
[Steam 產品頁](https://store.steampowered.com/app/1144400/SenrenBanka/)

官方支援／更新頁可見流程圖跳轉、backlog voice replay、喜愛語音 playlist、CG 差分、
menu customization、hotkeys、章節時間與角色別 system voice 等細節；同一頁也記錄
FHD 記憶體碎片、Direct3D 與影片播放 fallback 等修正。成熟產品的 QoL 與相容性並非
一次完成，而是能被重現、修補和回歸。
[千戀＊萬花支援與更新紀錄](https://www.yuzu-soft.com/products/senren/senren_sup.html)

角色頁則把每位女主角的視覺與個性作為購買前就能辨識的產品內容。
[官方角色頁](https://www.yuzu-soft.com/products/senren/chara.html)

對 World Gal-Game 的要求：

- 720p／1080p 的 title、dialogue、choice、save、settings、gallery 都要有審核過的
  visual baseline。
- Backlog voice replay、flowchart、read/selected markers、hotkeys、分軌與角色別音量
  必須經完整玩家流程測試，而不是只存在 class 或按鈕。
- Demo 需要本作專用 title/key art、menu chrome、角色 profile 與聲音身份。

### 《STEINS;GATE》：敘事內機制與存檔可靠度

官方網站把手機操作、複數路線與結局放在作品核心，而不是把選項做成與世界無關的
彈出選單。[STEINS;GATE 官方網站](https://steins-gate.com/)
《STEINS;GATE ELITE》的商店頁則以動畫素材融合敘事作為版本主賣點。
[STEINS;GATE ELITE Steam 頁](https://store.steampowered.com/app/819030/STEINSGATE_ELITE/)

評論同時提供反例：GameCritics 肯定影音融合但提到無法重綁控制；Nintendo World
Report 記錄過 hard lock／autosave 問題。名作也會在輸入與存檔可靠度上失分，因此
本基準不把知名度等同於零缺陷。
[GameCritics 評論](https://gamecritics.com/joshua-tolentino/steinsgate-elite-review/)、
[Nintendo World Report 評論](https://www.nintendoworldreport.com/review/49798/steinsgate-elite-switch-review)

對 World Gal-Game 的要求：

- Pack 應能用 plugin 實作「存在於世界內」的選擇機制，核心不加入特定遊戲邏輯。
- Autosave 必須在 narrative state 與 presentation 都提交後才寫入；任何 corrupt
  save、無效 resume envelope 或 plugin handler 都不得造成 crash、重播副作用或空畫面。
- 鍵位重綁與純鍵盤／手把 traversal 是發行 gate，不應重複市場作品的已知缺口。

### 《魔法使之夜》與《Fate/stay night REMASTERED》：逐行導演與流程可讀性

《魔法使之夜》Steam 頁列出 full voice 與 Full HD；官方規格頁列出語言、解析度與
controller／mouse／touch 等輸入方式。這些是「電影感」之外仍可驗收的基礎。
[Steam 產品頁](https://store.steampowered.com/app/2052410/WITCH_ON_THE_HOLY_NIGHT/)、
[TYPE-MOON 官方規格](https://typemoon.com/products/mahoyo/product/)

Nintendo World Report 將其描述為偏線性、強調視覺演出的作品。這提醒本專案：
分支數量不是唯一價值，逐句鏡頭、節奏、聲音與留白也能構成高完成度。
[Nintendo World Report 評論](https://www.nintendoworldreport.com/reviewmini/62021/witch-on-the-holy-night-switch-review-mini)

《Fate/stay night REMASTERED》提供另一個長篇、多路線產品參考；RPGFan 評論提到
flowchart 的價值，也指出 UI 遲滯與不能重綁等摩擦。
[Steam 產品頁](https://store.steampowered.com/app/2396980/Fate_Stay_night_REMASTERED/)、
[RPGFan 評論](https://www.rpgfan.com/review/fate-stay-night-remastered/)

對 World Gal-Game 的要求：

- 支援 line-level 背景、CG、立繪位置／表情、camera、tint、blur、weather、BGM、
  ambient、SFX、voice 與 transition，只是最低能力；正式 pack 要有逐場 direction
  sheet 與人工 review。
- Flowchart 必須幫助玩家理解已讀分支並安全跳轉；不能破壞 flags、解鎖或存檔。

### 無障礙與工具鏈基準

Xbox Accessibility 指南依文字用途給出不同的 1080p 基線：一般必要文字預設至少
26 px，對話／字幕預設至少 32 px；large-text 設定要能把前者放大到 38 px，PC
對話／字幕則要能放大到至少 46 px。文字對比至少 4.5:1；Microsoft 的遊戲無障礙
指南也涵蓋輸入、字幕、色彩與認知負荷。
[Xbox Accessibility Feature Tags](https://learn.microsoft.com/en-us/xbox/accessibility/accessibility-feature-tags)、
[Windows 遊戲無障礙概覽](https://learn.microsoft.com/en-us/windows/uwp/gaming/accessibility-for-games)

這些數字不是所有平台的唯一標準，但足以讓「看起來還可以」變成可檢查的最低線。

工具面則以 Ren'Py 官方 CLI 與 developer tools 作為成熟 VN 生態的參考：專案至少要
有 lint／驗證、可重現啟動、互動式檢查與 release 自動化，而不只是能從 IDE 執行。
[Ren'Py CLI](https://www.renpy.org/doc/html/cli.html)、
[Ren'Py Developer Tools](https://www.renpy.org/doc/html/developer_tools.html)

## 統一 10 維度 rubric

每項只允許整數 0–4。證據不足時向下取整；「程式碼中有 class」不等於 3 分，
「作者自己覺得好」也不等於外部驗收。

| 維度 | 0 | 1 | 2 | 3 | 4 |
|---|---|---|---|---|---|
| 敘事結構與回收 | 無可玩敘事 | 片段可讀但無弧 | 有完整骨架，回收薄或重複 | 全線經編輯、長期因果可見 | 多輪外部編修與盲測證明名場面、伏筆、餘韻皆穩定 |
| 角色一致性與弧線 | 角色只是標籤 | 語氣／動機漂移 | 主角有設定與基本弧 | 主配角跨章一致，未選角色仍生活 | 外部讀者能僅憑語氣辨識角色，弧線在多輪盲測成立 |
| 分支與後果 | 假選項 | 只改即時數值 | 有路線與結局，但 callbacks 稀薄 | 重要選擇跨章回收且可檢查 | 分支在敘事、演出與系統上都形成可重玩的不同經驗 |
| 逐行演出 | 靜態文字 | 少量背景／立繪 | 有常用 FX，但大量場景未導演 | 每場有 direction/cue sheet 且人工 review | 節奏、鏡頭、留白、聲音與表演一致支援情緒 |
| 立繪／CG／品牌視覺 | 缺檔或 placeholder | 風格／角色身份不穩 | 引用完整但缺人工 QA／產品門面 | key art、UI、立繪、CG 經一致性 QA | 差分、構圖、表演與品牌視覺在全流程維持高辨識度 |
| 音樂／SFX／Voice | 無聲或壞檔 | 零散／佔位 | 可播但聲音身份、cue 或表演不足 | 原創音樂、完整 soundscape、關鍵語音與混音驗收 | 全語音或等價表演規格，leitmotif、ducking、loop、loudness 全數 QA |
| UI 與閱讀 QoL | 阻礙通關 | 基本點擊 | 常見功能存在但流程／佈局未證明 | 全輸入、存讀檔、backlog、auto/skip、flowchart 經 E2E | 長篇重讀與鑑賞細節完整，玩家幾乎不需對抗介面 |
| 無障礙 | 無設定 | 只有音量／速度之一 | 有縮放或減少動態，但覆蓋不全 | 對比、字幕、重綁、純鍵盤、縮放、減少動態皆通過 | 多種障礙使用者與平台規範共同驗收 |
| 效能與發行可靠度 | 常 crash／無 build | 只在開發機可跑 | 有 build 路徑，缺真機長跑與 budget | 支援平台有 cold start、長跑、save compatibility、frame/memory gate | 多平台 crash-free、升級／回退、遙測與 post-launch 回歸證據完整 |
| AI／作者工具與 QA | 只能手改 | 有基本 validator | 可檢查結構與 smoke | 可推理 dataflow／impact、精確編輯、visual／contract gate | 從規格到發行證據可追溯，所有 gate 可在 CI 重現且防假綠燈 |

正式 release gate：

- 加權總分至少 **85/100**。
- 每一維至少 **3/4**；敘事、角色、逐行演出、視覺、聲音必須由專案外人員評分。
- 任何 crash、save corruption、讀檔重播副作用、全鎖選項、不可達宣告結局、缺必要
  授權、缺 reviewed visual baseline 或支援平台無法啟動，都直接阻斷，不得用平均分抵銷。

## 權重

引擎與遊戲 pack 的責任不同，因此使用不同權重；兩者都必須各自過 gate。

| 維度 | Engine 權重 | Demo 權重 |
|---|---:|---:|
| 敘事結構與回收 | 6 | 20 |
| 角色一致性與弧線 | 5 | 15 |
| 分支與後果 | 12 | 10 |
| 逐行演出 | 12 | 12 |
| 立繪／CG／品牌視覺 | 8 | 10 |
| 音樂／SFX／Voice | 8 | 8 |
| UI 與閱讀 QoL | 14 | 8 |
| 無障礙 | 12 | 5 |
| 效能與發行可靠度 | 11 | 5 |
| AI／作者工具與 QA | 12 | 7 |
| 合計 | 100 | 100 |

計分公式為 `Σ(分數 / 4 × 權重)`。

## World Gal-Game 當前基線

這是 2026-08-02 working tree 的保守 provisional score，不是發行認證。為避免把
「引擎能表達」誤算成「產品已證明」，下表同時要求能力存在、回歸測試通過，且至少
一個 in-repo reference pack 提供相應 production evidence；只具備 API 的項目最高
只能落在 2 分。

| 維度 | 權重 | 分數 | 加權 | 目前證據／主要缺口 |
|---|---:|---:|---:|---|
| 敘事結構與回收 | 6 | 2 | 3.00 | YAML、variables、chapters、contracts 可表達；沒有商業長篇 pack 的外部編輯證據 |
| 角色一致性與弧線 | 5 | 2 | 2.50 | NPC／affection／profile／dataflow 有能力；沒有語氣一致性或角色盲測 gate |
| 分支與後果 | 12 | 3 | 9.00 | condition/effect registry、planner、reachability、softlock、impact、coverage、rollback 完整且可測；尚無商業長篇分支盲測 |
| 逐行演出 | 12 | 2 | 6.00 | line-level staging、camera、CG、weather、movie 等能力齊；缺商業規模逐場 direction sheet 與人工 review |
| 立繪／CG／品牌視覺 | 8 | 2 | 4.00 | multi-slot、backend、gallery、transition 可用；reference pack 已有 v3 key-art／Logo review candidates，但人工核准、Live2D/Spine、關鍵結局 CG 與真素材 QA 未證明 |
| 音樂／SFX／Voice | 8 | 2 | 4.00 | BGM/ambient/SFX/voice bus、replay、per-character volume、exact PCM voice checkpoint 與嚴格 silence sentinel 有；load 會切掉 abandoned audio，Backlog 會還原底層 authored ownership；原創素材、codec、loop、mix、真機證據仍不足 |
| UI 與閱讀 QoL | 14 | 2 | 7.00 | save/load、page-aware auto/skip/rollback、backlog、flowchart、首次閱讀 onboarding、4K measured ADV viewport、YAML 尾換行不產生空白末頁、語意輸入與 persisted Controls 重綁均有回歸；仍缺真實裝置 E2E、外部 UX 與人工核准 visual baseline |
| 無障礙 | 12 | 2 | 6.00 | text scale、速度、音量、live reduce-motion、可重綁、純鍵盤與 typed sound captions（設定／Backlog／Replay／headless）有自動回歸；全 UI 對比、色弱、OS 偏好偵測、不同鍵盤配置、完整 cue coverage 與身障玩家驗收不足 |
| 效能與發行可靠度 | 11 | 2 | 5.50 | 有 build、單元/headless 測試與 decoder/plugin failure containment；地圖 focused local benchmark 為 MapScene 20.75 ms、detail 16.56 ms、0 full clone；仍缺支援平台 cold start、長跑、frame/memory/crash-free gate |
| AI／作者工具與 QA | 12 | 3 | 9.00 | context、impact、planner、warm NDJSON、editor、contract、self-check、strict visual 與 fail-closed Production Check v1 可重現；尚無商業規模 pack 的 passing production artifact |

暫定加權分：**56.0/100**。沒有任何一項得到 4 分，且多項只有能力證據、沒有外部
production evidence，所以不能宣稱 production-ready。

本輪已直接關閉的玩家可靠度缺口：

- Manual／quick／autosave 共用 `_resume.version: 1` 的 JSON-safe dialogue presentation
  envelope；voice checkpoint 與 visual timeline 各自有內層版本，pack migration 在分離
  `_resume` 前可和 state 同批更新 scene／cursor／presentation。讀檔不重播 line effects，
  能回到行、選項與 NVL transcript；voice v2 會區分 playing／completed，並以 decoded
  PCM SHA-256／mixer format 驗證素材身份後，從 frame-aligned 中斷位置續播。無身份的
  舊 checkpoint、同長度替換檔、decoder 失敗或不支援的 backend 都 fail silent；只有
  完全缺少 `voice_playback` 的舊外層 resume 保留從頭播放的相容行為。
- Autosave 在 dialogue transaction 與 presentation 都完成後寫入。
- Load 是 hard-cut replacement boundary：voice 先停止，不同／silent BGM 與 ambient 在
  嘗試 authored replacement 前以 `fade_ms=0` 清掉所有 outgoing slot，包括 tracked path
  已為 `None` 的 fade tail。缺檔或損毀替代素材因此 fail silent，不會漏播 abandoned audio；
  相同 tracked path 不重啟。
- 圖片 decode／scale 的資產例外降級成 cacheable placeholder，短音效／voice decode 與
  exact restore 失敗降級成 silent 且不污染 cache；plugin handler 與 renderer backend 的
  一般 `Exception` 由 `isolate()` 記錄並局部降級。這不是 sandbox，也不證明所有 codec、
  OOM 或 target-device failure 已通過。
- ChoiceMenu 支援鍵盤／手把焦點、disabled-row skipping、wrap 與 confirm；只有一個
  enabled option 時會立即聚焦並可直接確認，真正的多選決策仍等待玩家先指定目標。
  當 1.4× 文字無法容納完整比較卡時，會量測後切換為角色 tabs、可捲動預覽與固定
  44 px CTA。Tabs（含 disabled）只切換預覽，不能直接提交；locked reason 與 action
  分離，觸控拖曳使用 pointer capture，避免放開時誤選。
- 輸入層改以穩定的 semantic action id 驅動；`InputState` 同時提供 pressed、
  released、held 與 raw physical key edge／held，`InputRouter` 保留跨幀鍵盤／手把
  狀態、失焦釋放與 legacy synthetic event。鍵盤 profile 可即時重綁，但手把的
  confirm／back／方向語意不會被鍵盤設定改寫，因此同一互動有完整鍵盤與手把路徑。
- Controls 頁會保存正規化後的按鍵 profile，依 action context 判斷衝突並可交換；
  F1／F5／F11 保留給引擎，必要的 confirm／back／navigation 不得被清空，恢復預設
  則回到空 overrides。Capture 直接讀 physical key，且 capture frame 不會穿透到
  場景或觸發 F6／F9／F12 等全域動作。
- Onboarding、畫面提示與 Controls 可由 active `BindingProfile` 產生動態按鍵標籤；
  狹窄元件可只顯示 primary／concise label，不再把預設鍵名硬寫進提示，也避免多鍵
  profile 撐破卡片。
- Line 可 author 最多四筆 typed semantic sound captions；高對比穩定 overlay 會在
  360p–4K、1.0–1.4 倍文字下量測 HUD／AUTO／SKIP／quick menu 與 NVL 正文安全區。
  設定、ADV／NVL、存讀檔、rollback、Backlog、場景重溫與 headless 都保存同一份資料；
  hostile resume／replay 仍需通過長度、shape、canonical copy 與 authored provenance。
  這不等於螢幕閱讀器支援、完整 caption coverage 或由聽障玩家完成驗收。
- Backlog 重播使用共用 voice channel 前會擷取 authored audio ownership，關閉、pop、
  setup 例外與重複 teardown 都只還原一次，不再把歷史語音留在主線上。
- Choice 可選擇 strict typed `card` presentation；同一決策的 2–4 個可見選項全數
  opt-in 時，ChoiceMenu 以 720p／1080p 等高比較卡及 450p compact card 呈現人物圖、
  時間標籤、三列差異、證據 tags 與動作。功能 `text`、authored order、整卡 hitbox、
  disabled reason、focus ring 與輸入仍由 core 擁有；mixed／legacy 選項保持原列表。
  完整比較頁會依來源方向配置媒體：直式角色圖進 3:4 identity frame、橫式 CG 進
  16:9、近方形來源保留方形；文字與人物並排，fit／focus／zoom 契約不變。真實三張
  1086×1448 角色圖已覆蓋 720p／1080p／4K × 1.0／1.4 文字、焦點、CTA 與捲動；
  這仍是工程候選，不是 human-approved baseline。Night Folio 4K／1.4 的完整 draw
  60 個暖幀中位數為 9.753 ms（含每幀背景重填 11.932 ms），plugin render miss 為 0；
  128 MiB chrome byte-LRU 可保留 79.826 MiB working set，離開 choice 會釋放 transient
  render caches。未採用會改暗捲軸等半透明元件的全畫面預合成捷徑。
- Card metadata 會一致進入 rollback、dialogue resume、headless presentation、
  `affordances()` 與 capability schema；load 以目前 authored StoryGraph 重新綁定
  scene、line、choice、條件、effects、轉場及 card，只從存檔保留已知 played id 與
  有效 cursor；map 同樣以目前 location／exit／hook／ambient catalog 保留有效進度，
  NPC／portrait backend 永遠來自目前 registry。Resume 的背景、CG、portrait、audio、
  weather 與 choice image 在 decoder/plugin access 前對這些 authority 驗證，save 內夾帶
  的 story 不能擴張 allowlist。被刪除的 active scene／超界 cursor 需由 pack migration
  先更新，否則在 live state／scene stack 交換前原子拒絕；直接 restore 另以 fresh widget
  state 與 audio snapshot transaction 保護既有畫面。Backlog voice 亦會清除 authored line
  allowlist 外的路徑。選項尚未真正顯示前，`affordances()` 不再提前洩漏 choice/card 或
  hidden choice。
- Planner checkpoint 會同時保存 GameState 與已到達的 presentation boundary；最後
  一句與隨後選項即使共用 story cursor，也不會在 visited-set 被折疊或在分支還原時
  遺失。可重播路徑因此仍須先真正顯示選項，才會產生 `choose` action。
- AI／測試用 `GameDriver.skip_dialogue` 只快轉呈現；遇到任何可見 choice 會停在決策
  邊界。即使唯一選項已為一般玩家自動聚焦，工具也不會代替玩家提交劇情分支。
- 首次新遊戲改用精簡 onboarding，只教當下需要的推進、選擇與開啟選單；唯一的
  「開始閱讀」動作進場即有可見焦點，Space／Enter／滑鼠與 Esc 都能離開，並以
  720p、1080p、450p 佈局測試與 persisted `seen_intro` 避免重複打斷。
- `ui_chrome/v1` 已延伸到 onboarding、ADV／NVL、姓名牌、閱讀工具列、狀態徽章、
  繼續提示與 choice。Renderer 只畫底板；文字、disabled veil、可見 focus ring、
  hit-test 與輸入順序仍由引擎擁有。未處理或 renderer 失敗時有 stock fallback
  回歸，不會讓 pack skin 接管閱讀邏輯。
- TitleScene 改成字體量測與內容驅動佈局，覆蓋 1280×720 與 1920×1080。
- TitleScene 離場時會以 350 ms fade 停止自己啟動的 title BGM；因此 pack 刻意讓
  第一場安靜時，不會等到後續 cue 才被動蓋掉標題曲。若正文已先宣告自己的 BGM，
  稍後清除的 stale title base 不會再切斷正文音樂。無參數的舊／自訂 asset backend
  仍有相容 fallback，兩種所有權交接都有專門 contract test。
- Dialogue 的 status HUD 與 quick menu 不再固定使用 14/15 px；依 canvas 放大控制高度，
  並以自動測試守住 720p 17 px、1080p 26 px 的一般必要文字下限。
- Dialogue quick menu 從十個同級操作收斂為六個閱讀主操作＋「更多」；高風險快讀移到
  次層，Escape／點外收起會消耗整幀，overlay 不再透出底層 chrome；觸控 44 px、長翻譯
  tooltip、ADV／NVL 安全區與 Auto 臨界競態都有 GUI 回歸。Tab／Shift+Tab、方向輸入、
  確認鍵與 More layer 都可由鍵盤／控制器到達，確認輸入不會穿透去推進台詞。
- ADV 閱讀面改由實際字型 metrics 與完整 canvas density 共同量測，覆蓋
  720p／1080p／4K、1.0×／1.4× 文字與 800×450。普通長台詞保留同一 authored
  presentation，只把 laid-out rows 分成視覺頁；Manual／Auto／Skip／rollback、
  voice、effects、read log、backlog 與 choice boundary 都有整合回歸。
- 存檔以 additive page index、raw reveal cursor 與 authored-text cursor 保存閱讀
  位置；同布局保留 implicit-wrap 側別，跨解析度／文字倍率則回到同一 authored
  character，不會多顯示正文。連續空白列、頁界 wait 與 trailing newline 亦有回歸；
  authored 尾換行保留在 plain／reveal cursor，但 pagination 只裁掉 terminal empty layout
  rows 與 Unicode whitespace，所以 LF／CRLF 的三列 YAML literal 都不會多出空白末頁，
  全形空白／NBSP 亦不會建立假頁，而內部空白列仍保持可見；
  active wait 另以 authored plain position 保存 consumed set 與剩餘秒數，在 ADV／NVL、
  頁界和 reflow 後不會重播整段停頓。舊 v1 缺欄位仍可載入，hostile shape、非 canonical
  consumed set、future／unknown wait、負值／非有限／超過 authored duration 的剩餘時間
  會在 state swap 前 fail closed。背景 crossfade、camera、tint、blur 與 weather opacity
  另保存可重建 timeline；raw-frame scene transition、粒子 phase 與 transient shake／flash
  明確 settle／restart／drop，不宣稱 pixel-exact；背景 path 在 asset access 前綁定目前
  pack 的 authored scene／effect refs。文字 fade 當下 alpha 尚未保存，且單一
  超高 `[size]`／ruby row 仍需 release validator 阻擋。
- 從標題開始新遊戲會重建 state、對話與 plugin／bridge runtime；上一輪的時間、
  路線、好感、背包、地圖與 plugin state 不會滲入，只保留明確定義的跨周目資料與
  玩家名稱。跨周目的已讀、CG、仍由目前 pack 註冊的結局會在啟動、新周目與各種
  讀檔路徑做聯集；menu quit、OS QUIT、同步／非同步 loop 例外都會落盤與清理。
- `clear_data.json` 與其他非存檔 JSON 不再製造假的 Continue；存檔覆寫與讀檔都有
  預設焦點在取消的確認層，取消經測試不改變 state 或檔案。縮圖與 JSON 採 staging
  與 rollback；最後 JSON replace 失敗不會留下舊進度配新縮圖，無縮圖覆寫也不殘圖。
- CG／Music Room unlock 會和只由目前 StoryGraph、CG manifest、BGM directory 與啟動時
  明確註冊項目建立的 `MediaAuthority` 取交集；絕對、traversal、退役與 save-only path
  不會進 decoder。`clear_data.json` 在記錄與套用 CG 兩端使用相同 authority，不能把手改
  path 洗進下一周目。
- 遊戲內 20 個同級入口改為記錄／鑑賞／系統三類；save/load、settings、CG gallery
  都改成依實際字型量測的 360p／450p／720p／1080p 佈局，鍵盤、手把、滑鼠共用
  activation 與可見焦點。
- 共用 ScrollArea 改為依內容成長且跨幀重用的 offscreen buffer；超過 9000 px 仍可
  顯示最底列，內容縮短會同幀 clamp，不再每幀配置固定 8000 px buffer 與第二張 view。
- 地圖 hot path 的 focused local measurement 為 **MapScene 20.75 ms**、目的地
  **detail 16.56 ms**，且計數器記錄 **0 次 full clone**；route probe 改走
  輕量路徑，detail 只在條件預覽確有需要時建立隔離 preview。這是開發機上的局部
  路徑證據，不是支援平台的 render-frame、cold-start、RSS 或 soak gate。
- Map Interaction Polish v1 將 `marker:story`、`marker:current`、
  `marker:ordinary` 與 `edge:off_campus:<direction>` 定義成穩定的 map chrome
  語意：reference pack 的 Night Folio 可在引擎分配的固定 rect 內畫 marker／card／
  direction connector，但不能改功能文字、enabled 狀態、hit-test 或輸入順序。
  Hover／focus 裝飾以約 0.2 秒的有限 feedback 收斂，沒有 ambient pan、pulse、
  parallax 或 layout shift；所有 node rect 與至少 44 px hit geometry 維持不變。
  Live reduced-motion 在 zero-dt update 直接 snap 到終值，而 engine-owned
  高對比 focus ring 從第一幀立即可見、不等待 pack decoration。Reference pack 的
  西北校外卡也以 connector 形狀表達方位並保留已授權縮圖與署名，不只靠顏色。
- pack 可用 `verification.expected_unreachable` 明列只供舊存檔／直接重播的相容
  scene 與 ending；原始檢查結果仍保留，錯字、重複、目標不存在、類型不符或目標重新
  變為 reachable 都會令 self-check 失敗，不能用例外把新回歸洗成綠燈。
- `visual-check --strict-baselines` 與 `self-check --strict-visual-baselines` 在 baseline
  缺失時失敗，不再讓第一張未審畫面自動成為自己的正確答案。
- 預設 visual-check 現在捕捉十六個語意正確且可重現的 view：title、真實 fresh
  New Game onboarding stack、dialogue、choice、post-intro exploration、故事優先地圖、
  全部可達地圖、真實 `TravelPlan` 移動確認、pause menu、save、load、settings、
  CG gallery、有內容的 dialogue history、非空的 evidence-backed scene replay 與
  flowchart；每個 view 同時驗 1280×720 與 1920×1080，共 32 張，並固定
  seed／dt／離屏滑鼠、
  隔離本機 saves/settings。地圖情境會先沿真實出口／抵達 hook 走到
  `auto_open_after_scenes` 的 authored 交接點，不直接注入教學完成旗標；移動確認只
  報價、不提交地點或時間。缺人工審核 baseline 時仍應是 0/32，而不是自動通過；
  新產出的 candidate 也不等於已核准 baseline。
- 對話回顧已從「每幀繪製完整 500 筆歷史」改為只保存列幾何、直接畫 viewport 與一列
  overscan；滑鼠、觸控、鍵盤與手把共用焦點模型，舊存檔顯示名稱可回退解析 canonical
  NPC id，語音重播使用角色個別音量。Dummy-SDL 的 500 筆本機量測中，720p／1080p／
  4K owned surface 由約 101／221／856 MiB 降為 0，首次 draw 由約 567／623／1060 ms
  降為 6.93／9.07／25.48 ms；這是開發機微基準，不取代目標裝置 frame-time soak。
- 流程圖節點不再把歷史場景接回 live `_start_dialogue`。只有具有完整 first-view
  presentation trace 的節點能進 `SceneReplayScene`；legacy read bit 仍會標成已讀，
  但保持不可點擊，不會打開空白重播。Trace 保存當時實際出現的條件行、插值文字、
  HUD、內建視覺 FX 與選擇證據；播放不重新判斷條件、不呼叫 LLM、不 dispatch effect、
  不跟隨 chain，未選分支保持鎖定。正常結束、語意 Back、overlay removal 與例外都會
  依 revision 還原 BGM／ambient／voice，並只停止回想期間擁有且尚未被 channel 重用的
  SFX。route、時間、道具、旗標、事件／對話歷史、read log 與 story cursor 都有不污染
  live state 的回歸。Replay FX 必須是目前 builtin line／inbound transition 可產生的精確
  payload；BGM／ambient carry、line override、SFX／voice／CG／portrait 與 NPC speaker
  identity 都會對目前 StoryGraph／map／NPC provenance 驗證，非 NPC 的歷史姓名文案則可
  保留。252 列的本機 dummy-SDL 720p 微基準中，owned surface／首次 draw
  由約 67.8 MiB／49 ms 降至約 0.488 MiB／3.2 ms；這不是目標裝置 frame-time gate。

2026-07-31 的歷史 clean-working-tree 里程碑（不是目前證據）：engine
**2,199 passed、0 failed**，另有 16 個既知 dummy-SDL renderer warning（96.04 秒）。
`examine`／`gift`、對話中 map／move／time 鎖定、逐 phase ambient interruption 與
post-dialogue hook 都有玩家等價回歸；reference pack 另有不使用
`start_scene`／`apply`／直接狀態修改的 New Game-to-ending 自然重播。當時所有
visual manifest 均為 `review_candidate`；strict visual gate 仍如實為 0/32，全部缺
人工核准 baseline。這些仍是內部自動證據，不會把下列外部驗收缺口改寫為完成。

2026-08-02 的 current working-tree 證據為：engine 完整 suite
**2,640 passed、0 failed、3 skipped**（116.75 秒；16 個既知 dummy-SDL renderer
warning）；ADV 空白末頁、cursor 邊界與一般 Enter 推進聚焦矩陣另為
**42 passed、0 failed**。Reference pack 的 self-check 與 smoke 全綠，但人工
approved baseline 仍為 0，strict matrix 仍為 0/32 FAIL；內部全綠不會取代人工畫面、
外部玩家與目標裝置驗收。

本輪之後仍缺的關鍵證據：

- Reading-loop chrome 與 onboarding 現在有語意 request、fallback 與佈局 contract，
  但這只證明引擎接縫。尚無 reference pack 提交經 UI／art director 人工核准的
  onboarding、ADV、NVL、choice、focus／disabled 狀態之 720p／1080p baseline；
  自動生成的 candidate 即使像素 diff 通過，也不能升格為 approved baseline。
- 預設 32 張 visual-check 已包含真實 onboarding 與 flowchart scenario，但目前沒有
  人工核准基準與 passing production artifact。現有 onboarding 單元／GUI／像素
  測試仍不能替代第一次玩家是否真的理解操作的目標玩家測試。
- Semantic input、Controls 重綁、conflict swap、capture no-through 與動態提示已有
  自動回歸，但尚未以實體控制器跑完整產品，也未覆蓋不同地區／非美式鍵盤配置、
  不同裝置 focus restoration，或由身障玩家進行操作與疲勞驗收。這些與人工核准的
  interaction／visual baseline QA 仍是外部 release gate，不能由 synthetic pygame
  event 或作者自測取代。
- Title BGM handoff 目前是 scene／asset-double contract 證據；真實支援平台仍需用
  實際 codec 驗證 fade、安靜開場、下一首 cue、sleep/resume、loop、音量與長跑。
- 沒有外部 UX、視覺或聲音 reviewer 的簽核，也沒有任何新 evidence 支持把 Engine
  的 UI／聲音維度升到 3 分；本輪 provisional score 因此維持 **56.0/100**。

這些只提高可靠度基線，不會自動提高 Demo 的寫作、聲音或美術分數。

## Reference pack 基線

引擎同時以一個非公開的長篇 reference pack 做端到端壓力測試。該 pack 的逐維度
評分、證據 ledger 與 manifest 雜湊保存在它自己的 repo，不隨引擎公開；本文件只
保留對引擎有意義的結論：

- 結構面（多章節、多結局、上百個 choice、資料流零債）可以由引擎工具鏈驗證到全綠。
- 尚未達標的維度集中在寫作密度、原創音樂／語音、人工核准的視覺 baseline 與外部
  玩家驗收——這些都不是引擎能自行宣告完成的項目。

資料流零債只證明精確 choice 已有 reader，不等於角色弧線、逐行演出、情緒節奏與外部
玩家驗證已達商業敘事完成度。

## 差距優先級

### P0：任何新增內容前先關閉可靠度假綠燈

- 引擎全套單元測試、Demo GUI E2E、pack 全部 headless smoke 與宣告結局 reachability、
  variables／chapters／contracts 必須同時綠。
- 為 title、onboarding、dialogue line、choice、save/load、settings、map、gallery、
  flowchart 建立人工審核並提交的 720p／1080p baseline；CI 用 strict mode。
- 把已完成的 source-level exact save regressions 擴成 release matrix：ADV／NVL、line／
  choice、BGM silence、voice、weather、CG、無 current location、legacy／corrupt／跨版本
  journey，並在每個承諾平台用真實 codec 與 packaged build 執行。
- 為支援平台定義 cold-start、frame-time、peak RSS／VRAM、1 小時與 8 小時 soak、
  audio loop、sleep/resume 與 crash-free gate。
- 以實體控制器與不同地區鍵盤配置重跑完整 traversal；由身障玩家驗證重綁、焦點、
  長時間操作負荷與錯誤恢復，並由專案外 QA 核准 interaction／visual baselines。

### P1：只做一條可外部驗收的 vertical slice

先選一條角色路線，不平均擴充所有路線。8–12 小時是 reference pack 的產品目標，不是市場作品的官方
時數。Slice 必須包含：

- 初遇、至少兩個只屬於她的日常、一次摩擦、一次修復、路線鎖定、高潮、好／壞結局，
  以及一年後明確回收的 delayed callback。
- 原創 heroine leitmotif、共通日常、高潮變奏、逐場 cue sheet、環境床、diegetic
  SFX、loop/loudness/ducking 規格。
- 初遇、鎖定、高潮與結局的人工 voice direction partial voice；先證明選角、錄音、
  切句、呼吸、返工與混音流程。未經人工驗收的 TTS 不算正式資產。
- 本作專用 title/key art、menu chrome、地圖與高潮／結局 CG；每張經角色身份、
  手部、服裝、透明邊緣、構圖與授權 QA。
- 至少一位外部編輯與 10 名目標玩家盲測；阻斷問題清零後才能把方法複製到其餘路線。

### P2：把通過的 slice 擴到完整產品

- 將同一份 narrative bible、direction sheet、audio spec、UI component 與 QA gate
  套到其餘路線，補未選女主與配角的跨年生活。
- 重要 choices 全部建立跨章 reader；writer-no-reader 只允許明確註記為 telemetry／
  一次性呈現的 flag。
- 完成 runtime localization、CJK／Latin font fallback、文字膨脹、全 UI key 覆蓋，
  並由母語 reviewer 驗收簡中／繁中／英文等承諾語言。
- 以已完成的語意輸入、Controls 重綁與 typed sound-caption 契約為基礎，把字幕擴到
  全作所有已交付的重要 cue，補齊高對比／色弱，並在實體鍵盤／手把／觸控流程邀請
  相應使用者驗收；未交付的 Foley／Voice 不得先寫成已存在。

### P3：平台發行與 post-launch

- Windows／macOS／Web／Android／Steam 只有在各自 CI build、簽名／依賴、真機啟動、
  save compatibility、影片／音訊與長跑通過後，才能列為支援平台。
- 保存 migration fixture、舊版 saves、crash reproduction、performance traces 與
  發行候選版 checksum；每次更新重跑同一套 gate。
- 建立回報分類、severity/SLA、hotfix／rollback 流程與授權／credits 清單。

## 必須由外部資源完成的工作

「不計代價」仍不能讓程式碼取代專業判斷。下列工作是達標所需的真實依賴：

| 領域 | 最低外部角色 | 必須交付的證據 |
|---|---|---|
| 敘事 | GalGame 編輯、continuity editor、目標讀者 | scene notes、改稿紀錄、盲測問卷與通過條件 |
| 角色／語音 | voice director、casting、聲優、錄音／剪輯工程 | casting bible、line list、take approval、loudness／noise report |
| 音樂／聲音 | 作曲、sound designer、mix engineer | 原創 stems、loop、cue sheet、LUFS/true peak、授權 |
| 視覺 | art director、角色／背景／UI artist、人工 QA | style bible、model sheet、逐資產 review、source／license |
| 無障礙 | 對應障礙使用者與顧問 | 任務式測試、問題 severity、修復後複測 |
| 平台 QA | Windows/macOS/Web/Android/Steam 真機與發行工程 | build log、cold start、soak、save upgrade、crash/perf report |
| 法務／發行 | 授權與商店素材審核 | asset provenance、credits、privacy／平台文件、商店頁證據 |

## 可重跑的內部證據

從引擎 repo：

```bash
uv run pytest -q tests
uv run python -m world_gal_game.cli self-check games/demo_pack
uv run python -m world_gal_game.cli visual-check games/demo_pack \
  --strict-baselines

# 只有 pack 已提交 strict production policy、32 張人工核准 baseline、
# 100% 聯集 coverage、非空 contract 與具數值上限的 runtime policy 才應通過
uv run python -m world_gal_game.cli production-check /path/to/pack \
  --policy /path/to/pack/content/production.yaml \
  --report /tmp/release-evidence/production-check.json
uv run python -m world_gal_game.cli production-verify \
  /tmp/release-evidence/production-check.json --pack /path/to/pack
```

從任何 pack repo（以 pack 根目錄為工作目錄）：

```bash
uv run python -m world_gal_game.cli context "$(pwd)"
uv run python -m world_gal_game.cli self-check "$(pwd)"
uv run python -m world_gal_game.cli variables "$(pwd)" --check
uv run python -m world_gal_game.cli chapters "$(pwd)" --check
uv run python -m world_gal_game.cli contract "$(pwd)"
uv run --with pytest python -m pytest -q
```

每個 release candidate 都必須把日期、commit、平台、命令與結果存成 artifact。README
中的舊數字、第一次自動生成的 baseline、AI 自評與「我這邊可以跑」都不能替代當次證據。
Production Check v1 只把 clean、tracked、執行前後不變的來源樹與上述五個自動 gate
綁成有期限 artifact；封裝檔啟動、目標裝置／真實音訊／實體控制器、無障礙與法務
驗收仍必須另外保存，不能由 source preflight 推論。

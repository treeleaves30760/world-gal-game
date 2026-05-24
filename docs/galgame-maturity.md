# GalGame 成熟度:動態立繪與影片(Phase 5 設計)

Phase 5 把引擎推向商業 VN 的「演出/製作」門檻。三項中 **i18n 抽取已完成**
(`tools/i18n_extract.py`);本文是另外兩項 —— **Live2D/Spine 動態立繪**(5A)與
**影片播放**(5B)—— 的設計 spike:落地點、技術選項、web 限制與建議路線。

兩者都**牽涉外部依賴與渲染決策**(哪個函式庫、桌面 vs web),屬於需要你拍板的決策,
不適合盲目實作;下面把決策點列清楚。

---

## 5A — Live2D / Spine 動態立繪

### 現況與限制

立繪目前是**靜態 PNG + 槽位動畫**:`core/portrait_spec.py`(`PortraitSpec`:base +
expression + pose + outfit 的 fallback 鏈)決定貼哪張圖,`scenes/dialogue_scene.py`
做三槽 staging,`ui/portrait_anim.py`(fade/slide/bounce/pop)+
`ui/transitions.py`(`PortraitCrossfade`)做進出場動畫。

- **Live2D** 官方 Cubism SDK 是 C++/專有,**沒有可用的 pygame 綁定**,純 Python 無
  法直接驅動 `.model3.json`。
- **Spine** 有 `spine-python` runtime(社群),但維護與授權需評估。

### 落地點:portrait backend 抽象

關鍵是在「決定畫什麼」與「實際 blit」之間插一層 backend:

- `core/portrait_spec.py` 增一個 `backend: str = "static"` 欄位(預設不變)。
- 新增 portrait-backend 註冊點(比照八個 decorator 的模式,例如
  `@portrait_backend("live2d")`),backend 介面:`load(spec) -> handle` /
  `update(handle, dt, expression)` / `draw(surface, handle, rect)`。
- `scenes/dialogue_scene.py` 的立繪繪製改為:`backend == "static"` 走現有路徑;
  否則委派給註冊的 backend。**現有靜態路徑就是預設 backend,零行為變更。**

如此 Live2D/Spine/分層骨架都能以 **plugin** 提供,核心不綁特定函式庫(符合
「core 不放 game 邏輯」原則)。

### 選項

| 方案 | 可行性 | web(pygbag) | 備註 |
|---|---|---|---|
| 分層 PNG + 簡易變形(呼吸/眨眼/嘴型) | 高(純 pygame) | ✓ | 涵蓋多數「會動的立繪」需求,無外部依賴 |
| sprite-sheet 影格動畫 | 高 | ✓ | 美術成本高,但技術最簡單 |
| Spine(`spine-python`) | 中 | 需驗證 | 授權/維護需評估 |
| Live2D Cubism(native) | 低 | ✗ | 需 native SDK,桌面限定,工程量大 |

### 建議

1. 先做 **portrait-backend seam**(核心,低風險,不動既有靜態演出)。
2. 內建一個 **分層 PNG / sprite-sheet backend**(可行、web-safe),滿足「動態立繪」
   的大宗需求。
3. **Live2D 列為桌面限定 plugin**(native 依賴),不進核心、不保證 web。

---

## 5B — 影片播放(OP / ED / 過場)

### 現況與限制

引擎目前**沒有**影片支援(pygame 2 已移除 `pygame.movie`)。BGM/SE/voice 走
`ui/assets.py` 的 mixer;畫面層有 `ui/camera.py` 的 shake/flash/tint。

### 落地點

兩種對接方式(可並存):

- **`@scene MoviePlayerScene`** — 一個全螢幕 overlay scene,播放期間吃跳過鍵,播畢
  pop 自己。適合 OP/ED(獨立流程)。
- **`@dialogue_op` `[[movie:path]]`** — 在對白流中內嵌過場。適合場景轉換。

兩者都用既有 scene/dialogue_op 註冊點,核心不綁解碼器。

### 選項

| 方案 | 桌面 | web(pygbag) | 備註 |
|---|---|---|---|
| image-sequence(影格資料夾,引擎自播) | ✓ | ✓ | 純 pygame,web-safe;檔案大、無音軌同步 |
| `opencv-python` 解碼影格 | ✓ | ✗(WASM 無 cv2) | 真 .mp4/.webm;音軌需另接 mixer |
| `pyvidplayer2` | ✓ | ✗ | 含音軌,桌面方便;依賴 ffmpeg |
| web DOM `<video>` overlay | — | ✓ | pygbag 下用 JS overlay 蓋在 canvas 上(`platform_web.py` already bridges web APIs) |

### 建議

1. 先定義 **movie scene/op 的契約**(路徑、可跳過、播畢行為)+ 內建
   **image-sequence** 播放器(可行、web-safe、零外部依賴)。
2. 真 video 走 **平台分流**:桌面用 `opencv`/`pyvidplayer2` plugin;web 用
   `<video>` overlay(沿用 `build_web.py` / `platform_web.py` 的 web 橋接)。

---

## 需要你決定的

1. **動態立繪技術**:接受「分層 PNG / sprite-sheet backend(web-safe)+ Live2D 桌面
   plugin」的分流,還是要優先投入 Live2D(桌面限定、工程量大)?
2. **影片**:image-sequence(web-safe、檔案大)先行可接受嗎?真 video 的桌面函式庫
   選 `opencv` 還是 `pyvidplayer2`?web 是否一定要支援?
3. **優先序**:5A 與 5B 哪個先做?(i18n 抽取已完成,runtime 套用譯文是其後續。)

落地點(portrait-backend seam、movie scene/op 契約)都已盤好,任一方向拍板後即可開工。

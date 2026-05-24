# GalGame 成熟度:動態立繪與影片(Phase 5)

Phase 5 把引擎推向商業 VN 的「演出/製作」門檻。進度:

- **5C i18n 抽取 —— 已完成**(`tools/i18n_extract.py`)。
- **5A 動態立繪 —— 已實作**:portrait backend 接縫 + 內建 `breath`/`sprite`
  後端(下方 5A 節)。唯一剩下的是原生骨架(Live2D/Spine)的桌面插件,需要函式庫/
  授權決策才開工。
- **5B 影片播放 —— 待辦**:仍是設計 spike(下方 5B 節),牽涉外部解碼依賴與
  桌面 vs web 取捨,需要你拍板。

---

## 5A — 動態立繪(**已實作**)

採用「先做 seam + 內建 web-safe 後端,native 骨架走桌面插件」的分流路線(下方原始
建議),已於 2026-05-24 落地。

### 已實作:portrait backend 接縫(第 9 個擴充類別)

立繪過去是**靜態 PNG + 槽位動畫**:`core/portrait_spec.py`(base + expression +
pose + outfit 的 fallback 鏈)決定貼哪張圖,`scenes/dialogue_scene.py` 做三槽
staging,`ui/portrait_anim.py`(fade/slide/bounce/pop)+ `ui/transitions.py` 做
進出場動畫。現在在「畫哪張」與「怎麼動」之間多了一層 backend:

- **`PortraitSpec.backend: str = "static"`** + `backend_args: dict`(純加欄、可選,
  舊存檔零遷移)。`"static"` = 不動,與過去**逐位元相同**。
- **`@portrait_backend(name)`** —— 第 9 個擴充 decorator,比照既有八個的模式:
  `PortraitBackendEntry` / `PortraitBackendRegistry`,進 `plugin.yaml` 的
  `extends.portrait_backends`,`PluginManager` 比對宣告 vs 實際註冊並警告,
  `wgg capabilities` 與 markup 都列出已註冊後端。
- **後端介面**(每槽實例化 `cls(spec, assets, fallback_size)`,見
  `world_gal_game/ui/portrait_backend.py`):`update(dt)` /
  `draw(surface, rect, *, flip, alpha)` / `base_surface()`。
- **`scenes/dialogue_scene.py`**:槽位**安定後**的待機繪製委派給後端;
  enter/exit/crossfade 轉場仍走既有 surface 路徑(動畫的是 `base_surface()`)。
  後端呼叫以 try/except 隔離 —— 壞後端退回靜態繪製,絕不讓單幀崩潰。未註冊的後端名
  也優雅退回靜態。

### 已實作:內建 `animated_portraits` 插件(web-safe,純 pygame)

`world_gal_game/plugins_user/animated_portraits/` 提供兩個後端,桌面/web(pygbag)
表現一致,皆防禦式降級(缺圖→placeholder、壞參數→靜態):

- **`breath`** —— 單張立繪的程序化待機(呼吸縮放 + 起伏 + 可選晃動),**不需額外
  美術**,套在 demo_pack 現有平面立繪上即可動。參數:`period`/`scale`/`bob`/`sway`。
- **`sprite`** —— sprite-sheet 影格動畫。參數:`cols`/`rows`/`fps`/`frames`。

### 仍待辦:原生骨架(Live2D / Spine)

- **Live2D** 官方 Cubism SDK 是 C++/專有,**沒有可用的 pygame 綁定**;**Spine** 的
  `spine-python` 維護/授權待評估。兩者皆**桌面限定、需 native 依賴**,因此**不進核心**
  —— 上面的 `@portrait_backend` 擴充點讓它們可作為**桌面插件**提供。這是 5A 唯一
  剩下、且需要決策(函式庫/授權)才開工的部分。

| 方案 | 狀態 | web(pygbag) | 備註 |
|---|---|---|---|
| `breath` 程序化待機 | 已內建 | ✓ | 無外部依賴,套現有立繪即動 |
| `sprite` 影格動畫 | 已內建 | ✓ | 需 sheet,技術最簡單 |
| Spine(`spine-python`) | 待辦 | 需驗證 | 桌面插件;授權/維護需評估 |
| Live2D Cubism(native) | 待辦 | ✗ | 桌面插件;需 native SDK,工程量大 |

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

1. ~~**動態立繪技術**~~ —— **已定案並實作**:seam + web-safe `breath`/`sprite`
   後端已內建;native Live2D/Spine 走桌面插件。剩下要你拍板的只有:**是否現在就做
   原生骨架插件**,要做的話 **Spine(`spine-python`)還是 Live2D(native SDK)**
   先行?
2. **影片(5B)**:image-sequence(web-safe、檔案大)先行可接受嗎?真 video 的桌面
   函式庫選 `opencv` 還是 `pyvidplayer2`?web 是否一定要支援?
3. **優先序**:接下來做 5B 影片,還是 5A 的原生骨架插件?(runtime 套用譯文是 5C 的
   後續。)

落地點(movie scene/op 契約)已盤好,任一方向拍板後即可開工。

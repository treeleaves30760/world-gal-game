# 透明立繪 · 去背與白邊處理

立繪（portrait）和 UI 素材需要**透明背景**才能疊在場景上。這份文件講兩件事：

1. 怎麼生出乾淨、沒有白邊的透明 PNG。
2. 手上已經有白邊的舊素材怎麼救。

工具都在引擎的 `tools/`：

- `tools/imagegen_art.py` — 生成立繪 / 背景 / CG / UI。
- `tools/cutout.py` — 去背 + **邊緣顏色去汙染**（修白邊的關鍵）。

---

## 為什麼會有白邊？

> **白邊不是 alpha 的問題，是顏色汙染的問題。**

模型在白色（或近白）背景上畫圖時，髮絲邊緣的像素在物理上就是「髮色 ×
白底」的混色。就算去背把這些像素設成半透明，它們的 RGB 裡仍然**烤進了
白色**；疊到深色場景上，那層白就浮出來變成光暈（halo / 白邊）。

所以再強的去背模型都修不掉白邊 —— 必須做**前景顏色估計（foreground
colour estimation / 去汙染 / defringe）**：把邊緣像素被汙染的 RGB
換成它「真正的前景顏色」。`cutout.py` 預設就會做這件事。

另一個常見元兇：**遮罩太寬**。把多個模型的遮罩用 `max` 聯集會把輪廓
往外撐進背景，反而製造白邊。`cutout.py` 預設只用 `birefnet-portrait`
的精準邊緣，需要救手臂時才用 `--hybrid`（且會先侵蝕 u2net 再聯集）。

---

## 兩條路徑（擇一或併用）

### 路徑 B（推薦，需 OpenAI API 金鑰）：原生透明生成

讓模型**一開始就畫在透明背景上**，根本沒有白底可以汙染邊緣：

```bash
uv run tools/imagegen_art.py --kind portrait \
    --character "余寧" --description "黑髮、眼鏡、白袍" \
    --native-transparent \
    --output assets/characters/yuening/normal.png
```

- 走 OpenAI Images API（`gpt-image-1`，`background=transparent`），**每張圖計費**。
- 需要環境變數 `OPENAI_API_KEY`。沒有金鑰時會自動退回路徑 A 並接上 `cutout`。
- 只對 `portrait` / `ui` 有意義；背景 / CG / 標題不透明，會忽略此旗標。

### 路徑 A（用 ChatGPT 訂閱、零 API 費用）：生成後去背

codex 在背景上畫完，再用 `cutout.py` 把背景挖掉並去汙染：

```bash
# 生成 + 自動去背一步到位
uv run tools/imagegen_art.py --kind portrait \
    --character "余寧" --description "黑髮、眼鏡、白袍" \
    --auto-cutout \
    --output assets/characters/yuening/normal.png

# 或分兩步：先生成，再自己跑 cutout
uv run tools/imagegen_art.py --kind portrait ... --output out.png
uv run tools/cutout.py out.png
```

> 小撇步：請 codex 把立繪畫在**純綠幕**而非白底上，去背更乾淨、也不會有
> 白色汙染（`cutout.py` 會自動偵測綠幕並做去綠溢色 despill）。

---

## `cutout.py` 用法

```bash
# 一張圖，原地覆寫（生成在白/綠底上的立繪）
uv run tools/cutout.py portrait.png

# 指定輸出，不動原檔
uv run tools/cutout.py raw.png -o cutout.png

# 整個 pack 的角色圖，順便救被吃掉的手臂
uv run tools/cutout.py --hybrid "assets/characters/**/*.png"

# 修現有白邊素材：偵測到既有透明 → 先疊回白底重切（見下）
uv run tools/cutout.py "assets/characters/**/*.png"

# 原生透明圖只想清殘留邊緣，不要重切（保留 alpha）
uv run tools/cutout.py --decontaminate-only sprite.png

# 非人像主體（道具 / UI）改用泛用模型
uv run tools/cutout.py --model isnet-general-use icon.png
```

### 處理流程（segment 模式）

1. **去背遮罩**：預設 `birefnet-portrait`（邊緣精準）。`--hybrid` 會再跑
   u2net、把它**侵蝕後**的內部聯集進來救手臂，而不撐大輪廓。
2. **補洞**：填掉輪廓內部的小破洞（手指縫、袖口與手腕間的縫）。
3. **(可選) `--refine`**：用 closed-form matting 從 trimap 重算柔邊，
   對硬邊（只有 0/255）的遮罩有用。
4. **去汙染**（修白邊的關鍵步驟，預設開）：
   - `--decon unmix`：已知背景是均勻色時，用 `F = (I − (1−α)·B) / α`
     **代數反解**真正的前景色，把白/綠完全扣掉。
   - `--decon ml`：背景不均勻時，用 pymatting 盲估前景色。
   - `--decon auto`（預設）：背景均勻就 unmix，否則 ml。
   - 偵測到**飽和的去背色（綠/藍幕）**會自動加做 **despill** 去溢色。
5. 輸出 RGBA。

### 修復既有白邊素材

如果輸入**已經是去過背的透明 PNG**，`cutout.py` 會偵測到既有 alpha，
先把它**疊回背景色**（預設白，可用 `--bg-color` 改成綠 `0,255,0` 等）
重建出原始生成圖，再完整重切一次，所以邊緣會被正確重建。

```bash
# 假設這些舊圖原本生在白底上
uv run tools/cutout.py "assets/characters/**/*.png"
```

> 注意：硬邊二值遮罩（生成時就把白邊烤成不透明）能救回的程度有限 ——
> 資訊在當初去背時已被破壞。最乾淨的做法還是**重新生成**（路徑 A/B）。

### 常用旗標

| 旗標 | 作用 |
|---|---|
| `--model` | 去背模型，預設 `birefnet-portrait`。其他：`birefnet-general`、`isnet-general-use`、`u2net`… |
| `--hybrid` | 聯集 u2net 的侵蝕內部，救被吃掉的手臂 |
| `--hybrid-erode N` | `--hybrid` 聯集前侵蝕 u2net 的像素數（預設 3） |
| `--decon {auto,unmix,ml,none}` | 去汙染方式，預設 `auto` |
| `--decontaminate-only` | 保留既有 alpha，只清邊緣顏色（給原生透明圖） |
| `--bg-color R,G,B` | 修復既有素材時假設的原始背景色，預設白 |
| `--erode N` / `--feather R` | 額外內縮 / 羽化 alpha |
| `--refine` | closed-form matting 重算柔邊 |
| `--no-solidify` / `--solidify-thin-max N` | 關閉 / 調整「填補髮絲間細縫」（預設開，見下） |
| `--solidify-only` | 只填既有圖的髮絲細縫，不重切 |
| `--dry-run` | 只列出會處理哪些檔，不寫入 |

依賴（`uv` 會自動裝）：`pillow`、`numpy`、`scipy`、`rembg[cpu]`、
`onnxruntime`、`pymatting`。模型首次使用會下載到 `~/.u2net/`。

---

## 修復內部雜訊：髮絲細縫與「透明棋盤格」

去背只處理輪廓;**髮絲中間**有時還會有兩種瑕疵，來源都在生成圖本身：

### 1. 髮絲間的細縫（see-through streaks）

綠/白底在髮束之間漏出來，去背後變成**髮絲中間半透明的細縫**，疊到場景上會
透出背景。`cutout.py` 預設會做 **solidify**：把輪廓內部的**細**縫填實（顏色取
鄰近髮色），同時**保留**寬的、刻意的空隙（亂翹短髮的縫、手臂與身體之間）。
`--solidify-thin-max` 控制「多細才算要填的細縫」（預設 6px）。已切好的圖可單獨跑：

```bash
uv run tools/cutout.py --solidify-only "assets/characters/**/*.png"
```

### 2. 烤進去的「透明棋盤格」（baked-in checkerboard）

當生成模型被用**文字**要求「透明背景」時（而非真正的 alpha API），它有時會在
髮束間的空隙**畫出一塊灰白相間的棋盤格**（它對「透明」的想像）。去背後這塊棋盤
就以不透明、淺灰、空白的樣子留在頭髮裡 —— alpha matting 和去汙染都救不了，
因為那些像素在遮罩眼中是「實心前景」。

`tools/fix_checker.py` 用它的特徵（**去飽和的灰 + 亮 + 高頻紋理**，且被**深色頭髮
包圍**）找出這種色塊並用周圍頭髮 inpaint 補回。

> ⚠️ 這是啟發式偵測，**務必先 `--preview`**：它會在每張圖旁輸出
> `<name>.checkmask.png`，把偵測區塗紅讓你檢查。**蒼白膚色被深色頭髮框住時可能
> 誤判**（例如瀏海下的額頭），所以**不要無腦批次套用**，逐張確認後再實跑。

```bash
uv run tools/fix_checker.py --preview "assets/characters/**/*.png"   # 先看遮罩
uv run tools/fix_checker.py assets/characters/qingyi/worried.png      # 確認後實跑
```

最乾淨的根治法仍是 **路徑 B 原生透明生成** —— 走真正的 alpha API，模型不會在
髮縫裡畫棋盤格，這兩種內部雜訊都不會出現。

---

## 該選哪一條？

| 情境 | 建議 |
|---|---|
| 有 OpenAI API 金鑰、要做新圖 | 路徑 B `--native-transparent`（邊緣最乾淨） |
| 只想用 ChatGPT 訂閱、要做新圖 | 路徑 A `--auto-cutout`（請 codex 畫綠幕更佳） |
| 手上一堆白邊舊圖要救 | `cutout.py "<glob>"`（修復模式） |
| 原生透明圖有微量殘邊 | `cutout.py --decontaminate-only` |

兩條路可併用：原生透明生成把白邊問題從源頭消滅，`cutout.py` 則作為
通用清理 / 救援，對任何來源的素材都適用。

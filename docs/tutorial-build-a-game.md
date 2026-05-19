# Tutorial：從零到可玩 Demo——咖啡館的午後

> **學習路徑**：軌道 1 · 第一次來  
> **前置條件**：讀完 [getting-started.md](getting-started.md)（裝好引擎）  
> **下一步**：[pack-format.md](pack-format.md)（看完整 schema）或進軌道 2 各子系統  
> **完整索引**：[docs/README.md](README.md)

---

**目標讀者**：對 Python 與 YAML 都還算熟、第一次用 World Gal-Game 引擎、想做自己的 Gal-Game。

**你會做出什麼**：一款名為「咖啡館的午後」(pack id: `cafe_afternoon`) 的小 demo——玩家是咖啡館的常客，在三個時段（morning / afternoon / evening）內與兩位女主角互動，獲得不同結局。規模故意設得小：5 個場景、2 個 NPC、2 個地點、3 種物品、3 個成就。讀完這份 tutorial 你就知道把這套邏輯放大成完整遊戲所需的所有手段。

**預估時間**：2–3 小時（按節完成、途中可驗收）

**前置條件**：
- Python 3.10 以上
- [uv](https://github.com/astral-sh/uv) 已安裝（`uv --version` 可以跑）
- 已 clone World-Gal-Game 引擎倉庫到本機

---

## 目錄

1. [環境準備](#1-環境準備)
2. [Scaffold 新 pack](#2-scaffold-新-pack)
3. [第一個場景](#3-第一個場景)
4. [加角色立繪](#4-加角色立繪)
5. [加好感度與門檻](#5-加好感度與門檻)
6. [加分支選項](#6-加分支選項)
7. [加地點與時段](#7-加地點與時段)
8. [加 NPC 出沒](#8-加-npc-出沒)
9. [加物品](#9-加物品)
10. [加成就](#10-加成就)
11. [加金錢資源](#11-加金錢資源)
12. [Headless 測試](#12-headless-測試)
13. [打包成 exe](#13-打包成-exe)
14. [下一步](#14-下一步)

---

## 1. 環境準備

### 目標

讓引擎在你的機器上跑起來，並且用內建的 showcase pack 確認一切正常。

### 安裝引擎

```bash
git clone <world-gal-game-repo-url> World-Gal-Game
cd World-Gal-Game
uv venv
uv pip install -e .
```

安裝完成後確認：

```bash
uv run world-gal-game --help
```

應該看到類似這樣的輸出：

```
usage: world-gal-game [-h] --pack PACK [--headless] [--script SCRIPT] ...
```

### 跑 showcase pack

引擎倉附帶一個內建 showcase，用來確認音效、立繪、場景都能正常渲染：

```bash
uv run python main.py --pack demo_pack
```

看到標題畫面、能進入遊戲就代表環境沒問題，可以繼續。

> **注意**：如果第一次跑看到 `ModuleNotFoundError: No module named 'pygame'`，執行 `uv pip install pygame` 後再試一次。

---

現在環境確認沒問題，接下來建立我們自己的遊戲 pack。

---

## 2. Scaffold 新 pack

### 目標

產生「咖啡館的午後」的骨架目錄，確認引擎能載入它。

### 用 scaffold 工具產生

在引擎倉根目錄執行：

```bash
uv run python tools/scaffold_pack.py \
    --pack cafe_afternoon \
    --title "咖啡館的午後" \
    --subtitle "在那裡，時間過得特別慢"
```

工具會建出：

```
games/cafe_afternoon/
├── content/
│   ├── meta.yaml
│   ├── locations.yaml
│   ├── characters.yaml
│   └── scenes/
│       ├── 00_prologue.yaml
│       └── 10_meet_heroine.yaml
└── assets/
    ├── backgrounds/
    ├── characters/
    ├── cgs/
    ├── ui/
    ├── fonts/
    └── bgm/
```

### 把 pack 搬到引擎倉外面（建議）

為了讓引擎和遊戲各自獨立管理 git，把 pack 搬到兄弟資料夾：

```bash
mv games/cafe_afternoon ../cafe_afternoon
```

之後啟動用：

```bash
uv run python main.py --pack cafe_afternoon
```

引擎會自動在 `../cafe_afternoon/` 找到它。

### 把 meta.yaml 改成我們的設定

用文字編輯器開 `../cafe_afternoon/content/meta.yaml`，把內容換成：

```yaml
title: "咖啡館的午後"
subtitle: "在那裡，時間過得特別慢"

text_speed: 50

start_location: cafe
intro_scene: intro

player:
  name: "玩家"
  pronouns: "他"
```

### 怎麼跑

```bash
uv run python main.py --pack cafe_afternoon
```

### 預期結果

看到「咖啡館的午後」標題畫面。輸入名字後進入遊戲，會看到 scaffold 預設的序章（引擎產生的範例文字）。這代表 pack 被正確找到並載入了。

> **注意**：此時場景內容還是 scaffold 預設的佔位文字。接下來我們會逐步替換。

---

現在我們完成了 pack 骨架，接下來寫第一個真正屬於「咖啡館的午後」的場景。

---

## 3. 第一個場景

### 目標

寫 `scenes/intro.yaml`，讓玩家進入遊戲後看到咖啡館的開場白，並學會最基本的場景格式。

### 檔案內容

建立（或覆蓋）`../cafe_afternoon/content/scenes/intro.yaml`：

```yaml
scenes:

  - id: intro
    title: "午後的陽光"
    location: cafe
    background: assets/backgrounds/cafe_interior.png
    lines:
      - text: |
          週末的午後，你走進這間咖啡館。
          陽光透過落地玻璃斜切進來，把整個空間切成暖色的幾何。
      - text: |
          角落有人在翻書，另一側的吧台傳來研磨咖啡豆的聲音。
          這裡的空氣很適合發呆，或者思考還沒想清楚的事。
      - speaker: "玩家"
        text: "（老地方了。不知道今天會是什麼樣的一天。）"
    on_end:
      - kind: set_flag
        target: intro_done
        value: true
      - kind: log_event
        target: "你走進了咖啡館"
        value: "午後的陽光"
```

### 讓 meta.yaml 指向這個場景

確認 `content/meta.yaml` 的 `intro_scene` 欄位是 `intro`（第一節已設好）。

引擎在玩家按「新遊戲」後，會立刻播 `intro_scene` 指定的場景。

### 怎麼跑

```bash
uv run python main.py --pack cafe_afternoon
```

進遊戲，選「新遊戲」，應該看到「週末的午後，你走進這間咖啡館…」三行敘述，按空白或點擊推進。

### 預期結果

- 看到三行對白（兩行旁白 + 一行玩家獨白）
- 場景結束後，因為沒有定義咖啡館地點（下節才加），引擎會把你放在 scaffold 預設的起始地點
- 右上角不會有錯誤提示

> **注意**：`background: assets/backgrounds/cafe_interior.png` 的圖檔現在還不存在，引擎會用「條紋方框 + 檔名」的 placeholder 渲染——這是正常行為，遊戲完全可玩。

---

現在我們有了第一個場景，接下來加入角色，讓故事有人說話。

---

## 4. 加角色立繪

### 目標

在 `characters.yaml` 定義兩位女主角 Mia（店員）和 Aoi（讀書中的客人），讓對白能夠顯示說話者名稱與立繪。

### 檔案內容

覆蓋 `../cafe_afternoon/content/characters.yaml`：

```yaml
characters:

  # ---------- 女主角 ----------

  - id: mia
    name: "Mia"
    role: "咖啡館店員"
    age: 22
    is_heroine: true
    route_id: mia
    portrait: assets/characters/mia_normal.png
    portrait_set:
      smile:   assets/characters/mia_smile.png
      focused: assets/characters/mia_focused.png
      shy:     assets/characters/mia_shy.png
    description: |
      短馬尾、白色圍裙，動作很有效率但會在不經意間露出一個大的笑容。
      替你記得上次點了什麼、上次聊到哪裡。
    persona: |
      熱情但不讓人覺得壓迫；跟熟客說話的時候語速會放慢。
      最討厭浪費食物，會默默把快壞掉的糕點打折賣完。
    voice: "爽朗、句末常帶一個輕聲的「哦」或「喔」。"
    likes:    ["手沖咖啡", "新鮮甜點", "雨天的顧客很少"]
    dislikes: ["剩下很多的食物", "客人不說謝謝"]
    affiliated_location: cafe
    thresholds:
      - name: "記得你的臉"
        value: 20
        unlocks: ["mia_remembers"]
      - name: "把你當朋友"
        value: 50
        unlocks: ["mia_friend_mode"]
      - name: "不想讓你離開"
        value: 100
        unlocks: ["mia_ending_good"]

  - id: aoi
    name: "Aoi"
    role: "常客・在讀研究所"
    age: 24
    is_heroine: true
    route_id: aoi
    portrait: assets/characters/aoi_normal.png
    portrait_set:
      focused: assets/characters/aoi_focused.png
      smile:   assets/characters/aoi_smile.png
      tired:   assets/characters/aoi_tired.png
    description: |
      帶著筆電和一疊資料，永遠佔角落的那個座位。
      深色鏡框眼鏡、白色套頭衫，看起來像一直在思考什麼大問題。
    persona: |
      第一眼冷漠，但被聊到研究主題會突然滔滔不絕。
      對陌生人戒心高，但如果你不打擾她，她反而會主動說話。
    voice: "語速慢、用詞精確，偶爾說一半停下來思考。"
    likes:    ["安靜的角落", "深焙咖啡", "有趣的論文"]
    dislikes: ["突然被打擾", "太吵的音樂", "閒聊"]
    affiliated_location: cafe
    thresholds:
      - name: "不介意你坐旁邊"
        value: 20
        unlocks: ["aoi_tolerates"]
      - name: "跟你說研究的事"
        value: 50
        unlocks: ["aoi_shares_research"]
      - name: "希望你明天也來"
        value: 100
        unlocks: ["aoi_ending_good"]
```

### 在場景裡使用立繪

把 `content/scenes/intro.yaml` 的結尾加一個新場景，展示立繪出現的效果：

```yaml
scenes:

  - id: intro
    title: "午後的陽光"
    location: cafe
    background: assets/backgrounds/cafe_interior.png
    lines:
      - text: |
          週末的午後，你走進這間咖啡館。
          陽光透過落地玻璃斜切進來，把整個空間切成暖色的幾何。
      - text: |
          角落有人在翻書，另一側的吧台傳來研磨咖啡豆的聲音。
          這裡的空氣很適合發呆，或者思考還沒想清楚的事。
      - speaker: "玩家"
        text: "（老地方了。不知道今天會是什麼樣的一天。）"
    on_end:
      - kind: set_flag
        target: intro_done
        value: true
      - kind: log_event
        target: "你走進了咖啡館"
        value: "午後的陽光"

  - id: first_order
    title: "點餐"
    location: cafe
    background: assets/backgrounds/cafe_counter.png
    lines:
      - speaker: "Mia"
        text: "「歡迎！今天還是一樣的嗎？」"
        portrait: assets/characters/mia_normal.png
      - speaker: "玩家"
        text: "「嗯，中焙手沖。」"
      - speaker: "Mia"
        text: "「好——稍等一下。」"
        expression: smile
      - text: |
          她轉身，動作很流暢地開始準備。
          吧台後面的她，在你面前，還是一樣的樣子。
```

### 怎麼跑

```bash
uv run python main.py --pack cafe_afternoon
```

### 預期結果

- 進入 `first_order` 場景時，螢幕下方應出現 Mia 的立繪 placeholder（條紋方框）
- `expression: smile` 那行會嘗試切換到 `mia_smile.png`，同樣顯示 placeholder
- 說話者名稱「Mia」顯示在對白框上方

> **注意**：`expression: smile` 要對應 `portrait_set` 裡有 `smile` 這個 key 才會生效。如果你在 `portrait_set` 裡沒定義這個表情，引擎會回退到 `portrait` 預設值，不會報錯。

---

現在角色已經能在螢幕上出現，接下來讓好感度系統運作起來。

---

## 5. 加好感度與門檻

### 目標

在場景的 effects 裡加 `affection`，在 characters.yaml 的 `thresholds` 設定門檻，跑到門檻時在右上角看到 toast 通知。

### 好感度 effect

好感度用 `{kind: affection, target: <npc_id>, value: <delta>}` 修改。  
第 4 節的 `characters.yaml` 已經定義了 Mia 和 Aoi 的 `thresholds`，現在讓場景實際觸發好感變化。

把 `content/scenes/intro.yaml` 裡的 `first_order` 場景加上 `on_end`：

```yaml
  - id: first_order
    title: "點餐"
    location: cafe
    background: assets/backgrounds/cafe_counter.png
    lines:
      - speaker: "Mia"
        text: "「歡迎！今天還是一樣的嗎？」"
        portrait: assets/characters/mia_normal.png
      - speaker: "玩家"
        text: "「嗯，中焙手沖。」"
      - speaker: "Mia"
        text: "「好——稍等一下。」"
        expression: smile
      - text: |
          她轉身，動作很流暢地開始準備。
          吧台後面的她，在你面前，還是一樣的樣子。
    on_end:
      - kind: affection
        target: mia
        value: 5
      - kind: set_flag
        target: first_order_done
        value: true
```

### 門檻如何運作

`characters.yaml` 裡 Mia 的 `thresholds` 已設好：

```yaml
thresholds:
  - name: "記得你的臉"
    value: 20
    unlocks: ["mia_remembers"]
```

當好感度第一次超過 20 時：
1. 引擎把 `mia_remembers` 這個字串加入 `unlocked` 結果
2. 右上角出現 toast：「Mia：記得你的臉」
3. 你可以用 `{kind: flag, target: mia_remembers}` 作為之後場景的條件

### 手動測試門檻 toast

> **注意**：`first_order` 一次只給 +5 好感，要觸發 20 的門檻需要跑 4 次——但每個場景預設 `once: true`，所以測試時最快的方式是用 headless 的 `adjust_affection` op（見第 12 節）。
>
> 現在先用 `--dev-affection` flag 直接設好感再跑看看 toast：

```bash
uv run python main.py --pack cafe_afternoon \
    --dev-affection mia=25 \
    --dev-start explore
```

進入遊戲後的第一個 effect 觸發（例如從選單看好感頁）就會跑成就 re-evaluate，門檻 toast 會出現。

---

現在好感系統跑起來了，接下來加入真正的選擇分支。

---

## 6. 加分支選項

### 目標

在場景裡加 `choices`，讓玩家選擇走向不同的後續場景，體驗分支結構。

### 場景設計

我們在 `first_order` 之後加一個選項場景：玩家可以主動跟 Mia 搭話，或者靜靜等咖啡。

在 `content/scenes/intro.yaml` 結尾加（接在 `first_order` 之後）：

```yaml
  - id: waiting_for_coffee
    title: "等待的時間"
    location: cafe
    background: assets/backgrounds/cafe_counter.png
    lines:
      - text: |
          咖啡研磨的聲音細細地在空氣裡流動。
          你靠在吧台邊，看著 Mia 工作的側臉。
      - speaker: "玩家"
        text: "（說點什麼，還是就這樣看著就好？）"
    choices:
      - id: talk_to_mia
        text: "「這個配方是你自己調的嗎？」"
        effects:
          - kind: affection
            target: mia
            value: 3
          - kind: set_flag
            target: asked_mia_recipe
            value: true
        next_scene: mia_recipe_talk

      - id: stay_quiet
        text: "（靜靜等就好。）"
        effects:
          - kind: affection
            target: aoi
            value: 2
        next_scene: quiet_wait

  - id: mia_recipe_talk
    title: "等待的時間"
    location: cafe
    background: assets/backgrounds/cafe_counter.png
    lines:
      - speaker: "Mia"
        text: "「這個啊——中焙衣索比亞，沖的時候水溫要低一點，這樣酸味才不會太跳。」"
        expression: focused
      - speaker: "Mia"
        text: "「你喝得出來嗎？」"
        expression: smile
      - speaker: "玩家"
        text: "「老實說…分不太清楚，但就是覺得好喝。」"
      - speaker: "Mia"
        text: "「那就夠了。」"
        expression: smile
      - text: |
          她把咖啡端到你面前。
          蒸氣很薄，顏色是深褐色裡帶一點橙。

  - id: quiet_wait
    title: "等待的時間"
    location: cafe
    background: assets/backgrounds/cafe_interior.png
    lines:
      - text: |
          你沒有開口。
          角落那個一直低頭看著筆電的女生，
          不知道為什麼，抬起頭來掃了你一眼。
      - speaker: "Aoi"
        text: "「…你每週六都來。」"
        portrait: assets/characters/aoi_normal.png
      - speaker: "玩家"
        text: "「啊，是嗎…妳也是。」"
      - speaker: "Aoi"
        text: "「嗯。」"
        expression: focused
      - text: |
          她又低下頭，繼續工作。
          但某種東西，已經開始不一樣。
```

### choices 的語意

- `requires`（可選）：全部條件成立才顯示（灰色時顯示為「(條件未達)」）
- `forbids`（可選）：任何一個成立就擋掉
- `effects`：選完立刻套用
- `next_scene`：選完後接到這個場景；不寫則場景就此結束

### 怎麼跑

```bash
uv run python main.py --pack cafe_afternoon
```

注意：`intro` → `first_order` 是靠 `meta.yaml` 的 `intro_scene`，到 `waiting_for_coffee` 還沒有從 `first_order` 串過去的連結。現在先用 headless 測試：

```bash
uv run python main.py --pack cafe_afternoon \
    --headless --inspect
```

確認引擎能讀到這些場景（應該在 `scenes_available` 裡看到 `waiting_for_coffee`、`mia_recipe_talk`、`quiet_wait`）。

---

場景分支結構建好了，接下來加入遊戲的地圖與時段系統。

---

## 7. 加地點與時段

### 目標

寫 `locations.yaml`，定義「咖啡館」與「窗邊角落」兩個地點，並讓某個場景在結束時推進時段，讓玩家感受到時間流逝。

### 檔案內容

覆蓋 `../cafe_afternoon/content/locations.yaml`：

```yaml
locations:

  - id: cafe
    name: "咖啡館"
    region: "市區"
    description: |
      一間有著落地窗的小咖啡館。週末午後，陽光把木質地板曬得暖暖的。
      點餐區在右側，角落有幾張獨立的小桌子。
    background: assets/backgrounds/cafe_interior.png
    map_x: 30
    map_y: 40
    exits: [window_seat]
    tags: [indoor, relaxing]

  - id: window_seat
    name: "窗邊角落"
    region: "市區"
    description: |
      靠窗的角落座位。可以看到外面街道上的行人，
      適合一個人靜靜地喝咖啡，或偷偷觀察旁邊的人。
    background: assets/backgrounds/window_seat.png
    map_x: 35
    map_y: 40
    exits: [cafe]
    tags: [indoor, relaxing]
```

### 推進時段

在某個場景的 `on_end` 加 `advance_time`：

把 `quiet_wait` 的 `on_end` 加上時段推進（更新 intro.yaml 裡的 `quiet_wait`）：

```yaml
  - id: quiet_wait
    title: "等待的時間"
    location: cafe
    background: assets/backgrounds/cafe_interior.png
    lines:
      - text: |
          你沒有開口。
          角落那個一直低頭看著筆電的女生，
          不知道為什麼，抬起頭來掃了你一眼。
      - speaker: "Aoi"
        text: "「…你每週六都來。」"
        portrait: assets/characters/aoi_normal.png
      - speaker: "玩家"
        text: "「啊，是嗎…妳也是。」"
      - speaker: "Aoi"
        text: "「嗯。」"
        expression: focused
      - text: |
          她又低下頭，繼續工作。
          但某種東西，已經開始不一樣。
    on_end:
      - kind: set_flag
        target: met_aoi
        value: true
      - kind: advance_time
        value: 1
      - kind: log_event
        target: "和 Aoi 說了第一句話"
```

### 時段順序

引擎內建的時段順序是：

```
morning → noon → afternoon → evening → night → midnight → （下一天 morning）
```

`advance_time: 1` 推進一個時段；`advance_time: 3` 推進三個。

### 怎麼跑

```bash
uv run python main.py --pack cafe_afternoon
```

進遊戲，選「新遊戲」，完成序章後應該會進入探索畫面，看到地圖上有「咖啡館」。頂端狀態列應顯示當前時段（例如 `第 1 天 · 上午`）。

跑完 `quiet_wait` 後，時段應推進一格。

> **注意**：時段文字可以在 `meta.yaml` 的 `locale.time_of_day` 區塊客製化。不寫就用英文預設（morning、noon…）。

---

地點和時段都設好了，接下來把 NPC 安排進地點，讓探索畫面出現人物卡片。

---

## 8. 加 NPC 出沒

### 目標

在 `locations.yaml` 的地點設定 `npcs` block，指定 Mia 和 Aoi 各在哪些時段出現，讓探索畫面有 NPC card 可以點。同時加入 `scene_hooks`，讓玩家走進地點時自動觸發初次相遇場景。

### 更新 locations.yaml

把 `locations.yaml` 裡的 `cafe` 地點加上 `npcs` 和 `scene_hooks`：

```yaml
locations:

  - id: cafe
    name: "咖啡館"
    region: "市區"
    description: |
      一間有著落地窗的小咖啡館。週末午後，陽光把木質地板曬得暖暖的。
      點餐區在右側，角落有幾張獨立的小桌子。
    background: assets/backgrounds/cafe_interior.png
    map_x: 30
    map_y: 40
    exits: [window_seat]
    npcs:
      - npc_id: mia
        times: [morning, noon, afternoon, evening]
      - npc_id: aoi
        times: [afternoon, evening]
        requires_flags: [intro_done]
    scene_hooks:
      - scene_id: first_order
        trigger: auto
        requires_flags: [intro_done]
        forbids_flags: [first_order_done]
        once: true
      - scene_id: waiting_for_coffee
        trigger: examine
        requires_flags: [first_order_done]
        forbids_flags: [coffee_wait_done]
        once: true
    tags: [indoor, relaxing]

  - id: window_seat
    name: "窗邊角落"
    region: "市區"
    description: |
      靠窗的角落座位。可以看到外面街道上的行人，
      適合一個人靜靜地喝咖啡，或偷偷觀察旁邊的人。
    background: assets/backgrounds/window_seat.png
    map_x: 35
    map_y: 40
    exits: [cafe]
    tags: [indoor, relaxing]
```

### scene_hooks 的觸發邏輯

| trigger | 行為 |
|---|---|
| `auto` 或 `enter` | 玩家踏進地點時自動播 |
| `examine` | 探索畫面出現一顆「場景名」按鈕，讓玩家自己點 |

`requires_flags` 和 `forbids_flags` 都查旗標。`once: true` 代表這個 hook 只觸發一次（引擎記住之後不再重複）。

> **注意**：`scene_hooks` 的 `requires_flags` 只能查 flag（布林或 truthy 值），無法直接查資源或好感度。如果需要條件更複雜的觸發，改用 `trigger: examine`，然後在場景本身的 `requires` 裡加更細的條件。

### 怎麼跑

```bash
uv run python main.py --pack cafe_afternoon
```

進入探索畫面，移動到「咖啡館」，應該看到 Mia 的 NPC 卡片（下午／傍晚時才有 Aoi）。點 NPC 卡片開出行動 overlay（送禮 / 看貨 / 離開）。

---

NPC 現在在地點上出現了。接下來加入物品，讓「送禮」動作有東西可選。

---

## 9. 加物品

### 目標

寫 `items.yaml`，定義三種物品：一份甜點（禮物）、一杯咖啡（消耗品）、一本小說（關鍵物品）。展示 `give_item`、`use_item`、`gift` 三種操作。

### 檔案內容

建立 `../cafe_afternoon/content/items.yaml`：

```yaml
items:

  # 1) 甜點 — 送禮用，Mia 喜歡
  - id: cafe_tart
    name: "草莓塔"
    description: "Mia 自己做的草莓塔，剛好甜、剛好酸。"
    icon: assets/ui/item_tart.png
    category: gift
    matches_tags: ["新鮮甜點", "手沖咖啡"]
    value: 120
    tags: ["food", "sweet"]

  # 2) 外帶咖啡 — 消耗品，喝下去回體力
  - id: takeaway_coffee
    name: "外帶黑咖啡"
    description: "Mia 幫你裝進保溫杯，可以帶走慢慢喝。"
    icon: assets/ui/item_coffee.png
    category: consumable
    consumable: true
    value: 80
    use_effects:
      - kind: gain_resource
        target: energy
        value: 20
      - kind: log_event
        target: "喝了一杯外帶黑咖啡。"

  # 3) 小說 — 關鍵物品，給 Aoi 看會增加好感
  - id: novel_borrowed
    name: "Aoi 借給你的小說"
    description: "她說「先拿去看，不用還，我有另一本」。"
    icon: assets/ui/item_novel.png
    category: key
    locked: true
    matches_tags: ["有趣的論文", "安靜的角落"]
    tags: ["book"]
```

### 在遊戲裡發物品

在 `content/scenes/intro.yaml` 加一個場景，展示 `give_item`：

```yaml
  - id: mia_gift_tart
    title: "驚喜"
    location: cafe
    background: assets/backgrounds/cafe_counter.png
    lines:
      - speaker: "Mia"
        text: "「等一下，昨天多做了一個——給你。」"
        expression: smile
      - speaker: "玩家"
        text: "「草莓塔？謝謝。」"
      - text: |
          她把塔放進一個小紙袋遞過來，附帶一個笑容。
    on_end:
      - kind: give_item
        target: cafe_tart
        value: 1
      - kind: affection
        target: mia
        value: 5
      - kind: set_flag
        target: got_tart
        value: true
```

### 送禮給 NPC

若玩家點探索畫面的 NPC 卡片並選「送禮」，引擎會開啟 inventory picker，玩家選一個物品就觸發 `gift` effect。

你也可以在場景 effect 裡直接送：

```yaml
effects:
  - kind: gift
    target: aoi
    stat: novel_borrowed
```

好感變化規則：

1. `item.gift_modifier[npc.id]` 有定義 → 用它
2. `item.matches_tags ∩ npc.likes` 有交集 → +8
3. `item.matches_tags ∩ npc.dislikes` 有交集 → -5
4. 以上都沒有 → +2

`cafe_tart` 的 `matches_tags` 包含 `"新鮮甜點"`，而 Mia 的 `likes` 裡也有 `"新鮮甜點"`，所以送她草莓塔會有 +8 好感。

### 使用消耗品

在場景 effect 裡：

```yaml
effects:
  - kind: use_item
    target: takeaway_coffee
```

會消耗一個 `takeaway_coffee` 並把它的 `use_effects` 全部執行（+20 體力 + log event）。

玩家也可以在「物品 (I)」overlay 裡自己按「使用」按鈕——消耗品才會顯示這個按鈕。

### 怎麼跑

```bash
uv run python main.py --pack cafe_afternoon
```

或用 headless 給自己加一個物品確認：

```bash
uv run python main.py --pack cafe_afternoon --headless --inspect
```

輸出的 `inventory` 欄位應包含你在 starting_inventory 設定的物品（尚未設定時是空的）。

---

物品系統搭起來了，接下來加成就，讓玩家達成里程碑時有明顯的正向回饋。

---

## 10. 加成就

### 目標

寫 `achievements.yaml`，定義三個成就——「第一杯咖啡」、「與 Mia 成為朋友」、「隱藏：Aoi 的結局」——展示普通成就、門檻成就、隱藏成就。

### 檔案內容

建立 `../cafe_afternoon/content/achievements.yaml`：

```yaml
achievements:

  - id: ach_first_coffee
    title: "第一杯咖啡"
    description: "在咖啡館點了第一杯手沖，開始了這段午後的故事。"
    requires:
      - kind: flag
        target: first_order_done

  - id: ach_mia_friend
    title: "Mia 記得你了"
    description: "和 Mia 的感情到達了一個節點——她開始記得你點什麼。"
    requires:
      - kind: flag
        target: mia_remembers

  - id: ach_aoi_ending
    title: "窗邊的下午茶"
    description: "在最後的午後，Aoi 把椅子往你這邊移了一格。"
    hidden: true
    requires:
      - kind: flag
        target: aoi_ending_good
```

### 成就的觸發方式

引擎在每次套用任何 effect 之後，都會自動重新評估所有未解鎖成就的 `requires`。所以你不需要「手動觸發成就」——只要 flag / 好感 / 資源的條件成立，成就就會解鎖並彈出 toast。

### 查詢成就狀態作為條件

成就一旦解鎖，可以用 `{kind: achievement, target: ach_id}` 作為後續場景或選項的條件：

```yaml
choices:
  - id: special_talk
    text: "「你知道那件事之後，有什麼感受嗎？」"
    requires:
      - kind: achievement
        target: ach_mia_friend
    effects:
      - kind: affection
        target: mia
        value: 3
```

> **注意**：不建議用成就當核心劇情的條件鎖（例如「必須解成就才能推進主線」）。成就的本質是玩家行為的副產品，要鎖場景推薦用 flag。

### 怎麼跑

```bash
uv run python main.py --pack cafe_afternoon \
    --dev-flags '{"first_order_done": true}' \
    --dev-start explore
```

進入探索畫面後，成就 `ach_first_coffee` 應立刻解鎖（因為 `first_order_done` flag 已設好），並在右上角顯示 toast。

按 T 鍵或從選單打開成就 overlay，可以看到三個成就的狀態（第三個因為 `hidden: true` 且未解鎖，顯示為「？？？」）。

---

成就系統完成了。接下來加入金錢，讓遊戲有一個小商店機制。

---

## 11. 加金錢資源

### 目標

寫 `resources.yaml`，定義「咖啡基金」和「注意力」兩個資源。在場景裡展示 `gain_resource` 和 `spend_resource`，並讓 Mia 的行動 overlay 出現「看貨」按鈕，開啟一個可以買咖啡的小商店。

### 檔案內容

建立 `../cafe_afternoon/content/resources.yaml`：

```yaml
resources:

  - id: money
    name: "咖啡基金"
    symbol: "$"
    description: "帶來專門喝咖啡用的錢。"
    starting: 600
    min: 0

  - id: attention
    name: "注意力"
    description: "今天還剩多少心力可以和人說話。太低時就不太想搭話了。"
    starting: 100
    min: 0
    max: 100
```

### 在 meta.yaml 加入起始物品

更新 `content/meta.yaml`，讓玩家一開始就帶著一點東西：

```yaml
title: "咖啡館的午後"
subtitle: "在那裡，時間過得特別慢"

text_speed: 50

start_location: cafe
intro_scene: intro

player:
  name: "玩家"
  pronouns: "他"

starting_inventory:
  cafe_tart: 1
```

### 在場景裡消費資源

在 `content/scenes/intro.yaml` 加一個買飲料的場景：

```yaml
  - id: buy_coffee_scene
    title: "點單"
    location: cafe
    background: assets/backgrounds/cafe_counter.png
    lines:
      - speaker: "Mia"
        text: "「今天要喝什麼？手沖的話 $120，拿鐵 $130。」"
        expression: smile
      - speaker: "玩家"
        text: "（看一下今天的餘額…）"
    choices:
      - id: order_pourover
        text: "「手沖，謝謝。」($120)"
        requires:
          - kind: resource_gte
            target: money
            value: 120
        effects:
          - kind: spend_resource
            target: money
            value: 120
          - kind: affection
            target: mia
            value: 2
          - kind: gain_resource
            target: attention
            value: 10
        next_scene: coffee_delivered

      - id: order_latte
        text: "「拿鐵。」($130)"
        requires:
          - kind: resource_gte
            target: money
            value: 130
        effects:
          - kind: spend_resource
            target: money
            value: 130
          - kind: affection
            target: mia
            value: 2
          - kind: gain_resource
            target: attention
            value: 15
        next_scene: coffee_delivered

      - id: no_money
        text: "「…今天錢帶不夠，不好意思。」"
        hidden_if_locked: false
        effects:
          - kind: affection
            target: mia
            value: -1
        next_scene: embarrassed_leave

  - id: coffee_delivered
    title: "咖啡到了"
    location: cafe
    background: assets/backgrounds/cafe_counter.png
    lines:
      - speaker: "Mia"
        text: "「好了，慢慢喝。」"
        expression: smile
      - text: "咖啡的香氣先到，然後才是那個杯子。"

  - id: embarrassed_leave
    title: "有點尷尬"
    location: cafe
    background: assets/backgrounds/cafe_interior.png
    lines:
      - speaker: "Mia"
        text: "「沒關係，下次再說嘛！」"
        expression: smile
      - text: |
          你退到角落坐下。
          錢帶不夠這件事，今天應該會記很久。
```

### 開一個小商店

在 `characters.yaml` 裡，給 Mia 加上 `shop` block（在 Mia 的定義後面加）：

```yaml
  - id: mia
    name: "Mia"
    role: "咖啡館店員"
    age: 22
    is_heroine: true
    route_id: mia
    portrait: assets/characters/mia_normal.png
    portrait_set:
      smile:   assets/characters/mia_smile.png
      focused: assets/characters/mia_focused.png
      shy:     assets/characters/mia_shy.png
    description: |
      短馬尾、白色圍裙，動作很有效率但會在不經意間露出一個大的笑容。
      替你記得上次點了什麼、上次聊到哪裡。
    persona: |
      熱情但不讓人覺得壓迫；跟熟客說話的時候語速會放慢。
      最討厭浪費食物，會默默把快壞掉的糕點打折賣完。
    voice: "爽朗、句末常帶一個輕聲的「哦」或「喔」。"
    likes:    ["手沖咖啡", "新鮮甜點", "雨天的顧客很少"]
    dislikes: ["剩下很多的食物", "客人不說謝謝"]
    affiliated_location: cafe
    thresholds:
      - name: "記得你的臉"
        value: 20
        unlocks: ["mia_remembers"]
      - name: "把你當朋友"
        value: 50
        unlocks: ["mia_friend_mode"]
      - name: "不想讓你離開"
        value: 100
        unlocks: ["mia_ending_good"]
    shop:
      currency: money
      buy_back_ratio: 0.4
      greeting: "今天要點什麼？"
      listings:
        - {item: takeaway_coffee, price: 80,  stock: -1}
        - {item: cafe_tart,       price: 120, stock: 3}
        - {item: novel_borrowed,  price: 200, stock: 1, requires_flag: aoi_tolerates}
```

> **注意**：`novel_borrowed` 的 `requires_flag: aoi_tolerates` 代表好感達到「不介意你坐旁邊」門檻（解鎖 `aoi_tolerates` flag）後才顯示這筆商品。

### 怎麼跑

```bash
uv run python main.py --pack cafe_afternoon
```

進入探索畫面，在咖啡館地點點 Mia 的 NPC 卡片，行動 overlay 會多出「看貨」按鈕（因為設了 `shop`）。點「看貨」進入商店介面，左欄顯示可購買的商品，右欄顯示可出售的持有物品。

---

現在遊戲的所有系統都跑起來了。接下來寫 headless 測試，確保「主路線可以通關」這個基本保證在 CI 中能被守住。

---

## 12. Headless 測試

### 目標

寫一個 `scripts/test_route.json`，用 `--headless --script` 執行，自動跑完 Mia 路線的主要流程，並在最後 inspect 狀態確認旗標正確。

### headless 的意義

Headless 模式讓引擎在無視窗、無音效的環境下執行——CI 伺服器、終端機、沒有顯示器的機器都能跑。每次跑到場景的 `on_end`，effects 一樣會套用；成就一樣會觸發；存檔一樣寫進去。

所有 op 的行為跟玩家親自玩完全一樣，只是用 JSON 指令取代了滑鼠點擊。

### 完整腳本

建立 `../cafe_afternoon/scripts/test_mia_route.json`：

```json
{
  "commands": [
    {"op": "start_scene", "scene": "intro"},
    {"op": "next", "count": 5},

    {"op": "start_scene", "scene": "first_order"},
    {"op": "next", "count": 6},

    {"op": "start_scene", "scene": "waiting_for_coffee"},
    {"op": "next", "count": 3},
    {"op": "choose", "choice": "talk_to_mia"},
    {"op": "next", "count": 5},

    {"op": "adjust_affection", "npc": "mia", "delta": 20},

    {"op": "start_scene", "scene": "buy_coffee_scene"},
    {"op": "next", "count": 2},
    {"op": "choose", "choice": "order_pourover"},
    {"op": "next", "count": 3},

    {"op": "start_scene", "scene": "mia_gift_tart"},
    {"op": "next", "count": 4},

    {"op": "inspect"}
  ]
}
```

### 怎麼跑

```bash
uv run python main.py \
    --pack cafe_afternoon \
    --headless \
    --script ../cafe_afternoon/scripts/test_mia_route.json
```

### 預期輸出

最後 inspect 的 snapshot 應包含（節錄）：

```json
{
  "flags": {
    "intro_done": true,
    "first_order_done": true,
    "asked_mia_recipe": true,
    "got_tart": true
  },
  "all_characters": [
    {
      "id": "mia",
      "affection": 35
    }
  ],
  "achievements": {
    "unlocked": ["ach_first_coffee", "ach_mia_friend"],
    "total": 3
  },
  "inventory": {
    "cafe_tart": 1
  }
}
```

> **注意**：`adjust_affection` 是 headless 專用 op，只能在腳本裡用——它會直接設好感，不經過場景。適合測試「假設玩家已刷到特定好感，接下來的場景是否正確分支」。

### 讓測試失敗也有意義

可以在腳本的 `inspect` 後在 shell 用 `jq` 驗收：

```bash
uv run python main.py \
    --pack cafe_afternoon \
    --headless \
    --script ../cafe_afternoon/scripts/test_mia_route.json \
    | jq '.result.flags.first_order_done == true'
```

回傳 `true` 代表測試通過，`false` 或錯誤代表路線跑壞了。這個模式可以直接貼進 GitHub Actions 的 `run:` step。

---

主路線的自動測試寫好了。最後一步：把遊戲打包成可以分享給玩家的執行檔。

---

## 13. 打包成 exe

### 目標

用引擎內建的 `build.py` 把遊戲打包成單一 exe（Windows）或 app bundle（macOS），讓玩家不需要安裝 Python 也能執行。

### 快速打包

在引擎倉根目錄：

```bash
uv run python build.py --pack cafe_afternoon
```

打包過程大約 2–5 分鐘（PyInstaller 需要把 Python 執行環境和所有依賴一起捆進去）。完成後在 `dist/` 資料夾會出現：

- Windows：`cafe_afternoon.exe`（約 50–80 MB）
- macOS：`cafe_afternoon.app`（同等大小）

### 讓玩家看到正確的遊戲圖示

在 `assets/ui/` 放一個 `icon.ico`（Windows）或 `icon.icns`（macOS），build.py 會自動偵測並使用。

### 細節與進階選項

打包有更多選項（多平台、壓縮、簽章），以及「如何讓 pack 變成 pip-installable 的獨立套件」的說明，請見：

- [**distribution.md**](distribution.md)（如果這份文檔存在的話）
- 或 [**pack-format.md**](pack-format.md) 的「讓 pack 變成 pip-installable」一節

> **注意**：打包後的 exe 把 assets 打進去了。每次你改了 `content/` 或 `assets/` 裡的檔案，都需要重新打包才能讓玩家拿到更新。

---

## 14. 下一步

恭喜完成「咖啡館的午後」！你現在熟悉了：

- Pack 的目錄結構與 `meta.yaml`
- Scene 的對白、分支選項、條件鎖
- 角色定義、立繪表情切換
- 好感度 + 門檻解鎖 + toast 通知
- 地點地圖、時段推進、scene_hooks
- 物品（禮物 / 消耗品 / 關鍵物品）
- 成就的宣告式設計
- 資源（金錢 / 注意力）+ 小商店
- Headless 腳本測試

想把這個 demo 擴大，或者直接開始做更大的遊戲，建議接著讀：

### 深化現有功能

- [**effects-reference.md**](effects-reference.md) — 完整 effect kind 列表（包括 `move_to`、`unlock_location`、`end_scene`、`log_event`…）
- [**conditions-reference.md**](conditions-reference.md) — 完整 condition kind 列表（包括 `visited`、`scene_played`、`time_in`、`flag_eq`…）

### 常見設計模式

- [**cookbook.md**](cookbook.md) — 鎖路線、店家折扣、時段限定事件、隱藏結局、體力循環…

### 分發給玩家

- [**pack-format.md**](pack-format.md) — 把 pack 做成 pip-installable 套件，讓玩家用 `uv pip install` 就能玩

### 換掉 UI 外觀

- [**theme-and-locale.md**](theme-and-locale.md) — 換配色、字型、UI 文字語系

---

**問題或卡關**：先用 `--headless --inspect` 印出當前狀態看看 flags / inventory / affection 是否如預期；大多數問題在這一步就能找到原因。

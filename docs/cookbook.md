# Cookbook — 常見模式

從別人寫過的東西複製貼上是最快的學法。
這份是「我想做 X，怎麼寫？」的速查表。

---

## 「鎖某條女主角路線在達一定好感前不能跑」

```yaml
# 在女主角的 climax 場景的 requires:
- id: lover_event
  requires:
    - {kind: affection_gte, target: heroine_1, value: 80}
    - {kind: flag, target: met_heroine_1}
  ...
```

或更鬆一點：選項要鎖：

```yaml
choices:
  - id: confess
    text: "（向她告白）"
    requires:
      - {kind: affection_gte, target: heroine_1, value: 80}
    hidden_if_locked: true     # 不滿足就完全不出現（避免劇透）
```

---

## 「同一個場景因好感度不同播不同對白」

```yaml
lines:
  - text: "（她抬頭看了你一眼。）"

  # 低好感：冷淡
  - speaker: "林清雪"
    text: "「…嗯。」"
    requires:
      - {kind: affection_lt, target: heroine_1, value: 25}

  # 中好感：客氣
  - speaker: "林清雪"
    text: "「啊…你也來了。」"
    requires:
      - {kind: affection_gte, target: heroine_1, value: 25}
      - {kind: affection_lt,  target: heroine_1, value: 80}

  # 高好感：親密
  - speaker: "林清雪"
    text: "「你終於來了。」"
    requires:
      - {kind: affection_gte, target: heroine_1, value: 80}
```

不滿足 `requires` 的 line 整行被跳過。

---

## 「金錢消費 + 安全買賣」

```yaml
choices:
  - id: buy_tea
    text: "「我買這罐茉莉花茶。」($80)"
    requires:
      - {kind: resource_gte, target: money, value: 80}
    effects:
      - {kind: buy_item, target: jasmine_tea, stat: money, value: 80}
    next_scene: bought_tea_scene

  - id: cant_afford
    text: "「（沒錢，下次再來。）」"
    requires:
      - {kind: resource_lt, target: money, value: 80}
```

兩個選項互斥 — 有錢時看選項 1，沒錢時看選項 2。

---

## 「體力歸零強制睡覺」

```yaml
# scenes/forced_sleep.yaml
scenes:
  - id: forced_sleep
    title: "撐不住了"
    location: starting_room
    requires:
      - {kind: resource_lt, target: energy, value: 1}
    lines:
      - text: "你的雙腿不聽使喚地把你帶回房間。"
      - text: "倒在床上的瞬間就睡著了。"
    on_end:
      - {kind: advance_time, value: 4}             # 過了大半天
      - {kind: set_resource, target: energy, value: 100}
      - {kind: move_to, target: starting_room}
```

然後讓所有地點都有一個 auto-trigger 的 SceneHook 指向 `forced_sleep`：

```yaml
# locations.yaml — 每個地點加：
scene_hooks:
  - scene_id: forced_sleep
    trigger: auto
    requires_flags: [exhausted]   # 用一個 mirror flag，由 effects 觸發
    once: false
```

加 effect 讓 `gain_resource: energy = -X` 時順便設 flag：

```yaml
# 在會掉體力的場景的 effect 後面追加：
effects:
  - {kind: spend_resource, target: energy, value: 30}
  - {kind: spend_resource, target: energy, value: 0}    # 安全：用條件觸發 mirror flag
  - {kind: set_flag, target: exhausted}                  # 這個其實要先檢查 energy <= 0
```

—— 上面這條有點繞，因為目前沒有「effect 失敗時觸發某事」的 hook。
最乾淨的做法是寫個小場景把這邏輯 wrap 起來：

```yaml
- id: maybe_collapse
  lines: []
  requires:
    - {kind: resource_lt, target: energy, value: 1}
  on_end:
    - {kind: set_flag, target: exhausted}
    - {kind: play_scene, target: forced_sleep}
```

任何一個掉體力的 effect 後面接一個 `play_scene: maybe_collapse` 即可。

---

## 「時段 / 星期限定的活動」

```yaml
# locations.yaml
- id: night_market
  name: "夜市"
  scene_hooks:
    - scene_id: night_market_visit
      trigger: examine
      requires_time: [evening, night]
      forbids_flags: [night_market_done_today]

# scenes/night_market.yaml
- id: night_market_visit
  on_end:
    - {kind: set_flag, target: night_market_done_today}
    # 隔天早上由另一個場景清掉這個 flag。
```

---

## 「達成隱藏成就 = 達成壞結局」

```yaml
# scenes/bad_ending.yaml
- id: bad_ending_alone
  on_end:
    - {kind: set_flag, target: ending_alone}

# achievements.yaml
- id: ach_alone_ending
  title: "獨自一人"
  description: "走到了某個沒有女主角的結局。"
  hidden: true
  requires:
    - {kind: flag, target: ending_alone}
  forbids:
    - {kind: flag, target: ending_lover}
    - {kind: flag, target: ending_friend}
```

---

## 「LLM 自由對話影響劇情」(v2 deferred)

LLM 接入已 deferred 到 v2。v1 沒有「自由對話」這個介面 — 點 NPC card 只開
送禮 / 看貨 overlay。要做「玩家承諾某事 → 劇情記得」的耦合，目前唯一
管道是 **dialogue scene + choice**：

```yaml
choices:
  - id: promise_park
    text: "「下週末，要不要一起去湖畔公園？」"
    effects:
      - {kind: set_flag, target: promised_park_with_heroine_1}
      - {kind: affection, target: heroine_1, value: 3}
```

之後的場景可以用 `requires: [{kind: flag, target: promised_park_with_heroine_1}]`
鎖門檻、或在後續 line 引用該 flag。

---

## 「製作多結局」

```yaml
# scenes/ending_branch.yaml — 最後一場戲的選項
scenes:
  - id: final_choice
    lines:
      - speaker: "林清雪"
        text: "「你⋯要跟我一起走嗎？」"
    choices:
      - id: yes_together
        text: "「我陪妳走。」"
        requires:
          - {kind: affection_gte, target: heroine_1, value: 80}
        effects:
          - {kind: set_flag, target: ending_lover}
        next_scene: ending_lover

      - id: stay_friends
        text: "「我們還是朋友吧。」"
        requires:
          - {kind: affection_gte, target: heroine_1, value: 25}
        effects:
          - {kind: set_flag, target: ending_friend}
        next_scene: ending_friend

      - id: walk_away
        text: "「對不起⋯」"
        effects:
          - {kind: set_flag, target: ending_alone}
        next_scene: ending_alone
```

要避免「玩家撞死 NPC 後仍能拿 True Ending」這種荒謬，加 `forbids` 把
互斥 flag 列出來。

---

## 「給玩家一筆零用金的勞動場景」

```yaml
- id: part_time_job
  title: "雜貨鋪幫忙搬貨"
  location: shop_alley
  lines:
    - text: "你在雜貨鋪後場搬了三個小時的貨。"
  on_end:
    - {kind: gain_resource, target: money,  value: 200}
    - {kind: spend_resource, target: energy, value: 30}
    - {kind: advance_time, value: 3}
```

把它放在 shop_alley 的 `scene_hooks`，玩家有空就可以打工。

---

## 「對白語氣根據 LLM 介入動態 vs 靜態」(v2 deferred)

`llm_speaker` / `llm_directive` 欄位在 v1 仍可寫進 YAML（schema 接受），但
LLM 接入尚未啟用，所以這類 line 一律會顯示 `text:` 的 fallback：

```yaml
lines:
  # 靜態 — 寫死，可預測（v1 一律走這條路）
  - speaker: "林清雪"
    text: "「你來了。」"

  # 動態 — v2 才會真的問 LLM；v1 直接顯示底下的 text
  - speaker: "林清雪"
    llm_speaker: true
    llm_directive: |
      玩家上次跟你說好下次帶妳去湖畔公園。
      請以略帶期待但克制的口氣，問他這週末有沒有空。
    text: "「呃⋯這週末你有空嗎？」"
```

v1 的劇本撰寫請假設「文字一律寫死」即可。

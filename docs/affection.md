# Affection

每個 NPC 有一個或多個整數 stat — 預設叫 `affection`（好感度），
但你可以為一個角色同時追蹤 `affection` / `trust` / `fear` /
`reputation` 等任意 stat。

## Bump 好感度

在場景 effect 裡：

```yaml
effects:
  - {kind: affection, target: heroine_1, value: 5}              # 預設 stat
  - {kind: affection, target: heroine_1, value: 5, stat: trust} # 多軸
  - {kind: stat, target: heroine_1, value: 8, stat: fear}       # alias，跟上面一樣
```

## 條件

```yaml
requires:
  - {kind: affection_gte, target: heroine_1, value: 50}            # 預設 stat
  - {kind: affection_gte, target: heroine_1, value: 30, stat: trust}
  - {kind: affection_lt,  target: heroine_1, value: 80}
```

condition 預設用 `affection` stat — 寫 `stat: trust` 才會看 trust。

## 門檻 + 解鎖

在 `characters.yaml` 為 NPC 宣告門檻（這是 demo_pack 的 `heroine_1` 實際設定）：

```yaml
- id: heroine_1
  thresholds:
    - {name: "朋友", value: 25, unlocks: ["heroine_1_friend"]}
    - {name: "戀人", value: 80, unlocks: ["heroine_1_lover"]}
```

當好感度第一次達到 `value` 時，`unlocks` 的字串會被加進該角色的
`unlocked` set。場景條件可以查它（透過 flag 或 has_item 形式…
其實 unlocks 不是 flag、不能直接被 condition 查 — 推薦同時 set_flag）：

```yaml
- {kind: affection, target: heroine_1, value: 30}
- {kind: set_flag, target: heroine_1_friend}     # 顯式 flag 對齊
```

未來可能加 `unlock` condition kind；目前用 flag 是穩當的做法。

## 等級標籤

引擎內建一組好感度等級對應字串（陌生 → 認識 → 朋友 → 好友 → 心動 → 戀人）。
UI 會顯示。你可以在 `meta.yaml` 全部蓋過：

```yaml
locale:
  affection_levels:
    - {min: 0,   label: "Stranger"}
    - {min: 25,  label: "Friend"}
    - {min: 50,  label: "Close"}
    - {min: 100, label: "Lover"}
```

詳見 [theme-and-locale.md](theme-and-locale.md)。

## 自由對話如何影響好感度

`ChatScene` 每跑完一輪 LLM 對話：
- 自動 +1 好感度（鼓勵玩家多互動）
- 寫進 NPC 的 memory + 事件記錄

不喜歡這個自動 +1 的話，到 `world_gal_game/scenes/chat_scene.py` 的
`_send()` 把那行刪掉即可 — 行為很集中，未來會做成 config 開關。

## Headless 模式調整好感度

寫腳本測劇情時：

```json
{"op": "adjust_affection", "npc": "heroine_1", "delta": 55}
{"op": "adjust_affection", "npc": "heroine_1", "delta": 5, "stat": "trust"}
```

# Pack Format

> **學習路徑**：軌道 1（最後一站）  
> **前置條件**：讀完 [tutorial-build-a-game.md](tutorial-build-a-game.md)  
> **下一步**：軌道 2 — 各子系統指南（[scenes.md](scenes.md) / [characters.md](characters.md) / [locations.md](locations.md) / …）  
> **完整索引**：[docs/README.md](README.md)

---

一個 *pack* 就是一個目錄；放進引擎找得到的地方，
`world-gal-game --pack <name>` 就能玩。

## 完整目錄結構

```
my_game/
├── pyproject.toml          # 選填，把 pack 包成 pip-installable
├── README.md
├── main.py                 # 選填，當 standalone 跑時的入口
├── content/                # 必要 — 引擎只認這個資料夾
│   ├── meta.yaml           # 必要 — 沒有就不算 pack
│   ├── locations.yaml      # 選填
│   ├── characters.yaml     # 選填
│   ├── items.yaml          # 選填
│   ├── resources.yaml      # 選填（或寫在 meta.yaml）
│   ├── achievements.yaml   # 選填
│   ├── endings.yaml        # 選填 — 結局與完成度
│   └── scenes/             # 選填，多檔散裝
│       ├── 00_*.yaml
│       └── ...
└── assets/                 # 選填 — 不存在時引擎用 placeholder
    ├── backgrounds/
    ├── characters/
    ├── cgs/
    ├── ui/
    ├── fonts/
    └── bgm/
```

**所有 YAML 內的資產路徑寫 `assets/...`（pack-相對）**，例如
`assets/backgrounds/library.png`。引擎在載入時會自動把它對應到這個
pack 的根目錄。

## meta.yaml（必要）

最小可行：

```yaml
pack_format_version: "0.1"       # 引擎用來判斷 schema 版本（Phase 1 起建議）
title: "我的遊戲"
subtitle: "副標題"
start_location: starting_room    # 開新遊戲時把玩家放在這個地點
intro_scene: prologue            # 開新遊戲後第一個自動播的場景
```

### pack_format_version

從 Phase 1 開始引擎讀這個欄位，未來 schema 變動會在這裡 bump。Phase 1
只是「記錄」，不做 migration；缺欄位只會出 warning，不會拒絕載入。新建 pack
請寫 `"0.1"`。

完整可選欄位：

```yaml
title: "我的遊戲"
subtitle: "副標題"

# 文字顯示速度 (chars/sec)。0 = 瞬間。
text_speed: 60

# 內嵌字型；CJK 一律建議內嵌避免使用者系統沒有字型。
bundled_font: assets/fonts/MyFont.ttf

# 標題畫面背景圖。
title_bg: assets/backgrounds/title.png

# 起始地點 / 場景。
start_location: starting_room
intro_scene: prologue

# 玩家預設名字、第一人稱代詞（標題畫面可以改）。
player:
  name: "玩家"
  pronouns: "他"

# 起始持有物品（key = item id, value = count）。
starting_inventory:
  jasmine_tea: 1
  espresso: 1

# 起始資源值（key = resource id, value = amount）。
# 也可以直接在 resources 定義 starting；兩個都會被讀。
starting_resources:
  money: 500
  energy: 100

# Theme 覆寫（不寫的欄位用引擎預設）。
theme:
  accent:      [216, 80, 143]    # RGB or RGBA
  accent_alt:  [107, 107, 255]
  accent_warm: [240, 198, 116]
  bg_deep:     [13, 10, 20]
  pad_m: 14
  radius_m: 10

# 在地化覆寫 — 改 affection 等級、時段、UI 文字。
locale:
  affection_levels:
    - {min: 0,   label: "Stranger"}
    - {min: 25,  label: "Friend"}
    - {min: 100, label: "Lover"}
  time_of_day:
    morning: "Morning"
    midnight: "Witching Hour"
  ui:
    new_game: "New Game"
    map: "Map"

# 資源宣告 — 也可以寫在 content/resources.yaml。
resources:
  - id: money
    name: "錢包"
    symbol: "$"
    starting: 500
    min: 0
```

詳細欄位：
- [theme-and-locale.md](theme-and-locale.md) — theme / locale 區塊
- [resources.md](resources.md) — resources 區塊
- [locations.md](locations.md) — locations / regions / exits 完整 schema

## endings.yaml（選填）

宣告遊戲的結局；顯示在「結局與完成度」鑑賞 overlay 裡，依 `route_id` 分組。
每個結局靠 `requires` 條件解鎖 —— 慣例是綁一個在路線收尾場景 `on_end` set 的
`ending_*` flag。檔案結構與 `achievements.yaml` 對齊（`requires` / `forbids` 走標準
condition 載入器，任何 condition kind 都能用）。

```yaml
endings:
  - id: ending_lover
    title: "結局 · 戀人"
    description: "與林清雪的故事，走到了戀人結局。"
    route_id: heroine_1          # 用 heroine 的 route_id 分組（可選）
    requires:
      - {kind: flag, target: ending_lover}

  - id: ending_secret
    title: "隱藏結局"
    hidden: true                 # 未解鎖時不出現在清單（解鎖後才顯示）
    requires:
      - {kind: flag, target: ending_secret}

  - id: ending_alone
    title: "結局 · 一個人"
    description: "離開了廣場，沒有再回頭。"
    # 沒有 route_id → 落在「其他」分組
    requires:
      - {kind: flag, target: ending_alone}
```

每個 ending 的欄位：

| 欄位 | 型別 | 說明 |
|---|---|---|
| `id` | str（必要） | 唯一識別 |
| `title` | str | 顯示名稱 |
| `description` | str | 解鎖後顯示的描述 |
| `icon` | str | 選填圖示路徑（`assets/...`） |
| `route_id` | str | 分組用；對應標了 `is_heroine` + `route_id` 的 NPC 名字 |
| `hidden` | bool | `true` = 未解鎖時不進清單 |
| `requires` | condition list | 全部成立才解鎖（典型是 `ending_*` flag） |
| `forbids` | condition list | 任何一個成立就不解鎖 |

路線收尾場景照常 set flag：

```yaml
on_end:
  - kind: set_flag
    target: ending_lover
    value: true
  - kind: end_scene
```

完成度（劇情已讀 / 結局 / CG）由引擎自動計算，作者不用做任何事。玩家視角、分組與
完成度的細節見 [presentation-and-extras.md](presentation-and-extras.md)。

## Line 演出 effect（鏡頭 / 畫面特效）

除了一般 effect，引擎內建五個**演出 effect**，寫在某一行 line 的 `effects:` 裡，
line 演到時觸發。它們不碰存檔、不碰 pygame，只把指令排進場景的視覺佇列：

| kind | `value`（dict） |
|---|---|
| `camera_pan` | `{x, y, duration?, easing?}` |
| `camera_zoom` | `{scale, duration?, easing?}` |
| `screen_shake` | `{intensity?, duration?, easing?}` |
| `screen_flash` | `{color:[r,g,b]?, duration?, max_alpha?, easing?}` |
| `screen_tint` | `{color:[r,g,b]?, duration?, max_alpha?, persist?, clear?, easing?}` |

```yaml
- speaker: "林清雪"
  text: "「下次。」"
  effects:
    - kind: camera_zoom
      value: {scale: 1.12, duration: 0.9}
```

完整簽章、預設值、`screen_tint` 的 clear 用法見
[presentation-and-extras.md](presentation-and-extras.md)。權威清單（含外掛新增的）跑
`wgg capabilities --pack <pack>`。

## 引擎在哪裡找 pack？

```
1. <engine-repo>/games/<name>/           — 隨引擎散布的內建 pack
2. <engine-repo>/../<name>/              — 兄弟資料夾（建議）
3. ~/.world-gal-game/packs/<name>/        — 使用者本機 pack 緩存
4. --pack <abs/rel-path>                 — 直接指定
```

`<name>` 會自動嘗試 snake_case、kebab-case、Title-Case 變體。
所以 `--pack my_game_pack` 也會找到 `My-Game-Pack/`。

## 讓 pack 變成 pip-installable

如果你想把 pack 也包成可 `uv add` 的東西（給玩家或 CI 用）：

`pyproject.toml`：

```toml
[project]
name = "my-game"
version = "0.1.0"
requires-python = ">=3.10"
dependencies = ["world-gal-game>=0.1.0"]

[project.scripts]
my-game = "my_game.entry:main"

[tool.uv.sources]
# 本地開發指向 sibling 引擎源碼；未來上 PyPI 後可拿掉這段。
world-gal-game = { path = "../World-Gal-Game", editable = true }

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["my_game"]
include = ["content/**/*", "assets/**/*"]

[tool.hatch.build.targets.wheel.force-include]
"content" = "my_game/content"
"assets" = "my_game/assets"
```

`my_game/__init__.py` + `my_game/entry.py`（薄殼）：

```python
# my_game/entry.py
from pathlib import Path
import sys


def _pack_root() -> Path:
    here = Path(__file__).resolve().parent
    if (here / "content" / "meta.yaml").exists():
        return here          # wheel install
    return here.parent       # source checkout


def main(argv: list[str] | None = None) -> int:
    pack_root = _pack_root()
    forwarded = list(argv) if argv is not None else sys.argv[1:]
    if "--pack" not in forwarded:
        forwarded = ["--pack", str(pack_root)] + forwarded
    from world_gal_game.cli import main as engine_main
    return engine_main(forwarded)


if __name__ == "__main__":
    sys.exit(main())
```

之後使用者只要：

```bash
uv pip install my-game
my-game             # console script 直接啟動，引擎自動載這個 pack
```

範例見 repo 內建的 `games/demo_pack/`，或任何放在 sibling 目錄的外部 pack。

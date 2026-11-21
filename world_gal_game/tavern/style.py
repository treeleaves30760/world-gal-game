"""Style guides: what "sounds written by a model" means, per language.

Two things are defined here and used by two different parts of the system:

- **Craft rules** (``dialogue_rules`` / ``narration_rules``) are short,
  positive directions rendered into the acting prompt — the equivalent of a
  tavern preset's writing-style block.
- **Slop patterns** are the tells themselves (stock phrases, rhetorical
  templates, therapy-speak, summary closers, formatting leaks). They are
  *detected after the fact* by ``tavern.slop`` and fed back to the agent that
  wrote the line as a retake note.

The split is deliberate. Printing a long banned-word list into a prompt primes
the very words it bans; catching a phrase after it was written and sending the
line back works, and stays measurable. So only a handful of the most common
offenders are named in the prompt (``prompt_avoid``) and the full pattern list
does its work in the linter.

A pack tunes all of this in ``content/tavern/style.yaml`` — add its own banned
phrases, switch a pattern off, change the line length, or set ``region`` so
the regional-vocabulary check matches where the story is set.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator

Scope = Literal["any", "dialogue", "narration"]


class SlopPattern(BaseModel):
    """One detectable tell."""

    model_config = ConfigDict(extra="forbid")

    id: str
    pattern: str                       # regular expression
    label: str = ""                    # what it is, for the report
    hint: str = ""                     # what to do instead
    weight: float = 1.0
    scope: Scope = "any"
    hard: bool = False                 # a hard hit sends the line back
    #: Density rule: only report when hits per 100 lines exceed this.
    max_per_100: float | None = None

    @field_validator("pattern")
    @classmethod
    def _compiles(cls, value: str) -> str:
        re.compile(value)
        return value


class StyleGuide(BaseModel):
    """Per-language writing conventions plus the tells to catch."""

    model_config = ConfigDict(extra="forbid")

    locale: str = "zh-Hant"
    region: str = ""                   # e.g. "tw": enables regional_terms
    quote_open: str = "「"
    quote_close: str = "」"
    pov: Literal["first", "second", "third"] = "second"
    line_max_chars: int = Field(default=40, ge=8)        # one box of speech
    narration_max_chars: int = Field(default=70, ge=10)
    dialogue_rules: list[str] = Field(default_factory=list)
    narration_rules: list[str] = Field(default_factory=list)
    extra_rules: list[str] = Field(default_factory=list)  # pack additions
    #: Named in the prompt. Keep short — see module docstring.
    prompt_avoid: list[str] = Field(default_factory=list)
    #: Plain substrings that always count as a hard hit.
    banned_phrases: list[str] = Field(default_factory=list)
    patterns: list[SlopPattern] = Field(default_factory=list)
    #: ``{term to avoid: preferred term}`` for the pack's region.
    regional_terms: dict[str, str] = Field(default_factory=dict)
    #: Sentence-final particles counted in a speaker's voice fingerprint.
    particles: str = ""
    #: Pattern ids switched off for this pack.
    disable: list[str] = Field(default_factory=list)

    # ------------------------------------------------------------------

    @classmethod
    def builtin(cls, locale: str = "zh-Hant") -> "StyleGuide":
        """The engine's default guide for ``locale`` (falls back sensibly)."""
        key = normalize_locale(locale)
        data = _BUILTIN.get(key) or _BUILTIN["en"]
        return cls.model_validate({**data, "locale": key})

    @classmethod
    def load(cls, path: Path | None, *, locale: str | None = None) -> "StyleGuide":
        """Layer a pack's ``style.yaml`` over the built-in guide.

        Scalars replace. ``dialogue_rules`` / ``narration_rules`` replace when
        given (use ``extra_rules`` to append instead). ``banned_phrases``,
        ``patterns`` and ``prompt_avoid`` are appended to the built-ins,
        ``regional_terms`` is merged, and ``disable`` removes patterns by id.
        """
        raw: dict[str, Any] = {}
        if path is not None and Path(path).exists():
            loaded = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
            if loaded is not None and not isinstance(loaded, dict):
                raise ValueError(f"{Path(path).name}: style guide must be a mapping")
            raw = dict(loaded or {})
        base = cls.builtin(str(raw.get("locale") or locale or "zh-Hant"))
        data = base.model_dump()
        for key, value in raw.items():
            if key in ("banned_phrases", "prompt_avoid"):
                data[key] = list(dict.fromkeys([*data[key], *(value or [])]))
            elif key == "patterns":
                data[key] = [*data[key], *(value or [])]
            elif key == "regional_terms":
                data[key] = {**data[key], **(value or {})}
            else:
                data[key] = value
        return cls.model_validate(data)

    def active_patterns(self) -> list[SlopPattern]:
        off = set(self.disable)
        return [p for p in self.patterns if p.id not in off]

    def render_dialogue_rules(self) -> str:
        """The craft block for an acting prompt."""
        rules = [*self.dialogue_rules, *self.extra_rules]
        lines = [f"- {rule}" for rule in rules]
        if self.prompt_avoid:
            label = _AVOID_LABEL.get(self.locale, _AVOID_LABEL["en"])
            sep = "、" if self.locale.startswith("zh") or self.locale == "ja" else ", "
            lines.append(f"- {label}{sep.join(self.prompt_avoid)}")
        return "\n".join(lines)

    def render_narration_rules(self) -> str:
        return "\n".join(f"- {rule}" for rule in self.narration_rules)


_AVOID_LABEL = {
    "zh-Hant": "這些是寫爛了的句子，用了會被退回：",
    "zh-Hans": "这些是写烂了的句子，用了会被退回：",
    "ja": "使い古された言い回しは差し戻します：",
    "en": "These are worn out and will be sent back: ",
}

_HANS_MARKERS = "们这说为个来时么对还没发现过样国学会话让经问觉"
_HANT_MARKERS = "們這說為個來時麼對還沒發現過樣國學會話讓經問覺"
_KANA_RE = re.compile("[\u3040-\u30ff]")
_CJK_RE = re.compile("[\u4e00-\u9fff]")


def normalize_locale(locale: str | None) -> str:
    """Map a locale tag onto one of the built-in guides."""
    tag = (locale or "").replace("_", "-").strip()
    low = tag.casefold()
    if low.startswith("zh"):
        if any(part in low for part in ("hans", "-cn", "-sg", "-my")):
            return "zh-Hans"
        return "zh-Hant"
    if low.startswith("ja"):
        return "ja"
    if low.startswith("en") or not low:
        return "en" if low else "zh-Hant"
    return "en"


def detect_locale(text: str) -> str:
    """Guess a pack's writing language from a sample of its text."""
    if not text:
        return "zh-Hant"
    cjk = len(_CJK_RE.findall(text))
    kana = len(_KANA_RE.findall(text))
    if kana >= max(2, cjk * 0.1):
        return "ja"
    if cjk == 0 or cjk < len(text) * 0.05:
        return "en"
    hans = sum(text.count(ch) for ch in _HANS_MARKERS)
    hant = sum(text.count(ch) for ch in _HANT_MARKERS)
    return "zh-Hans" if hans > hant else "zh-Hant"


# ----------------------------------------------------------------------
# Built-in data
#
# Chinese patterns are written once with character classes covering both
# scripts, so Traditional and Simplified packs share them.

_ZH_PATTERNS: list[dict[str, Any]] = [
    # --- stock physical beats ------------------------------------------
    {"id": "stock.imperceptible", "weight": 3,
     "pattern": r"不易察[覺觉]|[難难]以察[覺觉]|[幾几]不可[聞闻見见察]|微不可[察聞闻見见]|若有若[無无]",
     "label": "「不易察覺」類的形容",
     "hint": "看不出來就別寫；看得出來就寫看到什麼。"},
    {"id": "stock.trace", "weight": 1.5,
     "pattern": r"(?:一[絲丝]|一抹|[幾几]分)[^，。！？\n]{0,6}(?:的|地)",
     "label": "「一絲／一抹……的」",
     "hint": "換成一個看得到的具體動作或物件。"},
    {"id": "stock.mouth", "weight": 3,
     "pattern": r"嘴角[^，。！？\n]{0,8}(?:上[揚扬]|勾起|弧度|笑意)|唇角[^，。！？\n]{0,6}(?:上[揚扬]|勾)",
     "label": "嘴角上揚／勾起弧度",
     "hint": "她笑了就寫她笑了，或寫她做了什麼。"},
    {"id": "stock.eyes", "weight": 3,
     "pattern": r"眼[中底裡里神眸][^，。！？\n]{0,6}[閃闪][過过爍烁]|眸[中子光底][^，。！？\n]{0,8}[閃闪]|眼神[^，。！？\n]{0,4}(?:暗了|[複复][雜杂]|柔和)",
     "label": "眼中閃過……",
     "hint": "寫她看哪裡、看多久、有沒有移開。"},
    {"id": "stock.breath", "weight": 2,
     "pattern": r"深吸(?:了)?一口[氣气]|(?:倒|猛)[抽吸]一口[涼凉冷]?[氣气]|[輕轻][輕轻][嘆叹]了?(?:一)?口[氣气]",
     "label": "深吸一口氣／輕嘆一口氣",
     "hint": "停頓可以用斷句和換行表現。"},
    {"id": "stock.heartbeat", "weight": 3,
     "pattern": r"心[跳臟脏][^，。！？\n]{0,6}(?:漏了?一拍|漏跳|停了?半拍|慢了半拍)|心[頭头]一[緊紧顫颤暖軟软]",
     "label": "心跳漏了一拍／心頭一緊",
     "hint": "寫當下做錯或說錯的那件小事。"},
    {"id": "stock.air", "weight": 3,
     "pattern": r"空[氣气](?:彷[彿佛]|仿佛|似乎|好像)?[^，。！？\n]{0,4}(?:凝固|凝[滯滞]|安[靜静]了下[來来]|[變变]得微妙)|[氣气]氛[^，。！？\n]{0,6}(?:微妙|凝重|[尷尴]尬)起[來来]",
     "label": "空氣凝固／氣氛變得微妙",
     "hint": "寫安靜時聽得到的聲音。"},
    {"id": "stock.voice_like", "weight": 2.5,
     "pattern": r"[聲声]音[^，。！？\n]{0,6}(?:[輕轻]|低|小)得(?:像|彷[彿佛]|仿佛|[幾几]乎)|[聲声]音(?:有些|有[點点]|微微)?(?:沙[啞哑]|[發发][顫颤]|[顫颤]抖)",
     "label": "聲音輕得像……／聲音沙啞",
     "hint": "直接讓那句台詞變短或斷掉。"},
    {"id": "stock.knuckles", "weight": 2.5,
     "pattern": r"指[節节尖][^，。！？\n]{0,6}(?:泛白|[發发]白|微微[顫颤]抖|收[緊紧])|[攥握][緊紧]了?(?:拳[頭头]|衣角|裙[擺摆角])",
     "label": "指節泛白／攥緊衣角",
     "hint": "換一個這個角色才會有的小動作。"},
    {"id": "stock.ripple", "weight": 3,
     "pattern": r"(?:石子|石[頭头])[^。！？\n]{0,10}(?:湖面|水面|心湖)|[漣涟]漪|心湖",
     "label": "石子投入湖面／漣漪",
     "hint": "刪掉比喻，留下發生的事。"},
    {"id": "stock.time_stops", "weight": 3,
     "pattern": r"(?:[時时][間间]|世界)(?:彷[彿佛]|仿佛|好像|似乎)?[^，。！？\n]{0,4}(?:[靜静]止|停止|凝固|慢了下[來来])",
     "label": "時間彷彿靜止",
     "hint": "刪掉。"},
    {"id": "stock.warm_wave", "weight": 2.5,
     "pattern": r"(?:一股|一[陣阵])(?:暖流|暖意|酸[楚澀涩]|莫名的|[說说]不[清出]的)",
     "label": "一股暖流／一陣酸楚",
     "hint": "情緒不要命名，寫它讓她做了什麼。"},
    {"id": "stock.unnameable", "weight": 2,
     "pattern": r"莫名(?:的|地)|[說说]不清(?:道不明)?(?:的|地)|[難难]以言[喻說说](?:的|地)?|[無无]法言[說说喻]|不知[為为]何[，,]|不知怎[麼么的地]+[，,]",
     "label": "莫名的／說不清的／不知為何",
     "hint": "說不清就先不要寫，或寫清楚。"},
    {"id": "stock.moment", "weight": 2,
     "pattern": r"(?:那一刻|[這这]一刻|在那一瞬[間间]?|那一瞬[間间])[，,]",
     "label": "那一刻，……",
     "hint": "直接寫發生什麼。"},
    {"id": "stock.gentle_adverbs", "weight": 4, "max_per_100": 18,
     "pattern": r"(?:[輕轻][輕轻]|[緩缓][緩缓]|微微|淡淡|默默|[靜静][靜静]|悄悄)(?:地|的)?",
     "label": "輕輕／緩緩／微微／淡淡 用太多",
     "hint": "大部分可以直接刪掉。"},
    # --- rhetorical templates ------------------------------------------
    {"id": "struct.not_but", "weight": 2.5,
     "pattern": r"不是[^，。！？\n「」]{1,18}[，,]?\s*而是|不是[^，。！？\n「」]{1,14}[，,]\s*是[^，。！？\n]{1,14}[。！]",
     "label": "「不是……而是……」",
     "hint": "直接說後半句。"},
    {"id": "struct.simile", "weight": 4, "max_per_100": 6,
     "pattern": r"(?:像|彷[彿佛]|仿佛|宛如|如同|好似|[猶犹]如)[^，。！？\n]{1,16}(?:一[樣样]|一般|似的)|(?:彷[彿佛]|仿佛|宛如|[猶犹]如)",
     "label": "比喻太密",
     "hint": "一場戲最多留一個真的需要的比喻。"},
    # --- telling the emotion -------------------------------------------
    {"id": "tell.emotion_named", "weight": 2.5, "scope": "dialogue",
     "pattern": r"我(?:真的|其實|其实)?(?:感到|感[覺觉]到?|[覺觉]得)(?:自己)?(?:一[陣阵]|有些|有[點点]|很|好|非常|[無无]比)?(?:[開开]心|高[興兴]|[難难][過过]|悲[傷伤]|[緊紧][張张]|不安|害怕|[溫温]暖|幸福|孤[單单]|寂寞|安心|失落|心疼|感[動动]|委屈)",
     "label": "把情緒直接說出口",
     "hint": "讓情緒從她挑的字、岔開的話題露出來。"},
    {"id": "tell.emotion_narrated", "weight": 2, "scope": "narration",
     "pattern": r"(?:她|他)(?:忽然|突然|不禁|不由得)?(?:感到|感[覺觉]到|[覺觉]得)(?:一[陣阵]|有些|有[點点]|[無无]比)?(?:[開开]心|[難难][過过]|悲[傷伤]|[緊紧][張张]|不安|害怕|[溫温]暖|幸福|孤[單单]|寂寞|安心|失落|心疼|感[動动]|委屈)",
     "label": "旁白替角色說出情緒",
     "hint": "旁白只寫看得到、聽得到的東西。"},
    # --- assistant voice -----------------------------------------------
    {"id": "voice.therapy", "weight": 4, "scope": "dialogue",
     "pattern": r"[謝谢][謝谢]你(?:[願愿]意|告[訴诉]我|的坦[誠诚])|你的感受|我(?:能|可以)?理解你的|我[會会]一直(?:在|陪)|你[並并]不(?:孤[單单]|是一[個个]人)|[這这](?:很|是)正常的|不用勉[強强]自己|慢慢[來来][，,]?[沒没][關关][係系]|[無无][論论]如何[，,]我都|我一直都在|你已[經经]做得很好",
     "label": "諮商師口吻",
     "hint": "除非這個角色本來就這樣講話，換成她自己會有的反應（包括不安慰）。"},
    {"id": "voice.balanced", "weight": 2, "scope": "dialogue",
     "pattern": r"某[種种]程度上|[從从]某[個个]角度[來来][說说看]|一方面[^。！？\n]{2,30}另一方面|[換换]句[話话][說说]|[總总]而言之|[總总]的[來来][說说]|值得一提的是|不可否[認认]",
     "label": "報告式／面面俱到的說法",
     "hint": "人講話會選邊，不會做總結。"},
    {"id": "voice.ai_meta", "weight": 10, "hard": True,
     "pattern": r"作[為为](?:一[個个])?(?:AI|人工智[慧能]|[語语]言模型)|我[無无]法(?:提供|[協协]助|[繼继][續续])|(?i:as an ai\b)|(?i:i (?:can(?:no|')t|am unable to) (?:assist|continue|help))",
     "label": "模型自述",
     "hint": "重寫這一句。"},
    # --- closers --------------------------------------------------------
    {"id": "close.aphorism", "weight": 3,
     "pattern": r"(?:也[許许]|或[許许])[，,]?[^。！？\n]{2,24}(?:就是|才是|便是)|[這这](?:大概|也[許许]|或[許许])?就是[^。！？\n]{1,14}(?:吧|的意[義义]|的答案)[。！]?$|我(?:[終终]於|[終终]于|忽然|突然)(?:明白|懂了|意[識识]到)",
     "label": "格言式收尾",
     "hint": "停在一個動作或一件具體的東西上。"},
    # --- formatting leaks ----------------------------------------------
    {"id": "fmt.markdown", "weight": 6, "hard": True,
     "pattern": r"\*\*[^*\n]+\*\*|(?m:^\s{0,3}#{1,6}\s)|(?m:^\s*[-*]\s+\S)|\*[^*\n]{1,40}\*",
     "label": "Markdown／星號動作",
     "hint": "台詞裡不要有格式符號。"},
    {"id": "fmt.ooc", "weight": 6, "hard": True,
     "pattern": r"[（(\[]\s*OOC|(?i:\bOOC\s*[:：])",
     "label": "出戲說明",
     "hint": "只輸出角色的話。"},
    {"id": "fmt.emoji", "weight": 6, "hard": True,
     "pattern": "[\U0001F300-\U0001FAFF\u2728\u2764\u2705\u274C]",
     "label": "表情符號",
     "hint": "刪掉。"},
]

_ZH_PARTICLES = "啊呀啦喔哦嘛呢吧耶欸誒诶嗎吗噢哇唷囉啰咧齁捏喲哟嘍喽啦咯哈"

# Mainland vocabulary that reads wrong in a Taiwan-set story -> local word.
_TW_TERMS: dict[str, str] = {
    "視頻": "影片", "高質量": "高品質", "質量好": "品質好", "質量差": "品質差",
    "信息": "訊息", "軟件": "軟體", "硬件": "硬體", "網絡": "網路",
    "互聯網": "網路", "屏幕": "螢幕", "打印": "列印", "鼠標": "滑鼠",
    "默認": "預設", "激活": "啟用", "短信": "簡訊", "博客": "部落格",
    "文件夾": "資料夾", "程序員": "工程師", "內存": "記憶體", "硬盤": "硬碟",
    "U盤": "隨身碟", "優盤": "隨身碟", "充電寶": "行動電源", "出租車": "計程車",
    "公交車": "公車", "打車": "叫車", "西紅柿": "番茄", "酸奶": "優格",
    "方便麵": "泡麵", "盒飯": "便當", "外賣": "外送", "班主任": "導師",
    "高考": "學測", "本科": "大學部", "靠譜": "可靠", "給力": "很棒",
    "牛逼": "很厲害", "晚上好": "晚安", "早上好": "早安", "哪兒": "哪裡",
    "這兒": "這裡", "那兒": "那裡", "沒事兒": "沒事", "視頻通話": "視訊",
    "點贊": "按讚", "點讚": "按讚", "空調": "冷氣", "塑料": "塑膠",
    "圓珠筆": "原子筆", "創可貼": "OK繃", "衛生間": "廁所", "小伙伴": "朋友",
    "小夥伴": "朋友", "反饋": "回饋", "人工智能": "人工智慧",
    "智能手機": "智慧型手機", "激光": "雷射", "概率": "機率", "攝像頭": "鏡頭",
    "充值": "儲值", "賬號": "帳號", "登錄": "登入", "鏈接": "連結",
    "文檔": "文件", "音頻": "音訊", "代碼": "程式碼", "服務器": "伺服器",
    "筆記本電腦": "筆電", "台式機": "桌機", "大巴": "客運", "宿舍樓": "宿舍",
    "教學樓": "教學大樓", "微信": "LINE", "朋友圈": "限時動態", "地鐵": "捷運",
    "聯繫方式": "聯絡方式",
}

_ZH_HANT: dict[str, Any] = {
    "region": "tw",
    "pov": "second",
    "line_max_chars": 40,
    "narration_max_chars": 70,
    "particles": _ZH_PARTICLES,
    "dialogue_rules": [
        "一次只說一兩句，像真人開口：短、口語，可以不完整、可以答非所問、可以只回一個字。",
        "情緒不要說破。不寫「我很難過」「我有點緊張」，讓它從挑的字、岔開的話題、說到一半的句子露出來。",
        "想要的東西和嘴上說的可以不一樣；被問到不想答的事，可以反問、裝沒聽到、換話題。",
        "不總結、不講道理、不替這場戲收尾。",
        "沒有必要就不道謝、不道歉、不安慰，也不要句句叫對方的名字。",
        "不複述對方剛說的話再回答。",
        "用字照台灣人平常講話的習慣。",
    ],
    "narration_rules": [
        "只寫主角看得到、聽得到、摸得到的東西；不寫別人心裡在想什麼。",
        "一行一個畫面，寫具體的東西（紙、杯子、鞋帶），不寫氣氛。",
        "不抒情、不比喻、不預告接下來會怎樣。",
    ],
    "prompt_avoid": [
        "嘴角微微上揚", "眼中閃過一絲", "不易察覺", "深吸一口氣",
        "心跳漏了一拍", "空氣彷彿凝固", "不是……而是……",
    ],
    "patterns": _ZH_PATTERNS,
    "regional_terms": _TW_TERMS,
}

_ZH_HANS: dict[str, Any] = {
    "region": "",
    "pov": "second",
    "line_max_chars": 40,
    "narration_max_chars": 70,
    "particles": _ZH_PARTICLES,
    "dialogue_rules": [
        "一次只说一两句，像真人开口：短、口语，可以不完整、可以答非所问、可以只回一个字。",
        "情绪不要说破。不写「我很难过」「我有点紧张」，让它从挑的字、岔开的话题、说到一半的句子露出来。",
        "想要的东西和嘴上说的可以不一样；被问到不想答的事，可以反问、装没听到、换话题。",
        "不总结、不讲道理、不替这场戏收尾。",
        "没有必要就不道谢、不道歉、不安慰，也不要句句叫对方的名字。",
        "不复述对方刚说的话再回答。",
    ],
    "narration_rules": [
        "只写主角看得到、听得到、摸得到的东西；不写别人心里在想什么。",
        "一行一个画面，写具体的东西（纸、杯子、鞋带），不写气氛。",
        "不抒情、不比喻、不预告接下来会怎样。",
    ],
    "prompt_avoid": [
        "嘴角微微上扬", "眼中闪过一丝", "不易察觉", "深吸一口气",
        "心跳漏了一拍", "空气仿佛凝固", "不是……而是……",
    ],
    "patterns": _ZH_PATTERNS,
    "regional_terms": {},
}

_EN: dict[str, Any] = {
    "region": "",
    "quote_open": "\u201c",
    "quote_close": "\u201d",
    "pov": "second",
    "line_max_chars": 110,
    "narration_max_chars": 180,
    "particles": "",
    "dialogue_rules": [
        "One or two sentences a turn, the way people talk: short, loose, allowed to "
        "trail off, dodge the question, or be a single word.",
        "Never name the feeling. No \"I'm nervous\" or \"I feel sad\" — let it show in "
        "word choice, a changed subject, a sentence left unfinished.",
        "What the character wants and what they say can differ; a question they don't "
        "want to answer can be met with another question or ignored.",
        "No summing up, no lessons, no wrapping the scene up.",
        "No thanks, apologies or reassurance unless this character would give them, "
        "and don't say the other person's name in every line.",
        "Don't repeat back what was just said before answering.",
    ],
    "narration_rules": [
        "Only what the viewpoint character can see, hear or touch; never another "
        "character's thoughts.",
        "One image per line, made of concrete things rather than mood.",
        "No lyricism, no similes, no foreshadowing.",
    ],
    "prompt_avoid": [
        "barely above a whisper", "a breath she didn't know she was holding",
        "eyes sparkling", "couldn't help but", "sent shivers down",
        "it's not X, it's Y",
    ],
    "patterns": [
        {"id": "stock.whisper", "weight": 3,
         "pattern": r"(?i)barely (?:above|more than) a whisper|voice (?:was )?(?:husky|thick with)",
         "label": "barely above a whisper", "hint": "Just make the line shorter."},
        {"id": "stock.breath", "weight": 3,
         "pattern": r"(?i)(?:let out|released) a breath (?:s?he|they|i) (?:didn'?t|hadn'?t) (?:know|realized?)|took a deep breath",
         "label": "the held breath", "hint": "Cut it."},
        {"id": "stock.shivers", "weight": 3,
         "pattern": r"(?i)shivers? (?:down|up|ran)|heart skipped a beat|heart (?:pounded|hammered) in (?:her|his|their|my) chest",
         "label": "shivers / skipped heartbeat", "hint": "Write the mistake she makes instead."},
        {"id": "stock.eyes", "weight": 3,
         "pattern": r"(?i)eyes? (?:sparkl|glint|gleam|twinkl|soften)\w*|a (?:flicker|flash|hint|glimmer) of \w+ (?:crossed|in) (?:her|his|their)",
         "label": "sparkling eyes / a flicker of", "hint": "Say where she looks and for how long."},
        {"id": "stock.couldnt_help", "weight": 2,
         "pattern": r"(?i)couldn'?t help but|found (?:her|him|them|my)self \w+ing",
         "label": "couldn't help but", "hint": "She just does it."},
        {"id": "stock.air", "weight": 3,
         "pattern": r"(?i)the air (?:was|grew|felt) (?:thick|heavy|charged)|time (?:seemed to )?(?:stop|slow|stand still)|silence (?:stretched|hung)",
         "label": "thick air / time stood still", "hint": "Write what can be heard."},
        {"id": "stock.words", "weight": 2,
         "pattern": r"(?i)\b(?:tapestry|testament to|symphony of|a dance of|ministrations|orbs)\b",
         "label": "tapestry / testament / orbs", "hint": "Use the plain word."},
        {"id": "struct.not_but", "weight": 2.5,
         "pattern": r"(?i)\b(?:it|this|that)(?:'s| is| was)n'?t (?:just |about |only )?[^.,;!?\n]{1,40}[,;—-]+\s*(?:it|this|that)(?:'s| is| was)\b|\bnot just [^.,;!?\n]{1,30}, but\b",
         "label": "it's not X, it's Y", "hint": "Say the second half."},
        {"id": "struct.simile", "weight": 4, "max_per_100": 8,
         "pattern": r"(?i)\b(?:like a|as if|as though)\b",
         "label": "too many similes", "hint": "Keep at most one that earns its place."},
        {"id": "tell.emotion_named", "weight": 2.5, "scope": "dialogue",
         "pattern": r"(?i)\bI (?:feel|felt|am|'m) (?:so |really |a (?:bit|little) )?(?:nervous|sad|happy|scared|lonely|anxious|grateful|overwhelmed)\b",
         "label": "naming the feeling", "hint": "Let it show in what she avoids saying."},
        {"id": "voice.therapy", "weight": 4, "scope": "dialogue",
         "pattern": r"(?i)thank you for (?:sharing|telling me|trusting)|your feelings are valid|i'?m here for you|it'?s okay to feel|take (?:all )?the time you need|you(?:'re| are) not alone|that must (?:be|have been) (?:so )?(?:hard|difficult)",
         "label": "therapist voice", "hint": "Give her own reaction, including not comforting."},
        {"id": "voice.ai_meta", "weight": 10, "hard": True,
         "pattern": r"(?i)\bas an ai\b|\bi (?:can(?:no|')t|am unable to) (?:assist|continue|help)|language model",
         "label": "model talking about itself", "hint": "Rewrite the line."},
        {"id": "close.aphorism", "weight": 3,
         "pattern": r"(?i)\band in that moment\b|\bperhaps that was\b|nothing would ever be the same|\bmaybe,? just maybe\b|little did (?:s?he|they|i) know",
         "label": "aphoristic closer", "hint": "End on an action or an object."},
        {"id": "fmt.markdown", "weight": 6, "hard": True,
         "pattern": r"\*\*[^*\n]+\*\*|(?m:^\s{0,3}#{1,6}\s)|\*[^*\n]{1,60}\*",
         "label": "markdown / asterisk actions", "hint": "No formatting marks in a line."},
        {"id": "fmt.ooc", "weight": 6, "hard": True,
         "pattern": r"(?i)[(\[]\s*OOC|\bOOC\s*:",
         "label": "out-of-character note", "hint": "Only the character's words."},
        {"id": "fmt.emoji", "weight": 6, "hard": True,
         "pattern": "[\U0001F300-\U0001FAFF\u2728\u2764\u2705\u274C]",
         "label": "emoji", "hint": "Remove it."},
    ],
    "regional_terms": {},
}

_JA: dict[str, Any] = {
    "region": "",
    "pov": "first",
    "line_max_chars": 44,
    "narration_max_chars": 80,
    "particles": "ねよなわぞぜかのさ",
    "dialogue_rules": [
        "一度に一、二文だけ。短く、口語で、言いかけてやめても、質問に答えなくてもいい。",
        "感情を言葉にしない。「悲しい」「緊張する」と言わせず、言葉の選び方や話題のそらし方に出す。",
        "まとめない、教訓を言わない、場面を締めくくらない。",
        "必要がなければ礼も謝罪も慰めも言わない。相手の名前を毎回呼ばない。",
    ],
    "narration_rules": [
        "主人公に見えるもの、聞こえるものだけを書く。他人の心情は書かない。",
        "一行に一つの画。具体的な物を書き、雰囲気を書かない。",
    ],
    "prompt_avoid": ["息を呑んだ", "胸が締め付けられる", "時が止まったよう", "不思議と"],
    "patterns": [
        {"id": "stock.breath", "weight": 3, "pattern": r"息を[呑飲]ん[だで]|深く息を吸",
         "label": "息を呑む", "hint": "削る。"},
        {"id": "stock.chest", "weight": 3,
         "pattern": r"胸が(?:締め付けられ|高鳴|ざわつ|熱くな)|心臓が(?:跳ね|止まり)",
         "label": "胸が締め付けられる", "hint": "その瞬間の小さな失敗を書く。"},
        {"id": "stock.time", "weight": 3, "pattern": r"時(?:間)?が止まった(?:よう|かの)|空気が(?:凍|張り詰め)",
         "label": "時が止まったよう", "hint": "聞こえる音を書く。"},
        {"id": "stock.somehow", "weight": 2, "pattern": r"不思議と|なぜか分からないが|どこか(?:寂し|懐かし|儚)",
         "label": "不思議と／どこか", "hint": "はっきり書くか、書かない。"},
        {"id": "struct.simile", "weight": 4, "max_per_100": 6,
         "pattern": r"まるで[^。！？\n]{1,20}(?:よう|みたい)|かのよう[なに]",
         "label": "比喩が多い", "hint": "一場面に一つまで。"},
        {"id": "voice.therapy", "weight": 4, "scope": "dialogue",
         "pattern": r"話してくれてありがとう|あなたの気持ち(?:は|を)|一人じゃない(?:よ|から)|無理しなくていい",
         "label": "カウンセラー口調", "hint": "その人物自身の反応に。"},
        {"id": "voice.ai_meta", "weight": 10, "hard": True,
         "pattern": r"AIとして|言語モデル|(?i:as an ai\b)", "label": "モデルの自己言及",
         "hint": "書き直す。"},
        {"id": "fmt.markdown", "weight": 6, "hard": True,
         "pattern": r"\*\*[^*\n]+\*\*|(?m:^\s{0,3}#{1,6}\s)|\*[^*\n]{1,40}\*",
         "label": "Markdown", "hint": "記号を入れない。"},
        {"id": "fmt.emoji", "weight": 6, "hard": True,
         "pattern": "[\U0001F300-\U0001FAFF\u2728\u2764\u2705\u274C]", "label": "絵文字", "hint": "削る。"},
    ],
    "regional_terms": {},
}

_BUILTIN: dict[str, dict[str, Any]] = {
    "zh-Hant": _ZH_HANT,
    "zh-Hans": _ZH_HANS,
    "en": _EN,
    "ja": _JA,
}

"""Prompt text for the stage's non-character roles, per language.

The acting prompt for a character comes from a swappable :class:`Preset`. The
director, the narrator, the choice writer, and the per-turn output format are
the stage's own machinery; their wording lives here so it can be read and
tuned in one place.

Each string is a ``str.format`` template. Keep them short: on current models a
long list of rules reads as a checklist to perform, and the result sounds
performed.
"""
from __future__ import annotations

from .style import normalize_locale

_ZH_HANT = {
    # ---- what an actor is asked to produce --------------------------------
    "actor_format": (
        "輪到{char}。只寫{char}這一次開口的內容，照這個格式：\n\n"
        "{sprite_line}「台詞」\n\n"
        "- 一到兩句就好；要兩句時各佔一行，每句都用「」。\n"
        "- 真的需要一個動作時，另起一行寫在（）裡，寫成旁人看得到的一句完整的話"
        "（例：（{char}把東西收進包包。）），最多一行。\n"
        "- 不寫別人（包括{user}）的台詞、動作和心理，不寫旁白，不加說明。"
    ),
    "sprite_line": "<sprite:情緒>\n",
    "player_format": (
        "輪到{char}（玩家操作的主角）。只寫{char}這一次開口的內容：\n\n"
        "「台詞」\n\n"
        "- 一句就好，像隨口說出來的話。\n"
        "- 需要動作時另起一行寫在（）裡，主詞用「{pov}」，最多一行。\n"
        "- 不寫別人的台詞、動作和心理，不加說明。"
    ),
    "cue_open": "（排演開始，{char}先開口。）",
    "example_sep": "（另一次）",
    "cue_again": "（{char}接著說。）",
    "narration_line": "（{text}）",
    "speech_line": "{name}：「{text}」",
    # ---- restaging an existing line ---------------------------------------
    "restage_speech": (
        "［這一句的原稿］{source}\n"
        "原稿要傳達的事要留著，但用{char}自己的話重說：可以更短，可以不說完，"
        "可以換一個講法。不要比原稿多出新的資訊。"
    ),
    "restage_narration": (
        "［這一行的原稿］{source}\n"
        "同一個畫面，重寫成{user}看得到、聽得到的具體東西。不要比原稿長，"
        "不要加進原稿沒有的事。"
    ),
    "restage_keep": "［這些字眼必須原樣出現］{tokens}",
    "restage_one_line": "這次只要一句台詞，不加動作行。",
    "restage_next": "［下一句對方會接這個，別先把它講掉］{next}",
    "keep_missing": "少了必須保留的字眼：{tokens}",
    # ---- narrator ---------------------------------------------------------
    "narrator_system": (
        "你負責這場戲的敘述文字（旁白）。視角是「{user}」：只寫{user}看得到、聽得到、"
        "摸得到的東西，用「{pov}」稱呼{user}。\n"
        "別的角色心裡在想什麼你不知道，所以不寫她們的心理，只寫看得到的動作和"
        "聽得到的聲音。\n"
        "{rules}"
    ),
    "narrator_assets_title": "【場景、音樂與 CG】",
    "narrator_bg": (
        "可以用的場景：\n{items}\n目前場景：{current}\n"
        "換地點時，在那一行前面單獨寫一行 <bg:場景名>，名字照上面的寫。"
        "地點沒變就不要寫。"
    ),
    "narrator_bgm": (
        "可以用的音樂：\n{items}\n目前音樂：{current}\n"
        "只有氣氛明顯轉變時才單獨寫一行 <bgm:音樂名>；沒有明顯變化就不要換。"
    ),
    "narrator_cg": (
        "可以用的 CG：\n{items}\n"
        "畫面對得上某張 CG 時，在那一行前面單獨寫一行 <cg:CG名>；"
        "那個場面結束時寫 <cg:off>。對不上就不要硬用。"
    ),
    "narrator_user": (
        "【情境】\n{premise}\n\n【到目前為止】\n{transcript}\n\n"
        "【這裡要寫的】\n{cue}\n\n"
        "只寫旁白，一到兩行，每行一個畫面。不寫任何人的台詞，不加說明。"
    ),
    "narrator_default_cue": "接著上面，寫{user}此刻看到的一個畫面。",
    "narrator_open_cue": "開場：用一兩行交代{user}人在哪裡、眼前有什麼。",
    "none": "（無）",
    "nothing_yet": "（還沒有人開口）",
    # ---- director ---------------------------------------------------------
    "director_system": (
        "你是這場排演的導演。你不寫台詞，也不寫旁白。你只做兩件事："
        "決定下一個開口的是誰，並給那個人一句可以演的提示。\n\n"
        "提示要寫成演員做得到的事——一個動作或一個意圖，例如「不回答，反問他為什麼"
        "想知道」「假裝沒聽到，繼續整理書架」「話說到一半停下來」。不要寫成形容詞"
        "（「難過地」「溫柔地」），不要直接給台詞，也不要把別人的祕密寫進提示裡。\n\n"
        "你看得到所有人的祕密和這場戲要去的地方，演員們看不到。好看的戲來自每個人"
        "各自想要不同的東西：讓他們互相打斷、答非所問、沒說出口。別讓任何人把心裡話"
        "直接講完，也別急著讓事情有結果。\n\n"
        "同一個人可以連續開口。需要交代主角看到的畫面時，叫旁白（narrator）。"
        "這一拍該發生的事發生了，就往下一拍；最後一拍結束，就結束這場戲——"
        "停在一個動作或一件東西上，不要停在一句總結上。"
    ),
    "director_brief": (
        "【情境】\n{premise}\n\n【角色】\n{cast}\n\n【節拍】\n{beats}\n\n"
        "【結尾】\n{ending}"
    ),
    "director_cast_line": "- {id}（{name}）{details}",
    "director_wants": "想要：{text}",
    "director_tactic": "做法：{text}",
    "director_secret": "祕密：{text}",
    "director_beat": "{n}. {goal}{must}{mark}",
    "director_must": "（要帶到：{items}）",
    "director_now": "　← 現在",
    "director_user": (
        "【到目前為止】（第 {turn} 句，上限 {max_turns}；這一拍已經 {beat_turns} 句，"
        "上限 {beat_max}）\n{transcript}\n\n"
        "請只回答這四行：\n"
        "下一個：<{options}>\n"
        "提示：<一句話。narrator 就寫要描述的畫面；end 就留空>\n"
        "節拍：<stay｜next>\n"
        "理由：<十個字以內>"
    ),
    "default_ending": "停在一個動作或一件具體的東西上。",
    # ---- choices ----------------------------------------------------------
    "choices_system": (
        "你替視覺小說寫玩家的選項。選項是主角「{user}」自己會說的話或會做的事，"
        "要短、要口語、要是真的不同的態度。"
    ),
    "choices_user": (
        "【{user}】\n{persona}\n\n【剛才的戲】\n{transcript}\n\n"
        "這場戲停在這裡，輪到玩家選。替下面每個選項寫出{user}會說的那句話"
        "（或會做的那件事）：\n{options}\n\n"
        "- 每個選項不超過 {limit} 個字。\n"
        "- 選項之間是不同的態度，不是同一句話的幾種客氣程度。\n"
        "- 不要在選項裡說明後果。\n"
        "格式：每行一個「id｜文字」，不要其他內容。"
    ),
    "choices_option": "- {id}：{intent}",
    "pov_first": "我", "pov_second": "你",
}

_ZH_HANS = {
    "actor_format": (
        "轮到{char}。只写{char}这一次开口的内容，照这个格式：\n\n"
        "{sprite_line}「台词」\n\n"
        "- 一到两句就好；要两句时各占一行，每句都用「」。\n"
        "- 真的需要一个动作时，另起一行写在（）里，写成旁人看得到的一句完整的话"
        "（例：（{char}把东西收进包里。）），最多一行。\n"
        "- 不写别人（包括{user}）的台词、动作和心理，不写旁白，不加说明。"
    ),
    "sprite_line": "<sprite:情绪>\n",
    "player_format": (
        "轮到{char}（玩家操作的主角）。只写{char}这一次开口的内容：\n\n"
        "「台词」\n\n"
        "- 一句就好，像随口说出来的话。\n"
        "- 需要动作时另起一行写在（）里，主语用「{pov}」，最多一行。\n"
        "- 不写别人的台词、动作和心理，不加说明。"
    ),
    "cue_open": "（排演开始，{char}先开口。）",
    "example_sep": "（另一次）",
    "cue_again": "（{char}接着说。）",
    "narration_line": "（{text}）",
    "speech_line": "{name}：「{text}」",
    "restage_speech": (
        "［这一句的原稿］{source}\n"
        "原稿要传达的事要留着，但用{char}自己的话重说：可以更短，可以不说完，"
        "可以换一个讲法。不要比原稿多出新的信息。"
    ),
    "restage_narration": (
        "［这一行的原稿］{source}\n"
        "同一个画面，重写成{user}看得到、听得到的具体东西。不要比原稿长，"
        "不要加进原稿没有的事。"
    ),
    "restage_keep": "［这些字眼必须原样出现］{tokens}",
    "restage_one_line": "这次只要一句台词，不加动作行。",
    "restage_next": "［下一句对方会接这个，别先把它讲掉］{next}",
    "keep_missing": "少了必须保留的字眼：{tokens}",
    "narrator_system": (
        "你负责这场戏的叙述文字（旁白）。视角是「{user}」：只写{user}看得到、听得到、"
        "摸得到的东西，用「{pov}」称呼{user}。\n"
        "别的角色心里在想什么你不知道，所以不写她们的心理，只写看得到的动作和"
        "听得到的声音。\n"
        "{rules}"
    ),
    "narrator_assets_title": "【场景、音乐与 CG】",
    "narrator_bg": (
        "可以用的场景：\n{items}\n目前场景：{current}\n"
        "换地点时，在那一行前面单独写一行 <bg:场景名>，名字照上面的写。"
        "地点没变就不要写。"
    ),
    "narrator_bgm": (
        "可以用的音乐：\n{items}\n目前音乐：{current}\n"
        "只有气氛明显转变时才单独写一行 <bgm:音乐名>；没有明显变化就不要换。"
    ),
    "narrator_cg": (
        "可以用的 CG：\n{items}\n"
        "画面对得上某张 CG 时，在那一行前面单独写一行 <cg:CG名>；"
        "那个场面结束时写 <cg:off>。对不上就不要硬用。"
    ),
    "narrator_user": (
        "【情境】\n{premise}\n\n【到目前为止】\n{transcript}\n\n"
        "【这里要写的】\n{cue}\n\n"
        "只写旁白，一到两行，每行一个画面。不写任何人的台词，不加说明。"
    ),
    "narrator_default_cue": "接着上面，写{user}此刻看到的一个画面。",
    "narrator_open_cue": "开场：用一两行交代{user}人在哪里、眼前有什么。",
    "none": "（无）",
    "nothing_yet": "（还没有人开口）",
    "director_system": (
        "你是这场排演的导演。你不写台词，也不写旁白。你只做两件事："
        "决定下一个开口的是谁，并给那个人一句可以演的提示。\n\n"
        "提示要写成演员做得到的事——一个动作或一个意图，例如「不回答，反问他为什么"
        "想知道」「假装没听到，继续整理书架」「话说到一半停下来」。不要写成形容词"
        "（「难过地」「温柔地」），不要直接给台词，也不要把别人的秘密写进提示里。\n\n"
        "你看得到所有人的秘密和这场戏要去的地方，演员们看不到。好看的戏来自每个人"
        "各自想要不同的东西：让他们互相打断、答非所问、没说出口。别让任何人把心里话"
        "直接讲完，也别急着让事情有结果。\n\n"
        "同一个人可以连续开口。需要交代主角看到的画面时，叫旁白（narrator）。"
        "这一拍该发生的事发生了，就往下一拍；最后一拍结束，就结束这场戏——"
        "停在一个动作或一件东西上，不要停在一句总结上。"
    ),
    "director_brief": (
        "【情境】\n{premise}\n\n【角色】\n{cast}\n\n【节拍】\n{beats}\n\n"
        "【结尾】\n{ending}"
    ),
    "director_cast_line": "- {id}（{name}）{details}",
    "director_wants": "想要：{text}",
    "director_tactic": "做法：{text}",
    "director_secret": "秘密：{text}",
    "director_beat": "{n}. {goal}{must}{mark}",
    "director_must": "（要带到：{items}）",
    "director_now": "　← 现在",
    "director_user": (
        "【到目前为止】（第 {turn} 句，上限 {max_turns}；这一拍已经 {beat_turns} 句，"
        "上限 {beat_max}）\n{transcript}\n\n"
        "请只回答这四行：\n"
        "下一个：<{options}>\n"
        "提示：<一句话。narrator 就写要描述的画面；end 就留空>\n"
        "节拍：<stay｜next>\n"
        "理由：<十个字以内>"
    ),
    "default_ending": "停在一个动作或一件具体的东西上。",
    "choices_system": (
        "你替视觉小说写玩家的选项。选项是主角「{user}」自己会说的话或会做的事，"
        "要短、要口语、要是真的不同的态度。"
    ),
    "choices_user": (
        "【{user}】\n{persona}\n\n【刚才的戏】\n{transcript}\n\n"
        "这场戏停在这里，轮到玩家选。替下面每个选项写出{user}会说的那句话"
        "（或会做的那件事）：\n{options}\n\n"
        "- 每个选项不超过 {limit} 个字。\n"
        "- 选项之间是不同的态度，不是同一句话的几种客气程度。\n"
        "- 不要在选项里说明后果。\n"
        "格式：每行一个「id｜文字」，不要其他内容。"
    ),
    "choices_option": "- {id}：{intent}",
    "pov_first": "我", "pov_second": "你",
}

_EN = {
    "actor_format": (
        "It is {char}'s turn. Write only what {char} says this once, in this shape:\n\n"
        "{sprite_line}“the line”\n\n"
        "- One or two sentences; if two, one per row, each in quotation marks.\n"
        "- If an action is really needed, add one more row in parentheses, written as "
        "a full sentence an onlooker could see (e.g. ({char} puts it back in the "
        "bag.)).\n"
        "- Nobody else's words, actions or thoughts ({user} included), no narration, "
        "no commentary."
    ),
    "sprite_line": "<sprite:emotion>\n",
    "player_format": (
        "It is {char}'s turn (the player's character). Write only what {char} says "
        "this once:\n\n“the line”\n\n"
        "- One sentence, the way it would be said offhand.\n"
        "- An action, if needed, goes on its own row in parentheses with “{pov}” "
        "as the subject.\n"
        "- Nobody else's words, actions or thoughts, no commentary."
    ),
    "cue_open": "(The rehearsal begins; {char} speaks first.)",
    "example_sep": "(another time)",
    "cue_again": "({char} goes on.)",
    "narration_line": "({text})",
    "speech_line": "{name}: “{text}”",
    "restage_speech": (
        "[The line as drafted] {source}\n"
        "Keep what it has to convey, but say it in {char}'s own words: shorter is "
        "fine, unfinished is fine, a different way in is fine. Add no information "
        "the draft did not have."
    ),
    "restage_narration": (
        "[The row as drafted] {source}\n"
        "The same image, rewritten as concrete things {user} can see and hear. No "
        "longer than the draft, nothing the draft did not contain."
    ),
    "restage_keep": "[These must appear exactly] {tokens}",
    "restage_one_line": "Just the one line this time, no action row.",
    "restage_next": "[The other person answers with this next; do not pre-empt it] {next}",
    "keep_missing": "missing required wording: {tokens}",
    "narrator_system": (
        "You write the narration for this scene. The viewpoint is {user}: only what "
        "{user} can see, hear or touch, addressed as “{pov}”.\n"
        "You do not know what the other characters think, so never write their "
        "thoughts — only actions that can be seen and sounds that can be heard.\n"
        "{rules}"
    ),
    "narrator_assets_title": "[Backgrounds, music and event CGs]",
    "narrator_bg": (
        "Backgrounds available:\n{items}\nCurrent background: {current}\n"
        "When the place changes, put <bg:name> on a row of its own before that row, "
        "using a name from the list. Do not write it when the place is the same."
    ),
    "narrator_bgm": (
        "Music available:\n{items}\nCurrent music: {current}\n"
        "Only when the mood clearly turns, put <bgm:name> on a row of its own."
    ),
    "narrator_cg": (
        "Event CGs available:\n{items}\n"
        "When the picture matches one, put <cg:name> on its own row before that row, "
        "and <cg:off> when that moment ends. Do not force one that does not fit."
    ),
    "narrator_user": (
        "[Situation]\n{premise}\n\n[So far]\n{transcript}\n\n"
        "[What to write here]\n{cue}\n\n"
        "Narration only, one or two rows, one image per row. Nobody's speech, no "
        "commentary."
    ),
    "narrator_default_cue": "Following on, one image of what {user} sees right now.",
    "narrator_open_cue": "Opening: one or two rows on where {user} is and what is "
                         "in front of them.",
    "none": "(none)",
    "nothing_yet": "(nobody has spoken yet)",
    "director_system": (
        "You are directing this rehearsal. You write no dialogue and no narration. "
        "You do two things: decide who speaks next, and give that person one playable "
        "note.\n\n"
        "A note is something an actor can do — an action or an intention, such as "
        "\"don't answer; ask why he wants to know\", \"pretend not to hear and keep "
        "shelving books\", \"stop halfway through the sentence\". Not an adjective "
        "(\"sadly\", \"gently\"), never the line itself, and never someone else's "
        "secret.\n\n"
        "You can see everyone's secrets and where the scene is going; the actors "
        "cannot. Good scenes come from people wanting different things: let them "
        "interrupt, talk past each other, leave things unsaid. Don't let anyone say "
        "the whole of what they feel, and don't hurry to a result.\n\n"
        "The same person may speak twice in a row. When what the protagonist sees "
        "needs describing, call the narrator. When a beat has happened, move to the "
        "next; after the last beat, end the scene — on an action or an object, not "
        "on a summary."
    ),
    "director_brief": (
        "[Situation]\n{premise}\n\n[Cast]\n{cast}\n\n[Beats]\n{beats}\n\n"
        "[Ending]\n{ending}"
    ),
    "director_cast_line": "- {id} ({name}) {details}",
    "director_wants": "wants: {text}",
    "director_tactic": "approach: {text}",
    "director_secret": "secret: {text}",
    "director_beat": "{n}. {goal}{must}{mark}",
    "director_must": " (must surface: {items})",
    "director_now": "  <- now",
    "director_user": (
        "[So far] (line {turn} of at most {max_turns}; this beat has had "
        "{beat_turns} of at most {beat_max})\n{transcript}\n\n"
        "Answer with these four rows only:\n"
        "next: <{options}>\n"
        "note: <one sentence. For narrator, the image to describe; for end, leave "
        "empty>\n"
        "beat: <stay|next>\n"
        "why: <a few words>"
    ),
    "default_ending": "Stop on an action or a concrete object.",
    "choices_system": (
        "You write the player's choices for a visual novel. A choice is something the "
        "protagonist {user} would say or do: short, spoken, and a genuinely different "
        "attitude from the others."
    ),
    "choices_user": (
        "[{user}]\n{persona}\n\n[The scene so far]\n{transcript}\n\n"
        "The scene stops here and the player chooses. For each option below, write "
        "what {user} would say (or do):\n{options}\n\n"
        "- At most {limit} characters each.\n"
        "- Different attitudes, not the same sentence at different levels of "
        "politeness.\n"
        "- Do not explain consequences in the option.\n"
        "Format: one per row as \"id|text\", nothing else."
    ),
    "choices_option": "- {id}: {intent}",
    "pov_first": "I", "pov_second": "you",
}

_TEXT = {"zh-Hant": _ZH_HANT, "zh-Hans": _ZH_HANS, "en": _EN, "ja": _EN}


def stage_text(locale: str) -> dict[str, str]:
    """The stage's prompt strings for ``locale``."""
    return _TEXT.get(normalize_locale(locale)) or _EN

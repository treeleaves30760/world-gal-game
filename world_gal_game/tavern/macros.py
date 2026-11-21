"""Tavern macro expansion: ``{{char}}``, ``{{random::a::b}}``, ``{{setvar::k::v}}``…

Cards, presets and world-book entries written for tavern front-ends are full
of ``{{macro}}`` placeholders. This module expands the widely used set so that
imported content behaves the way its author expected, plus a few engine-bound
ones (``{{location}}``, ``{{timeOfDay}}``, ``{{affection}}``…) that the stage
fills from the live game state.

Three properties matter for the engine and differ from a chat front-end:

- **Deterministic.** ``{{random}}`` / ``{{roll}}`` draw from the generator in
  :class:`MacroContext` (the stage passes ``GameState.rng()``), and ``{{pick}}``
  is a stable hash, so a seeded run renders the same prompt every time.
- **Game clock, not wall clock.** ``{{time}}`` / ``{{date}}`` / ``{{weekday}}``
  read the values the caller supplies (the in-fiction time). They render as an
  empty string when no value is supplied rather than leaking the real date
  into a story.
- **Sandboxed.** Variable macros only read and write the dictionaries in the
  context. Nothing here can reach game state, so a downloaded card cannot set
  a flag or change affection by containing a macro.

Text is parsed once and evaluated strictly left to right, so
``{{setvar::mood::calm}}{{if .mood}}…{{/if}}`` behaves the way it reads, and
the branch of an ``{{if}}`` that is not taken is never evaluated. Values that
come from the conversation (``{{lastMessage}}``, ``{{input}}``, variables) are
inserted as data and are *not* re-scanned for macros.

Unknown macros are left in the text untouched (so an author notices a typo)
and are reported in :attr:`MacroContext.unknown`.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Callable

from ..core.game_state import seeded_rng

if TYPE_CHECKING:  # pragma: no cover - typing only
    from .card import CharacterCard

_ROLL_RE = re.compile(r"^\s*(\d*)\s*d\s*(\d+)\s*([+-]\s*\d+)?\s*$", re.IGNORECASE)
_LEGACY = {
    "<user>": "{{user}}", "<bot>": "{{char}}", "<char>": "{{char}}",
    "<group>": "{{group}}", "<charifnotgroup>": "{{charIfNotGroup}}",
}
_LEGACY_RE = re.compile("|".join(re.escape(k) for k in _LEGACY), re.IGNORECASE)
_NAME_RE = re.compile(r"^([A-Za-z_][\w]*)\s*:\s*(.*)$", re.DOTALL)
_SPACE_FORM_RE = re.compile(r"^(\S+)\s+(.*)$", re.DOTALL)
_TRIM_MARK = "\x01"
_TRIM_MARK_RE = re.compile(r"\s*\x01\s*")

# Front-end features the engine has no equivalent for: render as nothing
# instead of leaving a stray placeholder in the prompt.
_ALWAYS_EMPTY = frozenset({
    "summary", "authorsnote", "charauthorsnote", "defaultauthorsnote",
    "charprefix", "charnegativeprefix", "instructinput", "instructoutput",
    "instructsystem", "instructstop", "instructfirstoutput", "instructlastoutput",
    "instructstorystringprefix", "instructstorystringsuffix",
    "instructsysteminstructionprefix", "instructuserfiller", "instructsystemprompt",
    "systemprompt", "defaultsystemprompt", "examplesep", "exampleseparator",
    "chatstart", "instructsystemsuffix", "instructinputsuffix", "instructoutputsuffix",
    "instructsystemprefix", "instructfirstinput", "instructlastinput",
    "instructseparator", "currentswipeid", "lastswipeid",
})
# Clock macros: served from MacroContext.values, never from the wall clock.
_CLOCK = frozenset({
    "time", "date", "weekday", "isotime", "isodate", "datetimeformat",
    "idle_duration", "timediff", "time_utc",
})
# Card-template fields: their text is itself a template and is expanded.
_TEMPLATE_FIELDS = frozenset({
    "description", "personality", "scenario", "persona", "mesexamples",
    "mesexamplesraw", "charprompt", "charinstruction", "charjailbreak",
    "creatornotes", "chardepthprompt", "greeting", "original", "outlet",
})

#: Nesting limit for templates that include other templates.
MAX_TEMPLATE_DEPTH = 4


@dataclass
class MacroContext:
    """Everything a macro can read, and the two things it can write."""

    char: str = ""
    user: str = ""
    card: "CharacterCard | None" = None
    persona: str = ""                       # the player's persona description
    group: list[str] = field(default_factory=list)
    group_not_muted: list[str] | None = None
    #: Chat-local variables (``{{setvar}}`` / ``{{getvar}}`` / ``{{.name}}``).
    variables: dict[str, Any] = field(default_factory=dict)
    #: Session-wide variables (``{{setglobalvar}}`` / ``{{$name}}``).
    global_variables: dict[str, Any] = field(default_factory=dict)
    #: Generator for ``{{random}}`` and ``{{roll}}`` (a seeded ``Random``).
    #: Left unset, a fixed-seed one is used so rendering stays reproducible.
    rng: Any = None
    #: Plain values by lower-cased macro name: time/date/location/chapter…
    values: dict[str, str] = field(default_factory=dict)
    #: Hook for parametric engine macros (``{{flag::x}}``, ``{{affection::id}}``).
    #: Returns ``None`` when it does not handle ``name``.
    resolver: Callable[[str, list[str]], str | None] | None = None
    last_message: str = ""
    last_user_message: str = ""
    last_char_message: str = ""
    last_message_id: int | None = None
    input: str = ""
    original: str = ""                      # preset text a card override replaces
    outlets: dict[str, str] = field(default_factory=dict)
    pick_seed: str = ""
    generation_type: str = "normal"
    model: str = ""
    max_prompt: int | None = None
    # ---- outputs -------------------------------------------------------
    banned: list[str] = field(default_factory=list)     # from {{banned "…"}}
    unknown: list[str] = field(default_factory=list)

    def _rng(self) -> Any:
        if self.rng is None:
            self.rng = seeded_rng(0)
        return self.rng


class _Macro:
    """A parsed ``{{…}}`` node: ``parts`` alternates text and nested nodes."""

    __slots__ = ("parts", "raw")

    def __init__(self, parts: list, raw: str) -> None:
        self.parts = parts
        self.raw = raw

    def head(self) -> str:
        """The literal text the body starts with (empty if it starts nested)."""
        return self.parts[0] if self.parts and isinstance(self.parts[0], str) else ""


def _parse(text: str, start: int, nested: bool) -> tuple[list, int, bool]:
    """Parse ``text`` from ``start`` into a node list.

    Returns ``(nodes, next_index, closed)``. With ``nested`` true the parse
    stops at the ``}}`` closing the enclosing macro; ``closed`` is false when
    the text ran out first (an unbalanced ``{{``, treated as literal text by
    the caller).
    """
    nodes: list = []
    i = start
    size = len(text)
    while True:
        j_open = text.find("{{", i)
        j_close = text.find("}}", i) if nested else -1
        if nested and j_close != -1 and (j_open == -1 or j_close < j_open):
            if j_close > i:
                nodes.append(text[i:j_close])
            return nodes, j_close + 2, True
        if j_open == -1:
            if i < size:
                nodes.append(text[i:])
            return nodes, size, not nested
        if j_open > i:
            nodes.append(text[i:j_open])
        inner, after, closed = _parse(text, j_open + 2, True)
        if closed:
            nodes.append(_Macro(inner, text[j_open:after]))
            i = after
        else:
            nodes.append("{{")
            i = j_open + 2


def _truthy(value: Any) -> bool:
    text = str(value).strip().casefold() if value is not None else ""
    return text not in ("", "false", "off", "0", "no", "none", "null")


def _number(value: Any) -> float | None:
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return None


def _fmt_number(value: float) -> str:
    return str(int(value)) if float(value).is_integer() else str(value)


def _split_args(body: str) -> tuple[str, list[str]]:
    """Split a macro body into ``(name, args)``.

    Handles the ``name::a::b`` form, the legacy ``name:a,b`` form and the
    ``name arg`` form (``roll 20``, ``banned "x"``).
    """
    body = body.strip()
    if "::" in body:
        parts = body.split("::")
        return parts[0].strip(), parts[1:]
    match = _NAME_RE.match(body)
    if match:
        name, rest = match.group(1), match.group(2)
        if name.casefold() in ("random", "pick"):
            return name, [part.strip() for part in re.split(r"(?<!\\),", rest)]
        return name, [rest]
    match = _SPACE_FORM_RE.match(body)
    if match:
        return match.group(1), [match.group(2)]
    return body, []


def _block_kind(node: Any) -> str:
    """Classify a node as a block token: ``if`` / ``else`` / ``/if`` / …"""
    if not isinstance(node, _Macro):
        return ""
    head = node.head().strip().casefold()
    if head.startswith("if ") or head == "if":
        return "if"
    if len(node.parts) == 1 and head in ("else", "/if", "trim", "/trim"):
        return head
    return ""


class _Expander:
    def __init__(self, ctx: MacroContext) -> None:
        self.ctx = ctx
        self._pick_index = 0

    # -- sequences and blocks -------------------------------------------

    def eval_nodes(self, nodes: list, depth: int = 0) -> str:
        out: list[str] = []
        i = 0
        count = len(nodes)
        while i < count:
            node = nodes[i]
            if isinstance(node, str):
                out.append(node)
                i += 1
                continue
            kind = _block_kind(node)
            if kind == "if":
                else_at, close_at = self._match_if(nodes, i)
                if close_at is None:
                    i += 1               # unbalanced opener: drop it
                    continue
                cond_text = self.eval_nodes(node.parts, depth)
                cond_text = cond_text.strip()[2:].strip()   # drop the leading "if"
                # "{{if {{getvar::x}}}}" has already been expanded to a value;
                # "{{if name}}" still names a macro or variable to look up.
                literal = any(isinstance(part, _Macro) for part in node.parts)
                if self._condition(cond_text, literal=literal):
                    branch = nodes[i + 1:else_at if else_at is not None else close_at]
                else:
                    branch = nodes[else_at + 1:close_at] if else_at is not None else []
                out.append(self.eval_nodes(branch, depth))
                i = close_at + 1
                continue
            if kind == "trim":
                close_at = self._match_trim(nodes, i)
                if close_at is None:
                    out.append(_TRIM_MARK)      # standalone: eat blank lines around
                    i += 1
                else:
                    out.append(self.eval_nodes(nodes[i + 1:close_at], depth).strip())
                    i = close_at + 1
                continue
            if kind in ("else", "/if", "/trim"):
                i += 1                   # stray block token: drop it
                continue
            out.append(self._eval_macro(node, depth))
            i += 1
        return "".join(out)

    @staticmethod
    def _match_if(nodes: list, start: int) -> tuple[int | None, int | None]:
        level = 0
        else_at: int | None = None
        for j in range(start + 1, len(nodes)):
            kind = _block_kind(nodes[j])
            if kind == "if":
                level += 1
            elif kind == "/if":
                if level == 0:
                    return else_at, j
                level -= 1
            elif kind == "else" and level == 0 and else_at is None:
                else_at = j
        return None, None

    @staticmethod
    def _match_trim(nodes: list, start: int) -> int | None:
        for j in range(start + 1, len(nodes)):
            kind = _block_kind(nodes[j])
            if kind == "/trim":
                return j
            if kind == "trim":
                return None
        return None

    def _condition(self, expr: str, *, literal: bool = False) -> bool:
        negate = False
        while expr.startswith("!"):
            negate = not negate
            expr = expr[1:].strip()
        if literal:
            value: Any = expr
        elif expr.startswith("."):
            value = self.ctx.variables.get(expr[1:].strip(), "")
        elif expr.startswith("$"):
            value = self.ctx.global_variables.get(expr[1:].strip(), "")
        else:
            rendered = self._resolve(expr, raw="{{" + expr + "}}", depth=0, probe=True)
            value = rendered if rendered is not None else ""   # unknown name: false
        result = _truthy(value)
        return (not result) if negate else result

    # -- one macro ------------------------------------------------------

    def _eval_macro(self, node: _Macro, depth: int) -> str:
        if node.head().lstrip().startswith("//"):
            return ""                    # comment: never evaluate what is inside
        body = self.eval_nodes(node.parts, depth)
        rendered = self._resolve(body, raw=node.raw, depth=depth)
        if rendered is None:
            name = _split_args(body)[0]
            if name and name not in self.ctx.unknown:
                self.ctx.unknown.append(name)
            return "{{" + body + "}}"
        return rendered

    def _template(self, text: str, depth: int) -> str:
        """Expand a value that is itself template text (a card field)."""
        if not text or "{{" not in text or depth >= MAX_TEMPLATE_DEPTH:
            return text or ""
        nodes, _end, _closed = _parse(text, 0, False)
        return self.eval_nodes(nodes, depth + 1)

    def _card_value(self, key: str) -> str | None:
        card = self.ctx.card
        if key == "persona":
            return self.ctx.persona
        if key == "original":
            return self.ctx.original
        if card is None:
            return "" if key in _TEMPLATE_FIELDS or key in (
                "charversion", "char_version") else None
        table = {
            "description": card.description,
            "personality": card.personality,
            "scenario": card.scenario,
            "mesexamplesraw": card.mes_example,
            "mesexamples": card.mes_example,
            "charprompt": card.system_prompt,
            "charinstruction": card.post_history_instructions,
            "charjailbreak": card.post_history_instructions,
            "charversion": card.character_version,
            "char_version": card.character_version,
            "creatornotes": card.creator_notes,
            "chardepthprompt": card.depth_prompt.prompt if card.depth_prompt else "",
        }
        return table.get(key)

    def _resolve(self, body: str, *, raw: str, depth: int,
                 probe: bool = False) -> str | None:
        """Render one macro body. ``None`` means "not a macro we know"."""
        ctx = self.ctx
        stripped = body.strip()
        if stripped.startswith("//"):
            return ""
        if stripped.startswith("."):
            return str(ctx.variables.get(stripped[1:].strip(), ""))
        if stripped.startswith("$"):
            return str(ctx.global_variables.get(stripped[1:].strip(), ""))
        name, args = _split_args(stripped)
        key = name.casefold()

        # -- identity ---------------------------------------------------
        if key == "char":
            return ctx.char
        if key == "user":
            return ctx.user
        if key == "group":
            return ", ".join(ctx.group) if ctx.group else ctx.char
        if key == "groupnotmuted":
            members = ctx.group_not_muted if ctx.group_not_muted is not None else ctx.group
            return ", ".join(members) if members else ctx.char
        if key == "charifnotgroup":
            return ", ".join(ctx.group) if len(ctx.group) > 1 else ctx.char
        if key == "notchar":
            others = [n for n in ctx.group if n != ctx.char]
            if ctx.user and ctx.user not in others:
                others.append(ctx.user)
            return ", ".join(others) if others else ctx.user

        # -- card / template fields -------------------------------------
        if key == "greeting":
            card = ctx.card
            if card is None:
                return ""
            index = int(_number(args[0]) or 0) if args else 0
            greetings = [card.first_mes, *card.alternate_greetings]
            value = greetings[index] if 0 <= index < len(greetings) else ""
            return self._template(value, depth)
        if key == "outlet":
            return self._template(ctx.outlets.get(args[0].strip(), "") if args else "",
                                  depth)
        card_value = self._card_value(key)
        if card_value is not None:
            return self._template(card_value, depth) if key in _TEMPLATE_FIELDS \
                else card_value

        # -- chat -------------------------------------------------------
        if key == "lastmessage":
            return ctx.last_message
        if key == "lastusermessage":
            return ctx.last_user_message
        if key == "lastcharmessage":
            return ctx.last_char_message
        if key == "lastmessageid":
            return "" if ctx.last_message_id is None else str(ctx.last_message_id)
        if key == "input":
            return ctx.input
        if key == "lastgenerationtype":
            return ctx.generation_type
        if key == "model":
            return ctx.model
        if key == "maxprompt":
            return "" if ctx.max_prompt is None else str(ctx.max_prompt)
        if key in ("ismobile", "hasextension"):
            return "false"

        # -- utility ----------------------------------------------------
        if key == "newline":
            count = int(_number(args[0]) or 1) if args else 1
            return "\n" * max(0, min(count, 50))
        if key == "space":
            count = int(_number(args[0]) or 1) if args else 1
            return " " * max(0, min(count, 200))
        if key == "noop":
            return ""
        if key == "reverse":
            return "::".join(args)[::-1]
        if key == "banned":
            phrase = "::".join(args).strip().strip('"').strip("“”「」")
            if phrase and phrase not in ctx.banned and not probe:
                ctx.banned.append(phrase)
            return ""
        if key == "random":
            options = [a for a in args if a != ""]
            return ctx._rng().choice(options) if options else ""
        if key == "pick":
            options = [a for a in args if a != ""]
            if not options:
                return ""
            digest = hashlib.sha256(
                f"{ctx.pick_seed}|{self._pick_index}|{raw}".encode("utf-8")).digest()
            self._pick_index += 1
            return options[int.from_bytes(digest[:4], "big") % len(options)]
        if key == "roll":
            spec = "::".join(args).strip()
            match = _ROLL_RE.match(spec)
            if match:
                count = int(match.group(1) or 1)
                sides = int(match.group(2))
                bonus = int((match.group(3) or "0").replace(" ", ""))
            elif spec.isdigit():
                count, sides, bonus = 1, int(spec), 0
            else:
                return ""
            if sides < 1 or not 1 <= count <= 100:
                return ""
            rng = ctx._rng()
            return str(sum(rng.randint(1, sides) for _ in range(count)) + bonus)

        # -- variables --------------------------------------------------
        for prefix, store in (("global", ctx.global_variables), ("", ctx.variables)):
            if key == f"set{prefix}var" and args:
                if not probe:
                    store[args[0].strip()] = "::".join(args[1:])
                return ""
            if key == f"get{prefix}var" and args:
                return str(store.get(args[0].strip(), ""))
            if key == f"has{prefix}var" and args:
                return "true" if args[0].strip() in store else "false"
            if key == f"delete{prefix}var" and args:
                if not probe:
                    store.pop(args[0].strip(), None)
                return ""
            if key == f"add{prefix}var" and len(args) >= 2:
                var = args[0].strip()
                current, delta = store.get(var, ""), "::".join(args[1:])
                a, b = _number(current if current != "" else 0), _number(delta)
                if not probe:
                    store[var] = (_fmt_number(a + b) if a is not None and b is not None
                                  else f"{current}{delta}")
                return ""
            if key in (f"inc{prefix}var", f"dec{prefix}var") and args:
                var = args[0].strip()
                current_n = _number(store.get(var, 0)) or 0.0
                current_n += 1 if key.startswith("inc") else -1
                if not probe:
                    store[var] = _fmt_number(current_n)
                return _fmt_number(current_n)

        # -- engine-bound values ----------------------------------------
        if ctx.resolver is not None:
            handled = ctx.resolver(key, [a.strip() for a in args])
            if handled is not None:
                return handled
        if not args and key in ctx.values:
            return ctx.values[key]
        if key in _CLOCK:
            return ""        # no game clock supplied: never leak the wall clock
        if key in _ALWAYS_EMPTY:
            return ""
        return None

    # -- whole text -----------------------------------------------------

    def run(self, text: str) -> str:
        text = _LEGACY_RE.sub(lambda m: _LEGACY[m.group(0).casefold()], text)
        nodes, _end, _closed = _parse(text, 0, False)
        rendered = self.eval_nodes(nodes)
        if _TRIM_MARK in rendered:
            rendered = _TRIM_MARK_RE.sub("", rendered)
        return rendered


def expand(text: str | None, ctx: MacroContext) -> str:
    """Expand every macro in ``text`` against ``ctx`` (see module docstring)."""
    if not text:
        return ""
    if "{{" not in text and "<" not in text:
        return text
    return _Expander(ctx).run(text)


def find_macros(text: str | None) -> list[str]:
    """List the macro names used in ``text`` (for inspection / linting)."""
    if not text:
        return []
    names: list[str] = []

    def walk(nodes: list) -> None:
        for node in nodes:
            if isinstance(node, _Macro):
                head = node.head().strip()
                name = _split_args(head)[0] if head else ""
                if name and not name.startswith("//") and name not in names:
                    names.append(name)
                walk(node.parts)

    nodes, _end, _closed = _parse(text, 0, False)
    walk(nodes)
    return names

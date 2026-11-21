"""Where a stage's answers come from.

The stage (``tavern.stage``) never talks to a model. It hands out
:class:`~world_gal_game.tavern.stage.StageRequest` objects and waits for text.
A *backend* is anything that can turn one request into that text:

``anthropic``
    Claude, through the official ``anthropic`` SDK (install the ``llm`` extra).
``openai``
    Any endpoint that speaks the OpenAI chat-completions protocol — local
    model servers and other providers. Not a way to reach Claude; use the
    ``anthropic`` backend for that.
``echo``
    An offline stand-in that replays each character's own example lines. It
    exercises the whole pipeline (parsing, linting, scene writing) with no
    model, which is what tests and dry runs want.
``scripted``
    Canned replies, for tests.

There is also the option of having **no backend at all**: an outer agent pumps
the stage itself (``world-gal-game tavern step``, or the ``tavern.*`` session
ops) and answers each request with a model or sub-agent of its own. That path
needs nothing from this module.

Credentials never live in a pack. The Anthropic SDK resolves its own (an API
key or a signed-in profile); the OpenAI-compatible backend reads its key from
an environment variable whose *name* is configured.
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable

import yaml

from .stage import Stage, StageRequest

#: Default Claude model for every role.
DEFAULT_MODEL = "claude-opus-5-5"

#: Output cap for a non-streaming request. Thinking counts against it, so it
#: is deliberately roomy — a line of dialogue is short, the reasoning before
#: it may not be, and a cap that truncates mid-thought yields an empty reply.
DEFAULT_MAX_TOKENS = 16000

# Models that accept {"role": "system"} inside ``messages``.
_MID_SYSTEM_PREFIXES = (
    "claude-opus-5", "claude-opus-4-8", "claude-fable-5", "claude-mythos-5",
    "claude-sonnet-5-5", "claude-haiku-5-5",
)
# Models with a server-side refusal fallback.
_FALLBACK_PREFIXES = (
    "claude-fable-5-2", "claude-fable-5-1", "claude-mythos-5-2", "claude-mythos-5-1",
    "claude-opus-5-5", "claude-opus-5", "claude-sonnet-5-5",
)
_FALLBACK_BETA = "server-side-fallback-2026-07-01"


class BackendError(RuntimeError):
    """The backend could not produce a reply."""


class BackendUnavailable(BackendError):
    """The backend cannot be used here (SDK missing, not configured)."""


class BackendRefusal(BackendError):
    """The model declined the request."""

    def __init__(self, message: str, *, category: str | None = None) -> None:
        super().__init__(message)
        self.category = category


class Backend:
    """Base class: turn one stage request into reply text."""

    name = "backend"

    def complete(self, request: StageRequest) -> str:  # pragma: no cover - interface
        raise NotImplementedError


# ----------------------------------------------------------------------
# Message shaping shared by the provider backends


def split_system(messages: list[dict[str, str]]) -> tuple[str, list[dict[str, str]]]:
    """Split off the leading system messages as one system prompt."""
    system: list[str] = []
    index = 0
    for index, message in enumerate(messages):
        if message.get("role") != "system":
            break
        system.append(message.get("content", ""))
    else:
        index = len(messages)
    return "\n\n".join(part for part in system if part.strip()), list(messages[index:])


def _merge_same_role(messages: list[dict[str, str]]) -> list[dict[str, str]]:
    merged: list[dict[str, str]] = []
    for message in messages:
        content = message.get("content", "")
        if not content.strip():
            continue
        if merged and merged[-1]["role"] == message["role"]:
            joiner = "\n\n" if message["role"] == "system" else "\n"
            merged[-1]["content"] += joiner + content
        else:
            merged.append({"role": message["role"], "content": content})
    return merged


def shape_chat(messages: list[dict[str, str]], *, mid_system: bool,
               opening_cue: str = "…") -> tuple[str, list[dict[str, str]]]:
    """Adapt builder output to a user/assistant chat with one system prompt.

    Leading system messages become the system prompt. A later system message
    stays a system message only when ``mid_system`` is true *and* it sits where
    the API accepts one (right after a user turn, and either last or followed
    by an assistant turn); otherwise it is folded into the user side of the
    conversation. Neighbouring messages of one role are merged, and the chat
    is made to start with a user turn.
    """
    system, rest = split_system(messages)
    rest = _merge_same_role(rest)
    shaped: list[dict[str, str]] = []
    for index, message in enumerate(rest):
        if message["role"] != "system":
            shaped.append(dict(message))
            continue
        previous = shaped[-1]["role"] if shaped else None
        following = rest[index + 1]["role"] if index + 1 < len(rest) else None
        if mid_system and previous == "user" and following in (None, "assistant"):
            shaped.append(dict(message))
        else:
            shaped.append({"role": "user", "content": message["content"]})
    shaped = _merge_same_role(shaped)
    if not shaped or shaped[0]["role"] != "user":
        shaped.insert(0, {"role": "user", "content": opening_cue})
    return system, shaped


# ----------------------------------------------------------------------
# Claude


@dataclass
class AnthropicBackend(Backend):
    """Claude through the official SDK.

    Sampling parameters are never sent — current Claude models reject them —
    so a preset's ``temperature`` is ignored here and ``effort`` is the knob.
    Thinking is left at the model's default (adaptive). The character's
    system prompt is marked cacheable, since an actor is asked many times
    with the same card and a growing transcript.
    """

    model: str = DEFAULT_MODEL
    #: Per-role model overrides: director / actor / narrator / player / choices.
    models: dict[str, str] = field(default_factory=dict)
    #: Per-role effort overrides; the request's own hint is used otherwise.
    efforts: dict[str, str] = field(default_factory=dict)
    max_tokens: int = DEFAULT_MAX_TOKENS
    #: Re-run a declined request on a fallback model, server side.
    fallbacks: bool = True
    #: Honour a card's ``model_hint`` for that character's lines.
    use_model_hints: bool = False
    client: Any = None
    name = "anthropic"

    def __post_init__(self) -> None:
        self._no_mid_system: set[str] = set()
        self.usage: dict[str, int] = {"requests": 0, "input_tokens": 0,
                                      "output_tokens": 0, "cache_read_input_tokens": 0}

    def _client(self) -> Any:
        if self.client is None:
            try:
                import anthropic
            except ImportError as exc:
                raise BackendUnavailable(
                    "the 'anthropic' package is not installed — install the engine "
                    "with the 'llm' extra (pip install 'world-gal-game[llm]')") from exc
            # Zero-argument construction lets the SDK resolve credentials from
            # the environment or a signed-in profile.
            self.client = anthropic.Anthropic()
        return self.client

    def _model_for(self, request: StageRequest) -> str:
        if self.use_model_hints and request.model_hint.startswith("claude-"):
            return request.model_hint
        return self.models.get(request.role) or self.model

    def build_kwargs(self, request: StageRequest) -> dict[str, Any]:
        """The exact arguments for one ``messages.create`` call."""
        model = self._model_for(request)
        mid_system = model.startswith(_MID_SYSTEM_PREFIXES) \
            and model != "claude-sonnet-5" and model not in self._no_mid_system
        system, messages = shape_chat(request.messages, mid_system=mid_system)
        effort = self.efforts.get(request.role) or str(
            request.params.get("effort") or "low")
        kwargs: dict[str, Any] = {
            "model": model,
            "max_tokens": self.max_tokens,
            "messages": messages,
            "output_config": {"effort": effort},
        }
        if system:
            kwargs["system"] = [{"type": "text", "text": system,
                                 "cache_control": {"type": "ephemeral"}}]
        if self.fallbacks and model.startswith(_FALLBACK_PREFIXES):
            kwargs["betas"] = [_FALLBACK_BETA]
            kwargs["fallbacks"] = "default"
        return kwargs

    def complete(self, request: StageRequest) -> str:
        client = self._client()
        import anthropic
        kwargs = self.build_kwargs(request)
        try:
            response = self._send(client, kwargs)
        except anthropic.BadRequestError as exc:
            if "role 'system' is not supported" not in str(exc):
                raise BackendError(f"Claude rejected the request: {exc}") from exc
            # This model takes no mid-conversation system messages: remember
            # that and resend with them folded into the user turns.
            self._no_mid_system.add(kwargs["model"])
            try:
                response = self._send(client, self.build_kwargs(request))
            except anthropic.APIError as retry_exc:
                raise BackendError(f"Claude request failed: {retry_exc}") from retry_exc
        except anthropic.AuthenticationError as exc:
            raise BackendUnavailable(
                "no usable Claude credentials — set ANTHROPIC_API_KEY or sign in "
                "with `ant auth login`") from exc
        except anthropic.PermissionDeniedError as exc:
            raise BackendError(f"credentials lack access to {kwargs['model']}: "
                               f"{exc}") from exc
        except anthropic.NotFoundError as exc:
            raise BackendError(f"unknown model '{kwargs['model']}': {exc}") from exc
        except anthropic.RateLimitError as exc:
            raise BackendError(f"rate limited after retries: {exc}") from exc
        except anthropic.APIStatusError as exc:
            raise BackendError(f"Claude API error {exc.status_code}: {exc}") from exc
        except anthropic.APIConnectionError as exc:
            raise BackendError(f"could not reach the Claude API: {exc}") from exc

        usage = getattr(response, "usage", None)
        self.usage["requests"] += 1
        for key in ("input_tokens", "output_tokens", "cache_read_input_tokens"):
            self.usage[key] += int(getattr(usage, key, 0) or 0)
        if response.stop_reason == "refusal":
            details = getattr(response, "stop_details", None)
            raise BackendRefusal(
                "the model declined this request"
                + (f": {details.explanation}" if getattr(details, "explanation", None)
                   else ""),
                category=getattr(details, "category", None))
        return "".join(block.text for block in response.content
                       if getattr(block, "type", "") == "text")

    @staticmethod
    def _send(client: Any, kwargs: dict[str, Any]) -> Any:
        # The fallback parameter lives on the beta surface.
        if "betas" in kwargs:
            return client.beta.messages.create(**kwargs)
        return client.messages.create(**kwargs)


# ----------------------------------------------------------------------
# OpenAI-compatible endpoints


@dataclass
class OpenAICompatBackend(Backend):
    """An OpenAI-style ``/chat/completions`` endpoint (not Claude)."""

    base_url: str = ""
    model: str = ""
    api_key: str = ""
    models: dict[str, str] = field(default_factory=dict)
    max_tokens: int = 1024
    temperature: float | None = None
    timeout: float = 120.0
    extra_body: dict[str, Any] = field(default_factory=dict)
    name = "openai"

    def build_payload(self, request: StageRequest) -> dict[str, Any]:
        system, messages = shape_chat(request.messages, mid_system=False)
        payload: dict[str, Any] = {
            "model": self.models.get(request.role) or self.model,
            "messages": ([{"role": "system", "content": system}] if system else [])
            + messages,
            "max_tokens": int(request.params.get("max_tokens") or self.max_tokens),
            "stream": False,
        }
        temperature = request.params.get("temperature")
        if temperature is None:
            temperature = self.temperature
        if temperature is not None:
            payload["temperature"] = float(temperature)
        if request.params.get("top_p") is not None:
            payload["top_p"] = float(request.params["top_p"])
        payload.update(self.extra_body)
        return payload

    def complete(self, request: StageRequest) -> str:
        if not self.base_url or not self.model:
            raise BackendUnavailable(
                "the openai backend needs a base_url and a model "
                "(see docs/tavern.md, 'Connections')")
        url = self.base_url.rstrip("/") + "/chat/completions"
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        body = json.dumps(self.build_payload(request), ensure_ascii=False).encode("utf-8")
        http_request = urllib.request.Request(url, data=body, headers=headers,
                                              method="POST")
        try:
            with urllib.request.urlopen(http_request, timeout=self.timeout) as response:
                data = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:300]
            raise BackendError(f"endpoint returned HTTP {exc.code}: {detail}") from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise BackendError(f"could not reach {url}: {exc}") from exc
        except ValueError as exc:
            raise BackendError(f"endpoint did not return JSON: {exc}") from exc
        try:
            content = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise BackendError("endpoint reply had no choices[0].message.content") from exc
        return content if isinstance(content, str) else ""


# ----------------------------------------------------------------------
# Offline backends


@dataclass
class ScriptedBackend(Backend):
    """Canned replies, for tests and demonstrations.

    ``replies`` is consumed in order. ``by_speaker`` / ``by_role`` queues take
    precedence when they have something for the request, and ``responder`` (a
    callable) takes precedence over everything.
    """

    replies: list[str] = field(default_factory=list)
    by_speaker: dict[str, list[str]] = field(default_factory=dict)
    by_role: dict[str, list[str]] = field(default_factory=dict)
    responder: Callable[[StageRequest], str | None] | None = None
    default: str | None = None
    name = "scripted"

    def __post_init__(self) -> None:
        self.requests: list[StageRequest] = []

    def complete(self, request: StageRequest) -> str:
        self.requests.append(request)
        if self.responder is not None:
            answer = self.responder(request)
            if answer is not None:
                return answer
        for queue in (self.by_speaker.get(request.speaker_id),
                      self.by_role.get(request.role), self.replies):
            if queue:
                return queue.pop(0)
        if self.default is not None:
            return self.default
        raise BackendError(f"scripted backend has no reply for a {request.role} "
                           f"request ({request.speaker_id or '-'})")


@dataclass
class EchoBackend(Backend):
    """Offline stand-in: each character repeats its own example lines.

    Nothing here is writing. It lets a brief run end to end without a model so
    the plumbing can be checked; the lines are placeholders to be replaced by
    a real run.
    """

    examples: dict[str, list[str]] = field(default_factory=dict)
    quote_open: str = "「"
    quote_close: str = "」"
    name = "echo"

    def __post_init__(self) -> None:
        self._cursor: dict[str, int] = {}

    @classmethod
    def for_stage(cls, stage: Stage) -> "EchoBackend":
        examples = {cid: card.example_lines() for cid, card in stage.cards.items()}
        return cls(examples=examples, quote_open=stage.style.quote_open,
                   quote_close=stage.style.quote_close)

    def complete(self, request: StageRequest) -> str:
        if request.role == "director":
            return ""            # unparsed on purpose: the stage picks naturally
        if request.role == "choices":
            return ""            # the stage falls back to each option's intent
        if request.role == "narrator":
            return "……"
        lines = self.examples.get(request.speaker_id) or []
        index = self._cursor.get(request.speaker_id, 0)
        self._cursor[request.speaker_id] = index + 1
        if lines:
            return lines[index % len(lines)]
        return f"{self.quote_open}……{self.quote_close}"


# ----------------------------------------------------------------------
# Configuration and the pump


def config_path() -> Path:
    """``~/.world-gal-game/tavern.yaml`` (override with ``WGG_TAVERN_CONFIG``)."""
    override = os.environ.get("WGG_TAVERN_CONFIG")
    return Path(override).expanduser() if override else \
        Path.home() / ".world-gal-game" / "tavern.yaml"


def load_config(path: Path | None = None) -> dict[str, Any]:
    path = path or config_path()
    if not path.exists():
        return {}
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise BackendError(f"{path}: connection settings must be a mapping")
    return raw


def make_backend(name: str | None = None, *, model: str | None = None,
                 stage: Stage | None = None,
                 config: dict[str, Any] | None = None) -> Backend:
    """Build a backend by name from ``~/.world-gal-game/tavern.yaml`` + env.

    ``name`` falls back to ``WGG_TAVERN_BACKEND``, then the config file's
    ``backend``, then ``anthropic``.
    """
    config = load_config() if config is None else config
    name = (name or os.environ.get("WGG_TAVERN_BACKEND") or config.get("backend")
            or "anthropic").strip().lower()
    model = model or os.environ.get("WGG_TAVERN_MODEL")
    if name in ("anthropic", "claude"):
        section = config.get("anthropic") if isinstance(config.get("anthropic"), dict) \
            else {}
        return AnthropicBackend(
            model=model or str(section.get("model") or DEFAULT_MODEL),
            models=dict(section.get("models") or {}),
            efforts=dict(section.get("effort") or {}),
            max_tokens=int(section.get("max_tokens") or DEFAULT_MAX_TOKENS),
            fallbacks=bool(section.get("fallbacks", True)),
            use_model_hints=bool(section.get("use_model_hints", False)),
        )
    if name in ("openai", "openai-compatible", "compat"):
        section = config.get("openai") if isinstance(config.get("openai"), dict) else {}
        key_env = str(section.get("api_key_env") or "WGG_TAVERN_API_KEY")
        return OpenAICompatBackend(
            base_url=os.environ.get("WGG_TAVERN_BASE_URL") or str(
                section.get("base_url") or ""),
            model=model or str(section.get("model") or ""),
            api_key=os.environ.get(key_env, ""),
            models=dict(section.get("models") or {}),
            max_tokens=int(section.get("max_tokens") or 1024),
            temperature=section.get("temperature"),
            timeout=float(section.get("timeout") or 120.0),
            extra_body=dict(section.get("extra_body") or {}),
        )
    if name == "echo":
        return EchoBackend.for_stage(stage) if stage is not None else EchoBackend()
    raise BackendUnavailable(
        f"unknown backend '{name}' (known: anthropic, openai, echo)")


def run_stage(stage: Stage, backend: Backend, *,
              on_step: Callable[[StageRequest, str, dict[str, Any]], None] | None = None,
              max_requests: int | None = None,
              skip_refusals: bool = True) -> int:
    """Pump ``stage`` with ``backend`` until it finishes. Returns request count.

    Stops (without error) when the stage asks for a human line. A refusal is
    treated as an empty reply when ``skip_refusals`` is true, which lets the
    stage retake or fall back instead of losing the whole run.
    """
    count = 0
    while True:
        request = stage.next_request()
        if request is None or request.role == "human":
            return count
        if max_requests is not None and count >= max_requests:
            return count
        try:
            reply = backend.complete(request)
        except BackendRefusal:
            if not skip_refusals:
                raise
            reply = ""
        result = stage.submit(request.id, reply)
        count += 1
        if on_step is not None:
            on_step(request, reply, result)


def speakers_of(requests: Iterable[StageRequest]) -> list[str]:
    """Distinct speaker ids, in first-seen order (handy when mapping sub-agents)."""
    seen: list[str] = []
    for request in requests:
        key = request.speaker_id or request.role
        if key not in seen:
            seen.append(key)
    return seen

"""Talks to any OpenAI-compatible server.
Default: Ollama on this PC. Groq/OpenRouter/Gemini are optional.
"""

import os
import re
import time

MAX_SPOKEN = 3  # general questions sometimes need a third sentence

# Shared default client
_client = None

# Provider name -> (API base URL, environment variable containing API key)
PROVIDERS = {
    "groq": (
        "https://api.groq.com/openai/v1",
        "GROQ_API_KEY",
    ),
    "openrouter": (
        "https://openrouter.ai/api/v1",
        "OPENROUTER_API_KEY",
    ),
    "gemini": (
        "https://generativelanguage.googleapis.com/v1beta/openai/",
        "GEMINI_API_KEY",
    ),
    "ollama": (
        "http://localhost:11434/v1",
        None,
    ),
}

# Shared provider-client cache and model cooldowns
_clients = {}
_cooldown = {}


def client():
    """Create and reuse the default OpenAI-compatible client."""
    global _client

    if _client is None:
        from openai import OpenAI

        _client = OpenAI(
            base_url=os.environ.get(
                "LLM_BASE_URL",
                "http://localhost:11434/v1",
            ),
            api_key=os.environ.get("LLM_API_KEY", "ollama"),
            timeout=20,
            max_retries=1,
        )

    return _client


# Split sentences without splitting common doctor/title abbreviations.
_END = re.compile(
    r"(?<=[.!?])(?<!\bDr\.)(?<!\bMr\.)(?<!\bMs\.)(?<!\bMrs\.)\s+"
)

_LEAK = re.compile(
    r"\((?:note|waiting|no further|end of)[^)]*\)",
    re.I,
)


def shorten(text, max_sentences=MAX_SPOKEN, max_chars=420):
    """Clean and shorten a reply before speaking it."""
    text = re.sub(
        r"<think>.*?</think>",
        "",
        text or "",
        flags=re.S,
    )

    # Hide an unfinished reasoning block too.
    text = text.split("<think>")[0]

    text = _LEAK.sub("", text)

    # Clean ellipses and missing spaces.
    text = re.sub(
        r"([?!.])\s*(?:\.{2,}|\u2026+)",
        r"\1",
        text,
    )
    text = re.sub(r"\.{2,}|\u2026+", ".", text)
    text = re.sub(r"([?!])(?=[A-Za-z])", r"\1 ", text)
    text = re.sub(
        r"(?<=[a-z])\.(?=[A-Z])",
        ". ",
        text,
    ).strip()

    # Remove repeated sentences.
    seen = set()
    parts = []

    for part in _END.split(text):
        key = re.sub(r"\W+", "", part.lower())

        if key and key not in seen:
            seen.add(key)
            parts.append(part.strip())

    return " ".join(parts[:max_sentences])[:max_chars].strip()


def sentences(text, final=False):
    """Return completed sentences from a streaming response."""
    text = re.sub(
        r"<think>.*?</think>",
        "",
        text,
        flags=re.S,
    )

    # Hide reasoning that has started but not finished.
    text = text.split("<think>")[0]

    parts = _END.split(text.lstrip())

    # Hold the final fragment until more tokens arrive.
    if not final:
        parts = parts[:-1]

    return [
        shorten(part, 10, 400)
        for part in parts
        if part.strip()
    ]


def _chain():
    """Return the configured models in preferred fallback order.

    Example:
        LLM_CHAIN=groq:model-a,openrouter:model-b:free

    Without LLM_CHAIN, use LLM_MODEL with the default client.
    """
    raw = os.environ.get("LLM_CHAIN", "").strip()

    if not raw:
        return [
            (
                None,
                os.environ.get("LLM_MODEL", "qwen2.5:7b"),
            )
        ]

    slots = []

    for item in raw.split(","):
        item = item.strip()

        if not item or ":" not in item:
            continue

        # Split only once so model identifiers may contain colons.
        provider, model = item.split(":", 1)
        provider = provider.strip().lower()
        model = model.strip()

        if provider not in PROVIDERS or not model:
            continue

        key_env = PROVIDERS[provider][1]

        # Cloud providers need their corresponding API key.
        if key_env is not None and not os.environ.get(key_env):
            continue

        slots.append((provider, model))

    return slots


def _client_for(provider):
    """Return the cached client for a provider."""
    if provider is None:
        return client()

    if provider not in PROVIDERS:
        raise ValueError(f"Unknown LLM provider: {provider}")

    if provider not in _clients:
        from openai import OpenAI

        url, key_env = PROVIDERS[provider]

        api_key = (
            os.environ.get(key_env, "")
            if key_env
            else "ollama"
        )

        _clients[provider] = OpenAI(
            base_url=url,
            api_key=api_key,
            timeout=15,
            max_retries=0,
        )

    return _clients[provider]


def _should_switch(error):
    """Check whether a failed model should trigger a fallback."""
    import openai

    if isinstance(
        error,
        (
            openai.RateLimitError,
            openai.APIConnectionError,
            openai.APITimeoutError,
        ),
    ):
        return True

    if isinstance(error, openai.APIStatusError):
        if error.status_code >= 500:
            return True

    message = str(error).lower()

    return (
        "tool_use_failed" in message
        or "failed_generation" in message
    )


def _cool_down(error):
    """Determine how long to skip a failed model."""
    try:
        wait = float(
            error.response.headers.get("retry-after")
        )
    except (AttributeError, TypeError, ValueError):
        wait = 60

    return min(max(wait, 10), 300)


def _chat_once(slot, messages, tools, on_sentence, cancel=None):
    """Make one streaming request to a selected model."""
    provider, model = slot

    request = {
        "model": model,
        "messages": messages,
        "temperature": 0.3,
        "stream": True,
    }

    if tools:
        request["tools"] = tools

    stream = _client_for(provider).chat.completions.create(
        **request
    )

    text = ""
    calls = {}
    sent = 0

    def flush(final=False):
        nonlocal sent

        # Do not speak text if the model is making tool calls.
        if calls:
            return

        ready = sentences(text, final)

        for sentence in ready[sent:MAX_SPOKEN]:
            on_sentence(sentence)

        sent = max(
            sent,
            min(len(ready), MAX_SPOKEN),
        )

    cancelled = False

    for event in stream:
        # The caller interrupted: stop reading the stream at once.
        if cancel is not None and cancel.is_set():
            cancelled = True
            try:
                stream.close()
            except Exception:
                pass
            break

        if not event.choices:
            continue

        delta = event.choices[0].delta

        # Tool calls can arrive across multiple stream events.
        for tool_call in delta.tool_calls or []:
            index = (
                tool_call.index
                if tool_call.index is not None
                else len(calls)
            )

            call = calls.setdefault(
                index,
                {
                    "id": "",
                    "type": "function",
                    "function": {
                        "name": "",
                        "arguments": "",
                    },
                },
            )

            if tool_call.id:
                call["id"] = tool_call.id

            if tool_call.type:
                call["type"] = tool_call.type

            if tool_call.function:
                call["function"]["name"] += (
                    tool_call.function.name or ""
                )
                call["function"]["arguments"] += (
                    tool_call.function.arguments or ""
                )

        if delta.content:
            text += delta.content

            if on_sentence:
                flush()

    if cancelled:
        # No tool call may run after an interruption.
        return {"role": "assistant", "content": shorten(text) if not calls else ""}

    if on_sentence:
        flush(final=True)

    result = {
        "role": "assistant",
        "content": "" if calls else shorten(text),
    }

    if calls:
        result["tool_calls"] = [
            calls[index]
            for index in sorted(calls)
        ]

    return result


def _retry_wait(error):
    """Seconds to wait if this is a SHORT rate limit (Groq says 'try again in 5.4s'), else None."""
    import openai

    if not isinstance(error, openai.RateLimitError):
        return None
    try:
        wait = float(error.response.headers.get("retry-after"))
    except (AttributeError, TypeError, ValueError):
        found = re.search(r"try again in ([\d.]+)\s*s", str(error))
        wait = float(found.group(1)) if found else None
    return wait if wait is not None and wait <= 8 else None


def chat(messages, tools=None, on_sentence=None, cancel=None, info=None):
    """Stream a response, switching models if an eligible failure occurs
    before any sentence has been spoken.
    info (optional dict) is filled with the model that answered and whether a fallback model was used.
    """
    if cancel is not None and cancel.is_set():
        return {"role": "assistant", "content": ""}

    now = time.time()
    slots = _chain()

    if not slots:
        raise RuntimeError(
            "No LLM configured. Check LLM_CHAIN and the API keys."
        )

    ready = [
        slot
        for slot in slots
        if _cooldown.get(slot, 0) <= now
    ]

    # If every model is cooling down, try the configured chain anyway.
    if not ready:
        ready = slots

    spoke = []

    def speak(sentence):
        spoke.append(sentence)

        if on_sentence:
            on_sentence(sentence)

    last_error = None

    for slot in ready:
        try:
            result = _chat_once(
                slot,
                messages,
                tools,
                speak if on_sentence else None,
                cancel,
            )
            if info is not None:
                info["model"] = slot[1]
                info["fallback"] = bool(info.get("fallback")) or slot != slots[0]
            return result

        except Exception as error:
            # Do not switch after speech has already started.
            if spoke or not _should_switch(error):
                raise

            print(
                f"LLM {slot[1]} failed; "
                f"switching to the next model: {error}"
            )

            _cooldown[slot] = (
                time.time() + _cool_down(error)
            )

            last_error = error

    if last_error is not None:
        wait = _retry_wait(last_error)
        if wait is not None and not spoke and not (cancel is not None and cancel.is_set()):
            # a short rate limit: wait a moment and try the first model once more instead of failing the turn
            time.sleep(wait + 0.3)
            result = _chat_once(ready[0], messages, tools, speak if on_sentence else None, cancel)
            if info is not None:
                info["model"] = ready[0][1]
                info["fallback"] = bool(info.get("fallback")) or ready[0] != slots[0]
            return result
        raise last_error

    raise RuntimeError("No model could be selected.")
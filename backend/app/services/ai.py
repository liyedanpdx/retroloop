"""The one client for the OpenAI-compatible proxy, shared by #10 and #19.

`_docs/decisions.md`, "AI proxy, not direct OpenAI": calls go to
`settings.openai_base_url`, never to `api.openai.com`, so the model can be
swapped without touching code. "One model name, one setting": the model is
`settings.openai_model` and never a literal here.

This module is deliberately thin and prompt-free. `chat_json` takes a system and
a user message and hands back the parsed JSON object the model produced; the
prompt and the shape of that object belong to the caller, because #10 extracts
decisions and actions from a transcript and #19 suggests cluster names from
feedback cards, and those are two different asks with two different answers.

It is also the only module in `backend/app` that imports `httpx`
(`_docs/decisions.md`, "httpx is the runtime HTTP client"). Nothing above it
handles an `httpx` exception type: every way this can fail comes back out as one
of the three `ProxyError` subclasses below, whose `code` is exactly the value
that lands in `ai_suggestions["error"]`. That is what keeps a dead proxy a piece
of data rather than a 500.
"""

import json

import httpx

from app.config import settings
from app.models.retro import PROXY_TIMEOUT_SECONDS


class ProxyError(Exception):
    """A call to the proxy that produced no usable answer.

    `code` is the whole vocabulary the client ever sees. No upstream body, no
    status line and no exception text is carried in it on purpose — a proxy URL
    or an upstream stack trace is not something a retro participant should be
    able to read off a 200.
    """

    code = "upstream_error"


class ProxyTimeout(ProxyError):
    """It never answered, or could not be reached at all."""

    code = "timeout"


class ProxyUpstreamError(ProxyError):
    """It answered, with something other than a 200."""

    code = "upstream_error"


class ProxyMalformedResponse(ProxyError):
    """It answered 200 with something that is not a JSON object."""

    code = "malformed_response"


def _endpoint() -> str:
    return f"{settings.openai_base_url.rstrip('/')}/chat/completions"


async def chat_json(system: str, user: str) -> dict:
    """Ask the proxy for one JSON object, or raise a `ProxyError`.

    The model is asked for `json_object` output and the content is parsed here,
    so a caller only ever deals with a `dict` or with one of three errors it can
    map straight onto a stored error code.
    """
    payload = {
        "model": settings.openai_model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "response_format": {"type": "json_object"},
    }
    headers = {"Authorization": f"Bearer {settings.openai_api_key}"}

    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                _endpoint(),
                json=payload,
                headers=headers,
                timeout=PROXY_TIMEOUT_SECONDS,
            )
    except httpx.TimeoutException as exc:
        raise ProxyTimeout("the proxy did not answer in time") from exc
    except httpx.TransportError as exc:
        # A refused connection, DNS, TLS: nothing came back, which is the same
        # thing to a caller as nothing coming back in time.
        raise ProxyTimeout("the proxy could not be reached") from exc
    except Exception as exc:
        # Broad on purpose. `httpx` raises a handful of things that are not
        # `HTTPError` — an unusable URL among them — and the contract this
        # module owes its callers is three errors of ours and nothing of
        # `httpx`'s ever reaching them.
        raise ProxyUpstreamError("the proxy could not be called") from exc

    if response.status_code != httpx.codes.OK:
        raise ProxyUpstreamError("the proxy answered with an error")

    try:
        content = response.json()["choices"][0]["message"]["content"]
        parsed = json.loads(content)
    except Exception as exc:
        raise ProxyMalformedResponse("the proxy answered with unparseable content") from exc

    if not isinstance(parsed, dict):
        raise ProxyMalformedResponse("the proxy answered with something that is not an object")
    return parsed

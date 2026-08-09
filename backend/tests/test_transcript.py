"""Transcript paste, AI extraction, and confirming the drafts (#10).

Three conventions carried over from `tests/test_discussion.py`. Every helper
takes `headers=None` meaning *no* `Authorization` header at all, so the 401 cases
are the same call with one argument dropped. A mutation that should not have
happened is checked by reading the retro back out of the database, not by
trusting the response body that refused it. And the proxy is never real: the
autouse `ai_proxy` fixture in `conftest.py` replaces the one coroutine that
would dial out, and a test that forgets to configure it fails rather than making
a network call.

`status: "processing"` is not observable over HTTP here — under
`httpx.ASGITransport` a `BackgroundTask` finishes before the client sees the
response — so that one state is set up in process and read back through the API,
the way `tests/test_discussion.py` drives `create_topics` directly.
"""

import re
from datetime import datetime, timezone
from pathlib import Path

import httpx
import pytest

from app.config import Settings, settings
from app.models.retro import (
    EXTRACTION_STATUSES,
    MAX_TRANSCRIPT_CHARS,
    PROXY_TIMEOUT_SECONDS,
    SUGGESTION_STATES,
    Retrospective,
)
from app.services import ai
from app.services.transcript import processing_document
from tests.conftest import UnstubbedProxyCall

UNKNOWN_ID = "507f1f77bcf86cd799439011"
UNKNOWN_UUID = "3f2504e0-4f89-41d3-9a0c-0305e82c3301"
MALFORMED_ID = "abc"

ERROR_CODES = ("timeout", "upstream_error", "malformed_response")

APP_DIR = Path(ai.__file__).resolve().parents[1]
BACKEND_DIR = APP_DIR.parent

# Captured at import, before the autouse stub replaces the module attribute:
# the client's own tests are the one place the real coroutine has to run. It
# still reaches no network — `_fake_httpx` stands in for `httpx.AsyncClient`.
real_chat_json = ai.chat_json


# --- helpers -----------------------------------------------------------------


def _kwargs(headers):
    return {} if headers is None else {"headers": headers}


async def _post_transcript(client, retro_id, body, headers=None):
    return await client.post(
        f"/api/retros/{retro_id}/transcript", json=body, **_kwargs(headers)
    )


async def _get_suggestions(client, retro_id, headers=None):
    return await client.get(f"/api/retros/{retro_id}/suggestions", **_kwargs(headers))


async def _post_confirm(client, retro_id, body, headers=None):
    return await client.post(
        f"/api/retros/{retro_id}/suggestions/confirm", json=body, **_kwargs(headers)
    )


async def _stored(retro_id) -> Retrospective:
    """The retro as the database holds it, not as a response body claims."""
    return await Retrospective.get(retro_id)


def _answer(decisions=(), actions=()):
    """A proxy answer in the shape the prompt asks for."""
    return {"decisions": list(decisions), "actions": list(actions)}


async def _extract(client, retro_id, headers, text="alice: we agreed to ship."):
    """Paste a transcript against the stubbed answer and hand back what was stored."""
    resp = await _post_transcript(client, retro_id, {"text": text}, headers)
    assert resp.status_code == 202, resp.text
    return (await _stored(retro_id)).ai_suggestions


async def _register_member(client, headers, project_id, email, display_name, join=True):
    """A new account, optionally added to the project as a plain member."""
    resp = await client.post(
        "/api/auth/register",
        json={"email": email, "password": "secret123", "display_name": display_name},
    )
    assert resp.status_code == 201, resp.text
    if join:
        added = await client.post(
            f"/api/projects/{project_id}/members", json={"email": email}, headers=headers
        )
        assert added.status_code in (200, 201), added.text
    return resp.json()


class _FakeResponse:
    def __init__(self, status_code, payload=None, content=None):
        self.status_code = status_code
        self._payload = payload
        self._content = content

    def json(self):
        if self._content is not None:
            raise ValueError("not json")
        return self._payload


class _FakeAsyncClient:
    """Stands in for `httpx.AsyncClient` so `chat_json` can be driven offline."""

    record = {}
    response = None
    error = None

    def __init__(self, **kwargs):
        type(self).record["init"] = kwargs

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def post(self, url, **kwargs):
        type(self).record["url"] = url
        type(self).record.update(kwargs)
        if type(self).error is not None:
            raise type(self).error
        return type(self).response


def _fake_httpx(monkeypatch, response=None, error=None):
    _FakeAsyncClient.record = {}
    _FakeAsyncClient.response = response
    _FakeAsyncClient.error = error
    monkeypatch.setattr(ai.httpx, "AsyncClient", _FakeAsyncClient)
    return _FakeAsyncClient.record


def _proxy_body(content):
    return {"choices": [{"message": {"content": content}}]}


# --- configuration and dependencies ------------------------------------------


@pytest.mark.asyncio
async def test_httpx_is_a_runtime_dependency_named_once():
    """The one dependency approval on record, and it extends to nothing else."""
    source = (BACKEND_DIR / "pyproject.toml").read_text(encoding="utf-8")
    assert source.count("httpx") == 1

    runtime, _, optional = source.partition("[project.optional-dependencies]")
    assert "httpx" in runtime
    assert "httpx" not in optional


@pytest.mark.asyncio
async def test_only_the_ai_service_imports_httpx():
    """The proxy call is behind a service, so no router can reach the network."""
    importers = {
        path
        for path in APP_DIR.rglob("*.py")
        if "import httpx" in path.read_text(encoding="utf-8")
    }
    assert importers == {APP_DIR / "services" / "ai.py"}


@pytest.mark.asyncio
async def test_the_model_name_lives_only_in_the_settings():
    """One model name, one setting — never a literal in the extraction code."""
    named = {
        path
        for path in APP_DIR.rglob("*.py")
        if re.search("gpt-", path.read_text(encoding="utf-8"), re.IGNORECASE)
    }
    assert named == {APP_DIR / "config.py"}
    assert settings.openai_model


@pytest.mark.asyncio
async def test_the_env_example_carries_the_model_name():
    example = (BACKEND_DIR.parent / ".env.example").read_text(encoding="utf-8")
    assert "OPENAI_MODEL=gpt-5.4" in example


@pytest.mark.asyncio
async def test_a_dotenv_naming_a_model_starts_the_app(tmp_path):
    """`Settings` forbids extra keys, so this is a startup failure if the field is missing."""
    env_file = tmp_path / ".env"
    env_file.write_text(
        "MONGO_URL=mongodb://localhost:27017\nOPENAI_MODEL=some-other-model\n",
        encoding="utf-8",
    )

    loaded = Settings(_env_file=str(env_file))
    assert loaded.openai_model == "some-other-model"


@pytest.mark.asyncio
async def test_the_extraction_constants_are_named_in_the_model_module():
    assert EXTRACTION_STATUSES == ("idle", "processing", "ready", "failed")
    assert SUGGESTION_STATES == ("pending", "confirmed", "rejected")
    assert MAX_TRANSCRIPT_CHARS == 100_000
    assert PROXY_TIMEOUT_SECONDS == 60


@pytest.mark.asyncio
async def test_the_status_strings_are_not_written_out_in_handlers():
    """Only the constants and the schemas' Literals spell these out.

    `"pending"` is skipped: it is also a topic status, spelled out in #9's own
    `Literal`, and this is a rule about where #10's strings live.
    """
    allowed = {APP_DIR / "models" / "retro.py", APP_DIR / "schemas" / "transcript.py"}
    for value in [v for v in EXTRACTION_STATUSES + SUGGESTION_STATES if v != "pending"]:
        spelled = {
            path
            for path in APP_DIR.rglob("*.py")
            if f'"{value}"' in path.read_text(encoding="utf-8")
        }
        assert spelled <= allowed, value


# --- the shared proxy client -------------------------------------------------


@pytest.mark.asyncio
async def test_the_client_calls_the_configured_proxy_with_the_configured_model(monkeypatch):
    record = _fake_httpx(
        monkeypatch, response=_FakeResponse(200, _proxy_body('{"decisions": [], "actions": []}'))
    )

    answer = await real_chat_json("be brief", "a transcript")
    assert answer == {"decisions": [], "actions": []}

    assert record["url"] == f"{settings.openai_base_url.rstrip('/')}/chat/completions"
    assert record["json"]["model"] == settings.openai_model
    assert record["json"]["messages"][0]["content"] == "be brief"
    assert record["json"]["messages"][1]["content"] == "a transcript"
    assert record["headers"]["Authorization"] == f"Bearer {settings.openai_api_key}"
    assert record["timeout"] == PROXY_TIMEOUT_SECONDS


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error",
    [
        httpx.ReadTimeout("too slow"),
        httpx.ConnectTimeout("too slow"),
        httpx.ConnectError("nothing listening"),
    ],
)
async def test_a_slow_or_unreachable_proxy_is_a_timeout(monkeypatch, error):
    _fake_httpx(monkeypatch, error=error)

    with pytest.raises(ai.ProxyTimeout) as raised:
        await real_chat_json("s", "u")
    assert raised.value.code == "timeout"


@pytest.mark.asyncio
async def test_a_non_200_from_the_proxy_is_an_upstream_error(monkeypatch):
    _fake_httpx(monkeypatch, response=_FakeResponse(500, {"detail": "boom"}))

    with pytest.raises(ai.ProxyUpstreamError) as raised:
        await real_chat_json("s", "u")
    assert raised.value.code == "upstream_error"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "response",
    [
        _FakeResponse(200, content="not json at all"),
        _FakeResponse(200, {"choices": []}),
        _FakeResponse(200, _proxy_body("this is prose, not json")),
        _FakeResponse(200, _proxy_body("[1, 2, 3]")),
    ],
)
async def test_an_unreadable_200_is_a_malformed_response(monkeypatch, response):
    _fake_httpx(monkeypatch, response=response)

    with pytest.raises(ai.ProxyMalformedResponse) as raised:
        await real_chat_json("s", "u")
    assert raised.value.code == "malformed_response"


@pytest.mark.asyncio
async def test_no_httpx_exception_escapes_the_client(monkeypatch):
    """Callers handle three errors of ours, never anything of httpx's."""
    _fake_httpx(monkeypatch, error=httpx.InvalidURL("nonsense"))

    with pytest.raises(ai.ProxyError):
        await real_chat_json("s", "u")


@pytest.mark.asyncio
async def test_the_autouse_proxy_stub_bites(ai_proxy):
    """A test that forgot to say what the proxy answers fails, it does not dial out."""
    from app.services import transcript as transcript_service

    with pytest.raises(UnstubbedProxyCall):
        await transcript_service.chat_json("s", "u")


# --- POST /api/retros/{id}/transcript ----------------------------------------


@pytest.mark.asyncio
async def test_the_facilitator_pastes_a_transcript(
    client, auth_headers, discussion_retro, ai_proxy
):
    retro = discussion_retro["retro"]
    ai_proxy.returns(_answer())

    resp = await _post_transcript(client, retro["id"], {"text": "alice: hello"}, auth_headers)
    assert resp.status_code == 202, resp.text
    assert resp.json() == {"status": "processing"}
    assert ai_proxy.calls, "the extraction actually asked the proxy"


@pytest.mark.asyncio
async def test_the_transcript_is_stored_stripped(
    client, auth_headers, discussion_retro, ai_proxy
):
    retro = discussion_retro["retro"]
    ai_proxy.returns(_answer())

    resp = await _post_transcript(
        client, retro["id"], {"text": "  alice: hello  \n"}, auth_headers
    )
    assert resp.status_code == 202, resp.text
    assert (await _stored(retro["id"])).transcript == "alice: hello"


@pytest.mark.asyncio
async def test_the_background_task_finishes_ready_with_a_completion_time(
    client, auth_headers, discussion_retro, ai_proxy
):
    retro = discussion_retro["retro"]
    ai_proxy.returns(_answer([{"text": "Ship it"}]))

    stored = await _extract(client, retro["id"], auth_headers)
    assert stored["status"] == "ready"
    assert stored["completed_at"] is not None
    assert stored["requested_at"] is not None


@pytest.mark.asyncio
@pytest.mark.parametrize("body", [{"text": ""}, {"text": "   "}, {"text": "\n\t "}, {}])
async def test_a_blank_or_missing_transcript_is_rejected(
    client, auth_headers, discussion_retro, body
):
    retro = discussion_retro["retro"]

    resp = await _post_transcript(client, retro["id"], body, auth_headers)
    assert resp.status_code == 422, resp.text

    stored = await _stored(retro["id"])
    assert stored.transcript is None and stored.ai_suggestions is None


@pytest.mark.asyncio
async def test_a_transcript_over_the_cap_is_rejected(client, auth_headers, discussion_retro):
    retro = discussion_retro["retro"]

    resp = await _post_transcript(
        client, retro["id"], {"text": "x" * (MAX_TRANSCRIPT_CHARS + 1)}, auth_headers
    )
    assert resp.status_code == 422, "a body-shape rule, so 422 and not 413"

    stored = await _stored(retro["id"])
    assert stored.transcript is None and stored.ai_suggestions is None


@pytest.mark.asyncio
async def test_a_transcript_exactly_at_the_cap_is_accepted(
    client, auth_headers, discussion_retro, ai_proxy
):
    retro = discussion_retro["retro"]
    ai_proxy.returns(_answer())

    resp = await _post_transcript(
        client, retro["id"], {"text": "x" * MAX_TRANSCRIPT_CHARS}, auth_headers
    )
    assert resp.status_code == 202, resp.text
    assert len((await _stored(retro["id"])).transcript) == MAX_TRANSCRIPT_CHARS


@pytest.mark.asyncio
async def test_a_second_paste_discards_every_earlier_suggestion(
    client, auth_headers, transcript_retro, ai_proxy
):
    """Pending and rejected alike — two independent LLM calls share no identity."""
    retro = transcript_retro["retro"]
    rejected = transcript_retro["unmatched_action"]["id"]
    discarded = {transcript_retro["decision"]["id"], transcript_retro["action"]["id"], rejected}

    refused = await _post_confirm(client, retro["id"], {"rejected": [rejected]}, auth_headers)
    assert refused.status_code == 200, refused.text

    ai_proxy.returns(_answer([{"text": "Cap work in progress"}]))
    stored = await _extract(client, retro["id"], auth_headers, text="a second meeting")

    assert stored["status"] == "ready"
    assert [d["text"] for d in stored["decisions"]] == ["Cap work in progress"]
    assert stored["actions"] == []
    assert not discarded & {item["id"] for item in stored["decisions"] + stored["actions"]}


@pytest.mark.asyncio
async def test_a_second_paste_leaves_confirmed_rows_alone(
    client, auth_headers, transcript_retro, ai_proxy
):
    retro = transcript_retro["retro"]
    confirmed = await _post_confirm(
        client,
        retro["id"],
        {
            "decisions": [{"id": transcript_retro["decision"]["id"]}],
            "actions": [{"id": transcript_retro["action"]["id"]}],
        },
        auth_headers,
    )
    assert confirmed.status_code == 200, confirmed.text
    decision_id = confirmed.json()["decisions"][0]["created_id"]
    action_id = confirmed.json()["actions"][0]["created_id"]

    ai_proxy.returns(_answer([{"text": "Something else"}]))
    await _extract(client, retro["id"], auth_headers, text="a second meeting")

    stored = await _stored(retro["id"])
    assert [d.id for d in stored.decisions] == [decision_id]
    assert [a.id for a in stored.actions] == [action_id]


@pytest.mark.asyncio
async def test_a_paste_while_processing_is_refused(client, auth_headers, discussion_retro):
    """The only conflict in this issue, and it is what makes discarding safe."""
    retro = discussion_retro["retro"]
    document = await _stored(retro["id"])
    document.transcript = "the first paste"
    document.ai_suggestions = processing_document()
    await document.save()

    resp = await _post_transcript(client, retro["id"], {"text": "the second"}, auth_headers)
    assert resp.status_code == 409, resp.text

    stored = await _stored(retro["id"])
    assert stored.transcript == "the first paste"
    assert stored.ai_suggestions == document.ai_suggestions


@pytest.mark.asyncio
async def test_an_unexpected_error_still_leaves_a_terminal_status(
    client, auth_headers, discussion_retro, ai_proxy
):
    """`processing` must not stick: a paste is a 409 while it is set."""
    retro = discussion_retro["retro"]
    ai_proxy.raises(RuntimeError("something nobody predicted"))

    stored = await _extract(client, retro["id"], auth_headers)
    assert stored["status"] == "failed"
    assert stored["error"] in ERROR_CODES


# --- the extraction itself ---------------------------------------------------


@pytest.mark.asyncio
async def test_one_call_fills_both_lists(client, auth_headers, discussion_retro, ai_proxy):
    retro = discussion_retro["retro"]
    ai_proxy.returns(
        _answer(
            [{"text": "Ship behind a flag"}],
            [{"description": "Write the migration note", "owner": None, "due_date": None}],
        )
    )

    stored = await _extract(client, retro["id"], auth_headers)
    assert len(ai_proxy.calls) == 1, "decisions and actions come out of one call"
    assert stored["status"] == "ready"

    decision = stored["decisions"][0]
    assert set(decision) == {"id", "text", "state", "created_id"}
    assert decision["text"] == "Ship behind a flag"
    assert decision["state"] == "pending" and decision["created_id"] is None
    assert len(decision["id"]) == 36

    action = stored["actions"][0]
    assert set(action) == {
        "id",
        "description",
        "owner_name",
        "owner_id",
        "due_date",
        "state",
        "created_id",
    }
    assert action["description"] == "Write the migration note"
    assert action["state"] == "pending" and action["created_id"] is None


@pytest.mark.asyncio
async def test_a_meeting_that_decided_nothing_is_ready_and_empty(
    client, auth_headers, discussion_retro, ai_proxy
):
    retro = discussion_retro["retro"]
    ai_proxy.returns(_answer())

    stored = await _extract(client, retro["id"], auth_headers)
    assert stored["status"] == "ready" and stored["error"] is None
    assert stored["decisions"] == [] and stored["actions"] == []


@pytest.mark.asyncio
async def test_extraction_writes_no_decision_or_action_rows(
    client, auth_headers, discussion_retro, ai_proxy
):
    """Suggestions are always drafts — nothing is saved without a human saying yes."""
    retro = discussion_retro["retro"]
    ai_proxy.returns(
        _answer([{"text": "Ship it"}], [{"description": "Do the thing", "owner": "Bob"}])
    )

    await _extract(client, retro["id"], auth_headers)

    stored = await _stored(retro["id"])
    assert stored.decisions == [] and stored.actions == []


@pytest.mark.asyncio
@pytest.mark.parametrize("raw", ["Bob", "  bob  ", "BOB"])
async def test_a_normalised_display_name_matches_one_member(
    client, auth_headers, discussion_retro, second_user, ai_proxy, raw
):
    """strip, collapse, casefold — both sides, the same way."""
    retro = discussion_retro["retro"]
    ai_proxy.returns(_answer(actions=[{"description": "Write the runbook", "owner": raw}]))

    stored = await _extract(client, retro["id"], auth_headers)
    action = stored["actions"][0]
    assert action["owner_id"] == second_user["id"]
    assert action["owner_name"] == raw, "the raw string is stored exactly as extracted"


@pytest.mark.asyncio
@pytest.mark.parametrize("raw", ["sam chen", "  Sam   Chen ", "SAM CHEN"])
async def test_internal_whitespace_and_case_still_match(
    client, auth_headers, discussion_retro, project_with_member, ai_proxy, raw
):
    retro = discussion_retro["retro"]
    sam = await _register_member(
        client, auth_headers, project_with_member["id"], "sam@example.com", "Sam Chen"
    )
    ai_proxy.returns(_answer(actions=[{"description": "Write the runbook", "owner": raw}]))

    stored = await _extract(client, retro["id"], auth_headers)
    assert stored["actions"][0]["owner_id"] == sam["id"]


@pytest.mark.asyncio
async def test_a_first_name_does_not_match_a_full_name(
    client, auth_headers, discussion_retro, project_with_member, ai_proxy
):
    """No prefix matching: a confidently wrong owner is worse than none."""
    retro = discussion_retro["retro"]
    await _register_member(
        client, auth_headers, project_with_member["id"], "sam@example.com", "Sam Chen"
    )
    ai_proxy.returns(_answer(actions=[{"description": "Write the runbook", "owner": "Sam"}]))

    stored = await _extract(client, retro["id"], auth_headers)
    assert stored["actions"][0]["owner_id"] is None
    assert stored["actions"][0]["owner_name"] == "Sam"


@pytest.mark.asyncio
async def test_two_members_with_the_same_name_are_unmatched(
    client, auth_headers, discussion_retro, project_with_member, ai_proxy
):
    retro = discussion_retro["retro"]
    for email in ("sam@example.com", "sam.chen@example.com"):
        await _register_member(client, auth_headers, project_with_member["id"], email, "Sam Chen")
    ai_proxy.returns(_answer(actions=[{"description": "Write the runbook", "owner": "Sam Chen"}]))

    stored = await _extract(client, retro["id"], auth_headers)
    assert stored["actions"][0]["owner_id"] is None, "ambiguous is unmatched"


@pytest.mark.asyncio
async def test_a_name_matching_a_non_member_is_unmatched(
    client, auth_headers, discussion_retro, project_with_member, ai_proxy
):
    retro = discussion_retro["retro"]
    await _register_member(
        client, auth_headers, project_with_member["id"], "dana@example.com", "Dana Wu", join=False
    )
    ai_proxy.returns(_answer(actions=[{"description": "Book the room", "owner": "Dana Wu"}]))

    stored = await _extract(client, retro["id"], auth_headers)
    assert stored["actions"][0]["owner_id"] is None
    assert stored["actions"][0]["owner_name"] == "Dana Wu"


@pytest.mark.asyncio
async def test_an_action_with_no_owner_carries_no_name(
    client, auth_headers, discussion_retro, ai_proxy
):
    retro = discussion_retro["retro"]
    ai_proxy.returns(_answer(actions=[{"description": "Book the room", "owner": None}]))

    stored = await _extract(client, retro["id"], auth_headers)
    assert stored["actions"][0]["owner_name"] is None
    assert stored["actions"][0]["owner_id"] is None


@pytest.mark.asyncio
async def test_a_proxy_timeout_is_stored_as_a_failure(
    client, auth_headers, discussion_retro, ai_proxy
):
    retro = discussion_retro["retro"]
    ai_proxy.raises(ai.ProxyTimeout("gone"))

    stored = await _extract(client, retro["id"], auth_headers)
    assert stored["status"] == "failed" and stored["error"] == "timeout"
    assert stored["decisions"] == [] and stored["actions"] == []


@pytest.mark.asyncio
async def test_a_proxy_500_is_stored_as_an_upstream_error(
    client, auth_headers, discussion_retro, ai_proxy
):
    retro = discussion_retro["retro"]
    ai_proxy.raises(ai.ProxyUpstreamError("the upstream said something private"))

    stored = await _extract(client, retro["id"], auth_headers)
    assert stored["status"] == "failed" and stored["error"] == "upstream_error"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "answer",
    [
        {"decisions": []},
        {"actions": []},
        {"decisions": "not a list", "actions": []},
        {"decisions": [], "actions": [{"owner": "Bob"}]},
        {"decisions": [{"text": "   "}], "actions": []},
        {"decisions": ["a bare string"], "actions": []},
    ],
)
async def test_an_answer_of_the_wrong_shape_is_malformed(
    client, auth_headers, discussion_retro, ai_proxy, answer
):
    retro = discussion_retro["retro"]
    ai_proxy.returns(answer)

    stored = await _extract(client, retro["id"], auth_headers)
    assert stored["status"] == "failed" and stored["error"] == "malformed_response"


@pytest.mark.asyncio
async def test_a_failure_keeps_the_transcript_and_a_retry_can_succeed(
    client, auth_headers, discussion_retro, ai_proxy
):
    """Recovery is pasting again — the only recovery there is until #27."""
    retro = discussion_retro["retro"]
    ai_proxy.raises(ai.ProxyTimeout("gone"))

    await _extract(client, retro["id"], auth_headers, text="the whole meeting")
    assert (await _stored(retro["id"])).transcript == "the whole meeting"

    ai_proxy.returns(_answer([{"text": "Ship it"}]))
    stored = await _extract(client, retro["id"], auth_headers, text="the whole meeting")
    assert stored["status"] == "ready"
    assert [d["text"] for d in stored["decisions"]] == ["Ship it"]


@pytest.mark.asyncio
async def test_a_failure_leaks_nothing_and_is_not_a_500(
    client, auth_headers, discussion_retro, ai_proxy
):
    retro = discussion_retro["retro"]
    ai_proxy.raises(ai.ProxyUpstreamError("http://proxy.internal said 502 Bad Gateway"))

    paste = await _post_transcript(client, retro["id"], {"text": "alice: hi"}, auth_headers)
    assert paste.status_code == 202, "the proxy has not been touched when this returns"

    read = await _get_suggestions(client, retro["id"], auth_headers)
    assert read.status_code == 200, read.text
    assert read.json()["error"] in ERROR_CODES
    assert "proxy.internal" not in read.text and "502" not in read.text


# --- GET /api/retros/{id}/suggestions ----------------------------------------


@pytest.mark.asyncio
async def test_suggestions_before_any_transcript_are_idle(
    client, auth_headers, discussion_retro
):
    """200 with `idle`, never 404 — on this endpoint 404 means the retro is gone."""
    retro = discussion_retro["retro"]

    resp = await _get_suggestions(client, retro["id"], auth_headers)
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"status": "idle", "error": None, "decisions": [], "actions": []}


@pytest.mark.asyncio
async def test_suggestions_while_processing(client, auth_headers, discussion_retro):
    """Set up in process: a background task finishes before the client sees the 202."""
    retro = discussion_retro["retro"]
    document = await _stored(retro["id"])
    document.ai_suggestions = processing_document()
    await document.save()

    resp = await _get_suggestions(client, retro["id"], auth_headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "processing" and body["error"] is None
    assert body["decisions"] == [] and body["actions"] == []


@pytest.mark.asyncio
async def test_suggestions_after_a_successful_extraction(
    client, auth_headers, transcript_retro
):
    retro = transcript_retro["retro"]

    resp = await _get_suggestions(client, retro["id"], auth_headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "ready" and body["error"] is None
    assert len(body["decisions"]) == 1 and len(body["actions"]) == 2
    for item in body["decisions"] + body["actions"]:
        assert item["id"] and item["state"] == "pending" and item["created_id"] is None


@pytest.mark.asyncio
async def test_suggestions_after_a_failure(client, auth_headers, discussion_retro, ai_proxy):
    retro = discussion_retro["retro"]
    ai_proxy.raises(ai.ProxyTimeout("gone"))
    await _extract(client, retro["id"], auth_headers)

    resp = await _get_suggestions(client, retro["id"], auth_headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "failed" and body["error"] == "timeout"
    assert body["decisions"] == [] and body["actions"] == []


@pytest.mark.asyncio
async def test_the_poll_payload_is_what_the_retro_carries(
    client, auth_headers, transcript_retro
):
    """A convenience for #17's poller, not a second source of truth."""
    retro = transcript_retro["retro"]

    poll = await _get_suggestions(client, retro["id"], auth_headers)
    whole = await client.get(f"/api/retros/{retro['id']}", headers=auth_headers)
    assert whole.status_code == 200, whole.text
    assert poll.json() == whole.json()["ai_suggestions"]


# --- POST /api/retros/{id}/suggestions/confirm -------------------------------


@pytest.mark.asyncio
async def test_confirming_a_decision_creates_a_confirmed_row(
    client, auth_headers, transcript_retro
):
    retro = transcript_retro["retro"]
    suggestion = transcript_retro["decision"]

    resp = await _post_confirm(
        client, retro["id"], {"decisions": [{"id": suggestion["id"]}]}, auth_headers
    )
    assert resp.status_code == 200, resp.text
    created_id = resp.json()["decisions"][0]["created_id"]

    stored = await _stored(retro["id"])
    assert len(stored.decisions) == 1
    decision = stored.decisions[0]
    assert decision.id == created_id and decision.id != suggestion["id"]
    assert decision.topic_id is None and decision.is_confirmed is True
    assert decision.text == suggestion["text"]

    marked = stored.ai_suggestions["decisions"][0]
    assert marked["state"] == "confirmed" and marked["created_id"] == created_id


@pytest.mark.asyncio
async def test_confirming_an_action_creates_an_open_row_carrying_the_owner_name(
    client, auth_headers, transcript_retro, second_user
):
    retro = transcript_retro["retro"]
    suggestion = transcript_retro["action"]

    resp = await _post_confirm(
        client, retro["id"], {"actions": [{"id": suggestion["id"]}]}, auth_headers
    )
    assert resp.status_code == 200, resp.text

    stored = await _stored(retro["id"])
    action = stored.actions[0]
    assert action.topic_id is None and action.status == "open"
    assert action.description == suggestion["description"]
    assert action.owner_name == "Bob"
    assert str(action.owner_id) == second_user["id"]


@pytest.mark.asyncio
async def test_confirmed_items_read_back_off_the_retro(client, auth_headers, transcript_retro):
    """The field QA saw as always null on #9 is non-null on a confirmed action."""
    retro = transcript_retro["retro"]

    resp = await _post_confirm(
        client,
        retro["id"],
        {
            "decisions": [{"id": transcript_retro["decision"]["id"]}],
            "actions": [{"id": transcript_retro["action"]["id"]}],
        },
        auth_headers,
    )
    assert resp.status_code == 200, resp.text

    whole = await client.get(f"/api/retros/{retro['id']}", headers=auth_headers)
    body = whole.json()
    assert body["decisions"][0]["is_confirmed"] is True
    assert body["actions"][0]["owner_name"] == "Bob"


@pytest.mark.asyncio
async def test_an_unmatched_owner_confirms_unassigned_but_named(
    client, auth_headers, transcript_retro
):
    """The facilitator is not forced to resolve a name that is not on the project."""
    retro = transcript_retro["retro"]
    suggestion = transcript_retro["unmatched_action"]
    assert suggestion["owner_id"] is None

    resp = await _post_confirm(
        client, retro["id"], {"actions": [{"id": suggestion["id"]}]}, auth_headers
    )
    assert resp.status_code == 200, resp.text

    action = (await _stored(retro["id"])).actions[0]
    assert action.owner_id is None
    assert action.owner_name == "Dana Wu"


@pytest.mark.asyncio
async def test_the_facilitator_overrides_the_owner(
    client, auth_headers, transcript_retro, registered_user, second_user
):
    retro = transcript_retro["retro"]

    resp = await _post_confirm(
        client,
        retro["id"],
        {
            "actions": [
                {"id": transcript_retro["action"]["id"], "owner_id": registered_user["id"]},
                {"id": transcript_retro["unmatched_action"]["id"], "owner_id": second_user["id"]},
            ]
        },
        auth_headers,
    )
    assert resp.status_code == 200, resp.text

    stored = await _stored(retro["id"])
    owners = {a.description: (str(a.owner_id), a.owner_name) for a in stored.actions}
    assert owners["Write the runbook"] == (registered_user["id"], "Bob")
    assert owners["Book the room"] == (second_user["id"], "Dana Wu")


@pytest.mark.asyncio
async def test_an_owner_who_is_not_a_member_writes_nothing(
    client, auth_headers, transcript_retro
):
    retro = transcript_retro["retro"]

    resp = await _post_confirm(
        client,
        retro["id"],
        {
            "decisions": [{"id": transcript_retro["decision"]["id"]}],
            "actions": [{"id": transcript_retro["action"]["id"], "owner_id": UNKNOWN_ID}],
        },
        auth_headers,
    )
    assert resp.status_code == 404, resp.text

    stored = await _stored(retro["id"])
    assert stored.decisions == [] and stored.actions == []


@pytest.mark.asyncio
async def test_a_malformed_owner_id_is_a_body_shape_error(
    client, auth_headers, transcript_retro
):
    retro = transcript_retro["retro"]

    resp = await _post_confirm(
        client,
        retro["id"],
        {"actions": [{"id": transcript_retro["action"]["id"], "owner_id": "not-an-id"}]},
        auth_headers,
    )
    assert resp.status_code == 422, resp.text
    assert (await _stored(retro["id"])).actions == []


@pytest.mark.asyncio
async def test_text_and_description_overrides_are_stored(
    client, auth_headers, transcript_retro
):
    retro = transcript_retro["retro"]

    resp = await _post_confirm(
        client,
        retro["id"],
        {
            "decisions": [
                {"id": transcript_retro["decision"]["id"], "text": "  Ship behind a flag  "}
            ],
            "actions": [
                {"id": transcript_retro["action"]["id"], "description": "Write the runbook first"}
            ],
        },
        auth_headers,
    )
    assert resp.status_code == 200, resp.text

    stored = await _stored(retro["id"])
    assert stored.decisions[0].text == "Ship behind a flag"
    assert stored.actions[0].description == "Write the runbook first"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "body_key, override",
    [("decisions", {"text": "   "}), ("actions", {"description": ""})],
)
async def test_a_blank_override_is_rejected_and_writes_nothing(
    client, auth_headers, transcript_retro, body_key, override
):
    """The blank one is sent alongside a good one: all or nothing, so neither lands."""
    retro = transcript_retro["retro"]
    item = {"id": transcript_retro["decision" if body_key == "decisions" else "action"]["id"]}
    item.update(override)
    body = {body_key: [item]}
    if body_key == "decisions":
        body["actions"] = [{"id": transcript_retro["action"]["id"]}]
    else:
        body["decisions"] = [{"id": transcript_retro["decision"]["id"]}]

    resp = await _post_confirm(client, retro["id"], body, auth_headers)
    assert resp.status_code == 422, resp.text

    stored = await _stored(retro["id"])
    assert stored.decisions == [] and stored.actions == []


@pytest.mark.asyncio
async def test_a_due_date_is_set_and_cleared_at_confirm_time(
    client, auth_headers, transcript_retro, ai_proxy
):
    """A past date is accepted, exactly as #9 accepts one."""
    retro = transcript_retro["retro"]

    resp = await _post_confirm(
        client,
        retro["id"],
        {
            "actions": [
                {"id": transcript_retro["action"]["id"], "due_date": "2020-01-02T00:00:00Z"},
                {"id": transcript_retro["unmatched_action"]["id"], "due_date": None},
            ]
        },
        auth_headers,
    )
    assert resp.status_code == 200, resp.text

    stored = await _stored(retro["id"])
    dates = {a.description: a.due_date for a in stored.actions}
    assert dates["Write the runbook"].replace(tzinfo=timezone.utc) == datetime(
        2020, 1, 2, tzinfo=timezone.utc
    )
    assert dates["Book the room"] is None


@pytest.mark.asyncio
async def test_an_unparseable_due_date_is_rejected(client, auth_headers, transcript_retro):
    retro = transcript_retro["retro"]

    resp = await _post_confirm(
        client,
        retro["id"],
        {"actions": [{"id": transcript_retro["action"]["id"], "due_date": "next tuesday"}]},
        auth_headers,
    )
    assert resp.status_code == 422, resp.text
    assert (await _stored(retro["id"])).actions == []


@pytest.mark.asyncio
async def test_rejecting_a_suggestion_creates_nothing(client, auth_headers, transcript_retro):
    retro = transcript_retro["retro"]
    suggestion = transcript_retro["decision"]

    resp = await _post_confirm(
        client, retro["id"], {"rejected": [suggestion["id"]]}, auth_headers
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["rejected"] == [suggestion["id"]]

    stored = await _stored(retro["id"])
    assert stored.decisions == []
    marked = stored.ai_suggestions["decisions"][0]
    assert marked["state"] == "rejected" and marked["created_id"] is None


@pytest.mark.asyncio
async def test_decisions_actions_and_rejections_in_one_body(
    client, auth_headers, transcript_retro
):
    retro = transcript_retro["retro"]

    resp = await _post_confirm(
        client,
        retro["id"],
        {
            "decisions": [{"id": transcript_retro["decision"]["id"]}],
            "actions": [{"id": transcript_retro["action"]["id"]}],
            "rejected": [transcript_retro["unmatched_action"]["id"]],
        },
        auth_headers,
    )
    assert resp.status_code == 200, resp.text

    stored = await _stored(retro["id"])
    assert len(stored.decisions) == 1 and len(stored.actions) == 1
    states = [item["state"] for item in stored.ai_suggestions["actions"]]
    assert states == ["confirmed", "rejected"]


@pytest.mark.asyncio
async def test_an_empty_confirm_body_changes_nothing(client, auth_headers, transcript_retro):
    retro = transcript_retro["retro"]
    before = (await _stored(retro["id"])).ai_suggestions

    resp = await _post_confirm(client, retro["id"], {}, auth_headers)
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"decisions": [], "actions": [], "rejected": []}

    stored = await _stored(retro["id"])
    assert stored.decisions == [] and stored.actions == []
    assert stored.ai_suggestions == before


@pytest.mark.asyncio
async def test_confirming_the_same_suggestion_twice_writes_one_row(
    client, auth_headers, transcript_retro
):
    """A 200 no-op returning the first `created_id`, not a conflict and not a 400."""
    retro = transcript_retro["retro"]
    body = {"decisions": [{"id": transcript_retro["decision"]["id"]}]}

    first = await _post_confirm(client, retro["id"], body, auth_headers)
    assert first.status_code == 200, first.text
    created_id = first.json()["decisions"][0]["created_id"]
    assert len((await _stored(retro["id"])).decisions) == 1

    second = await _post_confirm(client, retro["id"], body, auth_headers)
    assert second.status_code == 200, second.text
    assert second.json()["decisions"][0]["created_id"] == created_id

    stored = await _stored(retro["id"])
    assert len(stored.decisions) == 1
    assert stored.ai_suggestions["decisions"][0]["created_id"] == created_id


@pytest.mark.asyncio
async def test_re_confirming_with_a_different_text_does_not_rewrite_the_row(
    client, auth_headers, transcript_retro
):
    """Rewording a confirmed decision is #9's PATCH, not a second confirm."""
    retro = transcript_retro["retro"]
    suggestion_id = transcript_retro["decision"]["id"]

    await _post_confirm(client, retro["id"], {"decisions": [{"id": suggestion_id}]}, auth_headers)
    again = await _post_confirm(
        client, retro["id"], {"decisions": [{"id": suggestion_id, "text": "Something else"}]},
        auth_headers,
    )
    assert again.status_code == 200, again.text

    stored = await _stored(retro["id"])
    assert len(stored.decisions) == 1
    assert stored.decisions[0].text == transcript_retro["decision"]["text"]


@pytest.mark.asyncio
async def test_a_rejected_suggestion_can_still_be_confirmed(
    client, auth_headers, transcript_retro
):
    """Rejection is a review note, not a tombstone."""
    retro = transcript_retro["retro"]
    suggestion_id = transcript_retro["decision"]["id"]

    rejected = await _post_confirm(
        client, retro["id"], {"rejected": [suggestion_id]}, auth_headers
    )
    assert rejected.status_code == 200, rejected.text

    confirmed = await _post_confirm(
        client, retro["id"], {"decisions": [{"id": suggestion_id}]}, auth_headers
    )
    assert confirmed.status_code == 200, confirmed.text

    stored = await _stored(retro["id"])
    assert len(stored.decisions) == 1
    assert stored.ai_suggestions["decisions"][0]["state"] == "confirmed"


@pytest.mark.asyncio
@pytest.mark.parametrize("suggestion_id", [UNKNOWN_UUID, MALFORMED_ID])
async def test_an_unknown_suggestion_id_is_a_404(
    client, auth_headers, transcript_retro, suggestion_id
):
    retro = transcript_retro["retro"]

    resp = await _post_confirm(
        client, retro["id"], {"decisions": [{"id": suggestion_id}]}, auth_headers
    )
    assert resp.status_code == 404, resp.text
    assert (await _stored(retro["id"])).decisions == []


@pytest.mark.asyncio
async def test_a_decision_id_sent_as_an_action_is_a_404(
    client, auth_headers, transcript_retro
):
    retro = transcript_retro["retro"]

    resp = await _post_confirm(
        client, retro["id"], {"actions": [{"id": transcript_retro["decision"]["id"]}]},
        auth_headers,
    )
    assert resp.status_code == 404, resp.text
    assert (await _stored(retro["id"])).actions == []


@pytest.mark.asyncio
async def test_a_batch_with_one_unknown_id_writes_nothing(
    client, auth_headers, transcript_retro
):
    """All or nothing, the rule #8 applied to a ballot with one bad cluster id."""
    retro = transcript_retro["retro"]

    resp = await _post_confirm(
        client,
        retro["id"],
        {
            "decisions": [
                {"id": transcript_retro["decision"]["id"]},
                {"id": UNKNOWN_UUID},
            ]
        },
        auth_headers,
    )
    assert resp.status_code == 404, resp.text

    stored = await _stored(retro["id"])
    assert stored.decisions == []
    assert stored.ai_suggestions["decisions"][0]["state"] == "pending"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "body_builder",
    [
        lambda ids: {"decisions": [{"id": ids["decision"]}, {"id": ids["decision"]}]},
        lambda ids: {"decisions": [{"id": ids["decision"]}], "rejected": [ids["decision"]]},
        lambda ids: {"actions": [{"id": ids["action"]}], "rejected": [ids["action"]]},
    ],
)
async def test_the_same_id_twice_in_one_body_is_rejected(
    client, auth_headers, transcript_retro, body_builder
):
    retro = transcript_retro["retro"]
    ids = {
        "decision": transcript_retro["decision"]["id"],
        "action": transcript_retro["action"]["id"],
    }

    resp = await _post_confirm(client, retro["id"], body_builder(ids), auth_headers)
    assert resp.status_code == 422, resp.text

    stored = await _stored(retro["id"])
    assert stored.decisions == [] and stored.actions == []
    assert stored.ai_suggestions["decisions"][0]["state"] == "pending"


@pytest.mark.asyncio
async def test_confirming_when_there_are_no_suggestions(
    client, auth_headers, discussion_retro, ai_proxy
):
    """Idle, processing and failed all hold no suggestions, so every id is a 404."""
    retro = discussion_retro["retro"]

    empty = await _post_confirm(client, retro["id"], {}, auth_headers)
    assert empty.status_code == 200, "an empty body is a no-op even with nothing to confirm"

    idle = await _post_confirm(
        client, retro["id"], {"decisions": [{"id": UNKNOWN_UUID}]}, auth_headers
    )
    assert idle.status_code == 404, idle.text

    document = await _stored(retro["id"])
    document.ai_suggestions = processing_document()
    await document.save()
    processing = await _post_confirm(
        client, retro["id"], {"rejected": [UNKNOWN_UUID]}, auth_headers
    )
    assert processing.status_code == 404, processing.text

    ai_proxy.raises(ai.ProxyTimeout("gone"))
    document.ai_suggestions = None
    await document.save()
    await _extract(client, retro["id"], auth_headers)
    failed = await _post_confirm(
        client, retro["id"], {"actions": [{"id": UNKNOWN_UUID}]}, auth_headers
    )
    assert failed.status_code == 404, failed.text

    stored = await _stored(retro["id"])
    assert stored.decisions == [] and stored.actions == []


# --- cross-cutting -----------------------------------------------------------


@pytest.mark.asyncio
async def test_every_endpoint_is_refused_in_the_vote_phase(client, auth_headers, voting_retro):
    retro = voting_retro["retro"]

    assert (
        await _post_transcript(client, retro["id"], {"text": "too early"}, auth_headers)
    ).status_code == 400
    assert (await _get_suggestions(client, retro["id"], auth_headers)).status_code == 400
    assert (await _post_confirm(client, retro["id"], {}, auth_headers)).status_code == 400

    stored = await _stored(retro["id"])
    assert stored.transcript is None and stored.ai_suggestions is None


@pytest.mark.asyncio
async def test_every_endpoint_is_refused_once_the_retro_is_done(
    client, auth_headers, transcript_retro, advance_phase
):
    """The meeting is over and #11 has published; the transcript flow closes with it."""
    retro = transcript_retro["retro"]
    await advance_phase(retro["id"], "done")

    assert (
        await _post_transcript(client, retro["id"], {"text": "too late"}, auth_headers)
    ).status_code == 400
    assert (await _get_suggestions(client, retro["id"], auth_headers)).status_code == 400
    assert (
        await _post_confirm(
            client, retro["id"], {"decisions": [{"id": transcript_retro["decision"]["id"]}]},
            auth_headers,
        )
    ).status_code == 400

    assert (await _stored(retro["id"])).decisions == []


@pytest.mark.asyncio
async def test_a_member_who_is_not_the_facilitator_is_refused(
    client, second_auth_headers, transcript_retro
):
    retro = transcript_retro["retro"]

    assert (
        await _post_transcript(client, retro["id"], {"text": "not mine"}, second_auth_headers)
    ).status_code == 403
    assert (await _get_suggestions(client, retro["id"], second_auth_headers)).status_code == 403
    assert (await _post_confirm(client, retro["id"], {}, second_auth_headers)).status_code == 403


@pytest.mark.asyncio
async def test_a_non_member_is_refused(client, outsider_auth_headers, transcript_retro):
    retro = transcript_retro["retro"]

    assert (
        await _post_transcript(client, retro["id"], {"text": "not mine"}, outsider_auth_headers)
    ).status_code == 403
    assert (await _get_suggestions(client, retro["id"], outsider_auth_headers)).status_code == 403
    assert (await _post_confirm(client, retro["id"], {}, outsider_auth_headers)).status_code == 403


@pytest.mark.asyncio
async def test_every_endpoint_requires_authentication(client, discussion_retro):
    """HTTPBearer rejects before the dependency runs, so this is 401 and not 403."""
    retro = discussion_retro["retro"]

    responses = [
        await _post_transcript(client, retro["id"], {"text": "hello"}),
        await _get_suggestions(client, retro["id"]),
        await _post_confirm(client, retro["id"], {}),
    ]
    for resp in responses:
        assert resp.status_code == 401, resp.text


@pytest.mark.asyncio
@pytest.mark.parametrize("retro_id", [UNKNOWN_ID, MALFORMED_ID])
async def test_unknown_and_malformed_retro_ids(client, auth_headers, discussion_retro, retro_id):
    responses = [
        await _post_transcript(client, retro_id, {"text": "hello"}, auth_headers),
        await _get_suggestions(client, retro_id, auth_headers),
        await _post_confirm(client, retro_id, {}, auth_headers),
    ]
    for resp in responses:
        assert resp.status_code == 404, resp.text


@pytest.mark.asyncio
async def test_check_order_is_role_then_phase(client, second_auth_headers, voting_retro):
    """Membership+facilitator → phase → id lookup, so 403 beats the wrong phase."""
    retro = voting_retro["retro"]

    assert (
        await _post_transcript(client, retro["id"], {"text": "x"}, second_auth_headers)
    ).status_code == 403, "a non-facilitator member in the vote phase, not 400"
    assert (await _get_suggestions(client, retro["id"], second_auth_headers)).status_code == 403
    assert (await _post_confirm(client, retro["id"], {}, second_auth_headers)).status_code == 403


@pytest.mark.asyncio
async def test_the_retro_payload_still_hides_who_voted_for_what(
    client, auth_headers, transcript_retro
):
    """#8's vote secrecy is untouched by anything here."""
    retro = transcript_retro["retro"]

    resp = await client.get(f"/api/retros/{retro['id']}", headers=auth_headers)
    assert resp.status_code == 200, resp.text
    for ballot in resp.json()["votes"]:
        assert set(ballot) == {"user_id", "submitted_at"}


@pytest.mark.asyncio
async def test_only_the_owner_name_lines_changed_in_the_discussion_module():
    """#9's files carry the two-line addition and nothing else."""
    from app.api import discussion as discussion_api

    source = Path(discussion_api.__file__).read_text(encoding="utf-8")
    assert "owner_name=action.owner_name" in source
    assert "409" not in source and "CONFLICT" not in source

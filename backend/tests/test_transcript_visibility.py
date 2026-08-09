"""Who may read the transcript and the AI drafts (#26).

One rule, applied on both endpoints that carry them: the facilitator's. The
test that matters is the negative one — a plain member fetching
`GET /api/retros/{id}` must not receive the meeting text through the back door
that #10's facilitator-only endpoint was closing at the front.
"""

import pytest


@pytest.fixture
async def extracted(client, auth_headers, discussion_retro, ai_proxy):
    """A discuss-phase retro with a stored transcript and finished drafts."""
    retro_id = discussion_retro["retro"]["id"]
    ai_proxy.returns(
        {"decisions": [{"text": "Ship on Fridays"}], "actions": [{"description": "Write it up"}]}
    )
    response = await client.post(
        f"/api/retros/{retro_id}/transcript",
        json={"text": "Dana said the deploy was frightening"},
        headers=auth_headers,
    )
    assert response.status_code == 202, response.text
    return retro_id


@pytest.mark.asyncio
async def test_the_facilitator_sees_both(client, auth_headers, extracted):
    response = await client.get(f"/api/retros/{extracted}", headers=auth_headers)

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["transcript"] == "Dana said the deploy was frightening"
    assert body["ai_suggestions"] is not None
    assert body["ai_suggestions"]["decisions"][0]["text"] == "Ship on Fridays"


@pytest.mark.asyncio
async def test_a_plain_member_sees_neither(client, second_auth_headers, extracted):
    response = await client.get(f"/api/retros/{extracted}", headers=second_auth_headers)

    assert response.status_code == 200, "the retro is still theirs to read"
    body = response.json()
    assert body["transcript"] is None
    assert body["ai_suggestions"] is None
    # Not just the fields — none of the text reaches them by any route.
    assert "frightening" not in response.text
    assert "Ship on Fridays" not in response.text
    assert "Write it up" not in response.text


@pytest.mark.asyncio
async def test_the_rest_of_the_payload_is_unchanged_for_a_member(
    client, auth_headers, second_auth_headers, extracted
):
    """Redaction is two fields, not a different response."""
    facilitator = (await client.get(f"/api/retros/{extracted}", headers=auth_headers)).json()
    member = (await client.get(f"/api/retros/{extracted}", headers=second_auth_headers)).json()

    assert set(member) == set(facilitator), "same keys, so #16 reads the same shape"
    for key in set(member) - {"transcript", "ai_suggestions"}:
        assert member[key] == facilitator[key], key


@pytest.mark.asyncio
async def test_the_two_endpoints_agree(client, auth_headers, second_auth_headers, extracted):
    """#10's dedicated endpoint and #6's payload now say the same thing."""
    assert (
        await client.get(f"/api/retros/{extracted}/suggestions", headers=second_auth_headers)
    ).status_code == 403

    dedicated = await client.get(f"/api/retros/{extracted}/suggestions", headers=auth_headers)
    combined = await client.get(f"/api/retros/{extracted}", headers=auth_headers)
    assert dedicated.status_code == 200
    assert combined.json()["ai_suggestions"] == dedicated.json()


@pytest.mark.asyncio
async def test_a_published_retro_keeps_the_rule(
    client, auth_headers, second_auth_headers, extracted
):
    """Publishing opens the summary, not the raw meeting text."""
    published = await client.post(
        f"/api/retros/{extracted}/summary/publish", headers=auth_headers
    )
    assert published.status_code == 200, published.text

    member = await client.get(f"/api/retros/{extracted}", headers=second_auth_headers)
    assert member.json()["transcript"] is None
    assert member.json()["ai_suggestions"] is None

    facilitator = await client.get(f"/api/retros/{extracted}", headers=auth_headers)
    assert facilitator.json()["transcript"] is not None


@pytest.mark.asyncio
async def test_a_retro_with_nothing_stored_reads_the_same_to_everyone(
    client, auth_headers, second_auth_headers, discussion_retro
):
    retro_id = discussion_retro["retro"]["id"]
    facilitator = (await client.get(f"/api/retros/{retro_id}", headers=auth_headers)).json()
    member = (await client.get(f"/api/retros/{retro_id}", headers=second_auth_headers)).json()

    assert facilitator["transcript"] is None and member["transcript"] is None
    assert facilitator["ai_suggestions"] is None and member["ai_suggestions"] is None

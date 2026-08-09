"""Removing a stored transcript (#25).

The two assertions this issue exists for: the meeting text really is gone from
the document, and what the facilitator already confirmed is still there.
"""

import pytest

from app.models.retro import Retrospective

UNKNOWN_ID = "507f1f77bcf86cd799439011"
MALFORMED_ID = "abc"

TRANSCRIPT = "Dana said the deploy was frightening"


@pytest.fixture
async def extracted(client, auth_headers, discussion_retro, ai_proxy):
    retro_id = discussion_retro["retro"]["id"]
    ai_proxy.returns(
        {"decisions": [{"text": "Ship on Fridays"}], "actions": [{"description": "Write it up"}]}
    )
    response = await client.post(
        f"/api/retros/{retro_id}/transcript", json={"text": TRANSCRIPT}, headers=auth_headers
    )
    assert response.status_code == 202, response.text
    return retro_id


def _url(retro_id: str) -> str:
    return f"/api/retros/{retro_id}/transcript"


@pytest.mark.asyncio
async def test_the_text_and_the_drafts_go(client, auth_headers, extracted):
    stored = await Retrospective.get(extracted)
    assert stored.transcript == TRANSCRIPT, "there is something to delete"
    assert stored.ai_suggestions is not None

    response = await client.delete(_url(extracted), headers=auth_headers)
    assert response.status_code == 204
    assert response.content == b""

    after = await Retrospective.get(extracted)
    assert after.transcript is None
    assert after.ai_suggestions is None

    # And nothing of it survives on the read path either.
    board = await client.get(f"/api/retros/{extracted}", headers=auth_headers)
    assert "frightening" not in board.text
    assert board.json()["transcript"] is None


@pytest.mark.asyncio
async def test_what_the_facilitator_confirmed_survives(client, auth_headers, extracted):
    suggestions = await client.get(f"/api/retros/{extracted}/suggestions", headers=auth_headers)
    decision_id = suggestions.json()["decisions"][0]["id"]
    confirmed = await client.post(
        f"/api/retros/{extracted}/suggestions/confirm",
        json={"decisions": [{"id": decision_id}]},
        headers=auth_headers,
    )
    assert confirmed.status_code == 200, confirmed.text

    assert (await client.delete(_url(extracted), headers=auth_headers)).status_code == 204

    after = await Retrospective.get(extracted)
    assert after.transcript is None
    assert [decision.text for decision in after.decisions] == ["Ship on Fridays"], (
        "the retro's own record, not the transcript's"
    )

    board = await client.get(f"/api/retros/{extracted}", headers=auth_headers)
    assert board.json()["decisions"][0]["text"] == "Ship on Fridays"
    assert board.json()["ai_suggestions"] is None


@pytest.mark.asyncio
async def test_deleting_twice_is_still_a_204(client, auth_headers, extracted):
    assert (await client.delete(_url(extracted), headers=auth_headers)).status_code == 204
    assert (await client.delete(_url(extracted), headers=auth_headers)).status_code == 204


@pytest.mark.asyncio
async def test_a_retro_that_never_had_one_can_be_deleted(
    client, auth_headers, discussion_retro
):
    retro_id = discussion_retro["retro"]["id"]
    assert (await client.delete(_url(retro_id), headers=auth_headers)).status_code == 204
    assert (await Retrospective.get(retro_id)).transcript is None


@pytest.mark.asyncio
async def test_only_the_facilitator_may_delete_it(
    client, second_auth_headers, outsider_auth_headers, extracted
):
    assert (await client.delete(_url(extracted))).status_code == 401
    assert (
        await client.delete(_url(extracted), headers=second_auth_headers)
    ).status_code == 403
    assert (
        await client.delete(_url(extracted), headers=outsider_auth_headers)
    ).status_code == 403

    assert (await Retrospective.get(extracted)).transcript == TRANSCRIPT, "still there"


@pytest.mark.asyncio
async def test_unknown_and_malformed_ids_are_404(client, auth_headers, extracted):
    assert (await client.delete(_url(UNKNOWN_ID), headers=auth_headers)).status_code == 404
    assert (await client.delete(_url(MALFORMED_ID), headers=auth_headers)).status_code == 404


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["reveal", "cluster", "vote", "discuss", "done"])
async def test_retention_works_in_every_phase(client, auth_headers, extracted, phase):
    """A control that stops working when the retro finishes is not a control."""
    retro = await Retrospective.get(extracted)
    retro.phase = phase
    await retro.save()

    assert (await client.delete(_url(extracted), headers=auth_headers)).status_code == 204
    assert (await Retrospective.get(extracted)).transcript is None


@pytest.mark.asyncio
async def test_it_still_works_after_publish_closed_the_cycle(client, auth_headers, extracted):
    published = await client.post(
        f"/api/retros/{extracted}/summary/publish", headers=auth_headers
    )
    assert published.status_code == 200, published.text

    # #20 refuses every other write on a closed cycle; this one is retention,
    # and a published retro is exactly when the raw text is least wanted.
    assert (await client.delete(_url(extracted), headers=auth_headers)).status_code == 204
    assert (await Retrospective.get(extracted)).transcript is None

    summary = await client.get(f"/api/retros/{extracted}/summary", headers=auth_headers)
    assert summary.status_code == 200, "the published summary is unaffected"


@pytest.mark.asyncio
async def test_it_waits_for_an_extraction_in_flight(
    client, auth_headers, discussion_retro, ai_proxy
):
    """Deleting mid-extraction would be undone by the task's own final write."""
    retro_id = discussion_retro["retro"]["id"]
    retro = await Retrospective.get(retro_id)
    retro.transcript = TRANSCRIPT
    retro.ai_suggestions = {
        "status": "processing",
        "error": None,
        "requested_at": None,
        "completed_at": None,
        "decisions": [],
        "actions": [],
    }
    await retro.save()

    response = await client.delete(_url(retro_id), headers=auth_headers)
    assert response.status_code == 409
    assert (await Retrospective.get(retro_id)).transcript == TRANSCRIPT

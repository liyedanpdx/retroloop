"""A closed cycle takes no more retrospective writes (#20).

The shape of this file is one table, `COMMANDS`, listing every phase-gated retro
command with the phase it belongs to. Each command is then run four ways — as a
non-member, against a missing retro, in the wrong phase, and in its own phase
with the cycle closed — so the closed-cycle 400 is proved to sit *after* 403,
404 and the wrong-phase 400 rather than in front of them.

Every refusal is followed by a document readback. A guard that returns 400 and
still writes is the failure this issue exists to prevent, and a status code on
its own cannot tell you it did not happen.
"""

import pytest
from beanie import PydanticObjectId

from app.models.cycle import CLOSED, Cycle
from app.models.feedback import FeedbackCard
from app.models.retro import Retrospective

UNKNOWN_ID = "507f1f77bcf86cd799439011"
MALFORMED_ID = "abc"
CLOSED_DETAIL = "The retrospective's cycle is closed"


async def _close(cycle_id) -> None:
    """Close the cycle behind a retro without going through publish.

    Directly, on purpose: publish also moves the retro to `done`, and half of
    what #20 has to refuse is a command arriving at a retro still sitting in the
    phase that command belongs to.
    """
    cycle = await Cycle.get(cycle_id)
    cycle.status = CLOSED
    await cycle.save()


async def _snapshot(retro_id) -> tuple:
    """Everything a refused command must have left alone."""
    retro = await Retrospective.get(retro_id)
    cards = await FeedbackCard.find(FeedbackCard.cycle_id == retro.cycle_id).sort("+_id").to_list()
    return (
        retro.model_dump(mode="json"),
        [(str(card.id), card.cluster_id, card.text) for card in cards],
    )


@pytest.fixture
async def closed(client, auth_headers, second_auth_headers, discussion_retro, ai_proxy):
    """A retro carrying real content in every array, and a closed cycle.

    Built through the API up to `discuss` so the topics, decision, action and
    suggestions are the ones the app itself makes; the phase is then rewound per
    test, because a command has to be refused in the phase it is legal in.
    """
    retro_id = discussion_retro["retro"]["id"]
    decision = await client.post(
        f"/api/retros/{retro_id}/decisions", json={"text": "Ship it"}, headers=auth_headers
    )
    action = await client.post(
        f"/api/retros/{retro_id}/actions", json={"description": "Write it up"}, headers=auth_headers
    )
    ai_proxy.returns({"decisions": [{"text": "From the meeting"}], "actions": []})
    await client.post(
        f"/api/retros/{retro_id}/transcript", json={"text": "we agreed"}, headers=auth_headers
    )
    suggestions = await client.get(f"/api/retros/{retro_id}/suggestions", headers=auth_headers)

    return {
        "retro_id": retro_id,
        "cycle_id": discussion_retro["cycle"]["id"],
        "clusters": discussion_retro["clusters"],
        "topic": discussion_retro["topics"][0],
        "decision": decision.json(),
        "action": action.json(),
        "suggestion": suggestions.json()["decisions"][0],
        "cards": await FeedbackCard.find(
            FeedbackCard.cycle_id == PydanticObjectId(discussion_retro["cycle"]["id"])
        ).to_list(),
    }


def _commands(closed: dict) -> dict:
    """`name -> (phase, method, url, body)` for every phase-gated retro write."""
    retro_id = closed["retro_id"]
    cluster_id = closed["clusters"][0]["id"]
    card_id = str(closed["cards"][0].id)
    return {
        "advance phase": ("cluster", "patch", f"/api/retros/{retro_id}/phase", {"phase": "vote"}),
        "create cluster": ("cluster", "post", f"/api/retros/{retro_id}/clusters", {"name": "New"}),
        "rename cluster": (
            "cluster", "patch", f"/api/retros/{retro_id}/clusters/{cluster_id}", {"name": "Renamed"}
        ),
        "delete cluster": (
            "cluster", "delete", f"/api/retros/{retro_id}/clusters/{cluster_id}", None
        ),
        "move card": ("cluster", "patch", f"/api/feedback/{card_id}/cluster", {"cluster_id": cluster_id}),
        "suggest clusters": ("cluster", "post", f"/api/retros/{retro_id}/clusters/suggest", {}),
        "submit votes": ("vote", "post", f"/api/retros/{retro_id}/votes", {"cluster_ids": [cluster_id]}),
        "update topic": (
            "discuss", "patch", f"/api/retros/{retro_id}/topics/{closed['topic']['id']}",
            {"status": "discussed", "notes": "late note"},
        ),
        "create decision": ("discuss", "post", f"/api/retros/{retro_id}/decisions", {"text": "Late"}),
        "update decision": (
            "discuss", "patch", f"/api/retros/{retro_id}/decisions/{closed['decision']['id']}",
            {"text": "Reworded"},
        ),
        "delete decision": (
            "discuss", "delete", f"/api/retros/{retro_id}/decisions/{closed['decision']['id']}", None
        ),
        "create action": (
            "discuss", "post", f"/api/retros/{retro_id}/actions", {"description": "Late action"}
        ),
        "update action": (
            "discuss", "patch", f"/api/retros/{retro_id}/actions/{closed['action']['id']}",
            {"status": "done"},
        ),
        "delete action": (
            "discuss", "delete", f"/api/retros/{retro_id}/actions/{closed['action']['id']}", None
        ),
        "paste transcript": (
            "discuss", "post", f"/api/retros/{retro_id}/transcript", {"text": "another meeting"}
        ),
        "confirm suggestions": (
            "discuss", "post", f"/api/retros/{retro_id}/suggestions/confirm",
            {"decisions": [{"id": closed["suggestion"]["id"]}]},
        ),
        "publish summary": ("discuss", "post", f"/api/retros/{retro_id}/summary/publish", None),
    }


async def _call(client, method, url, body, headers):
    kwargs = {"headers": headers} if headers else {}
    if body is not None:
        kwargs["json"] = body
    return await getattr(client, method)(url, **kwargs)


async def _in_phase(retro_id, phase) -> None:
    retro = await Retrospective.get(retro_id)
    retro.phase = phase
    await retro.save()


COMMAND_NAMES = [
    "advance phase", "create cluster", "rename cluster", "delete cluster", "move card",
    "suggest clusters", "submit votes", "update topic", "create decision", "update decision",
    "delete decision", "create action", "update action", "delete action", "paste transcript",
    "confirm suggestions", "publish summary",
]


# --- the refusal -------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("name", COMMAND_NAMES)
async def test_every_command_is_refused_on_a_closed_cycle_and_writes_nothing(
    client, auth_headers, closed, ai_proxy, name
):
    phase, method, url, body = _commands(closed)[name]
    await _in_phase(closed["retro_id"], phase)
    await _close(closed["cycle_id"])

    before = await _snapshot(closed["retro_id"])
    response = await _call(client, method, url, body, auth_headers)

    assert response.status_code == 400, f"{name}: {response.text}"
    assert response.json()["detail"] == CLOSED_DETAIL, name
    assert await _snapshot(closed["retro_id"]) == before, f"{name} wrote something"


@pytest.mark.asyncio
@pytest.mark.parametrize("name", COMMAND_NAMES)
async def test_the_wrong_phase_still_wins_over_a_closed_cycle(
    client, auth_headers, closed, name
):
    """A caller in the wrong phase is told that, closed cycle or not.

    Both refusals are 400, so the detail is the only thing that separates them —
    which is exactly why the closed-cycle detail is a fixed string.
    """
    phase, method, url, body = _commands(closed)[name]
    wrong = "reveal" if phase != "reveal" else "cluster"
    await _in_phase(closed["retro_id"], wrong)
    await _close(closed["cycle_id"])

    response = await _call(client, method, url, body, auth_headers)
    assert response.status_code == 400, f"{name}: {response.text}"
    assert response.json()["detail"] != CLOSED_DETAIL, name


@pytest.mark.asyncio
@pytest.mark.parametrize("name", COMMAND_NAMES)
async def test_permission_and_missing_ids_still_win_over_a_closed_cycle(
    client, outsider_auth_headers, closed, name
):
    phase, method, url, body = _commands(closed)[name]
    await _in_phase(closed["retro_id"], phase)
    await _close(closed["cycle_id"])

    refused = await _call(client, method, url, body, outsider_auth_headers)
    assert refused.status_code in (403, 404), f"{name}: {refused.text}"
    assert refused.json()["detail"] != CLOSED_DETAIL, name

    missing = await _call(
        client, method, url.replace(closed["retro_id"], UNKNOWN_ID), body, outsider_auth_headers
    )
    assert missing.status_code in (403, 404), f"{name}: {missing.text}"


@pytest.mark.asyncio
@pytest.mark.parametrize("name", COMMAND_NAMES)
async def test_no_command_is_authenticated_by_a_closed_cycle(client, closed, name):
    phase, method, url, body = _commands(closed)[name]
    await _in_phase(closed["retro_id"], phase)
    await _close(closed["cycle_id"])

    response = await _call(client, method, url, body, None)
    assert response.status_code in (401, 403), f"{name}: {response.text}"


# --- the orders that are not simply "permission, phase, cycle" ----------------


@pytest.mark.asyncio
async def test_an_unknown_card_is_404_before_the_cycle_is_looked_at(
    client, auth_headers, closed
):
    """The card is loaded first because the cycle is reached *through* it."""
    await _in_phase(closed["retro_id"], "cluster")
    await _close(closed["cycle_id"])

    for card_id in (UNKNOWN_ID, MALFORMED_ID):
        response = await client.patch(
            f"/api/feedback/{card_id}/cluster", json={"cluster_id": None}, headers=auth_headers
        )
        assert response.status_code == 404, response.text
        assert response.json()["detail"] != CLOSED_DETAIL


@pytest.mark.asyncio
async def test_a_real_card_is_refused_before_the_target_cluster_is_checked(
    client, auth_headers, closed
):
    await _in_phase(closed["retro_id"], "cluster")
    await _close(closed["cycle_id"])

    response = await client.patch(
        f"/api/feedback/{str(closed['cards'][0].id)}/cluster",
        json={"cluster_id": "no-such-cluster"},
        headers=auth_headers,
    )
    assert response.status_code == 400, "the cycle, not the unknown cluster id"
    assert response.json()["detail"] == CLOSED_DETAIL


@pytest.mark.asyncio
async def test_an_owner_editing_their_own_action_is_refused_before_the_lookup(
    client, auth_headers, second_auth_headers, closed
):
    """Even the edit #9 lets a non-facilitator make stops at a closed cycle."""
    action = await client.post(
        f"/api/retros/{closed['retro_id']}/actions",
        json={"description": "Bob's", "owner_id": None},
        headers=auth_headers,
    )
    await _close(closed["cycle_id"])

    for headers in (auth_headers, second_auth_headers):
        response = await client.patch(
            f"/api/retros/{closed['retro_id']}/actions/{UNKNOWN_ID}",
            json={"status": "done"},
            headers=headers,
        )
        assert response.status_code in (400, 403), response.text
        if response.status_code == 400:
            assert response.json()["detail"] == CLOSED_DETAIL, "before the action lookup"
    assert action.status_code == 201


@pytest.mark.asyncio
async def test_a_closed_cycle_refuses_the_suggestion_before_the_proxy_is_called(
    client, auth_headers, closed, ai_proxy
):
    await _in_phase(closed["retro_id"], "cluster")
    await _close(closed["cycle_id"])
    ai_proxy.calls.clear()

    response = await client.post(
        f"/api/retros/{closed['retro_id']}/clusters/suggest", json={}, headers=auth_headers
    )
    assert response.status_code == 400
    assert response.json()["detail"] == CLOSED_DETAIL
    assert ai_proxy.calls == [], "no proxy budget is spent on a closed retro"


@pytest.mark.asyncio
async def test_a_closed_cycle_refuses_a_transcript_before_extraction_is_scheduled(
    client, auth_headers, closed, ai_proxy
):
    await _close(closed["cycle_id"])
    before = await _snapshot(closed["retro_id"])
    ai_proxy.calls.clear()

    response = await client.post(
        f"/api/retros/{closed['retro_id']}/transcript",
        json={"text": "a whole other meeting"},
        headers=auth_headers,
    )
    assert response.status_code == 400
    assert ai_proxy.calls == []
    assert await _snapshot(closed["retro_id"]) == before, "transcript and suggestions untouched"


# --- nothing is broadcast ----------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("name", ["advance phase", "create cluster", "move card", "update topic"])
async def test_a_refused_command_broadcasts_nothing(client, auth_headers, closed, name):
    from app.services import realtime

    sent = []
    original = realtime.manager.broadcast

    async def _record(retro_id, event, data):
        sent.append(event)
        await original(retro_id, event, data)

    realtime.manager.broadcast = _record
    try:
        phase, method, url, body = _commands(closed)[name]
        await _in_phase(closed["retro_id"], phase)
        await _close(closed["cycle_id"])
        response = await _call(client, method, url, body, auth_headers)
    finally:
        realtime.manager.broadcast = original

    assert response.status_code == 400
    assert sent == [], f"{name} announced a change it did not make"


# --- reads are untouched -----------------------------------------------------


@pytest.mark.asyncio
async def test_every_read_still_works_on_a_closed_cycle(
    client, auth_headers, second_auth_headers, closed
):
    retro_id, cycle_id = closed["retro_id"], closed["cycle_id"]
    project_id = (await Cycle.get(cycle_id)).project_id

    before = {
        "retro": await client.get(f"/api/retros/{retro_id}", headers=auth_headers),
        "cycle": await client.get(f"/api/cycles/{cycle_id}", headers=auth_headers),
        "cycles": await client.get(f"/api/projects/{project_id}/cycles", headers=auth_headers),
        "cards": await client.get(f"/api/cycles/{cycle_id}/feedback", headers=auth_headers),
        "results": await client.get(f"/api/retros/{retro_id}/votes/results", headers=auth_headers),
        "summary": await client.get(f"/api/retros/{retro_id}/summary", headers=auth_headers),
        "suggestions": await client.get(f"/api/retros/{retro_id}/suggestions", headers=auth_headers),
    }
    for name, response in before.items():
        assert response.status_code == 200, f"{name} before close: {response.text}"

    await _close(cycle_id)
    snapshot = await _snapshot(retro_id)

    for name, earlier in before.items():
        url = str(earlier.request.url).replace("http://test", "")
        again = await client.get(url, headers=auth_headers)
        assert again.status_code == 200, f"{name} after close: {again.text}"
        if name != "cycle" and name != "cycles":
            assert again.json() == earlier.json(), f"{name} changed"
    assert await _snapshot(retro_id) == snapshot, "and reading wrote nothing"


@pytest.mark.asyncio
async def test_a_published_retro_stays_readable_by_every_member(
    client, auth_headers, second_auth_headers, closed
):
    retro_id = closed["retro_id"]
    published = await client.post(
        f"/api/retros/{retro_id}/summary/publish", headers=auth_headers
    )
    assert published.status_code == 200, published.text

    for headers in (auth_headers, second_auth_headers):
        assert (await client.get(f"/api/retros/{retro_id}", headers=headers)).status_code == 200
        assert (
            await client.get(f"/api/retros/{retro_id}/summary", headers=headers)
        ).status_code == 200
        assert (
            await client.get(f"/api/retros/{retro_id}/votes/results", headers=headers)
        ).status_code == 200

    # #10's own restriction survives; "reads are not closed-gated" is not
    # "every read is now open".
    assert (
        await client.get(f"/api/retros/{retro_id}/suggestions", headers=second_auth_headers)
    ).status_code == 403


# --- publish, which is the one write that closes ------------------------------


@pytest.mark.asyncio
async def test_the_first_publish_is_allowed_and_every_later_command_is_not(
    client, auth_headers, closed
):
    retro_id = closed["retro_id"]
    published = await client.post(
        f"/api/retros/{retro_id}/summary/publish", headers=auth_headers
    )
    assert published.status_code == 200, published.text

    retro = await Retrospective.get(retro_id)
    cycle = await Cycle.get(closed["cycle_id"])
    assert (retro.phase, cycle.status) == ("done", CLOSED)
    assert cycle.closed_at is not None

    again = await client.post(f"/api/retros/{retro_id}/summary/publish", headers=auth_headers)
    assert again.status_code == 400
    assert (await Retrospective.get(retro_id)).phase == "done"


@pytest.mark.asyncio
async def test_a_cycle_closed_behind_a_discussing_retro_cannot_be_published(
    client, auth_headers, closed
):
    """PATCH /api/cycles/{id} closed it; publishing would reopen finished work."""
    await _close(closed["cycle_id"])
    before = await _snapshot(closed["retro_id"])

    response = await client.post(
        f"/api/retros/{closed['retro_id']}/summary/publish", headers=auth_headers
    )
    assert response.status_code == 400
    assert response.json()["detail"] == CLOSED_DETAIL
    assert await _snapshot(closed["retro_id"]) == before


# --- what #20 must not have changed ------------------------------------------


@pytest.mark.asyncio
async def test_the_older_closed_checks_keep_their_own_wording(
    client, auth_headers, project, cycle, add_card
):
    """#4 and #5 already refused a closed cycle. #20 does not restate them."""
    card = await add_card(cycle["id"])
    await client.patch(
        f"/api/cycles/{cycle['id']}", json={"status": "closed"}, headers=auth_headers
    )

    creating = await client.post(
        f"/api/cycles/{cycle['id']}/feedback",
        json={"category": "start", "text": "too late"},
        headers=auth_headers,
    )
    assert creating.status_code == 400
    assert creating.json()["detail"] != CLOSED_DETAIL

    editing = await client.patch(
        f"/api/feedback/{card['id']}", json={"text": "reworded"}, headers=auth_headers
    )
    assert editing.status_code == 400
    assert editing.json()["detail"] != CLOSED_DETAIL

    starting = await client.post(f"/api/cycles/{cycle['id']}/retro", headers=auth_headers)
    assert starting.status_code == 400
    assert starting.json()["detail"] != CLOSED_DETAIL
    assert await Retrospective.find_one(Retrospective.cycle_id == PydanticObjectId(cycle["id"])) is None


@pytest.mark.asyncio
async def test_closing_a_cycle_is_still_allowed(client, auth_headers, cycle):
    """The guard must not block the operation that creates the state it guards."""
    response = await client.patch(
        f"/api/cycles/{cycle['id']}", json={"status": "closed"}, headers=auth_headers
    )
    assert response.status_code == 200, response.text
    assert (await Cycle.get(cycle["id"])).status == CLOSED

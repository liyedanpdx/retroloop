"""Counting who took part without recording what they wrote (#28).

The first test is the one this issue exists for: a member whose every card was
anonymous is counted, and nothing anywhere links them to a card. The rest is
the arithmetic — once per member, never more than the member count, and never
leaked through an endpoint.
"""

import pytest
from beanie import PydanticObjectId

from app.models.cycle import Cycle
from app.models.feedback import FeedbackCard


async def _summary(client, retro_id, headers):
    response = await client.get(f"/api/retros/{retro_id}/summary", headers=headers)
    assert response.status_code == 200, response.text
    return response.json()["participation"]


@pytest.mark.asyncio
async def test_an_anonymous_only_submitter_is_counted(
    client, auth_headers, second_auth_headers, shared_cycle, add_card, reveal, advance_phase
):
    await add_card(shared_cycle["id"], headers=second_auth_headers, is_anonymous=True)

    retro = await reveal(shared_cycle["id"])
    for phase in ("cluster", "vote", "discuss"):
        retro = await advance_phase(retro["id"], phase)

    participation = await _summary(client, retro["id"], auth_headers)
    assert participation["submitted_feedback"] == 1, "bob took part, anonymously"
    assert participation["total_members"] == 2


@pytest.mark.asyncio
async def test_the_marker_points_at_no_card(
    client, second_auth_headers, shared_cycle, add_card
):
    """Nothing gained here can be used to tell which card is whose."""
    await add_card(
        shared_cycle["id"], headers=second_auth_headers, is_anonymous=True, text="in confidence"
    )

    cards = await FeedbackCard.find(
        FeedbackCard.cycle_id == PydanticObjectId(shared_cycle["id"])
    ).to_list()
    assert len(cards) == 1
    assert cards[0].author_id is None, "#5's rule is untouched"
    assert cards[0].is_anonymous is True

    cycle = await Cycle.get(shared_cycle["id"])
    assert len(cycle.participants) == 1
    # The marker is a user id on the cycle. It carries no card id, category,
    # text, cluster or time — there is nothing on it to join against.
    stored = str(cycle.model_dump(mode="json"))
    assert "in confidence" not in stored, "no text"
    assert str(cards[0].id) not in stored, "no card id"
    assert "category" not in stored and "cluster" not in stored, "nothing to join on"


@pytest.mark.asyncio
async def test_a_member_counts_once_however_many_cards(
    client, auth_headers, second_auth_headers, shared_cycle, add_card, reveal, advance_phase
):
    for text in ("one", "two", "three"):
        await add_card(shared_cycle["id"], headers=second_auth_headers, text=text)
    await add_card(shared_cycle["id"], headers=second_auth_headers, is_anonymous=True)

    cycle = await Cycle.get(shared_cycle["id"])
    assert len(cycle.participants) == 1

    retro = await reveal(shared_cycle["id"])
    for phase in ("cluster", "vote", "discuss"):
        retro = await advance_phase(retro["id"], phase)
    assert (await _summary(client, retro["id"], auth_headers))["submitted_feedback"] == 1


@pytest.mark.asyncio
async def test_mixed_anonymous_and_attributed_members_are_both_counted(
    client, auth_headers, second_auth_headers, shared_cycle, add_card, reveal, advance_phase
):
    await add_card(shared_cycle["id"], headers=auth_headers)
    await add_card(shared_cycle["id"], headers=second_auth_headers, is_anonymous=True)

    retro = await reveal(shared_cycle["id"])
    for phase in ("cluster", "vote", "discuss"):
        retro = await advance_phase(retro["id"], phase)

    participation = await _summary(client, retro["id"], auth_headers)
    assert participation == {"total_members": 2, "submitted_feedback": 2, "voted": 0}


@pytest.mark.asyncio
async def test_a_removed_member_stops_counting(
    client, auth_headers, second_auth_headers, second_user, project_with_member, shared_cycle,
    add_card, reveal, advance_phase,
):
    await add_card(shared_cycle["id"], headers=second_auth_headers, is_anonymous=True)
    retro = await reveal(shared_cycle["id"])
    for phase in ("cluster", "vote", "discuss"):
        retro = await advance_phase(retro["id"], phase)

    before = await _summary(client, retro["id"], auth_headers)
    assert before["submitted_feedback"] == 1

    removed = await client.delete(
        f"/api/projects/{project_with_member['id']}/members/{second_user['id']}",
        headers=auth_headers,
    )
    assert removed.status_code == 200, removed.text

    after = await _summary(client, retro["id"], auth_headers)
    assert after == {"total_members": 1, "submitted_feedback": 0, "voted": 0}
    assert after["submitted_feedback"] <= after["total_members"], "never more than the team"

    # The marker itself is left alone; it is simply not counted.
    cycle = await Cycle.get(shared_cycle["id"])
    assert len(cycle.participants) == 1


@pytest.mark.asyncio
async def test_the_dashboard_uses_the_same_marker(
    client, auth_headers, second_auth_headers, project_with_member, shared_cycle, add_card
):
    await add_card(shared_cycle["id"], headers=second_auth_headers, is_anonymous=True)

    response = await client.get(
        f"/api/projects/{project_with_member['id']}/dashboard", headers=auth_headers
    )
    assert response.status_code == 200, response.text
    assert response.json()["current_cycle"]["progress"] == {
        "submitted_members": 1,
        "total_members": 2,
    }


@pytest.mark.asyncio
async def test_no_endpoint_returns_the_marker(
    client, auth_headers, second_auth_headers, project_with_member, shared_cycle, add_card,
    second_user,
):
    """It is for counting, not for reading. Nothing hands it out."""
    await add_card(shared_cycle["id"], headers=second_auth_headers, is_anonymous=True)

    for url in (
        f"/api/cycles/{shared_cycle['id']}",
        f"/api/projects/{project_with_member['id']}/cycles",
        f"/api/projects/{project_with_member['id']}/dashboard",
        f"/api/cycles/{shared_cycle['id']}/feedback",
    ):
        response = await client.get(url, headers=auth_headers)
        assert response.status_code == 200, url
        assert "participants" not in response.text, url

    # On the cycle reads, bob's id appearing at all would *be* the leak — there
    # is nothing else on a cycle that mentions him. (#31's dashboard names every
    # member by design, which is a different thing entirely.)
    for url in (
        f"/api/cycles/{shared_cycle['id']}",
        f"/api/projects/{project_with_member['id']}/cycles",
        f"/api/cycles/{shared_cycle['id']}/feedback",
    ):
        response = await client.get(url, headers=auth_headers)
        assert second_user["id"] not in response.text, url


@pytest.mark.asyncio
async def test_a_cycle_nobody_wrote_in_counts_nobody(
    client, auth_headers, project_with_member, shared_cycle, reveal, advance_phase
):
    retro = await reveal(shared_cycle["id"])
    for phase in ("cluster", "vote", "discuss"):
        retro = await advance_phase(retro["id"], phase)

    assert (await _summary(client, retro["id"], auth_headers))["submitted_feedback"] == 0

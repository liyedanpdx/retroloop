from uuid import UUID, uuid4

import pytest

from app.models.feedback import FeedbackCard
from app.models.retro import Retrospective

UNKNOWN_ID = "507f1f77bcf86cd799439011"
UNKNOWN_CLUSTER_ID = "3f2504e0-4f89-41d3-9a0c-0305e82c3301"


async def _create_cluster(client, retro_id, headers, name="Flow"):
    resp = await client.post(
        f"/api/retros/{retro_id}/clusters", json={"name": name}, headers=headers
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _move(client, card_id, cluster_id, headers):
    return await client.patch(
        f"/api/feedback/{card_id}/cluster", json={"cluster_id": cluster_id}, headers=headers
    )


async def _call_every_endpoint(client, retro_id, cluster_id, card_id, headers=None):
    """Hit all four endpoints of this issue and return their responses.

    `headers=None` sends no Authorization header at all, which is what the
    unauthenticated case needs.
    """
    kwargs = {} if headers is None else {"headers": headers}
    return [
        await client.post(f"/api/retros/{retro_id}/clusters", json={"name": "Flow"}, **kwargs),
        await client.patch(
            f"/api/retros/{retro_id}/clusters/{cluster_id}", json={"name": "Renamed"}, **kwargs
        ),
        await client.delete(f"/api/retros/{retro_id}/clusters/{cluster_id}", **kwargs),
        await client.patch(
            f"/api/feedback/{card_id}/cluster", json={"cluster_id": cluster_id}, **kwargs
        ),
    ]


@pytest.mark.asyncio
async def test_create_cluster(client, auth_headers, clustering_retro):
    retro = clustering_retro["retro"]

    resp = await client.post(
        f"/api/retros/{retro['id']}/clusters", json={"name": "Flow"}, headers=auth_headers
    )
    assert resp.status_code == 201, resp.text
    data = resp.json()
    assert data["name"] == "Flow"
    assert UUID(data["id"]), "the id is a generated UUID"
    assert data["created_at"] is not None

    stored = await Retrospective.get(retro["id"])
    assert [(c.id, c.name) for c in stored.clusters] == [(data["id"], "Flow")]
    assert stored.clusters[0].created_at is not None


@pytest.mark.asyncio
async def test_create_cluster_gives_each_one_its_own_id(client, auth_headers, clustering_retro):
    retro = clustering_retro["retro"]
    first = await _create_cluster(client, retro["id"], auth_headers, name="Flow")
    second = await _create_cluster(client, retro["id"], auth_headers, name="Tooling")

    assert first["id"] != second["id"]
    stored = await Retrospective.get(retro["id"])
    assert [c.name for c in stored.clusters] == ["Flow", "Tooling"]


@pytest.mark.asyncio
@pytest.mark.parametrize("body", [{}, {"name": ""}, {"name": "   "}, {"name": None}])
async def test_create_cluster_rejects_a_blank_name(client, auth_headers, clustering_retro, body):
    resp = await client.post(
        f"/api/retros/{clustering_retro['retro']['id']}/clusters",
        json=body,
        headers=auth_headers,
    )
    assert resp.status_code == 422, resp.text


@pytest.mark.asyncio
async def test_rename_cluster(client, auth_headers, clustering_retro):
    retro = clustering_retro["retro"]
    cluster = await _create_cluster(client, retro["id"], auth_headers, name="Flow")

    resp = await client.patch(
        f"/api/retros/{retro['id']}/clusters/{cluster['id']}",
        json={"name": "Delivery flow"},
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["id"] == cluster["id"]
    assert resp.json()["name"] == "Delivery flow"

    stored = await Retrospective.get(retro["id"])
    assert [c.name for c in stored.clusters] == ["Delivery flow"]


@pytest.mark.asyncio
async def test_delete_cluster(client, auth_headers, clustering_retro):
    retro = clustering_retro["retro"]
    doomed = await _create_cluster(client, retro["id"], auth_headers, name="Flow")
    kept = await _create_cluster(client, retro["id"], auth_headers, name="Tooling")

    resp = await client.delete(
        f"/api/retros/{retro['id']}/clusters/{doomed['id']}", headers=auth_headers
    )
    assert resp.status_code == 200, resp.text

    stored = await Retrospective.get(retro["id"])
    assert [c.id for c in stored.clusters] == [kept["id"]]


@pytest.mark.asyncio
async def test_delete_cluster_ungroups_its_own_cards_and_leaves_the_rest(
    client, auth_headers, clustering_retro
):
    retro = clustering_retro["retro"]
    first, second, third = clustering_retro["cards"]
    doomed = await _create_cluster(client, retro["id"], auth_headers, name="Flow")
    kept = await _create_cluster(client, retro["id"], auth_headers, name="Tooling")

    assert (await _move(client, first["id"], doomed["id"], auth_headers)).status_code == 200
    assert (await _move(client, second["id"], doomed["id"], auth_headers)).status_code == 200
    assert (await _move(client, third["id"], kept["id"], auth_headers)).status_code == 200

    resp = await client.delete(
        f"/api/retros/{retro['id']}/clusters/{doomed['id']}", headers=auth_headers
    )
    assert resp.status_code == 200, resp.text

    # Read the cards back from the database, not from the response body.
    assert (await FeedbackCard.get(first["id"])).cluster_id is None
    assert (await FeedbackCard.get(second["id"])).cluster_id is None
    assert (await FeedbackCard.get(third["id"])).cluster_id == kept["id"]


@pytest.mark.asyncio
async def test_move_card_into_a_cluster(client, auth_headers, clustering_retro):
    retro = clustering_retro["retro"]
    card = clustering_retro["cards"][0]
    cluster = await _create_cluster(client, retro["id"], auth_headers, name="Flow")

    resp = await _move(client, card["id"], cluster["id"], auth_headers)
    assert resp.status_code == 200, resp.text
    assert resp.json()["id"] == card["id"]
    assert resp.json()["cluster_id"] == cluster["id"]

    assert (await FeedbackCard.get(card["id"])).cluster_id == cluster["id"]


@pytest.mark.asyncio
async def test_move_card_out_of_its_cluster(client, auth_headers, clustering_retro):
    retro = clustering_retro["retro"]
    card = clustering_retro["cards"][0]
    cluster = await _create_cluster(client, retro["id"], auth_headers, name="Flow")
    await _move(client, card["id"], cluster["id"], auth_headers)

    resp = await _move(client, card["id"], None, auth_headers)
    assert resp.status_code == 200, resp.text
    assert resp.json()["cluster_id"] is None

    assert (await FeedbackCard.get(card["id"])).cluster_id is None


@pytest.mark.asyncio
async def test_move_card_straight_from_one_cluster_to_another(
    client, auth_headers, clustering_retro
):
    retro = clustering_retro["retro"]
    card = clustering_retro["cards"][0]
    first = await _create_cluster(client, retro["id"], auth_headers, name="Flow")
    second = await _create_cluster(client, retro["id"], auth_headers, name="Tooling")
    await _move(client, card["id"], first["id"], auth_headers)

    resp = await _move(client, card["id"], second["id"], auth_headers)
    assert resp.status_code == 200, resp.text
    assert resp.json()["cluster_id"] == second["id"]

    assert (await FeedbackCard.get(card["id"])).cluster_id == second["id"]


@pytest.mark.asyncio
async def test_move_card_to_a_cluster_that_is_not_on_this_retro(
    client, auth_headers, clustering_retro
):
    card = clustering_retro["cards"][0]

    resp = await _move(client, card["id"], UNKNOWN_CLUSTER_ID, auth_headers)
    assert resp.status_code == 404, resp.text
    assert (await FeedbackCard.get(card["id"])).cluster_id is None


@pytest.mark.asyncio
async def test_move_a_card_that_belongs_to_a_different_cycle(
    client, auth_headers, clustering_retro, add_card, reveal, advance_phase
):
    """A cluster only groups cards from its own cycle, so this is a 404."""
    cluster = await _create_cluster(
        client, clustering_retro["retro"]["id"], auth_headers, name="Flow"
    )

    # A project holds one open cycle at a time, so the first one has to close.
    closed = await client.patch(
        f"/api/cycles/{clustering_retro['cycle']['id']}",
        json={"status": "closed"},
        headers=auth_headers,
    )
    assert closed.status_code == 200, closed.text
    later = await client.post(
        f"/api/projects/{clustering_retro['cycle']['project_id']}/cycles", headers=auth_headers
    )
    assert later.status_code == 201, later.text
    later_cycle = later.json()

    stranger = await add_card(later_cycle["id"], text="from the next cycle")
    later_retro = await reveal(later_cycle["id"])
    await advance_phase(later_retro["id"], "cluster")

    resp = await _move(client, stranger["id"], cluster["id"], auth_headers)
    assert resp.status_code == 404, resp.text
    assert (await FeedbackCard.get(stranger["id"])).cluster_id is None


@pytest.mark.asyncio
async def test_unknown_cluster_id_on_rename_delete_and_move(
    client, auth_headers, clustering_retro
):
    retro = clustering_retro["retro"]
    card = clustering_retro["cards"][0]
    responses = [
        await client.patch(
            f"/api/retros/{retro['id']}/clusters/{UNKNOWN_CLUSTER_ID}",
            json={"name": "Flow"},
            headers=auth_headers,
        ),
        await client.delete(
            f"/api/retros/{retro['id']}/clusters/{UNKNOWN_CLUSTER_ID}", headers=auth_headers
        ),
        await _move(client, card["id"], UNKNOWN_CLUSTER_ID, auth_headers),
    ]
    for resp in responses:
        assert resp.status_code == 404, resp.text


@pytest.mark.asyncio
async def test_any_member_can_cluster_not_only_the_facilitator(
    client, second_auth_headers, clustering_retro
):
    retro = clustering_retro["retro"]
    card = clustering_retro["cards"][0]

    created = await client.post(
        f"/api/retros/{retro['id']}/clusters",
        json={"name": "Flow"},
        headers=second_auth_headers,
    )
    assert created.status_code == 201, created.text
    cluster = created.json()

    renamed = await client.patch(
        f"/api/retros/{retro['id']}/clusters/{cluster['id']}",
        json={"name": "Delivery flow"},
        headers=second_auth_headers,
    )
    assert renamed.status_code == 200, renamed.text

    moved = await _move(client, card["id"], cluster["id"], second_auth_headers)
    assert moved.status_code == 200, moved.text

    deleted = await client.delete(
        f"/api/retros/{retro['id']}/clusters/{cluster['id']}", headers=second_auth_headers
    )
    assert deleted.status_code == 200, deleted.text


@pytest.mark.asyncio
async def test_a_member_may_move_a_card_written_by_someone_else(
    client, auth_headers, second_auth_headers, clustering_retro
):
    """Cards are frozen to their authors at reveal; clustering is exempt."""
    retro = clustering_retro["retro"]
    alices_card = clustering_retro["cards"][0]
    cluster = await _create_cluster(client, retro["id"], second_auth_headers, name="Flow")

    resp = await _move(client, alices_card["id"], cluster["id"], second_auth_headers)
    assert resp.status_code == 200, resp.text
    assert (await FeedbackCard.get(alices_card["id"])).cluster_id == cluster["id"]


@pytest.mark.asyncio
async def test_every_endpoint_is_refused_in_the_reveal_phase(
    client, auth_headers, shared_cycle, add_card, reveal
):
    card = await add_card(shared_cycle["id"])
    retro = await reveal(shared_cycle["id"])

    responses = await _call_every_endpoint(
        client, retro["id"], str(uuid4()), card["id"], auth_headers
    )
    for resp in responses:
        assert resp.status_code == 400, resp.text


@pytest.mark.asyncio
async def test_every_endpoint_is_refused_in_the_vote_phase(
    client, auth_headers, clustering_retro, advance_phase
):
    retro = clustering_retro["retro"]
    card = clustering_retro["cards"][0]
    cluster = await _create_cluster(client, retro["id"], auth_headers, name="Flow")
    await advance_phase(retro["id"], "vote")

    responses = await _call_every_endpoint(
        client, retro["id"], cluster["id"], card["id"], auth_headers
    )
    for resp in responses:
        assert resp.status_code == 400, resp.text


@pytest.mark.asyncio
async def test_non_members_are_refused_on_every_endpoint(
    client, auth_headers, outsider_auth_headers, clustering_retro
):
    retro = clustering_retro["retro"]
    card = clustering_retro["cards"][0]
    cluster = await _create_cluster(client, retro["id"], auth_headers, name="Flow")

    responses = await _call_every_endpoint(
        client, retro["id"], cluster["id"], card["id"], outsider_auth_headers
    )
    for resp in responses:
        assert resp.status_code == 403, resp.text


@pytest.mark.asyncio
async def test_unknown_and_malformed_ids(client, auth_headers, clustering_retro):
    retro = clustering_retro["retro"]
    card = clustering_retro["cards"][0]
    cases = [
        # Unknown and malformed retro ids.
        *await _call_every_endpoint(client, UNKNOWN_ID, str(uuid4()), card["id"], auth_headers),
        *await _call_every_endpoint(client, "abc", str(uuid4()), card["id"], auth_headers),
        # Unknown and malformed cluster ids, on a retro that does exist.
        await client.patch(
            f"/api/retros/{retro['id']}/clusters/abc",
            json={"name": "Flow"},
            headers=auth_headers,
        ),
        await client.delete(f"/api/retros/{retro['id']}/clusters/abc", headers=auth_headers),
        await _move(client, card["id"], "abc", auth_headers),
        # Unknown and malformed card ids.
        await _move(client, UNKNOWN_ID, None, auth_headers),
        await _move(client, "abc", None, auth_headers),
    ]
    for resp in cases:
        assert resp.status_code == 404, resp.text


@pytest.mark.asyncio
async def test_endpoints_require_authentication(client, auth_headers, clustering_retro):
    retro = clustering_retro["retro"]
    card = clustering_retro["cards"][0]
    cluster = await _create_cluster(client, retro["id"], auth_headers, name="Flow")

    responses = await _call_every_endpoint(client, retro["id"], cluster["id"], card["id"])
    for resp in responses:
        assert resp.status_code in (401, 403), resp.text

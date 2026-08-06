import pytest

from app.models.cycle import Cycle
from app.models.retro import PHASE_ORDER

UNKNOWN_ID = "507f1f77bcf86cd799439011"


async def _advance(client, retro_id, phase, headers):
    return await client.patch(
        f"/api/retros/{retro_id}/phase", json={"phase": phase}, headers=headers
    )


async def _add_card(client, cycle_id, headers, **overrides):
    payload = {"category": "start", "text": "pair more often"}
    payload.update(overrides)
    return await client.post(f"/api/cycles/{cycle_id}/feedback", json=payload, headers=headers)


@pytest.mark.asyncio
async def test_start_retro(client, auth_headers, cycle):
    resp = await client.post(f"/api/cycles/{cycle['id']}/retro", headers=auth_headers)
    assert resp.status_code == 201
    data = resp.json()
    assert data["phase"] == "reveal"
    assert data["cycle_id"] == cycle["id"]
    assert data["clusters"] == []
    assert data["votes"] == []
    assert data["topics"] == []
    assert data["decisions"] == []
    assert data["actions"] == []
    assert data["transcript"] is None
    assert data["ai_suggestions"] is None


@pytest.mark.asyncio
async def test_starting_the_retro_moves_the_cycle_to_retro(client, auth_headers, cycle):
    await client.post(f"/api/cycles/{cycle['id']}/retro", headers=auth_headers)

    stored = await Cycle.get(cycle["id"])
    assert stored.status == "retro"


@pytest.mark.asyncio
async def test_start_retro_twice(client, auth_headers, cycle):
    first = await client.post(f"/api/cycles/{cycle['id']}/retro", headers=auth_headers)
    assert first.status_code == 201
    second = await client.post(f"/api/cycles/{cycle['id']}/retro", headers=auth_headers)
    assert second.status_code == 409


@pytest.mark.asyncio
async def test_start_retro_on_a_closed_cycle(client, auth_headers, cycle):
    await client.patch(
        f"/api/cycles/{cycle['id']}", json={"status": "closed"}, headers=auth_headers
    )
    resp = await client.post(f"/api/cycles/{cycle['id']}/retro", headers=auth_headers)
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_start_retro_as_plain_member(
    client, auth_headers, second_auth_headers, shared_cycle
):
    resp = await client.post(
        f"/api/cycles/{shared_cycle['id']}/retro", headers=second_auth_headers
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_start_retro_as_non_member(client, second_auth_headers, cycle, second_user):
    resp = await client.post(f"/api/cycles/{cycle['id']}/retro", headers=second_auth_headers)
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_phase_advances_one_step_at_a_time(client, auth_headers, cycle, reveal):
    retro = await reveal(cycle["id"])

    for current, following in zip(PHASE_ORDER, PHASE_ORDER[1:]):
        resp = await _advance(client, retro["id"], following, auth_headers)
        assert resp.status_code == 200, f"{current} -> {following} should be allowed"
        assert resp.json()["phase"] == following


@pytest.mark.asyncio
async def test_phase_cannot_skip(client, auth_headers, cycle, reveal):
    retro = await reveal(cycle["id"])
    resp = await _advance(client, retro["id"], "vote", auth_headers)
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_phase_cannot_go_backward(client, auth_headers, cycle, reveal):
    retro = await reveal(cycle["id"])
    await _advance(client, retro["id"], "cluster", auth_headers)

    resp = await _advance(client, retro["id"], "reveal", auth_headers)
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_phase_cannot_repeat_itself(client, auth_headers, cycle, reveal):
    retro = await reveal(cycle["id"])
    resp = await _advance(client, retro["id"], "reveal", auth_headers)
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_phase_cannot_advance_past_done(client, auth_headers, cycle, reveal):
    retro = await reveal(cycle["id"])
    for phase in PHASE_ORDER[1:]:
        await _advance(client, retro["id"], phase, auth_headers)

    for phase in PHASE_ORDER:
        resp = await _advance(client, retro["id"], phase, auth_headers)
        assert resp.status_code == 400, f"nothing may follow done, tried {phase}"


@pytest.mark.asyncio
async def test_unknown_phase_name(client, auth_headers, cycle, reveal):
    retro = await reveal(cycle["id"])
    resp = await _advance(client, retro["id"], "retrospecting", auth_headers)
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_advance_phase_as_plain_member(
    client, auth_headers, second_auth_headers, shared_cycle, reveal
):
    retro = await reveal(shared_cycle["id"])
    resp = await _advance(client, retro["id"], "cluster", second_auth_headers)
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_get_retro_as_member(
    client, auth_headers, second_auth_headers, shared_cycle, reveal
):
    retro = await reveal(shared_cycle["id"])
    resp = await client.get(f"/api/retros/{retro['id']}", headers=second_auth_headers)
    assert resp.status_code == 200
    assert resp.json()["id"] == retro["id"]


@pytest.mark.asyncio
async def test_get_retro_as_non_member(
    client, auth_headers, second_auth_headers, cycle, second_user, reveal
):
    retro = await reveal(cycle["id"])
    resp = await client.get(f"/api/retros/{retro['id']}", headers=second_auth_headers)
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_cards_become_visible_to_everyone_at_reveal(
    client, auth_headers, second_auth_headers, shared_cycle, reveal
):
    await _add_card(client, shared_cycle["id"], auth_headers, text="alice card")
    await _add_card(client, shared_cycle["id"], second_auth_headers, text="bob card")

    before = await client.get(
        f"/api/cycles/{shared_cycle['id']}/feedback", headers=second_auth_headers
    )
    assert [c["text"] for c in before.json()] == ["bob card"]

    await reveal(shared_cycle["id"])

    after = await client.get(
        f"/api/cycles/{shared_cycle['id']}/feedback", headers=second_auth_headers
    )
    assert sorted(c["text"] for c in after.json()) == ["alice card", "bob card"]


@pytest.mark.asyncio
async def test_cards_freeze_at_reveal(client, auth_headers, cycle, reveal):
    created = await _add_card(client, cycle["id"], auth_headers)
    card_id = created.json()["id"]
    await reveal(cycle["id"])

    patch_resp = await client.patch(
        f"/api/feedback/{card_id}", json={"text": "reworded after the fact"}, headers=auth_headers
    )
    delete_resp = await client.delete(f"/api/feedback/{card_id}", headers=auth_headers)
    assert patch_resp.status_code == 400
    assert delete_resp.status_code == 400


@pytest.mark.asyncio
async def test_cards_stay_frozen_when_the_cycle_closes(client, auth_headers, cycle, reveal):
    created = await _add_card(client, cycle["id"], auth_headers)
    card_id = created.json()["id"]
    await reveal(cycle["id"])
    await client.patch(
        f"/api/cycles/{cycle['id']}", json={"status": "closed"}, headers=auth_headers
    )

    patch_resp = await client.patch(
        f"/api/feedback/{card_id}", json={"text": "too late"}, headers=auth_headers
    )
    delete_resp = await client.delete(f"/api/feedback/{card_id}", headers=auth_headers)
    assert patch_resp.status_code == 400
    assert delete_resp.status_code == 400


@pytest.mark.asyncio
async def test_frozen_anonymous_card_is_still_refused_as_forbidden(
    client, auth_headers, cycle, reveal
):
    created = await _add_card(client, cycle["id"], auth_headers, is_anonymous=True)
    card_id = created.json()["id"]
    await reveal(cycle["id"])

    resp = await client.patch(
        f"/api/feedback/{card_id}", json={"text": "x"}, headers=auth_headers
    )
    assert resp.status_code == 403, "anonymity is why it is refused, not the freeze"


@pytest.mark.asyncio
async def test_unknown_and_malformed_ids(client, auth_headers, cycle, reveal):
    retro = await reveal(cycle["id"])
    cases = [
        client.post(f"/api/cycles/{UNKNOWN_ID}/retro", headers=auth_headers),
        client.post("/api/cycles/abc/retro", headers=auth_headers),
        client.get(f"/api/retros/{UNKNOWN_ID}", headers=auth_headers),
        client.get("/api/retros/abc", headers=auth_headers),
        client.patch(
            f"/api/retros/{UNKNOWN_ID}/phase", json={"phase": "cluster"}, headers=auth_headers
        ),
        client.patch("/api/retros/abc/phase", json={"phase": "cluster"}, headers=auth_headers),
    ]
    for call in cases:
        resp = await call
        assert resp.status_code == 404


@pytest.mark.asyncio
async def test_endpoints_require_authentication(client, auth_headers, cycle, reveal):
    retro = await reveal(cycle["id"])
    unauthenticated = [
        client.post(f"/api/cycles/{cycle['id']}/retro"),
        client.get(f"/api/retros/{retro['id']}"),
        client.patch(f"/api/retros/{retro['id']}/phase", json={"phase": "cluster"}),
    ]
    for call in unauthenticated:
        resp = await call
        assert resp.status_code in (401, 403)

import pytest

from app.models.feedback import FeedbackCard

UNKNOWN_ID = "507f1f77bcf86cd799439011"


async def _add(client, cycle_id, headers, **overrides):
    payload = {"category": "start", "text": "pair more often"}
    payload.update(overrides)
    return await client.post(f"/api/cycles/{cycle_id}/feedback", json=payload, headers=headers)


@pytest.mark.asyncio
async def test_create_card(client, auth_headers, cycle, registered_user):
    resp = await _add(client, cycle["id"], auth_headers)
    assert resp.status_code == 201
    data = resp.json()
    assert data["category"] == "start"
    assert data["text"] == "pair more often"
    assert data["author_id"] == registered_user["id"]
    assert data["is_anonymous"] is False
    assert data["cluster_id"] is None


@pytest.mark.asyncio
async def test_all_three_categories(client, auth_headers, cycle):
    for category in ["start", "stop", "continue"]:
        resp = await _add(client, cycle["id"], auth_headers, category=category)
        assert resp.status_code == 201
        assert resp.json()["category"] == category


@pytest.mark.asyncio
async def test_invalid_category(client, auth_headers, cycle):
    resp = await _add(client, cycle["id"], auth_headers, category="maybe")
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_blank_text(client, auth_headers, cycle):
    for text in ["", "   "]:
        resp = await _add(client, cycle["id"], auth_headers, text=text)
        assert resp.status_code == 422, f"blank text {text!r} should be rejected"


@pytest.mark.asyncio
async def test_missing_text(client, auth_headers, cycle):
    resp = await client.post(
        f"/api/cycles/{cycle['id']}/feedback", json={"category": "start"}, headers=auth_headers
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_anonymous_card_has_no_author_in_the_database(client, auth_headers, cycle):
    resp = await _add(client, cycle["id"], auth_headers, is_anonymous=True)
    assert resp.status_code == 201
    assert resp.json()["author_id"] is None

    stored = await FeedbackCard.get(resp.json()["id"])
    assert stored.author_id is None, "anonymity must hold in the document, not just the response"
    assert stored.is_anonymous is True


@pytest.mark.asyncio
async def test_create_card_when_cycle_is_in_retro(client, auth_headers, cycle, reveal):
    await reveal(cycle["id"])
    resp = await _add(client, cycle["id"], auth_headers)
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_create_card_when_cycle_is_closed(client, auth_headers, cycle):
    await client.patch(
        f"/api/cycles/{cycle['id']}", json={"status": "closed"}, headers=auth_headers
    )
    resp = await _add(client, cycle["id"], auth_headers)
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_create_card_as_non_member(client, second_auth_headers, cycle, second_user):
    resp = await _add(client, cycle["id"], second_auth_headers)
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_collecting_phase_hides_other_members_cards(
    client, auth_headers, second_auth_headers, shared_cycle
):
    await _add(client, shared_cycle["id"], auth_headers, text="alice card")
    await _add(client, shared_cycle["id"], second_auth_headers, text="bob card")
    await _add(client, shared_cycle["id"], second_auth_headers, text="bob secret", is_anonymous=True)

    resp = await client.get(f"/api/cycles/{shared_cycle['id']}/feedback", headers=auth_headers)
    assert resp.status_code == 200
    texts = [c["text"] for c in resp.json()]
    assert texts == ["alice card"]


@pytest.mark.asyncio
async def test_reveal_shows_every_members_cards(
    client, auth_headers, second_auth_headers, shared_cycle, reveal
):
    await _add(client, shared_cycle["id"], auth_headers, text="alice card")
    await _add(client, shared_cycle["id"], second_auth_headers, text="bob card")
    await reveal(shared_cycle["id"])

    resp = await client.get(f"/api/cycles/{shared_cycle['id']}/feedback", headers=auth_headers)
    assert resp.status_code == 200
    texts = sorted(c["text"] for c in resp.json())
    assert texts == ["alice card", "bob card"]


@pytest.mark.asyncio
async def test_revealed_anonymous_card_names_nobody(
    client, auth_headers, second_auth_headers, shared_cycle, reveal
):
    await _add(
        client, shared_cycle["id"], second_auth_headers, text="bob secret", is_anonymous=True
    )
    await reveal(shared_cycle["id"])

    resp = await client.get(f"/api/cycles/{shared_cycle['id']}/feedback", headers=auth_headers)
    card = [c for c in resp.json() if c["text"] == "bob secret"][0]
    assert card["author_id"] is None
    assert card["is_anonymous"] is True


@pytest.mark.asyncio
async def test_list_cards_as_non_member(client, second_auth_headers, cycle, second_user):
    resp = await client.get(f"/api/cycles/{cycle['id']}/feedback", headers=second_auth_headers)
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_edit_own_card(client, auth_headers, cycle):
    created = await _add(client, cycle["id"], auth_headers)
    resp = await client.patch(
        f"/api/feedback/{created.json()['id']}",
        json={"text": "pair every morning"},
        headers=auth_headers,
    )
    assert resp.status_code == 200
    assert resp.json()["text"] == "pair every morning"


@pytest.mark.asyncio
async def test_edit_card_as_non_author(
    client, auth_headers, second_auth_headers, shared_cycle
):
    created = await _add(client, shared_cycle["id"], auth_headers)
    resp = await client.patch(
        f"/api/feedback/{created.json()['id']}",
        json={"text": "hijacked"},
        headers=second_auth_headers,
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_edit_an_anonymous_card_is_refused(client, auth_headers, cycle):
    created = await _add(client, cycle["id"], auth_headers, is_anonymous=True)
    resp = await client.patch(
        f"/api/feedback/{created.json()['id']}", json={"text": "nope"}, headers=auth_headers
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_making_a_card_anonymous_is_one_way(client, auth_headers, cycle):
    created = await _add(client, cycle["id"], auth_headers)
    card_id = created.json()["id"]

    resp = await client.patch(
        f"/api/feedback/{card_id}", json={"is_anonymous": True}, headers=auth_headers
    )
    assert resp.status_code == 200
    assert resp.json()["author_id"] is None

    stored = await FeedbackCard.get(card_id)
    assert stored.author_id is None

    again = await client.patch(
        f"/api/feedback/{card_id}", json={"text": "changed my mind"}, headers=auth_headers
    )
    assert again.status_code == 403


@pytest.mark.asyncio
async def test_delete_own_card(client, auth_headers, cycle):
    created = await _add(client, cycle["id"], auth_headers)
    card_id = created.json()["id"]

    resp = await client.delete(f"/api/feedback/{card_id}", headers=auth_headers)
    assert resp.status_code == 204
    assert await FeedbackCard.get(card_id) is None


@pytest.mark.asyncio
async def test_delete_card_as_non_author(
    client, auth_headers, second_auth_headers, shared_cycle
):
    created = await _add(client, shared_cycle["id"], auth_headers)
    resp = await client.delete(
        f"/api/feedback/{created.json()['id']}", headers=second_auth_headers
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_delete_an_anonymous_card_is_refused(client, auth_headers, cycle):
    created = await _add(client, cycle["id"], auth_headers, is_anonymous=True)
    resp = await client.delete(f"/api/feedback/{created.json()['id']}", headers=auth_headers)
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_edit_and_delete_as_non_member(
    client, auth_headers, second_auth_headers, cycle, second_user
):
    created = await _add(client, cycle["id"], auth_headers)
    card_id = created.json()["id"]

    patch_resp = await client.patch(
        f"/api/feedback/{card_id}", json={"text": "x"}, headers=second_auth_headers
    )
    delete_resp = await client.delete(f"/api/feedback/{card_id}", headers=second_auth_headers)
    assert patch_resp.status_code == 403
    assert delete_resp.status_code == 403


@pytest.mark.asyncio
async def test_unknown_and_malformed_ids(client, auth_headers):
    cases = [
        client.post(
            f"/api/cycles/{UNKNOWN_ID}/feedback",
            json={"category": "start", "text": "x"},
            headers=auth_headers,
        ),
        client.post(
            "/api/cycles/abc/feedback",
            json={"category": "start", "text": "x"},
            headers=auth_headers,
        ),
        client.get(f"/api/cycles/{UNKNOWN_ID}/feedback", headers=auth_headers),
        client.get("/api/cycles/abc/feedback", headers=auth_headers),
        client.patch(f"/api/feedback/{UNKNOWN_ID}", json={"text": "x"}, headers=auth_headers),
        client.patch("/api/feedback/abc", json={"text": "x"}, headers=auth_headers),
        client.delete(f"/api/feedback/{UNKNOWN_ID}", headers=auth_headers),
        client.delete("/api/feedback/abc", headers=auth_headers),
    ]
    for call in cases:
        resp = await call
        assert resp.status_code == 404


@pytest.mark.asyncio
async def test_endpoints_require_authentication(client, auth_headers, cycle):
    created = await _add(client, cycle["id"], auth_headers)
    card_id = created.json()["id"]

    unauthenticated = [
        client.post(
            f"/api/cycles/{cycle['id']}/feedback", json={"category": "start", "text": "x"}
        ),
        client.get(f"/api/cycles/{cycle['id']}/feedback"),
        client.patch(f"/api/feedback/{card_id}", json={"text": "x"}),
        client.delete(f"/api/feedback/{card_id}"),
    ]
    for call in unauthenticated:
        resp = await call
        assert resp.status_code in (401, 403)

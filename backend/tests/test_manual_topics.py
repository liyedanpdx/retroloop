"""按手工塑造议程:增、改名、排序、删 (#22)。

#9 生成的那份仍然是起点,这里只管之后的事。四个当初被推迟的难点各有一组断言:
`rank` 和 `vote_count` 分家、改名走覆盖而不是第二份真相、手工 topic 没有 cluster、
删除时把关联项 orphan 掉而不是级联删除。
"""

import pytest

from app.models.retro import Retrospective

UNKNOWN_ID = "507f1f77bcf86cd799439011"


def _url(retro_id: str) -> str:
    return f"/api/retros/{retro_id}/topics"


async def _topics(client, retro_id, headers):
    response = await client.get(f"/api/retros/{retro_id}", headers=headers)
    assert response.status_code == 200, response.text
    return response.json()["topics"]


# --- 新增 ---------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_topic_nobody_wrote_a_card_for(client, auth_headers, discussion_retro):
    retro_id = discussion_retro["retro"]["id"]
    before = len(await _topics(client, retro_id, auth_headers))

    response = await client.post(
        _url(retro_id), json={"name": "  On-call handover  "}, headers=auth_headers
    )

    assert response.status_code == 201, response.text
    created = response.json()
    assert created["name"] == "On-call handover", "两端空白被去掉"
    assert created["vote_count"] == 0, "它没有被投过票,而且说了实话"
    assert created["cluster_id"] is None
    assert created["status"] == "pending"
    assert created["rank"] == before + 1, "默认排在最后"


@pytest.mark.asyncio
async def test_a_new_topic_can_be_placed(client, auth_headers, discussion_retro):
    retro_id = discussion_retro["retro"]["id"]

    response = await client.post(
        _url(retro_id), json={"name": "First thing", "rank": 1}, headers=auth_headers
    )

    assert response.status_code == 201
    topics = await _topics(client, retro_id, auth_headers)
    assert topics[0]["name"] == "First thing"
    assert [topic["rank"] for topic in topics] == list(range(1, len(topics) + 1)), "1..n 连续"


@pytest.mark.asyncio
async def test_a_blank_name_is_refused(client, auth_headers, discussion_retro):
    retro_id = discussion_retro["retro"]["id"]
    before = await _topics(client, retro_id, auth_headers)

    assert (
        await client.post(_url(retro_id), json={"name": "   "}, headers=auth_headers)
    ).status_code == 422
    assert (await client.post(_url(retro_id), json={}, headers=auth_headers)).status_code == 422
    assert await _topics(client, retro_id, auth_headers) == before


# --- 改名 ---------------------------------------------------------------------


@pytest.mark.asyncio
async def test_renaming_is_an_override_that_can_be_cleared(
    client, auth_headers, discussion_retro
):
    """一个 topic 和它的 cluster 不会各说各话:覆盖在,就是覆盖说了算。"""
    retro_id = discussion_retro["retro"]["id"]
    topic = discussion_retro["topics"][0]
    original = topic["name"]

    renamed = await client.patch(
        f"{_url(retro_id)}/{topic['id']}", json={"name": "  Flow, properly  "}, headers=auth_headers
    )
    assert renamed.status_code == 200, renamed.text
    assert renamed.json()["name"] == "Flow, properly"

    stored = await Retrospective.get(retro_id)
    kept = next(row for row in stored.topics if row.id == topic["id"])
    assert kept.name_override == "Flow, properly"
    assert kept.cluster_id is not None, "cluster 没有被动过"

    cleared = await client.patch(
        f"{_url(retro_id)}/{topic['id']}", json={"name": None}, headers=auth_headers
    )
    assert cleared.status_code == 200
    assert cleared.json()["name"] == original, "清掉覆盖就回到 cluster 的名字"


@pytest.mark.asyncio
async def test_a_manual_topic_cannot_have_its_only_name_cleared(
    client, auth_headers, discussion_retro
):
    retro_id = discussion_retro["retro"]["id"]
    created = await client.post(
        _url(retro_id), json={"name": "On-call handover"}, headers=auth_headers
    )

    response = await client.patch(
        f"{_url(retro_id)}/{created.json()['id']}", json={"name": None}, headers=auth_headers
    )

    assert response.status_code == 400
    assert "needs a name" in response.json()["detail"]
    stored = await Retrospective.get(retro_id)
    assert next(
        row for row in stored.topics if row.id == created.json()["id"]
    ).name_override == "On-call handover"


# --- 排序 ---------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reordering_moves_one_and_closes_the_gap(
    client, auth_headers, discussion_retro
):
    retro_id = discussion_retro["retro"]["id"]
    names = [topic["name"] for topic in await _topics(client, retro_id, auth_headers)]
    assert len(names) >= 3, "这个 fixture 有三个 topic"
    last = (await _topics(client, retro_id, auth_headers))[-1]

    response = await client.patch(
        f"{_url(retro_id)}/{last['id']}", json={"rank": 1}, headers=auth_headers
    )
    assert response.status_code == 200, response.text

    after = await _topics(client, retro_id, auth_headers)
    assert [topic["name"] for topic in after] == [names[-1], *names[:-1]]
    assert [topic["rank"] for topic in after] == list(range(1, len(after) + 1))


@pytest.mark.asyncio
async def test_the_vote_count_never_moves_with_the_agenda(
    client, auth_headers, discussion_retro
):
    """`rank` 现在是议程顺序,`vote_count` 还是那次计票的快照。"""
    retro_id = discussion_retro["retro"]["id"]
    before = {
        topic["name"]: topic["vote_count"]
        for topic in await _topics(client, retro_id, auth_headers)
    }
    last = (await _topics(client, retro_id, auth_headers))[-1]

    await client.patch(f"{_url(retro_id)}/{last['id']}", json={"rank": 1}, headers=auth_headers)

    after = {
        topic["name"]: topic["vote_count"]
        for topic in await _topics(client, retro_id, auth_headers)
    }
    assert after == before


@pytest.mark.asyncio
async def test_the_summary_follows_the_agenda_order(client, auth_headers, discussion_retro):
    retro_id = discussion_retro["retro"]["id"]
    last = (await _topics(client, retro_id, auth_headers))[-1]
    await client.patch(f"{_url(retro_id)}/{last['id']}", json={"rank": 1}, headers=auth_headers)

    summary = await client.get(f"/api/retros/{retro_id}/summary", headers=auth_headers)
    assert summary.status_code == 200, summary.text
    assert summary.json()["topics"][0]["name"] == last["name"]
    assert [row["rank"] for row in summary.json()["topics"]] == list(
        range(1, len(summary.json()["topics"]) + 1)
    )


@pytest.mark.asyncio
async def test_an_out_of_range_position_lands_at_the_end(
    client, auth_headers, discussion_retro
):
    retro_id = discussion_retro["retro"]["id"]
    first = (await _topics(client, retro_id, auth_headers))[0]

    response = await client.patch(
        f"{_url(retro_id)}/{first['id']}", json={"rank": 999}, headers=auth_headers
    )

    assert response.status_code == 200
    after = await _topics(client, retro_id, auth_headers)
    assert after[-1]["name"] == first["name"]
    assert [topic["rank"] for topic in after] == list(range(1, len(after) + 1))
    assert (
        await client.patch(
            f"{_url(retro_id)}/{first['id']}", json={"rank": 0}, headers=auth_headers
        )
    ).status_code == 422, "0 不是位置"


# --- 删除 ---------------------------------------------------------------------


@pytest.mark.asyncio
async def test_deleting_a_topic_unlinks_its_items_instead_of_deleting_them(
    client, auth_headers, discussion_retro
):
    retro_id = discussion_retro["retro"]["id"]
    topic = discussion_retro["topics"][0]
    decision = await client.post(
        f"/api/retros/{retro_id}/decisions",
        json={"topic_id": topic["id"], "text": "Ship it"},
        headers=auth_headers,
    )
    action = await client.post(
        f"/api/retros/{retro_id}/actions",
        json={"topic_id": topic["id"], "description": "Write it up"},
        headers=auth_headers,
    )

    response = await client.delete(f"{_url(retro_id)}/{topic['id']}", headers=auth_headers)
    assert response.status_code == 204

    stored = await Retrospective.get(retro_id)
    assert all(row.id != topic["id"] for row in stored.topics)
    kept_decision = next(row for row in stored.decisions if row.id == decision.json()["id"])
    kept_action = next(row for row in stored.actions if row.id == action.json()["id"])
    assert kept_decision.text == "Ship it", "团队定下的东西还在"
    assert kept_decision.topic_id is None, "只是不再挂在那个 topic 上"
    assert kept_action.topic_id is None
    assert [row.rank for row in stored.topics] == list(range(1, len(stored.topics) + 1))


@pytest.mark.asyncio
async def test_an_unlinked_item_shows_up_in_the_summary(
    client, auth_headers, discussion_retro
):
    retro_id = discussion_retro["retro"]["id"]
    topic = discussion_retro["topics"][0]
    decision = await client.post(
        f"/api/retros/{retro_id}/decisions",
        json={"topic_id": topic["id"], "text": "Ship it"},
        headers=auth_headers,
    )
    # #11 的 summary 只收已确认的 decision,所以这里先确认再删 topic。
    await client.patch(
        f"/api/retros/{retro_id}/decisions/{decision.json()['id']}",
        json={"is_confirmed": True},
        headers=auth_headers,
    )
    await client.delete(f"{_url(retro_id)}/{topic['id']}", headers=auth_headers)

    summary = await client.get(f"/api/retros/{retro_id}/summary", headers=auth_headers)
    orphan = next(row for row in summary.json()["decisions"] if row["text"] == "Ship it")
    assert orphan["topic"] is None, "#11 已经会把它渲染成 Unlinked"
    assert orphan["topic_id"] is None


@pytest.mark.asyncio
async def test_deleting_something_that_is_not_there_is_404(
    client, auth_headers, discussion_retro
):
    retro_id = discussion_retro["retro"]["id"]
    for target in ("nope", UNKNOWN_ID):
        assert (
            await client.delete(f"{_url(retro_id)}/{target}", headers=auth_headers)
        ).status_code == 404


# --- 权限和阶段 ----------------------------------------------------------------


@pytest.mark.asyncio
async def test_only_a_facilitator_in_discuss_may_shape_the_agenda(
    client, auth_headers, second_auth_headers, outsider_auth_headers, discussion_retro
):
    retro_id = discussion_retro["retro"]["id"]
    topic = discussion_retro["topics"][0]

    assert (await client.post(_url(retro_id), json={"name": "Mine"})).status_code == 401
    for headers in (second_auth_headers, outsider_auth_headers):
        assert (
            await client.post(_url(retro_id), json={"name": "Mine"}, headers=headers)
        ).status_code == 403
        assert (
            await client.delete(f"{_url(retro_id)}/{topic['id']}", headers=headers)
        ).status_code == 403

    retro = await Retrospective.get(retro_id)
    retro.phase = "vote"
    await retro.save()
    assert (
        await client.post(_url(retro_id), json={"name": "Mine"}, headers=auth_headers)
    ).status_code == 400
    assert (
        await client.delete(f"{_url(retro_id)}/{topic['id']}", headers=auth_headers)
    ).status_code == 400


@pytest.mark.asyncio
async def test_a_closed_cycle_refuses_both(client, auth_headers, discussion_retro):
    retro_id = discussion_retro["retro"]["id"]
    topic = discussion_retro["topics"][0]
    await client.patch(
        f"/api/cycles/{discussion_retro['cycle']['id']}",
        json={"status": "closed"},
        headers=auth_headers,
    )

    for response in (
        await client.post(_url(retro_id), json={"name": "Late"}, headers=auth_headers),
        await client.delete(f"{_url(retro_id)}/{topic['id']}", headers=auth_headers),
    ):
        assert response.status_code == 400
        assert response.json()["detail"] == "The retrospective's cycle is closed"

"""关闭 cycle 和并发写之间的原子性 (#34)。

这里没有一个 `sleep`。`pause_saves` 把一次 mutation 精确地停在它已经通过
#20 的顺序守卫、但还没提交的那一刻——也就是竞态真正存在的那个窗口——然后测试
在这个窗口里执行关闭,再放行。这样每一个场景都是确定性的,而不是碰运气。

每一个场景都断言两件事:输掉的一方拿到非成功结果,以及数据库里没有留下它的痕迹。
"""

import asyncio

import pytest

from app.models.cycle import CLOSED, Cycle
from app.models.retro import Retrospective
from app.services import concurrency

# `save_retro` 被这些模块各自导入了一份绑定,所以要一起换掉。
SAVE_BINDINGS = (
    "app.api.clusters",
    "app.api.discussion",
    "app.api.retros",
    "app.api.transcript",
    "app.api.votes",
    "app.services.transcript",
)


@pytest.fixture
def pause_saves(monkeypatch):
    """把 retro 的提交挡在闸门后面,直到测试放行。"""
    gate = asyncio.Event()
    reached = asyncio.Event()
    real = concurrency.save_retro

    async def paused(retro):
        reached.set()
        await gate.wait()
        return await real(retro)

    for module in SAVE_BINDINGS:
        monkeypatch.setattr(f"{module}.save_retro", paused)

    return {"gate": gate, "reached": reached}


async def _close_cycle(client, headers, cycle_id):
    response = await client.patch(
        f"/api/cycles/{cycle_id}", json={"status": "closed"}, headers=headers
    )
    assert response.status_code == 200, response.text


async def _race(pause_saves, mutation, close):
    """先让 mutation 停在提交前,执行关闭,再放行 mutation。"""
    task = asyncio.create_task(mutation())
    await asyncio.wait_for(pause_saves["reached"].wait(), timeout=5)

    await close()

    pause_saves["gate"].set()
    return await asyncio.wait_for(task, timeout=5)


# --- 关闭赢:每一类写都提交不进来 ------------------------------------------


@pytest.mark.asyncio
async def test_a_cluster_created_across_the_close_does_not_commit(
    client, auth_headers, clustering_retro, pause_saves
):
    retro_id = clustering_retro["retro"]["id"]

    response = await _race(
        pause_saves,
        lambda: client.post(
            f"/api/retros/{retro_id}/clusters", json={"name": "Flow"}, headers=auth_headers
        ),
        lambda: _close_cycle(client, auth_headers, clustering_retro["cycle"]["id"]),
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "The retrospective's cycle is closed"
    assert (await Retrospective.get(retro_id)).clusters == [], "什么也没写进去"


@pytest.mark.asyncio
async def test_a_phase_advance_across_the_close_does_not_commit(
    client, auth_headers, clustering_retro, pause_saves
):
    retro_id = clustering_retro["retro"]["id"]

    response = await _race(
        pause_saves,
        lambda: client.patch(
            f"/api/retros/{retro_id}/phase", json={"phase": "vote"}, headers=auth_headers
        ),
        lambda: _close_cycle(client, auth_headers, clustering_retro["cycle"]["id"]),
    )

    assert response.status_code == 400
    assert (await Retrospective.get(retro_id)).phase == "cluster"


@pytest.mark.asyncio
async def test_a_ballot_across_the_close_does_not_commit(
    client, second_auth_headers, auth_headers, voting_retro, pause_saves
):
    retro_id = voting_retro["retro"]["id"]

    response = await _race(
        pause_saves,
        lambda: client.post(
            f"/api/retros/{retro_id}/votes",
            json={"cluster_ids": [voting_retro["clusters"][0]["id"]]},
            headers=second_auth_headers,
        ),
        lambda: _close_cycle(client, auth_headers, voting_retro["cycle"]["id"]),
    )

    assert response.status_code == 400
    assert (await Retrospective.get(retro_id)).votes == []


@pytest.mark.asyncio
async def test_a_decision_across_the_close_does_not_commit(
    client, auth_headers, discussion_retro, pause_saves
):
    retro_id = discussion_retro["retro"]["id"]

    response = await _race(
        pause_saves,
        lambda: client.post(
            f"/api/retros/{retro_id}/decisions", json={"text": "Ship it"}, headers=auth_headers
        ),
        lambda: _close_cycle(client, auth_headers, discussion_retro["cycle"]["id"]),
    )

    assert response.status_code == 400
    assert (await Retrospective.get(retro_id)).decisions == []


@pytest.mark.asyncio
async def test_a_suggestion_confirm_across_the_close_does_not_commit(
    client, auth_headers, transcript_retro, pause_saves
):
    retro_id = transcript_retro["retro"]["id"]
    decision_id = transcript_retro["decision"]["id"]

    response = await _race(
        pause_saves,
        lambda: client.post(
            f"/api/retros/{retro_id}/suggestions/confirm",
            json={"decisions": [{"id": decision_id}]},
            headers=auth_headers,
        ),
        lambda: _close_cycle(client, auth_headers, transcript_retro["cycle"]["id"]),
    )

    assert response.status_code == 400
    stored = await Retrospective.get(retro_id)
    assert stored.decisions == [], "没有半个确认下来的决定"


# --- 后台抽取 ---------------------------------------------------------------


@pytest.mark.asyncio
async def test_an_extraction_finishing_after_the_close_writes_nothing(
    client, auth_headers, discussion_retro, ai_proxy, pause_saves
):
    """而且不会留下一个假装还在跑的任务。"""
    from app.services.transcript import processing_document, run_extraction

    retro_id = discussion_retro["retro"]["id"]
    ai_proxy.returns({"decisions": [{"text": "From the meeting"}], "actions": []})

    # 直接把 retro 摆成「抽取进行中」,而不是走 POST /transcript。走接口的话
    # 它的 BackgroundTask 会撞上下面这个闸门,而 httpx 要等后台任务结束才返回
    # 响应——测试会把自己锁死。
    stored = await Retrospective.get(retro_id)
    stored.transcript = "we agreed"
    stored.ai_suggestions = processing_document()
    await stored.save()

    await _race(
        pause_saves,
        lambda: run_extraction(discussion_retro["retro"]["id"]),
        lambda: _close_cycle(client, auth_headers, discussion_retro["cycle"]["id"]),
    )

    stored = await Retrospective.get(retro_id)
    assert stored.ai_suggestions["status"] == "failed", "关闭那一步终结了它"
    assert stored.ai_suggestions["decisions"] == [], "抽取的结果没有写进来"
    assert stored.decisions == []

    # #17 的轮询看到的是一个终态,不会永远转下去。
    suggestions = await client.get(f"/api/retros/{retro_id}/suggestions", headers=auth_headers)
    assert suggestions.json()["status"] == "failed"


# --- mutation 赢 ------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_mutation_that_commits_first_is_seen_by_the_close(
    client, auth_headers, clustering_retro
):
    retro_id = clustering_retro["retro"]["id"]
    created = await client.post(
        f"/api/retros/{retro_id}/clusters", json={"name": "Flow"}, headers=auth_headers
    )
    assert created.status_code == 201, created.text

    await _close_cycle(client, auth_headers, clustering_retro["cycle"]["id"])

    stored = await Retrospective.get(retro_id)
    assert [cluster.name for cluster in stored.clusters] == ["Flow"], "先提交的那次还在"
    assert stored.writes_closed_at is not None


# --- 发布 -------------------------------------------------------------------


@pytest.mark.asyncio
async def test_publish_and_a_concurrent_write_cannot_both_succeed(
    client, auth_headers, discussion_retro, pause_saves
):
    retro_id = discussion_retro["retro"]["id"]

    response = await _race(
        pause_saves,
        lambda: client.post(
            f"/api/retros/{retro_id}/decisions", json={"text": "Too late"}, headers=auth_headers
        ),
        lambda: client.post(f"/api/retros/{retro_id}/summary/publish", headers=auth_headers),
    )

    assert response.status_code == 400
    stored = await Retrospective.get(retro_id)
    assert stored.phase == "done"
    assert stored.decisions == []
    assert (await Cycle.get(stored.cycle_id)).status == CLOSED


@pytest.mark.asyncio
async def test_two_simultaneous_publishes_produce_one_200(
    client, auth_headers, discussion_retro
):
    """条件写决定的,不是先读后写决定的。"""
    retro_id = discussion_retro["retro"]["id"]

    first, second = await asyncio.gather(
        client.post(f"/api/retros/{retro_id}/summary/publish", headers=auth_headers),
        client.post(f"/api/retros/{retro_id}/summary/publish", headers=auth_headers),
    )

    assert sorted([first.status_code, second.status_code]) == [200, 400]
    stored = await Retrospective.get(retro_id)
    assert stored.phase == "done"
    cycle = await Cycle.get(stored.cycle_id)
    assert cycle.status == CLOSED
    assert cycle.closed_at is not None


@pytest.mark.asyncio
async def test_publish_never_reports_success_with_half_the_work_done(
    client, auth_headers, discussion_retro, monkeypatch
):
    """#11 的要求:cycle 没关成,就不能说发布成功。"""
    from app.models.cycle import Cycle as CycleModel

    retro_id = discussion_retro["retro"]["id"]

    async def refuse(self, *args, **kwargs):
        raise RuntimeError("the cycle write failed")

    monkeypatch.setattr(CycleModel, "save", refuse)

    with pytest.raises(RuntimeError):
        await client.post(f"/api/retros/{retro_id}/summary/publish", headers=auth_headers)

    # 回滚过了:retro 既没停在 done,也没停在封住的状态。
    stored = await Retrospective.get(retro_id)
    assert stored.phase == "discuss"
    assert stored.writes_closed_at is None

    monkeypatch.undo()
    again = await client.post(f"/api/retros/{retro_id}/summary/publish", headers=auth_headers)
    assert again.status_code == 200, "回滚之后还能正常发布"

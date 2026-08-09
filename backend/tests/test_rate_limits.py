"""限流:AI 预算和登录尝试 (#27)。

两件事值得先看。抽取和聚类建议共用同一个桶——只限其中一个只会把开销挪走,
不会限住。以及登录也在限流之内:给最贵的接口加限制、却让密码可以无限次猜,
是错误的优先级。
"""

from datetime import datetime, timedelta, timezone

import pytest

from app.config import settings
from app.models.rate_limit import RateLimitWindow
from app.models.retro import Retrospective


async def _stub_grouping(ai_proxy, cycle_id):
    """一个对这个周期合法的建议:所有卡片都不分组。

    #19 要求每张源卡恰好出现一次,所以空答案对一个有卡的 retro 是不合法的
    ——那会变成 502,而这个文件想测的是 429。
    """
    from beanie import PydanticObjectId

    from app.models.feedback import FeedbackCard

    cards = await FeedbackCard.find(
        FeedbackCard.cycle_id == PydanticObjectId(cycle_id)
    ).to_list()
    ai_proxy.returns(
        {"clusters": [], "ungrouped_card_ids": [str(card.id) for card in cards]}
    )


async def _age_the_window(bucket_prefix: str, minutes: int) -> None:
    """把窗口的起点往回拨,模拟时间流逝——不用 sleep。"""
    collection = RateLimitWindow.get_motor_collection()
    document = await collection.find_one({"_id": {"$regex": f"^{bucket_prefix}"}})
    assert document is not None, "预期已经有一个桶了"
    await collection.update_one(
        {"_id": document["_id"]},
        {"$set": {"window_start": datetime.now(timezone.utc) - timedelta(minutes=minutes)}},
    )


# --- AI 预算 ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_budget_is_shared_by_both_proxy_callers(
    client, auth_headers, monkeypatch, clustering_retro, ai_proxy
):
    """抽取和聚类建议共用一份账单,所以共用一个桶。"""
    monkeypatch.setattr(settings, "ai_calls_per_hour", 2)
    retro_id = clustering_retro["retro"]["id"]
    ai_proxy.returns({"clusters": [], "ungrouped_card_ids": []})

    # 前两次用在建议上。
    for _ in range(2):
        response = await client.post(
            f"/api/retros/{retro_id}/clusters/suggest", json={}, headers=auth_headers
        )
        assert response.status_code in (200, 502), response.text

    # 第三次换成抽取,额度已经被用光了。
    retro = await Retrospective.get(retro_id)
    retro.phase = "discuss"
    await retro.save()
    refused = await client.post(
        f"/api/retros/{retro_id}/transcript", json={"text": "we agreed"}, headers=auth_headers
    )

    assert refused.status_code == 429
    assert refused.json()["detail"] == "This project has made too many AI requests. Try again later."
    assert int(refused.headers["Retry-After"]) > 0
    assert (await Retrospective.get(retro_id)).transcript is None, "什么也没存"


@pytest.mark.asyncio
async def test_the_ceiling_is_per_project_not_per_retro(
    client, auth_headers, monkeypatch, project, cycle, add_card, reveal, advance_phase, ai_proxy
):
    """开一个新 retro 就能重置额度的话,这个限制是装饰品。"""
    monkeypatch.setattr(settings, "ai_calls_per_hour", 1)

    await add_card(cycle["id"])
    first = await reveal(cycle["id"])
    first = await advance_phase(first["id"], "cluster")
    await _stub_grouping(ai_proxy, cycle["id"])
    assert (
        await client.post(
            f"/api/retros/{first['id']}/clusters/suggest", json={}, headers=auth_headers
        )
    ).status_code == 200

    # 关掉这个周期,在同一个项目下再开一个。
    for phase in ("vote", "discuss"):
        first = await advance_phase(first["id"], phase)
    await client.post(f"/api/retros/{first['id']}/summary/publish", headers=auth_headers)
    second_cycle = await client.post(
        f"/api/projects/{project['id']}/cycles", headers=auth_headers
    )
    second = await reveal(second_cycle.json()["id"])
    second = await advance_phase(second["id"], "cluster")
    await _stub_grouping(ai_proxy, second_cycle.json()["id"])

    refused = await client.post(
        f"/api/retros/{second['id']}/clusters/suggest", json={}, headers=auth_headers
    )
    assert refused.status_code == 429


@pytest.mark.asyncio
async def test_the_window_moves_on(
    client, auth_headers, monkeypatch, clustering_retro, ai_proxy
):
    monkeypatch.setattr(settings, "ai_calls_per_hour", 1)
    retro_id = clustering_retro["retro"]["id"]
    await _stub_grouping(ai_proxy, clustering_retro["cycle"]["id"])

    assert (
        await client.post(
            f"/api/retros/{retro_id}/clusters/suggest", json={}, headers=auth_headers
        )
    ).status_code == 200
    assert (
        await client.post(
            f"/api/retros/{retro_id}/clusters/suggest", json={}, headers=auth_headers
        )
    ).status_code == 429

    await _age_the_window("ai:", minutes=61)

    assert (
        await client.post(
            f"/api/retros/{retro_id}/clusters/suggest", json={}, headers=auth_headers
        )
    ).status_code == 200, "新窗口开了"
    assert (
        await client.post(
            f"/api/retros/{retro_id}/clusters/suggest", json={}, headers=auth_headers
        )
    ).status_code == 429, "而且新窗口的第一次也算数"


@pytest.mark.asyncio
async def test_a_refused_request_never_reaches_the_proxy(
    client, auth_headers, monkeypatch, clustering_retro, ai_proxy
):
    monkeypatch.setattr(settings, "ai_calls_per_hour", 1)
    retro_id = clustering_retro["retro"]["id"]
    ai_proxy.returns({"clusters": [], "ungrouped_card_ids": []})

    await client.post(f"/api/retros/{retro_id}/clusters/suggest", json={}, headers=auth_headers)
    before = len(ai_proxy.calls)

    assert (
        await client.post(
            f"/api/retros/{retro_id}/clusters/suggest", json={}, headers=auth_headers
        )
    ).status_code == 429
    assert len(ai_proxy.calls) == before, "被挡下来的请求没有花钱"


@pytest.mark.asyncio
async def test_permission_and_phase_still_come_first(
    client, second_auth_headers, monkeypatch, clustering_retro
):
    """限流不该把一个本来就该被拒的请求变成 429——那会泄漏别人的用量。"""
    monkeypatch.setattr(settings, "ai_calls_per_hour", 0)
    retro_id = clustering_retro["retro"]["id"]

    refused = await client.post(
        f"/api/retros/{retro_id}/clusters/suggest", json={}, headers=second_auth_headers
    )
    assert refused.status_code == 403
    ai_buckets = await RateLimitWindow.get_motor_collection().count_documents(
        {"_id": {"$regex": "^ai:"}}
    )
    assert ai_buckets == 0, "没有记 AI 的账"


# --- 登录 ---------------------------------------------------------------------


@pytest.mark.asyncio
async def test_password_guessing_is_bounded(client, monkeypatch, registered_user):
    monkeypatch.setattr(settings, "login_attempts_per_15_minutes", 3)
    wrong = {"email": "alice@example.com", "password": "not-the-password"}

    for _ in range(3):
        assert (await client.post("/api/auth/login", json=wrong)).status_code == 401

    blocked = await client.post("/api/auth/login", json=wrong)
    assert blocked.status_code == 429
    assert blocked.json()["detail"] == (
        "Too many sign-in attempts for this account. Try again later."
    )
    assert int(blocked.headers["Retry-After"]) > 0

    # 就算密码对了也一样——不然限流只要猜对一次就绕过去了。
    right = {"email": "alice@example.com", "password": "secret123"}
    assert (await client.post("/api/auth/login", json=right)).status_code == 429

    await _age_the_window("login:", minutes=16)
    assert (await client.post("/api/auth/login", json=right)).status_code == 200


@pytest.mark.asyncio
async def test_one_account_being_attacked_does_not_lock_out_another(
    client, monkeypatch, registered_user, second_user
):
    """按邮箱而不是按 IP:一个办公室共用一个出口 IP。"""
    monkeypatch.setattr(settings, "login_attempts_per_15_minutes", 2)

    for _ in range(3):
        await client.post(
            "/api/auth/login", json={"email": "alice@example.com", "password": "wrong"}
        )

    assert (
        await client.post(
            "/api/auth/login", json={"email": "alice@example.com", "password": "secret123"}
        )
    ).status_code == 429
    assert (
        await client.post(
            "/api/auth/login", json={"email": "bob@example.com", "password": "secret456"}
        )
    ).status_code == 200, "bob 完全不受影响"


@pytest.mark.asyncio
async def test_the_email_is_matched_case_insensitively(client, monkeypatch, registered_user):
    """不然把邮箱换个大小写就是一个新桶。"""
    monkeypatch.setattr(settings, "login_attempts_per_15_minutes", 2)

    for email in ("alice@example.com", "ALICE@example.com", "Alice@Example.com"):
        await client.post("/api/auth/login", json={"email": email, "password": "wrong"})

    assert (
        await client.post(
            "/api/auth/login", json={"email": "alice@example.com", "password": "secret123"}
        )
    ).status_code == 429


@pytest.mark.asyncio
async def test_the_message_says_nothing_about_the_quota(client, monkeypatch, registered_user):
    """「还剩几次」正好是让人把脚本调到上限之下的信息。"""
    monkeypatch.setattr(settings, "login_attempts_per_15_minutes", 1)
    wrong = {"email": "alice@example.com", "password": "wrong"}

    await client.post("/api/auth/login", json=wrong)
    blocked = await client.post("/api/auth/login", json=wrong)

    assert blocked.status_code == 429
    body = blocked.text
    for leaked in ("1", "limit", "remaining", "quota", "15"):
        assert leaked not in body.lower(), leaked


@pytest.mark.asyncio
async def test_registering_and_refreshing_are_untouched(client, monkeypatch, registered_user):
    """这个 issue 限的是猜密码和烧预算,不是把所有接口都收紧。"""
    monkeypatch.setattr(settings, "login_attempts_per_15_minutes", 1)
    await client.post(
        "/api/auth/login", json={"email": "alice@example.com", "password": "wrong"}
    )

    for index in range(3):
        response = await client.post(
            "/api/auth/register",
            json={
                "email": f"new{index}@example.com",
                "password": "secret123",
                "display_name": "New",
            },
        )
        assert response.status_code == 201, response.text

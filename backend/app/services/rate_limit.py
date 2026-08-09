"""固定窗口限流,存在数据库里 (#27)。

`_docs/decisions.md` 记了为什么是这个形状。放在应用内而不是网关上,因为要限的
是「一个项目一小时能烧多少 AI 预算」——那是应用才知道的东西,网关只看得见 IP。
计数放在 MongoDB 而不是进程内存里,因为进程内的计数器在两个实例下等于没有,
而这个项目的部署形态还没定 (#33)。

一次 `find_one_and_update` 带 upsert,所以「读—判断—写」不会被两个并发请求各自
读到同一个旧值。窗口是固定窗口,不是滑动窗口:滑动窗口要留时间戳列表,而这里
要挡的是「有人按住重试按钮」和「有人拿脚本刷密码」,固定窗口对这两件事够用,
也不会随着流量增长而变大。
"""

from datetime import datetime, timedelta, timezone

from fastapi import HTTPException, status

from app.models.rate_limit import RateLimitWindow


class RateLimited(HTTPException):
    """429,带 Retry-After,不带任何关于配额的细节。

    `detail` 里不写「还剩几次」或者「上限是多少」——那正好是让人把脚本调到
    上限之下的信息。客户端要知道的只有「现在不行」和「多久之后再来」。
    """

    def __init__(self, retry_after: int, detail: str):
        super().__init__(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=detail,
            headers={"Retry-After": str(max(1, retry_after))},
        )


async def consume(bucket: str, limit: int, window: timedelta, detail: str) -> None:
    """记一次,超了就抛 429。

    窗口过期时把计数重置成 1 而不是 0:这次调用本身也要算进新窗口里,否则每个
    窗口的第一次调用都是免费的。
    """
    now = datetime.now(timezone.utc)
    collection = RateLimitWindow.get_motor_collection()

    # 窗口还在有效期内 → 计数 +1。
    fresh = await collection.find_one_and_update(
        {"_id": bucket, "window_start": {"$gt": now - window}},
        {"$inc": {"hits": 1}},
        return_document=True,
    )
    if fresh is None:
        # 要么这个桶没见过,要么它的窗口已经过期。两种情况都开一个新窗口,
        # 而 upsert 保证并发下只有一个能开成。
        await collection.update_one(
            {"_id": bucket},
            {"$set": {"hits": 1, "window_start": now}},
            upsert=True,
        )
        return

    if fresh["hits"] > limit:
        elapsed = (now - _aware(fresh["window_start"])).total_seconds()
        raise RateLimited(int(window.total_seconds() - elapsed), detail)


def _aware(value: datetime) -> datetime:
    """Motor 默认把 BSON 的 UTC 时间读回成 naive 值。"""
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)

"""每一次 retro 写入的条件提交,以及关闭它的那一次 (#34)。

`_docs/decisions.md` 记录了为什么是这个形状:这个项目的 MongoDB 是单机
(`hello.setName` 为 None),多文档事务用不了。于是「关闭 cycle」和「写 retro」
之间的原子性没法靠事务拿到——只能把「还能不能写」这件事挪进 retro 文档本身,
让每一次写的条件和它要改的数据落在同一个文档上。

两个函数,一个规则:

`save_retro` 替代了到处散布的 `retro.save()`。它是一次条件替换,条件是这个
retro 还没被封。#20 的顺序守卫仍然在,负责给出可读的 400;这里挡的是它已经
放行、却在提交前被并发关闭抢先的那一小段窗口。

`close_retro_writes` 是唯一能封它的东西,幂等,并且顺手终结在途的抽取任务——
否则 #17 的轮询会永远显示「正在抽取」,而那个任务的终态写已经再也提交不进来了。
"""

from datetime import datetime, timezone

from fastapi import HTTPException, status

from app.models.retro import Retrospective

CLOSED_DETAIL = "The retrospective's cycle is closed"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


async def save_retro(retro: Retrospective) -> None:
    """提交这次修改,除非中途有人把这个 retro 封了。

    输在这个条件上和被 #20 的顺序守卫挡下,对调用方是同一件事——请求没生效,
    数据没变——所以给的是同一个 400 和同一句话。
    """
    document = retro.model_dump(by_alias=True)
    result = await Retrospective.get_motor_collection().replace_one(
        {"_id": retro.id, "writes_closed_at": None},
        document,
    )
    if result.matched_count != 1:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=CLOSED_DETAIL)


async def close_retro_writes(
    retro: Retrospective,
    *,
    require_phase: str | None = None,
    set_phase: str | None = None,
) -> bool:
    """封住这个 retro,返回是否是这次调用封的。

    一次原子 update:条件是它还没被封。所以两个并发的关闭里只有一个拿到 True,
    而任何一个还没提交的 mutation 从这一刻起都会输掉 `save_retro` 的条件。

    同一次写里把在途的抽取标成 failed。那个后台任务的终态写马上就要被条件挡下,
    如果不在这里终结它,`ai_suggestions.status` 会永远停在 `processing`,
    #17 的轮询会一直转——这正是 #34 说的「不能留下一个假装还活着的任务」。
    """
    now = datetime.now(timezone.utc)
    condition: dict = {"_id": retro.id, "writes_closed_at": None}
    if require_phase is not None:
        # 发布用得到:「只能从 discuss 发布」和「只能发布一次」在这里合成一个
        # 原子条件,而不是先读后写的两步。
        condition["phase"] = require_phase

    changes: dict = {"writes_closed_at": now}
    if set_phase is not None:
        changes["phase"] = set_phase

    result = await Retrospective.get_motor_collection().update_one(
        condition,
        [
            {
                "$set": {
                    **changes,
                    "ai_suggestions": {
                        "$cond": [
                            {"$eq": ["$ai_suggestions.status", "processing"]},
                            {
                                "$mergeObjects": [
                                    "$ai_suggestions",
                                    {
                                        "status": "failed",
                                        "error": None,
                                        "completed_at": _now_iso(),
                                    },
                                ]
                            },
                            "$ai_suggestions",
                        ]
                    },
                }
            }
        ],
    )
    if result.modified_count == 1:
        retro.writes_closed_at = now
        if set_phase is not None:
            retro.phase = set_phase
        return True
    return False


async def reopen_retro_writes(retro: Retrospective, *, phase: str | None = None) -> None:
    """只有回滚会用到:关闭是一步一步做的,中途失败必须解封。

    抽取任务的终态不还原。它已经结束了,而且它结束的原因——终态写被条件挡下——
    在解封之后依然为真,谎称它还在跑没有任何好处。
    """
    changes: dict = {"writes_closed_at": None}
    if phase is not None:
        changes["phase"] = phase
    await Retrospective.get_motor_collection().update_one({"_id": retro.id}, {"$set": changes})
    retro.writes_closed_at = None
    if phase is not None:
        retro.phase = phase

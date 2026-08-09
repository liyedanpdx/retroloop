from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status

from app.deps import get_current_user
from app.models.cycle import CLOSED
from app.models.retro import DISCUSS, DONE
from app.models.user import User
from app.schemas.summary import SummaryResponse
from app.services.concurrency import close_retro_writes, reopen_retro_writes
from app.services.access import (
    get_retro_for_facilitator,
    get_retro_for_member,
    load_cycle,
    require_cycle_open,
)
from app.services.summary import assemble_summary
from app.services.votes import load_project_for_retro

router = APIRouter(prefix="/api", tags=["summary"])


@router.get("/retros/{retro_id}/summary", response_model=SummaryResponse)
async def get_summary(retro_id: str, user: User = Depends(get_current_user)):
    retro = await get_retro_for_member(retro_id, user)
    project = await load_project_for_retro(retro)
    if retro.phase == DISCUSS and not project.is_facilitator(user.id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only the facilitator can preview the summary",
        )
    if retro.phase not in (DISCUSS, DONE):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Summary is only available during discuss or done",
        )
    return await assemble_summary(retro, project)


@router.post("/retros/{retro_id}/summary/publish", response_model=SummaryResponse)
async def publish_summary(retro_id: str, user: User = Depends(get_current_user)):
    retro = await get_retro_for_facilitator(retro_id, user)
    if retro.phase != DISCUSS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Only a discussion can be published",
        )
    # Publishing is the write that closes the cycle, so it checks the cycle is
    # still open first and is therefore the only command #20 lets through once.
    # A cycle closed some other way — through PATCH /api/cycles/{id} — leaves a
    # retro in `discuss` that can no longer be published, which is correct: the
    # team ended the cycle, and publishing would reopen work on it.
    await require_cycle_open(retro)

    cycle = await load_cycle(str(retro.cycle_id))
    project = await load_project_for_retro(retro)

    # 先封 retro,再动 cycle (#34)。这一步是一次原子写,条件是它还在 discuss
    # 且还没被封,所以「只能从 discuss 发布」「只能发布一次」「不能和并发的写
    # 同时成功」三件事在这里一次决定完。输掉的那一方什么也没改。
    if not await close_retro_writes(retro, require_phase=DISCUSS, set_phase=DONE):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Only a discussion can be published",
        )

    previous_closed_at = cycle.closed_at
    previous_status = cycle.status
    cycle.status = CLOSED
    cycle.closed_at = datetime.now(timezone.utc)
    try:
        await cycle.save()
    except BaseException:
        # 两个文档里第二个没写成。回滚第一个,否则会留下一个封着的 retro
        # 配一个开着的 cycle——一个谁也读不出来的状态。
        await reopen_retro_writes(retro, phase=DISCUSS)
        cycle.status = previous_status
        cycle.closed_at = previous_closed_at
        raise

    return await assemble_summary(retro, project)

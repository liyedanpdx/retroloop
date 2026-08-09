from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status

from app.deps import get_current_user
from app.models.cycle import CLOSED
from app.models.retro import DISCUSS, DONE
from app.models.user import User
from app.schemas.summary import SummaryResponse
from app.services.access import get_retro_for_facilitator, get_retro_for_member, load_cycle
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

    cycle = await load_cycle(str(retro.cycle_id))
    project = await load_project_for_retro(retro)
    previous_closed_at = cycle.closed_at
    previous_status = cycle.status
    cycle.status = CLOSED
    cycle.closed_at = datetime.now(timezone.utc)
    await cycle.save()

    retro.phase = DONE
    try:
        await retro.save()
    except BaseException:
        cycle.status = previous_status
        cycle.closed_at = previous_closed_at
        await cycle.save()
        raise

    return await assemble_summary(retro, project)

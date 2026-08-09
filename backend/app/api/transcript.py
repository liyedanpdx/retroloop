"""Pasting a transcript, reading what the AI made of it, and confirming (#10).

All three endpoints are facilitator-only and `discuss`-only. Confirming lands
rows straight in `retro.decisions` and `retro.actions`, which #9 gates to the
facilitator in `discuss` — a transcript pasted in `vote`, or confirmed by a
plain member, would be a hole straight through that gate.

Check order is #9's facilitator order: membership+facilitator → phase → id
lookup, so a non-facilitator member in the wrong phase gets 403 and not 400.

The proxy is never called from here. `POST /transcript` stores the text, marks
the retro `processing` and hands the work to a background task, so a slow or
dead proxy cannot turn into a slow or failed request. The failure surfaces on
the read, as a failed status with one of three short error codes, on a 200 —
`_docs/decisions.md` has #17 polling this endpoint every two seconds and a
poller needs an answer, not an exception.

The 409 on a second paste while one is in flight is the only conflict in this
module, and it is only safe because the background task writes a terminal status
in a `finally` behind a hard timeout: nothing can wedge a retro on `processing`.
"""

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status

from app.deps import get_current_user
from app.models.retro import DISCUSS, Retrospective
from app.models.user import User
from app.schemas.transcript import (
    ConfirmRequest,
    ConfirmResponse,
    TranscriptAcceptedResponse,
    TranscriptRequest,
)
from app.services.ai_budget import consume_ai_budget
from app.services.concurrency import save_retro
from app.services.access import get_retro_for_facilitator, require_cycle_open, require_phase
from app.services.transcript import (
    PROCESSING,
    confirm_suggestions,
    is_processing,
    processing_document,
    run_extraction,
    suggestions_view,
)

router = APIRouter(prefix="/api", tags=["transcript"])


async def _facilitator_retro(retro_id: str, user: User) -> Retrospective:
    """Membership and role first, then phase — the same order #9 uses."""
    retro = await get_retro_for_facilitator(retro_id, user)
    require_phase(retro, DISCUSS)
    return retro


async def _writable_retro(retro_id: str, user: User) -> Retrospective:
    """The same, plus the cycle still being open (#20).

    Split from the reader above rather than folded into it: `GET /suggestions`
    has to keep working on a published retro, and a shared helper that closed
    the gate would have taken the read down with the writes.
    """
    retro = await _facilitator_retro(retro_id, user)
    await require_cycle_open(retro)
    return retro


@router.post(
    "/retros/{retro_id}/transcript",
    response_model=TranscriptAcceptedResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def paste_transcript(
    retro_id: str,
    body: TranscriptRequest,
    background_tasks: BackgroundTasks,
    user: User = Depends(get_current_user),
):
    """Store the transcript and start an extraction. 202, always, if it starts.

    A second paste replaces the transcript and discards the whole previous
    suggestion document, `pending` and `rejected` alike. Merging two independent
    extractions would need an identity for "the same suggestion" across two LLM
    calls, which does not exist. Already-confirmed suggestions are untouched by
    that: they are real rows in `retro.decisions` and `retro.actions` already,
    and confirmation is one-way.
    """
    retro = await _writable_retro(retro_id, user)
    await consume_ai_budget(retro)

    if is_processing(retro):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="An extraction is already running on this retrospective",
        )

    retro.transcript = body.text
    retro.ai_suggestions = processing_document()
    await save_retro(retro)

    background_tasks.add_task(run_extraction, retro.id)
    return TranscriptAcceptedResponse(status=PROCESSING)


@router.get("/retros/{retro_id}/suggestions", response_model=dict)
async def get_suggestions(retro_id: str, user: User = Depends(get_current_user)):
    """The stored suggestion document, or the idle one, on a 200 either way.

    Never 404 for "nothing pasted yet" — on this endpoint 404 means the retro is
    not there. The payload is exactly what `GET /api/retros/{id}` carries in
    `ai_suggestions`; this endpoint is a convenience for #17's poller, not a
    second source of truth.
    """
    retro = await _facilitator_retro(retro_id, user)
    return suggestions_view(retro)


@router.delete("/retros/{retro_id}/transcript", status_code=status.HTTP_204_NO_CONTENT)
async def delete_transcript(retro_id: str, user: User = Depends(get_current_user)):
    """Remove the stored meeting text, and the drafts derived from it (#25).

    Three deliberate departures from the rules the rest of this module follows.

    *Every phase, and a closed cycle too.* This is a retention control, not a
    retrospective write. One that stopped working when the retro finished would
    be useless precisely when it is wanted — a published retro is exactly the
    case where nobody needs the raw transcript any more. So it does not go
    through #20's writable-phase guard.

    *Confirmed items survive.* A decision or an action the facilitator kept is
    the retro's own record and belongs to #9's arrays; only the transcript and
    the suggestion document go. Deleting the source must not quietly delete what
    the team agreed.

    *Idempotent.* Deleting a retro that has no transcript is a 204, not a 404.
    The caller asked for it to be gone, and it is.
    """
    retro = await get_retro_for_facilitator(retro_id, user)

    if is_processing(retro):
        # An extraction in flight would write the drafts straight back in its
        # `finally`, so the delete has to wait for it to finish.
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="An extraction is running; wait for it to finish",
        )

    retro.transcript = None
    retro.ai_suggestions = None
    await save_retro(retro)


@router.post("/retros/{retro_id}/suggestions/confirm", response_model=ConfirmResponse)
async def confirm(
    retro_id: str, body: ConfirmRequest, user: User = Depends(get_current_user)
):
    """Turn the drafts the facilitator kept into real rows, all of them or none.

    Confirming twice is a 200 no-op that returns the first `created_id` rather
    than a conflict — the same reasoning #9 used for re-confirming a decision.
    The save happens once, at the end, so a refusal has written nothing.
    """
    retro = await _writable_retro(retro_id, user)
    result = await confirm_suggestions(retro, body)
    await save_retro(retro)
    return ConfirmResponse(**result)

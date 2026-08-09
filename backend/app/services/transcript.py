"""Turning a pasted transcript into drafts, and drafts into real rows (#10).

Everything the transcript feature knows lives here: the prompt, the shape the
proxy is asked for, the owner match, and what confirming does. The router above
is a phase gate, a body shape and a call into this module.

Three rules shape the whole file.

*Nothing the AI produces is ever saved* (`_docs/decisions.md`, "Suggestions are
always drafts"). Extraction writes `retro.ai_suggestions` and nothing else —
`retro.decisions` and `retro.actions` are only ever touched by a facilitator
confirming, which is why `run_extraction` never appends to them.

*A dead proxy is data, not a 500.* `run_extraction` is a background task, so by
the time it fails nobody is holding a request to fail. It therefore catches
everything and writes a terminal status in a `finally`: a retro left on
`processing` would refuse every later paste with a 409 forever.

*Confirming goes through #9's models, not #9's API.* A handler cannot call its
own HTTP endpoints, the point #8 made about `tally()` and #9 repeated. Confirmed
items are built as `Decision` and `Action` and appended to the same arrays #9
writes, so `GET /api/retros/{id}`, #9's `PATCH`/`DELETE` and #11's summary all
see them without knowing where they came from.
"""

from datetime import datetime, timezone
from uuid import uuid4

from fastapi import HTTPException, status

from app.models.project import Project
from app.models.retro import (
    EXTRACTION_STATUSES,
    SUGGESTION_STATES,
    Action,
    Decision,
    Retrospective,
)
from app.models.user import User
from app.services.concurrency import save_retro
from app.services.ai import ProxyError, ProxyMalformedResponse, chat_json
from app.services.votes import load_project_for_retro

# Names for the stored values, taken from the constants rather than retyped, so
# the strings themselves live in `app/models/retro.py` and in the schemas'
# `Literal`s and nowhere else.
IDLE, PROCESSING, READY, FAILED = EXTRACTION_STATUSES
PENDING, CONFIRMED, REJECTED = SUGGESTION_STATES

DECISIONS = "decisions"
ACTIONS = "actions"


# --- the prompt --------------------------------------------------------------
#
# It lives here and not in `app/services/ai.py`: #19 shares the client and asks
# it something completely different.

SYSTEM_PROMPT = (
    "You extract decisions and action items from a team retrospective "
    "transcript. Answer with a JSON object and nothing else, shaped exactly "
    '{"decisions": [{"text": "..."}], "actions": [{"description": "...", '
    '"owner": "...", "due_date": "..."}]}. A decision is something the team '
    "settled on. An action is something a person committed to doing. Use the "
    "speaker's own words where you can, one sentence each. `owner` is the name "
    "exactly as it appears in the transcript, or null if nobody was named. "
    "`due_date` is an ISO 8601 date, or null if none was given. If the meeting "
    "decided nothing, answer with two empty lists rather than inventing "
    "anything."
)


def _user_prompt(transcript: str) -> str:
    return f"Transcript:\n\n{transcript}"


# --- the suggestion document -------------------------------------------------


def _now_iso() -> str:
    """Stored as a string so the dict survives a Mongo round trip unchanged.

    `ai_suggestions` is a plain `dict` on the document (no Beanie model, by
    design), and `GET /suggestions` hands back exactly what is stored — a string
    is the one representation that is the same on both sides of the database.
    """
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def idle_document() -> dict:
    """What a retro nobody has pasted into reads as.

    Never stored. `GET /suggestions` cannot answer 404 for "nothing yet" —
    on that endpoint 404 means the retro is not there, and one code must not
    mean two things.
    """
    return {"status": IDLE, "error": None, DECISIONS: [], ACTIONS: []}


def processing_document() -> dict:
    return {
        "status": PROCESSING,
        "error": None,
        "requested_at": _now_iso(),
        "completed_at": None,
        DECISIONS: [],
        ACTIONS: [],
    }


def suggestions_view(retro: Retrospective) -> dict:
    """The stored document, or the idle one — byte for byte what #17 polls."""
    if retro.ai_suggestions is None:
        return idle_document()
    return retro.ai_suggestions


def is_processing(retro: Retrospective) -> bool:
    return suggestions_view(retro)["status"] == PROCESSING


# --- reading the proxy's answer ----------------------------------------------


def _text_field(item: object, key: str) -> str:
    if not isinstance(item, dict):
        raise ProxyMalformedResponse("a suggestion was not an object")
    value = item.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ProxyMalformedResponse(f"a suggestion had no {key}")
    return value.strip()


def _owner_name(item: dict) -> str | None:
    """The raw string the model produced, kept exactly as extracted.

    Both spellings are read because the answer comes from a language model, not
    from a schema we control; anything that is not a usable string is no owner.
    """
    for key in ("owner", "owner_name"):
        value = item.get(key)
        if isinstance(value, str) and value.strip():
            return value
    return None


def _due_date(item: dict) -> str | None:
    """A date the model guessed at is a hint, not a contract.

    An unreadable one is dropped rather than failing the whole extraction: the
    facilitator sets the real date at confirm time, and losing five good actions
    over one malformed date would be a worse trade.
    """
    value = item.get("due_date")
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.isoformat().replace("+00:00", "Z")


def _parse_answer(answer: dict) -> tuple[list[dict], list[dict]]:
    """The proxy's JSON, turned into two lists of pending suggestions.

    Anything that is not the shape the prompt asked for is a
    `ProxyMalformedResponse`, which the caller stores as `malformed_response`.
    Two empty lists are a perfectly good answer — a short meeting decides
    nothing sometimes.
    """
    raw_decisions = answer.get(DECISIONS)
    raw_actions = answer.get(ACTIONS)
    if not isinstance(raw_decisions, list) or not isinstance(raw_actions, list):
        raise ProxyMalformedResponse("the answer had no decisions and actions lists")

    decisions = [
        {
            "id": str(uuid4()),
            "text": _text_field(item, "text"),
            "state": PENDING,
            "created_id": None,
        }
        for item in raw_decisions
    ]
    actions = [
        {
            "id": str(uuid4()),
            "description": _text_field(item, "description"),
            "owner_name": _owner_name(item),
            "owner_id": None,
            "due_date": _due_date(item),
            "state": PENDING,
            "created_id": None,
        }
        for item in raw_actions
    ]
    return decisions, actions


# --- owner matching ----------------------------------------------------------


def normalise_name(value: str) -> str:
    """strip, collapse internal whitespace, casefold — both sides, the same way."""
    return " ".join(value.split()).casefold()


async def member_name_index(project: Project) -> dict[str, str | None]:
    """Normalised display name to member id, or `None` where two members collide.

    Current members only. Somebody who has left the project is not a candidate,
    the same rule #9 applies when an action is assigned.
    """
    ids = [member.user_id for member in project.members]
    index: dict[str, str | None] = {}
    for user in await User.find({"_id": {"$in": ids}}).to_list():
        key = normalise_name(user.display_name)
        index[key] = None if key in index else str(user.id)
    return index


def match_owner(owner_name: str | None, index: dict[str, str | None]) -> str | None:
    """Exactly one hit, or nothing. Deliberately dumb (`_docs/decisions.md`).

    No prefix, first-name or email matching: `"Sam"` does not match
    `"Sam Chen"`, and two members who normalise alike are unmatched. A
    confidently wrong owner is worse than an unassigned action, because under
    #9's owner permissions the wrong person can then edit it — and the
    facilitator is sitting right there at confirm time.
    """
    if owner_name is None:
        return None
    return index.get(normalise_name(owner_name))


# --- extraction --------------------------------------------------------------


async def run_extraction(retro_id) -> None:
    """The background task. Always leaves a terminal status behind.

    Every failure is swallowed into an `error` code, including one nobody
    predicted: this runs after the 202 has gone out, so raising would only lose
    the status and strand the retro on `processing`, where a paste is a 409.
    """
    error: str | None = None
    decisions: list[dict] = []
    actions: list[dict] = []
    try:
        retro = await Retrospective.get(retro_id)
        if retro is None or not retro.transcript:
            return
        answer = await chat_json(SYSTEM_PROMPT, _user_prompt(retro.transcript))
        decisions, actions = _parse_answer(answer)
        index = await member_name_index(await load_project_for_retro(retro))
        for action in actions:
            action["owner_id"] = match_owner(action["owner_name"], index)
    except ProxyError as exc:
        error = exc.code
        decisions, actions = [], []
    except Exception:
        # A bug here is still not a stuck retro. `ProxyError.code` is the
        # generic "the proxy could not be used" of the three, and the client
        # vocabulary is those three codes and nothing else.
        error = ProxyError.code
        decisions, actions = [], []
    finally:
        await _store_result(retro_id, error, decisions, actions)


async def _store_result(retro_id, error: str | None, decisions, actions) -> None:
    retro = await Retrospective.get(retro_id)
    if retro is None:
        return
    previous = retro.ai_suggestions or {}
    retro.ai_suggestions = {
        "status": FAILED if error else READY,
        "error": error,
        "requested_at": previous.get("requested_at"),
        "completed_at": _now_iso(),
        DECISIONS: decisions,
        ACTIONS: actions,
    }
    try:
        await save_retro(retro)
    except HTTPException:
        # 这个 retro 在抽取跑的时候被关掉了 (#34)。终态写输掉了条件,而这里
        # 没有任何请求可以把这个 400 交给谁——它是一个后台任务。关闭那一步
        # 已经把在途的抽取标成 failed 了,所以状态不会停在 `processing`。
        return


# --- confirming --------------------------------------------------------------


def _find_suggestion(retro: Retrospective, kind: str, suggestion_id: str) -> dict:
    """404 for an id that is not in this list, including one from the other list.

    A suggestion id is a UUID string inside the document, so a malformed one is
    simply an id that is not there — the same path #9 takes for topic, decision
    and action ids.
    """
    for suggestion in suggestions_view(retro).get(kind, []):
        if suggestion["id"] == suggestion_id:
            return suggestion
    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Suggestion not found")


def _row_by_id(rows: list, row_id: str | None):
    for row in rows:
        if row.id == row_id:
            return row
    return None


async def _require_current_member(retro: Retrospective, owner_id) -> None:
    """An owner override must name somebody currently on the project.

    404 with nothing written — the same call #9 makes, through the same project
    loader, rather than a second copy of the check.
    """
    if owner_id is None:
        return
    project = await load_project_for_retro(retro)
    if not project.is_member(owner_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Owner is not a project member"
        )


async def confirm_suggestions(retro: Retrospective, body) -> dict:
    """Confirm and reject a whole review pass, all of it or none of it.

    Every id and every owner override is resolved before anything is built, so a
    batch mixing a good item with an unknown id writes neither — #8's rule for a
    ballot with one bad cluster id, and #9's for an owner touching a restricted
    field. The caller saves; raising here has therefore written nothing.

    Confirming twice is a 200 no-op returning the first `created_id`, and a
    different `text` the second time does not rewrite the row: re-wording a
    confirmed item is #9's `PATCH`, not a second confirm.
    """
    wanted_decisions = [
        (item, _find_suggestion(retro, DECISIONS, item.id)) for item in body.decisions
    ]
    wanted_actions = [
        (item, _find_suggestion(retro, ACTIONS, item.id)) for item in body.actions
    ]
    rejected = [_find_either(retro, suggestion_id) for suggestion_id in body.rejected]

    for item, _ in wanted_actions:
        if "owner_id" in item.model_fields_set:
            await _require_current_member(retro, item.owner_id)

    confirmed_decisions = [
        _confirm_decision(retro, item, suggestion) for item, suggestion in wanted_decisions
    ]
    confirmed_actions = [
        _confirm_action(retro, item, suggestion) for item, suggestion in wanted_actions
    ]
    for suggestion in rejected:
        suggestion["state"] = REJECTED

    # The three keys are `ConfirmResponse`'s field names. The last one is spelled
    # from the state constant it coincides with, so the status strings stay
    # written out in `app/models/retro.py` and the schemas and nowhere else.
    return {
        DECISIONS: confirmed_decisions,
        ACTIONS: confirmed_actions,
        REJECTED: [suggestion["id"] for suggestion in rejected],
    }


def _find_either(retro: Retrospective, suggestion_id: str) -> dict:
    """A rejection does not have to say which list the id is in."""
    for kind in (DECISIONS, ACTIONS):
        for suggestion in suggestions_view(retro).get(kind, []):
            if suggestion["id"] == suggestion_id:
                return suggestion
    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Suggestion not found")


def _confirm_decision(retro: Retrospective, item, suggestion: dict) -> dict:
    existing = _row_by_id(retro.decisions, suggestion.get("created_id"))
    if suggestion["state"] == CONFIRMED and existing is not None:
        return {
            "suggestion_id": suggestion["id"],
            "created_id": existing.id,
            "text": existing.text,
        }

    text = item.text if "text" in item.model_fields_set and item.text else suggestion["text"]
    decision = Decision(id=str(uuid4()), topic_id=None, text=text, is_confirmed=True)
    retro.decisions.append(decision)
    suggestion["state"] = CONFIRMED
    suggestion["created_id"] = decision.id
    return {"suggestion_id": suggestion["id"], "created_id": decision.id, "text": decision.text}


def _confirm_action(retro: Retrospective, item, suggestion: dict) -> dict:
    existing = _row_by_id(retro.actions, suggestion.get("created_id"))
    if suggestion["state"] == CONFIRMED and existing is not None:
        return _action_result(suggestion["id"], existing)

    sent = item.model_fields_set
    description = (
        item.description
        if "description" in sent and item.description
        else suggestion["description"]
    )
    owner_id = item.owner_id if "owner_id" in sent else suggestion["owner_id"]
    due_date = item.due_date if "due_date" in sent else suggestion["due_date"]

    action = Action(
        id=str(uuid4()),
        topic_id=None,
        description=description,
        owner_id=owner_id,
        # The raw extracted string travels with the row whether or not it
        # matched. It is the only record of who the meeting actually named.
        owner_name=suggestion["owner_name"],
        due_date=due_date,
    )
    retro.actions.append(action)
    suggestion["state"] = CONFIRMED
    suggestion["created_id"] = action.id
    return _action_result(suggestion["id"], action)


def _action_result(suggestion_id: str, action: Action) -> dict:
    return {
        "suggestion_id": suggestion_id,
        "created_id": action.id,
        "description": action.description,
        "owner_id": None if action.owner_id is None else str(action.owner_id),
        "owner_name": action.owner_name,
        "due_date": action.due_date,
    }

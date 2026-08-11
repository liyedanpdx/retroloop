"""One caller's own open actions, across every project (#45).

Nowhere else in the API is user-scoped rather than project- or retro-scoped,
which is why this is its own router instead of another route under
`/api/projects` — `/api/projects/mine` would collide with the dynamic
`/api/projects/{project_id}` path the moment a project happened to be named
"mine".
"""

from fastapi import APIRouter, Depends

from app.deps import get_current_user
from app.models.user import User
from app.schemas.actions import MyAction
from app.services.my_actions import assemble_my_actions

router = APIRouter(prefix="/api/actions", tags=["actions"])


@router.get("/mine", response_model=list[MyAction])
async def list_my_actions(user: User = Depends(get_current_user)):
    """Every open action owned by the caller, across every project they are
    a member of — including an archived one. An action already assigned does
    not stop needing doing just because its project got archived (#32);
    archiving only stops new work from starting.
    """
    return await assemble_my_actions(user)

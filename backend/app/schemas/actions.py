"""One caller's own open actions, aggregated across every project they belong
to (#45) — the same "assembled on read, no cached collection" shape #31's
dashboard and #11's summary already use.
"""

from datetime import datetime

from pydantic import BaseModel


class MyAction(BaseModel):
    """Always the caller's own action — `owner_id` is the query, not a field.

    There is no `owner`/`owner_name` here the way `dashboard.OpenAction` has
    one: every row is already known to belong to whoever asked for the list.
    """

    id: str
    project_id: str
    project_name: str
    retro_id: str
    description: str
    due_date: datetime | None

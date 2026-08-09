"""一个项目一小时能烧掉多少代理调用 (#27)。

抽取和聚类建议共用同一个桶,因为它们共用 `app/services/ai.py`,也共用同一份
账单。只限其中一个,只会把开销挪到另一个上,而不是把它限住。

桶按项目而不是按 retro:一个团队开一个新 retro 就能重置额度的话,这个限制是
装饰品。
"""

from datetime import timedelta

from app.config import settings
from app.models.retro import Retrospective
from app.services.rate_limit import consume
from app.services.votes import load_project_for_retro

DETAIL = "This project has made too many AI requests. Try again later."


async def consume_ai_budget(retro: Retrospective) -> None:
    project = await load_project_for_retro(retro)
    await consume(
        f"ai:{project.id}",
        settings.ai_calls_per_hour,
        timedelta(hours=1),
        DETAIL,
    )

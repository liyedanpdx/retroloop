from datetime import datetime, timezone

from beanie import Document
from pydantic import Field


def _now() -> datetime:
    return datetime.now(timezone.utc)


class RateLimitWindow(Document):
    """一个固定窗口的计数 (#27)。

    存在 MongoDB 里而不是进程内存里,因为进程内的计数器在两个后端实例下就等于
    没有——而这个项目的部署形态还没定 (#33)。数据库是这里唯一一个所有实例都看
    得见的东西。

    `_id` 就是桶的名字,所以「读—判断—写」可以塌缩成一次带 upsert 的原子更新;
    两个并发请求不会各自读到同一个旧计数然后都放行。
    """

    id: str  # 桶名,例如 "ai:<project_id>" 或 "login:<email>"
    hits: int = 0  # 不叫 count:那会遮住 Document 自己的属性
    window_start: datetime = Field(default_factory=_now)

    class Settings:
        name = "rate_limits"

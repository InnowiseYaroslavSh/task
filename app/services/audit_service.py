import json

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.time import utc_now
from app.models import AuditEvent


class AuditService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def record(
        self,
        action: str,
        *,
        actor_user_id: str | None = None,
        target_user_id: str | None = None,
        method: str | None = None,
        path: str | None = None,
        details: dict[str, object] | None = None,
    ) -> AuditEvent:
        event = AuditEvent(
            actor_user_id=actor_user_id,
            target_user_id=target_user_id,
            action=action,
            method=method,
            path=path,
            details=json.dumps(details or {}, sort_keys=True),
            created_at=utc_now(),
            updated_at=utc_now(),
        )
        self.session.add(event)
        await self.session.flush()
        return event

from datetime import time

from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import transaction
from app.core.errors import ApiError
from app.core.time import utc_now
from app.models import AvailabilityWindow, SubjectType
from app.repositories.identity_repository import IdentityRepository
from app.schemas import AvailabilityReplace, AvailabilityView


class AvailabilityService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.repo = IdentityRepository(session)

    async def replace(
        self, subject_type: SubjectType, subject_id: str, payload: AvailabilityReplace
    ) -> list[AvailabilityView]:
        self._validate(payload)
        async with transaction(self.session):
            await self.session.execute(
                delete(AvailabilityWindow).where(
                    AvailabilityWindow.subject_type == subject_type,
                    AvailabilityWindow.subject_id == subject_id,
                )
            )
            now = utc_now()
            for window in payload.windows:
                self.session.add(
                    AvailabilityWindow(
                        subject_type=subject_type,
                        subject_id=subject_id,
                        weekday=window.weekday,
                        start_time=window.start_time,
                        end_time=window.end_time,
                        created_at=now,
                        updated_at=now,
                    )
                )
        return await self.get(subject_type, subject_id)

    async def get(self, subject_type: SubjectType, subject_id: str) -> list[AvailabilityView]:
        windows = await self.repo.availability(subject_type, subject_id)
        return [AvailabilityView.model_validate(window) for window in windows]

    async def check_conflict(
        self,
        subject_type: SubjectType,
        subject_id: str,
        weekday: int,
        start: time,
        end: time,
    ) -> bool:
        if start >= end:
            return False
        for window in await self.repo.availability(subject_type, subject_id):
            if window.weekday == weekday and window.start_time < end and start < window.end_time:
                return True
        return False

    @staticmethod
    def _validate(payload: AvailabilityReplace) -> None:
        grouped: dict[int, list[tuple[time, time]]] = {}
        for window in payload.windows:
            if window.start_time >= window.end_time:
                raise ApiError(
                    "Availability start must be before end",
                    code="INVALID_TIME_RANGE",
                    status_code=422,
                )
            grouped.setdefault(window.weekday, []).append((window.start_time, window.end_time))
        for windows in grouped.values():
            ordered = sorted(windows)
            if any(start < previous_end for (_, previous_end), (start, _) in zip(ordered, ordered[1:], strict=False)):
                raise ApiError(
                    "Availability windows overlap",
                    code="OVERLAPPING_WINDOWS",
                    status_code=422,
                )

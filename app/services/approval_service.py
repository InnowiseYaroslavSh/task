from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import transaction
from app.core.errors import ApiError
from app.core.jobs import Job, JobQueue
from app.core.time import as_utc, utc_now
from app.models import ApprovalStatus, PurchaseApproval
from app.repositories.identity_repository import IdentityRepository
from app.schemas import ApprovalView
from app.services.audit_service import AuditService


class ApprovalService:
    def __init__(self, session: AsyncSession, jobs: JobQueue) -> None:
        self.session = session
        self.jobs = jobs
        self.repo = IdentityRepository(session)

    async def list_for_parent(self, parent_id: str) -> list[ApprovalView]:
        approvals = list(
            await self.session.scalars(
                select(PurchaseApproval)
                .where(PurchaseApproval.parent_user_id == parent_id)
                .order_by(PurchaseApproval.requested_at.desc())
            )
        )
        return [ApprovalView.model_validate(approval) for approval in approvals]

    async def create(
        self,
        *,
        player_profile_id: str,
        parent_user_id: str,
        trainer_id: str,
        subject_type: str,
        subject_id: str,
        amount_cents: int | None = None,
        token_amount: int | None = None,
    ) -> ApprovalView:
        async with transaction(self.session):
            now = utc_now()
            approval = PurchaseApproval(
                player_profile_id=player_profile_id,
                parent_user_id=parent_user_id,
                trainer_id=trainer_id,
                subject_type=subject_type,
                subject_id=subject_id,
                amount_cents=amount_cents,
                token_amount=token_amount,
                status=ApprovalStatus.PENDING,
                requested_at=now,
                expires_at=now + timedelta(hours=48),
                created_at=now,
                updated_at=now,
            )
            self.session.add(approval)
            await self.session.flush()
            await self.jobs.enqueue(
                Job(
                    "notify_parent_approval_request",
                    {"approval_id": approval.id},
                    f"approval:{approval.id}:requested",
                )
            )
        return ApprovalView.model_validate(approval)

    async def decide(self, approval_id: str, parent_id: str, approved: bool, note: str | None) -> ApprovalView:
        not_pending = False
        result: ApprovalView | None = None
        async with transaction(self.session):
            approval = await self.repo.approval_by_id(approval_id, lock=True)
            if approval is None or approval.parent_user_id != parent_id:
                raise ApiError("Approval not found", code="NOT_FOUND", status_code=404)
            if approval.status != ApprovalStatus.PENDING or as_utc(approval.expires_at) <= utc_now():
                if approval.status == ApprovalStatus.PENDING:
                    approval.status = ApprovalStatus.EXPIRED
                not_pending = True
            else:
                approval.status = ApprovalStatus.APPROVED if approved else ApprovalStatus.DENIED
                approval.responded_at = utc_now()
                approval.parent_note = note
                await AuditService(self.session).record(
                    "purchase_approval.approved" if approved else "purchase_approval.denied",
                    actor_user_id=parent_id,
                    target_user_id=parent_id,
                    details={"approval_id": approval_id},
                )
                await self.jobs.enqueue(
                    Job(
                        "notify_child_decision",
                        {"approval_id": approval.id, "status": str(approval.status)},
                        f"approval:{approval.id}:{approval.status}",
                    )
                )
            result = ApprovalView.model_validate(approval)
        if not_pending:
            raise ApiError(
                "Approval is not pending",
                code="APPROVAL_NOT_PENDING",
                status_code=409,
            )
        assert result is not None
        return result

    async def expire(self) -> int:
        async with transaction(self.session):
            approvals = list(
                await self.session.scalars(
                    select(PurchaseApproval).where(PurchaseApproval.status == ApprovalStatus.PENDING)
                )
            )
            for approval in approvals:
                if as_utc(approval.expires_at) <= utc_now():
                    approval.status = ApprovalStatus.EXPIRED
            return sum(1 for approval in approvals if approval.status == ApprovalStatus.EXPIRED)

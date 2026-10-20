from datetime import date, datetime, time
from enum import StrEnum

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    Time,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, id_column


class UserRole(StrEnum):
    SUPER_ADMIN = "super_admin"
    TRAINER = "trainer"
    COACH = "coach"
    PLAYER = "player"


class UserStatus(StrEnum):
    PENDING = "pending"
    ACTIVE = "active"
    INACTIVE = "inactive"
    DELETED = "deleted"


class Gender(StrEnum):
    FEMALE = "female"
    MALE = "male"
    NON_BINARY = "non_binary"
    UNSPECIFIED = "unspecified"


class LinkSource(StrEnum):
    SHARE_LINK = "share_link"
    EVENT_REGISTRATION = "event_registration"
    COACH_INVITE = "coach_invite"
    CAMP_CONVERSION = "camp_conversion"
    ADMIN = "admin"


class LinkStatus(StrEnum):
    ACTIVE = "active"
    INACTIVE = "inactive"


class ShareLinkKind(StrEnum):
    PLAYER_STATIC = "player_static"
    COACH_UNIQUE = "coach_unique"


class ShareLinkEventKind(StrEnum):
    OPENED = "opened"
    REGISTERED = "registered"


class SubjectType(StrEnum):
    PLAYER = "player"
    COACH = "coach"


class ApprovalStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    DENIED = "denied"
    EXPIRED = "expired"


class OneTimeTokenKind(StrEnum):
    SETUP = "setup"
    EMAIL_VERIFICATION = "email_verification"
    PASSWORD_RESET = "password_reset"
    EMAIL_CHANGE = "email_change"


class User(TimestampMixin, Base):
    __tablename__ = "users"
    __table_args__ = (Index("ix_users_role_status", "role", "status"),)

    id: Mapped[str] = id_column()
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(Text)
    role: Mapped[UserRole] = mapped_column(String(32), default=UserRole.PLAYER)
    status: Mapped[UserStatus] = mapped_column(String(32), default=UserStatus.PENDING)
    is_email_verified: Mapped[bool] = mapped_column(Boolean, default=False)
    must_change_password: Mapped[bool] = mapped_column(Boolean, default=False)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Profile(TimestampMixin, Base):
    __tablename__ = "profiles"

    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), primary_key=True)
    first_name: Mapped[str] = mapped_column(String(100))
    last_name: Mapped[str] = mapped_column(String(100))
    phone: Mapped[str | None] = mapped_column(String(40), nullable=True)
    avatar_key: Mapped[str | None] = mapped_column(String(512), nullable=True)
    school: Mapped[str | None] = mapped_column(String(200), nullable=True)
    timezone: Mapped[str] = mapped_column(String(64), default="UTC")


class TrainerProfile(TimestampMixin, Base):
    __tablename__ = "trainer_profiles"

    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), primary_key=True)
    business_name: Mapped[str] = mapped_column(String(200))
    slug: Mapped[str] = mapped_column(String(120), unique=True, index=True)
    address: Mapped[str | None] = mapped_column(String(400), nullable=True)
    website: Mapped[str | None] = mapped_column(String(300), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    logo_key: Mapped[str | None] = mapped_column(String(512), nullable=True)
    brand_primary_color: Mapped[str | None] = mapped_column(String(7), nullable=True)
    brand_secondary_color: Mapped[str | None] = mapped_column(String(7), nullable=True)


class CoachProfile(TimestampMixin, Base):
    __tablename__ = "coach_profiles"

    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), primary_key=True)
    trainer_id: Mapped[str] = mapped_column(ForeignKey("trainer_profiles.user_id", ondelete="RESTRICT"))
    bio: Mapped[str | None] = mapped_column(Text, nullable=True)
    credentials: Mapped[str | None] = mapped_column(Text, nullable=True)
    certifications: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_public: Mapped[bool] = mapped_column(Boolean, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    joined_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class PlayerProfile(TimestampMixin, Base):
    __tablename__ = "player_profiles"
    __table_args__ = (Index("ix_player_profiles_owner", "owner_user_id"),)

    id: Mapped[str] = id_column()
    owner_user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    login_user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), nullable=True)
    display_name: Mapped[str] = mapped_column(String(200))
    birth_date: Mapped[date] = mapped_column(Date)
    gender: Mapped[Gender] = mapped_column(String(32))
    skill_level: Mapped[str | None] = mapped_column(String(80), nullable=True)
    school: Mapped[str | None] = mapped_column(String(200), nullable=True)
    jersey_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    is_dependent: Mapped[bool] = mapped_column(Boolean, default=False)
    emergency_contact_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    emergency_contact_phone: Mapped[str | None] = mapped_column(String(40), nullable=True)
    token_spend_requires_approval: Mapped[bool] = mapped_column(Boolean, default=True)


class TrainerPlayerLink(TimestampMixin, Base):
    __tablename__ = "trainer_player_links"
    __table_args__ = (
        UniqueConstraint("trainer_id", "player_profile_id", name="uq_trainer_player"),
        Index("ix_tpl_trainer_status", "trainer_id", "status"),
    )

    id: Mapped[str] = id_column()
    trainer_id: Mapped[str] = mapped_column(ForeignKey("trainer_profiles.user_id", ondelete="RESTRICT"))
    player_profile_id: Mapped[str] = mapped_column(ForeignKey("player_profiles.id", ondelete="RESTRICT"))
    source: Mapped[LinkSource] = mapped_column(String(32))
    share_link_id: Mapped[str | None] = mapped_column(ForeignKey("share_links.id", ondelete="RESTRICT"), nullable=True)
    linked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    status: Mapped[LinkStatus] = mapped_column(String(32), default=LinkStatus.ACTIVE)


class ShareLink(TimestampMixin, Base):
    __tablename__ = "share_links"
    __table_args__ = (
        CheckConstraint(
            "NOT (kind = 'player_static' AND (expires_at IS NOT NULL OR max_uses IS NOT NULL))",
            name="ck_player_static_link_lifetime",
        ),
        CheckConstraint(
            "NOT (kind = 'coach_unique' AND (expires_at IS NULL OR max_uses != 1))",
            name="ck_coach_unique_link_lifetime",
        ),
    )

    id: Mapped[str] = id_column()
    code: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    trainer_id: Mapped[str] = mapped_column(ForeignKey("trainer_profiles.user_id", ondelete="RESTRICT"))
    created_by_user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    kind: Mapped[ShareLinkKind] = mapped_column(String(32))
    target_email: Mapped[str | None] = mapped_column(String(320), nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    max_uses: Mapped[int | None] = mapped_column(Integer, nullable=True)
    use_count: Mapped[int] = mapped_column(Integer, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class ShareLinkEvent(TimestampMixin, Base):
    __tablename__ = "share_link_events"

    id: Mapped[str] = id_column()
    share_link_id: Mapped[str] = mapped_column(ForeignKey("share_links.id", ondelete="RESTRICT"))
    kind: Mapped[ShareLinkEventKind] = mapped_column(String(32))
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    user_agent_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    ip_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)


class AvailabilityWindow(TimestampMixin, Base):
    __tablename__ = "availability_windows"
    __table_args__ = (
        CheckConstraint("start_time < end_time", name="ck_availability_order"),
        Index("ix_availability_subject", "subject_type", "subject_id", "weekday"),
    )

    id: Mapped[str] = id_column()
    subject_type: Mapped[SubjectType] = mapped_column(String(32))
    subject_id: Mapped[str] = mapped_column(String(36))
    weekday: Mapped[int] = mapped_column(Integer)
    start_time: Mapped[time] = mapped_column(Time)
    end_time: Mapped[time] = mapped_column(Time)


class PurchaseApproval(TimestampMixin, Base):
    __tablename__ = "purchase_approvals"
    __table_args__ = (Index("ix_purchase_approvals_pending", "status", "expires_at"),)

    id: Mapped[str] = id_column()
    player_profile_id: Mapped[str] = mapped_column(ForeignKey("player_profiles.id", ondelete="RESTRICT"))
    parent_user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    trainer_id: Mapped[str] = mapped_column(ForeignKey("trainer_profiles.user_id", ondelete="RESTRICT"))
    subject_type: Mapped[str] = mapped_column(String(32))
    subject_id: Mapped[str] = mapped_column(String(36))
    amount_cents: Mapped[int | None] = mapped_column(Integer, nullable=True)
    token_amount: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[ApprovalStatus] = mapped_column(String(32), default=ApprovalStatus.PENDING)
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    responded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    parent_note: Mapped[str | None] = mapped_column(Text, nullable=True)


class ImpersonationSession(TimestampMixin, Base):
    __tablename__ = "impersonation_sessions"

    id: Mapped[str] = id_column()
    admin_user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    target_user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    jti: Mapped[str] = mapped_column(String(36), unique=True)


class UserErasure(TimestampMixin, Base):
    __tablename__ = "user_erasures"

    id: Mapped[str] = id_column()
    original_user_id: Mapped[str] = mapped_column(String(36))
    original_email_hash: Mapped[str] = mapped_column(String(64))
    erased_by_user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    erased_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    legal_retention_until: Mapped[date | None] = mapped_column(Date, nullable=True)


class RefreshToken(TimestampMixin, Base):
    __tablename__ = "refresh_tokens"
    __table_args__ = (Index("ix_refresh_family", "family_id", "revoked_at"),)

    id: Mapped[str] = id_column()
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    family_id: Mapped[str] = mapped_column(String(36), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    user_agent_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    ip_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)


class OneTimeToken(TimestampMixin, Base):
    __tablename__ = "one_time_tokens"

    id: Mapped[str] = id_column()
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    kind: Mapped[OneTimeTokenKind] = mapped_column(String(40))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    payload: Mapped[str | None] = mapped_column(Text, nullable=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AuditEvent(TimestampMixin, Base):
    __tablename__ = "audit_events"

    id: Mapped[str] = id_column()
    actor_user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), nullable=True)
    target_user_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    action: Mapped[str] = mapped_column(String(120))
    method: Mapped[str | None] = mapped_column(String(12), nullable=True)
    path: Mapped[str | None] = mapped_column(String(400), nullable=True)
    details: Mapped[str | None] = mapped_column(Text, nullable=True)

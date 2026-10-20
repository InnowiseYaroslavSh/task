from datetime import date, datetime, time
from typing import Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from app.models import Gender, ShareLinkKind, UserRole, UserStatus


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", from_attributes=True)


class ProfileView(StrictModel):
    user_id: str
    first_name: str
    last_name: str
    phone: str | None = None
    avatar_key: str | None = None
    school: str | None = None
    timezone: str


class UserView(StrictModel):
    id: str
    email: EmailStr
    role: UserRole
    status: UserStatus
    is_email_verified: bool
    must_change_password: bool
    profile: ProfileView | None = None


class TokenResponse(StrictModel):
    access_token: str
    refresh_token: str
    token_type: Literal["bearer"] = "bearer"
    expires_in: int
    user: UserView


class LoginRequest(StrictModel):
    email: EmailStr
    password: str = Field(min_length=1)


class SetupRequest(StrictModel):
    token: str = Field(min_length=1)
    password: str = Field(min_length=12)


class RegisterRequest(StrictModel):
    email: EmailStr
    password: str = Field(min_length=12)
    first_name: str = Field(min_length=1, max_length=100)
    last_name: str = Field(min_length=1, max_length=100)
    phone: str | None = Field(default=None, max_length=40)
    player_profile_ids: list[str] | None = None
    role: Literal["player", "coach"] = "player"


class RefreshRequest(StrictModel):
    refresh_token: str = Field(min_length=1)


class PasswordResetRequest(StrictModel):
    email: EmailStr


class PasswordResetConfirm(StrictModel):
    token: str
    password: str = Field(min_length=12)


class VerifyEmailRequest(StrictModel):
    token: str


class EmailChangeRequest(StrictModel):
    email: EmailStr


class EmailChangeConfirm(StrictModel):
    token: str


class ProfilePatch(StrictModel):
    first_name: str | None = Field(default=None, min_length=1, max_length=100)
    last_name: str | None = Field(default=None, min_length=1, max_length=100)
    phone: str | None = Field(default=None, max_length=40)
    school: str | None = Field(default=None, max_length=200)
    timezone: str | None = Field(default=None, max_length=64)


class AvatarPrepareRequest(StrictModel):
    content_type: Literal["image/jpeg", "image/png", "image/webp"]
    size_bytes: int = Field(gt=0, le=5 * 1024 * 1024)


class AvatarConfirmRequest(StrictModel):
    avatar_key: str = Field(min_length=1, max_length=512)
    content_type: Literal["image/jpeg", "image/png", "image/webp"]
    size_bytes: int = Field(gt=0, le=5 * 1024 * 1024)


class AvailabilityInput(StrictModel):
    weekday: int = Field(ge=0, le=6)
    start_time: time
    end_time: time


class AvailabilityReplace(StrictModel):
    windows: list[AvailabilityInput]


class AvailabilityView(AvailabilityInput):
    id: str


class DependentCreate(StrictModel):
    display_name: str = Field(min_length=1, max_length=200)
    birth_date: date
    gender: Gender
    trainer_ids: list[str] = Field(default_factory=list)
    emergency_contact_name: str | None = Field(default=None, max_length=200)
    emergency_contact_phone: str | None = Field(default=None, max_length=40)


class DependentPatch(StrictModel):
    display_name: str | None = Field(default=None, min_length=1, max_length=200)
    school: str | None = Field(default=None, max_length=200)
    jersey_number: int | None = Field(default=None, ge=0, le=99)
    token_spend_requires_approval: bool | None = None
    emergency_contact_name: str | None = Field(default=None, max_length=200)
    emergency_contact_phone: str | None = Field(default=None, max_length=40)


class ChildLoginCreate(StrictModel):
    email: EmailStr
    password: str = Field(min_length=12)


class ChildLoginView(StrictModel):
    user_id: str
    email: EmailStr


class TrainerAssociationRequest(StrictModel):
    trainer_id: str


class LinkView(StrictModel):
    id: str
    trainer_id: str
    player_profile_id: str
    source: str
    status: str
    linked_at: datetime


class PlayerView(StrictModel):
    id: str
    owner_user_id: str
    login_user_id: str | None
    display_name: str
    birth_date: date
    gender: Gender
    school: str | None
    jersey_number: int | None
    is_dependent: bool
    token_spend_requires_approval: bool
    links: list[LinkView] = Field(default_factory=list)


class ShareLinkCreate(StrictModel):
    kind: ShareLinkKind
    target_email: EmailStr | None = None


class ShareLinkView(StrictModel):
    id: str
    code: str
    kind: ShareLinkKind
    expires_at: datetime | None
    max_uses: int | None
    use_count: int
    is_active: bool


class ShareLinkPreview(StrictModel):
    trainer_name: str
    kind: ShareLinkKind
    valid: bool
    expires_at: datetime | None


class ApprovalView(StrictModel):
    id: str
    player_profile_id: str
    subject_type: str
    subject_id: str
    status: str
    amount_cents: int | None
    token_amount: int | None
    expires_at: datetime


class ApprovalDecision(StrictModel):
    parent_note: str | None = Field(default=None, max_length=1000)


class TrainerProvisionRequest(StrictModel):
    email: EmailStr
    first_name: str = Field(min_length=1, max_length=100)
    last_name: str = Field(min_length=1, max_length=100)
    phone: str | None = Field(default=None, max_length=40)
    business_name: str = Field(min_length=1, max_length=200)
    slug: str = Field(min_length=1, max_length=120, pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


class TrainerProvisionView(StrictModel):
    id: str
    email: EmailStr
    business_name: str
    slug: str
    status: UserStatus


class AdminUserPatch(StrictModel):
    first_name: str | None = Field(default=None, min_length=1, max_length=100)
    last_name: str | None = Field(default=None, min_length=1, max_length=100)
    phone: str | None = Field(default=None, max_length=40)
    school: str | None = Field(default=None, max_length=200)


class BrandingPatch(StrictModel):
    brand_primary_color: str | None = Field(default=None, pattern=r"^#[0-9A-Fa-f]{6}$")
    brand_secondary_color: str | None = Field(default=None, pattern=r"^#[0-9A-Fa-f]{6}$")
    logo_key: str | None = Field(default=None, max_length=512)


class BrandingView(StrictModel):
    business_name: str
    brand_primary_color: str | None
    brand_secondary_color: str | None
    logo_url: str | None


class LogoPrepareRequest(StrictModel):
    content_type: Literal["image/png", "image/svg+xml", "image/webp"]
    size_bytes: int = Field(gt=0, le=2 * 1024 * 1024)


class UserListView(StrictModel):
    items: list[UserView]
    next_cursor: str | None = None
    has_more: bool = False


class ImpersonateRequest(StrictModel):
    reason: str | None = Field(default=None, max_length=1000)


class LogoPrepareView(StrictModel):
    upload_url: str
    logo_key: str


class ContextView(StrictModel):
    trainer_id: str
    business_name: str
    player_profile_id: str

import hashlib
import re
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

from app.core.config import Settings
from app.core.errors import AuthenticationError, DomainError
from app.core.time import new_id

PASSWORD_HASHER = PasswordHasher(time_cost=3, memory_cost=64 * 1024, parallelism=4)
DUMMY_PASSWORD_HASH = PASSWORD_HASHER.hash("accelerator-dummy-password")


def validate_password(password: str) -> str:
    if len(password) < 12:
        raise DomainError(
            "Password must contain at least 12 characters",
            details={"field": "password"},
        )
    if password.lower() == password or password.upper() == password or not re.search(r"\d", password):
        raise DomainError(
            "Password must include mixed case and a number",
            details={"field": "password"},
        )
    return password


def hash_password(password: str) -> str:
    return PASSWORD_HASHER.hash(validate_password(password))


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return PASSWORD_HASHER.verify(password_hash, password)
    except (InvalidHashError, VerificationError, VerifyMismatchError):
        return False


def verify_password_or_dummy(password: str, password_hash: str | None) -> bool:
    return verify_password(password, password_hash or DUMMY_PASSWORD_HASH)


def hash_token(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def random_token() -> str:
    return secrets.token_urlsafe(32)


def random_password() -> str:
    return f"{random_token()}Aa1!"


def create_access_token(
    *,
    user_id: str,
    role: str,
    trainer_id: str | None,
    settings: Settings,
    act: str | None = None,
    impersonation_session_id: str | None = None,
    ttl_minutes: int | None = None,
    jti: str | None = None,
) -> tuple[str, str, datetime]:
    issued_at = datetime.now(UTC)
    expires_at = issued_at + timedelta(minutes=ttl_minutes or settings.access_token_minutes)
    token_jti = jti or new_id()
    payload: dict[str, Any] = {
        "sub": user_id,
        "role": role,
        "trainer_id": trainer_id,
        "jti": token_jti,
        "iat": issued_at,
        "exp": expires_at,
    }
    if act is not None:
        payload["act"] = act
    if impersonation_session_id is not None:
        payload["impersonation_session_id"] = impersonation_session_id
    return (
        jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm),
        token_jti,
        expires_at,
    )


def decode_access_token(token: str, settings: Settings) -> dict[str, Any]:
    try:
        return jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
    except jwt.PyJWTError as error:
        raise AuthenticationError("Invalid access token") from error


def sanitize_svg(svg: str) -> str:
    result = re.sub(r"<script\b[^>]*>.*?</script\s*>", "", svg, flags=re.IGNORECASE | re.DOTALL)
    result = re.sub(
        r"<foreignObject\b[^>]*>.*?</foreignObject\s*>",
        "",
        result,
        flags=re.IGNORECASE | re.DOTALL,
    )
    return re.sub(
        r"\s+on[a-z]+\s*=\s*(?:\"[^\"]*\"|'[^']*'|[^\s>]+)",
        "",
        result,
        flags=re.IGNORECASE,
    )

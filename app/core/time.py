from datetime import UTC, datetime
from uuid import UUID, uuid4


def utc_now() -> datetime:
    return datetime.now(UTC)


def as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def new_id() -> str:
    """Return an isolated id value so deployments can replace UUID4 with UUID7."""

    return str(uuid4())


def as_uuid(value: str) -> UUID:
    return UUID(value)

from dataclasses import dataclass, field
from typing import Protocol


@dataclass(frozen=True)
class Job:
    name: str
    payload: dict[str, str]
    idempotency_key: str


class JobQueue(Protocol):
    async def enqueue(self, job: Job) -> None: ...


@dataclass
class InMemoryJobQueue:
    jobs: list[Job] = field(default_factory=list)

    async def enqueue(self, job: Job) -> None:
        if any(existing.idempotency_key == job.idempotency_key for existing in self.jobs):
            return
        self.jobs.append(job)

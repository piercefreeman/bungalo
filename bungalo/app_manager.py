import asyncio
import os
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, ClassVar, Dict, Optional

from bungalo.logger import LOGGER


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class ServiceStatus:
    name: str
    state: str
    detail: Optional[str] = None
    updated_at: datetime = field(default_factory=utcnow)
    next_run_at: Optional[datetime] = None
    last_run_at: Optional[datetime] = None

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["updated_at"] = self.updated_at.isoformat()
        if self.next_run_at:
            payload["next_run_at"] = self.next_run_at.isoformat()
        if self.last_run_at:
            payload["last_run_at"] = self.last_run_at.isoformat()
        return payload


@dataclass
class TaskState:
    id: str
    title: str
    prompt: str
    status: str = "pending"
    value: Optional[str] = None
    error: Optional[str] = None
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=utcnow)
    updated_at: datetime = field(default_factory=utcnow)

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["created_at"] = self.created_at.isoformat()
        payload["updated_at"] = self.updated_at.isoformat()
        return payload


class TaskNotFoundError(KeyError):
    pass


class AppTask:
    def __init__(self, manager: "AppManager", task_id: str):
        self._manager = manager
        self.task_id = task_id

    @property
    def url(self) -> str:
        return self._manager.task_url(self.task_id)

    def info(self) -> TaskState:
        return self._manager._tasks[self.task_id]

    async def wait(self) -> str:
        return await self._manager.wait_for_task(self.task_id)

    async def mark_completed(self) -> None:
        await self._manager.mark_task_completed(self.task_id)

    async def request_retry(self, error: str) -> None:
        await self._manager.retry_task(self.task_id, error)


class AppManager:
    _instance: ClassVar[Optional["AppManager"]] = None

    def __init__(self):
        self._lock = asyncio.Lock()
        self._services: Dict[str, ServiceStatus] = {}
        self._tasks: Dict[str, TaskState] = {}
        self._task_waiters: Dict[str, asyncio.Future[str]] = {}
        self.started_at = utcnow()
        self.dashboard_base_url = os.environ.get(
            "BUNGALO_DASHBOARD_URL", "http://localhost:8000"
        )

    @classmethod
    def get(cls) -> "AppManager":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def task_url(self, task_id: str) -> str:
        return f"{self.dashboard_base_url.rstrip('/')}/tasks/{task_id}"

    async def create_task(
        self,
        *,
        title: str,
        prompt: str,
        metadata: Optional[dict[str, Any]] = None,
    ) -> AppTask:
        task_id = str(uuid.uuid4())
        loop = asyncio.get_running_loop()
        future: asyncio.Future[str] = loop.create_future()

        async with self._lock:
            state = TaskState(
                id=task_id,
                title=title,
                prompt=prompt,
                metadata=metadata or {},
            )
            self._tasks[task_id] = state
            self._task_waiters[task_id] = future
            LOGGER.info("Created task %s (%s)", task_id, title)

        return AppTask(self, task_id)

    async def wait_for_task(self, task_id: str) -> str:
        future = self._task_waiters.get(task_id)
        if future is None:
            raise TaskNotFoundError(task_id)
        return await future

    async def retry_task(self, task_id: str, error: str) -> None:
        loop = asyncio.get_running_loop()
        new_future: asyncio.Future[str] = loop.create_future()

        async with self._lock:
            state = self._tasks.get(task_id)
            if not state:
                raise TaskNotFoundError(task_id)

            state.status = "pending"
            state.value = None
            state.error = error
            state.updated_at = utcnow()
            self._task_waiters[task_id] = new_future
            LOGGER.info("Reset task %s for retry: %s", task_id, error)

    async def mark_task_completed(self, task_id: str) -> None:
        async with self._lock:
            state = self._tasks.get(task_id)
            if not state:
                raise TaskNotFoundError(task_id)
            state.status = "completed"
            state.updated_at = utcnow()
            LOGGER.info("Task %s marked completed", task_id)

    async def submit_task_value(self, task_id: str, value: str) -> None:
        async with self._lock:
            future = self._task_waiters.get(task_id)
            state = self._tasks.get(task_id)
            if not state or future is None:
                raise TaskNotFoundError(task_id)

            if state.status not in {"pending", "retry"}:
                LOGGER.warning(
                    "Received submission for task %s but status is %s",
                    task_id,
                    state.status,
                )

            state.status = "submitted"
            state.value = value
            state.error = None
            state.updated_at = utcnow()

        if not future.done():
            future.set_result(value)
        else:
            LOGGER.warning(
                "Future for task %s already resolved before submission", task_id
            )

    async def update_service(
        self,
        name: str,
        *,
        state: str,
        detail: Optional[str] = None,
        next_run_at: Optional[datetime] = None,
        last_run_at: Optional[datetime] = None,
    ) -> None:
        async with self._lock:
            prev = self._services.get(name)
            payload = ServiceStatus(
                name=name,
                state=state,
                detail=detail
                if detail is not None
                else (prev.detail if prev else None),
                next_run_at=(
                    next_run_at
                    if next_run_at is not None
                    else (prev.next_run_at if prev else None)
                ),
                last_run_at=(
                    last_run_at
                    if last_run_at is not None
                    else (prev.last_run_at if prev else None)
                ),
            )
            payload.updated_at = utcnow()
            self._services[name] = payload
            LOGGER.debug("Service %s updated → %s", name, state)

    async def update_next_run(self, name: str, next_run_at: Optional[datetime]) -> None:
        async with self._lock:
            prevailing = self._services.get(name)
            if not prevailing:
                prevailing = ServiceStatus(name=name, state="idle")
            prevailing.next_run_at = next_run_at
            prevailing.updated_at = utcnow()
            self._services[name] = prevailing

    async def mark_service_run(
        self,
        name: str,
        *,
        state: str,
        detail: Optional[str] = None,
        interval_seconds: Optional[float] = None,
    ) -> None:
        next_run = (
            utcnow() + timedelta(seconds=interval_seconds) if interval_seconds else None
        )
        await self.update_service(
            name,
            state=state,
            detail=detail,
            next_run_at=next_run,
            last_run_at=utcnow(),
        )

    async def get_state(self) -> dict[str, Any]:
        async with self._lock:
            services = [service.to_dict() for service in self._services.values()]
            tasks = [
                {
                    **task.to_dict(),
                    "url": self.task_url(task.id),
                }
                for task in self._tasks.values()
            ]
            return {
                "started_at": self.started_at.isoformat(),
                "services": services,
                "tasks": tasks,
            }


__all__ = ["AppManager", "AppTask", "TaskState", "ServiceStatus"]

from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from queue import Empty, Queue
from threading import RLock
from typing import Any, Callable


ProgressReporter = Callable[[str, dict[str, Any]], None]
JobTask = Callable[[ProgressReporter], Any]


@dataclass(frozen=True, slots=True)
class JobSnapshot:
    name: str
    state: str
    stage: str = ""
    data: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class CompletedJob:
    name: str
    result: Any = None
    error: str = ""


@dataclass(slots=True)
class _ManagedJob:
    name: str
    token: int
    future: Future
    state: str = "queued"
    stage: str = ""
    data: dict[str, Any] = field(default_factory=dict)
    delivered: bool = False


class SchoolJobManager:
    """Keeps school searches alive across Streamlit reruns."""

    def __init__(self, max_workers: int = 3):
        self.max_workers = max_workers
        self._executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="school-queue")
        self._events: Queue = Queue()
        self._jobs: dict[str, _ManagedJob] = {}
        self._lock = RLock()
        self._next_token = 0

    def submit(self, name: str, task: JobTask) -> bool:
        with self._lock:
            existing = self._jobs.get(name)
            if existing and existing.state in {"queued", "running", "finished"}:
                return False
            self._next_token += 1
            token = self._next_token

            def report(stage: str, data: dict[str, Any]) -> None:
                self._events.put((name, token, stage, dict(data)))

            def execute() -> Any:
                self._events.put((name, token, "running", {}))
                return task(report)

            future = self._executor.submit(execute)
            self._jobs[name] = _ManagedJob(name=name, token=token, future=future)
            return True

    def poll(self) -> tuple[list[CompletedJob], list[JobSnapshot]]:
        with self._lock:
            while True:
                try:
                    name, token, stage, data = self._events.get_nowait()
                except Empty:
                    break
                job = self._jobs.get(name)
                if not job or job.token != token:
                    continue
                if stage == "running":
                    job.state = "running"
                else:
                    job.stage = stage
                    job.data = data

            completed: list[CompletedJob] = []
            for job in self._jobs.values():
                if not job.future.done() or job.delivered:
                    continue
                job.delivered = True
                job.state = "finished"
                try:
                    completed.append(CompletedJob(job.name, result=job.future.result()))
                except Exception as exc:
                    completed.append(CompletedJob(job.name, error=f"{type(exc).__name__}: {exc}"))

            snapshots = [
                JobSnapshot(job.name, job.state, job.stage, dict(job.data))
                for job in self._jobs.values()
            ]
            return completed, snapshots

    def has_active(self) -> bool:
        with self._lock:
            return any(job.state in {"queued", "running"} and not job.future.done() for job in self._jobs.values())

    def contains(self, name: str) -> bool:
        with self._lock:
            return name in self._jobs

    def cancel_all(self) -> None:
        with self._lock:
            for job in self._jobs.values():
                job.future.cancel()
            self._jobs.clear()
            while True:
                try:
                    self._events.get_nowait()
                except Empty:
                    break

    def shutdown(self) -> None:
        self.cancel_all()
        self._executor.shutdown(wait=False, cancel_futures=True)

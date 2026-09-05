"""Background worker for retryable memory jobs."""

from __future__ import annotations

from threading import Event, Lock, Thread
from time import sleep
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .recorder import MemoryRecorder


class MemoryWorker:
    def __init__(self, recorder: MemoryRecorder, *, poll_interval: float = 0.05) -> None:
        self.recorder = recorder
        self.poll_interval = poll_interval
        self.processed_jobs = 0
        self.errors: list[str] = []
        self._stop = Event()
        self._thread: Thread | None = None
        self._lock = Lock()

    def start(self) -> None:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._stop.clear()
            self._thread = Thread(target=self._run, name="antisentinel-memory-worker", daemon=True)
            self._thread.start()

    def stop(self, timeout: float = 5.0) -> bool:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout)
        thread_stopped = self._thread is None or not self._thread.is_alive()
        inflight = self.recorder.memory_jobs.has_inflight() if hasattr(self.recorder.memory_jobs, "has_inflight") else False
        return thread_stopped and not inflight and not self.errors

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                result = self.recorder.process_one_memory_job()
                if result is not None:
                    self.processed_jobs += 1
                else:
                    sleep(self.poll_interval)
            except Exception as exc:  # noqa: BLE001 - worker keeps retryable jobs observable
                self.errors.append(str(exc))
                sleep(self.poll_interval)

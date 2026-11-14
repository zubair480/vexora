"""Bounded, thread-safe event bus. Shared file: owned by the integration session.

Band callbacks only call `publish` / `publish_imu`, which enqueue and return. One worker thread
persists events and calls subscribers, so slow work never runs on the band's thread.
"""
from __future__ import annotations

import logging
import queue
import threading
from typing import Callable, Iterable, Optional

from . import database
from .models import NOT_PERSISTED, Event, ImuBatch

log = logging.getLogger("beltguard.bus")


class EventBus:
    def __init__(self, maxsize: int = 2000, imu_maxsize: int = 500, persist: bool = True) -> None:
        self._events: queue.Queue = queue.Queue(maxsize)
        self._imu: queue.Queue = queue.Queue(imu_maxsize)
        self._subs: list[tuple[Callable[[Event], None], Optional[frozenset]]] = []
        self._imu_subs: list[Callable[[ImuBatch], None]] = []
        self._threads: list[threading.Thread] = []
        self._stop = threading.Event()
        self.persist = persist
        self.dropped_events = 0
        self.dropped_imu = 0

    # producers: never block
    def publish(self, event: Event) -> bool:
        try:
            self._events.put_nowait(event)
            return True
        except queue.Full:
            self.dropped_events += 1
            return False

    def publish_imu(self, batch: ImuBatch) -> bool:
        try:
            self._imu.put_nowait(batch)
            return True
        except queue.Full:
            self.dropped_imu += 1   # visible in /api/health: never hidden
            return False

    # consumers
    def subscribe(self, callback: Callable[[Event], None], types: Optional[Iterable[str]] = None) -> None:
        self._subs.append((callback, frozenset(types) if types else None))

    def subscribe_imu(self, callback: Callable[[ImuBatch], None]) -> None:
        self._imu_subs.append(callback)

    def start(self) -> None:
        if self._threads:
            return
        self._stop.clear()
        for target, name in ((self._run_events, "bus-events"), (self._run_imu, "bus-imu")):
            thread = threading.Thread(target=target, name=name, daemon=True)
            thread.start()
            self._threads.append(thread)

    def stop(self) -> None:
        self._stop.set()
        for thread in self._threads:
            thread.join(timeout=2)
        self._threads.clear()

    def drain(self, timeout: float = 2.0) -> None:
        """Tests: wait until everything published so far has been handled."""
        import time
        end = time.time() + timeout
        while time.time() < end and (self._events.unfinished_tasks or self._imu.unfinished_tasks):
            time.sleep(0.01)

    def _dispatch(self, event: Event) -> None:
        if self.persist and event.type not in NOT_PERSISTED:
            try:
                database.insert_event(event)
            except Exception:
                log.exception("could not store event %s", event.type)
        for callback, types in list(self._subs):
            if types is None or event.type in types:
                try:
                    callback(event)
                except Exception:
                    log.exception("subscriber failed on %s", event.type)

    def _run_events(self) -> None:
        while not self._stop.is_set():
            try:
                event = self._events.get(timeout=0.2)
            except queue.Empty:
                continue
            try:
                self._dispatch(event)
            finally:
                self._events.task_done()

    def _run_imu(self) -> None:
        while not self._stop.is_set():
            try:
                batch = self._imu.get(timeout=0.2)
            except queue.Empty:
                continue
            try:
                for callback in list(self._imu_subs):
                    try:
                        callback(batch)
                    except Exception:
                        log.exception("imu subscriber failed")
            finally:
                self._imu.task_done()


bus = EventBus()

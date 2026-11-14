"""Internal event queue + WebSocket broadcaster (S2 band-backend).

Band callbacks run on the Vexo library's own threads. They never touch the database, OpenAI
or Deepgram: they only call `hub.post(item)`, which hands the item to the asyncio loop with
`call_soon_threadsafe` and returns at once. One consumer task on the loop does the slow work.
"""
from __future__ import annotations

import asyncio
import json
import time


class Hub:
    def __init__(self) -> None:
        self.loop: asyncio.AbstractEventLoop | None = None
        self.queue: asyncio.Queue | None = None
        self.clients: set = set()
        self.dropped = 0

    def bind(self, loop: asyncio.AbstractEventLoop) -> None:
        self.loop = loop
        self.queue = asyncio.Queue(maxsize=5000)

    # ---- producers (any thread) -------------------------------------------------------
    def post(self, item: dict) -> None:
        """Thread-safe and non-blocking. Drops the item if the loop is gone or the queue is full."""
        loop = self.loop
        if loop is None or loop.is_closed():
            return
        try:
            loop.call_soon_threadsafe(self._put, item)
        except RuntimeError:  # loop closed while shutting down
            pass

    def _put(self, item: dict) -> None:
        try:
            self.queue.put_nowait(item)
        except asyncio.QueueFull:
            self.dropped += 1

    # ---- consumer side (loop thread) --------------------------------------------------
    async def get(self) -> dict:
        return await self.queue.get()

    async def broadcast(self, msg: dict) -> None:
        msg.setdefault("ts", time.time())
        msg.setdefault("source", "system")
        if not self.clients:
            return
        text = json.dumps(msg, default=str, separators=(",", ":"))
        dead = []
        for ws in list(self.clients):
            try:
                await asyncio.wait_for(ws.send_text(text), timeout=1.0)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.clients.discard(ws)

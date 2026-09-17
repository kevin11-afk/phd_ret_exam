"""
Broadcasts candidate state changes to connected admin dashboards.

This is a single-process, in-memory manager: fine for one uvicorn worker
serving 50-100 candidates. If you scale to multiple worker processes, this
needs to move to a shared pub/sub layer (Redis Pub/Sub, etc.) since
in-memory sets don't cross process boundaries — noted here rather than
silently left as a scaling trap.
"""
import asyncio
from typing import Set

from fastapi import WebSocket


class ConnectionManager:
    def __init__(self):
        self._admin_sockets: Set[WebSocket] = set()
        self._candidate_sockets: dict[int, WebSocket] = {} # user_id -> socket
        self._lock = asyncio.Lock()

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        async with self._lock:
            self._admin_sockets.add(websocket)

    async def disconnect(self, websocket: WebSocket):
        async with self._lock:
            self._admin_sockets.discard(websocket)

    async def broadcast(self, payload: dict):
        dead = []
        for ws in list(self._admin_sockets):
            try:
                await ws.send_json(payload)
            except Exception:
                dead.append(ws)
        if dead:
            async with self._lock:
                for ws in dead:
                    self._admin_sockets.discard(ws)


    async def connect_candidate(self, user_id: int, websocket: WebSocket):
        await websocket.accept()
        async with self._lock:
            self._candidate_sockets[user_id] = websocket

    async def disconnect_candidate(self, user_id: int):
        async with self._lock:
            if user_id in self._candidate_sockets:
                del self._candidate_sockets[user_id]

    async def send_to_candidate(self, user_id: int, payload: dict):
        async with self._lock:
            ws = self._candidate_sockets.get(user_id)
        if ws:
            try:
                await ws.send_json(payload)
            except Exception:
                await self.disconnect_candidate(user_id)

    async def send_to_admins(self, payload: dict):
        await self.broadcast(payload)

    # Note: Using native WebRTC now, so broadcast_frame is kept for backwards compatibility or fallback
    async def broadcast_frame(self, session_id: str, frame_data: str):
        payload = {
            "type": "video_frame",
            "session_id": session_id,
            "frame": frame_data
        }
        await self.broadcast(payload)


manager = ConnectionManager()

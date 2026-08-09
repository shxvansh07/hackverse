"""WebSocket fan-out for the doctor queue.

Isolated from routers so both the patient flow (new case handed off) and the
doctor flow (decision recorded) can publish without importing each other.

Broadcasts carry queue-level metadata only — case id, risk state, chief
complaint, status. Never the transcript, and never prescription content. A
socket is the wrong place for the clinical record; the dashboard fetches that
over HTTP when a doctor actually opens the case.
"""

from __future__ import annotations

import asyncio
import logging
from enum import Enum
from typing import Any, Dict, List, Set

from fastapi import WebSocket

logger = logging.getLogger(__name__)


class WSEvent(str, Enum):
    CONNECTED = "CONNECTED"
    CASE_CREATED = "CASE_CREATED"
    CASE_UPDATED = "CASE_UPDATED"
    CASE_DECIDED = "CASE_DECIDED"
    URGENT_ALERT = "URGENT_ALERT"


class ConnectionManager:
    def __init__(self) -> None:
        self._connections: Set[WebSocket] = set()
        self._lock = asyncio.Lock()

    @property
    def connection_count(self) -> int:
        return len(self._connections)

    async def connect(self, websocket: WebSocket) -> None:
        await websocket.accept()
        async with self._lock:
            self._connections.add(websocket)
        logger.info("Doctor dashboard connected (%d active)", len(self._connections))

    async def disconnect(self, websocket: WebSocket) -> None:
        async with self._lock:
            self._connections.discard(websocket)
        logger.info("Doctor dashboard disconnected (%d active)", len(self._connections))

    async def broadcast(self, event: WSEvent, payload: Dict[str, Any]) -> None:
        """Send to every live socket, dropping any that have gone away.

        A dead socket must never break the request that triggered the
        broadcast, so failures are collected and pruned rather than raised.
        """
        if not self._connections:
            return

        message = {"event": event.value, "data": payload}
        stale: List[WebSocket] = []

        async with self._lock:
            targets = list(self._connections)

        for connection in targets:
            try:
                await connection.send_json(message)
            except Exception:  # noqa: BLE001 - client vanished mid-send
                stale.append(connection)

        if stale:
            async with self._lock:
                for connection in stale:
                    self._connections.discard(connection)
            logger.info("Pruned %d stale doctor connections", len(stale))


ws_manager = ConnectionManager()


def case_queue_payload(case) -> Dict[str, Any]:
    """Project a TriageCase down to the fields the queue actually renders."""
    return {
        "case_id": case.case_id,
        "patient_id": case.patient_id,
        "preferred_language": case.preferred_language,
        "chief_complaint": case.chief_complaint or (case.symptoms[0] if case.symptoms else ""),
        "symptoms": case.symptoms,
        "triage_status": case.triage_status.value,
        "review_status": case.review_status.value,
        "red_flags": case.red_flags,
        "has_draft": bool(case.prescription_id),
        "summary_en": case.summary_en,
        "created_at": case.created_at,
        "updated_at": case.updated_at,
    }

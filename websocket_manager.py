import asyncio
import logging
from collections import defaultdict
from fastapi import WebSocket

logger = logging.getLogger(__name__)


class ConnectionManager:
    """Mantiene clientes WebSocket separados por ID de vehículo."""

    def __init__(self):
        self._connections: dict[int, set[WebSocket]] = defaultdict(set)
        self._lock = asyncio.Lock()

    async def connect(self, vehicle_id: int, websocket: WebSocket):
        await websocket.accept()
        async with self._lock:
            self._connections[vehicle_id].add(websocket)

    def disconnect(self, vehicle_id: int, websocket: WebSocket):
        clients = self._connections.get(vehicle_id)
        if clients:
            clients.discard(websocket)
            if not clients:
                self._connections.pop(vehicle_id, None)

    async def broadcast_to_vehicle(self, vehicle_id: int, event: dict):
        clients = list(self._connections.get(vehicle_id, set()))
        failed = []
        for websocket in clients:
            try:
                await websocket.send_json(event)
            except Exception:
                failed.append(websocket)
                logger.debug("Falló envío WebSocket", exc_info=True)
        for websocket in failed:
            self.disconnect(vehicle_id, websocket)

    async def close_all(self):
        clients = [
            (vehicle_id, websocket)
            for vehicle_id, sockets in self._connections.items()
            for websocket in list(sockets)
        ]
        for vehicle_id, websocket in clients:
            try:
                await websocket.close(code=1001, reason="Servidor detenido")
            except Exception:
                pass
            self.disconnect(vehicle_id, websocket)

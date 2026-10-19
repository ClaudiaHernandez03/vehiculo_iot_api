# ==============================================================================
# MÓDULO: websocket_manager.py
# PROPÓSITO: Administrar las conexiones WebSocket en tiempo real de forma
#            separada por cada vehículo (usando su id_vehiculo).
#            Así, cuando ocurre un movimiento o una DEMO en un vehículo,
#            el mensaje se envía a TODOS los clientes conectados a ese vehículo
#            sin mezclarse con otros vehículos.
# ==============================================================================

import asyncio
import logging
from collections import defaultdict
from fastapi import WebSocket

logger = logging.getLogger(__name__)


class ConnectionManager:
    """Mantiene clientes WebSocket separados por ID de vehículo."""

    def __init__(self):
        # Diccionario donde la clave es el id_vehiculo y el valor es un conjunto (set)
        # con todas las conexiones WebSocket activas para ese vehículo.
        self._connections: dict[int, set[WebSocket]] = defaultdict(set)
        # Candado asíncrono para evitar conflictos cuando varios clientes se conectan a la vez
        self._lock = asyncio.Lock()

    async def connect(self, vehicle_id: int, websocket: WebSocket):
        """Acepta una nueva conexión WebSocket y la registra en el grupo del vehículo."""
        await websocket.accept()
        async with self._lock:
            self._connections[vehicle_id].add(websocket)

    def disconnect(self, vehicle_id: int, websocket: WebSocket):
        """Elimina un cliente WebSocket cuando se desconecta y limpia la lista si queda vacía."""
        clients = self._connections.get(vehicle_id)
        if clients:
            clients.discard(websocket)
            if not clients:
                self._connections.pop(vehicle_id, None)

    async def broadcast_to_vehicle(self, vehicle_id: int, event: dict):
        """Envía un mensaje JSON en tiempo real a todos los clientes conectados a un vehículo."""
        clients = list(self._connections.get(vehicle_id, set()))
        failed = []
        for websocket in clients:
            try:
                await websocket.send_json(event)
            except Exception:
                # Si un cliente ya se desconectó o falló, lo marcamos para retirarlo
                failed.append(websocket)
                logger.debug("Falló envío WebSocket", exc_info=True)
        for websocket in failed:
            self.disconnect(vehicle_id, websocket)

    async def close_all(self):
        """Cierra ordenadamente todas las conexiones WebSocket cuando el servidor se apaga."""
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

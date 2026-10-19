# ==============================================================================
# MÓDULO: demo_manager.py
# PROPÓSITO: Coordinar la ejecución asíncrona y temporizada de las secuencias
#            DEMO por vehículo:
#            - Impide que se ejecuten dos DEMO al mismo tiempo en un vehículo (409 Conflict).
#            - Consulta los N movimientos con sp_obtener_movimientos_demo.
#            - Respeta el orden, duracion_ms y pausa_ms de cada paso.
#            - Actualiza el último estado en RDS sin ensuciar el historial individual.
#            - Permite cancelar una DEMO en cualquier momento y notifica por WebSocket.
# ==============================================================================

import asyncio
from datetime import datetime, timezone
import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)


class DemoManager:
    """Gestiona la ejecución temporizada y cancelación de demostraciones por vehículo."""

    def __init__(self, db, connection_manager):
        # Recibe las instancias de Database (RDS) y ConnectionManager (WebSocket)
        self.db = db
        self.ws_manager = connection_manager
        # Tareas asíncronas activas en segundo plano indexadas por id_vehiculo
        self._active_tasks: dict[int, asyncio.Task] = {}
        # Información descriptiva de la DEMO que se está ejecutando en cada vehículo
        self._active_demos_info: dict[int, dict[str, Any]] = {}
        # Candado para evitar condiciones de carrera si llegan dos POST al mismo tiempo
        self._lock = asyncio.Lock()

    def is_running(self, id_vehiculo: int) -> bool:
        """Indica si el vehículo tiene actualmente una DEMO en ejecución."""
        task = self._active_tasks.get(id_vehiculo)
        return task is not None and not task.done()

    def get_active_demo_info(self, id_vehiculo: int) -> Optional[dict[str, Any]]:
        """Devuelve los datos de la DEMO activa del vehículo, o None si no hay ninguna."""
        if self.is_running(id_vehiculo):
            return self._active_demos_info.get(id_vehiculo)
        return None

    async def start_demo(self, id_demo: int) -> dict[str, Any]:
        """Valida e inicia una DEMO en segundo plano para el vehículo asociado."""
        # 1. Verificar que la DEMO exista y esté activa en RDS
        demo_info = await asyncio.to_thread(self.db.obtener_info_demo, id_demo)
        if not demo_info:
            raise LookupError(f"La DEMO {id_demo} no existe.")
        if not demo_info.get("activo"):
            raise ValueError(f"La DEMO {id_demo} está inactiva.")

        id_vehiculo = demo_info["id_vehiculo"]

        # 2. Verificar que el vehículo asociado exista y esté activo
        vehiculo = await asyncio.to_thread(self.db.obtener_vehiculo_activo, id_vehiculo)
        if not vehiculo:
            raise LookupError(f"El vehículo {id_vehiculo} asociado a la DEMO no existe.")
        if not vehiculo.get("activo"):
            raise ValueError(f"El vehículo {id_vehiculo} está inactivo.")

        # 3. Obtener los N movimientos de la DEMO desde sp_obtener_movimientos_demo
        movimientos = await asyncio.to_thread(self.db.obtener_movimientos_demo, id_demo)
        if not movimientos:
            raise ValueError(f"La DEMO {id_demo} no tiene movimientos registrados.")

        # Ordenar estrictamente los pasos por su número de orden
        movimientos.sort(key=lambda m: int(m.get("orden", 0)))

        # 4. Controlar simultaneidad: si ya corre una DEMO en ese vehículo, lanzar error (HTTP 409)
        async with self._lock:
            if self.is_running(id_vehiculo):
                raise RuntimeError(
                    f"Ya existe una DEMO en ejecución para el vehículo {id_vehiculo}."
                )

            # Crear la tarea en segundo plano para que el endpoint responda inmediato (202 Accepted)
            task = asyncio.create_task(
                self._run_demo_loop(id_vehiculo, demo_info, movimientos)
            )
            self._active_tasks[id_vehiculo] = task
            self._active_demos_info[id_vehiculo] = {
                "id_demo": id_demo,
                "id_vehiculo": id_vehiculo,
                "nombre": demo_info.get("nombre"),
                "total_pasos": len(movimientos),
                "iniciado_en": datetime.now(timezone.utc).isoformat(),
            }

        return {
            "ok": True,
            "mensaje": "DEMO iniciada correctamente",
            "id_demo": id_demo,
            "id_vehiculo": id_vehiculo,
            "nombre": demo_info.get("nombre"),
            "total_pasos": len(movimientos),
        }

    async def cancel_demo(self, id_vehiculo: int) -> dict[str, Any]:
        """Cancela la DEMO en curso para el vehículo especificado."""
        async with self._lock:
            task = self._active_tasks.get(id_vehiculo)
            if not task or task.done():
                return {
                    "ok": False,
                    "mensaje": f"No hay ninguna DEMO activa para el vehículo {id_vehiculo}.",
                }
            # Solicita cancelar la tarea asíncrona (dispara asyncio.CancelledError en el ciclo)
            task.cancel()

        return {
            "ok": True,
            "mensaje": f"Cancelación de DEMO solicitada para el vehículo {id_vehiculo}.",
        }

    async def cancel_demo_by_id(self, id_demo: int) -> dict[str, Any]:
        """Cancela la DEMO en curso buscando por id_demo."""
        async with self._lock:
            target_vehiculo = None
            for veh_id, info in self._active_demos_info.items():
                if info.get("id_demo") == id_demo and self.is_running(veh_id):
                    target_vehiculo = veh_id
                    break

        if target_vehiculo is not None:
            return await self.cancel_demo(target_vehiculo)

        demo_info = await asyncio.to_thread(self.db.obtener_info_demo, id_demo)
        if demo_info:
            return await self.cancel_demo(demo_info["id_vehiculo"])

        return {
            "ok": False,
            "mensaje": f"No se encontró una DEMO activa con id {id_demo}.",
        }

    async def cancel_all(self):
        """Cancela todas las demos activas al detener la aplicación."""
        async with self._lock:
            tasks = [t for t in self._active_tasks.values() if not t.done()]
            for t in tasks:
                t.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _run_demo_loop(self, id_vehiculo: int, demo_info: dict, movimientos: list):
        """Ciclo interno que transmite por WebSocket el inicio, los N pasos temporizados y el fin."""
        id_demo = demo_info["id_demo"]
        total_pasos = len(movimientos)
        paso_actual = 0
        try:
            # 1. Enviar evento 'demo_inicio' a todos los clientes WebSocket del vehículo
            evento_inicio = {
                "tipo": "demo_inicio",
                "id_vehiculo": id_vehiculo,
                "id_demo": id_demo,
                "nombre": demo_info.get("nombre"),
                "total_pasos": total_pasos,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
            await self.ws_manager.broadcast_to_vehicle(id_vehiculo, evento_inicio)

            # 2. Recorrer cada uno de los N movimientos respetando duracion_ms y pausa_ms
            for idx, mov in enumerate(movimientos, start=1):
                paso_actual = idx
                id_comando = mov["id_comando"]
                duracion_ms = max(0, int(mov.get("duracion_ms", 0)))
                pausa_ms = max(0, int(mov.get("pausa_ms", 0)))

                # Actualizar el último estado en RDS (SIN registrar en historial_comandos)
                await asyncio.to_thread(
                    self.db.actualizar_estado_paso_demo,
                    id_vehiculo,
                    id_comando,
                )

                # Notificar el paso actual por WebSocket
                evento_paso = {
                    "tipo": "demo_paso",
                    "id_vehiculo": id_vehiculo,
                    "id_demo": id_demo,
                    "paso": paso_actual,
                    "total_pasos": total_pasos,
                    "orden": mov.get("orden", idx),
                    "id_comando": id_comando,
                    "comando": mov.get("comando"),
                    "codigo": mov.get("codigo"),
                    "duracion_ms": duracion_ms,
                    "pausa_ms": pausa_ms,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                }
                await self.ws_manager.broadcast_to_vehicle(id_vehiculo, evento_paso)

                # Esperar la duración del movimiento y luego la pausa antes del siguiente paso
                if duracion_ms > 0:
                    await asyncio.sleep(duracion_ms / 1000.0)

                if pausa_ms > 0:
                    await asyncio.sleep(pausa_ms / 1000.0)

            # 3. Enviar evento 'demo_fin' al concluir todos los pasos
            evento_fin = {
                "tipo": "demo_fin",
                "id_vehiculo": id_vehiculo,
                "id_demo": id_demo,
                "total_pasos_ejecutados": total_pasos,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
            await self.ws_manager.broadcast_to_vehicle(id_vehiculo, evento_fin)

        except asyncio.CancelledError:
            # Si se canceló la DEMO antes de terminar, enviar evento 'demo_cancelada' por WebSocket
            logger.info(
                "DEMO %s para vehículo %s fue cancelada en el paso %s",
                id_demo,
                id_vehiculo,
                paso_actual,
            )
            evento_cancelada = {
                "tipo": "demo_cancelada",
                "id_vehiculo": id_vehiculo,
                "id_demo": id_demo,
                "paso_interrumpido": paso_actual,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
            try:
                await self.ws_manager.broadcast_to_vehicle(id_vehiculo, evento_cancelada)
            except Exception:
                logger.exception("Error al notificar cancelación de demo por WebSocket")
            raise
        except Exception:
            logger.exception("Error durante la ejecución de la DEMO %s", id_demo)
            evento_error = {
                "tipo": "demo_error",
                "id_vehiculo": id_vehiculo,
                "id_demo": id_demo,
                "mensaje": "Error inesperado durante la ejecución de la DEMO",
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
            try:
                await self.ws_manager.broadcast_to_vehicle(id_vehiculo, evento_error)
            except Exception:
                pass
        finally:
            # Al terminar o cancelar, liberar el vehículo para que pueda correr otra DEMO después
            async with self._lock:
                self._active_tasks.pop(id_vehiculo, None)
                self._active_demos_info.pop(id_vehiculo, None)

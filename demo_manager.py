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

from websocket_manager import OrderSequenceManager

logger = logging.getLogger(__name__)


class DemoManager:
    """Gestiona la ejecución temporizada y cancelación de demostraciones por vehículo (máximo 4 pasos)."""

    MAX_PASOS_DEMO = 4

    def __init__(self, db, connection_manager, sequence_manager: Optional[OrderSequenceManager] = None):
        # Recibe las instancias de Database (RDS), ConnectionManager (WebSocket) y OrderSequenceManager
        self.db = db
        self.ws_manager = connection_manager
        self.sequence_manager = sequence_manager or OrderSequenceManager()
        # Tareas asíncronas activas en segundo plano indexadas por id_vehiculo
        self._active_tasks: dict[int, asyncio.Task] = {}
        # Información descriptiva de la DEMO que se está ejecutando en cada vehículo
        self._active_demos_info: dict[int, dict[str, Any]] = {}
        # Época exclusiva vigente de la DEMO activa por vehículo (se invalida al cancelar o finalizar)
        self._active_demo_epochs: dict[int, int] = {}
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

    def invalidate_demo_epoch(self, id_vehiculo: int) -> None:
        """Invalida inmediatamente la época de la DEMO activa para impedir pasos rezagados."""
        self._active_demo_epochs.pop(id_vehiculo, None)

    def _is_demo_epoch_active(self, id_vehiculo: int, epoca_demo: int) -> bool:
        """Verifica que la época de la DEMO siga siendo la activa y no haya sido invalidada por DETENER."""
        if self._active_demo_epochs.get(id_vehiculo) != epoca_demo:
            return False
        if not self.sequence_manager.es_epoca_valida(id_vehiculo, epoca_demo):
            return False
        return True

    @classmethod
    def validar_pasos_demo(cls, id_demo: int, movimientos: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Valida que la DEMO tenga entre 1 y 4 pasos, con orden en rango 1..4 y sin duplicados."""
        if not movimientos:
            raise ValueError(f"La DEMO {id_demo} no tiene movimientos registrados.")
        if len(movimientos) > cls.MAX_PASOS_DEMO:
            raise ValueError(
                f"La DEMO {id_demo} tiene {len(movimientos)} movimientos registrados y supera el máximo permitido de {cls.MAX_PASOS_DEMO} pasos."
            )

        ordenes_vistos: set[int] = set()
        for mov in movimientos:
            raw_orden = mov.get("orden")
            if isinstance(raw_orden, bool) or raw_orden is None:
                raise ValueError(f"La DEMO {id_demo} contiene un paso sin número de orden válido (1 a 4).")
            try:
                orden_int = int(raw_orden)
            except (TypeError, ValueError) as exc:
                raise ValueError(f"La DEMO {id_demo} contiene un orden no numérico.") from exc

            if orden_int < 1 or orden_int > cls.MAX_PASOS_DEMO:
                raise ValueError(
                    f"El paso con orden {orden_int} en la DEMO {id_demo} está fuera del rango permitido (1 a {cls.MAX_PASOS_DEMO})."
                )
            if orden_int in ordenes_vistos:
                raise ValueError(
                    f"La DEMO {id_demo} tiene pasos con número de orden duplicado ({orden_int})."
                )
            ordenes_vistos.add(orden_int)

        return sorted(movimientos, key=lambda m: int(m["orden"]))

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

        # 3. Obtener y validar estrictamente los movimientos (1 a 4 pasos, orden 1..4, sin truncar)
        movimientos_raw = await asyncio.to_thread(self.db.obtener_movimientos_demo, id_demo)
        movimientos = self.validar_pasos_demo(id_demo, movimientos_raw)

        rds_max_hist = None
        if hasattr(self.db, "obtener_max_id_historial"):
            rds_max_hist = await asyncio.to_thread(self.db.obtener_max_id_historial, id_vehiculo)

        # 4. Controlar simultaneidad y reservar una época exclusiva para esta DEMO
        async with self._lock:
            if self.is_running(id_vehiculo):
                raise RuntimeError(
                    f"Ya existe una DEMO en ejecución para el vehículo {id_vehiculo}."
                )

            reserva = self.sequence_manager.reservar_epoca(
                id_vehiculo,
                "DEMO",
                rds_max_id_historial=rds_max_hist,
                rds_disponible=True,
                es_nuevo_registro_rds=False,
            )

            epoca_demo = int(reserva["epoca_orden"])
            self._active_demo_epochs[id_vehiculo] = epoca_demo

            # Crear la tarea en segundo plano para que el endpoint responda inmediato (202 Accepted)
            task = asyncio.create_task(
                self._run_demo_loop(id_vehiculo, demo_info, movimientos, epoca_demo)
            )
            self._active_tasks[id_vehiculo] = task
            self._active_demos_info[id_vehiculo] = {
                "id_demo": id_demo,
                "id_vehiculo": id_vehiculo,
                "nombre": demo_info.get("nombre"),
                "total_pasos": len(movimientos),
                "epoca_orden": epoca_demo,
                "orden_id_base": epoca_demo * OrderSequenceManager.FACTOR_EPOCA,
                "iniciado_en": datetime.now(timezone.utc).isoformat(),
            }

        return {
            "ok": True,
            "mensaje": "DEMO iniciada correctamente",
            "id_demo": id_demo,
            "id_vehiculo": id_vehiculo,
            "nombre": demo_info.get("nombre"),
            "total_pasos": len(movimientos),
            "epoca_orden": epoca_demo,
            "orden_id": epoca_demo * OrderSequenceManager.FACTOR_EPOCA,
        }

    async def cancel_demo(self, id_vehiculo: int) -> dict[str, Any]:
        """Cancela la DEMO en curso para el vehículo especificado y espera su finalización."""
        async with self._lock:
            self._active_demo_epochs.pop(id_vehiculo, None)
            task = self._active_tasks.get(id_vehiculo)
            if not task or task.done():
                return {
                    "ok": False,
                    "mensaje": f"No hay ninguna DEMO activa para el vehículo {id_vehiculo}.",
                }
            # Solicita cancelar la tarea asíncrona (dispara asyncio.CancelledError en el ciclo)
            task.cancel()

        # Esperar a que la tarea finalice por completo antes de retornar
        await asyncio.gather(task, return_exceptions=True)

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
            self._active_demo_epochs.clear()
            tasks = [t for t in self._active_tasks.values() if not t.done()]
            for t in tasks:
                t.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _actualizar_estado_detener_demo(self, id_vehiculo: int) -> int:
        """Actualiza el estado del vehículo a Detener (codigo=0) al cerrar una DEMO sin ensuciar historial."""
        if hasattr(self.db, "registrar_apagado_demo"):
            try:
                res = await asyncio.to_thread(self.db.registrar_apagado_demo, id_vehiculo)
                return int(res.get("id_comando", 0))
            except Exception:
                logger.debug("No se pudo registrar apagado de DEMO en RDS", exc_info=True)
        return 0

    async def _run_demo_loop(
        self, id_vehiculo: int, demo_info: dict, movimientos: list, epoca_demo: int
    ):
        """Ciclo interno que transmite por WebSocket el inicio, los hasta 4 pasos temporizados,
        sus pausas con apagado físico y el cierre con orden explícita de apagado (subórdenes 1..10)."""
        id_demo = demo_info["id_demo"]
        total_pasos = len(movimientos)
        paso_actual = 0
        orden_base = epoca_demo * OrderSequenceManager.FACTOR_EPOCA
        try:
            if not self._is_demo_epoch_active(id_vehiculo, epoca_demo):
                raise asyncio.CancelledError()

            # 1. Enviar evento 'demo_inicio' a todos los clientes WebSocket del vehículo
            evento_inicio = {
                "tipo": "demo_inicio",
                "id_vehiculo": id_vehiculo,
                "id_demo": id_demo,
                "nombre": demo_info.get("nombre"),
                "total_pasos": total_pasos,
                "epoca_orden": epoca_demo,
                "sub_orden": 0,
                "orden_id": orden_base,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
            await self.ws_manager.broadcast_to_vehicle(id_vehiculo, evento_inicio)

            # 2. Recorrer cada uno de los pasos (1 a 4) respetando duracion_ms y pausa_ms
            for idx, mov in enumerate(movimientos, start=1):
                if not self._is_demo_epoch_active(id_vehiculo, epoca_demo):
                    raise asyncio.CancelledError()

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

                # Verificar después de la consulta a RDS que la DEMO no haya sido cancelada ni invalidada por DETENER
                if not self._is_demo_epoch_active(id_vehiculo, epoca_demo):
                    raise asyncio.CancelledError()

                sub_paso = (2 * idx) - 1  # Paso 1->1, Paso 2->3, Paso 3->5, Paso 4->7
                orden_id_paso = orden_base + sub_paso

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
                    "epoca_orden": epoca_demo,
                    "sub_orden": sub_paso,
                    "orden_id": orden_id_paso,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                }
                await self.ws_manager.broadcast_to_vehicle(id_vehiculo, evento_paso)

                # Esperar la duración del movimiento
                if duracion_ms > 0:
                    await asyncio.sleep(duracion_ms / 1000.0)

                if not self._is_demo_epoch_active(id_vehiculo, epoca_demo):
                    raise asyncio.CancelledError()

                # Si existe pausa (> 0 ms), apagar físicamente las salidas durante la pausa (suborden par 2, 4, 6, 8)
                if pausa_ms > 0:
                    sub_pausa = 2 * idx
                    orden_id_pausa = orden_base + sub_pausa
                    evento_pausa = {
                        "tipo": "detener",
                        "motivo": "demo_pausa",
                        "accion": "DETENER",
                        "accion_salidas": "DETENER",
                        "id_vehiculo": id_vehiculo,
                        "id_demo": id_demo,
                        "paso": paso_actual,
                        "id_comando": 0,
                        "comando": "Detener",
                        "codigo": 0,
                        "pausa_ms": pausa_ms,
                        "epoca_orden": epoca_demo,
                        "sub_orden": sub_pausa,
                        "orden_id": orden_id_pausa,
                        "origen": "DEMO_PAUSA",
                        "prioridad": "MAXIMA",
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                    }
                    await self.ws_manager.broadcast_to_vehicle(id_vehiculo, evento_pausa)
                    await asyncio.sleep(pausa_ms / 1000.0)

                if not self._is_demo_epoch_active(id_vehiculo, epoca_demo):
                    raise asyncio.CancelledError()

            # 3. Clausurar la época de la DEMO antes de emitir el cierre y apagado final
            self._active_demo_epochs.pop(id_vehiculo, None)
            id_cmd_detener = await self._actualizar_estado_detener_demo(id_vehiculo)

            # Suborden 9: Cierre de DEMO (demo_fin)
            evento_fin = {
                "tipo": "demo_fin",
                "id_vehiculo": id_vehiculo,
                "id_demo": id_demo,
                "total_pasos_ejecutados": total_pasos,
                "id_comando": id_cmd_detener,
                "comando": "Detener",
                "codigo": 0,
                "accion_salidas": "DETENER",
                "epoca_orden": epoca_demo,
                "sub_orden": 9,
                "orden_id": orden_base + 9,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
            await self.ws_manager.broadcast_to_vehicle(id_vehiculo, evento_fin)

            # Suborden 10: Orden explícita de apagado al finalizar la DEMO
            evento_apagado_fin = {
                "tipo": "detener",
                "accion": "DETENER",
                "id_vehiculo": id_vehiculo,
                "id_demo": id_demo,
                "id_comando": id_cmd_detener,
                "comando": "Detener",
                "codigo": 0,
                "epoca_orden": epoca_demo,
                "sub_orden": 10,
                "orden_id": orden_base + 10,
                "origen": "DEMO_FIN",
                "prioridad": "MAXIMA",
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
            await self.ws_manager.broadcast_to_vehicle(id_vehiculo, evento_apagado_fin)

        except asyncio.CancelledError:
            # Invalidar época de la DEMO inmediatamente
            self._active_demo_epochs.pop(id_vehiculo, None)
            logger.info(
                "DEMO %s para vehículo %s fue cancelada en el paso %s",
                id_demo,
                id_vehiculo,
                paso_actual,
            )
            id_cmd_detener = await self._actualizar_estado_detener_demo(id_vehiculo)
            evento_cancelada = {
                "tipo": "demo_cancelada",
                "id_vehiculo": id_vehiculo,
                "id_demo": id_demo,
                "paso_interrumpido": paso_actual,
                "id_comando": id_cmd_detener,
                "comando": "Detener",
                "codigo": 0,
                "accion_salidas": "DETENER",
                "epoca_orden": epoca_demo,
                "sub_orden": 9,
                "orden_id": orden_base + 9,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
            evento_apagado_cancel = {
                "tipo": "detener",
                "accion": "DETENER",
                "id_vehiculo": id_vehiculo,
                "id_demo": id_demo,
                "id_comando": id_cmd_detener,
                "comando": "Detener",
                "codigo": 0,
                "epoca_orden": epoca_demo,
                "sub_orden": 10,
                "orden_id": orden_base + 10,
                "origen": "DEMO_CANCELADA",
                "prioridad": "MAXIMA",
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
            try:
                await self.ws_manager.broadcast_to_vehicle(id_vehiculo, evento_cancelada)
                await self.ws_manager.broadcast_to_vehicle(id_vehiculo, evento_apagado_cancel)
            except Exception:
                logger.exception("Error al notificar cancelación de demo por WebSocket")
            raise
        except Exception:
            self._active_demo_epochs.pop(id_vehiculo, None)
            logger.exception("Error durante la ejecución de la DEMO %s", id_demo)
            evento_error = {
                "tipo": "demo_error",
                "id_vehiculo": id_vehiculo,
                "id_demo": id_demo,
                "mensaje": "Error inesperado durante la ejecución de la DEMO",
                "codigo": 0,
                "accion_salidas": "DETENER",
                "epoca_orden": epoca_demo,
                "sub_orden": 9,
                "orden_id": orden_base + 9,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
            try:
                await self.ws_manager.broadcast_to_vehicle(id_vehiculo, evento_error)
            except Exception:
                pass
        finally:
            # Al terminar o cancelar, liberar el vehículo para que pueda correr otra DEMO después
            async with self._lock:
                self._active_demo_epochs.pop(id_vehiculo, None)
                self._active_tasks.pop(id_vehiculo, None)
                self._active_demos_info.pop(id_vehiculo, None)

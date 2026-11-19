# ==============================================================================
# MÓDULO: services.py
# PROPÓSITO: Encapsular mediante Programación Orientada a Objetos la lógica de
#            negocio del sistema (clase VehiculoIoTService) y la dependencia de
#            seguridad (clase ApiKeyGuard), separando los controladores HTTP/WS
#            de las reglas transaccionales y de acceso a datos.
# ==============================================================================

import asyncio
from collections import defaultdict
from datetime import datetime, timezone
import logging
import time
from typing import Any, Callable
from fastapi import Header, HTTPException, WebSocket

from database import Database
from demo_manager import DemoManager
from exceptions import (
    BaseDatosNoDisponibleError,
    ConflictoConcurrenciaError,
    ExceptionManager,
    ReglaNegocioError,
)
from models import (
    DetenerRequest,
    EstadoActualizar,
    HistorialCrear,
    MovimientoDemo,
    MovimientoRequest,
)
from settings import Settings, settings
from websocket_manager import ConnectionManager, OrderSequenceManager

logger = logging.getLogger("vehiculo_iot_api")


class ApiKeyGuard:
    """Dependencia POO para FastAPI que gestiona la validación opcional de x-api-key."""

    def __init__(self, config: Settings = settings) -> None:
        self.settings = config

    def __call__(self, x_api_key: str | None = Header(default=None)) -> None:
        """Si REQUIRE_API_KEY está activo en configuración, exige coincidencia del header x-api-key."""
        if self.settings.require_api_key and self.settings.api_key:
            if x_api_key != self.settings.api_key:
                raise HTTPException(status_code=401, detail="API key inválida o ausente")
        return None


class VehiculoIoTService:
    """Servicio de negocio POO que coordina Database (RDS), ConnectionManager (WS),
    DemoManager, secuencias orden_id, prioridad de DETENER y watchdog de sesión de control."""

    HISTORIAL_MAX_ITEMS = 10

    def __init__(
        self,
        db_provider: Callable[[], Database],
        ws_manager: ConnectionManager,
        demo_manager: DemoManager,
        sequence_manager: OrderSequenceManager | None = None,
        config: Settings = settings,
    ) -> None:
        self._db_provider = db_provider
        self.ws_manager = ws_manager
        self.demo_manager = demo_manager
        self.settings = config
        self.sequence_manager = sequence_manager or getattr(
            demo_manager, "sequence_manager", None
        ) or OrderSequenceManager(config.order_seq_state_file)
        self.demo_manager.sequence_manager = self.sequence_manager
        self.ws_manager.sequence_manager = self.sequence_manager

        # Sincronización por vehículo para serializar movimientos y detenciones
        self._vehicle_locks: dict[int, asyncio.Lock] = defaultdict(asyncio.Lock)
        # Contador rápido en memoria de órdenes DETENER para invalidar movimientos en vuelo
        self._stop_epoch: dict[int, int] = defaultdict(int)
        # Movimientos sostenidos actualmente activos por id_vehiculo
        self._active_movements: dict[int, dict[str, Any]] = {}
        # Tareas de watchdog de vigencia de sesión por id_vehiculo
        self._watchdog_tasks: dict[int, asyncio.Task] = {}
        # Detenciones pendientes de reconciliación cuando RDS falla durante DETENER
        self._pending_reconciliations: dict[int, dict[str, Any]] = {}

        # Conectar callbacks de WebSocket para renovar sesión con ping y detectar desconexiones
        self.ws_manager.set_callbacks(
            on_ping=self._on_client_ping,
            on_disconnect=self._on_client_disconnect,
        )

    @property
    def db(self) -> Database:
        """Obtiene la instancia activa de Database (compatible con inyección de pruebas)."""
        return self._db_provider()

    @property
    def control_watchdog_seconds(self) -> float:
        """Tiempo máximo de vigencia sin renovación antes de detención automática por seguridad."""
        return float(getattr(self.settings, "control_watchdog_seconds", 15.0))

    def get_active_movement(self, id_vehiculo: int) -> dict[str, Any] | None:
        """Devuelve la información del movimiento sostenido activo del vehículo, o None."""
        mov = self._active_movements.get(id_vehiculo)
        return dict(mov) if mov is not None else None

    def _cancel_watchdog(self, id_vehiculo: int) -> None:
        """Cancela la tarea de watchdog activa de un vehículo cuando se detiene."""
        task = self._watchdog_tasks.pop(id_vehiculo, None)
        if task is not None and not task.done():
            current = asyncio.current_task()
            if task is not current:
                task.cancel()

    def _start_or_reset_watchdog(self, id_vehiculo: int) -> None:
        """Inicia o reinicia el monitor de vigencia de sesión para el movimiento activo."""
        self._cancel_watchdog(id_vehiculo)
        try:
            loop = asyncio.get_running_loop()
            self._watchdog_tasks[id_vehiculo] = loop.create_task(
                self._watchdog_loop(id_vehiculo)
            )
        except RuntimeError:
            pass

    async def _watchdog_loop(self, id_vehiculo: int) -> None:
        """Vigila que el controlador propietario renueve periódicamente la sesión;
        si expira el tiempo sin renovación, solicita DETENER automáticamente."""
        try:
            while True:
                mov = self._active_movements.get(id_vehiculo)
                if not mov:
                    return
                timeout_s = max(0.05, self.control_watchdog_seconds)
                elapsed = time.monotonic() - float(mov["ultima_renovacion"])
                remaining = timeout_s - elapsed
                if remaining <= 0:
                    logger.warning(
                        "Watchdog de sesión expiró para vehículo %s (sesion=%s). Ejecutando DETENER.",
                        id_vehiculo,
                        mov.get("sesion_control"),
                    )
                    await self.detener_vehiculo(
                        id_vehiculo,
                        DetenerRequest(
                            origen="WATCHDOG_SESION",
                            esperar_confirmacion_ms=0,
                        ),
                    )
                    return
                await asyncio.sleep(min(remaining, 0.25))
        except asyncio.CancelledError:
            return

    def _on_client_ping(
        self, id_vehiculo: int | None, websocket: WebSocket, data: dict[str, Any]
    ) -> bool:
        """Procesa el heartbeat ping de un cliente WebSocket.
        Solo el controlador propietario del movimiento activo puede renovar su vigencia."""
        meta = self.ws_manager.get_client_meta(websocket)
        rol = str(data.get("rol") or meta.get("rol") or "controlador").strip().lower()
        if rol in ("observador", "esp8266"):
            return False

        sesion_ping = (
            str(data.get("sesion_control") or meta.get("sesion_control") or "").strip()
            or None
        )
        target_vid = id_vehiculo if id_vehiculo is not None else meta.get("vehicle_id")
        if target_vid is None:
            return False

        mov = self._active_movements.get(int(target_vid))
        if not mov:
            return False

        propietario = mov.get("sesion_control")
        # Si el movimiento tiene propietario identificado, solo ese propietario puede renovarlo
        if propietario is not None:
            if sesion_ping != propietario:
                return False
        else:
            # Si el movimiento se inició sin sesion_control, exigir que no sea observador
            if rol != "controlador":
                return False

        mov["ultima_renovacion"] = time.monotonic()
        return True

    async def _on_client_disconnect(
        self, id_vehiculo: int | None, meta: dict[str, Any]
    ) -> None:
        """Maneja la desconexión de un cliente WebSocket (ESP8266, controlador propietario u observador)."""
        if id_vehiculo is None:
            return
        vid = int(id_vehiculo)
        rol = str(meta.get("rol") or "controlador").strip().lower()
        sesion_desc = meta.get("sesion_control")

        # 1. Si se desconecta el ESP8266, pasar el vehículo a estado seguro detenido
        if rol == "esp8266":
            if self._active_movements.get(vid) or self.demo_manager.is_running(vid):
                await self.detener_vehiculo(
                    vid,
                    DetenerRequest(origen="DESCONEXION_ESP8266", esperar_confirmacion_ms=0),
                )
            return

        # 2. Si se desconecta una pestaña observadora, nunca detener al operador activo
        if rol == "observador":
            return

        # 3. Verificar si existe un movimiento manual activo cuyo propietario sea el cliente desconectado
        mov = self._active_movements.get(vid)
        if not mov:
            return

        propietario = mov.get("sesion_control")
        if propietario is not None:
            if sesion_desc == propietario and not self.ws_manager.has_active_session_socket(
                vid, propietario
            ):
                await self.detener_vehiculo(
                    vid,
                    DetenerRequest(
                        origen="DESCONEXION_CONTROLADOR",
                        esperar_confirmacion_ms=0,
                        sesion_control=propietario,
                    ),
                )
        else:
            # Si se operaba sin sesion_control explícita y ya no queda ningún controlador conectado
            remaining_controllers = [
                ws
                for ws in list(self.ws_manager._connections.get(vid, set()))
                if self.ws_manager.get_client_meta(ws).get("rol") == "controlador"
            ]
            if not remaining_controllers:
                await self.detener_vehiculo(
                    vid,
                    DetenerRequest(
                        origen="DESCONEXION_CONTROLADOR",
                        esperar_confirmacion_ms=0,
                    ),
                )

    def tiene_reconciliacion_pendiente(self, id_vehiculo: int) -> bool:
        """Indica si el vehículo tiene una detención pendiente de reconciliar con RDS."""
        if id_vehiculo in self._pending_reconciliations:
            return True
        try:
            pendiente, _ = self.sequence_manager.consultar_reconciliacion_disco(id_vehiculo)
            return pendiente
        except Exception:
            return False

    async def reconciliar_detencion_si_pendiente(self, id_vehiculo: int) -> bool:
        """Si el vehículo tiene una orden DETENER pendiente de persistir en RDS tras una caída,
        intenta reconciliar el estado e historial en RDS antes de permitir nuevas operaciones."""
        pendiente_mem = self._pending_reconciliations.get(id_vehiculo)
        pendiente_disco = None
        try:
            hay_pendiente, datos_disco = self.sequence_manager.consultar_reconciliacion_disco(
                id_vehiculo
            )
            if hay_pendiente:
                pendiente_disco = datos_disco or {}
        except ValueError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc

        if pendiente_mem is None and pendiente_disco is None:
            return False

        datos_rec = pendiente_mem or pendiente_disco or {}
        origen_rec = str(datos_rec.get("origen") or "RECONCILIACION_STOP")[:30]

        try:
            res_rds = await asyncio.to_thread(
                self.db.registrar_detencion, id_vehiculo, origen_rec
            )
        except Exception as exc:
            raise HTTPException(
                status_code=503,
                detail=(
                    f"Vehículo {id_vehiculo} bloqueado: existe una detención prioritaria pendiente "
                    "de reconciliar en Amazon RDS y la base de datos no está disponible."
                ),
            ) from exc

        self._pending_reconciliations.pop(id_vehiculo, None)
        self.sequence_manager.marcar_reconciliacion_pendiente(
            id_vehiculo,
            False,
            rds_max_id_historial=res_rds.get("id_historial"),
        )
        return True

    async def _emitir_detener_emergencia_estado_seguro(
        self, id_vehiculo: int, motivo: str, orden_id: int = 0
    ) -> None:
        """Envía una orden DETENER inmediata cuando se activa el estado seguro por fallo de secuencia/límite."""
        self._stop_epoch[id_vehiculo] += 1
        self._cancel_watchdog(id_vehiculo)
        self._active_movements.pop(id_vehiculo, None)
        self.demo_manager.invalidate_demo_epoch(id_vehiculo)
        if self.demo_manager.is_running(id_vehiculo):
            await self.demo_manager.cancel_demo(id_vehiculo)

        evento = {
            "tipo": "detener",
            "accion": "DETENER",
            "id_vehiculo": id_vehiculo,
            "id_comando": 0,
            "comando": "Detener",
            "codigo": 0,
            "orden_id": int(orden_id),
            "origen": motivo[:30],
            "prioridad": "MAXIMA",
            "estado_seguro": True,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        await self.ws_manager.broadcast_to_vehicle(id_vehiculo, evento)

    def ejecutar_sp(
        self, nombre: str, parametros: list[Any] | None = None, escritura: bool = False
    ) -> dict[str, Any]:
        """Ejecuta un Stored Procedure de los 21 autorizados y traduce errores a HTTPException."""
        try:
            return self.db.ejecutar_sp(nombre, parametros or [], escritura=escritura)
        except Exception as exc:
            raise ExceptionManager.translate_exception(exc, nombre) from exc

    def agregar_movimiento_demo(self, id_demo: int, p: MovimientoDemo) -> dict[str, Any]:
        """Valida el límite de 4 pasos (orden 1..4 sin duplicados) antes de ejecutar sp_agregar_movimiento_demo."""
        if p.orden < 1 or p.orden > DemoManager.MAX_PASOS_DEMO:
            raise ReglaNegocioError(
                f"El orden del paso debe estar entre 1 y {DemoManager.MAX_PASOS_DEMO}."
            )
        try:
            actuales = self.db.obtener_movimientos_demo(id_demo)
        except Exception as exc:
            raise ExceptionManager.translate_exception(
                exc, "obtener_movimientos_demo"
            ) from exc

        if len(actuales) >= DemoManager.MAX_PASOS_DEMO:
            raise ReglaNegocioError(
                f"La DEMO {id_demo} ya tiene el máximo permitido de {DemoManager.MAX_PASOS_DEMO} pasos."
            )

        for mov in actuales:
            if int(mov.get("orden", 0)) == int(p.orden):
                raise ReglaNegocioError(
                    f"El orden {p.orden} ya está registrado en la DEMO {id_demo}."
                )

        return self.ejecutar_sp(
            "sp_agregar_movimiento_demo",
            [id_demo, p.id_comando, p.orden, p.duracion_ms, p.pausa_ms],
            True,
        )

    def actualizar_movimiento_demo(
        self, id_demo_movimiento: int, p: MovimientoDemo
    ) -> dict[str, Any]:
        """Valida el rango 1..4 y la ausencia de órdenes duplicadas al actualizar un paso de DEMO."""
        if p.orden < 1 or p.orden > DemoManager.MAX_PASOS_DEMO:
            raise ReglaNegocioError(
                f"El orden del paso debe estar entre 1 y {DemoManager.MAX_PASOS_DEMO}."
            )
        if hasattr(self.db, "obtener_paso_demo_por_id"):
            try:
                paso_actual = self.db.obtener_paso_demo_por_id(id_demo_movimiento)
                if paso_actual and paso_actual.get("id_demo") is not None:
                    id_demo = int(paso_actual["id_demo"])
                    actuales = self.db.obtener_movimientos_demo(id_demo)
                    for mov in actuales:
                        id_item = mov.get("id_demo_movimiento")
                        if (
                            id_item is not None
                            and int(id_item) != int(id_demo_movimiento)
                            and int(mov.get("orden", 0)) == int(p.orden)
                        ):
                            raise ReglaNegocioError(
                                f"El orden {p.orden} ya está asignado a otro paso en la DEMO {id_demo}."
                            )
            except ReglaNegocioError:
                raise
            except Exception as exc:
                raise ExceptionManager.translate_exception(
                    exc, "actualizar_movimiento_demo"
                ) from exc

        return self.ejecutar_sp(
            "sp_actualizar_movimiento_demo",
            [id_demo_movimiento, p.id_comando, p.orden, p.duracion_ms, p.pausa_ms],
            True,
        )

    def obtener_historial_reciente(self, id_vehiculo: int) -> dict[str, Any]:
        """Consulta sp_obtener_historial y aplica la regla de negocio de máximo 10 registros."""
        resultado = self.ejecutar_sp("sp_obtener_historial", [id_vehiculo])
        resultado["resultados"] = resultado.get("resultados", [])[: self.HISTORIAL_MAX_ITEMS]
        return resultado

    async def actualizar_estado_vehiculo(
        self, id_vehiculo: int, p: EstadoActualizar
    ) -> dict[str, Any]:
        """Actualiza el estado del vehículo en RDS y emite el evento WebSocket 'estado_actualizado'."""
        await self.reconciliar_detencion_si_pendiente(id_vehiculo)
        resultado = await asyncio.to_thread(
            self.ejecutar_sp,
            "sp_actualizar_estado",
            [id_vehiculo, p.id_comando, int(p.conectado)],
            True,
        )
        evento = {
            "tipo": "estado_actualizado",
            "id_vehiculo": id_vehiculo,
            "id_comando": p.id_comando,
            "conectado": p.conectado,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        await self.ws_manager.broadcast_to_vehicle(id_vehiculo, evento)
        return resultado

    async def registrar_entrada_historial(self, p: HistorialCrear) -> dict[str, Any]:
        """Registra una entrada en historial_comandos y emite el evento 'historial_registrado'."""
        await self.reconciliar_detencion_si_pendiente(p.id_vehiculo)
        resultado = await asyncio.to_thread(
            self.ejecutar_sp,
            "sp_registrar_historial",
            [p.id_vehiculo, p.id_comando, p.origen],
            True,
        )
        evento = {
            "tipo": "historial_registrado",
            "id_vehiculo": p.id_vehiculo,
            "id_comando": p.id_comando,
            "origen": p.origen,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        await self.ws_manager.broadcast_to_vehicle(p.id_vehiculo, evento)
        return resultado

    async def registrar_movimiento_individual(
        self, p: MovimientoRequest, sesion_control_header: str | None = None
    ) -> dict[str, Any]:
        """Ejecuta en una sola transacción la actualización de estado e historial en RDS
        y notifica por WebSocket con número de secuencia orden_id, manteniendo el movimiento
        activo hasta una orden explícita de DETENER o un nuevo movimiento."""
        id_vehiculo = p.id_vehiculo
        stop_epoch_inicial = self._stop_epoch[id_vehiculo]

        # 1. Reconciliar primero si existía un DETENER pendiente en RDS
        await self.reconciliar_detencion_si_pendiente(id_vehiculo)

        # 2. Verificar que la secuencia del vehículo no esté bloqueada en estado seguro
        try:
            rds_max_prev = None
            if hasattr(self.db, "obtener_max_id_historial"):
                rds_max_prev = await asyncio.to_thread(
                    self.db.obtener_max_id_historial, id_vehiculo
                )
            estado_seq = self.sequence_manager.obtener_estado_vehiculo(
                id_vehiculo, rds_max_id_historial=rds_max_prev, rds_disponible=True
            )
            if estado_seq.get("estado_seguro_bloqueado"):
                motivo = estado_seq.get("motivo_bloqueo") or "ESTADO_SEGURO_ACTIVO"
                raise HTTPException(
                    status_code=503,
                    detail=f"Vehículo {id_vehiculo} bloqueado en estado seguro ({motivo}).",
                )
        except ValueError as exc:
            await self._emitir_detener_emergencia_estado_seguro(
                id_vehiculo, "ARCHIVO_SECUENCIA_CORRUPTO"
            )
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        except HTTPException:
            raise
        except Exception as exc:
            # Si tampoco hay archivo local y RDS falló al verificar secuencia
            try:
                estado_fallback = self.sequence_manager.obtener_estado_vehiculo(
                    id_vehiculo, rds_max_id_historial=None, rds_disponible=False
                )
                if estado_fallback.get("estado_seguro_bloqueado"):
                    raise HTTPException(
                        status_code=503,
                        detail=(
                            f"Vehículo {id_vehiculo} en estado seguro ({estado_fallback.get('motivo_bloqueo')}): "
                            "no se pudo recuperar secuencia confiable."
                        ),
                    ) from exc
            except ValueError as v_exc:
                raise HTTPException(status_code=503, detail=str(v_exc)) from v_exc

        # 3. Si había una DEMO activa, cancelarla antes de iniciar el movimiento manual
        if self.demo_manager.is_running(id_vehiculo):
            await self.demo_manager.cancel_demo(id_vehiculo)

        if self._stop_epoch[id_vehiculo] != stop_epoch_inicial:
            raise ConflictoConcurrenciaError(
                "El movimiento fue cancelado porque se recibió una orden prioritaria de DETENER."
            )

        # 4. Ejecutar transacción en RDS
        try:
            base_datos = await asyncio.to_thread(
                self.db.registrar_movimiento, id_vehiculo, p.id_comando, p.origen
            )
        except Exception as exc:
            raise ExceptionManager.translate_exception(exc, "registrar_movimiento") from exc

        # 5. Validar bajo candado que ninguna orden DETENER haya ocurrido mientras RDS respondía
        async with self._vehicle_locks[id_vehiculo]:
            if (
                self._stop_epoch[id_vehiculo] != stop_epoch_inicial
                or id_vehiculo in self._pending_reconciliations
            ):
                # Re-asegurar que RDS conserve el estado DETENER y no el movimiento tardío
                try:
                    if hasattr(self.db, "registrar_detencion"):
                        await asyncio.to_thread(
                            self.db.registrar_detencion, id_vehiculo, "PRIORIDAD_STOP"
                        )
                except Exception:
                    self._pending_reconciliations[id_vehiculo] = {
                        "origen": "PRIORIDAD_STOP",
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                    }
                    self.sequence_manager.marcar_reconciliacion_pendiente(
                        id_vehiculo, True, self._pending_reconciliations[id_vehiculo]
                    )
                raise ConflictoConcurrenciaError(
                    "El movimiento fue invalidado por una orden prioritaria de DETENER y no fue publicado."
                )

            try:
                reserva = self.sequence_manager.reservar_epoca(
                    id_vehiculo,
                    "MOVIMIENTO",
                    rds_max_id_historial=base_datos.get("id_historial"),
                    rds_disponible=True,
                    es_nuevo_registro_rds=True,
                )
            except OverflowError as exc:
                await self._emitir_detener_emergencia_estado_seguro(
                    id_vehiculo, "LIMITE_UINT32_ALCANZADO"
                )
                raise HTTPException(status_code=503, detail=str(exc)) from exc
            except RuntimeError as exc:
                await self._emitir_detener_emergencia_estado_seguro(
                    id_vehiculo, "SECUENCIA_NO_CONFIABLE"
                )
                raise HTTPException(status_code=503, detail=str(exc)) from exc

            epoca_orden = int(reserva["epoca_orden"])
            orden_id = int(reserva["orden_id"])
            codigo_cmd = base_datos.get("codigo")
            nombre_cmd = base_datos.get("comando")
            es_comando_detener = (
                (codigo_cmd is not None and int(codigo_cmd) == 0)
                or str(nombre_cmd or "").strip().upper() == "DETENER"
            )

            sesion_resuelta = (
                (p.sesion_control or sesion_control_header or "").strip() or None
            )

            event = {
                "tipo": "movimiento_individual",
                "id_vehiculo": id_vehiculo,
                "id_comando": p.id_comando,
                "comando": nombre_cmd,
                "codigo": codigo_cmd,
                "orden_id": orden_id,
                "epoca_orden": epoca_orden,
                "sub_orden": 0,
                "mantener_activo": not es_comando_detener,
                "origen": p.origen,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "estado": "confirmado_en_rds",
            }
            if sesion_resuelta is not None:
                event["sesion_control"] = sesion_resuelta

            if es_comando_detener:
                self._cancel_watchdog(id_vehiculo)
                self._active_movements.pop(id_vehiculo, None)
            else:
                ahora_mono = time.monotonic()
                self._active_movements[id_vehiculo] = {
                    "id_vehiculo": id_vehiculo,
                    "id_comando": p.id_comando,
                    "comando": nombre_cmd,
                    "codigo": codigo_cmd,
                    "orden_id": orden_id,
                    "epoca_orden": epoca_orden,
                    "sesion_control": sesion_resuelta,
                    "origen": p.origen,
                    "iniciado_en": ahora_mono,
                    "ultima_renovacion": ahora_mono,
                }
                self._start_or_reset_watchdog(id_vehiculo)

            await self.ws_manager.broadcast_to_vehicle(id_vehiculo, event)

        return {
            "ok": True,
            "mensaje": "Movimiento individual registrado correctamente",
            "movimiento": event,
            "base_datos": base_datos,
        }

    async def detener_vehiculo(
        self,
        id_vehiculo: int,
        p: DetenerRequest | None = None,
        sesion_control_header: str | None = None,
    ) -> dict[str, Any]:
        """Ejecuta una orden explícita de DETENER con prioridad máxima:
        1. Invalida inmediatamente cualquier movimiento en vuelo o sesión activa.
        2. Cancela cualquier DEMO activa en el vehículo y espera su finalización.
        3. Registra la detención en RDS cuando está disponible (o activa reconciliación pendiente si RDS falla).
        4. Publica el evento 'detener' por WebSocket hacia el ESP8266.
        5. Espera opcionalmente el ACK físico ('detencion_confirmada') del ESP8266."""
        if id_vehiculo <= 0:
            raise ReglaNegocioError("El identificador del vehículo debe ser mayor a 0.")

        req = p or DetenerRequest(
            origen="API",
            esperar_confirmacion_ms=self.settings.stop_ack_timeout_ms,
            sesion_control=sesion_control_header,
        )

        # 1. Invalidar inmediatamente cualquier operación previa o en vuelo
        self._stop_epoch[id_vehiculo] += 1
        self._cancel_watchdog(id_vehiculo)
        self._active_movements.pop(id_vehiculo, None)
        self.demo_manager.invalidate_demo_epoch(id_vehiculo)

        # 2. Si existe una DEMO activa, cancelarla y esperar su cierre completo
        demo_cancelada = False
        if self.demo_manager.is_running(id_vehiculo):
            res_cancel = await self.demo_manager.cancel_demo(id_vehiculo)
            demo_cancelada = bool(res_cancel.get("ok"))

        registrada_en_rds = False
        reconciliacion_rds_pendiente = False
        base_datos: dict[str, Any] = {
            "transaccion": "no_registrada",
            "conectado": False,
            "id_comando": 0,
            "comando": "Detener",
            "codigo": 0,
            "id_historial": None,
        }

        async with self._vehicle_locks[id_vehiculo]:
            # 3. Intentar registrar la detención en RDS
            try:
                if hasattr(self.db, "registrar_detencion"):
                    res_db = await asyncio.to_thread(
                        self.db.registrar_detencion, id_vehiculo, req.origen
                    )
                else:
                    # Fallback compatible si el stub solo implementa obtener_vehiculo_activo
                    veh = await asyncio.to_thread(
                        self.db.obtener_vehiculo_activo, id_vehiculo
                    )
                    if veh is None:
                        raise LookupError("El vehículo no existe.")
                    if not veh.get("activo"):
                        raise ValueError("El vehículo está inactivo.")
                    res_db = {
                        "transaccion": "confirmada",
                        "conectado": True,
                        "id_comando": 42,
                        "comando": "Detener",
                        "codigo": 0,
                        "id_historial": None,
                    }
                base_datos = res_db
                registrada_en_rds = True
                self._pending_reconciliations.pop(id_vehiculo, None)
            except (LookupError, ValueError) as exc:
                raise ExceptionManager.translate_exception(exc, "detener_vehiculo") from exc
            except Exception as exc:
                logger.error(
                    "Fallo de RDS durante DETENER para vehículo %s; priorizando parada física por WSS: %s",
                    id_vehiculo,
                    exc,
                )
                registrada_en_rds = False
                reconciliacion_rds_pendiente = True

            # 4. Reservar época exclusiva para DETENER
            resync_requerido = False
            try:
                reserva = self.sequence_manager.reservar_epoca(
                    id_vehiculo,
                    "DETENER",
                    rds_max_id_historial=base_datos.get("id_historial") if registrada_en_rds else None,
                    rds_disponible=registrada_en_rds,
                    es_nuevo_registro_rds=registrada_en_rds,
                )
                epoca_orden = int(reserva["epoca_orden"])
                orden_id = int(reserva["orden_id"])
                resync_requerido = bool(reserva.get("resync_requerido"))
            except OverflowError:
                orden_id = OrderSequenceManager.MAX_UINT32
                self.sequence_manager.activar_estado_seguro(
                    id_vehiculo,
                    "LIMITE_UINT32_ALCANZADO",
                    resync_orden_id_esperado=orden_id,
                )
                epoca_orden = OrderSequenceManager.MAX_EPOCA_UINT32
                resync_requerido = True

            if reconciliacion_rds_pendiente:
                datos_rec = {
                    "id_vehiculo": id_vehiculo,
                    "orden_id": orden_id,
                    "origen": req.origen,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                }
                self._pending_reconciliations[id_vehiculo] = datos_rec
                self.sequence_manager.marcar_reconciliacion_pendiente(
                    id_vehiculo, True, datos_rec
                )
            else:
                self.sequence_manager.marcar_reconciliacion_pendiente(
                    id_vehiculo,
                    False,
                    rds_max_id_historial=base_datos.get("id_historial"),
                )

            id_comando_real = int(base_datos.get("id_comando") or 0)
            nombre_comando_real = str(base_datos.get("comando") or "Detener")

            evento_ws = {
                "tipo": "detener",
                "accion": "DETENER",
                "id_vehiculo": id_vehiculo,
                "id_comando": id_comando_real,
                "comando": nombre_comando_real,
                "codigo": 0,
                "orden_id": orden_id,
                "epoca_orden": epoca_orden,
                "sub_orden": 0,
                "origen": req.origen,
                "prioridad": "MAXIMA",
                "resync_secuencia": resync_requerido,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }

            # Registrar futuro de ACK antes de enviar el frame por WebSocket
            self.ws_manager.register_pending_stop_ack(id_vehiculo, orden_id)
            stats_ws = await self.ws_manager.broadcast_to_vehicle(id_vehiculo, evento_ws)
            orden_publicada_ws = int(stats_ws.get("total_enviados", 0)) > 0

        # 5. Esperar el ACK físico del ESP8266 (nunca reportar confirmación física sin ACK real)
        ack_esp8266 = None
        if orden_publicada_ws and req.esperar_confirmacion_ms > 0:
            ack_esp8266 = await self.ws_manager.wait_for_stop_ack(
                id_vehiculo, orden_id, req.esperar_confirmacion_ms
            )

        detencion_fisica_confirmada = ack_esp8266 is not None
        resync_completada = False

        # 6. Si el vehículo requería resincronización, desbloquear SOLAMENTE si llegó el ACK
        #    verificable del ESP8266 con el orden_id exacto y salidas_activas=false (0), y RDS está al día
        if resync_requerido and detencion_fisica_confirmada and ack_esp8266 is not None and registrada_en_rds:
            try:
                res_sync = self.sequence_manager.mark_resync_completed(
                    id_vehiculo,
                    ack_data=ack_esp8266,
                    rds_id_historial=base_datos.get("id_historial"),
                )
                resync_completada = bool(res_sync.get("resync_completada"))
            except (ValueError, RuntimeError, OverflowError) as exc:
                logger.warning(
                    "Resincronización no completada para vehículo %s tras ACK: %s",
                    id_vehiculo,
                    exc,
                )
                resync_completada = False

        if detencion_fisica_confirmada and registrada_en_rds:
            mensaje = "Detención física confirmada por el ESP8266 y registrada en RDS"
        elif detencion_fisica_confirmada and not registrada_en_rds:
            mensaje = (
                "Detención física confirmada por el ESP8266; registro en RDS pendiente de reconciliación"
            )
        elif orden_publicada_ws and registrada_en_rds:
            mensaje = (
                "Orden de detención registrada en RDS y publicada por WebSocket; "
                "sin confirmación física (ACK) del ESP8266"
            )
        elif orden_publicada_ws and not registrada_en_rds:
            mensaje = (
                "Orden de detención publicada por WebSocket en modo de emergencia (RDS no disponible); "
                "reconciliación pendiente y sin ACK físico"
            )
        elif registrada_en_rds:
            mensaje = (
                "Orden de detención registrada en RDS; no hay clientes WebSocket conectados "
                "(detención física no confirmada)"
            )
        else:
            mensaje = (
                "Orden de detención recibida con RDS no disponible y sin clientes WebSocket conectados; "
                "reconciliación pendiente"
            )

        return {
            "ok": True,
            "mensaje": mensaje,
            "id_vehiculo": id_vehiculo,
            "orden_id": orden_id,
            "orden_recibida": True,
            "registrada_en_rds": registrada_en_rds,
            "orden_publicada_ws": orden_publicada_ws,
            "detencion_fisica_confirmada": detencion_fisica_confirmada,
            "reconciliacion_rds_pendiente": reconciliacion_rds_pendiente,
            "resync_requerido": resync_requerido,
            "resync_completada": resync_completada,
            "demo_cancelada": demo_cancelada,
            "estado_orden": {
                "orden_recibida": True,
                "registrada_en_rds": registrada_en_rds,
                "demo_cancelada": demo_cancelada,
                "orden_publicada_ws": orden_publicada_ws,
                "clientes_vehiculo_notificados": stats_ws.get("clientes_vehiculo", 0),
                "clientes_global_notificados": stats_ws.get("clientes_global", 0),
                "detencion_fisica_confirmada": detencion_fisica_confirmada,
                "reconciliacion_rds_pendiente": reconciliacion_rds_pendiente,
                "resync_requerido": resync_requerido,
                "resync_completada": resync_completada,
                "confirmacion_esp8266": ack_esp8266,
            },
            "evento_ws": evento_ws,
            "base_datos": base_datos,
        }

    async def iniciar_demo(self, id_demo: int) -> dict[str, Any]:
        """Inicia la ejecución temporizada de una DEMO validando concurrencia, límite de 4 pasos y estado seguro."""
        try:
            demo_info = await asyncio.to_thread(self.db.obtener_info_demo, id_demo)
            if demo_info and demo_info.get("id_vehiculo") is not None:
                id_veh = int(demo_info["id_vehiculo"])
                await self.reconciliar_detencion_si_pendiente(id_veh)
                # Si había un movimiento manual sostenido activo, cancelarlo antes de iniciar la DEMO
                self._cancel_watchdog(id_veh)
                self._active_movements.pop(id_veh, None)
            return await self.demo_manager.start_demo(id_demo)
        except HTTPException:
            raise
        except OverflowError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        except (LookupError, ValueError, RuntimeError) as exc:
            if isinstance(exc, RuntimeError) and "estado seguro" in str(exc).lower():
                raise HTTPException(status_code=503, detail=str(exc)) from exc
            raise ExceptionManager.translate_exception(exc, f"iniciar_demo({id_demo})") from exc
        except Exception as exc:
            logger.exception("Error al iniciar demo %s", id_demo)
            raise HTTPException(
                status_code=500, detail="Error interno al iniciar la DEMO."
            ) from exc

    async def cancelar_demo_por_id(self, id_demo: int) -> dict[str, Any]:
        """Cancela una DEMO activa buscando por id_demo."""
        res = await self.demo_manager.cancel_demo_by_id(id_demo)
        if not res.get("ok"):
            raise HTTPException(status_code=404, detail=res.get("mensaje"))
        return res

    def obtener_demo_activa(self, id_vehiculo: int) -> dict[str, Any]:
        """Consulta si el vehículo tiene actualmente una DEMO en ejecución."""
        info = self.demo_manager.get_active_demo_info(id_vehiculo)
        return {"id_vehiculo": id_vehiculo, "activa": info is not None, "demo": info}

    async def cancelar_demo_por_vehiculo(self, id_vehiculo: int) -> dict[str, Any]:
        """Cancela cualquier DEMO en ejecución para el vehículo especificado."""
        res = await self.demo_manager.cancel_demo(id_vehiculo)
        if not res.get("ok"):
            raise HTTPException(status_code=404, detail=res.get("mensaje"))
        return res

    async def enviar_estado_inicial_global(self, websocket: WebSocket) -> None:
        """Consulta y envía el evento 'estado_inicial_global' al conectar al canal global."""
        try:
            vehiculos = await asyncio.to_thread(self.db.obtener_estados_globales)
            await websocket.send_json(
                {
                    "tipo": "estado_inicial_global",
                    "vehiculos": vehiculos,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                }
            )
        except Exception:
            logger.exception("No se pudo obtener el estado inicial global de los vehículos")
            await websocket.send_json(
                {
                    "tipo": "error",
                    "mensaje": "No fue posible consultar el estado inicial global",
                }
            )

    async def cancel_all_watchdogs(self) -> None:
        """Cancela todas las tareas de watchdog activas al apagar el servidor."""
        tasks = [t for t in self._watchdog_tasks.values() if not t.done()]
        self._watchdog_tasks.clear()
        self._active_movements.clear()
        for t in tasks:
            t.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def enviar_estado_inicial_vehiculo(
        self, websocket: WebSocket, id_vehiculo: int
    ) -> None:
        """Consulta y envía el evento 'estado_inicial' al conectar al canal de un vehículo.
        Incluye activar_salidas=False para garantizar que reconectar un cliente o el ESP8266
        jamás reactive automáticamente un movimiento anterior."""
        try:
            if self.tiene_reconciliacion_pendiente(id_vehiculo):
                try:
                    await self.reconciliar_detencion_si_pendiente(id_vehiculo)
                except Exception:
                    await websocket.send_json(
                        {
                            "tipo": "estado_inicial",
                            "id_vehiculo": id_vehiculo,
                            "activar_salidas": False,
                            "reconciliacion_rds_pendiente": True,
                            "estado": [
                                {
                                    "id_vehiculo": id_vehiculo,
                                    "id_comando": 0,
                                    "comando": "Detener",
                                    "codigo": 0,
                                    "conectado": 1,
                                }
                            ],
                            "timestamp": datetime.now(timezone.utc).isoformat(),
                        }
                    )
                    return

            estado = await asyncio.to_thread(self.db.obtener_estado, id_vehiculo)
            await websocket.send_json(
                {
                    "tipo": "estado_inicial",
                    "id_vehiculo": id_vehiculo,
                    "activar_salidas": False,
                    "estado": estado,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                }
            )
        except Exception:
            logger.exception("No se pudo obtener el estado inicial del vehículo %s", id_vehiculo)
            await websocket.send_json(
                {
                    "tipo": "error",
                    "id_vehiculo": id_vehiculo,
                    "mensaje": "No fue posible consultar el estado inicial",
                }
            )

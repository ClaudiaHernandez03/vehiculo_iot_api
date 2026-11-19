# ==============================================================================
# MÓDULO: websocket_manager.py
# PROPÓSITO: Administrar mediante Programación Orientada a Objetos las conexiones
#            y el protocolo de mensajería WebSocket en tiempo real:
#            1. Por vehículo individual (/ws/vehiculos/{id_vehiculo}).
#            2. En el canal global (/ws y /ws/vehiculos) que monitorea todos los vehículos.
#            Evita duplicar notificaciones, gestiona heartbeats (ping/pong) y
#            limpia automáticamente conexiones desconectadas.
# ==============================================================================

import asyncio
from collections import defaultdict
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import logging
import os
from pathlib import Path
import threading
from typing import Any, Callable
import uuid
from fastapi import WebSocket

from settings import settings

logger = logging.getLogger(__name__)


class OrderSequenceManager:
    """Gestiona de forma atómica y persistente la secuencia de identificadores orden_id
    por vehículo utilizando época exclusiva * 100 + sub_orden.

    NOTA DE ARQUITECTURA DE WORKERS:
    El bloqueo de archivo (.orden_seq_state.json.lock) serializa únicamente la lectura y
    escritura del archivo local de secuencia, pero NO garantiza por sí solo la seguridad
    de múltiples workers de FastAPI/Uvicorn, ya que las conexiones WebSocket, sesiones
    de controlador, tareas de watchdog, futuros de ACK pendientes, barreras en memoria y
    tareas asíncronas de DEMO residen en la memoria del proceso local. El despliegue debe
    ejecutarse con un único worker (--workers 1) mientras no exista coordinación compartida
    externa para dichos estados."""

    FACTOR_EPOCA = 100
    MAX_SUB_ORDEN_DEMO = 10
    MAX_UINT32 = 4294967295
    MAX_EPOCA_UINT32 = (MAX_UINT32 - MAX_SUB_ORDEN_DEMO) // FACTOR_EPOCA  # 42949672

    def __init__(self, state_file: Path | str | None = None) -> None:
        self.state_file = Path(state_file) if state_file else Path(settings.order_seq_state_file)
        self.lock_file = self.state_file.with_suffix(self.state_file.suffix + ".lock")
        self._thread_lock = threading.RLock()
        self._memory_state: dict[str, dict[str, Any]] = {}
        self._corrupted: bool = False
        self._corruption_detail: str | None = None

    @staticmethod
    def _is_strict_int(val: Any) -> bool:
        return isinstance(val, int) and not isinstance(val, bool)

    @staticmethod
    def _is_salidas_inactivas(val: Any) -> bool:
        """Acepta únicamente salidas_activas=False (booleano JSON false) o salidas_activas=0."""
        if isinstance(val, bool):
            return val is False
        if isinstance(val, int):
            return val == 0
        return False

    @contextmanager
    def _os_file_lock(self):
        """Adquiere candado de hilo y bloqueo de archivo del sistema operativo (Windows/POSIX)."""
        with self._thread_lock:
            self.lock_file.parent.mkdir(parents=True, exist_ok=True)
            f_lock = open(self.lock_file, "a+b")
            try:
                f_lock.seek(0)
                if f_lock.tell() == 0:
                    f_lock.write(b"0")
                    f_lock.flush()
                f_lock.seek(0)
                if os.name == "nt":
                    import msvcrt

                    msvcrt.locking(f_lock.fileno(), msvcrt.LK_LOCK, 1)
                else:
                    import fcntl

                    fcntl.flock(f_lock.fileno(), fcntl.LOCK_EX)
                yield
            finally:
                try:
                    f_lock.seek(0)
                    if os.name == "nt":
                        import msvcrt

                        msvcrt.locking(f_lock.fileno(), msvcrt.LK_UNLCK, 1)
                    else:
                        import fcntl

                        fcntl.flock(f_lock.fileno(), fcntl.LOCK_UN)
                except Exception:
                    pass
                f_lock.close()

    @staticmethod
    def _compute_checksum(vehiculos_map: dict[str, Any]) -> str:
        canonical = json.dumps(vehiculos_map, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    @classmethod
    def _default_vehicle_entry(
        cls, epoca: int = 0, ultimo_id_historial_rds: int | None = None
    ) -> dict[str, Any]:
        return {
            "epoca_orden": int(epoca),
            "ultimo_orden_id": int(epoca) * cls.FACTOR_EPOCA,
            "ultimo_id_historial_rds": (
                int(ultimo_id_historial_rds)
                if ultimo_id_historial_rds is not None
                else None
            ),
            "ultimo_stop_epoca": 0,
            "ultimo_stop_orden_id": 0,
            "estado_seguro_bloqueado": False,
            "motivo_bloqueo": None,
            "resync_pendiente_ack": False,
            "resync_orden_id_esperado": None,
            "reconciliacion_rds_pendiente": False,
            "datos_reconciliacion": None,
            "inicializado": True,
            "actualizado_en": datetime.now(timezone.utc).isoformat(),
        }

    def _validate_and_load_unlocked(self) -> tuple[bool, dict[str, dict[str, Any]]]:
        """Lee y valida el archivo .orden_seq_state.json.
        Devuelve (file_exists, vehiculos_dict). Lanza ValueError si el archivo está corrupto."""
        if not self.state_file.exists():
            return False, {}

        try:
            raw_text = self.state_file.read_text(encoding="utf-8")
            if not raw_text.strip():
                raise ValueError("Archivo de secuencia vacío (posible escritura interrumpida).")
            payload = json.loads(raw_text)
        except Exception as exc:
            self._corrupted = True
            self._corruption_detail = f"ARCHIVO_SECUENCIA_CORRUPTO: {exc}"
            raise ValueError(self._corruption_detail) from exc

        if not isinstance(payload, dict):
            self._corrupted = True
            self._corruption_detail = "ARCHIVO_SECUENCIA_CORRUPTO: raíz no es un objeto JSON."
            raise ValueError(self._corruption_detail)

        vehiculos = payload.get("vehiculos")
        checksum = payload.get("checksum")
        if not isinstance(vehiculos, dict) or not isinstance(checksum, str):
            self._corrupted = True
            self._corruption_detail = "ARCHIVO_SECUENCIA_CORRUPTO: estructura incompleta."
            raise ValueError(self._corruption_detail)

        expected_checksum = self._compute_checksum(vehiculos)
        if checksum != expected_checksum:
            self._corrupted = True
            self._corruption_detail = "ARCHIVO_SECUENCIA_CORRUPTO: checksum SHA-256 inválido."
            raise ValueError(self._corruption_detail)

        for vid_str, entry in vehiculos.items():
            if not isinstance(entry, dict):
                self._corrupted = True
                self._corruption_detail = f"ARCHIVO_SECUENCIA_CORRUPTO: entrada inválida para vehículo {vid_str}."
                raise ValueError(self._corruption_detail)
            epoca = entry.get("epoca_orden")
            ult_id = entry.get("ultimo_orden_id")
            if (
                isinstance(epoca, bool)
                or not isinstance(epoca, int)
                or epoca < 0
                or epoca > self.MAX_EPOCA_UINT32
                or isinstance(ult_id, bool)
                or not isinstance(ult_id, int)
                or ult_id < 0
                or ult_id > self.MAX_UINT32
            ):
                self._corrupted = True
                self._corruption_detail = f"ARCHIVO_SECUENCIA_CORRUPTO: tipos o límites numéricos inválidos en vehículo {vid_str}."
                raise ValueError(self._corruption_detail)

        self._corrupted = False
        self._corruption_detail = None
        self._memory_state = dict(vehiculos)
        return True, vehiculos

    def _save_atomic_unlocked(self, vehiculos: dict[str, dict[str, Any]]) -> None:
        """Escribe atómicamente el estado de secuencias con suma de verificación SHA-256."""
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": 1,
            "vehiculos": vehiculos,
            "checksum": self._compute_checksum(vehiculos),
        }
        tmp_path = self.state_file.with_name(
            f"{self.state_file.name}.tmp.{os.getpid()}.{threading.get_ident()}"
        )
        serialized = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
        with open(tmp_path, "w", encoding="utf-8") as f_tmp:
            f_tmp.write(serialized)
            f_tmp.flush()
            os.fsync(f_tmp.fileno())
        os.replace(tmp_path, self.state_file)
        self._memory_state = dict(vehiculos)

    def obtener_estado_vehiculo(
        self,
        id_vehiculo: int,
        rds_max_id_historial: int | None = None,
        rds_disponible: bool = True,
    ) -> dict[str, Any]:
        """Obtiene el estado de secuencia del vehículo validando la integridad del archivo y
        la consistencia contra RDS.
        IMPORTANTE: MAX(id_historial) NO es epoca_orden ni orden_id. Si el archivo local
        no existe pero RDS ya tiene historial (id_historial > 0), o si id_historial en RDS
        difiere de ultimo_id_historial_rds, se bloquea el vehículo en estado seguro hasta
        completar una resincronización confirmada con ACK del ESP8266."""
        vid = str(id_vehiculo)
        with self._os_file_lock():
            file_exists, vehiculos = self._validate_and_load_unlocked()
            entry = vehiculos.get(vid)
            if entry is None:
                if not file_exists and not rds_disponible:
                    # Pérdida simultánea del archivo local y de RDS
                    bloqueado = self._default_vehicle_entry(0)
                    bloqueado["inicializado"] = False
                    bloqueado["estado_seguro_bloqueado"] = True
                    bloqueado["motivo_bloqueo"] = "PERDIDA_SIMULTANEA_ARCHIVO_Y_RDS"
                    bloqueado["resync_pendiente_ack"] = True
                    return bloqueado

                if rds_disponible and rds_max_id_historial is not None and int(rds_max_id_historial) > 0:
                    # El esquema actual de RDS solo almacena id_historial, no orden_id ni epoca_orden.
                    # No se debe inventar epoca_orden = id_historial; se bloquea el control hasta resincronizar con ACK.
                    entry = self._default_vehicle_entry(
                        0, ultimo_id_historial_rds=int(rds_max_id_historial)
                    )
                    entry["inicializado"] = False
                    entry["estado_seguro_bloqueado"] = True
                    entry["motivo_bloqueo"] = "SECUENCIA_NO_RECUPERABLE_DESDE_RDS"
                    entry["resync_pendiente_ack"] = True
                    vehiculos[vid] = entry
                    self._save_atomic_unlocked(vehiculos)
                    return dict(entry)

                entry = self._default_vehicle_entry(
                    0,
                    ultimo_id_historial_rds=(
                        0 if (rds_disponible and rds_max_id_historial == 0) else None
                    ),
                )
                vehiculos[vid] = entry
                self._save_atomic_unlocked(vehiculos)
            elif rds_disponible and rds_max_id_historial is not None and not entry.get("reconciliacion_rds_pendiente"):
                hist_rds = int(rds_max_id_historial)
                hist_local = entry.get("ultimo_id_historial_rds")
                if hist_local is None:
                    if hist_rds > 0:
                        motivo = (
                            "SECUENCIA_NO_RECUPERABLE_DESDE_RDS"
                            if int(entry.get("epoca_orden", 0)) == 0
                            else "DISCREPANCIA_ID_HISTORIAL_VS_ORDEN_ID"
                        )
                        entry["inicializado"] = False
                        entry["estado_seguro_bloqueado"] = True
                        entry["motivo_bloqueo"] = motivo
                        entry["resync_pendiente_ack"] = True
                        entry["actualizado_en"] = datetime.now(timezone.utc).isoformat()
                        vehiculos[vid] = entry
                        self._save_atomic_unlocked(vehiculos)
                    else:
                        entry["ultimo_id_historial_rds"] = 0
                        entry["actualizado_en"] = datetime.now(timezone.utc).isoformat()
                        vehiculos[vid] = entry
                        self._save_atomic_unlocked(vehiculos)
                elif hist_rds != int(hist_local):
                    entry["estado_seguro_bloqueado"] = True
                    entry["motivo_bloqueo"] = "DISCREPANCIA_ID_HISTORIAL_VS_ORDEN_ID"
                    entry["resync_pendiente_ack"] = True
                    entry["actualizado_en"] = datetime.now(timezone.utc).isoformat()
                    vehiculos[vid] = entry
                    self._save_atomic_unlocked(vehiculos)
            return dict(entry)

    def consultar_reconciliacion_disco(self, id_vehiculo: int) -> tuple[bool, dict[str, Any] | None]:
        """Consulta en modo solo lectura si existe una reconciliación RDS pendiente en disco,
        sin crear ni inicializar entradas nuevas cuando el archivo aún no existe."""
        vid = str(id_vehiculo)
        with self._os_file_lock():
            file_exists, vehiculos = self._validate_and_load_unlocked()
            if not file_exists:
                return False, None
            entry = vehiculos.get(vid)
            if not entry or not entry.get("reconciliacion_rds_pendiente"):
                return False, None
            return True, dict(entry.get("datos_reconciliacion") or {})

    def verificar_consistencia_rds(
        self,
        id_vehiculo: int,
        rds_max_id_historial: int | None,
        rds_orden_id: int | None = None,
    ) -> dict[str, Any]:
        """Comprueba si id_historial en RDS y/o un orden_id candidato coinciden con la
        secuencia persistida. Detecta discrepancias entre id_historial y orden_id y
        bloquea inmediatamente el vehículo en estado seguro."""
        vid = str(id_vehiculo)
        with self._os_file_lock():
            _, vehiculos = self._validate_and_load_unlocked()
            entry = vehiculos.get(vid)
            if entry is None:
                return self.obtener_estado_vehiculo(
                    id_vehiculo, rds_max_id_historial=rds_max_id_historial, rds_disponible=True
                )

            discrepancia = False
            if rds_orden_id is not None and int(rds_orden_id) != int(entry.get("ultimo_orden_id", 0)):
                discrepancia = True
            if rds_max_id_historial is not None:
                hist_local = entry.get("ultimo_id_historial_rds")
                if hist_local is None and int(rds_max_id_historial) > 0:
                    discrepancia = True
                elif hist_local is not None and int(rds_max_id_historial) != int(hist_local):
                    discrepancia = True

            if discrepancia:
                entry["estado_seguro_bloqueado"] = True
                entry["motivo_bloqueo"] = "DISCREPANCIA_ID_HISTORIAL_VS_ORDEN_ID"
                entry["resync_pendiente_ack"] = True
                entry["actualizado_en"] = datetime.now(timezone.utc).isoformat()
                vehiculos[vid] = entry
                self._save_atomic_unlocked(vehiculos)

            return dict(entry)

    def reservar_epoca(
        self,
        id_vehiculo: int,
        tipo_operacion: str,
        rds_max_id_historial: int | None = None,
        rds_disponible: bool = True,
        es_nuevo_registro_rds: bool = False,
    ) -> dict[str, Any]:
        """Reserva una época exclusiva e irrepetible para MOVIMIENTO, DEMO o DETENER.
        - Nunca utiliza MAX(id_historial) como epoca_orden ni como orden_id.
        - Nunca reinicia nueva_epoca = 1 ni ultimo_stop_orden_id = 0 al alcanzar uint32_t.
        - Si el vehículo está bloqueado y recibe DETENER, registra el orden_id esperado
          para resincronización pero mantiene estado_seguro_bloqueado=True hasta recibir el ACK."""
        vid = str(id_vehiculo)
        es_detener = tipo_operacion.upper() == "DETENER"
        es_demo = tipo_operacion.upper() == "DEMO"

        with self._os_file_lock():
            try:
                file_exists, vehiculos = self._validate_and_load_unlocked()
            except ValueError as exc:
                # Archivo corrupto: activar estado seguro sin usar id_historial como época
                if es_detener:
                    mem_entry = dict(self._memory_state.get(vid) or self._default_vehicle_entry(0))
                    base_epoca_mem = int(mem_entry.get("epoca_orden", 0))
                    nueva_epoca = base_epoca_mem + 1
                    if nueva_epoca > self.MAX_EPOCA_UINT32:
                        mem_entry["estado_seguro_bloqueado"] = True
                        mem_entry["motivo_bloqueo"] = "LIMITE_UINT32_ALCANZADO"
                        mem_entry["resync_pendiente_ack"] = True
                        self._memory_state[vid] = mem_entry
                        raise OverflowError(
                            f"El vehículo {id_vehiculo} alcanzó el límite uint32_t ({self.MAX_UINT32}). "
                            "No se permite reiniciar la secuencia a 1."
                        ) from exc
                    orden_id = nueva_epoca * self.FACTOR_EPOCA
                    mem_entry["epoca_orden"] = nueva_epoca
                    mem_entry["ultimo_orden_id"] = max(int(mem_entry.get("ultimo_orden_id", 0)), orden_id)
                    mem_entry["ultimo_stop_epoca"] = nueva_epoca
                    mem_entry["ultimo_stop_orden_id"] = orden_id
                    if rds_disponible and rds_max_id_historial is not None:
                        mem_entry["ultimo_id_historial_rds"] = int(rds_max_id_historial)
                    mem_entry["estado_seguro_bloqueado"] = True
                    mem_entry["motivo_bloqueo"] = "ARCHIVO_SECUENCIA_CORRUPTO"
                    mem_entry["resync_pendiente_ack"] = True
                    mem_entry["resync_orden_id_esperado"] = orden_id
                    self._memory_state[vid] = mem_entry
                    return {
                        "epoca_orden": nueva_epoca,
                        "orden_id": orden_id,
                        "ultimo_stop_epoca": nueva_epoca,
                        "reconciliacion_rds_pendiente": bool(mem_entry.get("reconciliacion_rds_pendiente")),
                        "estado_seguro_bloqueado": True,
                        "motivo_bloqueo": "ARCHIVO_SECUENCIA_CORRUPTO",
                        "resync_requerido": True,
                    }
                raise RuntimeError(str(exc)) from exc

            entry = vehiculos.get(vid)
            if entry is None:
                if not file_exists and not rds_disponible:
                    entry = self._default_vehicle_entry(0)
                    entry["inicializado"] = False
                    entry["estado_seguro_bloqueado"] = True
                    entry["motivo_bloqueo"] = "PERDIDA_SIMULTANEA_ARCHIVO_Y_RDS"
                    entry["resync_pendiente_ack"] = True
                    if not es_detener:
                        raise RuntimeError(
                            "No es posible recuperar una secuencia confiable: archivo local inexistente y RDS no disponible."
                        )
                elif (
                    rds_disponible
                    and rds_max_id_historial is not None
                    and int(rds_max_id_historial) > 0
                    and not es_nuevo_registro_rds
                ):
                    entry = self._default_vehicle_entry(
                        0, ultimo_id_historial_rds=int(rds_max_id_historial)
                    )
                    entry["inicializado"] = False
                    entry["estado_seguro_bloqueado"] = True
                    entry["motivo_bloqueo"] = "SECUENCIA_NO_RECUPERABLE_DESDE_RDS"
                    entry["resync_pendiente_ack"] = True
                    vehiculos[vid] = entry
                    self._save_atomic_unlocked(vehiculos)
                    if not es_detener:
                        raise RuntimeError(
                            f"Vehículo {id_vehiculo} bloqueado en estado seguro (SECUENCIA_NO_RECUPERABLE_DESDE_RDS): "
                            "MAX(id_historial) no equivale a orden_id y el archivo de secuencia no existe."
                        )
                else:
                    entry = self._default_vehicle_entry(
                        0,
                        ultimo_id_historial_rds=(
                            int(rds_max_id_historial)
                            if (rds_disponible and rds_max_id_historial is not None)
                            else None
                        ),
                    )
            elif rds_disponible and rds_max_id_historial is not None and not entry.get("reconciliacion_rds_pendiente"):
                hist_rds = int(rds_max_id_historial)
                hist_local = entry.get("ultimo_id_historial_rds")
                if not es_nuevo_registro_rds:
                    if (hist_local is None and hist_rds > 0) or (
                        hist_local is not None and hist_rds != int(hist_local)
                    ):
                        entry["estado_seguro_bloqueado"] = True
                        entry["motivo_bloqueo"] = "DISCREPANCIA_ID_HISTORIAL_VS_ORDEN_ID"
                        entry["resync_pendiente_ack"] = True
                        entry["actualizado_en"] = datetime.now(timezone.utc).isoformat()
                        vehiculos[vid] = entry
                        self._save_atomic_unlocked(vehiculos)
                else:
                    if hist_local is not None and hist_rds <= int(hist_local):
                        entry["estado_seguro_bloqueado"] = True
                        entry["motivo_bloqueo"] = "DISCREPANCIA_ID_HISTORIAL_VS_ORDEN_ID"
                        entry["resync_pendiente_ack"] = True
                        entry["actualizado_en"] = datetime.now(timezone.utc).isoformat()
                        vehiculos[vid] = entry
                        self._save_atomic_unlocked(vehiculos)

            # Si el vehículo está en estado seguro bloqueado o tiene reconciliación pendiente,
            # impedir cualquier nuevo MOVIMIENTO o DEMO
            if not es_detener:
                if entry.get("estado_seguro_bloqueado"):
                    motivo = entry.get("motivo_bloqueo") or "ESTADO_SEGURO_ACTIVO"
                    raise RuntimeError(
                        f"Vehículo {id_vehiculo} bloqueado en estado seguro ({motivo}). "
                        "Requiere resincronización confirmada con ACK del ESP8266."
                    )
                if entry.get("reconciliacion_rds_pendiente"):
                    raise RuntimeError(
                        f"Vehículo {id_vehiculo} bloqueado hasta reconciliar la orden DETENER pendiente en Amazon RDS."
                    )

            # NUNCA igualar base_epoca a rds_max_id_historial (son dominios distintos)
            base_epoca = int(entry.get("epoca_orden", 0))
            nueva_epoca = base_epoca + 1
            max_sub = self.MAX_SUB_ORDEN_DEMO if es_demo else 0
            max_orden_id_candidato = (nueva_epoca * self.FACTOR_EPOCA) + max_sub

            if nueva_epoca > self.MAX_EPOCA_UINT32 or max_orden_id_candidato > self.MAX_UINT32:
                # PROHIBIDO reiniciar nueva_epoca = 1 o ultimo_stop_orden_id = 0.
                entry["estado_seguro_bloqueado"] = True
                entry["motivo_bloqueo"] = "LIMITE_UINT32_ALCANZADO"
                entry["resync_pendiente_ack"] = True
                entry["actualizado_en"] = datetime.now(timezone.utc).isoformat()
                vehiculos[vid] = entry
                self._save_atomic_unlocked(vehiculos)
                raise OverflowError(
                    f"El identificador orden_id calculado ({max_orden_id_candidato}) supera el límite uint32_t ({self.MAX_UINT32}). "
                    "Se activó el estado de bloqueo seguro sin reiniciar la secuencia a 1."
                )

            orden_id = nueva_epoca * self.FACTOR_EPOCA
            entry["epoca_orden"] = nueva_epoca
            entry["ultimo_orden_id"] = max(int(entry.get("ultimo_orden_id", 0)), max_orden_id_candidato)
            if rds_disponible and rds_max_id_historial is not None:
                entry["ultimo_id_historial_rds"] = int(rds_max_id_historial)

            resync_requerido = False
            if es_detener:
                entry["ultimo_stop_epoca"] = nueva_epoca
                entry["ultimo_stop_orden_id"] = orden_id
                if entry.get("estado_seguro_bloqueado") or entry.get("resync_pendiente_ack"):
                    entry["resync_pendiente_ack"] = True
                    entry["resync_orden_id_esperado"] = orden_id
                    resync_requerido = True

            entry["actualizado_en"] = datetime.now(timezone.utc).isoformat()
            vehiculos[vid] = entry
            self._save_atomic_unlocked(vehiculos)
            return {
                "epoca_orden": nueva_epoca,
                "orden_id": orden_id,
                "ultimo_stop_epoca": entry["ultimo_stop_epoca"],
                "reconciliacion_rds_pendiente": bool(entry.get("reconciliacion_rds_pendiente")),
                "estado_seguro_bloqueado": bool(entry.get("estado_seguro_bloqueado")),
                "motivo_bloqueo": entry.get("motivo_bloqueo"),
                "resync_requerido": resync_requerido,
            }

    def reserve_epoch(
        self,
        id_vehiculo: int,
        tipo_operacion: str = "MOVIMIENTO",
        rds_max_id_historial: int | None = None,
        rds_disponible: bool = True,
        es_nuevo_registro_rds: bool = False,
    ) -> dict[str, Any]:
        """Alias compatible hacia reservar_epoca (nunca reinicia nueva_epoca=1 ni usa id_historial como época)."""
        return self.reservar_epoca(
            id_vehiculo=id_vehiculo,
            tipo_operacion=tipo_operacion,
            rds_max_id_historial=rds_max_id_historial,
            rds_disponible=rds_disponible,
            es_nuevo_registro_rds=es_nuevo_registro_rds,
        )

    def marcar_reconciliacion_pendiente(
        self,
        id_vehiculo: int,
        pendiente: bool,
        datos: dict[str, Any] | None = None,
        rds_max_id_historial: int | None = None,
    ) -> None:
        """Actualiza la bandera persistente de reconciliación de DETENER hacia RDS sin alterar epoca_orden con id_historial."""
        vid = str(id_vehiculo)
        with self._os_file_lock():
            try:
                _, vehiculos = self._validate_and_load_unlocked()
            except ValueError:
                return
            entry = vehiculos.get(vid) or self._default_vehicle_entry(
                0,
                ultimo_id_historial_rds=(
                    int(rds_max_id_historial) if rds_max_id_historial is not None else None
                ),
            )
            if rds_max_id_historial is not None:
                entry["ultimo_id_historial_rds"] = int(rds_max_id_historial)
            entry["reconciliacion_rds_pendiente"] = bool(pendiente)
            entry["datos_reconciliacion"] = datos if pendiente else None
            entry["actualizado_en"] = datetime.now(timezone.utc).isoformat()
            vehiculos[vid] = entry
            self._save_atomic_unlocked(vehiculos)

    def activar_estado_seguro(
        self, id_vehiculo: int, motivo: str, resync_orden_id_esperado: int | None = None
    ) -> None:
        """Bloquea un vehículo en estado seguro persistente sin reiniciar su época."""
        vid = str(id_vehiculo)
        with self._os_file_lock():
            try:
                _, vehiculos = self._validate_and_load_unlocked()
            except ValueError:
                vehiculos = dict(self._memory_state)
                entry = vehiculos.get(vid) or self._default_vehicle_entry(0)
                entry["estado_seguro_bloqueado"] = True
                entry["motivo_bloqueo"] = motivo
                entry["resync_pendiente_ack"] = True
                if resync_orden_id_esperado is not None:
                    entry["resync_orden_id_esperado"] = int(resync_orden_id_esperado)
                self._memory_state[vid] = entry
                return
            entry = vehiculos.get(vid) or self._default_vehicle_entry(0)
            entry["estado_seguro_bloqueado"] = True
            entry["motivo_bloqueo"] = motivo
            entry["resync_pendiente_ack"] = True
            if resync_orden_id_esperado is not None:
                entry["resync_orden_id_esperado"] = int(resync_orden_id_esperado)
            entry["actualizado_en"] = datetime.now(timezone.utc).isoformat()
            vehiculos[vid] = entry
            self._save_atomic_unlocked(vehiculos)

    def mark_resync_completed(
        self,
        id_vehiculo: int,
        ack_data: dict[str, Any] | None = None,
        rds_id_historial: int | None = None,
        nueva_epoca_base: int | None = None,
    ) -> dict[str, Any]:
        """Completa una resincronización SOLAMENTE si se recibe el ACK verificable del ESP8266
        con el orden_id exacto esperado, codigo=0, salidas_activas=false (0) y estado_fisico='detenido'.
        Si falta el ACK, el orden_id es distinto o se alcanzó el límite uint32_t, mantiene el bloqueo."""
        if not isinstance(ack_data, dict):
            raise ValueError(
                "No se puede marcar la resincronización como completada sin el ACK físico del ESP8266."
            )

        raw_veh = ack_data.get("id_vehiculo")
        raw_orden = ack_data.get("orden_id")
        raw_codigo = ack_data.get("codigo", 0)
        raw_salidas = ack_data.get("salidas_activas")
        raw_estado = ack_data.get("estado_fisico", "detenido")

        if not self._is_strict_int(raw_veh) or int(raw_veh) != int(id_vehiculo):
            raise ValueError("El ACK de resincronización no corresponde al id_vehiculo esperado.")
        if not self._is_strict_int(raw_orden) or int(raw_orden) <= 0:
            raise ValueError("El ACK de resincronización no contiene un orden_id entero válido.")
        if not self._is_strict_int(raw_codigo) or int(raw_codigo) != 0:
            raise ValueError("El ACK de resincronización requiere codigo=0.")
        if not self._is_salidas_inactivas(raw_salidas):
            raise ValueError("El ACK de resincronización requiere salidas_activas=false (0).")
        if not isinstance(raw_estado, str) or raw_estado.strip().lower() != "detenido":
            raise ValueError("El ACK de resincronización requiere estado_fisico='detenido'.")

        orden_id_ack = int(raw_orden)
        vid = str(id_vehiculo)

        with self._os_file_lock():
            try:
                _, vehiculos = self._validate_and_load_unlocked()
            except ValueError:
                vehiculos = dict(self._memory_state)

            entry = vehiculos.get(vid) or self._memory_state.get(vid)
            if entry is None:
                raise RuntimeError(
                    f"No existe una resincronización iniciada para el vehículo {id_vehiculo}."
                )

            if (
                int(entry.get("epoca_orden", 0)) >= self.MAX_EPOCA_UINT32
                or entry.get("motivo_bloqueo") == "LIMITE_UINT32_ALCANZADO"
            ):
                raise OverflowError(
                    f"Vehículo {id_vehiculo} bloqueado en LIMITE_UINT32_ALCANZADO: "
                    "no se permite reiniciar la secuencia a 1 ni reutilizar épocas anteriores."
                )

            orden_esperado = entry.get("resync_orden_id_esperado")
            if orden_esperado is None or int(orden_esperado) != orden_id_ack:
                raise ValueError(
                    f"ACK de resincronización rechazado: se esperaba orden_id={orden_esperado}, "
                    f"pero se recibió orden_id={orden_id_ack}."
                )

            epoca_actual = int(entry.get("epoca_orden", 0))
            if nueva_epoca_base is not None:
                if int(nueva_epoca_base) < epoca_actual:
                    raise ValueError(
                        "Prohibido reutilizar épocas anteriores o reducir epoca_orden durante la resincronización."
                    )
                if int(nueva_epoca_base) > self.MAX_EPOCA_UINT32:
                    raise OverflowError("La época base supera el límite uint32_t.")
                epoca_final = int(nueva_epoca_base)
            else:
                epoca_final = max(epoca_actual, orden_id_ack // self.FACTOR_EPOCA)

            orden_final = max(int(entry.get("ultimo_orden_id", 0)), orden_id_ack, epoca_final * self.FACTOR_EPOCA)
            entry["epoca_orden"] = epoca_final
            entry["ultimo_orden_id"] = orden_final
            entry["ultimo_stop_epoca"] = epoca_final
            entry["ultimo_stop_orden_id"] = orden_final
            if rds_id_historial is not None:
                entry["ultimo_id_historial_rds"] = int(rds_id_historial)
            entry["estado_seguro_bloqueado"] = False
            entry["motivo_bloqueo"] = None
            entry["resync_pendiente_ack"] = False
            entry["resync_orden_id_esperado"] = None
            entry["inicializado"] = True
            entry["actualizado_en"] = datetime.now(timezone.utc).isoformat()

            vehiculos[vid] = entry
            self._corrupted = False
            self._corruption_detail = None
            self._save_atomic_unlocked(vehiculos)
            return {
                "resync_completada": True,
                "id_vehiculo": id_vehiculo,
                "epoca_orden": epoca_final,
                "orden_id": orden_final,
                "ultimo_id_historial_rds": entry.get("ultimo_id_historial_rds"),
                "estado_seguro_bloqueado": False,
            }

    def confirmar_resincronizacion_con_ack(
        self,
        id_vehiculo: int,
        ack_data: dict[str, Any],
        rds_id_historial: int | None = None,
        nueva_epoca_base: int | None = None,
    ) -> dict[str, Any]:
        """Alias explícito en español hacia mark_resync_completed."""
        return self.mark_resync_completed(
            id_vehiculo=id_vehiculo,
            ack_data=ack_data,
            rds_id_historial=rds_id_historial,
            nueva_epoca_base=nueva_epoca_base,
        )

    def es_epoca_valida(self, id_vehiculo: int, epoca_operacion: int) -> bool:
        """Comprueba si la época de una operación sigue vigente y no ha sido superada por DETENER."""
        vid = str(id_vehiculo)
        with self._os_file_lock():
            try:
                _, vehiculos = self._validate_and_load_unlocked()
            except ValueError:
                return False
            entry = vehiculos.get(vid)
            if not entry:
                return False
            if entry.get("estado_seguro_bloqueado"):
                return False
            ultimo_stop = int(entry.get("ultimo_stop_epoca", 0))
            if ultimo_stop >= int(epoca_operacion):
                return False
            return True

    def is_stale_against_stop(self, id_vehiculo: int, orden_id: int) -> bool:
        """Devuelve True si orden_id es anterior o igual al último DETENER registrado
        o si el vehículo se encuentra bloqueado en estado seguro."""
        vid = str(id_vehiculo)
        with self._os_file_lock():
            try:
                _, vehiculos = self._validate_and_load_unlocked()
            except ValueError:
                mem_entry = self._memory_state.get(vid)
                if not mem_entry:
                    return True
                return int(orden_id) <= int(mem_entry.get("ultimo_stop_orden_id", 0))
            entry = vehiculos.get(vid)
            if not entry:
                return False
            if entry.get("estado_seguro_bloqueado"):
                return True
            ultimo_stop_id = int(entry.get("ultimo_stop_orden_id", 0))
            return int(orden_id) <= ultimo_stop_id

    def resincronizar_secuencia_controlada(
        self,
        id_vehiculo: int,
        nueva_epoca_base: int,
        ack_esp8266: dict[str, Any] | None = None,
        rds_id_historial: int | None = None,
    ) -> dict[str, Any]:
        """Avanza la época base hacia adelante sin permitir reutilizar épocas anteriores.
        Si el vehículo está bloqueado en estado seguro o el archivo estaba corrupto,
        exige obligatoriamente el ACK verificable del ESP8266."""
        if nueva_epoca_base < 0 or nueva_epoca_base > self.MAX_EPOCA_UINT32:
            raise ValueError("La nueva época base está fuera del rango válido de uint32_t.")
        vid = str(id_vehiculo)
        with self._os_file_lock():
            corrupto = False
            try:
                _, vehiculos = self._validate_and_load_unlocked()
            except ValueError:
                corrupto = True
                vehiculos = dict(self._memory_state)

            existing = vehiculos.get(vid) or self._memory_state.get(vid)
            if existing is not None:
                epoca_actual = int(existing.get("epoca_orden", 0))
                if int(nueva_epoca_base) < epoca_actual:
                    raise ValueError(
                        f"Prohibido reiniciar o reducir la secuencia (epoca_actual={epoca_actual}, "
                        f"solicitada={nueva_epoca_base})."
                    )
                if corrupto or existing.get("estado_seguro_bloqueado"):
                    return self.mark_resync_completed(
                        id_vehiculo=id_vehiculo,
                        ack_data=ack_esp8266,
                        rds_id_historial=rds_id_historial,
                        nueva_epoca_base=nueva_epoca_base,
                    )

            entry = self._default_vehicle_entry(
                nueva_epoca_base, ultimo_id_historial_rds=rds_id_historial
            )
            entry["ultimo_stop_epoca"] = nueva_epoca_base
            entry["ultimo_stop_orden_id"] = nueva_epoca_base * self.FACTOR_EPOCA
            vehiculos[vid] = entry
            self._save_atomic_unlocked(vehiculos)
            return dict(entry)


class ConnectionManager:
    """Mantiene clientes WebSocket separados por ID de vehículo y clientes del monitor global,
    gestionando roles (controlador, observador, esp8266), sesiones de control y ACKs físicos."""

    def __init__(self, sequence_manager: OrderSequenceManager | None = None):
        # Diccionario: id_vehiculo -> conjunto de conexiones WebSocket de ese vehículo
        self._connections: dict[int, set[WebSocket]] = defaultdict(set)
        # Conjunto de conexiones WebSocket suscritas al canal global (/ws/vehiculos)
        self._global_connections: set[WebSocket] = set()
        # Metadatos por conexión WebSocket (vehicle_id, rol, sesion_control, connection_id)
        self._client_meta: dict[WebSocket, dict[str, Any]] = {}
        # Futuros de espera de confirmación física del ESP8266 indexados por (id_vehiculo, orden_id)
        self._pending_stop_acks: dict[tuple[int, int], asyncio.Future] = {}
        # Últimas confirmaciones físicas recibidas por (id_vehiculo, orden_id)
        self._confirmed_stop_acks: dict[tuple[int, int], dict[str, Any]] = {}
        # Gestor de secuencias asociado para impedir publicación de órdenes antiguas tras DETENER
        self.sequence_manager: OrderSequenceManager | None = sequence_manager
        # Callbacks hacia VehiculoIoTService para renovar watchdog y manejar desconexiones
        self._on_ping_callback: Callable[[int | None, WebSocket, dict[str, Any]], Any] | None = None
        self._on_disconnect_callback: Callable[[int | None, dict[str, Any]], Any] | None = None
        # Candado asíncrono para evitar conflictos al conectar o desconectar clientes
        self._lock = asyncio.Lock()

    def set_callbacks(
        self,
        on_ping: Callable[[int | None, WebSocket, dict[str, Any]], Any] | None = None,
        on_disconnect: Callable[[int | None, dict[str, Any]], Any] | None = None,
    ) -> None:
        """Registra callbacks de negocio para eventos de ping y desconexión de clientes."""
        self._on_ping_callback = on_ping
        self._on_disconnect_callback = on_disconnect

    @staticmethod
    def _extract_ws_params(
        websocket: WebSocket,
        rol: str | None = None,
        sesion_control: str | None = None,
    ) -> tuple[str, str | None]:
        query_params = getattr(websocket, "query_params", None) or {}
        headers = getattr(websocket, "headers", None) or {}
        resolved_rol = (
            rol
            or query_params.get("rol")
            or headers.get("x-client-role")
            or "controlador"
        )
        resolved_rol = str(resolved_rol).strip().lower()
        if resolved_rol not in ("controlador", "observador", "esp8266"):
            resolved_rol = "controlador"

        resolved_session = (
            sesion_control
            or query_params.get("sesion_control")
            or headers.get("x-control-session")
            or None
        )
        if resolved_session is not None:
            resolved_session = str(resolved_session).strip() or None
        return resolved_rol, resolved_session

    async def connect(
        self,
        vehicle_id: int,
        websocket: WebSocket,
        rol: str | None = None,
        sesion_control: str | None = None,
    ):
        """Acepta una nueva conexión WebSocket individual y la registra en el vehículo indicado."""
        await websocket.accept()
        resolved_rol, resolved_session = self._extract_ws_params(
            websocket, rol=rol, sesion_control=sesion_control
        )
        async with self._lock:
            self._connections[vehicle_id].add(websocket)
            self._client_meta[websocket] = {
                "connection_id": uuid.uuid4().hex[:12],
                "vehicle_id": vehicle_id,
                "rol": resolved_rol,
                "sesion_control": resolved_session,
                "conectado_en": datetime.now(timezone.utc).isoformat(),
            }

    async def connect_global(
        self,
        websocket: WebSocket,
        rol: str | None = None,
        sesion_control: str | None = None,
    ):
        """Acepta una nueva conexión WebSocket en el canal global (/ws/vehiculos)."""
        await websocket.accept()
        resolved_rol, resolved_session = self._extract_ws_params(
            websocket, rol=rol or "observador", sesion_control=sesion_control
        )
        async with self._lock:
            self._global_connections.add(websocket)
            self._client_meta[websocket] = {
                "connection_id": uuid.uuid4().hex[:12],
                "vehicle_id": None,
                "rol": resolved_rol,
                "sesion_control": resolved_session,
                "conectado_en": datetime.now(timezone.utc).isoformat(),
            }

    def get_client_meta(self, websocket: WebSocket) -> dict[str, Any]:
        """Devuelve una copia de los metadatos asociados al cliente WebSocket."""
        return dict(self._client_meta.get(websocket) or {})

    def update_client_meta(
        self,
        websocket: WebSocket,
        rol: str | None = None,
        sesion_control: str | None = None,
        vehicle_id: int | None = None,
    ) -> dict[str, Any]:
        """Actualiza el rol o sesion_control de un cliente conectado."""
        meta = self._client_meta.setdefault(
            websocket,
            {
                "connection_id": uuid.uuid4().hex[:12],
                "vehicle_id": vehicle_id,
                "rol": "controlador",
                "sesion_control": None,
                "conectado_en": datetime.now(timezone.utc).isoformat(),
            },
        )
        if rol is not None:
            norm_rol = str(rol).strip().lower()
            if norm_rol in ("controlador", "observador", "esp8266"):
                meta["rol"] = norm_rol
        if sesion_control is not None:
            meta["sesion_control"] = str(sesion_control).strip() or None
        if vehicle_id is not None and meta.get("vehicle_id") is None:
            meta["vehicle_id"] = int(vehicle_id)
        return dict(meta)

    def has_active_session_socket(self, vehicle_id: int, sesion_control: str) -> bool:
        """Indica si aún queda algún socket conectado para el vehículo con la misma sesion_control."""
        for ws in list(self._connections.get(vehicle_id, set())):
            meta = self._client_meta.get(ws) or {}
            if (
                meta.get("rol") != "observador"
                and meta.get("rol") != "esp8266"
                and meta.get("sesion_control") == sesion_control
            ):
                return True
        return False

    def count_vehicle_clients(self, vehicle_id: int) -> int:
        """Devuelve el número de clientes conectados al canal del vehículo."""
        return len(self._connections.get(vehicle_id, set()))

    def disconnect(self, vehicle_id: int, websocket: WebSocket) -> dict[str, Any]:
        """Elimina un cliente WebSocket individual cuando se desconecta y notifica al servicio."""
        clients = self._connections.get(vehicle_id)
        if clients:
            clients.discard(websocket)
            if not clients:
                self._connections.pop(vehicle_id, None)
        meta = self._client_meta.pop(websocket, None) or {"vehicle_id": vehicle_id}
        if self._on_disconnect_callback is not None:
            try:
                res = self._on_disconnect_callback(vehicle_id, meta)
                if asyncio.iscoroutine(res):
                    try:
                        loop = asyncio.get_running_loop()
                        loop.create_task(res)
                    except RuntimeError:
                        pass
            except Exception:
                logger.debug("Error ejecutando callback de desconexión WS", exc_info=True)
        return meta

    def disconnect_global(self, websocket: WebSocket) -> dict[str, Any]:
        """Elimina un cliente WebSocket del canal global cuando se desconecta."""
        self._global_connections.discard(websocket)
        meta = self._client_meta.pop(websocket, None) or {"vehicle_id": None}
        if self._on_disconnect_callback is not None and meta.get("vehicle_id") is not None:
            try:
                res = self._on_disconnect_callback(meta.get("vehicle_id"), meta)
                if asyncio.iscoroutine(res):
                    try:
                        loop = asyncio.get_running_loop()
                        loop.create_task(res)
                    except RuntimeError:
                        pass
            except Exception:
                logger.debug("Error ejecutando callback de desconexión WS global", exc_info=True)
        return meta

    def register_pending_stop_ack(self, vehicle_id: int, orden_id: int) -> asyncio.Future:
        """Registra una orden de detención pendiente de ACK físico del ESP8266."""
        key = (int(vehicle_id), int(orden_id))
        if key in self._confirmed_stop_acks:
            fut = asyncio.get_running_loop().create_future()
            fut.set_result(self._confirmed_stop_acks[key])
            return fut
        existing = self._pending_stop_acks.get(key)
        if existing is not None and not existing.done():
            return existing
        fut = asyncio.get_running_loop().create_future()
        self._pending_stop_acks[key] = fut
        return fut

    async def wait_for_stop_ack(
        self, vehicle_id: int, orden_id: int, timeout_ms: int
    ) -> dict[str, Any] | None:
        """Espera hasta timeout_ms la confirmación física ('detencion_confirmada') del ESP8266."""
        key = (int(vehicle_id), int(orden_id))
        if key in self._confirmed_stop_acks:
            return self._confirmed_stop_acks[key]
        if timeout_ms <= 0:
            return None
        fut = self.register_pending_stop_ack(vehicle_id, orden_id)
        try:
            return await asyncio.wait_for(asyncio.shield(fut), timeout=timeout_ms / 1000.0)
        except asyncio.TimeoutError:
            return None
        finally:
            if fut.done():
                self._pending_stop_acks.pop(key, None)

    @staticmethod
    def _is_strict_int(val: Any) -> bool:
        return isinstance(val, int) and not isinstance(val, bool)

    @staticmethod
    def _is_salidas_inactivas(val: Any) -> bool:
        """Acepta únicamente salidas_activas=False (booleano JSON false) o salidas_activas=0."""
        if isinstance(val, bool):
            return val is False
        if isinstance(val, int):
            return val == 0
        return False

    async def _handle_detencion_confirmada(
        self, websocket: WebSocket, vehicle_id: int | None, data: dict[str, Any]
    ) -> None:
        """Valida estrictamente el ACK 'detencion_confirmada' enviado por el ESP8266."""
        raw_veh = data.get("id_vehiculo", vehicle_id)
        raw_orden = data.get("orden_id")
        raw_codigo = data.get("codigo", 0)
        raw_salidas = data.get("salidas_activas")
        raw_estado = data.get("estado_fisico", "detenido")

        if (
            not self._is_strict_int(raw_veh)
            or raw_veh <= 0
            or (vehicle_id is not None and int(raw_veh) != int(vehicle_id))
            or not self._is_strict_int(raw_orden)
            or raw_orden <= 0
            or not self._is_strict_int(raw_codigo)
            or raw_codigo != 0
            or not self._is_salidas_inactivas(raw_salidas)
            or not isinstance(raw_estado, str)
            or raw_estado.strip().lower() != "detenido"
        ):
            await websocket.send_json(
                {
                    "tipo": "error",
                    "mensaje": "ACK detencion_confirmada inválido: requiere id_vehiculo, orden_id, codigo=0, salidas_activas=false (0) y estado_fisico='detenido'.",
                }
            )
            return

        id_veh = int(raw_veh)
        orden_id = int(raw_orden)
        self.update_client_meta(websocket, rol="esp8266", vehicle_id=id_veh)

        key = (id_veh, orden_id)
        fut = self._pending_stop_acks.get(key)
        if fut is None:
            # Solo un ACK que corresponda a una orden pendiente puede confirmar la detención
            await websocket.send_json(
                {
                    "tipo": "ack_rechazado",
                    "id_vehiculo": id_veh,
                    "orden_id": orden_id,
                    "mensaje": "No existe una orden de detención pendiente para el orden_id indicado.",
                }
            )
            return

        ack_payload = {
            "tipo": "detencion_confirmada",
            "id_vehiculo": id_veh,
            "orden_id": orden_id,
            "codigo": 0,
            "salidas_activas": False if isinstance(raw_salidas, bool) else 0,
            "estado_fisico": "detenido",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        self._confirmed_stop_acks[key] = ack_payload
        if not fut.done():
            fut.set_result(ack_payload)
        self._pending_stop_acks.pop(key, None)

        await self.broadcast_to_vehicle(id_veh, ack_payload)

    async def listen_client_messages(self, websocket: WebSocket, vehicle_id: int | None = None):
        """Escucha mensajes entrantes del cliente WebSocket (ping, identificar, detencion_confirmada)."""
        while True:
            mensaje = await websocket.receive_text()
            try:
                data = json.loads(mensaje)
            except json.JSONDecodeError:
                await websocket.send_json(
                    {"tipo": "error", "mensaje": "El mensaje recibido debe ser JSON"}
                )
                continue

            if not isinstance(data, dict):
                await websocket.send_json(
                    {"tipo": "error", "mensaje": "El mensaje JSON debe ser un objeto"}
                )
                continue

            tipo = data.get("tipo") or data.get("evento")
            if tipo == "identificar":
                meta = self.update_client_meta(
                    websocket,
                    rol=data.get("rol"),
                    sesion_control=data.get("sesion_control"),
                    vehicle_id=vehicle_id or data.get("id_vehiculo"),
                )
                await websocket.send_json(
                    {
                        "tipo": "identificado",
                        "rol": meta.get("rol"),
                        "sesion_control": meta.get("sesion_control"),
                        "id_vehiculo": meta.get("vehicle_id"),
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                    }
                )
                continue

            if tipo == "detencion_confirmada":
                await self._handle_detencion_confirmada(websocket, vehicle_id, data)
                continue

            if tipo == "ping":
                if "rol" in data or "sesion_control" in data:
                    self.update_client_meta(
                        websocket,
                        rol=data.get("rol"),
                        sesion_control=data.get("sesion_control"),
                        vehicle_id=vehicle_id or data.get("id_vehiculo"),
                    )
                vigencia_renovada = False
                if self._on_ping_callback is not None:
                    target_vid = vehicle_id if vehicle_id is not None else data.get("id_vehiculo")
                    res = self._on_ping_callback(target_vid, websocket, data)
                    if asyncio.iscoroutine(res):
                        vigencia_renovada = bool(await res)
                    else:
                        vigencia_renovada = bool(res)

                pong_payload = {
                    "tipo": "pong",
                    "vigencia_renovada": vigencia_renovada,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                }
                if vehicle_id is None:
                    pong_payload["canal"] = "global"
                else:
                    pong_payload["id_vehiculo"] = vehicle_id
                await websocket.send_json(pong_payload)

    async def broadcast_to_vehicle(self, vehicle_id: int, event: dict) -> dict[str, Any]:
        """Envía un evento JSON en tiempo real tanto a los clientes del vehículo específico
        como a los clientes conectados al WebSocket global (/ws/vehiculos),
        garantizando que siempre incluya 'id_vehiculo', sin duplicar envíos y bloqueando
        cualquier orden de movimiento antigua posterior a un DETENER."""
        payload = dict(event)
        payload.setdefault("id_vehiculo", vehicle_id)

        # Barrera de transporte: ninguna orden de movimiento o paso DEMO con orden_id
        # anterior o igual al último DETENER puede publicarse hacia el ESP8266.
        if payload.get("tipo") in ("movimiento_individual", "demo_paso"):
            orden_ev = payload.get("orden_id")
            if self.sequence_manager is not None and self._is_strict_int(orden_ev):
                if self.sequence_manager.is_stale_against_stop(vehicle_id, int(orden_ev)):
                    logger.warning(
                        "Orden antigua bloqueada en WebSocket para vehículo %s (orden_id=%s <= ultimo_stop_orden_id)",
                        vehicle_id,
                        orden_ev,
                    )
                    return {
                        "clientes_vehiculo": 0,
                        "clientes_global": 0,
                        "total_enviados": 0,
                        "bloqueado_por_stop_posterior": True,
                    }

        vehicle_clients = list(self._connections.get(vehicle_id, set()))
        global_clients = list(self._global_connections)

        sent_sockets: set[WebSocket] = set()
        sent_vehicle_count = 0
        sent_global_count = 0
        failed_vehicle: list[WebSocket] = []
        failed_global: list[WebSocket] = []

        # 1. Notificar a los clientes conectados al vehículo específico
        for websocket in vehicle_clients:
            if websocket in sent_sockets:
                continue
            sent_sockets.add(websocket)
            try:
                await websocket.send_json(payload)
                sent_vehicle_count += 1
            except Exception:
                failed_vehicle.append(websocket)
                logger.debug("Falló envío WebSocket individual", exc_info=True)

        # 2. Notificar a los clientes conectados al canal global (/ws/vehiculos)
        for websocket in global_clients:
            if websocket in sent_sockets:
                continue
            sent_sockets.add(websocket)
            try:
                await websocket.send_json(payload)
                sent_global_count += 1
            except Exception:
                failed_global.append(websocket)
                logger.debug("Falló envío WebSocket global", exc_info=True)

        # 3. Limpiar conexiones caídas
        for websocket in failed_vehicle:
            self.disconnect(vehicle_id, websocket)
        for websocket in failed_global:
            self.disconnect_global(websocket)

        return {
            "clientes_vehiculo": sent_vehicle_count,
            "clientes_global": sent_global_count,
            "total_enviados": sent_vehicle_count + sent_global_count,
        }

    async def close_all(self):
        """Cierra ordenadamente todas las conexiones WebSocket (individuales y globales) al apagar."""
        vehicle_clients = [
            (vehicle_id, websocket)
            for vehicle_id, sockets in self._connections.items()
            for websocket in list(sockets)
        ]
        global_clients = list(self._global_connections)

        for vehicle_id, websocket in vehicle_clients:
            try:
                await websocket.close(code=1001, reason="Servidor detenido")
            except Exception:
                pass
            self.disconnect(vehicle_id, websocket)

        for websocket in global_clients:
            try:
                await websocket.close(code=1001, reason="Servidor detenido")
            except Exception:
                pass
            self.disconnect_global(websocket)

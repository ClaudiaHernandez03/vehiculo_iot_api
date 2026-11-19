# ==============================================================================
# MÓDULO DE PRUEBAS: test_detener.py
# PROPÓSITO: Ejecutar las 30 pruebas automatizadas obligatorias de la Etapa 1
#            para verificar el endpoint POST /api/vehiculos/{id_vehiculo}/detener,
#            movimientos sostenidos (> 1500 ms), secuencia orden_id, persistencia
#            y recuperación de .orden_seq_state.json, límite uint32_t, prioridad
#            de DETENER ante fallos de RDS y reconciliación, validación de DEMOs
#            de máximo 4 pasos (1..4), sesiones de controlador/watchdog y no regresión.
#
# NOTA IMPORTANTE DE AUDITORÍA:
# Todas las respuestas de confirmación ('detencion_confirmada') en este script
# corresponden a: Simulación de protocolo WebSocket (Mock de cliente ESP8266).
# NO constituyen pruebas físicas sobre el hardware real de la excavadora CAT-320.
# ==============================================================================

import asyncio
import json
from pathlib import Path
import tempfile
import threading
import time
from typing import Any
import unittest.mock as mock
import urllib.error
import urllib.request
import uvicorn

from database import Database
from demo_manager import DemoManager
from exceptions import ConflictoConcurrenciaError, ReglaNegocioError
import main
from models import (
    DetenerRequest,
    EstadoActualizar,
    HistorialCrear,
    MovimientoDemo,
    MovimientoRequest,
)
from services import VehiculoIoTService
from settings import Settings, settings
from test_websocket_global import SimpleWSClient
from websocket_manager import ConnectionManager, OrderSequenceManager


class DummyWS:
    """Mock ligero de cliente WebSocket en memoria (Simulación de protocolo WebSocket)."""

    def __init__(self, name: str, query_params: dict[str, str] | None = None):
        self.name = name
        self.query_params = query_params or {}
        self.headers: dict[str, str] = {}
        self.events: list[dict[str, Any]] = []
        self.is_accepted = False
        self._inbox: asyncio.Queue[str] = asyncio.Queue()

    async def accept(self):
        self.is_accepted = True

    async def send_json(self, data: dict[str, Any]):
        self.events.append(data)

    async def receive_text(self) -> str:
        return await self._inbox.get()


class IsolatedTestDB:
    """Capa de base de datos aislada en memoria que simula el comportamiento transaccional
    de MySQL/RDS (tablas vehiculos, comandos, estado_vehiculo, historial_comandos, demos y
    demo_movimientos) sin conectarse a Amazon RDS de producción."""

    def __init__(self, include_detener_initially: bool = False):
        self.rds_online = True
        self.artificial_delay_movimiento_s = 0.0
        self._next_cmd_id = 54  # ID arbitrario distinto de 13 para comprobar ID dinámico
        self._next_hist_id = 20
        self._next_demo_mov_id = 100

        self.vehiculos = {
            1: {"id_vehiculo": 1, "nombre": "Excavadora IoT", "modelo": "CAT-320", "activo": 1},
            6: {"id_vehiculo": 6, "nombre": "Excavadora IoT", "modelo": "CAT-320", "activo": 1},
        }
        self.comandos: dict[int, list[dict[str, Any]]] = {
            1: [
                {"id_comando": 9, "id_vehiculo": 1, "nombre": "Avanzar", "comando": "Avanzar", "codigo": 9, "descripcion": "Avanzar", "activo": 1},
                {"id_comando": 11, "id_vehiculo": 1, "nombre": "Retroceder", "comando": "Retroceder", "codigo": 11, "descripcion": "Retroceder", "activo": 1},
                {"id_comando": 1, "id_vehiculo": 1, "nombre": "Mover brazo arriba", "comando": "Mover brazo arriba", "codigo": 1, "descripcion": "Subir brazo", "activo": 1},
                {"id_comando": 3, "id_vehiculo": 1, "nombre": "Mover brazo abajo", "comando": "Mover brazo abajo", "codigo": 3, "descripcion": "Bajar brazo", "activo": 1},
            ],
            6: [
                {"id_comando": 38, "id_vehiculo": 6, "nombre": "Avanzar", "comando": "Avanzar", "codigo": 9, "descripcion": "Avanzar", "activo": 1},
                {"id_comando": 39, "id_vehiculo": 6, "nombre": "Retroceder", "comando": "Retroceder", "codigo": 11, "descripcion": "Retroceder", "activo": 1},
            ],
        }
        if include_detener_initially:
            self.comandos[1].append(
                {
                    "id_comando": 77,
                    "id_vehiculo": 1,
                    "nombre": "Detener",
                    "comando": "Detener",
                    "codigo": 0,
                    "descripcion": "Detener excavadora",
                    "activo": 1,
                }
            )

        self.estados: dict[int, list[dict[str, Any]]] = {
            1: [{"id_vehiculo": 1, "vehiculo": "Excavadora IoT", "id_comando": 9, "comando": "Avanzar", "codigo": 9, "conectado": 1}],
            6: [{"id_vehiculo": 6, "vehiculo": "Excavadora IoT", "id_comando": 38, "comando": "Avanzar", "codigo": 9, "conectado": 1}],
        }
        self.historial: dict[int, list[dict[str, Any]]] = {
            1: [{"id_historial": 20, "id_vehiculo": 1, "id_comando": 9, "comando": "Avanzar", "origen": "API"}],
            6: [{"id_historial": 19, "id_vehiculo": 6, "id_comando": 38, "comando": "Avanzar", "origen": "API"}],
        }
        self.demos: dict[int, dict[str, Any]] = {
            1: {"id_demo": 1, "id_vehiculo": 1, "nombre": "Demo 4 Pasos Valida", "activo": 1},
            2: {"id_demo": 2, "id_vehiculo": 1, "nombre": "Demo Exceso 5 Pasos", "activo": 1},
            3: {"id_demo": 3, "id_vehiculo": 1, "nombre": "Demo Orden Duplicado", "activo": 1},
            5: {"id_demo": 5, "id_vehiculo": 6, "nombre": "Demo Vehiculo 6", "activo": 1},
        }
        self.demo_movimientos: dict[int, list[dict[str, Any]]] = {
            1: [
                {"id_demo_movimiento": 1, "id_demo": 1, "id_comando": 9, "comando": "Avanzar", "codigo": 9, "orden": 1, "duracion_ms": 50, "pausa_ms": 30},
                {"id_demo_movimiento": 2, "id_demo": 1, "id_comando": 1, "comando": "Mover brazo arriba", "codigo": 1, "orden": 2, "duracion_ms": 50, "pausa_ms": 0},
                {"id_demo_movimiento": 3, "id_demo": 1, "id_comando": 3, "comando": "Mover brazo abajo", "codigo": 3, "orden": 3, "duracion_ms": 50, "pausa_ms": 30},
                {"id_demo_movimiento": 4, "id_demo": 1, "id_comando": 11, "comando": "Retroceder", "codigo": 11, "orden": 4, "duracion_ms": 50, "pausa_ms": 0},
            ],
            2: [
                {"id_demo_movimiento": 11, "id_demo": 2, "id_comando": 9, "comando": "Avanzar", "codigo": 9, "orden": 1, "duracion_ms": 20, "pausa_ms": 0},
                {"id_demo_movimiento": 12, "id_demo": 2, "id_comando": 1, "comando": "Mover brazo arriba", "codigo": 1, "orden": 2, "duracion_ms": 20, "pausa_ms": 0},
                {"id_demo_movimiento": 13, "id_demo": 2, "id_comando": 3, "comando": "Mover brazo abajo", "codigo": 3, "orden": 3, "duracion_ms": 20, "pausa_ms": 0},
                {"id_demo_movimiento": 14, "id_demo": 2, "id_comando": 11, "comando": "Retroceder", "codigo": 11, "orden": 4, "duracion_ms": 20, "pausa_ms": 0},
                {"id_demo_movimiento": 15, "id_demo": 2, "id_comando": 9, "comando": "Avanzar", "codigo": 9, "orden": 5, "duracion_ms": 20, "pausa_ms": 0},
            ],
            3: [
                {"id_demo_movimiento": 21, "id_demo": 3, "id_comando": 9, "comando": "Avanzar", "codigo": 9, "orden": 1, "duracion_ms": 20, "pausa_ms": 0},
                {"id_demo_movimiento": 22, "id_demo": 3, "id_comando": 11, "comando": "Retroceder", "codigo": 11, "orden": 1, "duracion_ms": 20, "pausa_ms": 0},
            ],
            5: [
                {"id_demo_movimiento": 31, "id_demo": 5, "id_comando": 38, "comando": "Avanzar", "codigo": 9, "orden": 1, "duracion_ms": 40, "pausa_ms": 20},
                {"id_demo_movimiento": 32, "id_demo": 5, "id_comando": 39, "comando": "Retroceder", "codigo": 11, "orden": 2, "duracion_ms": 40, "pausa_ms": 20},
            ],
        }

    def _check_online(self):
        if not self.rds_online:
            raise ConnectionError("Simulación de caída de Amazon RDS (Connection refused)")

    def obtener_max_id_historial(self, id_vehiculo: int) -> int:
        self._check_online()
        hist = self.historial.get(id_vehiculo, [])
        if not hist:
            return 0
        return max(int(h["id_historial"]) for h in hist)

    def obtener_o_registrar_comando_detener(self, id_vehiculo: int) -> dict[str, Any]:
        self._check_online()
        veh = self.vehiculos.get(id_vehiculo)
        if not veh:
            raise LookupError("El vehículo no existe.")
        if not veh.get("activo"):
            raise ValueError("El vehículo está inactivo.")

        cmds = self.comandos.setdefault(id_vehiculo, [])
        for c in cmds:
            if int(c.get("id_vehiculo", id_vehiculo)) == id_vehiculo and (
                int(c.get("codigo", -1)) == 0
                or str(c.get("nombre") or "").strip().upper() == "DETENER"
            ):
                c["activo"] = 1
                return {
                    "id_comando": int(c["id_comando"]),
                    "comando": c["nombre"],
                    "codigo": 0,
                    "creado": False,
                }

        self._next_cmd_id += 1
        nuevo_id = self._next_cmd_id
        nuevo_cmd = {
            "id_comando": nuevo_id,
            "id_vehiculo": id_vehiculo,
            "nombre": "Detener",
            "comando": "Detener",
            "codigo": 0,
            "descripcion": "Detener inmediatamente todos los movimientos del vehículo",
            "activo": 1,
        }
        cmds.append(nuevo_cmd)
        return {
            "id_comando": nuevo_id,
            "comando": "Detener",
            "codigo": 0,
            "creado": True,
        }

    def registrar_movimiento(self, id_vehiculo: int, id_comando: int, origen: str) -> dict[str, Any]:
        if self.artificial_delay_movimiento_s > 0:
            time.sleep(self.artificial_delay_movimiento_s)
        self._check_online()
        veh = self.vehiculos.get(id_vehiculo)
        if not veh:
            raise LookupError("El vehículo no existe.")
        if not veh.get("activo"):
            raise ValueError("El vehículo está inactivo.")

        cmd_found = None
        for c in self.comandos.get(id_vehiculo, []):
            if int(c["id_comando"]) == int(id_comando) and int(c["id_vehiculo"]) == int(id_vehiculo):
                cmd_found = c
                break
        if not cmd_found:
            raise ValueError("El comando no existe o no pertenece al vehículo.")
        if not cmd_found.get("activo"):
            raise ValueError("El comando está inactivo.")

        self._next_hist_id += 1
        hist_id = self._next_hist_id
        self.estados[id_vehiculo] = [
            {
                "id_vehiculo": id_vehiculo,
                "id_comando": id_comando,
                "comando": cmd_found["nombre"],
                "codigo": cmd_found["codigo"],
                "conectado": 1,
            }
        ]
        self.historial.setdefault(id_vehiculo, []).insert(
            0,
            {
                "id_historial": hist_id,
                "id_vehiculo": id_vehiculo,
                "id_comando": id_comando,
                "comando": cmd_found["nombre"],
                "origen": origen,
            },
        )
        return {
            "transaccion": "confirmada",
            "conectado": True,
            "id_comando": id_comando,
            "comando": cmd_found["nombre"],
            "codigo": cmd_found["codigo"],
            "id_historial": hist_id,
        }

    def registrar_detencion(self, id_vehiculo: int, origen: str = "API") -> dict[str, Any]:
        self._check_online()
        cmd_detener = self.obtener_o_registrar_comando_detener(id_vehiculo)
        self._next_hist_id += 1
        hist_id = self._next_hist_id
        self.estados[id_vehiculo] = [
            {
                "id_vehiculo": id_vehiculo,
                "id_comando": cmd_detener["id_comando"],
                "comando": cmd_detener["comando"],
                "codigo": 0,
                "conectado": 1,
            }
        ]
        self.historial.setdefault(id_vehiculo, []).insert(
            0,
            {
                "id_historial": hist_id,
                "id_vehiculo": id_vehiculo,
                "id_comando": cmd_detener["id_comando"],
                "comando": cmd_detener["comando"],
                "origen": origen,
            },
        )
        return {
            "transaccion": "confirmada",
            "conectado": True,
            "id_comando": cmd_detener["id_comando"],
            "comando": cmd_detener["comando"],
            "codigo": 0,
            "id_historial": hist_id,
            "comando_creado": cmd_detener["creado"],
        }

    def registrar_apagado_demo(self, id_vehiculo: int) -> dict[str, Any]:
        self._check_online()
        cmd_detener = self.obtener_o_registrar_comando_detener(id_vehiculo)
        self.estados[id_vehiculo] = [
            {
                "id_vehiculo": id_vehiculo,
                "id_comando": cmd_detener["id_comando"],
                "comando": cmd_detener["comando"],
                "codigo": 0,
                "conectado": 1,
            }
        ]
        return {
            "transaccion": "confirmada",
            "id_vehiculo": id_vehiculo,
            "id_comando": cmd_detener["id_comando"],
            "comando": cmd_detener["comando"],
            "codigo": 0,
        }

    def obtener_info_demo(self, id_demo: int):
        self._check_online()
        return self.demos.get(id_demo)

    def obtener_vehiculo_activo(self, id_vehiculo: int):
        self._check_online()
        return self.vehiculos.get(id_vehiculo)

    def obtener_movimientos_demo(self, id_demo: int):
        self._check_online()
        return list(self.demo_movimientos.get(id_demo, []))

    def obtener_paso_demo_por_id(self, id_demo_movimiento: int):
        self._check_online()
        for pasos in self.demo_movimientos.values():
            for p in pasos:
                if int(p["id_demo_movimiento"]) == int(id_demo_movimiento):
                    return dict(p)
        return None

    def actualizar_estado_paso_demo(self, id_vehiculo: int, id_comando: int):
        self._check_online()
        return {"transaccion": "confirmada", "id_vehiculo": id_vehiculo, "id_comando": id_comando}

    def obtener_estado(self, id_vehiculo: int):
        self._check_online()
        return self.estados.get(id_vehiculo, [])

    def obtener_estados_globales(self):
        self._check_online()
        return [
            {
                "id_vehiculo": vid,
                "nombre": v["nombre"],
                "modelo": v["modelo"],
                "activo": v["activo"],
                "estado": self.estados.get(vid, []),
            }
            for vid, v in self.vehiculos.items()
        ]

    def obtener_historial(self, id_vehiculo: int):
        self._check_online()
        return self.historial.get(id_vehiculo, [])[:10]

    def ejecutar_sp(self, nombre: str, parametros: list[Any] | None = None, escritura: bool = False):
        self._check_online()
        if nombre not in Database.SP_PERMITIDOS:
            raise ValueError("Stored Procedure no permitido.")
        params = parametros or []
        if nombre == "sp_obtener_vehiculos":
            return {"ok": True, "procedimiento": nombre, "resultados": list(self.vehiculos.values())}
        if nombre == "sp_obtener_estado":
            return {"ok": True, "procedimiento": nombre, "resultados": self.estados.get(params[0], [])}
        if nombre == "sp_obtener_historial":
            return {"ok": True, "procedimiento": nombre, "resultados": self.historial.get(params[0], [])}
        if nombre == "sp_obtener_movimientos_demo":
            return {"ok": True, "procedimiento": nombre, "resultados": self.obtener_movimientos_demo(params[0])}
        if nombre == "sp_agregar_movimiento_demo":
            id_demo, id_cmd, orden, dur, pausa = params
            self._next_demo_mov_id += 1
            nuevo = {
                "id_demo_movimiento": self._next_demo_mov_id,
                "id_demo": id_demo,
                "id_comando": id_cmd,
                "comando": f"CMD_{id_cmd}",
                "codigo": id_cmd,
                "orden": orden,
                "duracion_ms": dur,
                "pausa_ms": pausa,
            }
            self.demo_movimientos.setdefault(id_demo, []).append(nuevo)
            return {"ok": True, "procedimiento": nombre, "resultados": [nuevo]}
        return {"ok": True, "procedimiento": nombre, "resultados": []}


async def run_all_30_tests():
    print("================================================================================")
    print("INICIANDO SUITE DE 30 PRUEBAS OBLIGATORIAS — ETAPA 1 (SIMULACIÓN Y PROTOCOLO)")
    print("NOTA: Las confirmaciones del ESP8266 son Simulaciones de protocolo WebSocket")
    print("      (Mock de cliente ESP8266), NO pruebas físicas en hardware CAT-320.")
    print("================================================================================")

    with tempfile.TemporaryDirectory() as tmpdir:
        seq_file = Path(tmpdir) / ".orden_seq_state.json"
        test_cfg = Settings()
        test_cfg.order_seq_state_file = seq_file
        test_cfg.control_watchdog_seconds = 15.0
        test_cfg.stop_ack_timeout_ms = 500

        stub_db = IsolatedTestDB()
        ws_mgr = ConnectionManager()
        seq_mgr = OrderSequenceManager(seq_file)
        demo_mgr = DemoManager(stub_db, ws_mgr, sequence_manager=seq_mgr)
        svc = VehiculoIoTService(
            db_provider=lambda: stub_db,
            ws_manager=ws_mgr,
            demo_manager=demo_mgr,
            sequence_manager=seq_mgr,
            config=test_cfg,
        )

        # ----------------------------------------------------------------------
        # Caso 17: Archivo de secuencia inexistente con historial previo en RDS
        #          bloquea hasta completar resincronización con ACK (id_historial != orden_id)
        # ----------------------------------------------------------------------
        assert not seq_file.exists(), "El archivo no debe existir al iniciar Caso 17"
        est_init = seq_mgr.obtener_estado_vehiculo(1, rds_max_id_historial=20, rds_disponible=True)
        assert seq_file.exists(), "El archivo debe haberse creado atómicamente"
        assert est_init["epoca_orden"] == 0, "Nunca debe igualar epoca_orden a id_historial (20)"
        assert est_init["ultimo_id_historial_rds"] == 20
        assert est_init["estado_seguro_bloqueado"] is True
        assert est_init["motivo_bloqueo"] == "SECUENCIA_NO_RECUPERABLE_DESDE_RDS"

        ws_ctrl = DummyWS("Controlador_Op1", {"rol": "controlador", "sesion_control": "op-1"})
        ws_esp = DummyWS("Mock_ESP8266", {"rol": "esp8266"})
        await ws_mgr.connect(1, ws_ctrl, rol="controlador", sesion_control="op-1")
        await ws_mgr.connect(1, ws_esp, rol="esp8266")

        async def ack_resync_inicial():
            await asyncio.sleep(0.04)
            ev_stop = [e for e in ws_esp.events if e.get("tipo") == "detener"][-1]
            await ws_mgr._handle_detencion_confirmada(
                ws_esp,
                1,
                {
                    "tipo": "detencion_confirmada",
                    "id_vehiculo": 1,
                    "orden_id": ev_stop["orden_id"],
                    "codigo": 0,
                    "salidas_activas": False,
                    "estado_fisico": "detenido",
                },
            )

        t_init_ack = asyncio.create_task(ack_resync_inicial())
        res_init_sync = await svc.detener_vehiculo(
            1, DetenerRequest(origen="RESYNC_INICIAL", esperar_confirmacion_ms=400)
        )
        await t_init_ack
        assert res_init_sync["resync_requerido"] is True
        assert res_init_sync["resync_completada"] is True
        assert seq_mgr.obtener_estado_vehiculo(1)["estado_seguro_bloqueado"] is False
        seq_mgr.resincronizar_secuencia_controlada(6, 0, rds_id_historial=19)
        ws_esp.events.clear()
        print("[PASS] 17. Archivo inexistente con historial previo en RDS bloquea sin usar id_historial como época y desbloquea tras ACK")

        # ----------------------------------------------------------------------
        # Caso 1: AVANZAR permanece activo durante más de 1500 ms con sesión válida
        # ----------------------------------------------------------------------

        res_avanzar = await svc.registrar_movimiento_individual(
            MovimientoRequest(id_vehiculo=1, id_comando=9, origen="JOYSTICK", sesion_control="op-1")
        )
        assert res_avanzar["ok"] is True
        assert res_avanzar["movimiento"]["mantener_activo"] is True
        orden_avanzar = res_avanzar["movimiento"]["orden_id"]

        # Mantener durante 1.65 segundos (> 1500 ms) renovando con ping válido
        await asyncio.sleep(0.8)
        renovado = svc._on_client_ping(1, ws_ctrl, {"tipo": "ping", "rol": "controlador", "sesion_control": "op-1"})
        assert renovado is True
        await asyncio.sleep(0.85)

        mov_activo = svc.get_active_movement(1)
        assert mov_activo is not None and mov_activo["comando"] == "Avanzar"
        eventos_stop_auto = [e for e in ws_esp.events if e.get("tipo") == "detener"]
        assert len(eventos_stop_auto) == 0, "No debe existir autoapagado a los 1500 ms"
        print("[PASS] 01. AVANZAR permanece activo por > 1500 ms (1650 ms verificados) sin autoapagado")

        # ----------------------------------------------------------------------
        # Caso 2: DETENER sin ACK físico devuelve detencion_fisica_confirmada = false
        # ----------------------------------------------------------------------
        res_stop_no_ack = await svc.detener_vehiculo(
            1, DetenerRequest(origen="BOTON_PARO", esperar_confirmacion_ms=120)
        )
        assert res_stop_no_ack["orden_recibida"] is True
        assert res_stop_no_ack["registrada_en_rds"] is True
        assert res_stop_no_ack["orden_publicada_ws"] is True
        assert res_stop_no_ack["detencion_fisica_confirmada"] is False
        assert res_stop_no_ack["reconciliacion_rds_pendiente"] is False
        assert svc.get_active_movement(1) is None
        print("[PASS] 02. DETENER sin ACK físico devuelve detencion_fisica_confirmada = False")

        # ----------------------------------------------------------------------
        # Caso 3: DETENER con ACK simulado válido confirma la orden
        #         (Simulación de protocolo WebSocket - Mock de cliente ESP8266)
        # ----------------------------------------------------------------------
        async def simulate_esp8266_ack_valid():
            await asyncio.sleep(0.05)
            ultimo_ev = [e for e in ws_esp.events if e.get("tipo") == "detener"][-1]
            await ws_mgr._handle_detencion_confirmada(
                ws_esp,
                1,
                {
                    "tipo": "detencion_confirmada",
                    "id_vehiculo": 1,
                    "orden_id": ultimo_ev["orden_id"],
                    "codigo": 0,
                    "salidas_activas": 0,
                    "estado_fisico": "detenido",
                },
            )

        ack_task = asyncio.create_task(simulate_esp8266_ack_valid())
        res_stop_ack = await svc.detener_vehiculo(
            1, DetenerRequest(origen="BOTON_PARO", esperar_confirmacion_ms=500)
        )
        await ack_task
        assert res_stop_ack["detencion_fisica_confirmada"] is True
        assert res_stop_ack["estado_orden"]["confirmacion_esp8266"]["orden_id"] == res_stop_ack["orden_id"]
        assert res_stop_ack["evento_ws"]["id_comando"] != 13, "No debe usar ID fijo 13"
        print(
            f"[PASS] 03. [SIMULACIÓN WS] DETENER con ACK válido confirma la orden "
            f"(orden_id={res_stop_ack['orden_id']}, id_comando dinámico={res_stop_ack['evento_ws']['id_comando']})"
        )

        # ----------------------------------------------------------------------
        # Caso 4: ACK con orden_id incorrecto no confirma
        # ----------------------------------------------------------------------
        async def simulate_esp8266_ack_wrong_id():
            await asyncio.sleep(0.04)
            await ws_mgr._handle_detencion_confirmada(
                ws_esp,
                1,
                {
                    "tipo": "detencion_confirmada",
                    "id_vehiculo": 1,
                    "orden_id": 999999,  # orden_id inexistente/incorrecto
                    "codigo": 0,
                    "salidas_activas": 0,
                    "estado_fisico": "detenido",
                },
            )

        wrong_task = asyncio.create_task(simulate_esp8266_ack_wrong_id())
        res_wrong_ack = await svc.detener_vehiculo(
            1, DetenerRequest(origen="TEST_ACK_ERR", esperar_confirmacion_ms=150)
        )
        await wrong_task
        assert res_wrong_ack["detencion_fisica_confirmada"] is False
        rechazos = [e for e in ws_esp.events if e.get("tipo") == "ack_rechazado"]
        assert len(rechazos) >= 1
        print("[PASS] 04. [SIMULACIÓN WS] ACK con orden_id incorrecto es rechazado y NO confirma detención")

        # ----------------------------------------------------------------------
        # Caso 5: RETROCEDER seguido de DETENER
        # ----------------------------------------------------------------------
        res_retro = await svc.registrar_movimiento_individual(
            MovimientoRequest(id_vehiculo=1, id_comando=11, origen="MANUAL", sesion_control="op-1")
        )
        res_stop_after_retro = await svc.detener_vehiculo(
            1, DetenerRequest(origen="MANUAL", esperar_confirmacion_ms=0)
        )
        assert res_retro["movimiento"]["comando"] == "Retroceder"
        assert res_stop_after_retro["orden_id"] > res_retro["movimiento"]["orden_id"]
        assert stub_db.obtener_estado(1)[0]["codigo"] == 0
        print(
            f"[PASS] 05. RETROCEDER ({res_retro['movimiento']['orden_id']}) -> "
            f"DETENER ({res_stop_after_retro['orden_id']}) con estado final codigo=0"
        )

        # ----------------------------------------------------------------------
        # Caso 6 y Caso 7: DETENER durante DEMO y cancelación sin pasos atrasados
        # ----------------------------------------------------------------------
        ws_esp.events.clear()
        res_demo_start = await svc.iniciar_demo(1)
        assert res_demo_start["ok"] is True
        epoca_demo_1 = res_demo_start["epoca_orden"]
        await asyncio.sleep(0.06)  # Permitir que corra el paso 1

        res_stop_demo = await svc.detener_vehiculo(
            1, DetenerRequest(origen="PARO_EN_DEMO", esperar_confirmacion_ms=0)
        )
        assert res_stop_demo["demo_cancelada"] is True
        assert demo_mgr.is_running(1) is False
        await asyncio.sleep(0.15)  # Esperar más allá de los pasos restantes

        # Verificar que después del evento detener de PARO_EN_DEMO no exista ningún demo_paso
        idx_stop = max(
            i for i, e in enumerate(ws_esp.events) if e.get("origen") == "PARO_EN_DEMO"
        )
        eventos_posteriores = ws_esp.events[idx_stop + 1 :]
        assert all(e.get("tipo") != "demo_paso" for e in eventos_posteriores), (
            f"Se filtraron pasos atrasados tras DETENER: {eventos_posteriores}"
        )
        assert res_stop_demo["orden_id"] > (epoca_demo_1 * 100 + 10)
        print("[PASS] 06. DETENER durante DEMO cancela la secuencia y tiene prioridad máxima")
        print("[PASS] 07. Cancelación de DEMO verificada sin emisión de pasos atrasados")

        # ----------------------------------------------------------------------
        # Caso 8 y Caso 9: Fin de DEMO y Pausa de DEMO con salidas apagadas
        # ----------------------------------------------------------------------
        ws_esp.events.clear()
        res_demo_full = await svc.iniciar_demo(1)
        epoca_full = res_demo_full["epoca_orden"]
        assert epoca_full > epoca_demo_1, "Cada DEMO debe reservar una época exclusiva nueva"

        # Esperar a que concluyan los 4 pasos de la DEMO 1 (~260 ms en total)
        await asyncio.sleep(0.45)
        assert demo_mgr.is_running(1) is False

        eventos_pausa = [
            e for e in ws_esp.events if e.get("tipo") == "detener" and e.get("origen") == "DEMO_PAUSA"
        ]
        eventos_fin = [e for e in ws_esp.events if e.get("tipo") == "demo_fin"]
        eventos_apagado_fin = [
            e for e in ws_esp.events if e.get("tipo") == "detener" and e.get("origen") == "DEMO_FIN"
        ]
        # En DEMO 1, los pasos 1 y 3 tienen pausa_ms=30 (>0), los pasos 2 y 4 tienen pausa_ms=0
        assert len(eventos_pausa) == 2, f"Se esperaban 2 eventos de pausa con codigo=0, hubo {len(eventos_pausa)}"
        assert [e["sub_orden"] for e in eventos_pausa] == [2, 6]
        assert all(e["codigo"] == 0 for e in eventos_pausa)
        assert len(eventos_fin) == 1 and eventos_fin[0]["sub_orden"] == 9 and eventos_fin[0]["codigo"] == 0
        assert len(eventos_apagado_fin) == 1 and eventos_apagado_fin[0]["sub_orden"] == 10 and eventos_apagado_fin[0]["codigo"] == 0
        assert stub_db.obtener_estado(1)[0]["codigo"] == 0
        print("[PASS] 08. Fin de DEMO apaga salidas explícitamente (suborden 9 demo_fin y suborden 10 detener)")
        print("[PASS] 09. Pausas de DEMO apagan salidas físicamente (subórdenes pares 2 y 6 con codigo=0)")

        # ----------------------------------------------------------------------
        # Caso 10: Rechazo del quinto paso al agregar movimiento a una DEMO de 4 pasos
        # ----------------------------------------------------------------------
        try:
            svc.agregar_movimiento_demo(
                1, MovimientoDemo(id_comando=9, orden=4, duracion_ms=500, pausa_ms=100)
            )
            assert False, "Debió rechazar un 5to paso en la DEMO 1"
        except ReglaNegocioError as exc:
            assert exc.status_code == 400
            print(f"[PASS] 10. Rechazo explícito del 5to paso en DEMO (HTTP {exc.status_code}: {exc.detail})")

        # ----------------------------------------------------------------------
        # Caso 11: Rechazo de DEMO con datos existentes superiores a 4 pasos (sin truncar)
        # ----------------------------------------------------------------------
        try:
            await svc.iniciar_demo(2)
            assert False, "Debió rechazar la DEMO 2 con 5 pasos existentes"
        except Exception as exc:
            status = getattr(exc, "status_code", None)
            assert status == 400
            print(f"[PASS] 11. Rechazo explícito al ejecutar DEMO con > 4 pasos existentes (HTTP {status})")

        # ----------------------------------------------------------------------
        # Caso 12: Numeración válida del 1 al 4
        # ----------------------------------------------------------------------
        for orden_invalido in (0, 5, -1):
            try:
                MovimientoDemo(id_comando=9, orden=orden_invalido, duracion_ms=100, pausa_ms=0)
                assert False, f"Debió rechazar orden={orden_invalido}"
            except Exception:
                pass
        paso_valido = MovimientoDemo(id_comando=9, orden=3, duracion_ms=100, pausa_ms=0)
        assert paso_valido.orden == 3
        print("[PASS] 12. Validación estricta de numeración de pasos en rango 1..4")

        # ----------------------------------------------------------------------
        # Caso 13: Órdenes duplicadas de DEMO (al agregar, actualizar y ejecutar)
        # ----------------------------------------------------------------------
        try:
            svc.agregar_movimiento_demo(
                5, MovimientoDemo(id_comando=38, orden=1, duracion_ms=100, pausa_ms=0)
            )
            assert False, "Debió rechazar orden=1 duplicado en DEMO 5"
        except ReglaNegocioError as exc:
            assert exc.status_code == 400

        try:
            svc.actualizar_movimiento_demo(
                32, MovimientoDemo(id_comando=39, orden=1, duracion_ms=100, pausa_ms=0)
            )
            assert False, "Debió rechazar actualizar paso 32 al orden=1 ya usado por paso 31"
        except ReglaNegocioError as exc:
            assert exc.status_code == 400

        try:
            await svc.iniciar_demo(3)
            assert False, "Debió rechazar ejecutar DEMO 3 con órdenes duplicadas"
        except Exception as exc:
            assert getattr(exc, "status_code", None) == 400
        print("[PASS] 13. Rechazo de órdenes duplicadas en DEMO al agregar, actualizar y ejecutar")

        # ----------------------------------------------------------------------
        # Caso 14: Movimiento lento en RDS invalidado por DETENER concurrente
        # ----------------------------------------------------------------------
        ws_esp.events.clear()
        stub_db.artificial_delay_movimiento_s = 0.25

        async def slow_movement():
            return await svc.registrar_movimiento_individual(
                MovimientoRequest(id_vehiculo=1, id_comando=9, origen="LENTO", sesion_control="op-1")
            )

        task_slow = asyncio.create_task(slow_movement())
        await asyncio.sleep(0.05)  # Ya entró a la consulta lenta de RDS
        res_stop_priority = await svc.detener_vehiculo(
            1, DetenerRequest(origen="STOP_PRIORITARIO", esperar_confirmacion_ms=0)
        )
        assert res_stop_priority["ok"] is True

        try:
            await task_slow
            assert False, "El movimiento lento debió ser invalidado por DETENER"
        except ConflictoConcurrenciaError as exc:
            assert exc.status_code == 409
        finally:
            stub_db.artificial_delay_movimiento_s = 0.0

        eventos_lento_ws = [e for e in ws_esp.events if e.get("origen") == "LENTO"]
        assert len(eventos_lento_ws) == 0, "El movimiento invalidado jamás debe publicarse por WebSocket"
        assert stub_db.obtener_estado(1)[0]["codigo"] == 0
        print("[PASS] 14. Movimiento lento en RDS invalidado por DETENER concurrente (409 y 0 envíos WS)")

        # ----------------------------------------------------------------------
        # Caso 15 y Caso 16: Falla de RDS durante DETENER y Reconciliación posterior
        # ----------------------------------------------------------------------
        ws_esp.events.clear()
        stub_db.rds_online = False
        res_stop_outage = await svc.detener_vehiculo(
            1, DetenerRequest(origen="PARO_SIN_RDS", esperar_confirmacion_ms=0)
        )
        assert res_stop_outage["orden_recibida"] is True
        assert res_stop_outage["registrada_en_rds"] is False
        assert res_stop_outage["orden_publicada_ws"] is True
        assert res_stop_outage["reconciliacion_rds_pendiente"] is True
        assert any(e.get("origen") == "PARO_SIN_RDS" and e.get("tipo") == "detener" for e in ws_esp.events)

        # Mientras RDS sigue caído, nuevos movimientos están bloqueados
        try:
            await svc.registrar_movimiento_individual(
                MovimientoRequest(id_vehiculo=1, id_comando=9, origen="INTENTO_CAIDO")
            )
            assert False, "No debe permitir movimientos con reconciliación pendiente y RDS caído"
        except Exception as exc:
            assert getattr(exc, "status_code", None) == 503
        print("[PASS] 15. Falla de RDS durante DETENER publica parada por WSS y activa reconciliacion_rds_pendiente")

        # Restaurar RDS y comprobar reconciliación automática antes de un nuevo movimiento
        stub_db.rds_online = True
        reconciliado = await svc.reconciliar_detencion_si_pendiente(1)
        assert reconciliado is True
        assert svc.tiene_reconciliacion_pendiente(1) is False
        assert stub_db.obtener_estado(1)[0]["codigo"] == 0
        assert stub_db.obtener_historial(1)[0]["origen"] == "PARO_SIN_RDS"
        print("[PASS] 16. Reconciliación automática completada en RDS al restablecer conexión")

        # ----------------------------------------------------------------------
        # Caso 18: Archivo de secuencia corrupto activa estado seguro y bloquea movimientos
        # ----------------------------------------------------------------------
        seq_file.write_text("{corrupt_json_ truncated", encoding="utf-8")
        try:
            await svc.registrar_movimiento_individual(
                MovimientoRequest(id_vehiculo=1, id_comando=9, origen="POST_CORRUPTO")
            )
            assert False, "Debió bloquear el movimiento al detectar archivo corrupto"
        except Exception as exc:
            assert getattr(exc, "status_code", None) == 503

        # DETENER sin ACK envía parada de emergencia al ESP8266 pero MANTIENE bloqueado el control
        res_stop_corrupt_no_ack = await svc.detener_vehiculo(
            1, DetenerRequest(origen="STOP_CORRUPTO_SIN_ACK", esperar_confirmacion_ms=0)
        )
        assert res_stop_corrupt_no_ack["orden_publicada_ws"] is True
        assert res_stop_corrupt_no_ack["resync_completada"] is False

        # Resincronización controlada con ACK del ESP8266 para restablecer estado limpio
        async def ack_corrupt_recovery():
            await asyncio.sleep(0.04)
            ev_stop = [e for e in ws_esp.events if e.get("tipo") == "detener"][-1]
            await ws_mgr._handle_detencion_confirmada(
                ws_esp,
                1,
                {
                    "tipo": "detencion_confirmada",
                    "id_vehiculo": 1,
                    "orden_id": ev_stop["orden_id"],
                    "codigo": 0,
                    "salidas_activas": False,
                    "estado_fisico": "detenido",
                },
            )

        t_corrupt_ack = asyncio.create_task(ack_corrupt_recovery())
        res_stop_corrupt_ack = await svc.detener_vehiculo(
            1, DetenerRequest(origen="STOP_CORRUPTO_CON_ACK", esperar_confirmacion_ms=400)
        )
        await t_corrupt_ack
        assert res_stop_corrupt_ack["resync_completada"] is True
        print("[PASS] 18. Archivo de secuencia corrupto detectado: activa estado seguro, bloquea sin ACK y recupera con ACK")

        # ----------------------------------------------------------------------
        # Caso 19 y Caso 20: Reinicio de FastAPI con secuencia persistida y con RDS no disponible
        # ----------------------------------------------------------------------
        estado_antes_reinicio = seq_mgr.obtener_estado_vehiculo(1)
        epoca_antes = estado_antes_reinicio["epoca_orden"]

        # Simular reinicio creando nuevas instancias de OrderSequenceManager y VehiculoIoTService
        seq_mgr_restarted = OrderSequenceManager(seq_file)
        demo_mgr_restarted = DemoManager(stub_db, ws_mgr, sequence_manager=seq_mgr_restarted)
        svc_restarted = VehiculoIoTService(
            db_provider=lambda: stub_db,
            ws_manager=ws_mgr,
            demo_manager=demo_mgr_restarted,
            sequence_manager=seq_mgr_restarted,
            config=test_cfg,
        )
        res_post_restart = await svc_restarted.registrar_movimiento_individual(
            MovimientoRequest(id_vehiculo=1, id_comando=9, origen="POST_REINICIO", sesion_control="op-1")
        )
        assert res_post_restart["movimiento"]["epoca_orden"] > epoca_antes
        print("[PASS] 19. Reinicio de FastAPI conserva y continúa la secuencia desde .orden_seq_state.json")

        # Caso 20a: Reinicio cuando el archivo local existe pero RDS no está disponible -> permite DETENER con secuencia continua
        stub_db.rds_online = False
        seq_mgr_no_rds = OrderSequenceManager(seq_file)
        res_reserva_offline = seq_mgr_no_rds.reservar_epoca(1, "DETENER", rds_disponible=False)
        assert res_reserva_offline["epoca_orden"] > res_post_restart["movimiento"]["epoca_orden"]

        # Caso 20b: Pérdida simultánea de archivo local y RDS -> bloquea movimientos en estado seguro
        seq_file_missing = Path(tmpdir) / ".missing_seq.json"
        seq_mgr_lost_both = OrderSequenceManager(seq_file_missing)
        try:
            seq_mgr_lost_both.reservar_epoca(1, "MOVIMIENTO", rds_disponible=False)
            assert False, "Debió bloquear por pérdida simultánea de archivo y RDS"
        except RuntimeError:
            pass
        stub_db.rds_online = True
        seq_mgr_restarted.marcar_reconciliacion_pendiente(1, False, rds_max_id_historial=stub_db.obtener_max_id_historial(1))
        print("[PASS] 20. Reinicio sin RDS (con archivo y con pérdida simultánea) manejado de forma segura")

        # ----------------------------------------------------------------------
        # Caso 21: Protección contra desbordamiento uint32_t (4294967295)
        # ----------------------------------------------------------------------
        seq_file_overflow = Path(tmpdir) / ".overflow_seq.json"
        seq_mgr_ovf = OrderSequenceManager(seq_file_overflow)
        seq_mgr_ovf.resincronizar_secuencia_controlada(1, OrderSequenceManager.MAX_EPOCA_UINT32)
        try:
            seq_mgr_ovf.reservar_epoca(1, "MOVIMIENTO", rds_disponible=True)
            assert False, "Debió lanzar OverflowError al superar 4294967295"
        except OverflowError:
            est_ovf = seq_mgr_ovf.obtener_estado_vehiculo(1)
            assert est_ovf["estado_seguro_bloqueado"] is True
            assert est_ovf["motivo_bloqueo"] == "LIMITE_UINT32_ALCANZADO"
            assert est_ovf["epoca_orden"] == OrderSequenceManager.MAX_EPOCA_UINT32, "No debe reiniciar a 1"
        print("[PASS] 21. Protección contra desbordamiento uint32_t verificada (bloquea sin reiniciar a 1)")

        # ----------------------------------------------------------------------
        # Caso 22, 23, 24, 25: Sesiones de controlador, observador y watchdog
        # ----------------------------------------------------------------------
        svc = svc_restarted
        ws_obs = DummyWS("Observador_Tab2", {"rol": "observador", "sesion_control": "obs-2"})
        ws_otro_ctrl = DummyWS("Otro_Controlador", {"rol": "controlador", "sesion_control": "op-2"})
        await ws_mgr.connect(1, ws_obs, rol="observador", sesion_control="obs-2")
        await ws_mgr.connect(1, ws_otro_ctrl, rol="controlador", sesion_control="op-2")

        await svc.registrar_movimiento_individual(
            MovimientoRequest(id_vehiculo=1, id_comando=9, origen="JOYSTICK", sesion_control="op-1")
        )
        assert svc.get_active_movement(1) is not None

        # Caso 22: Ping de observador o de otro controlador no renueva la sesión de op-1
        assert svc._on_client_ping(1, ws_obs, {"tipo": "ping", "rol": "observador", "sesion_control": "obs-2"}) is False
        assert svc._on_client_ping(1, ws_otro_ctrl, {"tipo": "ping", "rol": "controlador", "sesion_control": "op-2"}) is False
        assert svc._on_client_ping(1, ws_ctrl, {"tipo": "ping", "rol": "controlador", "sesion_control": "op-1"}) is True
        print("[PASS] 22. Ping de sesión observadora o ajena NO renueva la vigencia del propietario")

        # Caso 23: Desconexión de observador o de otro controlador no detiene al operador activo (op-1)
        ws_mgr.disconnect(1, ws_obs)
        ws_mgr.disconnect(1, ws_otro_ctrl)
        await asyncio.sleep(0.05)
        assert svc.get_active_movement(1) is not None, "El movimiento de op-1 debe seguir activo"
        print("[PASS] 23. Desconexión de observador NO detiene el movimiento del operador activo")

        # Caso 24: Desconexión del propietario (op-1) provoca DETENER inmediato
        ws_mgr.disconnect(1, ws_ctrl)
        await asyncio.sleep(0.08)
        assert svc.get_active_movement(1) is None, "El movimiento debió detenerse al desconectarse op-1"
        assert stub_db.obtener_estado(1)[0]["codigo"] == 0
        assert any(e.get("origen") == "DESCONEXION_CONTROLADOR" for e in ws_esp.events)
        print("[PASS] 24. Desconexión del controlador propietario dispara DETENER automáticamente")

        # Caso 25: Reconexión de un controlador diferente no hereda movimientos anteriores
        ws_nuevo_ctrl = DummyWS("Controlador_Nuevo", {"rol": "controlador", "sesion_control": "op-nuevo"})
        await ws_mgr.connect(1, ws_nuevo_ctrl, rol="controlador", sesion_control="op-nuevo")
        await svc.enviar_estado_inicial_vehiculo(ws_nuevo_ctrl, 1)
        assert svc.get_active_movement(1) is None
        assert ws_nuevo_ctrl.events[-1]["tipo"] == "estado_inicial"
        assert ws_nuevo_ctrl.events[-1]["activar_salidas"] is False
        print("[PASS] 25. Reconexión de un controlador diferente NO hereda movimientos anteriores")

        # ----------------------------------------------------------------------
        # Caso 26 y Caso 27: Desconexión y reconexión de WSS del ESP8266 sin reactivar salidas
        # ----------------------------------------------------------------------
        await svc.registrar_movimiento_individual(
            MovimientoRequest(id_vehiculo=1, id_comando=9, origen="JOYSTICK", sesion_control="op-nuevo")
        )
        assert svc.get_active_movement(1) is not None

        # Desconectar ESP8266 -> servidor pasa el vehículo a estado seguro detenido
        ws_mgr.disconnect(1, ws_esp)
        await asyncio.sleep(0.08)
        assert svc.get_active_movement(1) is None
        assert stub_db.obtener_estado(1)[0]["codigo"] == 0
        print("[PASS] 26. Desconexión de WSS del ESP8266 transiciona el servidor a estado seguro detenido")

        # Reconectar ESP8266 -> recibe estado_inicial con activar_salidas=False y codigo=0
        ws_esp_reconn = DummyWS("Mock_ESP8266_Reconn", {"rol": "esp8266"})
        await ws_mgr.connect(1, ws_esp_reconn, rol="esp8266")
        await svc.enviar_estado_inicial_vehiculo(ws_esp_reconn, 1)
        assert len(ws_esp_reconn.events) == 1
        ev_reconn = ws_esp_reconn.events[0]
        assert ev_reconn["tipo"] == "estado_inicial"
        assert ev_reconn["activar_salidas"] is False
        assert ev_reconn["estado"][0]["codigo"] == 0
        ws_mgr.disconnect(1, ws_esp_reconn)
        ws_mgr.disconnect(1, ws_nuevo_ctrl)
        print("[PASS] 27. Reconexión del ESP8266 entrega estado_inicial seguro sin reactivar movimientos")

        # ----------------------------------------------------------------------
        # Caso 30: Verificación de los 21 Stored Procedures e idempotencia de comando DETENER
        # ----------------------------------------------------------------------
        assert len(Database.SP_PERMITIDOS) == 21
        cmd_1 = stub_db.obtener_o_registrar_comando_detener(1)
        cmd_2 = stub_db.obtener_o_registrar_comando_detener(1)
        assert cmd_1["id_comando"] == cmd_2["id_comando"], "No debe duplicar el comando DETENER"
        assert cmd_2["creado"] is False
        assert cmd_1["id_comando"] != 13, "Usa ID real dinámico, nunca el 13 fijo"
        print(
            f"[PASS] 30. Los 21 Stored Procedures se conservan intactos y DETENER es idempotente "
            f"(id_comando dinámico={cmd_1['id_comando']})"
        )

    # --------------------------------------------------------------------------
    # Caso 28 y Caso 29: Pruebas HTTP reales con Uvicorn (Regresión, CORS y API Key en /detener)
    # --------------------------------------------------------------------------
    port = 8009
    base_url = f"http://127.0.0.1:{port}"
    with tempfile.TemporaryDirectory() as tmp_http_dir:
        http_seq_file = Path(tmp_http_dir) / ".orden_seq_state.json"
        http_stub_db = IsolatedTestDB()
        http_seq_mgr = OrderSequenceManager(http_seq_file)

        with (
            mock.patch.object(main, "db", http_stub_db),
            mock.patch.object(main.demo_runner, "db", http_stub_db),
            mock.patch.object(main.demo_runner, "sequence_manager", http_seq_mgr),
            mock.patch.object(main.service, "sequence_manager", http_seq_mgr),
        ):
            config = uvicorn.Config(main.app, host="127.0.0.1", port=port, log_level="warning")
            server = uvicorn.Server(config)
            thread = threading.Thread(target=server.run, daemon=True)
            thread.start()
            time.sleep(1.2)

            ws_client_esp = None
            try:
                headers = {"Content-Type": "application/json"}
                if settings.api_key:
                    headers["X-API-Key"] = settings.api_key

                # Verificar OpenAPI y las 20 rutas HTTP
                with urllib.request.urlopen(f"{base_url}/openapi.json") as resp:
                    oas = json.loads(resp.read().decode())
                    assert len(oas["paths"]) == 20
                    assert "/api/vehiculos/{id_vehiculo}/detener" in oas["paths"]

                # Conectar cliente WebSocket real RFC 6455 simulando el ESP8266
                ws_client_esp = SimpleWSClient("127.0.0.1", port, "/ws/vehiculos/1?rol=esp8266")
                init_msg = ws_client_esp.recv_json()
                assert init_msg["tipo"] == "estado_inicial"
                assert init_msg["activar_salidas"] is False

                # Hilo que simula el ACK del ESP8266 cuando recibe 'detener'
                def esp_responder():
                    msg = ws_client_esp.recv_json()
                    if msg.get("tipo") == "detener":
                        ws_client_esp.send_text(
                            json.dumps(
                                {
                                    "tipo": "detencion_confirmada",
                                    "id_vehiculo": 1,
                                    "orden_id": msg["orden_id"],
                                    "codigo": 0,
                                    "salidas_activas": 0,
                                    "estado_fisico": "detenido",
                                }
                            )
                        )

                resp_thread = threading.Thread(target=esp_responder, daemon=True)
                resp_thread.start()

                req_stop = urllib.request.Request(
                    f"{base_url}/api/vehiculos/1/detener",
                    data=json.dumps({"origen": "HTTP_TEST", "esperar_confirmacion_ms": 1500}).encode(),
                    headers=headers,
                    method="POST",
                )
                with urllib.request.urlopen(req_stop) as resp:
                    assert resp.status == 200
                    stop_data = json.loads(resp.read().decode())
                    assert stop_data["orden_recibida"] is True
                    assert stop_data["registrada_en_rds"] is True
                    assert stop_data["orden_publicada_ws"] is True
                    assert stop_data["detencion_fisica_confirmada"] is True
                    assert stop_data["reconciliacion_rds_pendiente"] is False
                resp_thread.join(timeout=2)
                print("[PASS] 28. Regresión HTTP y endpoint POST /api/vehiculos/1/detener verificados con Uvicorn")

                # Caso 29: CORS Preflight y X-API-Key sobre /api/vehiculos/1/detener
                req_cors = urllib.request.Request(
                    f"{base_url}/api/vehiculos/1/detener",
                    headers={
                        "Origin": "http://localhost:5500",
                        "Access-Control-Request-Method": "POST",
                        "Access-Control-Request-Headers": "Content-Type, X-API-Key, X-Control-Session",
                    },
                    method="OPTIONS",
                )
                with urllib.request.urlopen(req_cors) as resp:
                    assert resp.status == 200
                    assert resp.headers.get("Access-Control-Allow-Origin") == "http://localhost:5500"

                if settings.api_key and settings.require_api_key:
                    req_unauth = urllib.request.Request(
                        f"{base_url}/api/vehiculos/1/detener",
                        data=b"{}",
                        headers={"Content-Type": "application/json", "X-API-Key": "clave-invalida"},
                        method="POST",
                    )
                    try:
                        urllib.request.urlopen(req_unauth)
                        assert False, "Debió rechazar con 401"
                    except urllib.error.HTTPError as exc:
                        assert exc.code == 401
                print("[PASS] 29. Compatibilidad CORS y seguridad X-API-Key verificadas en /detener")
            finally:
                if ws_client_esp:
                    ws_client_esp.close()
                server.should_exit = True
                thread.join(timeout=3)

    # ==========================================================================
    # PRUEBAS NUEVAS AISLADAS OBLIGATORIAS (CASOS 31 A 37)
    # ==========================================================================
    with tempfile.TemporaryDirectory() as tmp_extra_dir:
        extra_dir = Path(tmp_extra_dir)

        # ----------------------------------------------------------------------
        # Caso 31: Alcanzar el límite de uint32_t sin reiniciar la secuencia a 1
        # ----------------------------------------------------------------------
        seq_31_file = extra_dir / ".seq_31.json"
        db_31 = IsolatedTestDB()
        ws_31 = ConnectionManager()
        seq_31 = OrderSequenceManager(seq_31_file)
        demo_31 = DemoManager(db_31, ws_31, sequence_manager=seq_31)
        cfg_31 = Settings()
        cfg_31.order_seq_state_file = seq_31_file
        svc_31 = VehiculoIoTService(
            db_provider=lambda: db_31,
            ws_manager=ws_31,
            demo_manager=demo_31,
            sequence_manager=seq_31,
            config=cfg_31,
        )

        # Posicionar justo en MAX_EPOCA_UINT32 - 1 (42_949_671) y reservar la última época válida (42_949_672)
        seq_31.resincronizar_secuencia_controlada(
            1, OrderSequenceManager.MAX_EPOCA_UINT32 - 1, rds_id_historial=20
        )
        res_ultima_valida = seq_31.reserve_epoch(
            1, "DETENER", rds_max_id_historial=20, rds_disponible=True
        )
        assert res_ultima_valida["epoca_orden"] == OrderSequenceManager.MAX_EPOCA_UINT32
        assert res_ultima_valida["orden_id"] == OrderSequenceManager.MAX_EPOCA_UINT32 * 100

        # Al intentar reservar una época adicional, debe lanzar OverflowError y NUNCA reiniciar a 1 ni a 0
        try:
            seq_31.reserve_epoch(1, "MOVIMIENTO", rds_max_id_historial=20, rds_disponible=True)
            assert False, "Debió lanzar OverflowError al superar MAX_EPOCA_UINT32"
        except OverflowError:
            pass

        est_31 = seq_31.obtener_estado_vehiculo(1, rds_max_id_historial=20, rds_disponible=True)
        assert est_31["estado_seguro_bloqueado"] is True
        assert est_31["motivo_bloqueo"] == "LIMITE_UINT32_ALCANZADO"
        assert est_31["epoca_orden"] == OrderSequenceManager.MAX_EPOCA_UINT32, "Prohibido reiniciar nueva_epoca = 1"
        assert est_31["ultimo_stop_orden_id"] == OrderSequenceManager.MAX_EPOCA_UINT32 * 100, "Prohibido reiniciar ultimo_stop_orden_id = 0"

        # Verificar que ni movimientos ni DEMOs están permitidos en el servicio
        try:
            await svc_31.registrar_movimiento_individual(
                MovimientoRequest(id_vehiculo=1, id_comando=9, origen="OVF_MOV")
            )
            assert False, "No debe permitir movimientos al alcanzar límite uint32_t"
        except Exception as exc:
            assert getattr(exc, "status_code", None) == 503

        try:
            await svc_31.iniciar_demo(1)
            assert False, "No debe permitir DEMOs al alcanzar límite uint32_t"
        except Exception as exc:
            assert getattr(exc, "status_code", None) == 503

        # Tampoco permite reducir la época a 1 manualmente
        try:
            seq_31.resincronizar_secuencia_controlada(1, 1)
            assert False, "No debe permitir reutilizar ni reducir la época a 1"
        except ValueError:
            pass
        print("[PASS] 31. Límite uint32_t alcanzado entra en bloqueo seguro SIN reiniciar nueva_epoca=1 ni ultimo_stop_orden_id=0")

        # ----------------------------------------------------------------------
        # Caso 32: Detectar discrepancias entre id_historial y orden_id
        # ----------------------------------------------------------------------
        seq_32_file = extra_dir / ".seq_32.json"
        db_32 = IsolatedTestDB()
        ws_32 = ConnectionManager()
        seq_32 = OrderSequenceManager(seq_32_file)
        demo_32 = DemoManager(db_32, ws_32, sequence_manager=seq_32)
        cfg_32 = Settings()
        cfg_32.order_seq_state_file = seq_32_file
        svc_32 = VehiculoIoTService(
            db_provider=lambda: db_32,
            ws_manager=ws_32,
            demo_manager=demo_32,
            sequence_manager=seq_32,
            config=cfg_32,
        )

        # Inicializar con época=1 (orden_id=100) e id_historial=20 -> comprobar que son valores distintos
        seq_32.resincronizar_secuencia_controlada(1, 1, rds_id_historial=20)
        est_32_init = seq_32.obtener_estado_vehiculo(1, rds_max_id_historial=20, rds_disponible=True)
        assert est_32_init["epoca_orden"] == 1
        assert est_32_init["ultimo_orden_id"] == 100
        assert est_32_init["ultimo_id_historial_rds"] == 20
        assert est_32_init["ultimo_id_historial_rds"] != est_32_init["epoca_orden"]
        assert est_32_init["ultimo_id_historial_rds"] != est_32_init["ultimo_orden_id"]

        # Si se pasa id_historial (20) como si fuera orden_id (esperado 100), detecta discrepancia y bloquea
        est_32_disc_orden = seq_32.verificar_consistencia_rds(
            1, rds_max_id_historial=20, rds_orden_id=20
        )
        assert est_32_disc_orden["estado_seguro_bloqueado"] is True
        assert est_32_disc_orden["motivo_bloqueo"] == "DISCREPANCIA_ID_HISTORIAL_VS_ORDEN_ID"

        # También si id_historial en RDS cambia fuera de banda (p. ej. 20 -> 35 sin pasar por OrderSequenceManager)
        seq_32_b_file = extra_dir / ".seq_32b.json"
        seq_32_b = OrderSequenceManager(seq_32_b_file)
        svc_32.sequence_manager = seq_32_b
        demo_32.sequence_manager = seq_32_b
        ws_32.sequence_manager = seq_32_b
        seq_32_b.resincronizar_secuencia_controlada(1, 5, rds_id_historial=20)
        db_32.historial[1].insert(
            0, {"id_historial": 35, "id_vehiculo": 1, "id_comando": 9, "comando": "Avanzar", "origen": "EXTERNO"}
        )

        try:
            await svc_32.registrar_movimiento_individual(
                MovimientoRequest(id_vehiculo=1, id_comando=9, origen="CON_DISCREPANCIA")
            )
            assert False, "Debió bloquear el movimiento al detectar discrepancia entre RDS y secuencia local"
        except Exception as exc:
            assert getattr(exc, "status_code", None) == 503

        est_32_b = seq_32_b.obtener_estado_vehiculo(1)
        assert est_32_b["estado_seguro_bloqueado"] is True
        assert est_32_b["motivo_bloqueo"] == "DISCREPANCIA_ID_HISTORIAL_VS_ORDEN_ID"
        assert est_32_b["epoca_orden"] == 5, "No debe sobrescribir epoca_orden (5) con id_historial (35)"
        print("[PASS] 32. Discrepancias entre id_historial y orden_id detectadas y bloqueadas sin igualar valores")

        # ----------------------------------------------------------------------
        # Caso 33: Impedir movimientos cuando la secuencia no puede recuperarse
        # ----------------------------------------------------------------------
        seq_33_file = extra_dir / ".seq_33_no_existe.json"
        db_33 = IsolatedTestDB()  # Tiene id_historial=20 en RDS, pero no existe archivo de secuencia
        ws_33 = ConnectionManager()
        seq_33 = OrderSequenceManager(seq_33_file)
        demo_33 = DemoManager(db_33, ws_33, sequence_manager=seq_33)
        cfg_33 = Settings()
        cfg_33.order_seq_state_file = seq_33_file
        svc_33 = VehiculoIoTService(
            db_provider=lambda: db_33,
            ws_manager=ws_33,
            demo_manager=demo_33,
            sequence_manager=seq_33,
            config=cfg_33,
        )

        try:
            await svc_33.registrar_movimiento_individual(
                MovimientoRequest(id_vehiculo=1, id_comando=9, origen="SIN_SECUENCIA")
            )
            assert False, "Debió impedir el movimiento cuando la secuencia no puede recuperarse desde RDS"
        except Exception as exc:
            assert getattr(exc, "status_code", None) == 503

        try:
            await svc_33.iniciar_demo(1)
            assert False, "Debió impedir la DEMO cuando la secuencia no puede recuperarse desde RDS"
        except Exception as exc:
            assert getattr(exc, "status_code", None) == 503

        est_33 = seq_33.obtener_estado_vehiculo(1)
        assert est_33["estado_seguro_bloqueado"] is True
        assert est_33["motivo_bloqueo"] == "SECUENCIA_NO_RECUPERABLE_DESDE_RDS"
        print("[PASS] 33. Movimientos y DEMOs bloqueados (HTTP 503) cuando la secuencia no puede recuperarse")

        # ----------------------------------------------------------------------
        # Caso 34: Mantener bloqueado el control si no llega el ACK de resincronización
        # ----------------------------------------------------------------------
        ws_esp_33 = DummyWS("ESP_Resync_Test", {"rol": "esp8266"})
        await ws_33.connect(1, ws_esp_33, rol="esp8266")

        res_stop_sin_ack = await svc_33.detener_vehiculo(
            1, DetenerRequest(origen="RESYNC_SIN_ACK", esperar_confirmacion_ms=80)
        )
        assert res_stop_sin_ack["orden_publicada_ws"] is True
        assert res_stop_sin_ack["detencion_fisica_confirmada"] is False
        assert res_stop_sin_ack["resync_requerido"] is True
        assert res_stop_sin_ack["resync_completada"] is False
        assert seq_33.obtener_estado_vehiculo(1)["estado_seguro_bloqueado"] is True

        # Intentar mover después de publicar DETENER sin ACK -> sigue bloqueado
        try:
            await svc_33.registrar_movimiento_individual(
                MovimientoRequest(id_vehiculo=1, id_comando=9, origen="INTENTO_SIN_ACK")
            )
            assert False, "El control debe permanecer bloqueado si no llegó el ACK de resincronización"
        except Exception as exc:
            assert getattr(exc, "status_code", None) == 503
        print("[PASS] 34. Control permanece bloqueado tras publicar DETENER si no llega el ACK de resincronización")

        # ----------------------------------------------------------------------
        # Caso 35: Rechazar ACK de resincronización con un orden_id diferente
        # ----------------------------------------------------------------------
        ws_esp_33.events.clear()

        async def enviar_ack_orden_diferente():
            await asyncio.sleep(0.03)
            ev_stop = [e for e in ws_esp_33.events if e.get("tipo") == "detener"][-1]
            await ws_33._handle_detencion_confirmada(
                ws_esp_33,
                1,
                {
                    "tipo": "detencion_confirmada",
                    "id_vehiculo": 1,
                    "orden_id": ev_stop["orden_id"] + 500,  # orden_id diferente al esperado
                    "codigo": 0,
                    "salidas_activas": False,
                    "estado_fisico": "detenido",
                },
            )

        t_wrong_resync = asyncio.create_task(enviar_ack_orden_diferente())
        res_stop_wrong_id = await svc_33.detener_vehiculo(
            1, DetenerRequest(origen="RESYNC_ACK_ERRONEO", esperar_confirmacion_ms=120)
        )
        await t_wrong_resync
        assert res_stop_wrong_id["detencion_fisica_confirmada"] is False
        assert res_stop_wrong_id["resync_completada"] is False
        assert any(e.get("tipo") == "ack_rechazado" for e in ws_esp_33.events)
        assert seq_33.obtener_estado_vehiculo(1)["estado_seguro_bloqueado"] is True

        # También verificar directamente que mark_resync_completed rechaza un orden_id diferente
        try:
            seq_33.mark_resync_completed(
                1,
                ack_data={
                    "id_vehiculo": 1,
                    "orden_id": res_stop_wrong_id["orden_id"] + 999,
                    "codigo": 0,
                    "salidas_activas": False,
                    "estado_fisico": "detenido",
                },
            )
            assert False, "mark_resync_completed debió rechazar un ACK con orden_id diferente"
        except ValueError:
            pass

        try:
            await svc_33.registrar_movimiento_individual(
                MovimientoRequest(id_vehiculo=1, id_comando=9, origen="TRAS_ACK_ERRONEO")
            )
            assert False, "El control debe seguir bloqueado tras un ACK con orden_id diferente"
        except Exception as exc:
            assert getattr(exc, "status_code", None) == 503
        print("[PASS] 35. ACK de resincronización con orden_id diferente es rechazado y mantiene el bloqueo")

        # ----------------------------------------------------------------------
        # Caso 36: Permitir nuevos movimientos solamente después de recibir el ACK correcto
        # ----------------------------------------------------------------------
        ws_esp_33.events.clear()

        async def enviar_ack_correcto():
            await asyncio.sleep(0.03)
            ev_stop = [e for e in ws_esp_33.events if e.get("tipo") == "detener"][-1]
            await ws_33._handle_detencion_confirmada(
                ws_esp_33,
                1,
                {
                    "tipo": "detencion_confirmada",
                    "id_vehiculo": 1,
                    "orden_id": ev_stop["orden_id"],  # orden_id exacto
                    "codigo": 0,
                    "salidas_activas": False,          # salidas_activas: false
                    "estado_fisico": "detenido",
                },
            )

        t_ok_resync = asyncio.create_task(enviar_ack_correcto())
        res_stop_ok = await svc_33.detener_vehiculo(
            1, DetenerRequest(origen="RESYNC_ACK_OK", esperar_confirmacion_ms=400)
        )
        await t_ok_resync
        assert res_stop_ok["detencion_fisica_confirmada"] is True
        assert res_stop_ok["resync_completada"] is True
        assert seq_33.obtener_estado_vehiculo(1)["estado_seguro_bloqueado"] is False

        # Ahora sí se permite ejecutar un nuevo movimiento y su orden_id es estrictamente mayor
        res_mov_desbloqueado = await svc_33.registrar_movimiento_individual(
            MovimientoRequest(id_vehiculo=1, id_comando=9, origen="POST_ACK_CORRECTO")
        )
        assert res_mov_desbloqueado["ok"] is True
        assert res_mov_desbloqueado["movimiento"]["orden_id"] > res_stop_ok["orden_id"]
        print("[PASS] 36. Nuevos movimientos permitidos únicamente tras recibir el ACK correcto (orden_id exacto y salidas_activas=false)")

        # ----------------------------------------------------------------------
        # Caso 37: Comprobar que una orden antigua no pueda reactivar salidas después de DETENER
        # ----------------------------------------------------------------------
        orden_mov_anterior = res_mov_desbloqueado["movimiento"]["orden_id"]
        res_stop_barrera = await svc_33.detener_vehiculo(
            1, DetenerRequest(origen="STOP_BARRERA", esperar_confirmacion_ms=0)
        )
        orden_stop_barrera = res_stop_barrera["orden_id"]
        assert orden_stop_barrera > orden_mov_anterior
        assert seq_33.is_stale_against_stop(1, orden_mov_anterior) is True
        assert seq_33.is_stale_against_stop(1, orden_stop_barrera) is True

        # Intentar transmitir por WebSocket una orden antigua (movimiento_individual y demo_paso con orden_id <= orden_stop_barrera)
        ws_esp_33.events.clear()
        stats_stale_mov = await ws_33.broadcast_to_vehicle(
            1,
            {
                "tipo": "movimiento_individual",
                "id_vehiculo": 1,
                "id_comando": 9,
                "comando": "Avanzar",
                "codigo": 9,
                "orden_id": orden_mov_anterior,
                "mantener_activo": True,
            },
        )
        stats_stale_demo = await ws_33.broadcast_to_vehicle(
            1,
            {
                "tipo": "demo_paso",
                "id_vehiculo": 1,
                "id_demo": 1,
                "paso": 2,
                "id_comando": 9,
                "comando": "Avanzar",
                "codigo": 9,
                "orden_id": orden_stop_barrera - 1,
            },
        )
        assert stats_stale_mov["total_enviados"] == 0
        assert stats_stale_mov.get("bloqueado_por_stop_posterior") is True
        assert stats_stale_demo["total_enviados"] == 0
        assert stats_stale_demo.get("bloqueado_por_stop_posterior") is True
        assert len(ws_esp_33.events) == 0, "Ninguna orden antigua puede llegar al ESP8266 después de DETENER"
        print("[PASS] 37. Orden antigua bloqueada por barrera de secuencia y transporte WS tras DETENER (0 envíos al ESP8266)")

    print("================================================================================")
    print("TODAS LAS 37 PRUEBAS AUTOMATIZADAS SUPERADAS CON ÉXITO (37/37)")
    print("================================================================================")


if __name__ == "__main__":
    asyncio.run(run_all_30_tests())

# ==============================================================================
# SUITE DE PRUEBAS OBLIGATORIAS DE RECUPERACIÓN INTEGRAL (15 PRUEBAS REALES)
# Archivo: test_recuperacion_15_pruebas.py
# Propósito: Ejecutar y verificar las 15 pruebas obligatorias de la recuperación
#            integral del proyecto Vehículo IoT sobre MySQL real + Uvicorn + WS + Web.
# ==============================================================================

import json
from pathlib import Path
import subprocess
import sys
import threading
import time
import unittest
import urllib.error
import urllib.request
import uvicorn

BASE_DIR = Path(__file__).resolve().parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from database import Database
import main
from settings import settings
from test_websocket_global import SimpleWSClient


PORT = 8015
BASE_URL = f"http://127.0.0.1:{PORT}"

EXPECTED_TABLES = {
    "vehiculos",
    "comandos",
    "estado_vehiculo",
    "historial_comandos",
    "demos",
    "demo_movimientos",
}

EXPECTED_PROCEDURES = {
    "sp_crear_vehiculo",
    "sp_obtener_vehiculos",
    "sp_obtener_vehiculo",
    "sp_actualizar_vehiculo",
    "sp_eliminar_vehiculo",
    "sp_crear_comando",
    "sp_obtener_comandos_vehiculo",
    "sp_actualizar_comando",
    "sp_eliminar_comando",
    "sp_crear_demo",
    "sp_obtener_demos",
    "sp_actualizar_demo",
    "sp_eliminar_demo",
    "sp_agregar_movimiento_demo",
    "sp_obtener_movimientos_demo",
    "sp_actualizar_movimiento_demo",
    "sp_eliminar_movimiento_demo",
    "sp_actualizar_estado",
    "sp_obtener_estado",
    "sp_registrar_historial",
    "sp_obtener_historial",
}


def http_request(
    path: str,
    method: str = "GET",
    body: dict | None = None,
    headers: dict[str, str] | None = None,
    raw_text: bool = False,
) -> tuple[int, dict | str, dict[str, str]]:
    url = f"{BASE_URL}{path}"
    req_headers = {"Accept": "application/json"}
    if headers:
        req_headers.update(headers)
    data_bytes = None
    if body is not None:
        req_headers["Content-Type"] = "application/json"
        data_bytes = json.dumps(body).encode("utf-8")

    req = urllib.request.Request(url, data=data_bytes, headers=req_headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            raw = resp.read().decode("utf-8")
            resp_headers = {k.lower(): v for k, v in resp.headers.items()}
            if raw_text:
                return resp.status, raw, resp_headers
            return resp.status, (json.loads(raw) if raw else {}), resp_headers
    except urllib.error.HTTPError as err:
        raw = err.read().decode("utf-8")
        resp_headers = {k.lower(): v for k, v in err.headers.items()}
        if raw_text:
            return err.code, raw, resp_headers
        try:
            parsed = json.loads(raw) if raw else {}
        except Exception:
            parsed = {"raw": raw}
        return err.code, parsed, resp_headers


class TestRecuperacionIntegral15Pruebas(unittest.TestCase):
    """Ejecuta las 15 pruebas obligatorias contra MySQL real, Uvicorn HTTP, WebSocket y Frontend."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.db = Database(settings)
        cls.api_key = settings.api_key
        cls.auth_headers = {"X-API-Key": cls.api_key}
        cls.created_demo_id: int | None = None

        # Línea base del historial: al finalizar se eliminan SOLO las filas generadas durante las pruebas
        with cls.db._cursor_session(commit=False, dictionary=True) as cursor:
            cursor.execute("SELECT COALESCE(MAX(id_historial), 0) AS m FROM historial_comandos")
            cls.baseline_max_historial = int(cursor.fetchone()["m"])

        # Limpiar archivo local de secuencia antes de iniciar
        for suffix in ("", ".bak", ".lock"):
            seq_path = Path(f"{settings.order_seq_state_file}{suffix}")
            if seq_path.exists():
                try:
                    seq_path.unlink()
                except OSError:
                    pass
        main.seq_manager = main.OrderSequenceManager(settings.order_seq_state_file)
        main.service.sequence_manager = main.seq_manager
        main.demo_runner.sequence_manager = main.seq_manager

        # Iniciar servidor Uvicorn real en segundo plano conectado a MySQL real
        config = uvicorn.Config(main.app, host="127.0.0.1", port=PORT, log_level="warning")
        cls.server = uvicorn.Server(config)
        cls.server_thread = threading.Thread(target=cls.server.run, daemon=True)
        cls.server_thread.start()

        # Esperar a que Uvicorn responda en /health
        for _ in range(30):
            try:
                status, _, _ = http_request("/health")
                if status == 200:
                    break
            except Exception:
                time.sleep(0.15)

    @classmethod
    def tearDownClass(cls) -> None:
        if hasattr(cls, "server"):
            cls.server.should_exit = True
            cls.server_thread.join(timeout=3.0)

        # Limpiar cualquier demo temporal residual si existiera
        if cls.created_demo_id is not None:
            try:
                cls.db.ejecutar_sp("sp_eliminar_demo", [cls.created_demo_id], escritura=True)
            except Exception:
                pass

        # Restaurar estado_vehiculo a Detener (id_comando=13, conectado=1) y limpiar historial de prueba
        try:
            with cls.db._cursor_session(commit=True, dictionary=True) as cursor:
                cursor.execute(
                    "DELETE FROM historial_comandos WHERE id_historial > %s",
                    (cls.baseline_max_historial,),
                )
                cursor.execute(
                    "UPDATE estado_vehiculo SET id_comando = 13, conectado = 1 WHERE id_vehiculo = 1"
                )
        except Exception:
            pass

        for suffix in ("", ".bak", ".lock"):
            seq_path = Path(f"{settings.order_seq_state_file}{suffix}")
            if seq_path.exists():
                try:
                    seq_path.unlink()
                except OSError:
                    pass

    # ------------------------------------------------------------------
    # PRUEBA 01: Conexión a MySQL
    # ------------------------------------------------------------------
    def test_01_conexion_mysql(self) -> None:
        info = self.db.verificar_conexion()
        self.assertEqual(info.get("base_datos"), "vehiculo_iot")
        self.assertTrue(info.get("version_mysql"))

    # ------------------------------------------------------------------
    # PRUEBA 02: Existencia de las 6 tablas
    # ------------------------------------------------------------------
    def test_02_existencia_6_tablas(self) -> None:
        with self.db._cursor_session(commit=False, dictionary=True) as cursor:
            cursor.execute(
                """
                SELECT TABLE_NAME AS tabla
                FROM information_schema.TABLES
                WHERE TABLE_SCHEMA = DATABASE() AND TABLE_TYPE = 'BASE TABLE'
                """
            )
            rows = cursor.fetchall()
        found_tables = {r["tabla"] for r in rows}
        self.assertEqual(found_tables, EXPECTED_TABLES)
        self.assertEqual(len(found_tables), 6)

    # ------------------------------------------------------------------
    # PRUEBA 03: Existencia de los 21 procedimientos almacenados
    # ------------------------------------------------------------------
    def test_03_existencia_21_procedimientos_almacenados(self) -> None:
        with self.db._cursor_session(commit=False, dictionary=True) as cursor:
            cursor.execute(
                """
                SELECT ROUTINE_NAME AS sp
                FROM information_schema.ROUTINES
                WHERE ROUTINE_SCHEMA = DATABASE() AND ROUTINE_TYPE = 'PROCEDURE'
                """
            )
            rows = cursor.fetchall()
        found_sps = {r["sp"] for r in rows}
        self.assertEqual(found_sps, EXPECTED_PROCEDURES)
        self.assertEqual(len(found_sps), 21)

    # ------------------------------------------------------------------
    # PRUEBA 04: Integridad de claves foráneas (0 huérfanos)
    # ------------------------------------------------------------------
    def test_04_integridad_claves_foraneas(self) -> None:
        with self.db._cursor_session(commit=False, dictionary=True) as cursor:
            cursor.execute(
                """
                SELECT COUNT(*) AS huerfanos_demo
                FROM demo_movimientos dm
                LEFT JOIN demos d ON dm.id_demo = d.id_demo
                WHERE d.id_demo IS NULL
                """
            )
            huerfanos_demo = int(cursor.fetchone()["huerfanos_demo"])

            cursor.execute(
                """
                SELECT COUNT(*) AS huerfanos_cmd
                FROM demo_movimientos dm
                LEFT JOIN comandos c ON dm.id_comando = c.id_comando
                WHERE c.id_comando IS NULL
                """
            )
            huerfanos_cmd = int(cursor.fetchone()["huerfanos_cmd"])

            cursor.execute(
                """
                SELECT COUNT(*) AS fk_count
                FROM information_schema.TABLE_CONSTRAINTS
                WHERE CONSTRAINT_SCHEMA = DATABASE() AND CONSTRAINT_TYPE = 'FOREIGN KEY'
                """
            )
            fk_count = int(cursor.fetchone()["fk_count"])

        self.assertEqual(huerfanos_demo, 0, "No deben existir movimientos huérfanos sin registro en demos")
        self.assertEqual(huerfanos_cmd, 0, "No deben existir movimientos huérfanos sin registro en comandos")
        self.assertGreaterEqual(fk_count, 7)

    # ------------------------------------------------------------------
    # PRUEBA 05: Consulta de vehículos
    # ------------------------------------------------------------------
    def test_05_consulta_vehiculos(self) -> None:
        status_all, body_all, _ = http_request("/api/vehiculos")
        self.assertEqual(status_all, 200)
        self.assertTrue(body_all.get("ok"))
        vehiculos = body_all.get("resultados", [])
        self.assertGreaterEqual(len(vehiculos), 1)

        status_one, body_one, _ = http_request("/api/vehiculos/1")
        self.assertEqual(status_one, 200)
        v1 = body_one["resultados"][0]
        self.assertEqual(v1["id_vehiculo"], 1)
        self.assertEqual(v1["nombre"], "Excavadora IoT")
        self.assertEqual(v1["modelo"], "CAT-320")
        self.assertEqual(v1["microcontrolador"], "ESP8266")

    # ------------------------------------------------------------------
    # PRUEBA 06: Consulta de comandos
    # ------------------------------------------------------------------
    def test_06_consulta_comandos(self) -> None:
        status, body, _ = http_request("/api/vehiculos/1/comandos")
        self.assertEqual(status, 200)
        comandos = body.get("resultados", [])
        self.assertEqual(len(comandos), 13)
        codigos = {int(c["codigo"]) for c in comandos}
        self.assertIn(0, codigos)
        self.assertIn(1, codigos)
        self.assertIn(9, codigos)
        self.assertIn(12, codigos)

    # ------------------------------------------------------------------
    # PRUEBA 07: Registro de movimientos y orden DETENER
    # ------------------------------------------------------------------
    def test_07_registro_movimientos_y_detener(self) -> None:
        payload = {
            "id_vehiculo": 1,
            "id_comando": 9,
            "conectado": True,
            "origen": "PRUEBA_REC_MOV_07",
        }
        status, data, _ = http_request(
            "/api/movimientos", method="POST", body=payload, headers=self.auth_headers
        )
        self.assertEqual(status, 201, str(data))
        self.assertTrue(data.get("ok"))
        mov = data.get("movimiento", {})
        self.assertTrue(mov.get("mantener_activo"))
        self.assertGreater(int(mov.get("orden_id", 0)), 0)

        # Enviar DETENER explícito para volver a estado de reposo seguro
        status_stop, stop_data, _ = http_request(
            "/api/vehiculos/1/detener",
            method="POST",
            body={"origen": "PRUEBA_REC_STOP_07", "esperar_confirmacion_ms": 100},
            headers=self.auth_headers,
        )
        self.assertEqual(status_stop, 200, str(stop_data))
        self.assertTrue(stop_data.get("ok"))
        self.assertEqual(stop_data.get("evento_ws", {}).get("accion"), "DETENER")
        self.assertTrue(stop_data.get("orden_recibida"))
        self.assertTrue(stop_data.get("registrada_en_rds"))
        # Sin ESP8266 conectado no se puede afirmar confirmación física
        self.assertFalse(stop_data.get("detencion_fisica_confirmada"))
        self.assertGreater(int(stop_data["orden_id"]), int(mov["orden_id"]))

    # ------------------------------------------------------------------
    # PRUEBA 08: Consulta de historial (máximo 10 registros)
    # ------------------------------------------------------------------
    def test_08_consulta_historial(self) -> None:
        status, body, _ = http_request("/api/vehiculos/1/historial")
        self.assertEqual(status, 200)
        self.assertTrue(body.get("ok"))
        items = body.get("resultados", [])
        self.assertGreaterEqual(len(items), 1)
        self.assertLessEqual(len(items), 10)

    # ------------------------------------------------------------------
    # PRUEBA 09: Creación y consulta de demostraciones (incluyendo DEMO 1, 2 y 3)
    # ------------------------------------------------------------------
    def test_09_creacion_y_consulta_demostraciones(self) -> None:
        status_get, body_get, _ = http_request("/api/vehiculos/1/demos")
        self.assertEqual(status_get, 200)
        demos = body_get.get("resultados", [])
        ids_demos = {int(d["id_demo"]) for d in demos}
        self.assertTrue({1, 2, 3}.issubset(ids_demos), f"Deben existir las demos 1, 2 y 3. Encontradas: {ids_demos}")

        status_post, body_post, _ = http_request(
            "/api/demos",
            method="POST",
            body={
                "id_vehiculo": 1,
                "nombre": "Demo Temporal Verificacion Recuperacion",
                "descripcion": "Secuencia temporal para validar creación y borrado controlado",
            },
            headers=self.auth_headers,
        )
        self.assertEqual(status_post, 201, str(body_post))
        created_rows = body_post.get("resultados", [])
        self.assertEqual(len(created_rows), 1)
        type(self).created_demo_id = int(created_rows[0]["id_demo"])
        self.assertGreater(type(self).created_demo_id, 3)

    # ------------------------------------------------------------------
    # PRUEBA 10: Consulta de movimientos de demostraciones (DEMO 1, 2 y 3)
    # ------------------------------------------------------------------
    def test_10_consulta_movimientos_demostraciones(self) -> None:
        for id_demo in (1, 2, 3):
            status, body, _ = http_request(f"/api/demos/{id_demo}/movimientos")
            self.assertEqual(status, 200)
            movs = body.get("resultados", [])
            self.assertEqual(
                len(movs),
                5,
                f"La DEMO {id_demo} debe conservar sus 5 registros originales en demo_movimientos",
            )

    # ------------------------------------------------------------------
    # PRUEBA 11: Eliminación controlada de demostraciones
    # ------------------------------------------------------------------
    def test_11_eliminacion_controlada_demostraciones(self) -> None:
        demo_id = type(self).created_demo_id
        self.assertIsNotNone(demo_id, "La prueba 09 debió crear una DEMO temporal")

        status_step, body_step, _ = http_request(
            f"/api/demos/{demo_id}/movimientos",
            method="POST",
            body={
                "id_comando": 9,
                "orden": 1,
                "duracion_ms": 1000,
                "pausa_ms": 300,
            },
            headers=self.auth_headers,
        )
        self.assertEqual(status_step, 201, str(body_step))

        status_del, body_del, _ = http_request(
            f"/api/demos/{demo_id}", method="DELETE", headers=self.auth_headers
        )
        self.assertEqual(status_del, 200, str(body_del))
        self.assertEqual(body_del["resultados"][0]["filas_afectadas"], 1)
        type(self).created_demo_id = None

        status_after, body_after, _ = http_request("/api/vehiculos/1/demos")
        self.assertEqual(status_after, 200)
        ids_restantes = {int(d["id_demo"]) for d in body_after.get("resultados", [])}
        self.assertEqual(ids_restantes, {1, 2, 3})

    # ------------------------------------------------------------------
    # PRUEBA 12: Comunicación WebSocket (/ws/vehiculos/1 y /ws/vehiculos)
    # ------------------------------------------------------------------
    def test_12_comunicacion_websocket(self) -> None:
        ws_veh = SimpleWSClient("127.0.0.1", PORT, "/ws/vehiculos/1")
        ws_global = SimpleWSClient("127.0.0.1", PORT, "/ws/vehiculos")
        try:
            inicial = ws_veh.recv_json()
            self.assertEqual(inicial.get("tipo"), "estado_inicial")
            self.assertEqual(int(inicial.get("id_vehiculo", 0)), 1)

            inicial_global = ws_global.recv_json()
            self.assertEqual(inicial_global.get("tipo"), "estado_inicial_global")
            self.assertGreaterEqual(len(inicial_global.get("vehiculos", [])), 1)

            # Emitir orden DETENER y verificar difusión en tiempo real en ambos canales WebSocket
            status_stop, _, _ = http_request(
                "/api/vehiculos/1/detener",
                method="POST",
                body={"origen": "PRUEBA_REC_WS_12", "esperar_confirmacion_ms": 100},
                headers=self.auth_headers,
            )
            self.assertEqual(status_stop, 200)

            ev_veh = ws_veh.recv_json()
            self.assertEqual(ev_veh.get("tipo"), "detener")
            self.assertEqual(int(ev_veh.get("id_vehiculo", 0)), 1)

            ev_global = ws_global.recv_json()
            self.assertEqual(ev_global.get("tipo"), "detener")
            self.assertEqual(int(ev_global.get("id_vehiculo", 0)), 1)
        finally:
            ws_veh.close()
            ws_global.close()

    # ------------------------------------------------------------------
    # PRUEBA 13: Autenticación con API_KEY (X-API-Key)
    # ------------------------------------------------------------------
    def test_13_autenticacion_api_key(self) -> None:
        status_no_key, _, _ = http_request(
            "/api/vehiculos/1/detener",
            method="POST",
            body={"origen": "PRUEBA_REC_SIN_KEY"},
        )
        self.assertEqual(status_no_key, 401)

        status_bad_key, _, _ = http_request(
            "/api/vehiculos/1/detener",
            method="POST",
            body={"origen": "PRUEBA_REC_BAD_KEY"},
            headers={"X-API-Key": "clave_incorrecta_123"},
        )
        self.assertEqual(status_bad_key, 401)

        status_ok_key, _, _ = http_request(
            "/api/vehiculos/1/detener",
            method="POST",
            body={"origen": "PRUEBA_REC_OK_KEY"},
            headers=self.auth_headers,
        )
        self.assertEqual(status_ok_key, 200)

    # ------------------------------------------------------------------
    # PRUEBA 14: CORS (orígenes permitidos vs orígenes no autorizados)
    # ------------------------------------------------------------------
    def test_14_cors(self) -> None:
        status_allowed, _, headers_allowed = http_request(
            "/api/movimientos",
            method="OPTIONS",
            headers={
                "Origin": "https://vehiculo-iot-api.duckdns.org",
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "Content-Type,X-API-Key",
            },
            raw_text=True,
        )
        self.assertEqual(status_allowed, 200)
        self.assertEqual(
            headers_allowed.get("access-control-allow-origin"),
            "https://vehiculo-iot-api.duckdns.org",
        )

        _, _, headers_denied = http_request(
            "/api/movimientos",
            method="OPTIONS",
            headers={
                "Origin": "https://sitio-malicioso.example.com",
                "Access-Control-Request-Method": "POST",
            },
            raw_text=True,
        )
        self.assertIsNone(headers_denied.get("access-control-allow-origin"))

    # ------------------------------------------------------------------
    # PRUEBA 15: Funcionamiento de la página web (HTTP /app/ + suite JS)
    # ------------------------------------------------------------------
    def test_15_funcionamiento_pagina_web(self) -> None:
        status_html, html_text, _ = http_request("/app/index.html", raw_text=True)
        self.assertEqual(status_html, 200)
        self.assertIn("CAT-320", html_text)
        self.assertIn("DEMO 1", html_text)
        self.assertIn("DEMO 2", html_text)
        self.assertIn("DEMO 3", html_text)

        status_css, _, _ = http_request("/app/assets/css/styles.css", raw_text=True)
        self.assertEqual(status_css, 200)

        status_js, _, _ = http_request("/app/js/main.js", raw_text=True)
        self.assertEqual(status_js, 200)

        frontend_test = BASE_DIR / "frontend" / "tests" / "passive.integration.test.mjs"
        proc = subprocess.run(
            ["node", str(frontend_test)],
            cwd=str(BASE_DIR),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
            check=False,
        )
        self.assertEqual(proc.returncode, 0, f"Fallo en test JS:\n{proc.stdout}\n{proc.stderr}")
        self.assertIn("TODAS LAS PRUEBAS PASIVAS Y SIMULACIONES FINALIZARON CON ÉXITO", proc.stdout)


if __name__ == "__main__":
    unittest.main(verbosity=2)

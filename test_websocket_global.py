# ==============================================================================
# MÓDULO DE PRUEBAS: test_websocket_global.py
# PROPÓSITO: Probar de extremo a extremo (con servidor Uvicorn real y clientes
#            WebSocket RFC 6455 sin dependencias externas) tanto la ruta
#            individual /ws/vehiculos/{id} como la nueva ruta global /ws/vehiculos
#            recibiendo eventos reales de distintos vehículos (movimientos,
#            cambios de estado y DEMO) sin duplicar notificaciones.
# ==============================================================================

import base64
import json
import os
import socket
import struct
import threading
import time
import unittest.mock as mock
import urllib.request
import uvicorn

import main

PORT = 8006
BASE_URL = f"http://127.0.0.1:{PORT}"


class SimpleWSClient:
    """Cliente WebSocket RFC 6455 ligero usando solo biblioteca estándar de Python."""

    def __init__(self, host: str, port: int, path: str):
        self.sock = socket.create_connection((host, port), timeout=5)
        key = base64.b64encode(os.urandom(16)).decode()
        req = (
            f"GET {path} HTTP/1.1\r\n"
            f"Host: {host}:{port}\r\n"
            f"Upgrade: websocket\r\n"
            f"Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            f"Sec-WebSocket-Version: 13\r\n\r\n"
        )
        self.sock.sendall(req.encode())
        resp = b""
        while b"\r\n\r\n" not in resp:
            chunk = self.sock.recv(1024)
            if not chunk:
                break
            resp += chunk
        header_end = resp.index(b"\r\n\r\n") + 4
        header_text = resp[:header_end].decode(errors="ignore")
        assert "101 Switching Protocols" in header_text, f"Handshake falló en {path}: {header_text}"
        self.buffer = resp[header_end:]

    def _read_exact(self, n: int) -> bytes:
        while len(self.buffer) < n:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise ConnectionError("Conexión WebSocket cerrada")
            self.buffer += chunk
        out, self.buffer = self.buffer[:n], self.buffer[n:]
        return out

    def recv_json(self) -> dict:
        b1, b2 = self._read_exact(2)
        length = b2 & 0x7F
        if length == 126:
            length = struct.unpack("!H", self._read_exact(2))[0]
        elif length == 127:
            length = struct.unpack("!Q", self._read_exact(8))[0]
        payload = self._read_exact(length)
        return json.loads(payload.decode("utf-8"))

    def send_text(self, text: str):
        data = text.encode("utf-8")
        mask = os.urandom(4)
        header = bytearray([0x81])
        n = len(data)
        if n < 126:
            header.append(0x80 | n)
        elif n < 65536:
            header.append(0x80 | 126)
            header.extend(struct.pack("!H", n))
        else:
            header.append(0x80 | 127)
            header.extend(struct.pack("!Q", n))
        header.extend(mask)
        masked = bytearray(b ^ mask[i % 4] for i, b in enumerate(data))
        self.sock.sendall(header + masked)

    def close(self):
        try:
            self.sock.close()
        except Exception:
            pass


def build_test_db_layer(real_db):
    """Utiliza una capa de datos aislada en memoria para probar las rutas HTTP
    y WebSocket localmente sin escribir movimientos de prueba en producción."""
    print("[INFO] Ejecutando pruebas con capa de datos aislada (sin escribir en producción RDS).")

    vehiculos_data = [
        {"id_vehiculo": 6, "nombre": "Excavadora IoT", "modelo": "CAT-320", "activo": 1},
        {"id_vehiculo": 7, "nombre": "Vehiculo_Prueba_SP", "modelo": "ESP32-Bot", "activo": 1},
    ]
    estados_data = {
        6: [{"id_vehiculo": 6, "vehiculo": "Excavadora IoT", "id_comando": 38, "comando": "AVANZAR", "codigo": 1, "conectado": 1}],
        7: [{"id_vehiculo": 7, "vehiculo": "Vehiculo_Prueba_SP", "id_comando": 42, "comando": "DETENER", "codigo": 0, "conectado": 1}],
    }
    historial_data = {
        6: [{"id_historial": 10, "id_vehiculo": 6, "id_comando": 38, "comando": "AVANZAR", "origen": "API"}],
        7: [{"id_historial": 11, "id_vehiculo": 7, "id_comando": 42, "comando": "DETENER", "origen": "API"}],
    }
    movimientos_demo_5 = [
        {"id_demo_movimiento": 1, "id_demo": 5, "id_comando": 38, "comando": "AVANZAR", "codigo": 1, "orden": 1, "duracion_ms": 60, "pausa_ms": 30},
        {"id_demo_movimiento": 2, "id_demo": 5, "id_comando": 39, "comando": "RETROCEDER", "codigo": 2, "orden": 2, "duracion_ms": 60, "pausa_ms": 30},
    ]

    class LocalStubDB:
        def __init__(self):
            self._next_hist_id = 12

        def obtener_estado(self, id_vehiculo: int):
            return estados_data.get(id_vehiculo, [])

        def obtener_estados_globales(self):
            return [
                {
                    "id_vehiculo": v["id_vehiculo"],
                    "nombre": v["nombre"],
                    "modelo": v["modelo"],
                    "activo": v["activo"],
                    "estado": estados_data.get(v["id_vehiculo"], []),
                }
                for v in vehiculos_data
            ]

        def obtener_historial(self, id_vehiculo: int):
            return historial_data.get(id_vehiculo, [])[:10]

        def registrar_movimiento(self, id_vehiculo: int, id_comando: int, origen: str):
            if id_vehiculo not in (6, 7):
                raise LookupError("El vehículo no existe.")
            catalogo_cmd = {
                38: ("AVANZAR", 1),
                39: ("RETROCEDER", 2),
                42: ("DETENER", 0),
            }
            nombre_cmd, codigo_cmd = catalogo_cmd.get(id_comando, (f"CMD_{id_comando}", id_comando))
            self._next_hist_id += 1
            hist_id = self._next_hist_id
            estados_data[id_vehiculo] = [
                {
                    "id_vehiculo": id_vehiculo,
                    "id_comando": id_comando,
                    "comando": nombre_cmd,
                    "codigo": codigo_cmd,
                    "conectado": 1,
                }
            ]
            return {
                "transaccion": "confirmada",
                "conectado": True,
                "id_comando": id_comando,
                "comando": nombre_cmd,
                "codigo": codigo_cmd,
                "id_historial": hist_id,
            }

        def registrar_detencion(self, id_vehiculo: int, origen: str = "API"):
            if id_vehiculo not in (6, 7):
                raise LookupError("El vehículo no existe.")
            self._next_hist_id += 1
            hist_id = self._next_hist_id
            estados_data[id_vehiculo] = [
                {
                    "id_vehiculo": id_vehiculo,
                    "id_comando": 42,
                    "comando": "Detener",
                    "codigo": 0,
                    "conectado": 1,
                }
            ]
            return {
                "transaccion": "confirmada",
                "conectado": True,
                "id_comando": 42,
                "comando": "Detener",
                "codigo": 0,
                "id_historial": hist_id,
                "comando_creado": False,
            }

        def registrar_apagado_demo(self, id_vehiculo: int):
            if id_vehiculo not in (6, 7):
                raise LookupError("El vehículo no existe.")
            estados_data[id_vehiculo] = [
                {
                    "id_vehiculo": id_vehiculo,
                    "id_comando": 42,
                    "comando": "Detener",
                    "codigo": 0,
                    "conectado": 1,
                }
            ]
            return {
                "transaccion": "confirmada",
                "id_vehiculo": id_vehiculo,
                "id_comando": 42,
                "comando": "Detener",
                "codigo": 0,
            }

        def obtener_info_demo(self, id_demo: int):
            if id_demo == 5:
                return {"id_demo": 5, "id_vehiculo": 6, "nombre": "Prueba de movimientos IoT", "activo": 1}
            return None

        def obtener_vehiculo_activo(self, id_vehiculo: int):
            for v in vehiculos_data:
                if v["id_vehiculo"] == id_vehiculo:
                    return v
            return None

        def obtener_movimientos_demo(self, id_demo: int):
            return list(movimientos_demo_5) if id_demo == 5 else []

        def actualizar_estado_paso_demo(self, id_vehiculo: int, id_comando: int):
            return {"transaccion": "confirmada", "id_vehiculo": id_vehiculo, "id_comando": id_comando}

        def ejecutar_sp(self, nombre, parametros=None, escritura=False):
            if nombre == "sp_obtener_vehiculos":
                return {"ok": True, "procedimiento": nombre, "resultados": vehiculos_data}
            if nombre == "sp_obtener_estado":
                return {"ok": True, "procedimiento": nombre, "resultados": estados_data.get(parametros[0], [])}
            if nombre == "sp_obtener_historial":
                return {"ok": True, "procedimiento": nombre, "resultados": historial_data.get(parametros[0], [])}
            return {"ok": True, "procedimiento": nombre, "resultados": []}

    return LocalStubDB()


def http_json(method: str, path: str, body: dict | None = None, headers: dict | None = None):
    hdrs = {"Content-Type": "application/json", **(headers or {})}
    data = json.dumps(body).encode("utf-8") if body is not None else b""
    req = urllib.request.Request(f"{BASE_URL}{path}", data=data, headers=hdrs, method=method)
    with urllib.request.urlopen(req, timeout=5) as resp:
        return resp.status, json.loads(resp.read().decode("utf-8"))


def run_websocket_tests():
    print("=== INICIANDO PRUEBAS AUTOMATIZADAS DE WEBSOCKET INDIVIDUAL Y GLOBAL ===")
    stub_db = build_test_db_layer(main.db)
    headers = {"x-api-key": main.settings.api_key} if main.settings.api_key else {}

    ctx_db = mock.patch.object(main, "db", stub_db) if stub_db else mock.nullcontext()
    ctx_demo_db = mock.patch.object(main.demo_runner, "db", stub_db) if stub_db else mock.nullcontext()

    with ctx_db, ctx_demo_db:
        config = uvicorn.Config(main.app, host="127.0.0.1", port=PORT, log_level="warning")
        server = uvicorn.Server(config)
        thread = threading.Thread(target=server.run, daemon=True)
        thread.start()
        time.sleep(1.5)

        ws_global = None
        ws_veh6 = None
        ws_veh7 = None
        try:
            # 1. Conectar las rutas WebSocket (/ws, /ws/vehiculos y /ws/vehiculos/{id}) y verificar estado inicial
            ws_short = SimpleWSClient("127.0.0.1", PORT, "/ws")
            init_short = ws_short.recv_json()
            assert init_short["tipo"] == "estado_inicial_global"
            ws_short.close()

            ws_global = SimpleWSClient("127.0.0.1", PORT, "/ws/vehiculos")
            init_global = ws_global.recv_json()
            assert init_global["tipo"] == "estado_inicial_global"
            ids_globales = [v["id_vehiculo"] for v in init_global["vehiculos"]]
            assert 6 in ids_globales and 7 in ids_globales
            print(f"[PASS] 1. /ws y /ws/vehiculos entregaron 'estado_inicial_global' con vehículos {ids_globales}")

            ws_veh6 = SimpleWSClient("127.0.0.1", PORT, "/ws/vehiculos/6")
            init_v6 = ws_veh6.recv_json()
            assert init_v6["tipo"] == "estado_inicial" and init_v6["id_vehiculo"] == 6
            print("[PASS] 2. /ws/vehiculos/6 entregó 'estado_inicial' para id_vehiculo=6")

            ws_veh7 = SimpleWSClient("127.0.0.1", PORT, "/ws/vehiculos/7")
            init_v7 = ws_veh7.recv_json()
            assert init_v7["tipo"] == "estado_inicial" and init_v7["id_vehiculo"] == 7
            print("[PASS] 3. /ws/vehiculos/7 entregó 'estado_inicial' para id_vehiculo=7")

            # 2. Enviar POST /api/movimientos para vehículo 6
            status, _ = http_json(
                "POST",
                "/api/movimientos",
                {"id_vehiculo": 6, "id_comando": 38, "origen": "MANUAL"},
                headers,
            )
            assert status == 201
            ev_v6 = ws_veh6.recv_json()
            ev_global_1 = ws_global.recv_json()
            assert ev_v6["tipo"] == "movimiento_individual" and ev_v6["id_vehiculo"] == 6
            assert ev_v6.get("comando") is not None and ev_v6.get("codigo") is not None
            assert ev_global_1["tipo"] == "movimiento_individual" and ev_global_1["id_vehiculo"] == 6
            assert ev_global_1.get("comando") is not None and ev_global_1.get("codigo") is not None
            print(f"[PASS] 4. Movimiento del vehículo 6 recibido en /ws/vehiculos/6 y en /ws/vehiculos con comando='{ev_global_1.get('comando')}', codigo={ev_global_1.get('codigo')}")

            # 3. Enviar POST /api/movimientos para vehículo 7
            status, _ = http_json(
                "POST",
                "/api/movimientos",
                {"id_vehiculo": 7, "id_comando": 42, "origen": "TestGlobal"},
                headers,
            )
            assert status == 201
            ev_v7 = ws_veh7.recv_json()
            ev_global_2 = ws_global.recv_json()
            assert ev_v7["tipo"] == "movimiento_individual" and ev_v7["id_vehiculo"] == 7
            assert ev_global_2["tipo"] == "movimiento_individual" and ev_global_2["id_vehiculo"] == 7
            print("[PASS] 5. Movimiento del vehículo 7 recibido en /ws/vehiculos/7 y en /ws/vehiculos (sin mezclarse con vehículo 6)")

            # 4. Enviar PUT /api/vehiculos/6/estado
            status, _ = http_json(
                "PUT",
                "/api/vehiculos/6/estado",
                {"id_comando": 38, "conectado": True},
                headers,
            )
            assert status == 200
            ev_est_v6 = ws_veh6.recv_json()
            ev_est_global = ws_global.recv_json()
            assert ev_est_v6["tipo"] == "estado_actualizado" and ev_est_v6["id_vehiculo"] == 6
            assert ev_est_global["tipo"] == "estado_actualizado" and ev_est_global["id_vehiculo"] == 6
            print("[PASS] 6. Cambio de estado (PUT /api/vehiculos/6/estado) recibido en canal individual y global")

            # 5. Ejecutar DEMO en vehículo 6 y comprobar todos los eventos posteriores en tiempo real
            status, _ = http_json("POST", "/api/demos/5/ejecutar", None, headers)
            assert status == 202

            tipos_global = []
            tipos_v6 = []
            while True:
                msg_g = ws_global.recv_json()
                assert msg_g["id_vehiculo"] == 6
                tipos_global.append(msg_g["tipo"])
                if msg_g["tipo"] == "detener" and msg_g.get("origen") in ("DEMO_FIN", "DEMO_CANCELADA"):
                    break

            while True:
                msg_6 = ws_veh6.recv_json()
                assert msg_6["id_vehiculo"] == 6
                tipos_v6.append(msg_6["tipo"])
                if msg_6["tipo"] == "detener" and msg_6.get("origen") in ("DEMO_FIN", "DEMO_CANCELADA"):
                    break

            assert tipos_global[0] == "demo_inicio"
            assert "demo_paso" in tipos_global
            assert "demo_fin" in tipos_global
            assert tipos_global[-1] == "detener"
            assert tipos_global == tipos_v6, "Secuencia global e individual deben coincidir sin duplicados"
            print(f"[PASS] 7. Secuencia DEMO completa recibida en tiempo real en /ws/vehiculos y /ws/vehiculos/6: {tipos_global}")

            # 6. Verificar ping/pong y que /ws/vehiculos/7 no haya recibido eventos del vehículo 6
            ws_global.send_text(json.dumps({"tipo": "ping"}))
            pong_global = ws_global.recv_json()
            assert pong_global["tipo"] == "pong" and pong_global["canal"] == "global"

            ws_veh7.send_text(json.dumps({"tipo": "ping"}))
            pong_v7 = ws_veh7.recv_json()
            assert pong_v7["tipo"] == "pong" and pong_v7["id_vehiculo"] == 7
            print("[PASS] 8. Ping/Pong verificado y confirmado que /ws/vehiculos/7 no recibió eventos del vehículo 6")

        finally:
            for ws in (ws_global, ws_veh6, ws_veh7):
                if ws:
                    ws.close()
            server.should_exit = True
            thread.join(timeout=3)

    print("=== TODAS LAS PRUEBAS (8/8) DE WEBSOCKET GLOBAL E INDIVIDUAL SUPERADAS ===")


if __name__ == "__main__":
    run_websocket_tests()

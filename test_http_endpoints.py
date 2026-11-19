import json
import threading
import time
import unittest.mock as mock
import urllib.error
import urllib.request
import uvicorn

import main
from settings import settings
from test_websocket_global import SimpleWSClient, build_test_db_layer

PORT = 8005
BASE_URL = f"http://127.0.0.1:{PORT}"


def test_http():
    print(f"=== PROBANDO SERVIDOR UVICORN EN {BASE_URL} ===")
    stub_db = build_test_db_layer(main.db)
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
            # 1. Health check
            req = urllib.request.Request(f"{BASE_URL}/health")
            with urllib.request.urlopen(req) as resp:
                data = json.loads(resp.read().decode())
                assert data.get("ok") is True
                print("[PASS] GET /health -> 200 OK:", data)

            # 1b. Swagger UI /docs
            req_docs = urllib.request.Request(f"{BASE_URL}/docs")
            with urllib.request.urlopen(req_docs) as resp:
                html = resp.read().decode()
                assert resp.status == 200
                assert "swagger-ui" in html
                assert "/static/swagger-custom.css" in html
                print("[PASS] GET /docs -> 200 OK (Swagger UI nativo cargado correctamente)")

            # 1c. OpenAPI Schema /openapi.json
            req_oas = urllib.request.Request(f"{BASE_URL}/openapi.json")
            with urllib.request.urlopen(req_oas) as resp:
                oas_data = json.loads(resp.read().decode())
                assert "paths" in oas_data
                assert len(oas_data["paths"]) == 20
                assert "/api/vehiculos/{id_vehiculo}/detener" in oas_data["paths"]
                print("[PASS] GET /openapi.json -> 200 OK (20 rutas HTTP registradas, incluyendo /detener)")

            # 1d. Static CSS /static/swagger-custom.css
            req_css = urllib.request.Request(f"{BASE_URL}/static/swagger-custom.css")
            with urllib.request.urlopen(req_css) as resp:
                css_data = resp.read().decode()
                assert resp.status == 200
                assert "@import" in css_data
                assert "#0B1220" in css_data
                print("[PASS] GET /static/swagger-custom.css -> 200 OK (CSS cargado correctamente)")

            # 2. GET vehiculos
            req = urllib.request.Request(f"{BASE_URL}/api/vehiculos")
            with urllib.request.urlopen(req) as resp:
                data = json.loads(resp.read().decode())
                assert data.get("ok") is True
                print("[PASS] GET /api/vehiculos -> 200 OK:", len(data.get("resultados", [])), "vehiculos encontrados")

            # 3. GET historial
            req = urllib.request.Request(f"{BASE_URL}/api/vehiculos/6/historial")
            with urllib.request.urlopen(req) as resp:
                data = json.loads(resp.read().decode())
                assert len(data.get("resultados", [])) <= 10
                print("[PASS] GET /api/vehiculos/6/historial -> 200 OK (<= 10 registros)")

            # 4. Conectar clientes WebSocket: global (/ws/vehiculos) e individuales (/ws/vehiculos/6 y /ws/vehiculos/7)
            ws_global = SimpleWSClient("127.0.0.1", PORT, "/ws/vehiculos")
            msg_init_global = ws_global.recv_json()
            assert msg_init_global.get("tipo") == "estado_inicial_global"
            assert isinstance(msg_init_global.get("vehiculos"), list) and len(msg_init_global["vehiculos"]) >= 2
            print(f"[PASS] WS /ws/vehiculos -> estado_inicial_global recibido ({len(msg_init_global['vehiculos'])} vehículos)")

            ws_veh6 = SimpleWSClient("127.0.0.1", PORT, "/ws/vehiculos/6")
            msg_init_6 = ws_veh6.recv_json()
            assert msg_init_6.get("tipo") == "estado_inicial" and msg_init_6.get("id_vehiculo") == 6
            print("[PASS] WS /ws/vehiculos/6 -> estado_inicial recibido para vehículo 6")

            ws_veh7 = SimpleWSClient("127.0.0.1", PORT, "/ws/vehiculos/7")
            msg_init_7 = ws_veh7.recv_json()
            assert msg_init_7.get("tipo") == "estado_inicial" and msg_init_7.get("id_vehiculo") == 7
            print("[PASS] WS /ws/vehiculos/7 -> estado_inicial recibido para vehículo 7")

            # 5. GET demo activa
            req = urllib.request.Request(f"{BASE_URL}/api/vehiculos/6/demo/activa")
            with urllib.request.urlopen(req) as resp:
                data = json.loads(resp.read().decode())
                assert "activa" in data
                print("[PASS] GET /api/vehiculos/6/demo/activa -> 200 OK:", data)

            # 6. POST ejecutar demo en vehículo 6 con x-api-key
            headers = {"x-api-key": settings.api_key} if settings.api_key else {}
            req = urllib.request.Request(f"{BASE_URL}/api/demos/5/ejecutar", data=b"", headers=headers, method="POST")
            with urllib.request.urlopen(req) as resp:
                data = json.loads(resp.read().decode())
                assert data.get("ok") is True
                print("[PASS] POST /api/demos/5/ejecutar -> 202 Accepted:", data)

            # 7. Intentar ejecutar 2da demo concurrente -> 409 Conflict
            req2 = urllib.request.Request(f"{BASE_URL}/api/demos/5/ejecutar", data=b"", headers=headers, method="POST")
            try:
                with urllib.request.urlopen(req2) as resp:
                    assert False, "Deberia haber lanzado HTTP 409"
            except urllib.error.HTTPError as exc:
                assert exc.code == 409
                print(f"[PASS] POST /api/demos/5/ejecutar concurrente -> 409 Conflict verificado ({exc.code})")

            # 8. POST cancelar demo en vehículo 6
            req_cancel = urllib.request.Request(f"{BASE_URL}/api/demos/5/cancelar", data=b"", headers=headers, method="POST")
            with urllib.request.urlopen(req_cancel) as resp:
                data = json.loads(resp.read().decode())
                assert data.get("ok") is True
                print("[PASS] POST /api/demos/5/cancelar -> 200 OK:", data)

            # 9. Verificar que tanto /ws/vehiculos/6 como /ws/vehiculos recibieron los eventos en vivo de la DEMO
            ev_veh6_inicio = ws_veh6.recv_json()
            ev_global_inicio = ws_global.recv_json()
            assert ev_veh6_inicio.get("tipo") == "demo_inicio" and ev_veh6_inicio.get("id_vehiculo") == 6
            assert ev_global_inicio.get("tipo") == "demo_inicio" and ev_global_inicio.get("id_vehiculo") == 6
            print("[PASS] Eventos posteriores en tiempo real recibidos en /ws/vehiculos/6 y en /ws/vehiculos (demo_inicio)")

            # 10. Verificar CORS con origen autorizado (GET y preflight OPTIONS)
            authorized_origin = "http://localhost:5500"
            req_cors_ok = urllib.request.Request(
                f"{BASE_URL}/api/vehiculos",
                headers={"Origin": authorized_origin},
                method="GET",
            )
            with urllib.request.urlopen(req_cors_ok) as resp:
                acao = resp.headers.get("Access-Control-Allow-Origin")
                assert acao == authorized_origin, f"Se esperaba ACAO={authorized_origin}, se recibió {acao}"
                print(f"[PASS] CORS GET desde origen autorizado ({authorized_origin}) -> Access-Control-Allow-Origin: {acao}")

            req_preflight_ok = urllib.request.Request(
                f"{BASE_URL}/api/movimientos",
                headers={
                    "Origin": authorized_origin,
                    "Access-Control-Request-Method": "POST",
                    "Access-Control-Request-Headers": "Content-Type, X-API-Key",
                },
                method="OPTIONS",
            )
            with urllib.request.urlopen(req_preflight_ok) as resp:
                assert resp.status == 200
                assert resp.headers.get("Access-Control-Allow-Origin") == authorized_origin
                allow_methods = resp.headers.get("Access-Control-Allow-Methods", "")
                assert "POST" in allow_methods
                print(f"[PASS] CORS Preflight OPTIONS desde origen autorizado -> 200 OK (Methods: {allow_methods})")

            # 11. Verificar rechazo de CORS desde origen NO autorizado
            unauthorized_origin = "https://sitio-no-autorizado.example.com"
            req_cors_bad = urllib.request.Request(
                f"{BASE_URL}/api/vehiculos",
                headers={"Origin": unauthorized_origin},
                method="GET",
            )
            with urllib.request.urlopen(req_cors_bad) as resp:
                acao_bad = resp.headers.get("Access-Control-Allow-Origin")
                assert acao_bad is None, f"Origen no autorizado NO debe recibir ACAO, recibió: {acao_bad}"
                print(f"[PASS] CORS GET desde origen NO autorizado ({unauthorized_origin}) -> Sin cabecera Access-Control-Allow-Origin")

            req_preflight_bad = urllib.request.Request(
                f"{BASE_URL}/api/movimientos",
                headers={
                    "Origin": unauthorized_origin,
                    "Access-Control-Request-Method": "POST",
                },
                method="OPTIONS",
            )
            try:
                with urllib.request.urlopen(req_preflight_bad) as resp:
                    assert resp.headers.get("Access-Control-Allow-Origin") is None
            except urllib.error.HTTPError as exc:
                assert exc.code == 400
                print(f"[PASS] CORS Preflight OPTIONS desde origen NO autorizado -> Rechazado con HTTP {exc.code}")

            # 12. Verificar protección X-API-Key en endpoints de escritura (401 sin clave / clave inválida)
            if settings.api_key and settings.require_api_key:
                req_no_key = urllib.request.Request(
                    f"{BASE_URL}/api/demos/5/ejecutar",
                    data=b"",
                    headers={"X-API-Key": "clave-incorrecta"},
                    method="POST",
                )
                try:
                    with urllib.request.urlopen(req_no_key) as resp:
                        assert False, "Debería haber rechazado con HTTP 401 sin X-API-Key válida"
                except urllib.error.HTTPError as exc:
                    assert exc.code == 401
                    print(f"[PASS] Seguridad X-API-Key activa -> Rechazado POST sin clave válida con HTTP {exc.code}")

            print("=== TODAS LAS PRUEBAS HTTP, CORS, X-API-KEY, WEBSOCKET GLOBAL E INDIVIDUAL COMPLETADAS CON EXITO ===")
        finally:
            for ws in (ws_global, ws_veh6, ws_veh7):
                if ws:
                    ws.close()
            server.should_exit = True
            thread.join(timeout=3)


if __name__ == "__main__":
    test_http()

import asyncio
import json
import urllib.request
import urllib.error
import subprocess
import time
import sys

from settings import settings

PORT = 8005
BASE_URL = f"http://127.0.0.1:{PORT}"

def test_http():
    print(f"=== PROBANDO SERVIDOR UVICORN EN {BASE_URL} ===")

    # Iniciar servidor en segundo plano
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "main:app", "--port", str(PORT), "--host", "127.0.0.1"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True
    )

    try:
        # Esperar a que arranque
        time.sleep(2.5)

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
            assert len(oas_data["paths"]) == 19
            print("[PASS] GET /openapi.json -> 200 OK (19 rutas registradas)")

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

        # 4. GET demo activa
        req = urllib.request.Request(f"{BASE_URL}/api/vehiculos/6/demo/activa")
        with urllib.request.urlopen(req) as resp:
            data = json.loads(resp.read().decode())
            assert "activa" in data
            print("[PASS] GET /api/vehiculos/6/demo/activa -> 200 OK:", data)

        # 5. POST ejecutar demo con x-api-key
        headers = {"x-api-key": settings.api_key} if settings.api_key else {}
        req = urllib.request.Request(f"{BASE_URL}/api/demos/5/ejecutar", data=b"", headers=headers, method="POST")
        with urllib.request.urlopen(req) as resp:
            data = json.loads(resp.read().decode())
            assert data.get("ok") is True
            print("[PASS] POST /api/demos/5/ejecutar -> 202 Accepted:", data)

        # 6. Intentar ejecutar 2da demo concurrente -> 409 Conflict
        req2 = urllib.request.Request(f"{BASE_URL}/api/demos/5/ejecutar", data=b"", headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req2) as resp:
                assert False, "Deberia haber lanzado HTTP 409"
        except urllib.error.HTTPError as exc:
            assert exc.code == 409
            print(f"[PASS] POST /api/demos/5/ejecutar concurrente -> 409 Conflict verificado ({exc.code})")

        # 7. POST cancelar demo
        req_cancel = urllib.request.Request(f"{BASE_URL}/api/demos/5/cancelar", data=b"", headers=headers, method="POST")
        with urllib.request.urlopen(req_cancel) as resp:
            data = json.loads(resp.read().decode())
            assert data.get("ok") is True
            print("[PASS] POST /api/demos/5/cancelar -> 200 OK:", data)

        print("=== TODAS LAS PRUEBAS HTTP Y CONCURRENCIA COMPLETADAS CON EXITO ===")
    finally:
        proc.terminate()
        proc.wait(timeout=5)

if __name__ == "__main__":
    test_http()

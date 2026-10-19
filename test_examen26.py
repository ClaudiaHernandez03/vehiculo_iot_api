import asyncio
import sys
from settings import settings
from database import Database
from websocket_manager import ConnectionManager
from demo_manager import DemoManager


async def run_all_checks():
    print("=== INICIANDO SUITE DE PRUEBAS DE EXAMEN 2.6 ===")
    db = Database()
    ws_mgr = ConnectionManager()
    demo_mgr = DemoManager(db, ws_mgr)

    # 1. Comprobar GET de vehiculos
    vehiculos = db.ejecutar_sp("sp_obtener_vehiculos")
    assert vehiculos.get("ok") is True, "sp_obtener_vehiculos falló"
    assert len(vehiculos.get("resultados", [])) > 0, "No se encontraron vehículos en RDS"
    id_vehiculo = vehiculos["resultados"][0]["id_vehiculo"]
    nombre_vehiculo = vehiculos["resultados"][0]["nombre"]
    print(f"[PASS] 1. sp_obtener_vehiculos: detectado vehículo id={id_vehiculo} ({nombre_vehiculo})")

    # 2. Comprobar GET de historial limitado a 10
    historial = db.obtener_historial(id_vehiculo)
    assert isinstance(historial, list), "Historial debe ser lista"
    assert len(historial) <= 10, "El historial debe contener como máximo 10 registros"
    historial_count_inicial = len(historial)
    print(f"[PASS] 2. sp_obtener_historial: {historial_count_inicial} registros (<= 10)")

    # 3. Comprobar GET de estado inicial
    estado = db.obtener_estado(id_vehiculo)
    assert len(estado) > 0, "Estado del vehículo no encontrado"
    cmd_actual = estado[0].get("comando")
    print(f"[PASS] 3. sp_obtener_estado: comando actual es '{cmd_actual}'")

    # 4. Comprobar dos clientes WebSocket simultáneos recibiendo broadcast
    class DummyWebSocket:
        def __init__(self, name):
            self.name = name
            self.events = []
            self.is_accepted = False

        async def accept(self):
            self.is_accepted = True

        async def send_json(self, data):
            self.events.append(data)

    ws1 = DummyWebSocket("Cliente 1")
    ws2 = DummyWebSocket("Cliente 2")
    await ws_mgr.connect(id_vehiculo, ws1)
    await ws_mgr.connect(id_vehiculo, ws2)

    test_event = {"tipo": "movimiento_individual", "id_vehiculo": id_vehiculo, "test": True}
    await ws_mgr.broadcast_to_vehicle(id_vehiculo, test_event)
    assert len(ws1.events) == 1 and ws1.events[0]["tipo"] == "movimiento_individual"
    assert len(ws2.events) == 1 and ws2.events[0]["tipo"] == "movimiento_individual"
    print("[PASS] 4. Dos clientes WebSocket simultáneos recibieron broadcast correctamente")

    # 5. Comprobar inicio de DEMO
    demos = db.ejecutar_sp("sp_obtener_demos", [id_vehiculo])["resultados"]
    assert len(demos) > 0, "No se encontraron demos registradas para este vehículo"
    id_demo = demos[0]["id_demo"]
    nombre_demo = demos[0]["nombre"]
    print(f"   Iniciando prueba de DEMO id={id_demo} ('{nombre_demo}') en vehículo {id_vehiculo}...")

    res_demo = await demo_mgr.start_demo(id_demo)
    assert res_demo.get("ok") is True, "Error al iniciar demo"
    assert demo_mgr.is_running(id_vehiculo) is True, "Demo debería estar activa"
    print(f"[PASS] 5. DEMO iniciada exitosamente ({res_demo.get('total_pasos')} pasos configurados)")

    # 6. Rechazo de segunda DEMO simultánea para el mismo vehículo (409 Conflict)
    try:
        await demo_mgr.start_demo(id_demo)
        assert False, "Debería haber rechazado la segunda DEMO simultánea"
    except RuntimeError as err:
        print(f"[PASS] 6. Rechazo de 2da DEMO simultánea verificado: '{err}'")

    # 7. Cancelación de DEMO y notificación por WebSocket
    cancel_res = await demo_mgr.cancel_demo(id_vehiculo)
    assert cancel_res.get("ok") is True, "Cancelación de DEMO falló"
    await asyncio.sleep(0.2)  # tiempo para que asyncio propague CancelledError
    assert demo_mgr.is_running(id_vehiculo) is False, "Demo debería estar detenida tras cancelación"

    eventos_cancel_ws1 = [e for e in ws1.events if e.get("tipo") == "demo_cancelada"]
    eventos_cancel_ws2 = [e for e in ws2.events if e.get("tipo") == "demo_cancelada"]
    assert len(eventos_cancel_ws1) > 0, "Cliente WS 1 no recibió evento demo_cancelada"
    assert len(eventos_cancel_ws2) > 0, "Cliente WS 2 no recibió evento demo_cancelada"
    print(f"[PASS] 7. DEMO cancelada limpiamente y notificada a ambos clientes WebSocket")

    # 8. Ausencia de registros en el historial individual durante la DEMO
    historial_final = db.obtener_historial(id_vehiculo)
    assert len(historial_final) == historial_count_inicial, (
        f"El historial no debe crecer por la DEMO. Inicial={historial_count_inicial}, Final={len(historial_final)}"
    )
    print(f"[PASS] 8. Verificado: El historial individual NO registró pasos de DEMO ({len(historial_final)} == {historial_count_inicial})")

    # Limpieza
    ws_mgr.disconnect(id_vehiculo, ws1)
    ws_mgr.disconnect(id_vehiculo, ws2)
    print("=== TODAS LAS PRUEBAS (8/8) COMPLETADAS CON ÉXITO ===")


if __name__ == "__main__":
    asyncio.run(run_all_checks())

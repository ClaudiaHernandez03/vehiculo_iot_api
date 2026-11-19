import asyncio
from database import Database
from websocket_manager import ConnectionManager
from demo_manager import DemoManager
from test_websocket_global import build_test_db_layer


async def run_all_checks():
    print("=== INICIANDO SUITE DE PRUEBAS DE EXAMEN 2.6 ===")
    real_db = Database()
    stub_db = build_test_db_layer(real_db)
    db = stub_db if stub_db is not None else real_db
    ws_mgr = ConnectionManager()
    demo_mgr = DemoManager(db, ws_mgr)

    # 1. Comprobar GET de vehiculos
    vehiculos = db.ejecutar_sp("sp_obtener_vehiculos")
    assert vehiculos.get("ok") is True, "sp_obtener_vehiculos falló"
    lista_vehiculos = vehiculos.get("resultados", [])
    assert len(lista_vehiculos) > 0, "No se encontraron vehículos"
    id_vehiculo = lista_vehiculos[0]["id_vehiculo"]
    nombre_vehiculo = lista_vehiculos[0]["nombre"]
    id_vehiculo_2 = lista_vehiculos[1]["id_vehiculo"] if len(lista_vehiculos) > 1 else id_vehiculo + 1
    print(f"[PASS] 1. sp_obtener_vehiculos: detectados vehículos id={id_vehiculo} ({nombre_vehiculo}) e id={id_vehiculo_2}")

    # 2. Comprobar GET de historial limitado a 10
    historial = db.obtener_historial(id_vehiculo)
    assert isinstance(historial, list), "Historial debe ser lista"
    assert len(historial) <= 10, "El historial debe contener como máximo 10 registros"
    historial_count_inicial = len(historial)
    print(f"[PASS] 2. sp_obtener_historial: {historial_count_inicial} registros (<= 10)")

    # 3. Comprobar GET de estado inicial individual y global
    estado = db.obtener_estado(id_vehiculo)
    assert len(estado) > 0, "Estado del vehículo no encontrado"
    cmd_actual = estado[0].get("comando")
    estados_globales = db.obtener_estados_globales()
    assert len(estados_globales) == len(lista_vehiculos), "Estados globales incompletos"
    print(f"[PASS] 3. sp_obtener_estado y obtener_estados_globales: comando actual='{cmd_actual}', {len(estados_globales)} vehículos")

    # 4. Comprobar clientes WebSocket individuales y canal global con múltiples vehículos
    class DummyWebSocket:
        def __init__(self, name):
            self.name = name
            self.events = []
            self.is_accepted = False

        async def accept(self):
            self.is_accepted = True

        async def send_json(self, data):
            self.events.append(data)

    ws1 = DummyWebSocket("Cliente Veh1 - A")
    ws2 = DummyWebSocket("Cliente Veh1 - B")
    ws_otro = DummyWebSocket("Cliente Veh2")
    ws_global = DummyWebSocket("Cliente Global /ws/vehiculos")

    await ws_mgr.connect(id_vehiculo, ws1)
    await ws_mgr.connect(id_vehiculo, ws2)
    await ws_mgr.connect(id_vehiculo_2, ws_otro)
    await ws_mgr.connect_global(ws_global)

    # Evento en vehículo 1
    ev_v1 = {"tipo": "movimiento_individual", "id_vehiculo": id_vehiculo, "id_comando": 38}
    await ws_mgr.broadcast_to_vehicle(id_vehiculo, ev_v1)

    # Evento en vehículo 2
    ev_v2 = {"tipo": "movimiento_individual", "id_vehiculo": id_vehiculo_2, "id_comando": 42}
    await ws_mgr.broadcast_to_vehicle(id_vehiculo_2, ev_v2)

    assert len(ws1.events) == 1 and ws1.events[0]["id_vehiculo"] == id_vehiculo
    assert len(ws2.events) == 1 and ws2.events[0]["id_vehiculo"] == id_vehiculo
    assert len(ws_otro.events) == 1 and ws_otro.events[0]["id_vehiculo"] == id_vehiculo_2
    assert len(ws_global.events) == 2, f"Global debía recibir 2 eventos (sin duplicados), recibió {len(ws_global.events)}"
    assert [e["id_vehiculo"] for e in ws_global.events] == [id_vehiculo, id_vehiculo_2]
    print("[PASS] 4. WebSocket individual (aislado por vehículo) y WebSocket global (multivehículo sin duplicados) verificados")

    # 5. Comprobar inicio de DEMO
    demos_res = db.ejecutar_sp("sp_obtener_demos", [id_vehiculo])["resultados"]
    id_demo = demos_res[0]["id_demo"] if demos_res else 5
    nombre_demo = demos_res[0]["nombre"] if demos_res else "Prueba de movimientos IoT"
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

    # 7. Cancelación de DEMO y notificación tanto a clientes individuales como al global
    cancel_res = await demo_mgr.cancel_demo(id_vehiculo)
    assert cancel_res.get("ok") is True, "Cancelación de DEMO falló"
    await asyncio.sleep(0.2)
    assert demo_mgr.is_running(id_vehiculo) is False, "Demo debería estar detenida tras cancelación"

    eventos_cancel_ws1 = [e for e in ws1.events if e.get("tipo") == "demo_cancelada"]
    eventos_cancel_ws2 = [e for e in ws2.events if e.get("tipo") == "demo_cancelada"]
    eventos_cancel_global = [e for e in ws_global.events if e.get("tipo") == "demo_cancelada" and e.get("id_vehiculo") == id_vehiculo]
    assert len(eventos_cancel_ws1) == 1, "Cliente WS 1 debe recibir exactamente 1 demo_cancelada"
    assert len(eventos_cancel_ws2) == 1, "Cliente WS 2 debe recibir exactamente 1 demo_cancelada"
    assert len(eventos_cancel_global) == 1, "Cliente WS Global debe recibir exactamente 1 demo_cancelada"
    assert len(ws_otro.events) == 1, "Cliente de otro vehículo NO debe recibir eventos de la DEMO del vehículo 1"
    print("[PASS] 7. DEMO cancelada limpiamente y notificada a clientes individuales y al canal global")

    # 8. Ausencia de registros en el historial individual durante la DEMO
    historial_final = db.obtener_historial(id_vehiculo)
    assert len(historial_final) == historial_count_inicial, (
        f"El historial no debe crecer por la DEMO. Inicial={historial_count_inicial}, Final={len(historial_final)}"
    )
    print(f"[PASS] 8. Verificado: El historial individual NO registró pasos de DEMO ({len(historial_final)} == {historial_count_inicial})")

    # Limpieza
    ws_mgr.disconnect(id_vehiculo, ws1)
    ws_mgr.disconnect(id_vehiculo, ws2)
    ws_mgr.disconnect(id_vehiculo_2, ws_otro)
    ws_mgr.disconnect_global(ws_global)
    print("=== TODAS LAS PRUEBAS (8/8) COMPLETADAS CON ÉXITO ===")


if __name__ == "__main__":
    asyncio.run(run_all_checks())

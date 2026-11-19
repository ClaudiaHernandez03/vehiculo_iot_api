# ==============================================================================
# MÓDULO PRINCIPAL: main.py
# PROPÓSITO: Inicializar la aplicación FastAPI, configurar el middleware CORS
#            explícito y seguro, registrar los manejadores globales de excepciones
#            y exponer los controladores HTTP y WebSocket delegando la lógica de
#            negocio y el acceso a datos en las clases POO del sistema.
# ==============================================================================

from contextlib import asynccontextmanager
import logging

from fastapi import Body, Depends, FastAPI, Header, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.docs import get_swagger_ui_html
from fastapi.staticfiles import StaticFiles

from database import Database
from demo_manager import DemoManager
from exceptions import ExceptionManager
from models import (
    ComandoActualizar,
    ComandoCrear,
    DemoActualizar,
    DemoCrear,
    DetenerRequest,
    EstadoActualizar,
    HistorialCrear,
    MovimientoDemo,
    MovimientoRequest,
    VehiculoActualizar,
    VehiculoCrear,
)
from services import ApiKeyGuard, VehiculoIoTService
from settings import settings
from websocket_manager import ConnectionManager, OrderSequenceManager

# Configuración de bitácora (logs) e instanciación de las clases POO principales
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("vehiculo_iot_api")

db = Database(settings)                 # Repositorio de acceso a Amazon RDS (21 Stored Procedures)
manager = ConnectionManager()           # Administrador de conexiones y eventos WebSocket
seq_manager = OrderSequenceManager(     # Gestor atómico y persistente de secuencia orden_id
    settings.order_seq_state_file
)
demo_runner = DemoManager(              # Coordinador asíncrono de secuencias DEMO (máx. 4 pasos)
    db, manager, sequence_manager=seq_manager
)
api_key_guard = ApiKeyGuard(settings)   # Dependencia POO de validación opcional de API Key
service = VehiculoIoTService(           # Servicio de negocio POO que orquesta RDS, WS, DEMO y DETENER
    db_provider=lambda: db,
    ws_manager=manager,
    demo_manager=demo_runner,
    sequence_manager=seq_manager,
    config=settings,
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Ciclo de vida de FastAPI: al apagar el servidor, cancela watchdogs, las DEMO activas
    y cierra limpiamente todas las conexiones WebSocket."""
    yield
    await service.cancel_all_watchdogs()
    await demo_runner.cancel_all()
    await manager.close_all()


# Inicialización de la aplicación FastAPI (docs_url=None para usar nuestra ruta /docs personalizada)
app = FastAPI(
    title=settings.app_title,
    description="API FastAPI conectada a Amazon RDS: 21 Stored Procedures, WebSocket y ejecución de DEMO.",
    version=settings.app_version,
    lifespan=lifespan,
    docs_url=None,
)

# Configuración explícita y segura de CORS para desarrollo local y GitHub Pages (HTTPS)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=settings.cors_allow_credentials,
    allow_methods=settings.cors_allow_methods,
    allow_headers=settings.cors_allow_headers,
    max_age=settings.cors_max_age,
)

# Registro centralizado de manejadores de excepciones de dominio y base de datos
ExceptionManager.register_handlers(app)

# Servir archivos estáticos (hoja de estilos oscura para Swagger UI) usando ruta absoluta
app.mount("/static", StaticFiles(directory=str(settings.static_dir)), name="static")

# Servir aplicación web frontend recuperada bajo /app si el directorio está presente
if settings.frontend_dir.is_dir():
    app.mount(
        "/app",
        StaticFiles(directory=str(settings.frontend_dir), html=True),
        name="frontend",
    )


@app.get("/docs", include_in_schema=False)
async def custom_swagger_ui_html():
    """Sirve la interfaz Swagger UI nativa estilizada con CSS corporativo."""
    return get_swagger_ui_html(
        openapi_url=app.openapi_url,
        title=settings.app_title,
        swagger_css_url="/static/swagger-custom.css",
        swagger_favicon_url="https://fastapi.tiangolo.com/img/favicon.png",
        swagger_ui_parameters={"docExpansion": "list", "defaultModelsExpandDepth": 1},
    )


# Alias de compatibilidad hacia las clases POO de seguridad y ejecución de SPs
check_api_key = api_key_guard


def ejecutar(nombre, parametros=None, escritura=False):
    """Envoltorio compatible que delega en VehiculoIoTService.ejecutar_sp."""
    return service.ejecutar_sp(nombre, parametros, escritura=escritura)


@app.get("/health", tags=["Sistema"])
def health():
    """Endpoint de verificación rápida para comprobar que el servidor está en línea."""
    return {"ok": True, "servicio": "vehiculo_iot_api", "version": settings.app_version}


# ==============================================================================
# 1. VEHÍCULOS (5 Stored Procedures: crear, obtener todos, obtener uno, actualizar, eliminar)
# ==============================================================================
@app.post("/api/vehiculos", status_code=201, tags=["Vehículos"], dependencies=[Depends(check_api_key)])
def crear_vehiculo(p: VehiculoCrear):
    return service.ejecutar_sp("sp_crear_vehiculo", [p.nombre, p.modelo, p.descripcion, p.microcontrolador], True)


@app.get("/api/vehiculos", tags=["Vehículos"])
def obtener_vehiculos():
    return service.ejecutar_sp("sp_obtener_vehiculos")


@app.get("/api/vehiculos/{id_vehiculo}", tags=["Vehículos"])
def obtener_vehiculo(id_vehiculo: int):
    return service.ejecutar_sp("sp_obtener_vehiculo", [id_vehiculo])


@app.put("/api/vehiculos/{id_vehiculo}", tags=["Vehículos"], dependencies=[Depends(check_api_key)])
def actualizar_vehiculo(id_vehiculo: int, p: VehiculoActualizar):
    return service.ejecutar_sp(
        "sp_actualizar_vehiculo",
        [id_vehiculo, p.nombre, p.modelo, p.descripcion, p.microcontrolador, int(p.activo)],
        True,
    )


@app.delete("/api/vehiculos/{id_vehiculo}", tags=["Vehículos"], dependencies=[Depends(check_api_key)])
def eliminar_vehiculo(id_vehiculo: int):
    return service.ejecutar_sp("sp_eliminar_vehiculo", [id_vehiculo], True)


# ==============================================================================
# 2. COMANDOS (4 Stored Procedures: crear, listar por vehículo, actualizar, eliminar)
# ==============================================================================
@app.post("/api/comandos", status_code=201, tags=["Comandos"], dependencies=[Depends(check_api_key)])
def crear_comando(p: ComandoCrear):
    return service.ejecutar_sp("sp_crear_comando", [p.id_vehiculo, p.nombre, p.codigo, p.descripcion], True)


@app.get("/api/vehiculos/{id_vehiculo}/comandos", tags=["Comandos"])
def obtener_comandos_vehiculo(id_vehiculo: int):
    return service.ejecutar_sp("sp_obtener_comandos_vehiculo", [id_vehiculo])


@app.put("/api/comandos/{id_comando}", tags=["Comandos"], dependencies=[Depends(check_api_key)])
def actualizar_comando(id_comando: int, p: ComandoActualizar):
    return service.ejecutar_sp(
        "sp_actualizar_comando",
        [id_comando, p.nombre, p.codigo, p.descripcion, int(p.activo)],
        True,
    )


@app.delete("/api/comandos/{id_comando}", tags=["Comandos"], dependencies=[Depends(check_api_key)])
def eliminar_comando(id_comando: int):
    return service.ejecutar_sp("sp_eliminar_comando", [id_comando], True)


# ==============================================================================
# 3. DEMOSTRACIONES (4 Stored Procedures: crear, listar por vehículo, actualizar, eliminar)
# ==============================================================================
@app.post("/api/demos", status_code=201, tags=["Demostraciones"], dependencies=[Depends(check_api_key)])
def crear_demo(p: DemoCrear):
    return service.ejecutar_sp("sp_crear_demo", [p.id_vehiculo, p.nombre, p.descripcion], True)


@app.get("/api/vehiculos/{id_vehiculo}/demos", tags=["Demostraciones"])
def obtener_demos(id_vehiculo: int):
    return service.ejecutar_sp("sp_obtener_demos", [id_vehiculo])


@app.put("/api/demos/{id_demo}", tags=["Demostraciones"], dependencies=[Depends(check_api_key)])
def actualizar_demo(id_demo: int, p: DemoActualizar):
    return service.ejecutar_sp("sp_actualizar_demo", [id_demo, p.nombre, p.descripcion, int(p.activo)], True)


@app.delete("/api/demos/{id_demo}", tags=["Demostraciones"], dependencies=[Depends(check_api_key)])
def eliminar_demo(id_demo: int):
    return service.ejecutar_sp("sp_eliminar_demo", [id_demo], True)


# ==============================================================================
# 4. MOVIMIENTOS DEMO (4 Stored Procedures: agregar, listar, actualizar, eliminar)
# ==============================================================================
@app.post("/api/demos/{id_demo}/movimientos", status_code=201, tags=["Movimientos demo"], dependencies=[Depends(check_api_key)])
def agregar_movimiento_demo(id_demo: int, p: MovimientoDemo):
    return service.agregar_movimiento_demo(id_demo, p)


@app.get("/api/demos/{id_demo}/movimientos", tags=["Movimientos demo"])
def obtener_movimientos_demo(id_demo: int):
    return service.ejecutar_sp("sp_obtener_movimientos_demo", [id_demo])


@app.put("/api/movimientos-demo/{id_demo_movimiento}", tags=["Movimientos demo"], dependencies=[Depends(check_api_key)])
def actualizar_movimiento_demo(id_demo_movimiento: int, p: MovimientoDemo):
    return service.actualizar_movimiento_demo(id_demo_movimiento, p)


@app.delete("/api/movimientos-demo/{id_demo_movimiento}", tags=["Movimientos demo"], dependencies=[Depends(check_api_key)])
def eliminar_movimiento_demo(id_demo_movimiento: int):
    return service.ejecutar_sp("sp_eliminar_movimiento_demo", [id_demo_movimiento], True)


# ==============================================================================
# 5. EJECUCIÓN TEMPORIZADA Y CONTROL DE CONCURRENCIA DE DEMOS
# ==============================================================================
@app.post("/api/demos/{id_demo}/ejecutar", status_code=202, tags=["Demostraciones"], dependencies=[Depends(check_api_key)])
async def ejecutar_demo(id_demo: int):
    """Inicia la ejecución temporizada de una DEMO si el vehículo no tiene otra en curso."""
    return await service.iniciar_demo(id_demo)


@app.post("/api/demos/{id_demo}/cancelar", tags=["Demostraciones"], dependencies=[Depends(check_api_key)])
async def cancelar_demo(id_demo: int):
    """Cancela una DEMO activa asociada al id_demo."""
    return await service.cancelar_demo_por_id(id_demo)


@app.delete("/api/demos/{id_demo}/ejecutar", tags=["Demostraciones"], dependencies=[Depends(check_api_key)])
async def cancelar_demo_delete(id_demo: int):
    """Alias HTTP DELETE para cancelar una DEMO activa."""
    return await service.cancelar_demo_por_id(id_demo)


@app.get("/api/vehiculos/{id_vehiculo}/demo/activa", tags=["Demostraciones"])
def obtener_demo_activa_vehiculo(id_vehiculo: int):
    """Consulta si existe una DEMO en ejecución para el vehículo especificado."""
    return service.obtener_demo_activa(id_vehiculo)


@app.post("/api/vehiculos/{id_vehiculo}/demo/cancelar", tags=["Demostraciones"], dependencies=[Depends(check_api_key)])
async def cancelar_demo_vehiculo(id_vehiculo: int):
    """Cancela cualquier DEMO en ejecución para el vehículo especificado."""
    return await service.cancelar_demo_por_vehiculo(id_vehiculo)


# ==============================================================================
# 6. ESTADO E HISTORIAL (4 Stored Procedures + POST transaccional de movimiento)
# ==============================================================================
@app.put("/api/vehiculos/{id_vehiculo}/estado", tags=["Estado"], dependencies=[Depends(check_api_key)])
async def actualizar_estado(id_vehiculo: int, p: EstadoActualizar):
    return await service.actualizar_estado_vehiculo(id_vehiculo, p)


@app.get("/api/vehiculos/{id_vehiculo}/estado", tags=["Estado"])
def obtener_estado(id_vehiculo: int):
    return service.ejecutar_sp("sp_obtener_estado", [id_vehiculo])


@app.get("/api/vehiculos/{id_vehiculo}/historial", tags=["Historial"])
def obtener_historial(id_vehiculo: int):
    """Devuelve como máximo los 10 movimientos individuales más recientes del vehículo."""
    return service.obtener_historial_reciente(id_vehiculo)


@app.post("/api/historial", status_code=201, tags=["Historial"], dependencies=[Depends(check_api_key)])
async def registrar_historial(p: HistorialCrear):
    return await service.registrar_entrada_historial(p)


@app.post("/api/movimientos", status_code=201, tags=["Movimientos"], dependencies=[Depends(check_api_key)])
async def agregar_movimiento(
    p: MovimientoRequest,
    x_control_session: str | None = Header(default=None),
):
    """Registra un movimiento individual: actualiza estado e historial en una sola transacción
    en Amazon RDS y después notifica por WebSocket a todos los clientes del vehículo y al monitor global."""
    return await service.registrar_movimiento_individual(
        p, sesion_control_header=x_control_session
    )


@app.post("/api/vehiculos/{id_vehiculo}/detener", tags=["Movimientos"], dependencies=[Depends(check_api_key)])
async def detener_vehiculo(
    id_vehiculo: int,
    p: DetenerRequest | None = Body(default=None),
    x_control_session: str | None = Header(default=None),
):
    """Detiene con prioridad máxima cualquier movimiento o DEMO activa del vehículo,
    invalida órdenes anteriores, publica el evento 'detener' por WebSocket y espera
    opcionalmente el ACK de confirmación física ('detencion_confirmada') del ESP8266."""
    return await service.detener_vehiculo(
        id_vehiculo,
        p=p,
        sesion_control_header=x_control_session,
    )


# ==============================================================================
# 7. CANALES WEBSOCKET EN TIEMPO REAL (/ws, /ws/vehiculos y /ws/vehiculos/{id_vehiculo})
# ==============================================================================
@app.websocket("/ws")
@app.websocket("/ws/vehiculos")
async def websocket_vehiculos_global(websocket: WebSocket):
    """Canal WebSocket global:
    1. Al conectarse un cliente, envía el estado inicial de todos los vehículos ('estado_inicial_global').
    2. Permanece conectado para recibir en tiempo real los eventos posteriores de cualquier vehículo
       ('movimiento_individual', 'estado_actualizado', 'historial_registrado', 'demo_inicio',
       'demo_paso', 'demo_fin', 'demo_cancelada'), siempre incluyendo su 'id_vehiculo'."""
    await manager.connect_global(websocket)
    try:
        await service.enviar_estado_inicial_global(websocket)
        await manager.listen_client_messages(websocket, vehicle_id=None)
    except WebSocketDisconnect:
        pass
    except Exception:
        logger.exception("Error en WebSocket global /ws/vehiculos")
    finally:
        manager.disconnect_global(websocket)


@app.websocket("/ws/vehiculos/{id_vehiculo}")
async def websocket_vehiculo(websocket: WebSocket, id_vehiculo: int):
    """Canal WebSocket individual por vehículo:
    1. Al conectarse un cliente, consulta y envía inmediatamente 'estado_inicial' desde RDS.
    2. Mantiene la conexión abierta para recibir eventos ('movimiento_individual', 'demo_inicio',
       'demo_paso', 'demo_fin', 'demo_cancelada') y responder mensajes 'ping' con 'pong'."""
    if id_vehiculo <= 0:
        await websocket.close(code=1008, reason="id_vehiculo inválido")
        return
    await manager.connect(id_vehiculo, websocket)
    try:
        await service.enviar_estado_inicial_vehiculo(websocket, id_vehiculo)
        await manager.listen_client_messages(websocket, vehicle_id=id_vehiculo)
    except WebSocketDisconnect:
        pass
    except Exception:
        logger.exception("Error WebSocket para vehículo %s", id_vehiculo)
    finally:
        manager.disconnect(id_vehiculo, websocket)

# ==============================================================================
# MÓDULO PRINCIPAL: main.py
# PROPÓSITO: Inicializar la aplicación FastAPI, configurar el middleware CORS
#            explícito y seguro, registrar los manejadores globales de excepciones
#            y exponer los controladores HTTP y WebSocket delegando la lógica de
#            negocio y el acceso a datos en las clases POO del sistema.
# ==============================================================================

from contextlib import asynccontextmanager
from datetime import datetime, timezone
import logging

from fastapi import Body, Depends, FastAPI, Header, HTTPException, WebSocket, WebSocketDisconnect
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
    """Ciclo de vida de FastAPI: al iniciar asegura las tablas RBAC 4.3 en Amazon RDS;
    al apagar el servidor, cancela watchdogs, las DEMO activas y cierra conexiones WebSocket."""
    try:
        db.inicializar_esquema_rbac_admin()
        logger.info("Esquema RBAC 4.3 (usuarios_iot, asignaciones_vehiculo, bitacora_auditoria_iot) verificado en Amazon RDS.")
    except Exception:
        logger.exception("No se pudo inicializar el esquema RBAC 4.3 al arranque.")
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
# 6.B ADMINISTRACIÓN IoT MULTIUSUARIO (ACTIVIDAD 4.3 · RBAC / BOLA EN AMAZON RDS)
# ==============================================================================
@app.get("/api/admin/rbac", tags=["Administración 4.3"])
def obtener_estado_rbac():
    """Devuelve desde Amazon RDS los usuarios/roles, las asignaciones comerciales
    de propiedad por vehículo y la bitácora de auditoría."""
    db.inicializar_esquema_rbac_admin()
    return db.obtener_estado_rbac_admin()


@app.post("/api/admin/clientes", status_code=201, tags=["Administración 4.3"], dependencies=[Depends(check_api_key)])
def crear_cliente_rbac(payload: dict = Body(...)):
    """Registra un nuevo comprador en `usuarios_iot` de Amazon RDS."""
    nombre = str(payload.get("nombre") or "").strip()
    correo = str(payload.get("correo") or "").strip()
    if not nombre:
        raise ValueError("El nombre del cliente comprador es obligatorio.")
    return db.crear_cliente_rbac(nombre, correo or "comprador@empresa-iot.mx")


@app.put("/api/admin/asignaciones/{id_vehiculo}", tags=["Administración 4.3"], dependencies=[Depends(check_api_key)])
def actualizar_asignacion_rbac(id_vehiculo: int, payload: dict = Body(...)):
    """Asigna o revoca el propietario y estado comercial de un vehículo en Amazon RDS."""
    id_usuario = int(payload.get("id_cliente") or payload.get("id_usuario") or 101)
    estado_comercial = str(payload.get("estado_comercial") or "Vendido / Asignado")
    consentimiento = bool(payload.get("consentimiento_soporte", True))
    return db.asignar_vehiculo_rbac(id_vehiculo, id_usuario, estado_comercial, consentimiento)


@app.post("/api/admin/auditoria", status_code=201, tags=["Administración 4.3"])
def registrar_auditoria_rbac(payload: dict = Body(...)):
    """Persiste en Amazon RDS (`bitacora_auditoria_iot`) un evento de auditoría o rechazo RBAC/BOLA."""
    return db.registrar_auditoria_rbac(
        actor=str(payload.get("actor") or "SISTEMA"),
        rol=str(payload.get("rol") or "ADMIN"),
        accion=str(payload.get("accion") or "EVENTO"),
        recurso=str(payload.get("recurso") or "Vehículo #1"),
        resultado=str(payload.get("resultado") or "OK"),
    )


# ==============================================================================
# 6.C AUTENTICACIÓN, SESIONES Y ROLES (ACTIVIDAD 4.3 · LOGIN RBAC MULTIUSUARIO)
# ==============================================================================
@app.get("/api/auth/usuarios", tags=["Autenticación 4.3"])
def listar_usuarios_login():
    """Devuelve los perfiles de usuarios registrados en Amazon RDS para el login."""
    try:
        db.inicializar_esquema_rbac_admin()
        rbac = db.obtener_estado_rbac_admin()
        usuarios = rbac.get("usuarios", [])
    except Exception as e:
        logger.warning(f"No se pudo consultar usuarios de RDS ({e}), usando catálogo de respaldo.")
        usuarios = [
            {"id_cliente": 100, "nombre": "Administrador General (Empresa IoT)", "correo": "admin@cat320-iot.mx", "rol": "ADMIN", "permisos": "CRUD Inventario, Clientes, Asignaciones, Editor DEMO Fábrica, Auditoría Global", "activo": 1},
            {"id_cliente": 101, "nombre": "Claudia América Hernández (Comprador A)", "correo": "claudia.comprador@empresa-iot.mx", "rol": "USUARIO", "permisos": "Control Total y Monitoreo de Mis Vehículos (#1), Editor DEMO Propia (1-4 pasos)", "activo": 1},
            {"id_cliente": 102, "nombre": "Constructora Industrial del Centro (Comprador B)", "correo": "operaciones@constructora-centro.mx", "rol": "USUARIO", "permisos": "Control y Editor DEMO solo en sus unidades asignadas (Aislamiento BOLA/IDOR)", "activo": 1},
            {"id_cliente": 103, "nombre": "Visitante Sala de Exhibición (Prospecto)", "correo": "exhibicion@cat320-iot.mx", "rol": "INVITADO", "permisos": "Catálogo Público y Prueba Temporal Exclusiva 60s", "activo": 1}
        ]
    return {
        "ok": True,
        "usuarios": usuarios,
        "roles_permitidos": ["ADMIN", "USUARIO", "INVITADO"]
    }


@app.post("/api/auth/login", tags=["Autenticación 4.3"])
def autenticar_usuario(payload: dict = Body(...)):
    """Inicia sesión autenticando al usuario (ADMIN, USUARIO o INVITADO) y registra
    la evidencia en la bitácora de auditoría de Amazon RDS."""
    correo = str(payload.get("correo") or "").strip().lower()
    rol_solicitado = str(payload.get("rol") or "").strip().upper()

    try:
        db.inicializar_esquema_rbac_admin()
        rbac = db.obtener_estado_rbac_admin()
        usuarios = rbac.get("usuarios", [])
    except Exception as e:
        logger.warning(f"No se pudo consultar usuarios de RDS ({e}), usando catálogo de respaldo.")
        usuarios = [
            {"id_cliente": 100, "nombre": "Administrador General (Empresa IoT)", "correo": "admin@cat320-iot.mx", "rol": "ADMIN", "permisos": "CRUD Inventario, Clientes, Asignaciones, Editor DEMO Fábrica, Auditoría Global", "activo": 1},
            {"id_cliente": 101, "nombre": "Claudia América Hernández (Comprador A)", "correo": "claudia.comprador@empresa-iot.mx", "rol": "USUARIO", "permisos": "Control Total y Monitoreo de Mis Vehículos (#1), Editor DEMO Propia (1-4 pasos)", "activo": 1},
            {"id_cliente": 102, "nombre": "Constructora Industrial del Centro (Comprador B)", "correo": "operaciones@constructora-centro.mx", "rol": "USUARIO", "permisos": "Control y Editor DEMO solo en sus unidades asignadas (Aislamiento BOLA/IDOR)", "activo": 1},
            {"id_cliente": 103, "nombre": "Visitante Sala de Exhibición (Prospecto)", "correo": "exhibicion@cat320-iot.mx", "rol": "INVITADO", "permisos": "Catálogo Público y Prueba Temporal Exclusiva 60s", "activo": 1}
        ]

    usuario_encontrado = None
    if correo:
        for u in usuarios:
            if str(u.get("correo") or "").strip().lower() == correo:
                usuario_encontrado = dict(u)
                break

    if not usuario_encontrado and rol_solicitado:
        for u in usuarios:
            if str(u.get("rol") or "").strip().upper() == rol_solicitado:
                usuario_encontrado = dict(u)
                break

    if not usuario_encontrado:
        if rol_solicitado == "INVITADO" or "invitado" in correo or "exhibicion" in correo:
            usuario_encontrado = {
                "id_cliente": 103,
                "nombre": "Visitante Sala de Exhibición (Prospecto)",
                "correo": "exhibicion@cat320-iot.mx",
                "rol": "INVITADO",
                "permisos": "Catálogo Público y Prueba Temporal Exclusiva 60s",
                "activo": 1
            }
        else:
            raise HTTPException(status_code=401, detail="Usuario o credenciales no válidas en Amazon RDS.")

    # Registro en bitácora de auditoría en Amazon RDS
    try:
        db.registrar_auditoria_rbac(
            actor=usuario_encontrado.get("nombre", "Usuario"),
            rol=usuario_encontrado.get("rol", "INVITADO"),
            accion="LOGIN_INICIO_SESION",
            recurso="Plataforma IoT Control/Monitoreo",
            resultado="200 OK · Sesión iniciada y rol activado"
        )
    except Exception as e:
        logger.warning(f"No se pudo registrar auditoría en RDS ({e})")

    token = f"iot-token-{usuario_encontrado.get('rol', '').lower()}-{usuario_encontrado.get('id_cliente', 0)}"
    return {
        "ok": True,
        "mensaje": f"Bienvenido/a {usuario_encontrado.get('nombre')}",
        "usuario": usuario_encontrado,
        "token_sesion": token,
        "timestamp": datetime.now(timezone.utc).isoformat()
    }


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

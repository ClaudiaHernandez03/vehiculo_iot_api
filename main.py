from contextlib import asynccontextmanager
from datetime import datetime, timezone
import asyncio
import json
import logging

from fastapi import Depends, FastAPI, Header, HTTPException, WebSocket, WebSocketDisconnect
from mysql.connector import Error as MySQLError
from pydantic import BaseModel, Field

from database import Database
from websocket_manager import ConnectionManager
from settings import settings

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("vehiculo_iot_api")
db = Database()
manager = ConnectionManager()


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield
    await manager.close_all()


app = FastAPI(
    title="API Vehículo IoT - Práctica 2.5",
    description="API FastAPI conectada a Amazon RDS: 21 Stored Procedures y WebSocket.",
    version="1.2.0",
    lifespan=lifespan,
)


# Modelos de entrada
class VehiculoCrear(BaseModel):
    nombre: str = Field(min_length=1, max_length=80)
    modelo: str | None = Field(default=None, max_length=80)
    descripcion: str | None = Field(default=None, max_length=255)
    microcontrolador: str | None = Field(default=None, max_length=50)


class VehiculoActualizar(VehiculoCrear):
    activo: bool = True


class ComandoCrear(BaseModel):
    id_vehiculo: int = Field(gt=0)
    nombre: str = Field(min_length=1, max_length=60)
    codigo: int
    descripcion: str | None = Field(default=None, max_length=255)


class ComandoActualizar(BaseModel):
    nombre: str = Field(min_length=1, max_length=60)
    codigo: int
    descripcion: str | None = Field(default=None, max_length=255)
    activo: bool = True


class DemoCrear(BaseModel):
    id_vehiculo: int = Field(gt=0)
    nombre: str = Field(min_length=1, max_length=80)
    descripcion: str | None = Field(default=None, max_length=255)


class DemoActualizar(BaseModel):
    nombre: str = Field(min_length=1, max_length=80)
    descripcion: str | None = Field(default=None, max_length=255)
    activo: bool = True


class MovimientoDemo(BaseModel):
    id_comando: int = Field(gt=0)
    orden: int = Field(ge=0)
    duracion_ms: int = Field(ge=0)
    pausa_ms: int = Field(ge=0)


class EstadoActualizar(BaseModel):
    id_comando: int = Field(gt=0)
    conectado: bool


class MovimientoRequest(BaseModel):
    id_vehiculo: int = Field(gt=0)
    id_comando: int = Field(gt=0)
    origen: str = Field(default="API", min_length=1, max_length=30)


class HistorialCrear(BaseModel):
    id_vehiculo: int = Field(gt=0)
    id_comando: int = Field(gt=0)
    origen: str = Field(default="API", min_length=1, max_length=30)


def check_api_key(x_api_key: str | None = Header(default=None)):
    if settings.api_key and x_api_key != settings.api_key:
        raise HTTPException(status_code=401, detail="API key inválida o ausente")


def ejecutar(nombre, parametros=None, escritura=False):
    try:
        return db.ejecutar_sp(nombre, parametros or [], escritura=escritura)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except MySQLError as exc:
        logger.exception("Error MySQL ejecutando %s", nombre)
        raise HTTPException(status_code=503, detail="No fue posible completar la operación en Amazon RDS.") from exc
    except Exception as exc:
        logger.exception("Error ejecutando %s", nombre)
        raise HTTPException(status_code=503, detail="Error inesperado al consultar la base de datos.") from exc


@app.get("/health", tags=["Sistema"])
def health():
    return {"ok": True, "servicio": "vehiculo_iot_api", "version": "1.2.0"}


# VEHÍCULOS: crear, obtener uno, obtener todos, actualizar, eliminar
@app.post("/api/vehiculos", status_code=201, tags=["Vehículos"], dependencies=[Depends(check_api_key)])
def crear_vehiculo(p: VehiculoCrear):
    return ejecutar("sp_crear_vehiculo", [p.nombre, p.modelo, p.descripcion, p.microcontrolador], True)


@app.get("/api/vehiculos", tags=["Vehículos"])
def obtener_vehiculos():
    return ejecutar("sp_obtener_vehiculos")


@app.get("/api/vehiculos/{id_vehiculo}", tags=["Vehículos"])
def obtener_vehiculo(id_vehiculo: int):
    return ejecutar("sp_obtener_vehiculo", [id_vehiculo])


@app.put("/api/vehiculos/{id_vehiculo}", tags=["Vehículos"], dependencies=[Depends(check_api_key)])
def actualizar_vehiculo(id_vehiculo: int, p: VehiculoActualizar):
    return ejecutar("sp_actualizar_vehiculo",
                    [id_vehiculo, p.nombre, p.modelo, p.descripcion, p.microcontrolador, int(p.activo)], True)


@app.delete("/api/vehiculos/{id_vehiculo}", tags=["Vehículos"], dependencies=[Depends(check_api_key)])
def eliminar_vehiculo(id_vehiculo: int):
    return ejecutar("sp_eliminar_vehiculo", [id_vehiculo], True)


# COMANDOS: crear, listar por vehículo, actualizar, eliminar
@app.post("/api/comandos", status_code=201, tags=["Comandos"], dependencies=[Depends(check_api_key)])
def crear_comando(p: ComandoCrear):
    return ejecutar("sp_crear_comando", [p.id_vehiculo, p.nombre, p.codigo, p.descripcion], True)


@app.get("/api/vehiculos/{id_vehiculo}/comandos", tags=["Comandos"])
def obtener_comandos_vehiculo(id_vehiculo: int):
    return ejecutar("sp_obtener_comandos_vehiculo", [id_vehiculo])


@app.put("/api/comandos/{id_comando}", tags=["Comandos"], dependencies=[Depends(check_api_key)])
def actualizar_comando(id_comando: int, p: ComandoActualizar):
    return ejecutar("sp_actualizar_comando",
                    [id_comando, p.nombre, p.codigo, p.descripcion, int(p.activo)], True)


@app.delete("/api/comandos/{id_comando}", tags=["Comandos"], dependencies=[Depends(check_api_key)])
def eliminar_comando(id_comando: int):
    return ejecutar("sp_eliminar_comando", [id_comando], True)


# DEMOS: crear, listar por vehículo, actualizar, eliminar
@app.post("/api/demos", status_code=201, tags=["Demostraciones"], dependencies=[Depends(check_api_key)])
def crear_demo(p: DemoCrear):
    return ejecutar("sp_crear_demo", [p.id_vehiculo, p.nombre, p.descripcion], True)


@app.get("/api/vehiculos/{id_vehiculo}/demos", tags=["Demostraciones"])
def obtener_demos(id_vehiculo: int):
    return ejecutar("sp_obtener_demos", [id_vehiculo])


@app.put("/api/demos/{id_demo}", tags=["Demostraciones"], dependencies=[Depends(check_api_key)])
def actualizar_demo(id_demo: int, p: DemoActualizar):
    return ejecutar("sp_actualizar_demo", [id_demo, p.nombre, p.descripcion, int(p.activo)], True)


@app.delete("/api/demos/{id_demo}", tags=["Demostraciones"], dependencies=[Depends(check_api_key)])
def eliminar_demo(id_demo: int):
    return ejecutar("sp_eliminar_demo", [id_demo], True)


# MOVIMIENTOS DEMO: agregar, listar, actualizar, eliminar
@app.post("/api/demos/{id_demo}/movimientos", status_code=201, tags=["Movimientos demo"], dependencies=[Depends(check_api_key)])
def agregar_movimiento_demo(id_demo: int, p: MovimientoDemo):
    return ejecutar("sp_agregar_movimiento_demo",
                    [id_demo, p.id_comando, p.orden, p.duracion_ms, p.pausa_ms], True)


@app.get("/api/demos/{id_demo}/movimientos", tags=["Movimientos demo"])
def obtener_movimientos_demo(id_demo: int):
    return ejecutar("sp_obtener_movimientos_demo", [id_demo])


@app.put("/api/movimientos-demo/{id_demo_movimiento}", tags=["Movimientos demo"], dependencies=[Depends(check_api_key)])
def actualizar_movimiento_demo(id_demo_movimiento: int, p: MovimientoDemo):
    return ejecutar("sp_actualizar_movimiento_demo",
                    [id_demo_movimiento, p.id_comando, p.orden, p.duracion_ms, p.pausa_ms], True)


@app.delete("/api/movimientos-demo/{id_demo_movimiento}", tags=["Movimientos demo"], dependencies=[Depends(check_api_key)])
def eliminar_movimiento_demo(id_demo_movimiento: int):
    return ejecutar("sp_eliminar_movimiento_demo", [id_demo_movimiento], True)


# ESTADO E HISTORIAL
@app.put("/api/vehiculos/{id_vehiculo}/estado", tags=["Estado"], dependencies=[Depends(check_api_key)])
def actualizar_estado(id_vehiculo: int, p: EstadoActualizar):
    return ejecutar("sp_actualizar_estado", [id_vehiculo, p.id_comando, int(p.conectado)], True)


@app.get("/api/vehiculos/{id_vehiculo}/estado", tags=["Estado"])
def obtener_estado(id_vehiculo: int):
    return ejecutar("sp_obtener_estado", [id_vehiculo])


@app.get("/api/vehiculos/{id_vehiculo}/historial", tags=["Historial"])
def obtener_historial(id_vehiculo: int):
    resultado = ejecutar("sp_obtener_historial", [id_vehiculo])
    resultado["resultados"] = resultado["resultados"][:10]
    return resultado


@app.post("/api/historial", status_code=201, tags=["Historial"], dependencies=[Depends(check_api_key)])
def registrar_historial(p: HistorialCrear):
    return ejecutar("sp_registrar_historial",
                    [p.id_vehiculo, p.id_comando, p.origen], True)


@app.post("/api/movimientos", status_code=201, tags=["Movimientos"], dependencies=[Depends(check_api_key)])
async def agregar_movimiento(p: MovimientoRequest):
    try:
        base_datos = db.registrar_movimiento(p.id_vehiculo, p.id_comando, p.origen)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except MySQLError as exc:
        logger.exception("Error MySQL al registrar movimiento")
        raise HTTPException(status_code=503, detail="No fue posible completar la operación en Amazon RDS.") from exc
    event = {"tipo": "movimiento_individual", "id_vehiculo": p.id_vehiculo,
             "id_comando": p.id_comando, "origen": p.origen,
             "timestamp": datetime.now(timezone.utc).isoformat(),
             "estado": "confirmado_en_rds"}
    await manager.broadcast_to_vehicle(p.id_vehiculo, event)
    return {"ok": True, "mensaje": "Movimiento individual registrado correctamente",
            "movimiento": event, "base_datos": base_datos}


@app.websocket("/ws/vehiculos/{id_vehiculo}")
async def websocket_vehiculo(websocket: WebSocket, id_vehiculo: int):
    if id_vehiculo <= 0:
        await websocket.close(code=1008, reason="id_vehiculo inválido")
        return
    await manager.connect(id_vehiculo, websocket)
    try:
        try:
            estado = await asyncio.to_thread(db.obtener_estado, id_vehiculo)
            await websocket.send_json({"tipo": "estado_inicial", "id_vehiculo": id_vehiculo,
                                       "estado": estado, "timestamp": datetime.now(timezone.utc).isoformat()})
        except Exception:
            logger.exception("No se pudo obtener el estado inicial del vehículo %s", id_vehiculo)
            await websocket.send_json({"tipo": "error", "id_vehiculo": id_vehiculo,
                                       "mensaje": "No fue posible consultar el estado inicial"})
        while True:
            mensaje = await websocket.receive_text()
            try:
                data = json.loads(mensaje)
            except json.JSONDecodeError:
                await websocket.send_json({"tipo": "error", "mensaje": "El mensaje recibido debe ser JSON"})
                continue
            if isinstance(data, dict) and data.get("tipo") == "ping":
                await websocket.send_json({"tipo": "pong", "id_vehiculo": id_vehiculo,
                                           "timestamp": datetime.now(timezone.utc).isoformat()})
    except WebSocketDisconnect:
        pass
    except Exception:
        logger.exception("Error WebSocket para vehículo %s", id_vehiculo)
    finally:
        manager.disconnect(id_vehiculo, websocket)

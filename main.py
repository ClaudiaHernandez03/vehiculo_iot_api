from contextlib import asynccontextmanager
from datetime import datetime, timezone
import asyncio
import json
import logging

from fastapi import FastAPI, Header, HTTPException, WebSocket, WebSocketDisconnect, Depends
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
    description=(
        "API modular para movimiento individual (POST), consulta de los últimos "
        "10 movimientos (GET) y monitoreo en tiempo real (WebSocket). "
        "La base de datos está en Amazon RDS; FastAPI se ejecuta localmente."
    ),
    version="1.1.0",
    lifespan=lifespan,
)


class MovimientoRequest(BaseModel):
    id_vehiculo: int = Field(gt=0, description="ID del vehículo existente")
    id_comando: int = Field(gt=0, description="ID de un comando válido para ese vehículo")
    origen: str = Field(default="API", min_length=1, max_length=30)


def check_api_key(x_api_key: str | None = Header(default=None)):
    if settings.api_key and x_api_key != settings.api_key:
        raise HTTPException(status_code=401, detail="API key inválida o ausente")


def raise_db_unavailable(exc: Exception):
    logger.exception("Error de base de datos")
    raise HTTPException(
        status_code=503,
        detail="No fue posible completar la operación con la base de datos.",
    ) from exc


@app.get("/api/vehiculos/{id_vehiculo}/historial", tags=["Historial"])
def obtener_ultimos_movimientos(id_vehiculo: int):
    """Devuelve como máximo los diez movimientos individuales más recientes."""
    if id_vehiculo <= 0:
        raise HTTPException(status_code=422, detail="id_vehiculo debe ser mayor que cero")
    try:
        rows = db.obtener_historial(id_vehiculo)
        return {
            "ok": True,
            "id_vehiculo": id_vehiculo,
            "cantidad": len(rows),
            "limite": 10,
            "movimientos": rows[:10],
        }
    except Exception as exc:
        raise_db_unavailable(exc)


@app.post(
    "/api/movimientos",
    status_code=201,
    tags=["Movimientos"],
    dependencies=[Depends(check_api_key)],
)
async def agregar_movimiento(payload: MovimientoRequest):
    """
    Registra un movimiento individual. El estado y el historial se guardan
    en una transacción; el evento se publica solo después del COMMIT.
    """
    try:
        result = db.registrar_movimiento(
            id_vehiculo=payload.id_vehiculo,
            id_comando=payload.id_comando,
            origen=payload.origen,
        )
   
    except LookupError as exc:
        raise HTTPException(
            status_code=404,
            detail=str(exc)
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail=str(exc)
        ) from exc
    except MySQLError as exc:
        raise_db_unavailable(exc)
    except Exception as exc:
        raise_db_unavailable(exc)

    event = {
        "tipo": "movimiento_individual",
        "id_vehiculo": payload.id_vehiculo,
        "id_comando": payload.id_comando,
        "origen": payload.origen,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "estado": "confirmado_en_rds",
    }
    await manager.broadcast_to_vehicle(payload.id_vehiculo, event)
    return {
        "ok": True,
        "mensaje": "Movimiento individual registrado correctamente",
        "movimiento": event,
        "base_datos": result,
    }


@app.websocket("/ws/vehiculos/{id_vehiculo}")
async def websocket_vehiculo(websocket: WebSocket, id_vehiculo: int):
    """Envía el estado inicial y notifica movimientos del vehículo suscrito."""
    if id_vehiculo <= 0:
        await websocket.close(code=1008, reason="id_vehiculo inválido")
        return

    await manager.connect(id_vehiculo, websocket)
    try:
        try:
            estado = await asyncio.to_thread(db.obtener_estado, id_vehiculo)
            await websocket.send_json({
                "tipo": "estado_inicial",
                "id_vehiculo": id_vehiculo,
                "estado": estado,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })
        except Exception:
            logger.exception("No se pudo obtener el estado inicial del vehículo %s", id_vehiculo)
            await websocket.send_json({
                "tipo": "error",
                "id_vehiculo": id_vehiculo,
                "mensaje": "No fue posible consultar el estado inicial",
            })

        while True:
            message = await websocket.receive_text()
            try:
                data = json.loads(message)
            except json.JSONDecodeError:
                await websocket.send_json({
                    "tipo": "error",
                    "mensaje": "El mensaje recibido debe ser JSON",
                })
                continue
            if isinstance(data, dict) and data.get("tipo") == "ping":
                await websocket.send_json({
                    "tipo": "pong",
                    "id_vehiculo": id_vehiculo,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                })
    except WebSocketDisconnect:
        pass
    except Exception:
        logger.exception("Error WebSocket para vehículo %s", id_vehiculo)
    finally:
        manager.disconnect(id_vehiculo, websocket)


@app.get("/health", tags=["Sistema"])
def health():
    return {"ok": True, "servicio": "vehiculo_iot_api", "version": "1.1.0"}

# ==============================================================================
# MÓDULO: exceptions.py
# PROPÓSITO: Centralizar la jerarquía de excepciones de dominio y el registro
#            de manejadores globales (Exception Handlers) en FastAPI para evitar
#            bloques try/except duplicados en los controladores.
# ==============================================================================

import logging
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from mysql.connector import Error as MySQLError

logger = logging.getLogger("vehiculo_iot_api")


class VehiculoIoTError(Exception):
    """Excepción base del dominio Vehículo IoT."""

    status_code: int = 500

    def __init__(self, detail: str, status_code: int | None = None) -> None:
        super().__init__(detail)
        self.detail = detail
        if status_code is not None:
            self.status_code = status_code


class RecursoNoEncontradoError(VehiculoIoTError, LookupError):
    """Se lanza cuando un vehículo, comando o DEMO no existe (HTTP 404)."""

    status_code = 404


class ReglaNegocioError(VehiculoIoTError, ValueError):
    """Se lanza cuando falla una regla de validación de negocio (HTTP 400)."""

    status_code = 400


class ConflictoConcurrenciaError(VehiculoIoTError, RuntimeError):
    """Se lanza cuando ya existe una DEMO en curso para el mismo vehículo (HTTP 409)."""

    status_code = 409


class BaseDatosNoDisponibleError(VehiculoIoTError):
    """Se lanza cuando ocurre un fallo de comunicación o ejecución en Amazon RDS (HTTP 503)."""

    status_code = 503


class ExceptionManager:
    """Clase POO que centraliza la traducción de excepciones a respuestas HTTP de FastAPI."""

    @staticmethod
    def translate_exception(exc: Exception, context: str = "") -> HTTPException:
        """Convierte una excepción de Python o MySQL en HTTPException preservando los códigos de estado."""
        if isinstance(exc, HTTPException):
            return exc
        if isinstance(exc, LookupError):
            return HTTPException(status_code=404, detail=str(exc))
        if isinstance(exc, ValueError):
            return HTTPException(status_code=400, detail=str(exc))
        if isinstance(exc, RuntimeError):
            return HTTPException(status_code=409, detail=str(exc))
        if isinstance(exc, MySQLError):
            logger.exception("Error MySQL en %s", context or "operación de base de datos")
            return HTTPException(
                status_code=503,
                detail="No fue posible completar la operación en Amazon RDS.",
            )
        logger.exception("Error inesperado en %s", context or "operación del servidor")
        return HTTPException(
            status_code=503,
            detail="Error inesperado al consultar la base de datos.",
        )

    @classmethod
    def register_handlers(cls, app: FastAPI) -> None:
        """Registra manejadores globales en la instancia FastAPI."""

        @app.exception_handler(VehiculoIoTError)
        async def handle_domain_error(_request: Request, exc: VehiculoIoTError) -> JSONResponse:
            return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})

        @app.exception_handler(MySQLError)
        async def handle_mysql_error(_request: Request, _exc: MySQLError) -> JSONResponse:
            logger.exception("Error de MySQL capturado por el manejador global")
            return JSONResponse(
                status_code=503,
                content={"detail": "No fue posible completar la operación en Amazon RDS."},
            )

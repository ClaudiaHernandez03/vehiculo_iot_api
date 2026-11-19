# ==============================================================================
# MÓDULO: models.py
# PROPÓSITO: Definir mediante Programación Orientada a Objetos los modelos y
#            esquemas de validación (Pydantic) de las entidades del sistema:
#            Vehículos, Comandos, Demostraciones, Movimientos Demo, Estado,
#            Historial y Movimientos Individuales.
# ==============================================================================

from pydantic import BaseModel, Field


class VehiculoCrear(BaseModel):
    """Modelo de entrada para registrar un nuevo vehículo IoT (sp_crear_vehiculo)."""

    nombre: str = Field(min_length=1, max_length=80)
    modelo: str | None = Field(default=None, max_length=80)
    descripcion: str | None = Field(default=None, max_length=255)
    microcontrolador: str | None = Field(default=None, max_length=50)


class VehiculoActualizar(VehiculoCrear):
    """Modelo de entrada para actualizar un vehículo existente (sp_actualizar_vehiculo)."""

    activo: bool = True


class ComandoCrear(BaseModel):
    """Modelo de entrada para registrar un comando de vehículo (sp_crear_comando)."""

    id_vehiculo: int = Field(gt=0)
    nombre: str = Field(min_length=1, max_length=60)
    codigo: int
    descripcion: str | None = Field(default=None, max_length=255)


class ComandoActualizar(BaseModel):
    """Modelo de entrada para actualizar un comando existente (sp_actualizar_comando)."""

    nombre: str = Field(min_length=1, max_length=60)
    codigo: int
    descripcion: str | None = Field(default=None, max_length=255)
    activo: bool = True


class DemoCrear(BaseModel):
    """Modelo de entrada para crear una secuencia DEMO (sp_crear_demo)."""

    id_vehiculo: int = Field(gt=0)
    nombre: str = Field(min_length=1, max_length=80)
    descripcion: str | None = Field(default=None, max_length=255)


class DemoActualizar(BaseModel):
    """Modelo de entrada para actualizar una secuencia DEMO (sp_actualizar_demo)."""

    nombre: str = Field(min_length=1, max_length=80)
    descripcion: str | None = Field(default=None, max_length=255)
    activo: bool = True


class MovimientoDemo(BaseModel):
    """Modelo de entrada para agregar o actualizar un paso de DEMO (orden válido: 1 a 4)."""

    id_comando: int = Field(gt=0)
    orden: int = Field(ge=1, le=4)
    duracion_ms: int = Field(ge=0)
    pausa_ms: int = Field(ge=0)


class EstadoActualizar(BaseModel):
    """Modelo de entrada para actualizar el estado actual del vehículo (sp_actualizar_estado)."""

    id_comando: int = Field(gt=0)
    conectado: bool


class MovimientoRequest(BaseModel):
    """Modelo de entrada para ejecutar y registrar un movimiento individual transaccional."""

    id_vehiculo: int = Field(gt=0)
    id_comando: int = Field(gt=0)
    origen: str = Field(default="API", min_length=1, max_length=30)
    sesion_control: str | None = Field(default=None, min_length=1, max_length=64)


class DetenerRequest(BaseModel):
    """Modelo de entrada para solicitar la detención prioritaria de un vehículo."""

    origen: str = Field(default="API", min_length=1, max_length=30)
    esperar_confirmacion_ms: int = Field(default=1500, ge=0, le=10000)
    sesion_control: str | None = Field(default=None, min_length=1, max_length=64)


class HistorialCrear(BaseModel):
    """Modelo de entrada para registrar directamente una entrada en historial_comandos."""

    id_vehiculo: int = Field(gt=0)
    id_comando: int = Field(gt=0)
    origen: str = Field(default="API", min_length=1, max_length=30)

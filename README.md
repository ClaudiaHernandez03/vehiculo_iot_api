# API Vehículo IoT — Práctica 2.5 (versión inicial actualizada)

Esta aplicación se ejecuta localmente en VS Code y se conecta a la base `vehiculo_iot` alojada en Amazon RDS. La arquitectura está dividida en módulos para añadir después los otros Stored Procedures y endpoints sin rehacer la aplicación.

## Alcance actual

- `POST /api/movimientos`: ejecuta `sp_actualizar_estado` y `sp_registrar_historial` en una sola transacción; solo notifica WebSocket después de `COMMIT`.
- `GET /api/vehiculos/{id_vehiculo}/historial`: devuelve como máximo los diez movimientos individuales más recientes.
- `WS /ws/vehiculos/{id_vehiculo}`: envía el estado inicial y notifica eventos del vehículo correspondiente.
- `GET /health`: comprobación auxiliar de la API.

No incluye DEMO ni los otros endpoints todavía.

## Preparar en Windows / VS Code

Abre esta carpeta en VS Code. En la terminal PowerShell:

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
```

Completa `.env` con los datos reales de Amazon RDS. No publiques `.env` ni compartas la contraseña.

## Ejecutar

```powershell
uvicorn main:app --reload --host 127.0.0.1 --port 8000
```

- Swagger: http://localhost:8000/docs
- Salud: http://localhost:8000/health

## POST de movimiento individual

URL: `POST http://127.0.0.1:8000/api/movimientos`

Header: `x-api-key: <API_KEY de .env>` si configuraste una API key no vacía.

```json
{
  "id_vehiculo": 1,
  "id_comando": 2,
  "origen": "Thunder Client"
}
```

El código ejecuta:
- `sp_actualizar_estado(p_id_vehiculo, p_id_comando, p_conectado)`
- `sp_registrar_historial(p_id_vehiculo, p_id_comando, p_origen)`

El valor `p_conectado` se manda como `FALSE` provisionalmente para no declarar una conexión física que no se ha confirmado. Esto se debe revisar con las reglas y campos de la base antes de considerar el POST listo para evaluación.

## GET de historial

URL: `GET http://127.0.0.1:8000/api/vehiculos/1/historial`

`sp_obtener_historial` devuelve los datos en orden descendente por `fecha_hora` e `id_historial`. La API devuelve máximo diez registros.

## WebSocket

Conecta Thunder Client a `ws://127.0.0.1:8000/ws/vehiculos/1`. Recibirás `estado_inicial`. Después, ejecuta el POST en otra pestaña para recibir el evento `movimiento_individual`. Puedes enviar `{"tipo":"ping"}` para recibir `pong`.

## Firmas de SP confirmadas en RDS

```sql
sp_actualizar_estado(IN p_id_vehiculo INT, IN p_id_comando INT, IN p_conectado BOOLEAN)
sp_registrar_historial(IN p_id_vehiculo INT, IN p_id_comando INT, IN p_origen VARCHAR(30))
sp_obtener_historial(IN p_id_vehiculo INT)
sp_obtener_estado(IN p_id_vehiculo INT)
```

## Pendiente antes de la prueba funcional

La guía 2.5 exige validar que el vehículo exista y esté activo, que el comando exista y esté activo y que pertenezca a ese vehículo. Las definiciones recibidas hasta ahora no incluyen esas comprobaciones dentro de `sp_actualizar_estado`, por lo que falta revisar `SHOW CREATE TABLE vehiculo_iot.vehiculos` y `SHOW CREATE TABLE vehiculo_iot.comandos` para implementar la validación con los nombres reales de columnas. No ejecutes pruebas POST de datos reales hasta terminar esa validación.

## Arquitectura ampliable

- `main.py`: rutas HTTP y WebSocket.
- `database.py`: acceso a datos y Stored Procedures.
- `websocket_manager.py`: conexiones por vehículo.
- `settings.py`: configuración.
- `requirements.txt`: dependencias.

Los servicios futuros para vehículos, comandos, estado y DEMO se agregarán como módulos separados reutilizando el acceso a datos. El despliegue público no corresponde a esta práctica.

---

## Examen 2.6 — Auditoría de Stored Procedures (Versión 1)

### Fuentes de información

Los parámetros de entrada de los Stored Procedures se obtuvieron de
dos fuentes independientes:

- **CSV exportado de MySQL Workbench** (`mysql.csv`): contiene los
  parámetros registrados en `information_schema.PARAMETERS` para 20 de
  los 21 procedimientos. Los procedimientos sin parámetros de entrada
  no aparecen en esa vista; por eso `sp_obtener_vehiculos` está ausente.
- **Prueba funcional**: `GET /api/vehiculos` devolvió HTTP 200 con
  registros reales (vehículos con ID 6 y 7), confirmando que el
  procedimiento existe y responde. Su lista de parámetros internos
  no fue verificada directamente.

La tabla siguiente compara los parámetros del CSV con los argumentos
que el código pasa al invocar cada procedimiento. Esta comparación
no garantiza la compatibilidad en ejecución: el comportamiento interno
de los procedimientos que no han sido probados individualmente contra
RDS todavía no está validado.

### Parámetros de los 21 Stored Procedures

| Stored Procedure | Parámetros de entrada (fuente: CSV de RDS) | Verificación |
|---|---|---|
| `sp_obtener_vehiculos` | *(sin parámetros en CSV)* | Funcional: HTTP 200 |
| `sp_obtener_vehiculo` | `IN p_id_vehiculo INT` | CSV |
| `sp_crear_vehiculo` | `IN p_nombre VARCHAR, IN p_modelo VARCHAR, IN p_descripcion VARCHAR, IN p_microcontrolador VARCHAR` | CSV |
| `sp_actualizar_vehiculo` | `IN p_id_vehiculo INT, IN p_nombre VARCHAR, IN p_modelo VARCHAR, IN p_descripcion VARCHAR, IN p_microcontrolador VARCHAR, IN p_activo TINYINT` | CSV |
| `sp_eliminar_vehiculo` | `IN p_id_vehiculo INT` | CSV |
| `sp_crear_comando` | `IN p_id_vehiculo INT, IN p_nombre VARCHAR, IN p_codigo INT, IN p_descripcion VARCHAR` | CSV |
| `sp_obtener_comandos_vehiculo` | `IN p_id_vehiculo INT` | CSV |
| `sp_actualizar_comando` | `IN p_id_comando INT, IN p_nombre VARCHAR, IN p_codigo INT, IN p_descripcion VARCHAR, IN p_activo TINYINT` | CSV |
| `sp_eliminar_comando` | `IN p_id_comando INT` | CSV |
| `sp_crear_demo` | `IN p_id_vehiculo INT, IN p_nombre VARCHAR, IN p_descripcion VARCHAR` | CSV |
| `sp_obtener_demos` | `IN p_id_vehiculo INT` | CSV |
| `sp_actualizar_demo` | `IN p_id_demo INT, IN p_nombre VARCHAR, IN p_descripcion VARCHAR, IN p_activo TINYINT` | CSV |
| `sp_eliminar_demo` | `IN p_id_demo INT` | CSV |
| `sp_agregar_movimiento_demo` | `IN p_id_demo INT, IN p_id_comando INT, IN p_orden INT, IN p_duracion_ms INT, IN p_pausa_ms INT` | CSV |
| `sp_obtener_movimientos_demo` | `IN p_id_demo INT` | CSV |
| `sp_actualizar_movimiento_demo` | `IN p_id_demo_movimiento INT, IN p_id_comando INT, IN p_orden INT, IN p_duracion_ms INT, IN p_pausa_ms INT` | CSV |
| `sp_eliminar_movimiento_demo` | `IN p_id_demo_movimiento INT` | CSV |
| `sp_actualizar_estado` | `IN p_id_vehiculo INT, IN p_id_comando INT, IN p_conectado TINYINT` | CSV |
| `sp_obtener_estado` | `IN p_id_vehiculo INT` | CSV |
| `sp_obtener_historial` | `IN p_id_vehiculo INT` | CSV |
| `sp_registrar_historial` | `IN p_id_vehiculo INT, IN p_id_comando INT, IN p_origen VARCHAR` | CSV |

### Nota sobre `sp_actualizar_estado` en movimientos individuales

En `database.py`, `registrar_movimiento()` llama a `sp_actualizar_estado`
pasando como `p_conectado` el valor previo almacenado en la base de datos,
no un valor nuevo proporcionado por el cliente. Este comportamiento se
conserva intencionalmente hasta que los requisitos exactos del examen
lo precisen.

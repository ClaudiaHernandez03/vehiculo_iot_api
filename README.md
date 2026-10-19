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

---

## Examen 2.6 — Arquitectura y Tecnologías de IoT

Esta sección complementa la práctica 2.5 integrando la ejecución temporizada de demostraciones (DEMO), control de concurrencia, cancelación, eventos WebSocket en tiempo real y la preparación para el despliegue persistente en Amazon EC2 con HTTPS y WSS.

### 1. Endpoints de Demostración (DEMO)

| Método | Ruta | Código HTTP | Descripción |
|---|---|:---:|---|
| `POST` | `/api/demos/{id_demo}/ejecutar` | `202 Accepted` | Inicia en segundo plano la secuencia de pasos temporizados de la DEMO. Si el vehículo ya tiene una DEMO en curso, retorna `409 Conflict`. |
| `POST` | `/api/demos/{id_demo}/cancelar` | `200 OK` | Solicita la cancelación inmediata de la DEMO activa asociada al `id_demo`. |
| `DELETE` | `/api/demos/{id_demo}/ejecutar` | `200 OK` | Alias HTTP DELETE para cancelar la DEMO activa. |
| `GET` | `/api/vehiculos/{id_vehiculo}/demo/activa` | `200 OK` | Consulta si existe una DEMO en ejecución para el vehículo y sus datos. |
| `POST` | `/api/vehiculos/{id_vehiculo}/demo/cancelar` | `200 OK` | Cancela la DEMO activa buscando directamente por el ID del vehículo. |

### 2. Eventos WebSocket (`/ws/vehiculos/{id_vehiculo}`)

Todos los clientes WebSocket conectados a un mismo vehículo reciben en tiempo real los eventos correspondientes en formato JSON:

- **`estado_inicial` (al conectar el cliente):**
  Envía el último estado del vehículo registrado en Amazon RDS (`sp_obtener_estado`).
- **`movimiento_individual` (`POST /api/movimientos`):**
  Notifica a todos los clientes conectados tras confirmarse la transacción en RDS (`sp_actualizar_estado` + `sp_registrar_historial`).
- **`demo_inicio` (`POST /api/demos/{id}/ejecutar`):**
  Indica el arranque de la demostración, el nombre y el número total de pasos a ejecutar.
- **`demo_paso`:**
  Emitido en cada paso de la DEMO respetando el orden (`orden`), duración (`duracion_ms`) y pausa (`pausa_ms`). Actualiza el estado en RDS pero **no** se registra en el historial individual.
- **`demo_fin`:**
  Notifica la culminación exitosa de todos los pasos de la DEMO.
- **`demo_cancelada` (`POST /api/demos/{id}/cancelar`):**
  Notifica la interrupción anticipada de la DEMO indicando el paso en el que se detuvo.

### 3. Instrucciones de Ejecución y Pruebas Locales

1. **Iniciar el servidor localmente:**
   ```powershell
   .\.venv\Scripts\Activate.ps1
   uvicorn main:app --reload --host 127.0.0.1 --port 8000
   ```
2. **Pruebas interactivas en Swagger UI:**
   - Abrir en el navegador: `http://127.0.0.1:8000/docs`
   - Si configuraste `API_KEY` en `.env`, usa el botón **Authorize** o incluye el header `x-api-key`.
3. **Pruebas automatizadas del proyecto:**
   Se incluyen dos suites para verificar la lógica y los endpoints sin pasos manuales:
   ```powershell
   # Suite de integración (Base de datos RDS, DemoManager, WebSocket y transacciones)
   python test_examen26.py

   # Suite de integración HTTP (servidor Uvicorn, concurrencia 409 y cancelación)
   python test_http_endpoints.py
   ```

### 4. Despliegue en Amazon EC2 (Windows Server, Caddy, HTTPS y WSS)

La API se encuentra desplegada en una instancia de **Amazon EC2 con Windows Server** (`WebServer`), configurada con ejecución persistente en segundo plano y proxy inverso con certificado TLS automático:

- **Directorio en el servidor:** `C:\Users\Administrator\Documents\vehiculo_iot_api`
- **Servicio persistente de Windows (`VehiculoIoTAPI`):**
  Registrado mediante **NSSM** para ejecutar Uvicorn sobre el entorno virtual del proyecto escuchando únicamente en la interfaz local (`127.0.0.1:8000`), con reinicio automático ante fallos y arranque automático junto con el sistema operativo (`StartMode = Auto`, estado `Running`).
- **Proxy inverso HTTPS y WSS (`Caddy`):**
  Configurado como servicio de Windows (`StartMode = Auto`, estado `Running`) para atender el dominio `vehiculo-iot-api.duckdns.org` en los puertos públicos `80` (HTTP) y `443` (HTTPS/WSS) y redirigir el tráfico hacia `127.0.0.1:8000`.
- **Seguridad de red (AWS Security Group):**
  Se cerró la exposición pública directa del puerto `8000`, manteniendo abiertos únicamente los puertos web necesarios (`80` y `443`) hacia el proxy inverso.
- **Direcciones públicas de verificación:**
  - Documentación Swagger UI: `https://vehiculo-iot-api.duckdns.org/docs`
  - Estado del servicio: `https://vehiculo-iot-api.duckdns.org/health`
  - WebSocket seguro (Excavadora IoT): `wss://vehiculo-iot-api.duckdns.org/ws/vehiculos/6`
- **Reporte detallado y evidencias del despliegue:**
  Disponible en [`docs/despliegue_aws/Reporte_Despliegue_API_IoT_AWS.docx`](docs/despliegue_aws/Reporte_Despliegue_API_IoT_AWS.docx).
- **Archivos adicionales para entornos Linux:**
  También se incluyen en el repositorio `vehiculo_iot_api.service` (systemd), `nginx.conf` y `deploy_ec2.sh` como referencia para despliegues sobre distribuciones Linux.

### 5. Lista de Evidencias Requeridas para el Examen

Para documentar la entrega en el reporte en PDF, captura las siguientes pantallas:

1. **Evidencia 1 — Swagger UI:**
   - Captura de `http://127.0.0.1:8000/docs` (o URL pública HTTPS) mostrando los tags `Demostraciones`, `Movimientos`, `Vehículos`, `Comandos`, `Estado` e `Historial`.
2. **Evidencia 2 — POST individual con dos clientes WebSocket simultáneos:**
   - Dos pestañas de Thunder Client conectadas a `ws://127.0.0.1:8000/ws/vehiculos/6` (ambas mostrando `estado_inicial`).
   - Una tercera pestaña enviando `POST /api/movimientos` con cuerpo:
     `{"id_vehiculo": 6, "id_comando": 38, "origen": "Thunder Client"}`.
   - Ambas pestañas WebSocket mostrando la llegada simultánea del evento `movimiento_individual`.
3. **Evidencia 3 — GET Historial (últimos 10 registros):**
   - Petición `GET /api/vehiculos/6/historial` en Thunder Client mostrando los registros ordenados descendente por fecha.
4. **Evidencia 4 — DEMO temporizada y control de concurrencia:**
   - Petición `POST /api/demos/5/ejecutar` recibiendo código `202 Accepted`.
   - Clientes WebSocket recibiendo `demo_inicio` y los eventos `demo_paso` en orden.
   - Envío inmediato de una segunda petición `POST /api/demos/5/ejecutar` mostrando la respuesta `409 Conflict`.
5. **Evidencia 5 — Cancelación de DEMO y verificación de historial:**
   - Petición `POST /api/demos/5/cancelar` recibiendo código `200 OK` y evento `demo_cancelada` en WebSocket.
   - Petición `GET /api/vehiculos/6/historial` comprobando que los pasos de la DEMO **no** agregaron registros al historial individual.
6. **Evidencia 6 — Servicio persistente (systemd):**
   - Ejecución de `test_examen26.py` con resultado `8/8 PASS` o estado de `systemctl status vehiculo_iot_api`.

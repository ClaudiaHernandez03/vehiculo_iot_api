# API Vehículo IoT

API REST y WebSocket desarrollada con **Python**, **FastAPI** y **Programación Orientada a Objetos (POO)**, conectada a la base de datos MySQL `vehiculo_iot` alojada en **Amazon RDS** y publicada en **Amazon EC2** mediante **HTTPS** y **WSS**.

---

## Versiones del Proyecto

> **Distinción entre la versión declarada en FastAPI y las versiones de Git:**
> - **Versión declarada en FastAPI (`2.6.0`):** configurada en `main.py`, `/health` y `/openapi.json`; indica la versión del servicio web para la evaluación 2.6.
> - **Versiones evolutivas en Git (`v1.0` a `v4.0`):** identifican en el historial de commits las cuatro etapas en las que se construyó y desplegó el proyecto (listas para etiquetarse en Git).

| Versión | Cambios principales | Estado |
| :--- | :--- | :--- |
| **v1.0 — Revisión y preparación** | Configuración inicial del proyecto en Python con POO (`settings.py`, `database.py`), variables de entorno (`.env.example`) y script de prueba (`probar_conexion.py`). Sirve para establecer y validar la conexión segura con la base de datos `vehiculo_iot` en Amazon RDS. | Completado |
| **v2.0 — API y procedimientos almacenados** | Integración en `database.py` y `main.py` de los 21 Stored Procedures de MySQL (vehículos, comandos, estado, historial, demos y movimientos demo) y su tabla de parámetros. Sirve para administrar el catálogo completo y registrar movimientos individuales en una sola transacción. | Completado |
| **v3.0 — Comunicación IoT y WebSocket** | Implementación del canal WebSocket por vehículo (`websocket_manager.py`) y del coordinador de demostraciones (`demo_manager.py`). Sirve para enviar el último estado al conectar, notificar movimientos individuales en tiempo real y ejecutar secuencias DEMO temporizadas sin ensuciar el historial individual, bloqueando ejecuciones simultáneas (`409 Conflict`) y permitiendo su cancelación. | Completado |
| **v4.0 — Despliegue final en AWS** | Publicación en Amazon EC2 (Windows Server) con servicio automático (`VehiculoIoTAPI` vía NSSM), proxy inverso Caddy con HTTPS y WSS, interfaz Swagger UI en tema oscuro (`static/swagger-custom.css`), código comentado y reporte técnico en [`docs/despliegue_aws/Reporte_Despliegue_API_IoT_AWS.docx`](docs/despliegue_aws/Reporte_Despliegue_API_IoT_AWS.docx). Sirve para operar la API públicamente de forma continua sin depender de Visual Studio Code. | Completado |

---

## Arquitectura del Proyecto (POO)

- `main.py`: rutas HTTP, validaciones Pydantic, configuración de Swagger UI y canal WebSocket.
- `database.py`: clase `Database` encargada de la conexión a Amazon RDS, transacciones (`COMMIT` / `ROLLBACK`) e invocación de los 21 Stored Procedures.
- `websocket_manager.py`: clase `ConnectionManager` que administra las conexiones WebSocket separadas por `id_vehiculo`.
- `demo_manager.py`: clase `DemoManager` que coordina la ejecución asíncrona temporizada de secuencias DEMO, el control de concurrencia y la cancelación.
- `settings.py`: clase `Settings` que carga las variables de configuración desde `.env`.
- `static/swagger-custom.css`: hoja de estilos en tema oscuro para la documentación interactiva en `/docs`.
- `docs/despliegue_aws/Reporte_Despliegue_API_IoT_AWS.docx`: documentación y evidencias del despliegue en Amazon EC2.

---

## Inventario y Parámetros de los 21 Stored Procedures (`vehiculo_iot`)

Los parámetros de entrada de los 21 procedimientos almacenados fueron verificados con el esquema de `information_schema.PARAMETERS` en Amazon RDS y pruebas funcionales desde la API:

| Stored Procedure | Parámetros de entrada | Endpoint asociado |
|---|---|---|
| `sp_obtener_vehiculos` | *(sin parámetros de entrada)* | `GET /api/vehiculos` |
| `sp_obtener_vehiculo` | `IN p_id_vehiculo INT` | `GET /api/vehiculos/{id_vehiculo}` |
| `sp_crear_vehiculo` | `IN p_nombre VARCHAR, IN p_modelo VARCHAR, IN p_descripcion VARCHAR, IN p_microcontrolador VARCHAR` | `POST /api/vehiculos` |
| `sp_actualizar_vehiculo` | `IN p_id_vehiculo INT, IN p_nombre VARCHAR, IN p_modelo VARCHAR, IN p_descripcion VARCHAR, IN p_microcontrolador VARCHAR, IN p_activo TINYINT` | `PUT /api/vehiculos/{id_vehiculo}` |
| `sp_eliminar_vehiculo` | `IN p_id_vehiculo INT` | `DELETE /api/vehiculos/{id_vehiculo}` |
| `sp_crear_comando` | `IN p_id_vehiculo INT, IN p_nombre VARCHAR, IN p_codigo INT, IN p_descripcion VARCHAR` | `POST /api/comandos` |
| `sp_obtener_comandos_vehiculo` | `IN p_id_vehiculo INT` | `GET /api/vehiculos/{id_vehiculo}/comandos` |
| `sp_actualizar_comando` | `IN p_id_comando INT, IN p_nombre VARCHAR, IN p_codigo INT, IN p_descripcion VARCHAR, IN p_activo TINYINT` | `PUT /api/comandos/{id_comando}` |
| `sp_eliminar_comando` | `IN p_id_comando INT` | `DELETE /api/comandos/{id_comando}` |
| `sp_crear_demo` | `IN p_id_vehiculo INT, IN p_nombre VARCHAR, IN p_descripcion VARCHAR` | `POST /api/demos` |
| `sp_obtener_demos` | `IN p_id_vehiculo INT` | `GET /api/vehiculos/{id_vehiculo}/demos` |
| `sp_actualizar_demo` | `IN p_id_demo INT, IN p_nombre VARCHAR, IN p_descripcion VARCHAR, IN p_activo TINYINT` | `PUT /api/demos/{id_demo}` |
| `sp_eliminar_demo` | `IN p_id_demo INT` | `DELETE /api/demos/{id_demo}` |
| `sp_agregar_movimiento_demo` | `IN p_id_demo INT, IN p_id_comando INT, IN p_orden INT, IN p_duracion_ms INT, IN p_pausa_ms INT` | `POST /api/demos/{id_demo}/movimientos` |
| `sp_obtener_movimientos_demo` | `IN p_id_demo INT` | `GET /api/demos/{id_demo}/movimientos` |
| `sp_actualizar_movimiento_demo` | `IN p_id_demo_movimiento INT, IN p_id_comando INT, IN p_orden INT, IN p_duracion_ms INT, IN p_pausa_ms INT` | `PUT /api/movimientos-demo/{id_demo_movimiento}` |
| `sp_eliminar_movimiento_demo` | `IN p_id_demo_movimiento INT` | `DELETE /api/movimientos-demo/{id_demo_movimiento}` |
| `sp_actualizar_estado` | `IN p_id_vehiculo INT, IN p_id_comando INT, IN p_conectado TINYINT` | `PUT /api/vehiculos/{id_vehiculo}/estado` |
| `sp_obtener_estado` | `IN p_id_vehiculo INT` | `GET /api/vehiculos/{id_vehiculo}/estado` |
| `sp_obtener_historial` | `IN p_id_vehiculo INT` | `GET /api/vehiculos/{id_vehiculo}/historial` |
| `sp_registrar_historial` | `IN p_id_vehiculo INT, IN p_id_comando INT, IN p_origen VARCHAR` | `POST /api/historial` |

---

## Operaciones Principales y Reglas de Negocio

### 1. Movimientos individuales e historial reciente
- **POST de movimiento individual (`POST /api/movimientos`):**
  Valida que el vehículo y el comando existan, estén activos y correspondan entre sí. Ejecuta `sp_actualizar_estado` y `sp_registrar_historial` en **una sola transacción** y notifica por WebSocket únicamente después del `COMMIT`.
  ```json
  {
    "id_vehiculo": 6,
    "id_comando": 38,
    "origen": "Thunder Client"
  }
  ```
- **GET de historial (`GET /api/vehiculos/{id_vehiculo}/historial`):**
  Ejecuta `sp_obtener_historial` y devuelve como máximo los **10 movimientos individuales más recientes**, ordenados del más reciente al más antiguo.

### 2. Endpoints de Demostración (DEMO)

| Método | Ruta | Código HTTP | Descripción |
|---|---|:---:|---|
| `POST` | `/api/demos/{id_demo}/ejecutar` | `202 Accepted` | Inicia en segundo plano la secuencia de pasos temporizados de la DEMO. Si el vehículo ya tiene una DEMO en curso, retorna `409 Conflict`. |
| `POST` | `/api/demos/{id_demo}/cancelar` | `200 OK` | Solicita la cancelación inmediata de la DEMO activa asociada al `id_demo`. |
| `DELETE` | `/api/demos/{id_demo}/ejecutar` | `200 OK` | Alias HTTP DELETE para cancelar la DEMO activa. |
| `GET` | `/api/vehiculos/{id_vehiculo}/demo/activa` | `200 OK` | Consulta si existe una DEMO en ejecución para el vehículo y sus datos. |
| `POST` | `/api/vehiculos/{id_vehiculo}/demo/cancelar` | `200 OK` | Cancela la DEMO activa buscando directamente por el ID del vehículo. |

### 3. Eventos WebSocket (`/ws/vehiculos/{id_vehiculo}`)

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
- **`ping` / `pong`:**
  Si el cliente envía `{"tipo": "ping"}`, el servidor responde `{"tipo": "pong"}` para comprobar que el canal sigue activo.

---

## Instrucciones de Ejecución y Pruebas Locales

1. **Preparar el entorno en Windows / VS Code:**
   ```powershell
   py -m venv .venv
   .\.venv\Scripts\Activate.ps1
   pip install -r requirements.txt
   Copy-Item .env.example .env
   ```
   *(Completa `.env` con las credenciales reales de Amazon RDS. No publiques `.env` ni compartas contraseñas).*

2. **Iniciar el servidor localmente:**
   ```powershell
   uvicorn main:app --reload --host 127.0.0.1 --port 8000
   ```
   - Swagger UI: `http://127.0.0.1:8000/docs`
   - Salud del servicio: `http://127.0.0.1:8000/health`

3. **Ejecutar pruebas automatizadas del proyecto:**
   ```powershell
   # Suite de integración (Base de datos RDS, DemoManager, WebSocket y transacciones)
   python test_examen26.py

   # Suite de integración HTTP (servidor Uvicorn, concurrencia 409 y cancelación)
   python test_http_endpoints.py
   ```

---

## Despliegue en Amazon EC2 (Windows Server, Caddy, HTTPS y WSS)

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

---

## Lista de Evidencias para Evaluación

1. **Evidencia 1 — Swagger UI:**
   Captura de `/docs` mostrando los grupos `Vehículos`, `Comandos`, `Demostraciones`, `Movimientos demo`, `Estado`, `Historial` y `Movimientos`.
2. **Evidencia 2 — POST individual con dos clientes WebSocket simultáneos:**
   Dos pestañas de Thunder Client conectadas al WebSocket del vehículo (ambas mostrando `estado_inicial`) y una tercera pestaña enviando `POST /api/movimientos`, recibiendo simultáneamente el evento `movimiento_individual`.
3. **Evidencia 3 — GET Historial (últimos 10 registros):**
   Petición `GET /api/vehiculos/6/historial` mostrando como máximo los 10 movimientos individuales más recientes en orden descendente.
4. **Evidencia 4 — DEMO temporizada y control de concurrencia:**
   Petición `POST /api/demos/5/ejecutar` con respuesta `202 Accepted`, recepción de `demo_inicio` y `demo_paso` en orden por WebSocket, y rechazo `409 Conflict` ante una segunda ejecución simultánea.
5. **Evidencia 5 — Cancelación de DEMO y verificación de historial:**
   Petición `POST /api/demos/5/cancelar` recibiendo `200 OK` y evento `demo_cancelada`, comprobando con `GET /api/vehiculos/6/historial` que los pasos de la DEMO no se agregaron al historial individual.

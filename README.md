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

- `main.py`: inicialización de FastAPI, `CORSMiddleware`, configuración de Swagger UI y controladores de rutas HTTP y WebSocket.
- `models.py`: clases de esquemas Pydantic (`VehiculoCrear`, `VehiculoActualizar`, `ComandoCrear`, `ComandoActualizar`, `DemoCrear`, `DemoActualizar`, `MovimientoDemo`, `EstadoActualizar`, `MovimientoRequest`, `HistorialCrear`).
- `services.py`: clase `VehiculoIoTService` (lógica de negocio, coordinación transaccional y emisión de eventos WebSocket) y clase `ApiKeyGuard` (dependencia de seguridad).
- `database.py`: clase `Database` encargada de la conexión a Amazon RDS, ciclo de vida de cursores/transacciones (`_cursor_session`), verificación segura de conexión (`verificar_conexion`) e invocación de los 21 Stored Procedures.
- `websocket_manager.py`: clase `ConnectionManager` que administra las conexiones WebSocket individuales (`/ws/vehiculos/{id_vehiculo}`) y globales (`/ws`, `/ws/vehiculos`), broadcast sin duplicados y protocolo `ping`/`pong`.
- `demo_manager.py`: clase `DemoManager` que coordina la ejecución asíncrona temporizada de secuencias DEMO, el control de concurrencia (`409 Conflict`) y la cancelación.
- `exceptions.py`: jerarquía de excepciones de dominio (`VehiculoIoTError` y subclases) y clase `ExceptionManager` para el manejo centralizado de errores HTTP y MySQL.
- `settings.py`: clase `Settings` que centraliza y valida las variables de entorno (`.env`), configuración de RDS y lista explícita de orígenes CORS.
- `static/swagger-custom.css`: hoja de estilos en tema oscuro para la documentación interactiva en `/docs`.
- `docs/despliegue_aws/Reporte_Despliegue_API_IoT_AWS.docx`: documentación y evidencias del despliegue en Amazon EC2.

---

## Configuración de CORS y GitHub Pages (Práctica 4.1)

La API utiliza `CORSMiddleware` en `main.py` alimentado por `Settings` (`settings.py`) con una **lista explícita de orígenes autorizados** (sin usar comodines `*` y con `allow_credentials=False` por defecto):

- **Orígenes locales preconfigurados:** `http://localhost:3000`, `http://127.0.0.1:3000`, `http://localhost:5173`, `http://127.0.0.1:5173`, `http://localhost:5500`, `http://127.0.0.1:5500`, `http://localhost:8000`, `http://127.0.0.1:8000`, `http://localhost:8080`, `http://127.0.0.1:8080`.
- **Origen propio HTTPS en producción:** `https://vehiculo-iot-api.duckdns.org`.
- **Dónde agregar la URL definitiva de GitHub Pages cuando se conozca:**
  1. En el archivo `.env` del servidor EC2 (recomendado), definir la variable:
     ```env
     FRONTEND_GITHUB_PAGES_URL=https://<tu-usuario>.github.io
     ```
     *(Nota: el encabezado `Origin` que envía el navegador desde GitHub Pages incluye únicamente `https://<tu-usuario>.github.io`, sin el nombre del repositorio ni barra diagonal final. `Settings._normalize_origin()` normaliza automáticamente la URL si se incluye ruta o `/` final).*
  2. Alternativamente, en `settings.py`, asignar la constante `GITHUB_PAGES_ORIGIN_PLACEHOLDER = "https://<tu-usuario>.github.io"` o agregar orígenes adicionales separados por comas en la variable de entorno `CORS_ORIGINS`.
- **Métodos permitidos:** `GET`, `POST`, `PUT`, `DELETE`, `OPTIONS`.
- **Encabezados permitidos:** `Accept`, `Content-Type`, `Origin`, `X-Requested-With`, `X-API-Key`, `Authorization`.

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

### 1. Movimientos individuales sostenidos (`POST /api/movimientos`) e historial reciente
- **Movimientos sostenidos sin apagado automático de 1500 ms:**
  - Al enviar `POST /api/movimientos`, la API valida vehículo y comando, registra el cambio en una sola transacción (`sp_actualizar_estado` + `sp_registrar_historial`), reserva una época exclusiva (`orden_id = epoca_orden * 100`) y publica el evento `movimiento_individual` con `"mantener_activo": true`.
  - El movimiento permanece activo mientras el controlador propietario mantenga su sesión vigente (`sesion_control`), hasta recibir una orden explícita de `DETENER` (`POST /api/vehiculos/{id_vehiculo}/detener`) o una nueva orden de movimiento que sustituya a la anterior.
  ```json
  {
    "id_vehiculo": 1,
    "id_comando": 9,
    "origen": "API",
    "sesion_control": "op-cabina-1"
  }
  ```
- **Sesión de control y Watchdog de seguridad (`CONTROL_WATCHDOG_SECONDS = 15.0`):**
  - El navegador propietario envía `{"tipo": "ping", "rol": "controlador", "sesion_control": "op-cabina-1"}` cada 3–5 segundos mientras mantiene presionado el control.
  - Solo el controlador propietario puede renovar la sesión; las pestañas observadoras (`rol="observador"`) no renuevan el movimiento ni lo detienen al desconectarse.
  - Si el controlador propietario cierra su conexión WebSocket o deja de enviar `ping` durante `15.0` segundos, el servidor dispara automáticamente `DETENER`.
- **GET de historial (`GET /api/vehiculos/{id_vehiculo}/historial`):**
  Ejecuta `sp_obtener_historial` y devuelve como máximo los **10 movimientos individuales más recientes**, ordenados del más reciente al más antiguo.

### 2. Endpoint de Detención Prioritaria (`POST /api/vehiculos/{id_vehiculo}/detener`)

- **Ruta:** `POST /api/vehiculos/{id_vehiculo}/detener`
- **Prioridad máxima:** Invalida inmediatamente cualquier movimiento en vuelo anterior, cancela y espera el cierre de cualquier DEMO activa, localiza o registra de forma idempotente el comando `Detener` (`codigo = 0`) en RDS (sin usar un ID fijo), reserva una época exclusiva (`orden_id = epoca_orden * 100`), publica el evento `detener` por WebSocket y espera hasta `esperar_confirmacion_ms` (por defecto `1500` ms) el ACK físico del ESP8266.
- **Tolerancia a caídas de RDS:** Si Amazon RDS no responde al momento de solicitar `DETENER`, la API publica igualmente el evento `detener` por WebSocket hacia el ESP8266, marca `reconciliacion_rds_pendiente = true` y bloquea nuevos movimientos hasta que RDS vuelva a estar en línea y se reconcilie el estado a `Detener`.
- **Petición (cuerpo opcional):**
  ```json
  {
    "origen": "API",
    "esperar_confirmacion_ms": 1500,
    "sesion_control": "op-cabina-1"
  }
  ```
- **Respuesta (diferencia explícitamente recepción HTTP, persistencia en RDS, publicación WS y confirmación física del ESP8266):**
  ```json
  {
    "ok": true,
    "mensaje": "Detención física confirmada por el ESP8266 y registrada en RDS",
    "id_vehiculo": 1,
    "orden_id": 2500,
    "orden_recibida": true,
    "registrada_en_rds": true,
    "orden_publicada_ws": true,
    "detencion_fisica_confirmada": true,
    "reconciliacion_rds_pendiente": false,
    "demo_cancelada": false,
    "estado_orden": {
      "orden_recibida": true,
      "registrada_en_rds": true,
      "demo_cancelada": false,
      "orden_publicada_ws": true,
      "clientes_vehiculo_notificados": 1,
      "clientes_global_notificados": 1,
      "detencion_fisica_confirmada": true,
      "reconciliacion_rds_pendiente": false,
      "confirmacion_esp8266": {
        "tipo": "detencion_confirmada",
        "id_vehiculo": 1,
        "orden_id": 2500,
        "codigo": 0,
        "salidas_activas": 0,
        "estado_fisico": "detenido"
      }
    }
  }
  ```
  *(Nota: `detencion_fisica_confirmada` es `false` siempre que no se reciba el ACK `detencion_confirmada` del ESP8266 con el `id_vehiculo` y `orden_id` exactos).*

### 3. Endpoints de Demostración (DEMO — Máximo 4 pasos)

Las secuencias DEMO están limitadas a un **máximo de 4 pasos (`MAX_PASOS_DEMO = 4`)** con numeración obligatoria en base 1 (**`1, 2, 3, 4`**) y sin órdenes duplicadas:
- Al agregar (`POST /api/demos/{id_demo}/movimientos`), actualizar (`PUT /api/movimientos-demo/{id_demo_movimiento}`) o ejecutar (`POST /api/demos/{id_demo}/ejecutar`), cualquier intento de superar los 4 pasos o duplicar un número de `orden` se rechaza explícitamente con **HTTP `400 Bad Request`** (sin truncamiento silencioso).
- Cada DEMO reserva una época exclusiva (`epoca_orden`) y asigna subórdenes deterministas:
  - Paso 1..4: subórdenes impares `1, 3, 5, 7`.
  - Pausa 1..4 (cuando `pausa_ms > 0`): subórdenes pares `2, 4, 6, 8` (emitiendo orden de apagado `codigo = 0`).
  - Cierre (`demo_fin` / `demo_cancelada`): suborden `9` (`codigo = 0`, `accion_salidas = "DETENER"`).
  - Orden explícita de apagado final: suborden `10` (`tipo = "detener"`, `codigo = 0`).

| Método | Ruta | Código HTTP | Descripción |
|---|---|:---:|---|
| `POST` | `/api/demos/{id_demo}/ejecutar` | `202 Accepted` | Inicia en segundo plano la secuencia de hasta 4 pasos de la DEMO. Si ya hay una en curso retorna `409 Conflict`; si supera 4 pasos retorna `400 Bad Request`. |
| `POST` | `/api/demos/{id_demo}/cancelar` | `200 OK` | Cancela la DEMO activa, espera el término de la tarea y apaga las salidas (`sub_orden = 9` y `10`). |
| `DELETE` | `/api/demos/{id_demo}/ejecutar` | `200 OK` | Alias HTTP DELETE para cancelar la DEMO activa. |
| `GET` | `/api/vehiculos/{id_vehiculo}/demo/activa` | `200 OK` | Consulta si existe una DEMO en ejecución para el vehículo y sus datos. |
| `POST` | `/api/vehiculos/{id_vehiculo}/demo/cancelar` | `200 OK` | Cancela la DEMO activa buscando directamente por el ID del vehículo. |

### 4. Secuencia `orden_id`, Persistencia `.orden_seq_state.json` y Límite `uint32_t`

- **Fórmula:** `orden_id = (epoca_orden * 100) + sub_orden`.
- **Persistencia y sincronización:** Se almacena atómicamente en `.orden_seq_state.json` con suma de verificación `SHA-256` y bloqueo de archivo del sistema operativo (`msvcrt.locking` / `fcntl.flock`), sincronizándose con `MAX(id_historial)` de Amazon RDS.
- **Límite `uint32_t` (`4294967295`):** Antes de emitir cualquier orden se verifica que `orden_id <= 4294967295`. Jamás se reinicia automáticamente a `1`; si se alcanza el límite o el archivo `.orden_seq_state.json` se corrompe (o se pierden simultáneamente el archivo local y la conexión a RDS), el vehículo entra en **estado seguro**, envía `DETENER` al ESP8266 y bloquea nuevos movimientos hasta realizar una resincronización controlada.
- **Restricción de ejecución de Uvicorn:** Dado que las conexiones WebSocket activas (`ConnectionManager`) y las tareas `asyncio.Task` de `DemoManager` residen en la memoria del proceso Python, Uvicorn debe ejecutarse con **un único proceso worker (`--workers 1`)**, tal como está configurado en `vehiculo_iot_api.service` y en el servicio NSSM de EC2.

### 5. Canales y Eventos WebSocket (`/ws/vehiculos/{id_vehiculo}` y `/ws/vehiculos`)

La API ofrece dos rutas WebSocket complementarias:
- **Canal individual (`/ws/vehiculos/{id_vehiculo}`):** admite parámetros opcionales `?rol=controlador|observador|esp8266&sesion_control=<id>`. Recibe `estado_inicial` (con `"activar_salidas": false`) y los eventos en tiempo real del vehículo.
- **Canal global (`/ws/vehiculos`):** recibe `estado_inicial_global` y transmite en tiempo real todos los eventos posteriores de cualquier vehículo.

Eventos principales en formato JSON:
- **`detener` (API → ESP8266):**
  ```json
  {
    "tipo": "detener",
    "accion": "DETENER",
    "id_vehiculo": 1,
    "id_comando": 55,
    "comando": "Detener",
    "codigo": 0,
    "orden_id": 2500,
    "epoca_orden": 25,
    "sub_orden": 0,
    "origen": "API",
    "prioridad": "MAXIMA"
  }
  ```
- **`detencion_confirmada` (ACK ESP8266 → API):**
  ```json
  {
    "tipo": "detencion_confirmada",
    "id_vehiculo": 1,
    "orden_id": 2500,
    "codigo": 0,
    "salidas_activas": 0,
    "estado_fisico": "detenido"
  }
  ```
- **`estado_inicial`**, **`estado_inicial_global`**, **`movimiento_individual`**, **`estado_actualizado`**, **`historial_registrado`**, **`demo_inicio`**, **`demo_paso`**, **`demo_fin`**, **`demo_cancelada`** y **`ping` / `pong`**.

---

## Reglas de Integridad de Secuencia (`orden_id`), Resincronización y Arquitectura de Workers

1. **Separación estricta entre `id_historial` (RDS) y `orden_id` / `epoca_orden`:**
   - `id_historial` es la clave primaria `AUTO_INCREMENT` de `historial_comandos` en MySQL y **NO equivale** a `epoca_orden` ni a `orden_id`.
   - El esquema actual de Amazon RDS y los 21 Stored Procedures no almacenan `epoca_orden` ni `orden_id`. Por ello, `OrderSequenceManager` jamás iguala `epoca_orden = MAX(id_historial)`.
   - El archivo local `.orden_seq_state.json` almacena `ultimo_id_historial_rds` separado de `epoca_orden` y `ultimo_orden_id`. Si el archivo local se pierde existiendo historial previo en RDS (`MAX(id_historial) > 0`), o si `id_historial` en RDS difiere de `ultimo_id_historial_rds`, el vehículo entra en `estado_seguro_bloqueado = true` (`SECUENCIA_NO_RECUPERABLE_DESDE_RDS` o `DISCREPANCIA_ID_HISTORIAL_VS_ORDEN_ID`) e impide nuevos movimientos y DEMOs (`HTTP 503`).
   - **Cambio de esquema requerido en RDS para recuperación nativa sin archivo local:** agregar una columna `ultimo_orden_id BIGINT UNSIGNED NOT NULL DEFAULT 0` (y `epoca_orden INT UNSIGNED NOT NULL DEFAULT 0`) en `estado_vehiculo` (o una tabla `secuencia_control_vehiculo`) actualizada transaccionalmente.

2. **Límite `uint32_t` (`4294967295`) sin reinicio a 1:**
   - Al alcanzar `MAX_EPOCA_UINT32 = 42949672` (`orden_id` máximo `4294967210`), `OrderSequenceManager` bloquea el vehículo en `LIMITE_UINT32_ALCANZADO` **sin reiniciar `nueva_epoca = 1` ni `ultimo_stop_orden_id = 0`**, impidiendo reutilizar épocas o identificadores anteriores.

3. **Confirmación real de resincronización con ACK del ESP8266:**
   - Publicar una trama WebSocket `detener` con `"resync_secuencia": true` **no** desbloquea por sí sola el control.
   - `mark_resync_completed` exige recibir el ACK `detencion_confirmada` del ESP8266 con el `orden_id` exacto esperado, `codigo = 0`, `salidas_activas = false` (`0`) y `estado_fisico = "detenido"`. Mientras no llegue ese ACK válido, el control permanece bloqueado.

4. **Arquitectura de un solo worker (`--workers 1`):**
   - El bloqueo `.orden_seq_state.json.lock` protege únicamente la escritura del archivo local, pero **no garantiza por sí solo la seguridad de múltiples workers**, porque las conexiones WebSocket (`_connections`, `_global_connections`), sesiones de controlador y tareas de watchdog (`_active_movements`, `_watchdog_tasks`), barreras rápidas en memoria (`_stop_epoch`, `_vehicle_locks`), futuros de ACK pendientes (`_pending_stop_acks`) y tareas asíncronas de DEMO (`_active_tasks`, `_active_demo_epochs`) residen en la memoria local del proceso.
   - Por lo tanto, Uvicorn debe ejecutarse con `--workers 1` mientras no exista coordinación compartida externa para todos esos estados.

---

## Guía para Actualizar Posteriormente el Firmware del ESP8266 en Arduino IDE

*(Nota: El código fuente `.ino` del ESP8266 se administra en un proyecto de Arduino IDE separado y no forma parte de este repositorio).*

### 1. Mapeo Físico Confirmado (Conservar sin cambios)
- **Pines del ESP8266 NodeMCU:**
  - `D5` = Clock (`SH_CP`)
  - `D6` = Latch (`ST_CP`)
  - `D7` = Data (`DS`)
- **Primer `74HC595`:**
  - `Q1` = Avanzar (`codigo = 9`)
  - `Q2` = Girar torre izquierda (`codigo = 10`)
  - `Q3` = Retroceder (`codigo = 11`)
  - `Q4` = Girar torre derecha (`codigo = 12`)
- **Segundo `74HC595`:**
  - `Q0` = Mover brazo arriba (`codigo = 1`)
  - `Q1` = Girar izquierda / orugas (`codigo = 2`)
  - `Q2` = Mover brazo abajo (`codigo = 3`)
  - `Q3` = Girar derecha / orugas (`codigo = 4`)
  - `Q4` = Velocidad (`codigo = 5`)
  - `Q5` = Sonido (`codigo = 6`)
  - `Q6` = Demo (`codigo = 7`)
  - `Q7` = Luz (`codigo = 8`)

### 2. Cambios Requeridos en el Sketch `.ino` del ESP8266
1. **Eliminar el apagado automático de 1500 ms:**
   Suprimir en `loop()` la condición `if (movimientoActivo && millis() - tInicio >= 1500)` que apagaba los dos `74HC595`. Al recibir `movimiento_individual` o `demo_paso` con `codigo` entre `1` y `12`, escribir exclusivamente la máscara de ese comando en los dos `74HC595` (sustituyendo la salida anterior para impedir combinaciones incompatibles) y mantenerla activa hasta recibir `detener` (`codigo == 0`) o un nuevo comando con `orden_id` superior.
2. **Validación de `orden_id` (`uint32_t`) y prioridad de `DETENER`:**
   - Mantener `uint32_t ultimoOrdenId = 0;`.
   - Si llega `"tipo": "detener"` o `codigo == 0`:
     - Apagar inmediatamente todas las salidas de ambos `74HC595` (`0x00, 0x00` con pulso en `D6/Latch`).
     - Si `orden_id > ultimoOrdenId`, actualizar `ultimoOrdenId = orden_id`.
     - Enviar por WebSocket el ACK de confirmación física:
       ```json
       {"tipo":"detencion_confirmada","id_vehiculo":1,"orden_id":<orden_id>,"codigo":0,"salidas_activas":0,"estado_fisico":"detenido"}
       ```
   - Si llega `"tipo": "movimiento_individual"` o `"tipo": "demo_paso"` con `codigo > 0`:
     - Si `orden_id <= ultimoOrdenId`, **ignorar el mensaje** para evitar que paquetes atrasados reactiven salidas después de `DETENER`.
     - Si `orden_id > ultimoOrdenId`, actualizar `ultimoOrdenId = orden_id` y activar únicamente la salida correspondiente al nuevo `codigo`.
3. **Estado seguro ante pérdida de conexión y reconexión:**
   - En `WStype_DISCONNECTED` o si `WiFi.status() != WL_CONNECTED`: apagar inmediatamente ambos `74HC595` (`0x00, 0x00`).
   - En `WStype_CONNECTED`: iniciar con salidas apagadas (`0x00, 0x00`) y enviar `{"tipo":"identificar","rol":"esp8266","id_vehiculo":1}`.
   - Al recibir `"tipo": "estado_inicial"`, **nunca** activar salidas físicas (`activar_salidas` es `false`).

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

2. **Iniciar el servidor localmente (siempre con 1 worker para estado WebSocket en memoria):**
   ```powershell
   uvicorn main:app --host 127.0.0.1 --port 8000 --workers 1
   ```
   - Swagger UI: `http://127.0.0.1:8000/docs`
   - Salud del servicio: `http://127.0.0.1:8000/health`

3. **Ejecutar las suites de pruebas automatizadas (sobre capa aislada sin tocar RDS de producción):**
   ```powershell
   # Suite de 30 pruebas obligatorias de DETENER, secuencia, concurrencia, watchdog y DEMOs (4 pasos)
   python test_detener.py

   # Suite de integración base (Stored Procedures, DemoManager y WebSocket)
   python test_examen26.py

   # Suite de integración HTTP, CORS y X-API-Key
   python test_http_endpoints.py

   # Suite de WebSocket individual y global
   python test_websocket_global.py
   ```
   *(Nota: Las pruebas automatizadas en Python validan la lógica de la API y utilizan **Simulación de protocolo WebSocket (Mock de cliente ESP8266)** para el ACK. Las pruebas físicas de hardware requieren el ESP8266 con el firmware actualizado y la excavadora CAT-320 conectada).*

### Limitaciones Pendientes de Verificación en Etapas Posteriores
- **Verificación en Amazon RDS real:** Confirmar desde EC2 (o una IP autorizada en el Security Group de RDS) el `id_comando` real asignado al comando `Detener` (`codigo = 0`) del vehículo `1` en la tabla `comandos`.
- **Actualización física del firmware ESP8266:** Cargar en Arduino IDE los cambios documentados arriba para eliminar el timer de 1500 ms en el microcontrolador y emitir el ACK físico `detencion_confirmada`.

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

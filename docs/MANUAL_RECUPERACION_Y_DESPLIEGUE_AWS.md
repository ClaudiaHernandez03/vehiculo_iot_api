# Manual de Recuperación Integral y Despliegue en AWS – Proyecto Vehículo IoT (CAT-320)

Este documento describe de principio a fin la arquitectura recuperada, los hallazgos de auditoría de datos, el catálogo completo de endpoints REST y WebSocket, y el procedimiento paso a paso para reconstruir la infraestructura en **AWS Academy (EC2 + Amazon RDS MySQL + DuckDNS + Nginx SSL/WSS + Systemd)**.

---

## 1. Arquitectura General Recuperada

```
[Frontend Web (GitHub Pages / FastAPI /app/)]      [ESP8266 NodeMCU + 2x 74HC595]
                 |                                                |
     HTTPS REST + WSS (/ws/vehiculos/1)               WSS (/ws/vehiculos/1)
                 |                                                |
                 +-----------------------+------------------------+
                                         |
                                         v
                   [Amazon EC2 (t3.micro / t2.micro - us-east-1)]
                   ├── Nginx Reverse Proxy (Puertos 80 -> 443 TLS Let's Encrypt)
                   └── Systemd: vehiculo_iot_api.service (Uvicorn 127.0.0.1:8000)
                                         |
                             TCP 3306 (VPC / Security Group)
                                         |
                                         v
                   [Amazon RDS MySQL 8.x (Esquema: vehiculo_iot)]
                   ├── 6 Tablas relacionales con integridad referencial
                   └── 21 Procedimientos Almacenados (Stored Procedures)
```

---

## 2. Hallazgos de Auditoría y Conservación de Datos Originales

Durante la auditoría forense de las fuentes locales (`127.0.0.1:3306` MySQL 8.4.11, dumps previos, repositorio `vehiculo_iot_api`, frontend `practica_4_1_control_iot` y sketches de firmware ESP8266), se identificaron y resolvieron los siguientes puntos sin perder un solo registro:

1. **Recuperación de `DEMO 3` (`id_demo = 3`) y sus 5 movimientos huérfanos:**
   - En la instancia MySQL local existían `id_demo = 1` (*Inspeccion Completa de Torre y Brazo*) e `id_demo = 2` (*Recorrido y Giro de Orugas*), mientras que en `demo_movimientos` permanecían 5 registros (`id_demo_movimiento` `3, 8, 9, 10, 11`) apuntando a `id_demo = 3`.
   - Cruzando el script histórico `limpiar_base_excavadora.py` y el frontend `index.html`, se recuperó íntegramente el registro padre `id_demo = 3` (`nombre = 'Ciclo de Excavacion'`, `descripcion = 'Baja el brazo, sube el brazo, gira la torre y avanza'`, `activo = 1`), restableciendo el 100% de la integridad referencial.
2. **Conservación documentada de pasos con `orden = 4` en `demo_movimientos`:**
   - Cada una de las 3 demostraciones cuenta con 4 pasos originales (`id_demo_movimiento` `1..12`, órdenes `1, 2, 3, 4`) más un quinto registro adicional con `id_comando = 13` (`Detener`) y `orden = 4` (`id_demo_movimiento` `13, 14, 15`, creados el `2026-04-12 20:51:43`).
   - Siguiendo la regla estricta de **no eliminar registros automáticamente**, los 15 registros se conservan intactos en los scripts `00`, `02` y en la base de datos. En `sql/02_recuperacion_datos_y_migracion_idempotente.sql` queda documentado el caso.
3. **Estado de la tabla `comandos` (13 registros):**
   - Se conservan los 13 registros originales (`id_comando` `1..13`). Se documenta que en la copia local `id_comando = 3` tiene `nombre = 'Detener', codigo = 0` al igual que `id_comando = 13`, mientras que en el dump previo `id_comando = 3` correspondía a `Mover brazo abajo (codigo = 3)`. En `sql/02_recuperacion_datos_y_migracion_idempotente.sql` se incluye como comentario opcional la sentencia `UPDATE` por si el operador desea restituir `id_comando = 3` a `Mover brazo abajo (codigo = 3)`.
4. **Unificación y verificación del Firmware ESP8266 (`Excavadora_ESP8266_WSS.ino`):**
   - El sketch `Excavadora_WiFi.ino.ino` tenía invertidos algunos bits del primer `74HC595` respecto al mapeo físico confirmado en `sketch_oct29a.ino`.
   - Se respaldaron ambos archivos originales en `firmware/originales_respaldo/` y se consolidó `firmware/Excavadora_ESP8266_WSS/Excavadora_ESP8266_WSS.ino` respetando **exactamente** el mapeo físico confirmado:
     - `CLOCK = D5`, `LATCH = D6`, `DATA = D7`
     - **Primer 74HC595:** `Q1 = Avanzar (9)`, `Q2 = Girar torre izquierda (10)`, `Q3 = Retroceder (11)`, `Q4 = Girar torre derecha (12)`.
     - **Segundo 74HC595:** `Q0 = Mover brazo arriba (1)`, `Q1 = Girar izquierda (2)`, `Q2 = Mover brazo abajo (3)`, `Q3 = Girar derecha (4)`, `Q4 = Velocidad (5)`, `Q5 = Sonido (6)`, `Q6 = Demo (7)`, `Q7 = Luz (8)`.

---

## 3. Catálogo Completo de Endpoints (20 HTTP + 3 WebSocket)

### 3.1. Sistema y Documentación
| Método | Ruta | Auth (`X-API-Key`) | Descripción |
|---|---|---|---|
| `GET` | `/health` | No | Verificación de estado y versión del servicio (`2.6.0`). |
| `GET` | `/docs` | No | Interfaz Swagger UI personalizada con tema oscuro (`/static/swagger-custom.css`). |
| `GET` | `/openapi.json` | No | Esquema OpenAPI 3.x generado por FastAPI. |
| `GET` | `/app/` | No | Aplicación web de control IoT (`frontend/index.html`). |

### 3.2. Vehículos (5 Stored Procedures)
| Método | Ruta | Auth (`X-API-Key`) | Stored Procedure |
|---|---|---|---|
| `POST` | `/api/vehiculos` | Sí | `sp_crear_vehiculo` |
| `GET` | `/api/vehiculos` | No | `sp_obtener_vehiculos` |
| `GET` | `/api/vehiculos/{id_vehiculo}` | No | `sp_obtener_vehiculo` |
| `PUT` | `/api/vehiculos/{id_vehiculo}` | Sí | `sp_actualizar_vehiculo` |
| `DELETE` | `/api/vehiculos/{id_vehiculo}` | Sí | `sp_eliminar_vehiculo` |

### 3.3. Comandos (4 Stored Procedures)
| Método | Ruta | Auth (`X-API-Key`) | Stored Procedure |
|---|---|---|---|
| `POST` | `/api/comandos` | Sí | `sp_crear_comando` |
| `GET` | `/api/vehiculos/{id_vehiculo}/comandos` | No | `sp_obtener_comandos_vehiculo` |
| `PUT` | `/api/comandos/{id_comando}` | Sí | `sp_actualizar_comando` |
| `DELETE` | `/api/comandos/{id_comando}` | Sí | `sp_eliminar_comando` |

### 3.4. Demostraciones y Ejecución (4 Stored Procedures + Control Asíncrono)
| Método | Ruta | Auth (`X-API-Key`) | Stored Procedure / Acción |
|---|---|---|---|
| `POST` | `/api/demos` | Sí | `sp_crear_demo` |
| `GET` | `/api/vehiculos/{id_vehiculo}/demos` | No | `sp_obtener_demos` |
| `PUT` | `/api/demos/{id_demo}` | Sí | `sp_actualizar_demo` |
| `DELETE` | `/api/demos/{id_demo}` | Sí | `sp_eliminar_demo` |
| `POST` | `/api/demos/{id_demo}/ejecutar` | Sí | Inicia ejecución temporizada asíncrona de una DEMO |
| `POST` | `/api/demos/{id_demo}/cancelar` | Sí | Cancela una DEMO en ejecución por `id_demo` |
| `DELETE` | `/api/demos/{id_demo}/ejecutar` | Sí | Alias DELETE para cancelar DEMO por `id_demo` |
| `GET` | `/api/vehiculos/{id_vehiculo}/demo/activa` | No | Consulta si el vehículo tiene una DEMO activa |
| `POST` | `/api/vehiculos/{id_vehiculo}/demo/cancelar` | Sí | Cancela cualquier DEMO activa del vehículo |

### 3.5. Movimientos de Demostración (4 Stored Procedures)
| Método | Ruta | Auth (`X-API-Key`) | Stored Procedure |
|---|---|---|---|
| `POST` | `/api/demos/{id_demo}/movimientos` | Sí | `sp_agregar_movimiento_demo` |
| `GET` | `/api/demos/{id_demo}/movimientos` | No | `sp_obtener_movimientos_demo` |
| `PUT` | `/api/movimientos-demo/{id_demo_movimiento}` | Sí | `sp_actualizar_movimiento_demo` |
| `DELETE` | `/api/movimientos-demo/{id_demo_movimiento}` | Sí | `sp_eliminar_movimiento_demo` |

### 3.6. Estado, Historial, Movimientos Sostenidos y DETENER (4 Stored Procedures + Transacciones)
| Método | Ruta | Auth (`X-API-Key`) | Stored Procedure / Acción |
|---|---|---|---|
| `GET` | `/api/vehiculos/{id_vehiculo}/estado` | No | `sp_obtener_estado` |
| `PUT` | `/api/vehiculos/{id_vehiculo}/estado` | Sí | `sp_actualizar_estado` + emisión WS `estado_actualizado` |
| `GET` | `/api/vehiculos/{id_vehiculo}/historial` | No | `sp_obtener_historial` (limitado a los últimos 10 registros) |
| `POST` | `/api/historial` | Sí | `sp_registrar_historial` + emisión WS `historial_registrado` |
| `POST` | `/api/movimientos` | Sí | Transacción atómica (`estado_vehiculo` + `historial_comandos`) + WS `movimiento_individual` |
| `POST` | `/api/vehiculos/{id_vehiculo}/detener` | Sí | Prioridad máxima de parada (`DETENER`), cancelación de DEMO/watchdog y emisión WS `detener` |

### 3.7. Canales WebSocket en Tiempo Real
| Canal | Ruta | Propósito |
|---|---|---|
| Individual | `/ws/vehiculos/{id_vehiculo}` | Canal principal del ESP8266 (`/ws/vehiculos/1`) y del panel web del vehículo. Envía `estado_inicial` al conectar, emite eventos de control y responde `ping` -> `pong` y `detencion_confirmada`. |
| Global | `/ws/vehiculos` (y alias `/ws`) | Monitor global multi-vehículo. Envía `estado_inicial_global` al conectar y retransmite todos los eventos de todos los vehículos. |

---

## 4. Guía Paso a Paso para Reconstruir AWS (AWS Academy)

### Paso 1: Crear la Base de Datos Amazon RDS (MySQL)

1. En la consola de AWS Academy (`us-east-1`), abre **Amazon RDS** -> **Create database**.
2. Selecciona:
   - **Creation method:** Standard create.
   - **Engine options:** MySQL (versión 8.0.x / 8.4.x).
   - **Templates:** Free tier (o Dev/Test si AWS Academy bloquea Free Tier; usa clase de instancia `db.t3.micro`).
3. En **Settings**:
   - **DB instance identifier:** `instancia-rds-iot`
   - **Master username:** `admin`
   - **Master password:** Define tu contraseña segura (y anótala para el archivo `.env`).
4. En **Instance configuration** y **Storage**:
   - **Burstable classes:** `db.t3.micro`
   - **Allocated storage:** `20 GiB` (General Purpose SSD `gp2`/`gp3`, desmarca *Enable storage autoscaling* si deseas acotar costos de laboratorio).
5. En **Connectivity**:
   - **VPC:** Default VPC.
   - **Public access:**
     - **Recomendado:** `No` si solo te conectarás desde la instancia EC2 en la misma VPC.
     - **Opcional (Laboratorio):** `Yes` si necesitas conectarte temporalmente desde MySQL Workbench en tu PC para inspección directa.
   - **VPC security group:** Crea o selecciona `sg-rds-vehiculo-iot` permitiendo el puerto TCP `3306` desde el Security Group de tu instancia EC2 (y desde tu IP pública solo si habilitaste acceso público para pruebas).
6. En **Additional configuration**:
   - **Initial database name:** `vehiculo_iot`
7. Haz clic en **Create database**, espera a que pase a estado **Available** y copia el **Endpoint** (por ejemplo: `instancia-rds-iot.xxxxxx.us-east-1.rds.amazonaws.com`).

---

### Paso 2: Crear la Instancia Amazon EC2 en AWS Academy

1. Abre **EC2** -> **Launch instance**.
2. Configura:
   - **Name:** `vehiculo-iot-api-server`
   - **AMI:** **Amazon Linux 2023 AMI** o **Ubuntu Server 22.04/24.04 LTS** (64-bit x86).
   - **Instance type:** `t3.micro` o `t2.micro` (permitidos en AWS Academy Learner Lab).
   - **Key pair (login):** Selecciona `vockey` (clave estándar de AWS Academy descargable desde *Learner Lab -> AWS Details -> Download PEM*) o tu par de claves `.pem`.
   - **Network settings (Security Group `sg-ec2-vehiculo-iot`):**
     - `SSH (TCP 22)`: Desde tu IP (`My IP`) o `0.0.0.0/0`.
     - `HTTP (TCP 80)`: `0.0.0.0/0` (necesario para redirección HTTPS y validación de Let's Encrypt/Certbot).
     - `HTTPS (TCP 443)`: `0.0.0.0/0` (tráfico REST HTTPS y WebSockets WSS para el navegador y el ESP8266).
     - *(Importante: NO abras el puerto 8000 al exterior; Uvicorn escucha solo en `127.0.0.1:8000` detrás de Nginx).*
3. Lanza la instancia y copia su **Public IPv4 address**.

---

### Paso 3: Actualizar el Dominio en DuckDNS

1. Ingresa a [https://www.duckdns.org](https://www.duckdns.org).
2. Localiza tu subdominio `vehiculo-iot-api` (`vehiculo-iot-api.duckdns.org`).
3. Sustituye la IP anterior por la **nueva Public IPv4 address** de tu instancia EC2 y haz clic en **update ip**.
4. Verifica desde tu terminal que el DNS resuelve a la nueva IP:
   ```bash
   nslookup vehiculo-iot-api.duckdns.org
   ```

---

### Paso 4: Desplegar el Backend y Reconstruir la Base de Datos desde EC2

1. Conéctate por SSH a la nueva instancia EC2:
   ```bash
   ssh -i labsuser.pem ec2-user@vehiculo-iot-api.duckdns.org
   # (En Ubuntu usa ubuntu@vehiculo-iot-api.duckdns.org)
   ```
2. Instala `git` y clona el repositorio:
   ```bash
   sudo dnf install -y git || sudo apt-get install -y git
   git clone https://github.com/ClaudiaHernandez03/vehiculo_iot_api.git
   cd vehiculo_iot_api/vehiculo_iot_api
   ```
3. Crea el archivo `.env` a partir de `.env.example` (sin subirlo nunca a Git):
   ```bash
   cp .env.example .env
   nano .env
   ```
   Completa los valores reales:
   ```env
   DB_HOST=tu-nuevo-endpoint.us-east-1.rds.amazonaws.com
   DB_PORT=3306
   DB_NAME=vehiculo_iot
   DB_USER=admin
   DB_PASSWORD=TU_PASSWORD_RDS
   DB_CONNECT_TIMEOUT=10
   API_KEY=TU_API_KEY_SEGURA
   REQUIRE_API_KEY=true
   FRONTEND_GITHUB_PAGES_URL=https://claudiahernandez03.github.io
   CORS_ORIGINS=http://localhost:5500,http://127.0.0.1:5500,https://vehiculo-iot-api.duckdns.org,https://claudiahernandez03.github.io
   CORS_ALLOW_CREDENTIALS=false
   ```
4. Ejecuta el script automatizado `deploy_ec2.sh`:
   ```bash
   chmod +x deploy_ec2.sh
   ./deploy_ec2.sh
   ```
   Este script automáticamente:
   - Instala Python 3, `venv` y Nginx.
   - Crea `.venv` e instala `requirements.txt`.
   - Ejecuta `sql/ejecutar_migracion_y_validacion.py` (que corre `01_reconstruccion_completa_esquema_y_sps.sql`, `02_recuperacion_datos_y_migracion_idempotente.sql` y `03_validacion_integridad.sql` contra Amazon RDS, creando las 6 tablas, los 21 SPs, el vehículo 1, los 13 comandos y las 3 demos con sus 15 movimientos).
   - Ejecuta `probar_conexion.py`.
   - Instala, habilita e inicia el servicio `systemd` `vehiculo_iot_api.service`.

---

### Paso 5: Configurar Nginx + Certificado TLS (HTTPS y WSS)

1. Copia el archivo de configuración `nginx.conf` (mientras aún no tienes certificado TLS, puedes emitir el certificado con Certbot):
   ```bash
   sudo cp nginx.conf /etc/nginx/conf.d/vehiculo_iot.conf
   ```
2. Instala y ejecuta Certbot para `vehiculo-iot-api.duckdns.org`:
   ```bash
   # En Amazon Linux 2023:
   sudo dnf install -y augeas-libs
   sudo python3 -m venv /opt/certbot/
   sudo /opt/certbot/bin/pip install --upgrade pip certbot certbot-nginx
   sudo ln -s /opt/certbot/bin/certbot /usr/bin/certbot

   # Emitir e instalar el certificado TLS:
   sudo certbot --nginx -d vehiculo-iot-api.duckdns.org
   ```
3. Verifica y recarga Nginx:
   ```bash
   sudo nginx -t
   sudo systemctl enable nginx
   sudo systemctl restart nginx
   ```
4. Comprueba desde el exterior:
   - `https://vehiculo-iot-api.duckdns.org/health`
   - `https://vehiculo-iot-api.duckdns.org/docs`
   - `https://vehiculo-iot-api.duckdns.org/app/`
   - `wss://vehiculo-iot-api.duckdns.org/ws/vehiculos/1`

# Plataforma Comercial IoT — Excavadora CAT-320 (Prácticas 4.1, 4.2 y 4.3 Integradas)

Plataforma web modular, responsiva (**Mobile First**), en paleta **Negro Obsidiana y Contrastes de Morado**, lista para publicarse en **GitHub Pages**, que integra en una sola aplicación y repositorio las tres etapas de la asignatura **Implementación de Soluciones IoT** conectadas a **AWS EC2 (FastAPI)**, **Amazon RDS MySQL (21 Stored Procedures)** y **WebSocket Seguro (WSS)**:

1. **4.1 CONTROL**: Consola de mando con **Doble Joystick Táctil** (Izquierdo: Orugas/Tracción · Derecho: Brazo Hidráulico y Torre), botones táctiles del catálogo, paro prioritario **DETENER**, ejecución/cancelación de **DEMO** y bloqueo de *pull-to-refresh* en dispositivos móviles.
2. **4.2 MONITOREO**: Visualización en tiempo real del **Último Movimiento / Estado Reportado** con fecha, hora y origen verificables, y **Tabla Dinámica de los 10 Movimientos Individuales Más Recientes** persistidos en Amazon RDS (`sp_obtener_historial`), actualizados automáticamente por la sesión WSS compartida y reconciliados por REST sin recargar la página (sin contaminar el historial con pasos internos de DEMO).
3. **4.3 ADMINISTRACIÓN**: Plataforma comercial multiusuario con los 3 roles (**Administrador / Empresa**, **Usuario / Comprador** e **Invitado / Exhibición**), Dashboard de métricas reales, Inventario de Modelos y Unidades Físicas (`/api/vehiculos`), Asignación de Clientes con aislamiento BOLA/IDOR, **Editor Visual de DEMOs Persistentes en RDS** (1 a 4 pasos ordenados), **Sesión Temporal Exclusiva de Exhibición (60s)** con auto-DETENER y **Bitácora de Auditoría**.

---

## 1. Infraestructura Conectada en AWS

| Recurso | Valor / Dirección Oficial |
| :--- | :--- |
| **API REST Base (HTTPS)** | `https://vehiculo-iot-api.duckdns.org` |
| **Documentación Swagger** | `https://vehiculo-iot-api.duckdns.org/docs` |
| **Contrato OpenAPI 3.1.0** | `https://vehiculo-iot-api.duckdns.org/openapi.json` |
| **Canal WebSocket (WSS)** | `wss://vehiculo-iot-api.duckdns.org/ws/vehiculos/1` |
| **Vehículo Principal** | `ID: 1` · `Excavadora IoT` · `Modelo: CAT-320` · `MCU: ESP8266` |

---

## 2. Estructura Modular del Proyecto

```text
practica_4_1_control_iot/
├── index.html                           # Interfaz integrada con las 3 ventanas: Control | Monitoreo | Administración
├── assets/
│   ├── favicon.svg                      # Favicon vectorial propio en Negro y Morado (CAT-320)
│   └── css/
│       └── styles.css                   # Estilos minimalistas Black & Multi-Contrast Purple + Anti Pull-to-Refresh
├── js/
│   ├── main.js                          # Orquestador de las 3 vistas y sesión WSS única compartida
│   ├── config/
│   │   └── api.config.js                # Configuración centralizada de las 21 APIs y códigos (sin secretos)
│   ├── services/
│   │   ├── http.client.js               # Cliente HTTP Fetch (GET, POST, PUT, DELETE) con AbortController
│   │   ├── iot.api.service.js           # Métodos REST para Control, Monitoreo (10 registros) y Administración
│   │   └── ws.client.js                 # Cliente WebSocket nativo con reconexión exponencial y resincronización
│   ├── controllers/
│   │   ├── command.dispatcher.js        # Despachador único, anti-duplicados, RBAC/BOLA y prioridad DETENER
│   │   ├── joystick.controller.js       # Controlador dual de Joysticks con Pointer Events y bloqueo anti-refresh
│   │   ├── demo.controller.js           # Ejecución y cancelación de DEMOs temporizadas por servidor
│   │   ├── monitoring.controller.js     # (4.2) Último estado en vivo + Tabla de 10 movimientos individuales RDS
│   │   ├── admin.controller.js          # (4.3) Roles Admin/Usuario/Invitado, Unidades, Editor DEMO y Auditoría
│   │   └── connectivity.monitor.js      # Diagnóstico independiente de los 3 estados (WSS, API 21 SPs, RDS)
│   ├── ui/
│   │   └── ui.renderer.js               # Renderizado seguro del DOM para las 3 ventanas (textContent, sin XSS)
│   └── utils/
│       └── validators.js                # Validación de esquemas JSON y verificación de SPs en RDS
├── tests/
│   └── passive.integration.test.mjs     # Suite de pruebas unitarias y simulaciones para 4.1, 4.2 y 4.3
└── README.md                            # Documentación general del proyecto integrado
```

---

## 3. Ejecución Local y Pruebas

```powershell
cd C:\Users\l2120\OneDrive\Documentos\practica_4_1_control_iot
python -m http.server 5500
```
Abre en tu navegador: `http://localhost:5500`

Para ejecutar la suite de pruebas automatizadas de las 3 prácticas (4.1, 4.2 y 4.3):
```powershell
node tests/passive.integration.test.mjs
```

---

## 4. Créditos

**Desarrollado por Claudia America Hernandez Jimenez · 2026**  
Ingeniería en Sistemas Computacionales · Implementación de Soluciones IoT (4.1 Control · 4.2 Monitoreo · 4.3 Administración).

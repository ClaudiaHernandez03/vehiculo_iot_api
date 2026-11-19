# Plan de Pruebas, Verificación Independiente WebSocket y Lista de Evidencias — Práctica 4.1

## 1. Cómo Verificar Eventos en Tiempo Real con un Cliente WebSocket Externo (Postman / Thunder Client)

Dado que en esta práctica **no se desarrolla todavía la aplicación independiente de monitoreo**, la verificación externa de los eventos en tiempo real se realiza conectando un cliente WebSocket independiente al canal del vehículo.

### Paso a paso en Postman o Thunder Client:
1. Abre **Postman** (o la extensión **Thunder Client** en VS Code) y selecciona **New → WebSocket Request**.
2. Ingresa la URL real WSS de la infraestructura:
   ```text
   wss://vehiculo-iot-api.duckdns.org/ws/vehiculos/1
   ```
3. Haz clic en **Connect**.
4. **Verificación del evento de conexión inicial (`estado_inicial`):**
   Inmediatamente al conectar, observarás en la bandeja de mensajes de Postman un JSON con la estructura:
   ```json
   {
     "tipo": "estado_inicial",
     "id_vehiculo": 1,
     "estado": [
       {
         "id_vehiculo": 1,
         "vehiculo": "Excavadora IoT",
         "id_comando": 8,
         "comando": "Luz",
         "codigo": 8,
         "conectado": 1,
         "fecha_actualizacion": "2026-11-09T15:25:36"
       }
     ],
     "timestamp": "2026-11-09T15:26:24.427050+00:00"
   }
   ```
5. **Correlación de un movimiento individual (`movimiento_individual`):**
   Con la sesión WSS de Postman abierta en paralelo, abre el frontend web de la Práctica 4.1 y activa un control (por ejemplo, **Luz [Código 8]** o desplaza el **Joystick hacia AVANZAR [Código 9]**). En Postman aparecerá en tiempo real:
   ```json
   {
     "tipo": "movimiento_individual",
     "id_vehiculo": 1,
     "id_comando": 9,
     "comando": "Avanzar",
     "codigo": 9,
     "origen": "WEB_CONTROL_P41",
     "timestamp": "2026-11-09T15:30:00.000000+00:00",
     "estado": "confirmado_en_rds"
   }
   ```
6. **Correlación de inicio, N pasos, finalización o cancelación de una DEMO:**
   - Al seleccionar una DEMO en el frontend (ej. **ID 1: Ciclo de Excavación**) y pulsar **Ejecutar DEMO**, observarás en Postman:
     1. Un evento `"tipo": "demo_inicio"` con `"id_demo": 1` y `"total_pasos": 5`.
     2. Cinco eventos temporizados por el servidor con `"tipo": "demo_paso"`, indicando `"paso": 1..5`, `"comando"`, `"codigo"`, `"duracion_ms"` y `"pausa_ms"`.
     3. Al concluir el quinto paso, un evento `"tipo": "demo_fin"` con `"total_pasos_ejecutados": 5`.
   - Si durante la ejecución presionas **Cancelar DEMO** o el botón rojo **DETENER**, observarás inmediatamente el evento `"tipo": "demo_cancelada"` indicando `"paso_interrumpido"`.

---

## 2. Casos de Prueba Paso a Paso

| ID | Componente | Procedimiento de Prueba | Resultado Esperado |
| :--- | :--- | :--- | :--- |
| **CP-01** | **Panel de 3 Indicadores** | Abrir la aplicación y observar las 3 tarjetas superiores; pulsar **Verificar API y RDS** y **Reconectar WSS**. | 1) **WSS** muestra `En línea` al abrir el socket. 2) **Servidor AWS** muestra `Disponible` tras `GET /health`. 3) **Amazon RDS** pasa de `Sin verificar` a `Disponible` únicamente tras confirmar la respuesta de los Stored Procedures (`sp_obtener_vehiculo`, `sp_obtener_comandos_vehiculo`, `sp_obtener_estado`). |
| **CP-02** | **Joystick Circular (4 direcciones y anti-spam)** | Arrastrar el pomo hacia Arriba (`#9`), mantenerlo moviendo ligeramente dentro del cuadrante superior y soltar. Repetir hacia Abajo (`#11`), Izquierda (`#2`) y Derecha (`#4`). | En la pestaña Network (`F12`), se envía **un único** `POST /api/movimientos` por cada transición de dirección (sin ráfagas por píxel). Al soltar, el pomo regresa al centro (`NEUTRAL`). |
| **CP-03** | **Botones Secundarios del Catálogo** | Presionar los botones de Brazo arriba (`#1`), Brazo abajo (`#3`), Torre izquierda (`#10`), Torre derecha (`#12`), Velocidad (`#5`), Sonido (`#6`) y Luz (`#8`). | El botón cambia visualmente por los estados `presionado` → `enviando` → `aceptado`. Se registra el movimiento en RDS (`HTTP 201`) y se recibe el evento en WebSocket. |
| **CP-04** | **Prioridad DETENER y Limitación de Catálogo** | Presionar el botón rojo **DETENER / NEUTRAL PRIORITARIO** tanto en reposo como durante la ejecución de una DEMO. | Retorno inmediato a neutral, aborto de peticiones en vuelo y, si hay DEMO activa, invocación de `POST /api/vehiculos/1/demo/cancelar`. Muestra aviso técnico claro indicando que el catálogo actual (`1–12`) no incluye un código de paro físico (`0`). |
| **CP-05** | **Módulo DEMO (Consulta, Ejecución y Exclusión)** | Seleccionar DEMO 1, 2 y 3 en el selector; verificar sus pasos cargados desde `GET /api/demos/{id}/movimientos`. Iniciar una DEMO y probar que los botones manuales y el botón de iniciar otra DEMO quedan bloqueados hasta que termine o se cancele. | No se permite ejecutar dos DEMOs simultáneas. El servidor controla los tiempos y el avance de cada paso se ilumina en tiempo real vía WebSocket. |
| **CP-06** | **Diseño Mobile First, Favicon y Footer** | Abrir la aplicación en un teléfono móvil físico (vertical y horizontal) o en el emulador responsivo. | Sin desbordamiento horizontal; joystick táctil fluido (`touch-action: none`); favicon visible en pestaña y footer `Desarrollado por Claudia America Hernandez Jimenez · 2026` visible al pie sin tapar controles. |

---

## 3. Lista de Evidencias que Debes Capturar para tu Entrega

Para documentar tu entrega final, captura las siguientes pantallas reales:

1. **Evidencia 1 — Interfaz en Escritorio y Favicon:** Captura de pantalla completa en navegador de escritorio donde se aprecie la pestaña con el **favicon de la excavadora**, el encabezado con `Excavadora IoT · Modelo CAT-320 · ESP8266`, los **3 indicadores de conectividad en verde**, el joystick, los controles secundarios, el panel DEMO y el **footer con tu nombre al pie**.
2. **Evidencia 2 — Vista en Teléfono Móvil Físico (Vertical y Horizontal):** Fotografías o capturas desde tu celular abriendo la URL pública de GitHub Pages.
3. **Evidencia 3 — Correlación de Movimiento Individual (Frontend + Postman WSS):** Pantalla dividida mostrando el clic en un comando o dirección del joystick en tu aplicación y el mensaje JSON `"tipo": "movimiento_individual"` recibido simultáneamente en el cliente WebSocket de Postman/Thunder Client.
4. **Evidencia 4 — Ejecución y Cancelación de DEMO por WebSocket:** Captura mostrando los eventos `"demo_inicio"`, `"demo_paso"` y `"demo_fin"` (o `"demo_cancelada"`) en Postman y en la bitácora inferior de la aplicación.
5. **Evidencia 5 — Publicación en GitHub Pages y Repositorio:** Captura de la configuración de GitHub Pages activa con la URL `https://...github.io/...` y la estructura limpia del repositorio sin archivos `.env` ni claves expuestas.

---

**Desarrollado por Claudia America Hernandez Jimenez · 2026**  
Práctica 4.1 — Aplicación de control para IoT.

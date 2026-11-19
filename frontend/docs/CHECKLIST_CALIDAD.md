# Checklist de Seguridad, Accesibilidad y Manejo de Errores — Práctica 4.1

## 1. Seguridad y Prevención de Vulnerabilidades
- [x] **Cero secretos en el frontend:** No existen contraseñas, tokens, claves AWS, cadenas de conexión MySQL RDS ni cabeceras `X-API-Key` incrustadas en ningún archivo `.js`, `.html` o de configuración.
- [x] **HTTPS y WSS exclusivos (Sin Mixed Content):** Todas las peticiones REST apuntan a `https://vehiculo-iot-api.duckdns.org` y el socket en tiempo real a `wss://vehiculo-iot-api.duckdns.org/ws/vehiculos/1`.
- [x] **Prevención de XSS / Inyección DOM:** Todo dato proveniente de la API o del WebSocket se inserta exclusivamente mediante `textContent` y creación segura de nodos (`document.createElement`), nunca mediante `innerHTML` con datos remotos.
- [x] **Validación de comandos contra catálogo real:** Antes de emitir cualquier `POST /api/movimientos`, `CommandDispatcher` valida que el código exista y esté activo en el catálogo devuelto por `GET /api/vehiculos/1/comandos` y que cumpla `id_comando > 0`.
- [x] **Distinción transparente de estados físicos y lógicos:**
  - La sesión WSS indica únicamente la conexión navegador ↔ servidor AWS, sin afirmar que el microcontrolador físico ESP8266 esté conectado.
  - La disponibilidad de Amazon RDS se informa como `Disponible` únicamente tras verificar respuestas de procedimientos almacenados (`sp_...`), sin confundir `GET /health` con la base de datos.
  - La ausencia de un comando `DETENER` en el catálogo actual (`1–12`) está identificada y documentada sin simular un código `0` inexistente.

## 2. Accesibilidad (WCAG / Mobile First)
- [x] **HTML5 Semántico:** Uso apropiado de `<header>`, `<main>`, `<section>`, `<footer>`, `<button>` y etiquetas `<label>`.
- [x] **Áreas táctiles accesibles:** Todos los botones de comando tienen una altura mínima de `52px–60px` para operación cómoda con el pulgar en pantallas móviles.
- [x] **Navegación por teclado y foco visible:** Todos los controles y el joystick (`tabindex="0"`, soporte para teclas de flecha / `WASD` / `Espacio` / `Escape`) cuentan con indicador `:focus-visible` de alto contraste en color amarillo industrial (`#FFCD11`).
- [x] **Atributos ARIA en vivo:** Los indicadores de estado y el banner de retroalimentación emplean `role="status"`, `role="alert"` y `aria-live="polite"`.
- [x] **Doble codificación visual:** Los estados de conectividad usan simultáneamente color, icono y etiqueta de texto (`En línea`, `Disponible`, `Sin verificar`, `Desconectado`).

## 3. Resiliencia y Manejo de Errores de Red
- [x] **Control de Timeout y AbortController:** Todas las llamadas `fetch` en `HttpClient` disponen de un temporizador de corte (`8000 ms` general, `5000 ms` para salud) y soporte de cancelación inmediata al soltar el joystick o pulsar `DETENER`.
- [x] **Anti-duplicación de POST:** El joystick evalúa transiciones reales de cuadrante (`UP`, `DOWN`, `LEFT`, `RIGHT`, `NEUTRAL`), evitando disparos repetidos por píxel o fotograma.
- [x] **Reconexión automática WebSocket:** `VehicleWsClient` implementa reconexión automática con backoff exponencial acotado (`1.5 s` hasta `15 s`) más botón manual de reconexión.

---

**Desarrollado por Claudia America Hernandez Jimenez · 2026**  
Práctica 4.1 — Aplicación de control para IoT.

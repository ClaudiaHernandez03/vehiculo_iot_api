/**
 * @file api.config.js
 * @description Configuración centralizada de infraestructura, endpoints REST (21 servicios),
 * WebSocket WSS y catálogo para la Plataforma Integral IoT:
 *   - 4.1 CONTROL (Dual-Joystick + Botones + DETENER + Ejecución DEMO)
 *   - 4.2 MONITOREO (Último estado en vivo + Tabla de 10 movimientos individuales persistidos)
 *   - 4.3 ADMINISTRACIÓN (Roles Admin/Usuario/Invitado, Unidades, Clientes, Editor DEMO, Exhibición y Auditoría)
 *
 * SEGURIDAD:
 * Este archivo NO contiene claves secretas, tokens privilegiados, credenciales de AWS ni accesos a RDS.
 */

export const APP_CONFIG = Object.freeze({
  PROJECT_NAME: 'CAT-320 IoT Suite',
  DEVELOPER_NAME: 'Claudia America Hernandez Jimenez',
  DEVELOPER_ID: 'L21200607',
  ACADEMIC_YEAR: 2026,
  PRACTICE_TITLE: 'Plataforma IoT · Control, Monitoreo y Administración',

  // Infraestructura AWS existente (FastAPI + Caddy HTTPS/WSS + Amazon RDS MySQL)
  API_BASE_URL: 'https://vehiculo-iot-api.duckdns.org',
  SWAGGER_URL: 'https://vehiculo-iot-api.duckdns.org/docs',
  OPENAPI_URL: 'https://vehiculo-iot-api.duckdns.org/openapi.json',
  WS_BASE_URL: 'wss://vehiculo-iot-api.duckdns.org',

  // Identificador del vehículo activo por defecto en Amazon RDS
  VEHICLE_ID: 1,
  VEHICLE_DEFAULTS: Object.freeze({
    id_vehiculo: 1,
    nombre: 'Excavadora IoT',
    modelo: 'CAT-320',
    microcontrolador: 'ESP8266',
    descripcion: 'Excavadora IoT con 12 movimientos'
  }),

  COMMAND_ORIGIN: 'WEB_IOT_SUITE',

  // Parámetros de red, tiempos de espera y resiliencia
  HTTP_TIMEOUT_MS: 8000,
  HEALTH_POLL_INTERVAL_MS: 20000,
  WS_RECONNECT_BASE_MS: 1500,
  WS_RECONNECT_MAX_MS: 15000,
  JOYSTICK_COOLDOWN_MS: 220,
  HISTORY_MAX_ROWS: 10,
  DEMO_MAX_STEPS: 4,
  GUEST_SESSION_SECONDS: 60,

  // Rutas reales verificadas contra https://vehiculo-iot-api.duckdns.org/openapi.json
  ENDPOINTS: Object.freeze({
    HEALTH: '/health',
    VEHICULOS: '/api/vehiculos',
    VEHICULO_DETALLE: (idVehiculo = 1) => `/api/vehiculos/${encodeURIComponent(idVehiculo)}`,
    VEHICULO_COMANDOS: (idVehiculo = 1) => `/api/vehiculos/${encodeURIComponent(idVehiculo)}/comandos`,
    COMANDOS: '/api/comandos',
    COMANDO_DETALLE: (idComando) => `/api/comandos/${encodeURIComponent(idComando)}`,
    VEHICULO_ESTADO: (idVehiculo = 1) => `/api/vehiculos/${encodeURIComponent(idVehiculo)}/estado`,
    VEHICULO_HISTORIAL: (idVehiculo = 1) => `/api/vehiculos/${encodeURIComponent(idVehiculo)}/historial`,
    HISTORIAL: '/api/historial',
    MOVIMIENTOS: '/api/movimientos',
    VEHICULO_DETENER: (idVehiculo = 1) => `/api/vehiculos/${encodeURIComponent(idVehiculo)}/detener`,
    VEHICULO_DEMOS: (idVehiculo = 1) => `/api/vehiculos/${encodeURIComponent(idVehiculo)}/demos`,
    DEMOS: '/api/demos',
    DEMO_DETALLE: (idDemo) => `/api/demos/${encodeURIComponent(idDemo)}`,
    DEMO_MOVIMIENTOS: (idDemo) => `/api/demos/${encodeURIComponent(idDemo)}/movimientos`,
    MOVIMIENTO_DEMO_DETALLE: (idDemoMov) => `/api/movimientos-demo/${encodeURIComponent(idDemoMov)}`,
    DEMO_EJECUTAR: (idDemo) => `/api/demos/${encodeURIComponent(idDemo)}/ejecutar`,
    DEMO_CANCELAR: (idDemo) => `/api/demos/${encodeURIComponent(idDemo)}/cancelar`,
    VEHICULO_DEMO_ACTIVA: (idVehiculo = 1) => `/api/vehiculos/${encodeURIComponent(idVehiculo)}/demo/activa`,
    VEHICULO_DEMO_CANCELAR: (idVehiculo = 1) => `/api/vehiculos/${encodeURIComponent(idVehiculo)}/demo/cancelar`,
    WS_VEHICULO: (idVehiculo = 1) => `/ws/vehiculos/${encodeURIComponent(idVehiculo)}`
  }),

  // Joystick Izquierdo: Tracción / Orugas
  JOYSTICK_CODES: Object.freeze({
    UP: 9,      // Avanzar
    DOWN: 11,   // Retroceder
    LEFT: 2,    // Girar izquierda
    RIGHT: 4    // Girar derecha
  }),

  // Joystick Derecho: Brazo Hidráulico y Rotación de Torre
  RIGHT_JOYSTICK_CODES: Object.freeze({
    UP: 1,      // Mover brazo arriba
    DOWN: 3,    // Mover brazo abajo
    LEFT: 10,   // Girar torre izquierda
    RIGHT: 12   // Girar torre derecha
  }),

  // Controles secundarios y auxiliares del catálogo
  SECONDARY_CODES: Object.freeze({
    ARM_UP: 1,
    ARM_DOWN: 3,
    TURRET_LEFT: 10,
    TURRET_RIGHT: 12,
    SPEED: 5,
    SOUND: 6,
    LIGHT: 8
  }),

  STOP_CATALOG_CODE: null
});

/**
 * Construye la URL absoluta hacia un recurso REST del backend.
 * @param {string} path Ruta relativa del endpoint (ej. '/health')
 * @returns {string} URL completa HTTPS
 */
export function buildApiUrl(path) {
  const cleanBase = APP_CONFIG.API_BASE_URL.replace(/\/+$/, '');
  const cleanPath = String(path || '').startsWith('/') ? path : `/${path}`;
  return `${cleanBase}${cleanPath}`;
}

/**
 * Construye la URL absoluta WSS para el canal WebSocket del vehículo.
 * @param {number} [vehicleId=APP_CONFIG.VEHICLE_ID] Identificador del vehículo
 * @returns {string} URL completa WSS
 */
export function buildWsUrl(vehicleId = APP_CONFIG.VEHICLE_ID) {
  const cleanBase = APP_CONFIG.WS_BASE_URL.replace(/\/+$/, '');
  const wsPath = APP_CONFIG.ENDPOINTS.WS_VEHICULO(vehicleId);
  return `${cleanBase}${wsPath}`;
}

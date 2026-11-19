/**
 * @file ws.client.js
 * @description Cliente WebSocket nativo con gestión de estados (ONLINE, CONNECTING, DISCONNECTED),
 * reconexión automática con backoff exponencial y despacho seguro de eventos JSON en tiempo real.
 *
 * IMPORTANTE:
 * Este cliente representa únicamente la sesión WSS entre el navegador web y el servidor FastAPI en AWS.
 * NO afirma ni simula que el microcontrolador físico ESP8266 esté conectado.
 */

import { APP_CONFIG, buildWsUrl } from '../config/api.config.js';
import { isPlainObject } from '../utils/validators.js';

export const WS_CONNECTION_STATES = Object.freeze({
  ONLINE: 'ONLINE',
  CONNECTING: 'CONNECTING',
  DISCONNECTED: 'DISCONNECTED'
});

export class VehicleWsClient {
  /**
   * @param {object} [options]
   * @param {number} [options.vehicleId=APP_CONFIG.VEHICLE_ID]
   * @param {(state: string, meta?: object) => void} [options.onStateChange]
   * @param {(eventPayload: object) => void} [options.onEvent]
   */
  constructor({ vehicleId = APP_CONFIG.VEHICLE_ID, onStateChange = null, onEvent = null } = {}) {
    this.vehicleId = vehicleId;
    this.onStateChange = onStateChange;
    this.onEvent = onEvent;

    this.socket = null;
    this.state = WS_CONNECTION_STATES.DISCONNECTED;
    this.reconnectAttempts = 0;
    this.reconnectTimerId = null;
    this.manualClose = false;
  }

  /**
   * Inicia la conexión WSS hacia `wss://vehiculo-iot-api.duckdns.org/ws/vehiculos/1`.
   */
  connect() {
    this.clearReconnectTimer();
    this.manualClose = false;

    if (
      this.socket &&
      (this.socket.readyState === WebSocket.OPEN || this.socket.readyState === WebSocket.CONNECTING)
    ) {
      return;
    }

    const wsUrl = buildWsUrl(this.vehicleId);
    const isReconnecting = this.reconnectAttempts > 0;

    this.updateState(WS_CONNECTION_STATES.CONNECTING, {
      reconnecting: isReconnecting,
      attempt: this.reconnectAttempts + 1,
      url: wsUrl
    });

    try {
      this.socket = new WebSocket(wsUrl);
    } catch (error) {
      this.updateState(WS_CONNECTION_STATES.DISCONNECTED, {
        reason: error?.message || 'Error al instanciar WebSocket'
      });
      this.scheduleReconnect();
      return;
    }

    this.socket.addEventListener('open', () => {
      this.reconnectAttempts = 0;
      this.updateState(WS_CONNECTION_STATES.ONLINE, {
        url: wsUrl,
        note: 'Canal navegador ↔ AWS activo (no implica ESP8266 físico en línea)'
      });
    });

    this.socket.addEventListener('message', (messageEvent) => {
      this.handleIncomingMessage(messageEvent.data);
    });

    this.socket.addEventListener('error', () => {
      // El evento close se disparará inmediatamente después y gestionará la reconexión
    });

    this.socket.addEventListener('close', (closeEvent) => {
      this.socket = null;
      this.updateState(WS_CONNECTION_STATES.DISCONNECTED, {
        code: closeEvent.code,
        wasClean: closeEvent.wasClean
      });

      if (!this.manualClose) {
        this.scheduleReconnect();
      }
    });
  }

  /**
   * Fuerza una reconexión limpia solicitada por el operador desde el panel de conectividad.
   */
  reconnectNow() {
    this.disconnect();
    this.reconnectAttempts = 0;
    this.connect();
  }

  /**
   * Cierra manualmente la sesión WebSocket y detiene el temporizador de reconexión.
   */
  disconnect() {
    this.manualClose = true;
    this.clearReconnectTimer();
    if (this.socket) {
      try {
        this.socket.close(1000, 'Cierre solicitado por el cliente');
      } catch {
        // Ignorar errores al cerrar un socket ya cerrado
      }
      this.socket = null;
    }
    this.updateState(WS_CONNECTION_STATES.DISCONNECTED, { reason: 'Cerrado por el usuario' });
  }

  /**
   * Procesa y valida de forma segura el mensaje JSON recibido por WSS.
   * Eventos reales soportados del backend:
   * - `estado_inicial`
   * - `movimiento_individual`
   * - `demo_inicio`
   * - `demo_paso`
   * - `demo_fin`
   * - `demo_cancelada`
   * @param {string} rawData Cadena recibida del servidor
   */
  handleIncomingMessage(rawData) {
    if (typeof rawData !== 'string' || !rawData.trim()) return;
    try {
      const parsed = JSON.parse(rawData);
      if (!isPlainObject(parsed)) return;
      if (typeof this.onEvent === 'function') {
        this.onEvent(parsed);
      }
    } catch {
      // Ignorar tramas que no sean JSON válido sin romper la aplicación
    }
  }

  /**
   * Programa un reintento de conexión con backoff exponencial acotado.
   */
  scheduleReconnect() {
    this.clearReconnectTimer();
    this.reconnectAttempts += 1;

    const exponentialDelay = Math.min(
      APP_CONFIG.WS_RECONNECT_BASE_MS * Math.pow(1.6, this.reconnectAttempts - 1),
      APP_CONFIG.WS_RECONNECT_MAX_MS
    );
    const delayMs = Math.round(exponentialDelay);

    this.updateState(WS_CONNECTION_STATES.CONNECTING, {
      reconnecting: true,
      attempt: this.reconnectAttempts,
      nextRetryMs: delayMs
    });

    this.reconnectTimerId = window.setTimeout(() => {
      this.connect();
    }, delayMs);
  }

  clearReconnectTimer() {
    if (this.reconnectTimerId !== null) {
      window.clearTimeout(this.reconnectTimerId);
      this.reconnectTimerId = null;
    }
  }

  updateState(newState, meta = {}) {
    this.state = newState;
    if (typeof this.onStateChange === 'function') {
      this.onStateChange(newState, meta);
    }
  }
}

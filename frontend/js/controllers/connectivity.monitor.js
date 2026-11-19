/**
 * @file connectivity.monitor.js
 * @description Gestiona de forma separada y verificable los TRES indicadores de conectividad requeridos:
 * 1. WebSocket WSS: En línea / Conectando-Reconectando / Desconectado (sesión navegador ↔ servidor AWS).
 * 2. Servidor AWS de 21 APIs: Disponible / Comprobando / No disponible (vía GET /health).
 * 3. Amazon RDS MySQL: Disponible / No disponible / Sin verificar (jamás se asume por GET /health;
 *    solo se marca Disponible cuando una operación que ejecuta Stored Procedures en RDS responde con éxito).
 */

import { APP_CONFIG } from '../config/api.config.js';
import { iotApiService } from '../services/iot.api.service.js';
import { WS_CONNECTION_STATES } from '../services/ws.client.js';
import { evaluateRdsVerification, formatTimestamp, validateHealthResponse } from '../utils/validators.js';

export const API_STATES = Object.freeze({
  AVAILABLE: 'AVAILABLE',
  CHECKING: 'CHECKING',
  UNAVAILABLE: 'UNAVAILABLE'
});

export const RDS_STATES = Object.freeze({
  AVAILABLE: 'AVAILABLE',
  UNAVAILABLE: 'UNAVAILABLE',
  UNVERIFIED: 'UNVERIFIED'
});

export class ConnectivityMonitor {
  /**
   * @param {object} [options]
   * @param {(snapshot: object) => void} [options.onUpdate] Callback invocado cuando cambia cualquiera de los 3 estados
   * @param {import('../services/iot.api.service.js').IotApiService} [options.apiService=iotApiService]
   */
  constructor({ onUpdate = null, apiService = iotApiService } = {}) {
    this.onUpdate = onUpdate;
    this.apiService = apiService;
    this.pollTimerId = null;

    this.state = {
      ws: {
        status: WS_CONNECTION_STATES.DISCONNECTED,
        label: 'Desconectado',
        detail: 'Sin sesión activa con el servidor WSS',
        updatedAt: null
      },
      api: {
        status: API_STATES.CHECKING,
        label: 'Comprobando',
        detail: 'Verificando GET /health...',
        version: '—',
        updatedAt: null
      },
      rds: {
        status: RDS_STATES.UNVERIFIED,
        label: 'Sin verificar',
        detail: 'GET /health no comprueba RDS; requiere consulta de SP',
        procedure: null,
        updatedAt: null
      }
    };
  }

  /**
   * Inicia el monitoreo periódico de salud del servidor AWS (GET /health).
   */
  startMonitoring() {
    this.stopMonitoring();
    this.checkAwsApiHealth();
    this.pollTimerId = window.setInterval(() => {
      this.checkAwsApiHealth();
    }, APP_CONFIG.HEALTH_POLL_INTERVAL_MS);
  }

  /**
   * Detiene el temporizador de monitoreo periódico.
   */
  stopMonitoring() {
    if (this.pollTimerId !== null) {
      window.clearInterval(this.pollTimerId);
      this.pollTimerId = null;
    }
  }

  /**
   * Actualiza el indicador 1: WebSocket WSS (Navegador ↔ Servidor AWS).
   * @param {string} wsState Estado de WS_CONNECTION_STATES
   * @param {object} [meta={}]
   */
  updateWsState(wsState, meta = {}) {
    const nowIso = new Date().toISOString();

    if (wsState === WS_CONNECTION_STATES.ONLINE) {
      this.state.ws = {
        status: WS_CONNECTION_STATES.ONLINE,
        label: 'En línea',
        detail: 'Sesión navegador ↔ AWS activa (no indica ESP8266 físico)',
        updatedAt: formatTimestamp(nowIso)
      };
    } else if (wsState === WS_CONNECTION_STATES.CONNECTING) {
      const isRetry = Boolean(meta.reconnecting);
      this.state.ws = {
        status: WS_CONNECTION_STATES.CONNECTING,
        label: isRetry ? 'Reconectando' : 'Conectando',
        detail: isRetry
          ? `Intento #${meta.attempt || 1} hacia canal WSS...`
          : 'Estableciendo handshake WSS con AWS...',
        updatedAt: formatTimestamp(nowIso)
      };
    } else {
      this.state.ws = {
        status: WS_CONNECTION_STATES.DISCONNECTED,
        label: 'Desconectado',
        detail: meta.reason || 'Canal WSS cerrado o sin respuesta',
        updatedAt: formatTimestamp(nowIso)
      };
    }

    this.emitUpdate();
  }

  /**
   * Comprueba el indicador 2: Servidor AWS de 21 APIs mediante GET /health.
   * Importante: Si GET /health no incluye diagnóstico de RDS, NO altera ni asume RDS como disponible.
   */
  async checkAwsApiHealth() {
    this.state.api = {
      ...this.state.api,
      status: API_STATES.CHECKING,
      label: 'Comprobando',
      detail: 'Consultando GET /health...'
    };
    this.emitUpdate();

    const nowIso = new Date().toISOString();
    try {
      const payload = await this.apiService.checkApiHealth();
      const health = validateHealthResponse(payload);

      if (health.apiAvailable) {
        this.state.api = {
          status: API_STATES.AVAILABLE,
          label: 'Disponible',
          detail: `${health.serviceName} v${health.version} (HTTP 200)`,
          version: health.version,
          updatedAt: formatTimestamp(nowIso)
        };

        // Solo si /health trajera un campo explícito de base de datos se actualizaría aquí
        if (health.hasRdsField && health.rdsAvailable !== null) {
          this.state.rds = {
            status: health.rdsAvailable ? RDS_STATES.AVAILABLE : RDS_STATES.UNAVAILABLE,
            label: health.rdsAvailable ? 'Disponible' : 'No disponible',
            detail: 'Reportado por diagnóstico de salud',
            procedure: 'health_check',
            updatedAt: formatTimestamp(nowIso)
          };
        }
      } else {
        this.state.api = {
          status: API_STATES.UNAVAILABLE,
          label: 'No disponible',
          detail: 'Respuesta incompleta en GET /health',
          version: '—',
          updatedAt: formatTimestamp(nowIso)
        };
      }
    } catch (error) {
      this.state.api = {
        status: API_STATES.UNAVAILABLE,
        label: 'No disponible',
        detail: error?.message || 'Error al contactar servidor AWS EC2',
        version: '—',
        updatedAt: formatTimestamp(nowIso)
      };
      // Si la API cae, RDS no puede verificarse desde el cliente
      this.state.rds = {
        status: RDS_STATES.UNVERIFIED,
        label: 'Sin verificar',
        detail: 'Servidor API inaccesible para comprobar RDS',
        procedure: null,
        updatedAt: formatTimestamp(nowIso)
      };
    }

    this.emitUpdate();
  }

  /**
   * Registra evidencia real de Amazon RDS a partir de una respuesta de Stored Procedure.
   * @param {*} apiPayload Respuesta JSON de un endpoint que ejecuta SP en RDS
   */
  recordRdsEvidenceFromPayload(apiPayload) {
    const evaluation = evaluateRdsVerification(apiPayload);
    if (!evaluation.verified) return;

    const nowIso = new Date().toISOString();
    this.state.rds = {
      status: evaluation.available ? RDS_STATES.AVAILABLE : RDS_STATES.UNAVAILABLE,
      label: evaluation.available ? 'Disponible' : 'No disponible',
      detail: evaluation.detail,
      procedure: evaluation.procedure,
      updatedAt: formatTimestamp(nowIso)
    };
    this.emitUpdate();
  }

  /**
   * Registra un fallo al intentar ejecutar un Stored Procedure en Amazon RDS.
   * @param {Error} error Error capturado
   */
  recordRdsFailure(error) {
    const nowIso = new Date().toISOString();
    const isServerDbError = error && (error.status >= 500 || error.code === 'HTTP_500');

    if (isServerDbError) {
      this.state.rds = {
        status: RDS_STATES.UNAVAILABLE,
        label: 'No disponible',
        detail: error.message || 'Fallo al ejecutar Stored Procedure en RDS',
        procedure: null,
        updatedAt: formatTimestamp(nowIso)
      };
    } else {
      this.state.rds = {
        status: RDS_STATES.UNVERIFIED,
        label: 'Sin verificar',
        detail: 'No se pudo completar la consulta de verificación RDS',
        procedure: null,
        updatedAt: formatTimestamp(nowIso)
      };
    }
    this.emitUpdate();
  }

  /**
   * Ejecuta una verificación segura y de solo lectura de Amazon RDS consultando `sp_obtener_estado`
   * (`GET /api/vehiculos/1/estado`). No genera movimientos ni altera datos en RDS.
   */
  async verifyRdsReadOnly() {
    try {
      const { raw } = await this.apiService.getVehicleStatus(APP_CONFIG.VEHICLE_ID);
      this.recordRdsEvidenceFromPayload(raw);
      return true;
    } catch (error) {
      this.recordRdsFailure(error);
      return false;
    }
  }

  getSnapshot() {
    return {
      ws: { ...this.state.ws },
      api: { ...this.state.api },
      rds: { ...this.state.rds }
    };
  }

  emitUpdate() {
    if (typeof this.onUpdate === 'function') {
      this.onUpdate(this.getSnapshot());
    }
  }
}

/**
 * @file monitoring.controller.js
 * @description Controlador del Módulo 4.2 MONITOREO EN TIEMPO REAL.
 *
 * Responsabilidades:
 * 1. Carga inicial por REST del último estado (`GET /api/vehiculos/{id}/estado`) y de los 10
 *    movimientos individuales más recientes (`GET /api/vehiculos/{id}/historial`).
 * 2. Actualización inmediata sin recargar la página al recibir eventos desde la sesión WSS compartida.
 * 3. Reconciliación controlada por REST tras cada `movimiento_individual`, `detener` o reconexión WSS,
 *    sin usar polling continuo como sustituto de WebSocket.
 * 4. Exclusión estricta de los pasos internos de DEMO (`demo_paso`) en la tabla de historial individual,
 *    permitiendo que sí actualicen la tarjeta de último estado reportado en vivo.
 * 5. Prevención de duplicados (`id_historial`), filtrado por `id_vehiculo` y protección contra respuestas REST tardías.
 */

import { APP_CONFIG } from '../config/api.config.js';
import { iotApiService } from '../services/iot.api.service.js';
import { toPositiveInteger } from '../utils/validators.js';

export function splitDateAndTime(rawTimestamp) {
  if (!rawTimestamp) {
    return { fecha: 'Sin fecha', hora: 'Sin hora', epochMs: 0 };
  }
  const normalized = String(rawTimestamp).includes('T')
    ? String(rawTimestamp)
    : String(rawTimestamp).replace(' ', 'T');
  const parsed = new Date(normalized);
  if (Number.isNaN(parsed.getTime())) {
    const parts = String(rawTimestamp).trim().split(/\s+/);
    return {
      fecha: parts[0] || String(rawTimestamp),
      hora: parts[1] || '—',
      epochMs: 0
    };
  }
  return {
    fecha: parsed.toLocaleDateString('es-MX', {
      year: 'numeric',
      month: '2-digit',
      day: '2-digit'
    }),
    hora: parsed.toLocaleTimeString('es-MX', {
      hour12: false,
      hour: '2-digit',
      minute: '2-digit',
      second: '2-digit'
    }),
    epochMs: parsed.getTime()
  };
}

export class MonitoringController {
  /**
   * @param {object} [options]
   * @param {import('../services/iot.api.service.js').IotApiService} [options.apiService=iotApiService]
   * @param {(snapshot: object) => void} [options.onStateChange]
   * @param {(rdsPayload: object) => void} [options.onRdsEvidence]
   */
  constructor({ apiService = iotApiService, onStateChange = null, onRdsEvidence = null } = {}) {
    this.apiService = apiService;
    this.onStateChange = onStateChange;
    this.onRdsEvidence = onRdsEvidence;

    this.vehicleId = APP_CONFIG.VEHICLE_ID;
    this.vehicleName = APP_CONFIG.VEHICLE_DEFAULTS.nombre;
    this.vehicleModel = APP_CONFIG.VEHICLE_DEFAULTS.modelo;

    this.loading = false;
    this.error = null;
    this.lastSyncAt = null;
    this.syncSequence = 0;
    this.lastEventEpochMs = 0;
    this.resyncTimerId = null;

    // Tarjeta destacada del último movimiento o estado reportado
    this.lastMovement = null;

    // Lista deduplicada y ordenada de máximo 10 movimientos individuales persistidos en RDS
    this.historyRows = [];
  }

  setVehicleContext(vehicle) {
    if (!vehicle) return;
    const validId = toPositiveInteger(vehicle.id_vehiculo);
    if (validId) {
      this.vehicleId = validId;
    }
    if (vehicle.nombre) this.vehicleName = String(vehicle.nombre);
    if (vehicle.modelo) this.vehicleModel = String(vehicle.modelo);
    this.emitState();
  }

  /**
   * Carga inicial y resincronización completa mediante REST (`estado` + `historial`).
   * @param {object} [options]
   * @param {boolean} [options.silent=false]
   */
  async loadInitialMonitoring({ silent = false } = {}) {
    const seq = ++this.syncSequence;
    if (!silent) {
      this.loading = true;
      this.error = null;
      this.emitState();
    }

    try {
      const [statusRes, historyRes] = await Promise.all([
        this.apiService.getVehicleStatus(this.vehicleId),
        this.apiService.getVehicleHistory(this.vehicleId)
      ]);

      // Evitar que una respuesta HTTP vieja sobrescriba una sincronización posterior
      if (seq < this.syncSequence) return;

      if (typeof this.onRdsEvidence === 'function') {
        this.onRdsEvidence(statusRes.raw);
        this.onRdsEvidence(historyRes.raw);
      }

      this.applyHistoryRows(historyRes.history);

      if (statusRes.status) {
        this.applyRestLastStatus(statusRes.status);
      } else if (this.historyRows.length > 0 && !this.lastMovement) {
        const latestRow = this.historyRows[0];
        this.lastMovement = {
          id_vehiculo: this.vehicleId,
          vehiculo: latestRow.vehiculo || this.vehicleName,
          id_comando: latestRow.id_comando,
          comando: latestRow.comando,
          codigo: latestRow.codigo,
          origen: latestRow.origen || 'RDS',
          fecha: latestRow.fecha,
          hora: latestRow.hora,
          stageLabel: 'Persistido en Amazon RDS (Historial)',
          isDemoEvent: false
        };
      }

      this.loading = false;
      this.error = null;
      this.lastSyncAt = new Date().toISOString();
      this.emitState();
    } catch (err) {
      if (seq < this.syncSequence) return;
      this.loading = false;
      this.error = err?.message || 'No fue posible consultar el estado e historial desde AWS/RDS.';
      this.emitState();
    }
  }

  /**
   * Reconcilia únicamente la tabla de los 10 movimientos individuales desde `GET /api/vehiculos/{id}/historial`.
   */
  async resyncHistoryFromRds() {
    const seq = ++this.syncSequence;
    try {
      const historyRes = await this.apiService.getVehicleHistory(this.vehicleId);
      if (seq < this.syncSequence) return;

      if (typeof this.onRdsEvidence === 'function') {
        this.onRdsEvidence(historyRes.raw);
      }

      this.applyHistoryRows(historyRes.history);
      this.lastSyncAt = new Date().toISOString();
      this.error = null;
      this.emitState();
    } catch (err) {
      this.error = err?.message || 'Error al resincronizar historial con Amazon RDS.';
      this.emitState();
    }
  }

  scheduleHistoryResync(delayMs = 150) {
    if (this.resyncTimerId !== null) {
      globalThis.clearTimeout(this.resyncTimerId);
    }
    this.resyncTimerId = globalThis.setTimeout(() => {
      this.resyncTimerId = null;
      this.resyncHistoryFromRds();
    }, delayMs);
  }

  /**
   * Procesa y deduplica las filas provenientes de `sp_obtener_historial` (máximo 10, del más nuevo al más antiguo).
   * @param {Array<object>} rawRows
   */
  applyHistoryRows(rawRows = []) {
    if (!Array.isArray(rawRows)) {
      this.historyRows = [];
      return;
    }

    const seenIds = new Set();
    const cleaned = [];

    for (const row of rawRows) {
      const rowVehicleId = toPositiveInteger(row?.id_vehiculo);
      if (rowVehicleId && rowVehicleId !== this.vehicleId) {
        continue;
      }

      const idHist = toPositiveInteger(row?.id_historial);
      const dedupKey = idHist ? `ID_${idHist}` : `${row?.id_comando}_${row?.fecha_ejecucion}`;
      if (seenIds.has(dedupKey)) {
        continue;
      }
      seenIds.add(dedupKey);

      const dt = splitDateAndTime(row?.fecha_ejecucion || row?.timestamp);
      cleaned.push({
        id_historial: idHist || null,
        id_vehiculo: rowVehicleId || this.vehicleId,
        vehiculo: String(row?.vehiculo || this.vehicleName),
        id_comando: row?.id_comando ?? '—',
        comando: String(row?.comando || `Comando #${row?.id_comando || '—'}`),
        codigo: row?.codigo ?? '—',
        origen: String(row?.origen || 'API'),
        rawTimestamp: row?.fecha_ejecucion || null,
        fecha: dt.fecha,
        hora: dt.hora,
        epochMs: dt.epochMs
      });
    }

    // Ordenar del más reciente al más antiguo por id_historial o timestamp
    cleaned.sort((a, b) => {
      if (a.id_historial && b.id_historial) {
        return b.id_historial - a.id_historial;
      }
      return b.epochMs - a.epochMs;
    });

    this.historyRows = cleaned.slice(0, APP_CONFIG.HISTORY_MAX_ROWS);
  }

  applyRestLastStatus(statusRecord) {
    if (!statusRecord) return;
    const dt = splitDateAndTime(statusRecord.fecha_actualizacion || statusRecord.timestamp);
    if (this.lastEventEpochMs > 0 && dt.epochMs > 0 && dt.epochMs < this.lastEventEpochMs) {
      // No sobrescribir un evento WSS más reciente con un registro REST más antiguo
      return;
    }

    this.lastMovement = {
      id_vehiculo: statusRecord.id_vehiculo || this.vehicleId,
      vehiculo: statusRecord.vehiculo || this.vehicleName,
      id_comando: statusRecord.id_comando ?? '—',
      comando: String(statusRecord.comando || 'Sin comando'),
      codigo: statusRecord.codigo ?? '—',
      origen: 'Amazon RDS (sp_obtener_estado)',
      fecha: dt.fecha,
      hora: dt.hora,
      stageLabel: 'Confirmado en Amazon RDS (No implica ejecución física)',
      isDemoEvent: false
    };
  }

  /**
   * Procesa en vivo los eventos del canal WebSocket compartido (`/ws/vehiculos/1`).
   * @param {object} wsEvent
   */
  handleWebSocketEvent(wsEvent) {
    if (!wsEvent || typeof wsEvent.tipo !== 'string') return;

    const eventVehicleId = toPositiveInteger(wsEvent.id_vehiculo);
    if (eventVehicleId && eventVehicleId !== this.vehicleId) {
      return;
    }

    const tipo = wsEvent.tipo;

    if (tipo === 'estado_inicial') {
      const first = Array.isArray(wsEvent.estado) && wsEvent.estado[0] ? wsEvent.estado[0] : null;
      if (first) {
        const dt = splitDateAndTime(first.fecha_actualizacion || wsEvent.timestamp);
        this.lastMovement = {
          id_vehiculo: this.vehicleId,
          vehiculo: first.vehiculo || this.vehicleName,
          id_comando: first.id_comando ?? '—',
          comando: String(first.comando || 'Reposo'),
          codigo: first.codigo ?? '—',
          origen: 'Handshake inicial WSS',
          fecha: dt.fecha,
          hora: dt.hora,
          stageLabel: 'Estado inicial reportado por servidor WSS',
          isDemoEvent: false
        };
        this.emitState();
      }
      return;
    }

    if (tipo === 'movimiento_individual' || tipo === 'detener') {
      const dt = splitDateAndTime(wsEvent.timestamp);
      if (dt.epochMs > 0) {
        this.lastEventEpochMs = Math.max(this.lastEventEpochMs, dt.epochMs);
      }

      const isStop = tipo === 'detener' || Number(wsEvent.codigo) === 0;
      this.lastMovement = {
        id_vehiculo: this.vehicleId,
        vehiculo: this.vehicleName,
        id_comando: wsEvent.id_comando ?? (isStop ? 13 : '—'),
        comando: String(wsEvent.comando || (isStop ? 'Detener' : 'Movimiento')),
        codigo: wsEvent.codigo ?? (isStop ? 0 : '—'),
        origen: String(wsEvent.origen || 'WSS'),
        orden_id: wsEvent.orden_id || null,
        fecha: dt.fecha,
        hora: dt.hora,
        stageLabel: 'Evento WSS recibido · Reconciliando historial con RDS',
        isDemoEvent: false
      };
      this.emitState();

      // Reconciliar la tabla de 10 movimientos individuales persistidos desde RDS
      this.scheduleHistoryResync(140);
      return;
    }

    if (tipo === 'historial_registrado' || tipo === 'estado_actualizado') {
      this.scheduleHistoryResync(140);
      return;
    }

    // Eventos internos de DEMO: actualizan la tarjeta de último estado en vivo,
    // pero JAMÁS se insertan en la tabla de los 10 movimientos individuales (Regla 9.3).
    if (tipo === 'demo_inicio' || tipo === 'demo_paso' || tipo === 'demo_fin' || tipo === 'demo_cancelada') {
      const dt = splitDateAndTime(wsEvent.timestamp);
      if (dt.epochMs > 0) {
        this.lastEventEpochMs = Math.max(this.lastEventEpochMs, dt.epochMs);
      }

      if (tipo === 'demo_paso') {
        this.lastMovement = {
          id_vehiculo: this.vehicleId,
          vehiculo: this.vehicleName,
          id_comando: wsEvent.id_comando ?? '—',
          comando: `${wsEvent.comando || 'Paso DEMO'} [Paso ${wsEvent.paso}/${wsEvent.total_pasos}]`,
          codigo: wsEvent.codigo ?? '—',
          origen: `DEMO #${wsEvent.id_demo}`,
          fecha: dt.fecha,
          hora: dt.hora,
          stageLabel: 'Evento DEMO en vivo (No se agrega al historial individual)',
          isDemoEvent: true
        };
      } else if (tipo === 'demo_inicio') {
        this.lastMovement = {
          id_vehiculo: this.vehicleId,
          vehiculo: this.vehicleName,
          id_comando: '—',
          comando: `Inicio DEMO: ${wsEvent.nombre || `#${wsEvent.id_demo}`}`,
          codigo: 'DEMO',
          origen: `DEMO #${wsEvent.id_demo}`,
          fecha: dt.fecha,
          hora: dt.hora,
          stageLabel: 'Secuencia DEMO iniciada en servidor AWS',
          isDemoEvent: true
        };
      } else if (tipo === 'demo_fin' || tipo === 'demo_cancelada') {
        this.lastMovement = {
          id_vehiculo: this.vehicleId,
          vehiculo: this.vehicleName,
          id_comando: 13,
          comando: tipo === 'demo_fin' ? 'DEMO Finalizada (Detener)' : 'DEMO Cancelada (Detener)',
          codigo: 0,
          origen: `DEMO #${wsEvent.id_demo}`,
          fecha: dt.fecha,
          hora: dt.hora,
          stageLabel: 'Fin de secuencia DEMO · Historial individual intacto',
          isDemoEvent: true
        };
      }
      this.emitState();
    }
  }

  getSnapshot() {
    return {
      vehicleId: this.vehicleId,
      vehicleName: this.vehicleName,
      vehicleModel: this.vehicleModel,
      loading: this.loading,
      error: this.error,
      lastSyncAt: this.lastSyncAt,
      lastMovement: this.lastMovement ? { ...this.lastMovement } : null,
      historyRows: this.historyRows.slice()
    };
  }

  emitState() {
    if (typeof this.onStateChange === 'function') {
      this.onStateChange(this.getSnapshot());
    }
  }
}

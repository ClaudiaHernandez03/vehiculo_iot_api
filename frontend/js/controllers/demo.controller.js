/**
 * @file demo.controller.js
 * @description Controlador del Módulo DEMO (Ciclo de Excavación, Recorrido y Giro de Orugas,
 * Inspección Completa de Torre y Brazo).
 *
 * Reglas de arquitectura cumplidas:
 * - Consulta las DEMOs existentes desde GET /api/vehiculos/1/demos.
 * - Consulta la secuencia real de movimientos desde GET /api/demos/{id_demo}/movimientos.
 * - NO reproduce ni temporiza pasos en el navegador: el servidor FastAPI ejecuta la secuencia
 *   mediante POST /api/demos/{id_demo}/ejecutar y reporta el avance en tiempo real por WebSocket
 *   (`demo_inicio`, `demo_paso`, `demo_fin`, `demo_cancelada`).
 * - Soporta consulta de DEMO activa (`GET /api/vehiculos/1/demo/activa`) y cancelación inmediata
 *   (`POST /api/demos/{id_demo}/cancelar` y `POST /api/vehiculos/1/demo/cancelar`).
 * - Evita iniciar dos DEMOs simultáneas.
 */

import { APP_CONFIG } from '../config/api.config.js';
import { iotApiService } from '../services/iot.api.service.js';
import { toPositiveInteger } from '../utils/validators.js';

export class DemoController {
  /**
   * @param {object} [options]
   * @param {import('../services/iot.api.service.js').IotApiService} [options.apiService=iotApiService]
   * @param {(demoState: object) => void} [options.onStateChange]
   * @param {(feedback: object) => void} [options.onFeedback]
   * @param {(rdsPayload: object) => void} [options.onRdsEvidence]
   */
  constructor({
    apiService = iotApiService,
    onStateChange = null,
    onFeedback = null,
    onRdsEvidence = null
  } = {}) {
    this.apiService = apiService;
    this.onStateChange = onStateChange;
    this.onFeedback = onFeedback;
    this.onRdsEvidence = onRdsEvidence;

    this.demos = [];
    this.selectedDemoId = null;
    this.selectedDemoSteps = [];
    this.loadingSteps = false;

    this.activeDemo = null; // { id_demo, nombre, total_pasos, currentStep, currentCommand, statusText }
    this.requestInFlight = false;
  }

  /**
   * Indica si hay una DEMO actualmente en ejecución en el backend.
   * @returns {boolean}
   */
  isRunning() {
    return Boolean(this.activeDemo);
  }

  /**
   * Carga el listado de DEMOs desde `GET /api/vehiculos/1/demos` y comprueba si hay alguna activa.
   */
  async loadCatalogAndActiveState() {
    const [{ raw, demos }, activeCheck] = await Promise.all([
      this.apiService.getVehicleDemos(APP_CONFIG.VEHICLE_ID),
      this.apiService.getActiveDemo(APP_CONFIG.VEHICLE_ID)
    ]);

    if (typeof this.onRdsEvidence === 'function') {
      this.onRdsEvidence(raw);
    }

    this.demos = demos.filter((d) => Number(d?.activo) !== 0);

    if (activeCheck.activa && activeCheck.demo) {
      const activeId = toPositiveInteger(activeCheck.demo.id_demo);
      this.activeDemo = {
        id_demo: activeId,
        nombre: activeCheck.demo.nombre || `DEMO #${activeId}`,
        total_pasos: Number(activeCheck.demo.total_pasos) || 0,
        currentStep: 0,
        currentCommand: 'En curso en servidor...',
        statusText: 'Ejecutando en servidor AWS'
      };
      this.selectedDemoId = activeId;
    } else if (this.demos.length > 0 && !this.selectedDemoId) {
      this.selectedDemoId = toPositiveInteger(this.demos[0].id_demo);
    }

    if (this.selectedDemoId) {
      await this.selectDemo(this.selectedDemoId, { silent: true });
    } else {
      this.emitState();
    }
  }

  /**
   * Selecciona una DEMO del catálogo y consulta sus movimientos reales desde RDS (`GET /api/demos/{id_demo}/movimientos`).
   * @param {number} idDemo Identificador de la DEMO (1, 2 o 3)
   * @param {object} [options]
   * @param {boolean} [options.silent=false]
   */
  async selectDemo(idDemo, { silent = false } = {}) {
    const validId = toPositiveInteger(idDemo);
    if (!validId) return;

    this.selectedDemoId = validId;
    this.loadingSteps = true;
    this.emitState();

    try {
      const { raw, steps } = await this.apiService.getDemoMovements(validId);
      if (typeof this.onRdsEvidence === 'function') {
        this.onRdsEvidence(raw);
      }

      this.selectedDemoSteps = steps.slice().sort((a, b) => Number(a.orden) - Number(b.orden));
      this.loadingSteps = false;
      this.emitState();

      if (!silent) {
        const selectedMeta = this.demos.find((d) => Number(d.id_demo) === validId);
        this.emitFeedback({
          type: 'info',
          title: `DEMO #${validId} seleccionada`,
          message: `${selectedMeta?.nombre || 'Secuencia'} (${this.selectedDemoSteps.length} pasos configurados en RDS).`
        });
      }
    } catch (error) {
      this.loadingSteps = false;
      this.selectedDemoSteps = [];
      this.emitState();
      this.emitFeedback({
        type: 'danger',
        title: 'Error al consultar movimientos de DEMO',
        message: error?.message || 'No se pudieron recuperar los pasos de la DEMO seleccionada.'
      });
    }
  }

  /**
   * Solicita al backend iniciar la DEMO seleccionada (`POST /api/demos/{id_demo}/ejecutar`).
   */
  async startSelectedDemo() {
    if (this.requestInFlight) return;

    if (this.isRunning()) {
      this.emitFeedback({
        type: 'warning',
        title: 'DEMO ya en curso',
        message: `La DEMO "${this.activeDemo.nombre}" ya se está ejecutando en el servidor.`
      });
      return;
    }

    const validId = toPositiveInteger(this.selectedDemoId);
    if (!validId) {
      this.emitFeedback({
        type: 'warning',
        title: 'Selección requerida',
        message: 'Selecciona una de las 3 secuencias DEMO antes de iniciar.'
      });
      return;
    }

    this.requestInFlight = true;
    this.emitState();

    try {
      // Comprobar en el servidor que no se haya iniciado otra DEMO desde otra sesión
      const activeStatus = await this.apiService.getActiveDemo(APP_CONFIG.VEHICLE_ID);
      if (activeStatus.activa && activeStatus.demo) {
        const runningId = toPositiveInteger(activeStatus.demo.id_demo);
        this.activeDemo = {
          id_demo: runningId,
          nombre: activeStatus.demo.nombre || `DEMO #${runningId}`,
          total_pasos: Number(activeStatus.demo.total_pasos) || this.selectedDemoSteps.length,
          currentStep: 0,
          currentCommand: 'En curso en otra sesión...',
          statusText: 'Ejecutando en servidor AWS'
        };
        this.emitFeedback({
          type: 'warning',
          title: 'Otra DEMO activa detectada en el servidor',
          message: `El vehículo ya está ejecutando "${this.activeDemo.nombre}".`
        });
        return;
      }

      const response = await this.apiService.startDemo(validId);
      const selectedMeta = this.demos.find((d) => Number(d.id_demo) === validId);

      this.activeDemo = {
        id_demo: validId,
        nombre: response?.nombre || selectedMeta?.nombre || `DEMO #${validId}`,
        total_pasos: Number(response?.total_pasos) || this.selectedDemoSteps.length,
        currentStep: 0,
        currentCommand: 'Iniciando secuencia en servidor...',
        statusText: 'Iniciada (esperando eventos WSS)'
      };

      this.emitFeedback({
        type: 'success',
        title: `DEMO #${validId} iniciada en el servidor`,
        message:
          response?.mensaje ||
          'El backend FastAPI está ejecutando la secuencia temporizada y emitiendo eventos por WebSocket.'
      });
    } catch (error) {
      this.emitFeedback({
        type: 'danger',
        title: 'No se pudo iniciar la DEMO',
        message: error?.message || 'Error al solicitar POST /api/demos/{id_demo}/ejecutar.'
      });
    } finally {
      this.requestInFlight = false;
      this.emitState();
    }
  }

  /**
   * Solicita al backend cancelar la DEMO en curso (`POST /api/demos/{id_demo}/cancelar`
   * o `POST /api/vehiculos/1/demo/cancelar`).
   */
  async cancelActiveDemo() {
    if (this.requestInFlight) return false;
    this.requestInFlight = true;
    this.emitState();

    const targetDemoId = toPositiveInteger(this.activeDemo?.id_demo || this.selectedDemoId);

    try {
      let response;
      if (targetDemoId) {
        response = await this.apiService.cancelDemoById(targetDemoId);
      } else {
        response = await this.apiService.cancelVehicleDemo(APP_CONFIG.VEHICLE_ID);
      }

      this.activeDemo = null;
      this.emitFeedback({
        type: 'warning',
        title: 'Cancelación de DEMO enviada',
        message: response?.mensaje || 'Se solicitó al servidor detener la secuencia DEMO.'
      });
      return true;
    } catch (error) {
      // Intentar fallback por vehículo si falló por ID específico
      try {
        await this.apiService.cancelVehicleDemo(APP_CONFIG.VEHICLE_ID);
        this.activeDemo = null;
        return true;
      } catch {
        this.emitFeedback({
          type: 'danger',
          title: 'Error al cancelar DEMO',
          message: error?.message || 'No fue posible cancelar la DEMO activa.'
        });
        return false;
      }
    } finally {
      this.requestInFlight = false;
      this.emitState();
    }
  }

  /**
   * Procesa los eventos reales recibidos por WebSocket (`demo_inicio`, `demo_paso`, `demo_fin`, `demo_cancelada`).
   * @param {object} wsEvent Evento JSON enviado por el backend
   */
  handleWebSocketEvent(wsEvent) {
    if (!wsEvent || typeof wsEvent.tipo !== 'string') return;

    const tipo = wsEvent.tipo;

    if (tipo === 'demo_inicio') {
      const idDemo = toPositiveInteger(wsEvent.id_demo);
      this.activeDemo = {
        id_demo: idDemo,
        nombre: wsEvent.nombre || `DEMO #${idDemo}`,
        total_pasos: Number(wsEvent.total_pasos) || this.selectedDemoSteps.length,
        currentStep: 0,
        currentCommand: 'Secuencia iniciada',
        statusText: 'En ejecución (WSS)'
      };
      if (idDemo && this.selectedDemoId !== idDemo) {
        this.selectDemo(idDemo, { silent: true });
      } else {
        this.emitState();
      }
      return;
    }

    if (tipo === 'demo_paso') {
      const idDemo = toPositiveInteger(wsEvent.id_demo) || this.activeDemo?.id_demo;
      const stepNum = Number(wsEvent.paso || wsEvent.orden || 1);
      const totalSteps = Number(wsEvent.total_pasos || this.activeDemo?.total_pasos || this.selectedDemoSteps.length);
      const cmdName = wsEvent.comando || `Comando #${wsEvent.codigo || wsEvent.id_comando}`;

      this.activeDemo = {
        id_demo: idDemo,
        nombre: this.activeDemo?.nombre || `DEMO #${idDemo}`,
        total_pasos: totalSteps,
        currentStep: stepNum,
        currentCommand: `${cmdName} (Cód. ${wsEvent.codigo}) · ${wsEvent.duracion_ms}ms`,
        currentOrder: Number(wsEvent.orden || stepNum),
        statusText: `Ejecutando paso ${stepNum} de ${totalSteps}`
      };
      this.emitState();
      return;
    }

    if (tipo === 'demo_fin') {
      const completedName = this.activeDemo?.nombre || `DEMO #${wsEvent.id_demo}`;
      this.activeDemo = null;
      this.emitState();
      this.emitFeedback({
        type: 'success',
        title: `${completedName} finalizada`,
        message: `Secuencia completada en el servidor (${wsEvent.total_pasos_ejecutados || 0} pasos ejecutados).`
      });
      return;
    }

    if (tipo === 'demo_cancelada') {
      const cancelledName = this.activeDemo?.nombre || `DEMO #${wsEvent.id_demo}`;
      this.activeDemo = null;
      this.emitState();
      this.emitFeedback({
        type: 'warning',
        title: `${cancelledName} cancelada`,
        message: `Ejecución interrumpida en el paso #${wsEvent.paso_interrumpido ?? '—'} por solicitud del operador.`
      });
    }
  }

  getSnapshot() {
    const selectedDemo = this.demos.find((d) => Number(d.id_demo) === Number(this.selectedDemoId)) || null;
    return {
      demos: this.demos.slice(),
      selectedDemoId: this.selectedDemoId,
      selectedDemo,
      selectedDemoSteps: this.selectedDemoSteps.slice(),
      loadingSteps: this.loadingSteps,
      activeDemo: this.activeDemo ? { ...this.activeDemo } : null,
      requestInFlight: this.requestInFlight
    };
  }

  emitState() {
    if (typeof this.onStateChange === 'function') {
      this.onStateChange(this.getSnapshot());
    }
  }

  emitFeedback(feedback) {
    if (typeof this.onFeedback === 'function') {
      this.onFeedback({
        timestamp: new Date().toISOString(),
        ...feedback
      });
    }
  }
}

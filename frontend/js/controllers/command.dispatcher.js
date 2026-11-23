/**
 * @file command.dispatcher.js
 * @description Servicio único y centralizado para validación, exclusión mutua y despacho
 * de comandos de movimiento desde ambos Joysticks (Orugas y Brazo/Torre) y los Botones de Catálogo.
 *
 * Responsabilidades:
 * 1. Validar que todo código enviado pertenezca al catálogo real de `GET /api/vehiculos/1/comandos`.
 * 2. Evitar POST duplicados o ráfagas simultáneas incompatibles.
 * 3. Gestionar los estados de cada control: `normal`, `presionado`, `enviando`, `aceptado`, `error`, `deshabilitado`.
 * 4. Otorgar prioridad absoluta a la acción `DETENER` (abortar peticiones en vuelo, retornar controles
 *    a neutral, cancelar cualquier secuencia DEMO activa e invocar `POST /api/vehiculos/1/detener`).
 */

import { APP_CONFIG } from '../config/api.config.js';
import { iotApiService } from '../services/iot.api.service.js';
import { toPositiveInteger } from '../utils/validators.js';

export const CONTROL_VISUAL_STATES = Object.freeze({
  NORMAL: 'normal',
  PRESSED: 'presionado',
  SENDING: 'enviando',
  ACCEPTED: 'aceptado',
  ERROR: 'error',
  DISABLED: 'deshabilitado'
});

export class CommandDispatcher {
  /**
   * @param {object} [options]
   * @param {import('../services/iot.api.service.js').IotApiService} [options.apiService=iotApiService]
   * @param {(stateEvent: object) => void} [options.onControlStateChange]
   * @param {(feedback: object) => void} [options.onOperationFeedback]
   * @param {(rdsPayload: object) => void} [options.onRdsEvidence]
   * @param {() => boolean} [options.isDemoRunning]
   * @param {() => Promise<boolean>} [options.onEmergencyCancelDemo]
   */
  constructor({
    apiService = iotApiService,
    onControlStateChange = null,
    onOperationFeedback = null,
    onRdsEvidence = null,
    isDemoRunning = () => false,
    onEmergencyCancelDemo = async () => false,
    authorizeCommand = () => ({ allowed: true, reason: 'OK' })
  } = {}) {
    this.apiService = apiService;
    this.onControlStateChange = onControlStateChange;
    this.onOperationFeedback = onOperationFeedback;
    this.onRdsEvidence = onRdsEvidence;
    this.isDemoRunning = isDemoRunning;
    this.onEmergencyCancelDemo = onEmergencyCancelDemo;
    this.authorizeCommand = authorizeCommand;

    /** @type {Map<number, {id_comando: number, codigo: number, nombre: string, descripcion: string, activo: number}>} */
    this.catalogByCode = new Map([
      [1,  { id_comando: 1,  codigo: 1,  nombre: 'Mover brazo arriba',    descripcion: 'Subir brazo', activo: 1 }],
      [2,  { id_comando: 2,  codigo: 2,  nombre: 'Girar izquierda',       descripcion: 'Oruga izquierda', activo: 1 }],
      [3,  { id_comando: 3,  codigo: 3,  nombre: 'Mover brazo abajo',     descripcion: 'Bajar brazo', activo: 1 }],
      [4,  { id_comando: 4,  codigo: 4,  nombre: 'Girar derecha',         descripcion: 'Oruga derecha', activo: 1 }],
      [5,  { id_comando: 5,  codigo: 5,  nombre: 'Velocidad',             descripcion: 'Cambiar velocidad', activo: 1 }],
      [6,  { id_comando: 6,  codigo: 6,  nombre: 'Sonido',                descripcion: 'Encender/apagar sonido', activo: 1 }],
      [7,  { id_comando: 7,  codigo: 7,  nombre: 'Demo',                  descripcion: 'Modo demostración', activo: 1 }],
      [8,  { id_comando: 8,  codigo: 8,  nombre: 'Luz',                   descripcion: 'Encender/apagar luces', activo: 1 }],
      [9,  { id_comando: 9,  codigo: 9,  nombre: 'Avanzar',               descripcion: 'Desplazar adelante', activo: 1 }],
      [10, { id_comando: 10, codigo: 10, nombre: 'Girar torre izquierda', descripcion: 'Torre izquierda', activo: 1 }],
      [11, { id_comando: 11, codigo: 11, nombre: 'Retroceder',            descripcion: 'Desplazar atrás', activo: 1 }],
      [12, { id_comando: 12, codigo: 12, nombre: 'Girar torre derecha',   descripcion: 'Torre derecha', activo: 1 }]
    ]);

    this.activeAbortController = null;
    this.inFlightCode = null;
    this.lastJoystickCode = null;
    this.lastJoystickCodeByStick = {
      LEFT: null,
      RIGHT: null
    };
    this.lastDispatchTime = 0;
    this.stopEpoch = 0;
  }

  /**
   * Registra el catálogo real de comandos cargado desde `GET /api/vehiculos/1/comandos`.
   * @param {Array<object>} commands Lista de comandos de RDS
   */
  setCatalog(commands = []) {
    if (!Array.isArray(commands) || commands.length === 0) {
      return; // Conservar los 12 comandos predeterminados de la excavadora si el catálogo de red no ha cargado
    }
    this.catalogByCode.clear();

    for (const item of commands) {
      const code = toPositiveInteger(item?.codigo);
      const idComando = toPositiveInteger(item?.id_comando);
      if (code && idComando && Number(item?.activo) !== 0) {
        this.catalogByCode.set(code, {
          id_comando: idComando,
          codigo: code,
          nombre: String(item.nombre || `Comando ${code}`),
          descripcion: String(item.descripcion || ''),
          activo: 1
        });
      }
    }
  }

  /**
   * Verifica si un código existe y está activo en el catálogo del vehículo.
   * @param {number} code Código de comando (ej. 9 para Avanzar)
   * @returns {object|null}
   */
  getValidCommand(code) {
    const validCode = toPositiveInteger(code);
    if (!validCode) return null;
    let found = this.catalogByCode.get(validCode);
    if (!found && validCode >= 1 && validCode <= 12) {
      const fallbackNames = {
        1: 'Mover brazo arriba',
        2: 'Girar izquierda',
        3: 'Mover brazo abajo',
        4: 'Girar derecha',
        5: 'Velocidad',
        6: 'Sonido',
        7: 'Demo',
        8: 'Luz',
        9: 'Avanzar',
        10: 'Girar torre izquierda',
        11: 'Retroceder',
        12: 'Girar torre derecha'
      };
      found = {
        id_comando: validCode,
        codigo: validCode,
        nombre: fallbackNames[validCode] || `Comando ${validCode}`,
        descripcion: 'Comando estándar CAT-320',
        activo: 1
      };
      this.catalogByCode.set(validCode, found);
    }
    return found || null;
  }

  /**
   * Envía un comando desde cualquiera de los dos Joysticks o desde un botón táctil.
   * @param {number} code Código del comando en catálogo (1..12)
   * @param {'JOYSTICK'|'BUTTON'} [source='BUTTON'] Fuente de la interacción
   * @returns {Promise<object|null>}
   */
  async dispatchCommand(code, source = 'BUTTON') {
    // 1. Verificar autorización por rol y recurso (RBAC + BOLA/IDOR)
    if (typeof this.authorizeCommand === 'function') {
      const authCheck = this.authorizeCommand(APP_CONFIG.VEHICLE_ID, code);
      if (authCheck && authCheck.allowed === false) {
        this.emitStateChange(code, CONTROL_VISUAL_STATES.ERROR, source);
        this.emitFeedback({
          type: 'danger',
          stage: 'ERROR',
          title: 'Operación bloqueada por permisos',
          message: authCheck.reason || '403 Acceso restringido para el rol actual.'
        });
        return null;
      }
    }

    // 1b. Bloquear comandos manuales si hay una secuencia DEMO activa
    if (this.isDemoRunning()) {
      this.emitFeedback({
        type: 'warning',
        stage: 'SOLICITADO',
        title: 'DEMO en curso',
        message: 'Presiona DETENER o cancela la DEMO antes de operar manualmente.'
      });
      this.emitStateChange(code, CONTROL_VISUAL_STATES.DISABLED, source);
      return null;
    }

    // 2. Validar comando contra el catálogo real de RDS
    const commandMeta = this.getValidCommand(code);
    if (!commandMeta) {
      this.emitStateChange(code, CONTROL_VISUAL_STATES.ERROR, source);
      this.emitFeedback({
        type: 'danger',
        stage: 'ERROR',
        title: 'Comando no disponible',
        message: `Código #${code} fuera del catálogo activo.`
      });
      return null;
    }

    // 3. Evitar duplicación si el mismo comando sigue en vuelo
    if (this.inFlightCode === commandMeta.codigo) {
      return null;
    }

    // 4. Si hay una solicitud previa en vuelo, abortar la anterior para dar paso a la nueva dirección
    if (this.activeAbortController) {
      this.activeAbortController.abort();
      this.activeAbortController = null;
    }

    const currentStopEpoch = this.stopEpoch;
    const abortController = new AbortController();
    this.activeAbortController = abortController;
    this.inFlightCode = commandMeta.codigo;
    this.lastDispatchTime = Date.now();

    this.emitStateChange('ALL', CONTROL_VISUAL_STATES.NORMAL, source);
    this.emitStateChange(commandMeta.codigo, CONTROL_VISUAL_STATES.PRESSED, source, commandMeta);
    this.emitStateChange(commandMeta.codigo, CONTROL_VISUAL_STATES.SENDING, source, commandMeta);

    this.emitFeedback({
      type: 'info',
      stage: 'SOLICITADO',
      title: `Solicitado: ${commandMeta.nombre} (#${commandMeta.codigo})`,
      message: 'Enviando POST /api/movimientos a AWS...',
      command: commandMeta
    });

    try {
      const response = await this.apiService.sendMovement(commandMeta.id_comando, {
        vehicleId: APP_CONFIG.VEHICLE_ID,
        origen: APP_CONFIG.COMMAND_ORIGIN,
        signal: abortController.signal
      });

      // Si ocurrió DETENER mientras la petición regresaba, ignorar la respuesta tardía (Req. 5.4)
      if (this.stopEpoch !== currentStopEpoch) {
        this.emitStateChange(commandMeta.codigo, CONTROL_VISUAL_STATES.NORMAL, source, commandMeta);
        return null;
      }

      if (typeof this.onRdsEvidence === 'function') {
        this.onRdsEvidence(response);
      }

      this.emitStateChange(commandMeta.codigo, CONTROL_VISUAL_STATES.ACCEPTED, source, commandMeta);
      this.emitFeedback({
        type: 'success',
        stage: 'ACEPTADO_API',
        title: `Aceptado por API: ${commandMeta.nombre} (#${commandMeta.codigo})`,
        message: 'Persistido en Amazon RDS · Evento publicado en canal WSS.',
        command: commandMeta,
        response
      });

      return response;
    } catch (error) {
      if (error?.code === 'ABORTED' || error?.code === 'ABORTED_BY_OPERATOR') {
        this.emitStateChange(commandMeta.codigo, CONTROL_VISUAL_STATES.NORMAL, source, commandMeta);
        return null;
      }

      this.emitStateChange(commandMeta.codigo, CONTROL_VISUAL_STATES.ERROR, source, commandMeta);
      this.emitFeedback({
        type: 'danger',
        stage: 'ERROR',
        title: `No se pudo enviar ${commandMeta.nombre} (#${commandMeta.codigo})`,
        message: error?.message || 'Error de comunicación con la API.',
        command: commandMeta,
        error
      });
      return null;
    } finally {
      if (this.activeAbortController === abortController) {
        this.activeAbortController = null;
        this.inFlightCode = null;
      }
    }
  }

  /**
   * Gestiona las transiciones de dirección de cualquiera de los dos Joysticks sin generar POST repetitivos por píxel.
   * @param {number|null} targetCode Código de dirección o `null` para zona neutral
   * @param {string} [reason='TRANSITION'] Motivo de la transición
   * @param {'LEFT'|'RIGHT'} [joystickId='LEFT'] Identificador del joystick
   */
  async handleJoystickTransition(targetCode, reason = 'TRANSITION', joystickId = 'LEFT') {
    const stickKey = joystickId === 'RIGHT' ? 'RIGHT' : 'LEFT';

    if (targetCode === null) {
      const hadActiveDirection =
        this.lastJoystickCodeByStick[stickKey] !== null ||
        (stickKey === 'LEFT' && this.lastJoystickCode !== null);

      this.lastJoystickCodeByStick[stickKey] = null;
      this.lastJoystickCode = this.lastJoystickCodeByStick.LEFT || this.lastJoystickCodeByStick.RIGHT || null;

      if (hadActiveDirection && this.lastJoystickCode === null) {
        await this.executeStop({ source: 'JOYSTICK_RELEASE', reason });
      }
      return;
    }

    // Evitar enviar de nuevo el mismo código mientras se mantiene en la misma zona direccional
    if (this.lastJoystickCodeByStick[stickKey] === targetCode) {
      return;
    }

    this.lastJoystickCodeByStick[stickKey] = targetCode;
    this.lastJoystickCode = targetCode;
    await this.dispatchCommand(targetCode, 'JOYSTICK');
  }

  /**
   * Prioridad absoluta de DETENER:
   * 1. Incrementa época de paro para descartar respuestas tardías e invalida cualquier POST en vuelo.
   * 2. Restablece el estado de ambos joysticks y botones a neutral.
   * 3. Si hay una secuencia DEMO en ejecución en el backend, invoca su cancelación inmediata.
   * 4. Invoca `POST /api/vehiculos/1/detener` (cuando está disponible en el servicio) para registrar
   *    el paro en RDS y emitir el evento WebSocket `detener` (`codigo: 0`).
   *
   * @param {object} [options]
   * @param {'STOP_BUTTON'|'JOYSTICK_RELEASE'} [options.source='STOP_BUTTON']
   */
  async executeStop({ source = 'STOP_BUTTON' } = {}) {
    this.stopEpoch += 1;
    this.lastJoystickCode = null;
    this.lastJoystickCodeByStick.LEFT = null;
    this.lastJoystickCodeByStick.RIGHT = null;

    if (this.activeAbortController) {
      this.activeAbortController.abort();
      this.activeAbortController = null;
      this.inFlightCode = null;
    }

    this.emitStateChange('ALL', CONTROL_VISUAL_STATES.NORMAL, source);

    // Si existe una DEMO activa en el servidor, cancelarla prioritariamente
    if (this.isDemoRunning()) {
      try {
        await this.onEmergencyCancelDemo();
      } catch {
        // Continuar hacia la orden prioritaria de detener
      }
    }

    this.emitFeedback({
      type: 'warning',
      stage: 'SOLICITADO',
      title: source === 'STOP_BUTTON' ? 'DETENER solicitado' : 'Neutral · DETENER solicitado',
      message: 'Enviando orden prioritaria de detención a AWS...'
    });

    if (typeof this.apiService?.stopVehicle === 'function') {
      try {
        const stopRes = await this.apiService.stopVehicle(APP_CONFIG.VEHICLE_ID, {
          origen: source === 'STOP_BUTTON' ? 'BTN_DETENER_P41' : 'JOY_NEUTRAL_P41'
        });

        if (typeof this.onRdsEvidence === 'function' && stopRes?.base_datos) {
          this.onRdsEvidence(stopRes);
        }

        this.emitFeedback({
          type: 'warning',
          stage: 'ACEPTADO_API',
          title: 'DETENER aceptado por API (#0)',
          message: 'Vehículo en neutral · Evento detener publicado por WebSocket.',
          command: { id_comando: 13, codigo: 0, nombre: 'Detener' },
          response: stopRes
        });
      } catch (error) {
        this.emitFeedback({
          type: 'danger',
          stage: 'ERROR',
          title: 'Controles en neutral (aviso de red al detener)',
          message: error?.message || 'Controles restablecidos localmente.'
        });
      }
    } else {
      this.emitFeedback({
        type: 'info',
        stage: 'ACEPTADO_API',
        title: 'Controles en neutral',
        message: 'Posición de reposo restablecida.'
      });
    }
  }

  emitStateChange(code, visualState, source, commandMeta = null) {
    if (typeof this.onControlStateChange === 'function') {
      this.onControlStateChange({
        code,
        visualState,
        source,
        command: commandMeta
      });
    }
  }

  emitFeedback(feedback) {
    if (typeof this.onOperationFeedback === 'function') {
      this.onOperationFeedback({
        timestamp: new Date().toISOString(),
        ...feedback
      });
    }
  }
}

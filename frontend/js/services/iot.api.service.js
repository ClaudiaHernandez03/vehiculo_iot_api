/**
 * @file iot.api.service.js
 * @description Servicio de dominio que consume los 21 endpoints reales de `vehiculo_iot_api`
 * documentados en OpenAPI 3.1.0 (`https://vehiculo-iot-api.duckdns.org/openapi.json`)
 * para las 3 vistas integradas: CONTROL (4.1), MONITOREO (4.2) y ADMINISTRACIÓN (4.3).
 */

import { APP_CONFIG } from '../config/api.config.js';
import { httpClient } from './http.client.js';
import { isPlainObject, toPositiveInteger } from '../utils/validators.js';

export class IotApiService {
  /**
   * @param {import('./http.client.js').HttpClient} [client=httpClient]
   */
  constructor(client = httpClient) {
    this.client = client;
  }

  /**
   * 1. Verifica disponibilidad de la API AWS mediante GET /health
   */
  async checkApiHealth() {
    return this.client.get(APP_CONFIG.ENDPOINTS.HEALTH, { timeoutMs: 5000 });
  }

  /**
   * 2. Obtiene todos los vehículos registrados en Amazon RDS (`sp_obtener_vehiculos`)
   */
  async getAllVehicles() {
    const response = await this.client.get(APP_CONFIG.ENDPOINTS.VEHICULOS);
    const list = Array.isArray(response?.resultados) ? response.resultados : [];
    return {
      raw: response,
      vehicles: list
    };
  }

  /**
   * 3. Obtiene los datos de un vehículo específico (`sp_obtener_vehiculo`)
   * @param {number} [vehicleId=APP_CONFIG.VEHICLE_ID]
   */
  async getVehicleInfo(vehicleId = APP_CONFIG.VEHICLE_ID) {
    const response = await this.client.get(APP_CONFIG.ENDPOINTS.VEHICULO_DETALLE(vehicleId));
    const record = Array.isArray(response?.resultados) && response.resultados.length > 0
      ? response.resultados[0]
      : APP_CONFIG.VEHICLE_DEFAULTS;
    return {
      raw: response,
      vehicle: record
    };
  }

  /**
   * 4. Crea una nueva unidad de vehículo en Amazon RDS (`sp_crear_vehiculo`)
   */
  async createVehicle({ nombre, modelo, descripcion, microcontrolador }) {
    return this.client.post(APP_CONFIG.ENDPOINTS.VEHICULOS, {
      nombre: String(nombre || '').trim(),
      modelo: String(modelo || 'CAT-320').trim(),
      descripcion: String(descripcion || '').trim(),
      microcontrolador: String(microcontrolador || 'ESP8266').trim()
    });
  }

  /**
   * 5. Actualiza una unidad de vehículo existente (`sp_actualizar_vehiculo`)
   */
  async updateVehicle(idVehiculo, { nombre, modelo, descripcion, microcontrolador, activo = true }) {
    const validId = toPositiveInteger(idVehiculo);
    if (!validId) throw new Error('ID de vehículo inválido.');
    return this.client.put(APP_CONFIG.ENDPOINTS.VEHICULO_DETALLE(validId), {
      nombre: String(nombre || '').trim(),
      modelo: String(modelo || 'CAT-320').trim(),
      descripcion: String(descripcion || '').trim(),
      microcontrolador: String(microcontrolador || 'ESP8266').trim(),
      activo: Boolean(activo)
    });
  }

  /**
   * 6. Obtiene el catálogo real de comandos del vehículo (`sp_obtener_comandos_vehiculo`)
   * @param {number} [vehicleId=APP_CONFIG.VEHICLE_ID]
   */
  async getVehicleCommands(vehicleId = APP_CONFIG.VEHICLE_ID) {
    const response = await this.client.get(APP_CONFIG.ENDPOINTS.VEHICULO_COMANDOS(vehicleId));
    const list = Array.isArray(response?.resultados) ? response.resultados : [];
    return {
      raw: response,
      commands: list
    };
  }

  /**
   * 7. Obtiene el último estado registrado en Amazon RDS para el vehículo (`sp_obtener_estado`)
   * @param {number} [vehicleId=APP_CONFIG.VEHICLE_ID]
   */
  async getVehicleStatus(vehicleId = APP_CONFIG.VEHICLE_ID) {
    const response = await this.client.get(APP_CONFIG.ENDPOINTS.VEHICULO_ESTADO(vehicleId));
    const statusRecord = Array.isArray(response?.resultados) && response.resultados.length > 0
      ? response.resultados[0]
      : null;
    return {
      raw: response,
      status: statusRecord
    };
  }

  /**
   * 8. Obtiene los últimos 10 movimientos individuales persistidos en RDS (`sp_obtener_historial`)
   * @param {number} [vehicleId=APP_CONFIG.VEHICLE_ID]
   */
  async getVehicleHistory(vehicleId = APP_CONFIG.VEHICLE_ID) {
    const response = await this.client.get(APP_CONFIG.ENDPOINTS.VEHICULO_HISTORIAL(vehicleId));
    const items = Array.isArray(response?.resultados) ? response.resultados : [];
    return {
      raw: response,
      history: items.slice(0, APP_CONFIG.HISTORY_MAX_ROWS)
    };
  }

  /**
   * 9. Registra y activa un movimiento individual en el backend y RDS (`POST /api/movimientos`)
   */
  async sendMovement(idComando, { vehicleId = APP_CONFIG.VEHICLE_ID, origen = APP_CONFIG.COMMAND_ORIGIN, signal = null } = {}) {
    const validVehicleId = toPositiveInteger(vehicleId);
    const validCommandId = toPositiveInteger(idComando);

    if (!validVehicleId || !validCommandId) {
      throw new Error('Identificador de vehículo o comando inválido (debe ser un entero mayor a 0).');
    }

    const payload = {
      id_vehiculo: validVehicleId,
      id_comando: validCommandId,
      origen: String(origen || APP_CONFIG.COMMAND_ORIGIN).slice(0, 30)
    };

    return this.client.post(APP_CONFIG.ENDPOINTS.MOVIMIENTOS, payload, {
      externalSignal: signal
    });
  }

  /**
   * 10. Envía orden prioritaria de DETENER al backend y canal WebSocket (`POST /api/vehiculos/{id}/detener`)
   */
  async stopVehicle(vehicleId = APP_CONFIG.VEHICLE_ID, { origen = APP_CONFIG.COMMAND_ORIGIN } = {}) {
    const validVehicleId = toPositiveInteger(vehicleId) || APP_CONFIG.VEHICLE_ID;
    const payload = {
      origen: String(origen || APP_CONFIG.COMMAND_ORIGIN).slice(0, 30),
      esperar_confirmacion_ms: 120
    };
    return this.client.post(APP_CONFIG.ENDPOINTS.VEHICULO_DETENER(validVehicleId), payload);
  }

  /**
   * 11. Obtiene la lista de secuencias DEMO configuradas para el vehículo (`sp_obtener_demos`)
   */
  async getVehicleDemos(vehicleId = APP_CONFIG.VEHICLE_ID) {
    const response = await this.client.get(APP_CONFIG.ENDPOINTS.VEHICULO_DEMOS(vehicleId));
    const demos = Array.isArray(response?.resultados) ? response.resultados : [];
    return {
      raw: response,
      demos
    };
  }

  /**
   * 12. Crea una nueva secuencia DEMO en Amazon RDS (`sp_crear_demo`)
   */
  async createDemo({ id_vehiculo = APP_CONFIG.VEHICLE_ID, nombre, descripcion }) {
    return this.client.post(APP_CONFIG.ENDPOINTS.DEMOS, {
      id_vehiculo: toPositiveInteger(id_vehiculo) || APP_CONFIG.VEHICLE_ID,
      nombre: String(nombre || '').trim(),
      descripcion: String(descripcion || '').trim()
    });
  }

  /**
   * 13. Actualiza una secuencia DEMO existente (`sp_actualizar_demo`)
   */
  async updateDemo(idDemo, { nombre, descripcion, activo = true }) {
    const validId = toPositiveInteger(idDemo);
    if (!validId) throw new Error('ID de DEMO inválido.');
    return this.client.put(APP_CONFIG.ENDPOINTS.DEMO_DETALLE(validId), {
      nombre: String(nombre || '').trim(),
      descripcion: String(descripcion || '').trim(),
      activo: Boolean(activo)
    });
  }

  /**
   * 14. Elimina una secuencia DEMO en RDS (`sp_eliminar_demo`)
   */
  async deleteDemo(idDemo) {
    const validId = toPositiveInteger(idDemo);
    if (!validId) throw new Error('ID de DEMO inválido.');
    return this.client.delete(APP_CONFIG.ENDPOINTS.DEMO_DETALLE(validId));
  }

  /**
   * 15. Obtiene los movimientos ordenados que componen una DEMO (`sp_obtener_movimientos_demo`)
   */
  async getDemoMovements(idDemo) {
    const validDemoId = toPositiveInteger(idDemo);
    if (!validDemoId) {
      throw new Error('Identificador de DEMO inválido.');
    }
    const response = await this.client.get(APP_CONFIG.ENDPOINTS.DEMO_MOVIMIENTOS(validDemoId));
    const steps = Array.isArray(response?.resultados) ? response.resultados : [];
    return {
      raw: response,
      steps
    };
  }

  /**
   * 16. Agrega un paso a una secuencia DEMO (`sp_agregar_movimiento_demo`, orden 1..4)
   */
  async addDemoMovement(idDemo, { id_comando, orden, duracion_ms = 1500, pausa_ms = 500 }) {
    const validDemoId = toPositiveInteger(idDemo);
    if (!validDemoId) throw new Error('Identificador de DEMO inválido.');
    return this.client.post(APP_CONFIG.ENDPOINTS.DEMO_MOVIMIENTOS(validDemoId), {
      id_comando: Number(id_comando),
      orden: Number(orden),
      duracion_ms: Number(duracion_ms),
      pausa_ms: Number(pausa_ms)
    });
  }

  /**
   * 17. Actualiza un paso de DEMO (`sp_actualizar_movimiento_demo`)
   */
  async updateDemoMovement(idDemoMovimiento, { id_comando, orden, duracion_ms = 1500, pausa_ms = 500 }) {
    const validStepId = toPositiveInteger(idDemoMovimiento);
    if (!validStepId) throw new Error('Identificador de paso inválido.');
    return this.client.put(APP_CONFIG.ENDPOINTS.MOVIMIENTO_DEMO_DETALLE(validStepId), {
      id_comando: Number(id_comando),
      orden: Number(orden),
      duracion_ms: Number(duracion_ms),
      pausa_ms: Number(pausa_ms)
    });
  }

  /**
   * 18. Elimina un paso de DEMO (`sp_eliminar_movimiento_demo`)
   */
  async deleteDemoMovement(idDemoMovimiento) {
    const validStepId = toPositiveInteger(idDemoMovimiento);
    if (!validStepId) throw new Error('Identificador de paso inválido.');
    return this.client.delete(APP_CONFIG.ENDPOINTS.MOVIMIENTO_DEMO_DETALLE(validStepId));
  }

  /**
   * 19. Consulta si el vehículo tiene una DEMO activa en ejecución en el servidor
   */
  async getActiveDemo(vehicleId = APP_CONFIG.VEHICLE_ID) {
    const response = await this.client.get(APP_CONFIG.ENDPOINTS.VEHICULO_DEMO_ACTIVA(vehicleId));
    return {
      activa: Boolean(isPlainObject(response) && response.activa === true),
      demo: isPlainObject(response?.demo) ? response.demo : null,
      raw: response
    };
  }

  /**
   * 20. Inicia la ejecución temporizada en el servidor de una DEMO existente
   */
  async startDemo(idDemo) {
    const validDemoId = toPositiveInteger(idDemo);
    if (!validDemoId) {
      throw new Error('Seleccione una secuencia DEMO válida antes de iniciar.');
    }
    return this.client.post(APP_CONFIG.ENDPOINTS.DEMO_EJECUTAR(validDemoId));
  }

  /**
   * 21. Cancela una DEMO activa por su ID de DEMO o por ID de vehículo
   */
  async cancelDemoById(idDemo) {
    const validDemoId = toPositiveInteger(idDemo);
    if (!validDemoId) {
      throw new Error('Identificador de DEMO inválido para cancelar.');
    }
    return this.client.post(APP_CONFIG.ENDPOINTS.DEMO_CANCELAR(validDemoId));
  }

  async cancelVehicleDemo(vehicleId = APP_CONFIG.VEHICLE_ID) {
    return this.client.post(APP_CONFIG.ENDPOINTS.VEHICULO_DEMO_CANCELAR(vehicleId));
  }

  /**
   * 22. Obtiene desde Amazon RDS el estado RBAC 4.3 (`usuarios_iot`, `asignaciones_vehiculo`, `bitacora_auditoria_iot`)
   */
  async getAdminRbacState() {
    return this.client.get('/api/admin/rbac');
  }

  /**
   * 23. Registra un nuevo cliente comprador en `usuarios_iot` de Amazon RDS
   */
  async createAdminClient({ nombre, correo }) {
    return this.client.post('/api/admin/clientes', { nombre, correo });
  }

  /**
   * 24. Actualiza la asignación de propietario, estado comercial y consentimiento de soporte en Amazon RDS
   */
  async updateVehicleAssignment(idVehiculo, { id_cliente, estado_comercial, consentimiento_soporte = true }) {
    return this.client.put(`/api/admin/asignaciones/${Number(idVehiculo)}`, {
      id_cliente: Number(id_cliente),
      estado_comercial: String(estado_comercial || 'Vendido / Asignado'),
      consentimiento_soporte: Boolean(consentimiento_soporte)
    });
  }

  /**
   * 25. Persiste un evento en `bitacora_auditoria_iot` de Amazon RDS
   */
  async recordAdminAudit({ actor, rol, accion, recurso, resultado }) {
    return this.client.post('/api/admin/auditoria', {
      actor,
      rol,
      accion,
      recurso,
      resultado
    });
  }

  /**
   * 26. Obtiene los usuarios y perfiles registrados para inicio de sesión
   */
  async getAuthUsers() {
    return this.client.get('/api/auth/usuarios');
  }

  /**
   * 27. Inicia sesión en la plataforma y valida permisos en Amazon RDS
   */
  async login({ correo, rol, password = null }) {
    return this.client.post('/api/auth/login', { correo, rol, password });
  }
}

export const iotApiService = new IotApiService();

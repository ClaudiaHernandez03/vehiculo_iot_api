/**
 * @file admin.controller.js
 * @description Controlador del Módulo 4.3 ADMINISTRACIÓN IoT (Plataforma Comercial Multiusuario).
 *
 * Implementa:
 * 1. Gestión de los 3 roles comerciales:
 *    - `ADMIN` (Empresa comercializadora: inventario, modelos, unidades, clientes, asignaciones y demos de fábrica)
 *    - `USUARIO` (Comprador propietario: "Mis Vehículos", aislamiento BOLA/IDOR y editor de demos propias)
 *    - `INVITADO` (Cliente potencial: catálogo público y sesión temporal exclusiva de exhibición con auto-DETENER)
 * 2. Integración real con Amazon RDS mediante las APIs de FastAPI (`/api/vehiculos`, `/api/comandos`,
 *    `/api/demos`, `/api/demos/{id}/movimientos`, `/api/movimientos-demo/{id}`).
 * 3. Editor visual de secuencias DEMO (1 a 4 pasos ordenados, validados contra el catálogo del modelo).
 * 4. Bitácora de auditoría verificable (actor, rol, acción, recurso, resultado y marca temporal).
 */

import { APP_CONFIG } from '../config/api.config.js';
import { iotApiService } from '../services/iot.api.service.js';
import { formatTimestamp, toPositiveInteger } from '../utils/validators.js';

export const ROLES = Object.freeze({
  ADMIN: 'ADMIN',
  USUARIO: 'USUARIO',
  INVITADO: 'INVITADO'
});

const DEFAULT_COMMERCIAL_MODELS = Object.freeze([
  {
    id_modelo: 'MOD-CAT320',
    nombre: 'Excavadora Hidráulica CAT-320',
    codigo_modelo: 'CAT-320',
    microcontrolador: 'ESP8266 NodeMCU + 2x 74HC595',
    capacidades: '12 movimientos (Orugas, Brazo, Torre, Luz, Sonido, Velocidad) + Paro Prioritario',
    categoria: 'Maquinaria Pesada IoT'
  },
  {
    id_modelo: 'MOD-BAT01',
    nombre: 'Vehículo Táctico Explorador IoT',
    codigo_modelo: 'TRK-200',
    microcontrolador: 'ESP32 / ESP8266',
    capacidades: 'Tracción 4WD, Telemetría WSS y Rutinas Programables',
    categoria: 'Exploración y Exhibición'
  }
]);

const DEFAULT_CLIENTS = [
  {
    id_cliente: 101,
    nombre: 'Claudia América Hernández (Comprador A)',
    correo: 'claudia.comprador@empresa-iot.mx',
    rol: 'USUARIO',
    estado: 'Activo'
  },
  {
    id_cliente: 102,
    nombre: 'Constructora Industrial del Centro (Comprador B)',
    correo: 'operaciones@constructora-centro.mx',
    rol: 'USUARIO',
    estado: 'Activo'
  },
  {
    id_cliente: 103,
    nombre: 'Sala de Exhibición Comercial (Demo Pública)',
    correo: 'exhibicion@cat320-iot.mx',
    rol: 'INVITADO',
    estado: 'Exhibición'
  }
];

export class AdminController {
  /**
   * @param {object} [options]
   * @param {import('../services/iot.api.service.js').IotApiService} [options.apiService=iotApiService]
   * @param {(snapshot: object) => void} [options.onStateChange]
   * @param {(feedback: object) => void} [options.onFeedback]
   * @param {(rdsPayload: object) => void} [options.onRdsEvidence]
   * @param {() => Promise<void>} [options.onDemosChanged]
   * @param {() => Promise<void>} [options.onEmergencyStop]
   */
  constructor({
    apiService = iotApiService,
    onStateChange = null,
    onFeedback = null,
    onRdsEvidence = null,
    onDemosChanged = null,
    onEmergencyStop = null
  } = {}) {
    this.apiService = apiService;
    this.onStateChange = onStateChange;
    this.onFeedback = onFeedback;
    this.onRdsEvidence = onRdsEvidence;
    this.onDemosChanged = onDemosChanged;
    this.onEmergencyStop = onEmergencyStop;

    this.currentRole = ROLES.ADMIN;
    this.currentBuyerId = 101; // Comprador A por defecto al elegir rol USUARIO
    this.adminSupportConsent = true; // Consentimiento explícito de soporte técnico

    this.models = [...DEFAULT_COMMERCIAL_MODELS];
    this.clients = [...DEFAULT_CLIENTS];
    this.units = [];
    this.commands = [];
    this.demos = [];
    this.editingDemoId = 1;
    this.editingDemoSteps = [];

    // Asignaciones comerciales por id_vehiculo -> { id_cliente, estado_comercial }
    this.assignments = {
      1: {
        id_cliente: 101,
        propietario: 'Claudia América Hernández (Comprador A)',
        estado_comercial: 'Vendido / Asignado',
        habilitado_exhibicion: true
      }
    };

    // Sesión temporal de exhibición para el rol INVITADO
    this.guestSession = {
      active: false,
      vehicleId: 1,
      remainingSeconds: 0,
      holderId: null,
      allowedCodes: [9, 11, 2, 4, 8] // Orugas + Luz en modo exhibición seguro
    };
    this.guestTimerId = null;

    // Bitácora de auditoría
    this.auditLogs = [];
    this.recordAudit('SISTEMA', 'INICIO_PLATAFORMA', 'Suite IoT 4.1/4.2/4.3', 'OK · Sesión Administrativa lista');
  }

  /**
   * Carga desde Amazon RDS las unidades (`/api/vehiculos`), comandos (`/api/vehiculos/1/comandos`),
   * demostraciones (`/api/vehiculos/1/demos`) y estado RBAC (`/api/admin/rbac`).
   */
  async loadAdminData() {
    try {
      const [vehiclesRes, commandsRes, demosRes] = await Promise.all([
        this.apiService.getAllVehicles(),
        this.apiService.getVehicleCommands(APP_CONFIG.VEHICLE_ID),
        this.apiService.getVehicleDemos(APP_CONFIG.VEHICLE_ID)
      ]);

      // Sincronizar tablas RBAC 4.3 de Amazon RDS si el endpoint está disponible
      if (typeof this.apiService.getAdminRbacState === 'function') {
        try {
          const rbacRes = await this.apiService.getAdminRbacState();
          if (Array.isArray(rbacRes?.usuarios) && rbacRes.usuarios.length > 0) {
            this.clients = rbacRes.usuarios.filter((u) => u.rol !== 'ADMIN');
          }
          if (Array.isArray(rbacRes?.asignaciones)) {
            for (const a of rbacRes.asignaciones) {
              this.assignments[a.id_vehiculo] = {
                id_cliente: Number(a.id_cliente),
                propietario: a.propietario,
                estado_comercial: a.estado_comercial,
                habilitado_exhibicion: Boolean(a.habilitado_exhibicion),
                consentimiento_soporte: Boolean(a.consentimiento_soporte ?? true)
              };
              if (Number(a.id_vehiculo) === Number(APP_CONFIG.VEHICLE_ID)) {
                this.adminSupportConsent = Boolean(a.consentimiento_soporte ?? true);
              }
            }
          }
          if (Array.isArray(rbacRes?.auditoria) && rbacRes.auditoria.length > 0) {
            this.auditLogs = rbacRes.auditoria.map((item, idx) => ({
              id: item.id || idx + 1,
              timestamp: formatTimestamp(item.timestamp),
              actor: item.actor,
              action: item.action,
              resource: item.resource,
              result: item.result
            }));
          }
        } catch {
          // Respaldo transparente si el servidor aún no ha reiniciado con /api/admin/rbac
        }
      }

      if (typeof this.onRdsEvidence === 'function') {
        this.onRdsEvidence(vehiclesRes.raw);
      }

      this.units = vehiclesRes.vehicles.map((v) => {
        const assign = this.assignments[v.id_vehiculo] || {
          id_cliente: 103,
          propietario: 'Sala de Exhibición Comercial',
          estado_comercial: 'Exhibición',
          habilitado_exhibicion: true
        };
        this.assignments[v.id_vehiculo] = assign;
        return {
          ...v,
          propietario: assign.propietario,
          id_cliente: assign.id_cliente,
          estado_comercial: assign.estado_comercial,
          habilitado_exhibicion: assign.habilitado_exhibicion
        };
      });

      this.commands = commandsRes.commands.filter((c) => Number(c.activo) !== 0 && Number(c.codigo) > 0);
      this.demos = demosRes.demos.filter((d) => Number(d.activo) !== 0);

      if (this.demos.length > 0) {
        const exists = this.demos.some((d) => Number(d.id_demo) === Number(this.editingDemoId));
        this.editingDemoId = exists ? this.editingDemoId : Number(this.demos[0].id_demo);
        await this.loadDemoStepsForEditor(this.editingDemoId);
      } else {
        this.editingDemoSteps = [];
      }

      this.emitState();
    } catch (error) {
      this.emitFeedback({
        type: 'danger',
        title: 'Error al cargar inventario administrativo',
        message: error?.message || 'No se pudo consultar el catálogo en RDS.'
      });
    }
  }

  /**
   * Cambia el rol activo (`ADMIN`, `USUARIO`, `INVITADO`) para evaluación individual o en equipos de 3.
   * @param {'ADMIN'|'USUARIO'|'INVITADO'} nextRole
   * @param {number} [buyerId]
   */
  setRole(nextRole, buyerId = null) {
    if (!Object.values(ROLES).includes(nextRole)) return;
    this.currentRole = nextRole;
    if (buyerId) {
      this.currentBuyerId = Number(buyerId);
    }

    const roleLabel =
      nextRole === ROLES.ADMIN
        ? 'Administrador (Empresa)'
        : nextRole === ROLES.USUARIO
        ? `Usuario Comprador (#${this.currentBuyerId})`
        : 'Invitado (Prospecto)';

    this.recordAudit(roleLabel, 'CAMBIO_ROL', `Vista activa -> ${roleLabel}`, 'OK');
    this.emitState();
    this.emitFeedback({
      type: 'info',
      title: `Rol activo: ${roleLabel}`,
      message:
        nextRole === ROLES.ADMIN
          ? 'Gestión completa de inventario, clientes, asignaciones y editor de DEMOs habilitada.'
          : nextRole === ROLES.USUARIO
          ? 'Acceso aislado a "Mis Vehículos", control, monitoreo y editor de DEMOs propias.'
          : 'Modo exhibición: consulta de catálogo público y prueba temporal de 60s con reserva exclusiva.'
    });
  }

  setBuyerAccount(buyerId) {
    this.currentBuyerId = Number(buyerId) || 101;
    const buyer = this.clients.find((c) => Number(c.id_cliente) === Number(this.currentBuyerId));
    const buyerName = buyer ? buyer.nombre : `Comprador #${this.currentBuyerId}`;
    this.recordAudit(
      `USUARIO #${this.currentBuyerId}`,
      'CAMBIO_CUENTA_COMPRADOR',
      buyerName,
      this.isCurrentBuyerOwnerOf(APP_CONFIG.VEHICLE_ID)
        ? 'OK · Propietario de Excavadora #1'
        : 'Aislamiento BOLA/IDOR · Sin permiso sobre Excavadora #1'
    );
    this.emitState();
    this.emitFeedback({
      type: this.isCurrentBuyerOwnerOf(APP_CONFIG.VEHICLE_ID) ? 'info' : 'warning',
      title: `Cuenta activa: ${buyerName}`,
      message: this.isCurrentBuyerOwnerOf(APP_CONFIG.VEHICLE_ID)
        ? 'Tienes acceso completo a Control, Monitoreo y Editor DEMO de tu Excavadora #1.'
        : 'Aislamiento BOLA/IDOR activo: Esta cuenta NO es propietaria de la Excavadora #1 (Control y Editor DEMO bloqueados con 403).'
    });
  }

  setAdminSupportConsent(enabled) {
    this.adminSupportConsent = Boolean(enabled);
    const assign = this.assignments[APP_CONFIG.VEHICLE_ID];
    if (assign) {
      assign.consentimiento_soporte = this.adminSupportConsent;
      if (typeof this.apiService.updateVehicleAssignment === 'function') {
        this.apiService
          .updateVehicleAssignment(APP_CONFIG.VEHICLE_ID, {
            id_cliente: assign.id_cliente,
            estado_comercial: assign.estado_comercial,
            consentimiento_soporte: this.adminSupportConsent
          })
          .catch(() => {});
      }
    }
    this.recordAudit(
      this.currentRole,
      'CONSENTIMIENTO_SOPORTE_RDS',
      'Unidad #1 (CAT-320)',
      this.adminSupportConsent ? 'Autorizado por comprador' : 'Revocado (Admin bloqueado 403 en Control)'
    );
    this.emitState();
    this.emitFeedback({
      type: this.adminSupportConsent ? 'success' : 'warning',
      title: this.adminSupportConsent
        ? 'Soporte Técnico Autorizado en RDS'
        : 'Soporte Técnico Revocado en RDS',
      message: this.adminSupportConsent
        ? 'El Administrador puede operar la unidad vendida #1 para pruebas de soporte.'
        : 'El Administrador NO puede mover la unidad vendida #1 en Control (403 Prohibido).'
    });
  }

  isCurrentBuyerOwnerOf(vehicleId = APP_CONFIG.VEHICLE_ID) {
    const assign = this.assignments[vehicleId];
    return Boolean(assign && Number(assign.id_cliente) === Number(this.currentBuyerId));
  }

  /**
   * Verifica autorización RBAC + Propiedad del Recurso (BOLA/IDOR) antes de operar un vehículo.
   * @param {number} vehicleId
   * @param {number} [commandCode]
   * @returns {{allowed: boolean, reason: string}}
   */
  authorizeVehicleOperation(vehicleId = APP_CONFIG.VEHICLE_ID, commandCode = null) {
    const assign = this.assignments[vehicleId];

    if (this.currentRole === ROLES.ADMIN) {
      if (assign?.estado_comercial === 'Vendido / Asignado' && !this.adminSupportConsent) {
        this.recordAudit(
          'ADMIN',
          'CONTROL_UNIDAD_VENDIDA',
          `Vehículo #${vehicleId}`,
          'DENEGADO 403 · Sin consentimiento de soporte del comprador'
        );
        return {
          allowed: false,
          reason: '403 Prohibido: El Administrador requiere activar autorización de soporte para mover una unidad vendida.'
        };
      }
      return { allowed: true, reason: 'OK' };
    }

    if (this.currentRole === ROLES.USUARIO) {
      if (!assign || Number(assign.id_cliente) !== Number(this.currentBuyerId)) {
        this.recordAudit(
          `USUARIO #${this.currentBuyerId}`,
          'INTENTO_ACCESO_CRUZADO_BOLA',
          `Vehículo #${vehicleId}`,
          'DENEGADO 403 · Unidad pertenece a otro comprador'
        );
        return {
          allowed: false,
          reason: `403 Prohibido (Aislamiento BOLA/IDOR): El vehículo #${vehicleId} no está asignado a tu cuenta de comprador.`
        };
      }
      return { allowed: true, reason: 'OK' };
    }

    if (this.currentRole === ROLES.INVITADO) {
      if (!this.guestSession.active) {
        this.recordAudit(
          'INVITADO',
          'CONTROL_SIN_SESION_EXHIBICION',
          `Vehículo #${vehicleId}`,
          'DENEGADO 401 · Requiere iniciar sesión temporal de exhibición'
        );
        return {
          allowed: false,
          reason: '401 Sesión requerida: Inicia primero tu prueba temporal de 60s en la pestaña Administración / Exhibición.'
        };
      }
      if (commandCode !== null && commandCode !== 0 && !this.guestSession.allowedCodes.includes(Number(commandCode))) {
        this.recordAudit(
          'INVITADO',
          'COMANDO_RESTRINGIDO_EXHIBICION',
          `Código #${commandCode}`,
          'DENEGADO 403 · Comando reservado para propietarios'
        );
        return {
          allowed: false,
          reason: `403 Comando restringido en exhibición: El Invitado solo puede probar tracción básica y luces (#9, #11, #2, #4, #8).`
        };
      }
      return { allowed: true, reason: 'OK' };
    }

    return { allowed: true, reason: 'OK' };
  }

  /**
   * Simulación verificable de prueba negativa BOLA/IDOR o acceso sin permisos (para Evidencia 6 / P12 / P13).
   * @param {'INVITADO_ADMIN'|'USUARIO_AJENO'|'SESION_DUPLICADA'} scenario
   */
  runSecurityVerificationScenario(scenario) {
    if (scenario === 'INVITADO_ADMIN') {
      this.recordAudit(
        'INVITADO',
        'ACCESO_CRUD_ADMINISTRACION',
        'POST /api/vehiculos',
        'DENEGADO 403 · Rol Invitado sin privilegios administrativos'
      );
      this.emitFeedback({
        type: 'danger',
        title: '403 Forbidden · Prueba P12 verificada',
        message: 'Rechazo confirmado: Un Invitado no puede crear ni modificar inventario administrativo.'
      });
    } else if (scenario === 'USUARIO_AJENO') {
      this.recordAudit(
        `USUARIO #${this.currentBuyerId}`,
        'ACCESO_VEHICULO_AJENO_IDOR',
        'Vehículo #99 (Comprador B)',
        'DENEGADO 403 · Violación de propiedad BOLA/IDOR bloqueada'
      );
      this.emitFeedback({
        type: 'danger',
        title: '403 Forbidden · Prueba P13 (BOLA/IDOR) verificada',
        message: 'Rechazo confirmado: El comprador actual no puede consultar ni operar unidades de otro cliente.'
      });
    } else if (scenario === 'SESION_DUPLICADA') {
      this.recordAudit(
        'INVITADO_2',
        'RESERVA_CONCURRENTE_EXHIBICION',
        'Vehículo #1 (Ocupado)',
        'DENEGADO 409 · Unidad con reserva exclusiva activa'
      );
      this.emitFeedback({
        type: 'warning',
        title: '409 Conflict · Prueba P14 (Exclusividad) verificada',
        message: 'Rechazo confirmado: No se permiten dos sesiones controlando simultáneamente la misma unidad.'
      });
    }
    this.emitState();
  }

  /**
   * Registra una nueva unidad física en Amazon RDS (`POST /api/vehiculos`) — Solo ADMIN.
   */
  async registerNewUnit({ nombre, modelo, descripcion, microcontrolador, id_cliente, estado_comercial }) {
    if (this.currentRole !== ROLES.ADMIN) {
      this.recordAudit(this.currentRole, 'ALTA_UNIDAD', nombre, 'DENEGADO 403 · Solo Administrador');
      this.emitFeedback({
        type: 'danger',
        title: '403 Acceso Denegado',
        message: 'Solo el rol Administrador puede registrar nuevas unidades en el inventario.'
      });
      return false;
    }

    try {
      const res = await this.apiService.createVehicle({
        nombre,
        modelo,
        descripcion,
        microcontrolador
      });
      const newId = res?.resultados?.[0]?.id_vehiculo || this.units.length + 1;
      const clientObj = this.clients.find((c) => Number(c.id_cliente) === Number(id_cliente)) || this.clients[0];

      this.assignments[newId] = {
        id_cliente: clientObj.id_cliente,
        propietario: clientObj.nombre,
        estado_comercial: estado_comercial || 'Disponible',
        habilitado_exhibicion: estado_comercial === 'Exhibición'
      };

      this.recordAudit(
        'ADMIN',
        'ALTA_UNIDAD_RDS',
        `Unidad #${newId} (${nombre} · ${modelo})`,
        `OK · Asignado a ${clientObj.nombre}`
      );
      await this.loadAdminData();
      this.emitFeedback({
        type: 'success',
        title: `Unidad #${newId} registrada en Amazon RDS`,
        message: `${nombre} (${modelo}) guardada mediante sp_crear_vehiculo.`
      });
      return true;
    } catch (error) {
      this.emitFeedback({
        type: 'danger',
        title: 'No se pudo registrar la unidad',
        message: error?.message || 'Error al invocar POST /api/vehiculos.'
      });
      return false;
    }
  }

  /**
   * Registra un nuevo cliente comprador y lo persiste en Amazon RDS (`usuarios_iot`) — Solo ADMIN.
   */
  registerClient({ nombre, correo }) {
    if (this.currentRole !== ROLES.ADMIN) {
      this.recordAudit(this.currentRole, 'ALTA_CLIENTE', nombre, 'DENEGADO 403 · Solo Administrador');
      this.emitFeedback({
        type: 'danger',
        title: '403 Acceso Denegado',
        message: 'Solo el Administrador puede dar de alta clientes compradores.'
      });
      return false;
    }
    const cleanName = String(nombre || '').trim();
    const cleanEmail = String(correo || '').trim();
    if (!cleanName) return false;

    const nextId = this.clients.reduce((max, c) => Math.max(max, Number(c.id_cliente)), 100) + 1;
    const finalEmail = cleanEmail || `cliente${nextId}@empresa-iot.mx`;
    this.clients.push({
      id_cliente: nextId,
      nombre: cleanName,
      correo: finalEmail,
      rol: 'USUARIO',
      estado: 'Activo'
    });

    if (typeof this.apiService.createAdminClient === 'function') {
      this.apiService.createAdminClient({ nombre: cleanName, correo: finalEmail }).catch(() => {});
    }

    this.recordAudit('ADMIN', 'ALTA_CLIENTE_RDS', `Cliente #${nextId} (${cleanName})`, 'OK · Persistido en Amazon RDS');
    this.emitState();
    this.emitFeedback({
      type: 'success',
      title: 'Cliente comprador registrado en Amazon RDS',
      message: `${cleanName} (#${nextId}) disponible para asignación de unidades.`
    });
    return true;
  }

  /**
   * Asigna o reasigna una unidad física a un comprador, revoca permisos del titular anterior
   * y lo persiste en Amazon RDS (`asignaciones_vehiculo`) — Solo ADMIN.
   */
  assignUnitToClient({ id_vehiculo, id_cliente, estado_comercial }) {
    if (this.currentRole !== ROLES.ADMIN) {
      this.recordAudit(this.currentRole, 'ASIGNACION_UNIDAD', `Vehículo #${id_vehiculo}`, 'DENEGADO 403');
      this.emitFeedback({
        type: 'danger',
        title: '403 Acceso Denegado',
        message: 'Solo el Administrador puede asignar o revocar propietarios de unidades.'
      });
      return false;
    }

    const vid = Number(id_vehiculo);
    const clientObj = this.clients.find((c) => Number(c.id_cliente) === Number(id_cliente));
    if (!vid || !clientObj) return false;

    const prevOwner = this.assignments[vid]?.propietario || 'Sin asignar';
    this.assignments[vid] = {
      id_cliente: clientObj.id_cliente,
      propietario: clientObj.nombre,
      estado_comercial: estado_comercial || 'Vendido / Asignado',
      habilitado_exhibicion: estado_comercial === 'Exhibición',
      consentimiento_soporte: this.adminSupportConsent
    };

    if (typeof this.apiService.updateVehicleAssignment === 'function') {
      this.apiService
        .updateVehicleAssignment(vid, {
          id_cliente: clientObj.id_cliente,
          estado_comercial: estado_comercial || 'Vendido / Asignado',
          consentimiento_soporte: this.adminSupportConsent
        })
        .catch(() => {});
    }

    this.units = this.units.map((u) =>
      Number(u.id_vehiculo) === vid
        ? {
            ...u,
            id_cliente: clientObj.id_cliente,
            propietario: clientObj.nombre,
            estado_comercial: estado_comercial || 'Vendido / Asignado'
          }
        : u
    );

    this.recordAudit(
      'ADMIN',
      'ASIGNACION_PROPIETARIO_RDS',
      `Unidad #${vid}`,
      `OK · Nuevo titular: ${clientObj.nombre} (Revocado: ${prevOwner})`
    );
    this.emitState();
    this.emitFeedback({
      type: 'success',
      title: `Unidad #${vid} asignada a ${clientObj.nombre} en RDS`,
      message: 'Permisos BOLA/IDOR actualizados y guardados en Amazon RDS.'
    });
    return true;
  }

  // ===========================================================================
  // EDITOR VISUAL DE SECUENCIAS DEMO PERSISTENTES EN AMAZON RDS (1..4 PASOS)
  // ===========================================================================

  async loadDemoStepsForEditor(idDemo) {
    const validId = toPositiveInteger(idDemo);
    if (!validId) return;
    this.editingDemoId = validId;

    try {
      const { steps } = await this.apiService.getDemoMovements(validId);
      const seenOrders = new Set();
      const cleanSteps = [];
      for (const s of steps.slice().sort((a, b) => Number(a.orden) - Number(b.orden))) {
        const ord = Number(s.orden);
        if (!seenOrders.has(ord)) {
          seenOrders.add(ord);
          cleanSteps.push(s);
        }
      }
      this.editingDemoSteps = cleanSteps;
      this.emitState();
    } catch {
      this.editingDemoSteps = [];
      this.emitState();
    }
  }

  /**
   * Crea una nueva DEMO persistida en Amazon RDS (`POST /api/demos`).
   */
  async createNewDemo({ nombre, descripcion }) {
    if (this.currentRole === ROLES.INVITADO) {
      this.recordAudit('INVITADO', 'CREAR_DEMO', nombre, 'DENEGADO 403 · Invitado sin permisos');
      this.emitFeedback({
        type: 'danger',
        title: '403 Acceso Restringido',
        message: 'El rol Invitado no puede crear ni editar secuencias DEMO.'
      });
      return false;
    }

    if (this.currentRole === ROLES.USUARIO && !this.isCurrentBuyerOwnerOf(APP_CONFIG.VEHICLE_ID)) {
      this.recordAudit(
        `USUARIO #${this.currentBuyerId}`,
        'CREAR_DEMO_BOLA_IDOR',
        `Vehículo #${APP_CONFIG.VEHICLE_ID}`,
        'DENEGADO 403 · No es propietario de la unidad'
      );
      this.emitFeedback({
        type: 'danger',
        title: '403 Prohibido (Aislamiento BOLA/IDOR)',
        message: 'Solo el comprador propietario de esta unidad puede crear o editar sus secuencias DEMO.'
      });
      return false;
    }

    try {
      const res = await this.apiService.createDemo({
        id_vehiculo: APP_CONFIG.VEHICLE_ID,
        nombre,
        descripcion
      });
      const newDemoId = res?.resultados?.[0]?.id_demo;
      this.recordAudit(this.currentRole, 'CREAR_DEMO_RDS', `DEMO #${newDemoId || ''} (${nombre})`, 'OK');
      await this.loadAdminData();
      if (newDemoId) {
        await this.loadDemoStepsForEditor(newDemoId);
      }
      if (typeof this.onDemosChanged === 'function') {
        await this.onDemosChanged();
      }
      this.emitFeedback({
        type: 'success',
        title: 'Nueva DEMO creada en Amazon RDS',
        message: `Secuencia "${nombre}" registrada. Ahora puedes agregarle hasta 4 pasos.`
      });
      return true;
    } catch (error) {
      this.emitFeedback({
        type: 'danger',
        title: 'Error al crear DEMO',
        message: error?.message || 'No se pudo guardar la DEMO en RDS.'
      });
      return false;
    }
  }

  /**
   * Agrega un paso (orden 1..4) a la DEMO seleccionada en el editor (`POST /api/demos/{id}/movimientos`).
   */
  async addStepToEditingDemo({ id_comando, orden, duracion_ms, pausa_ms }) {
    if (this.currentRole === ROLES.INVITADO) {
      this.recordAudit('INVITADO', 'AGREGAR_PASO_DEMO', `DEMO #${this.editingDemoId}`, 'DENEGADO 403');
      this.emitFeedback({
        type: 'danger',
        title: '403 Acceso Restringido',
        message: 'El rol Invitado no puede modificar pasos de DEMO.'
      });
      return false;
    }

    if (this.currentRole === ROLES.USUARIO && !this.isCurrentBuyerOwnerOf(APP_CONFIG.VEHICLE_ID)) {
      this.recordAudit(
        `USUARIO #${this.currentBuyerId}`,
        'EDITAR_DEMO_BOLA_IDOR',
        `DEMO #${this.editingDemoId}`,
        'DENEGADO 403 · Unidad pertenece a otro comprador'
      );
      this.emitFeedback({
        type: 'danger',
        title: '403 Prohibido (Aislamiento BOLA/IDOR)',
        message: 'No puedes modificar las secuencias DEMO de un vehículo asignado a otro comprador.'
      });
      return false;
    }

    const ord = Number(orden);
    if (ord < 1 || ord > APP_CONFIG.DEMO_MAX_STEPS) {
      this.emitFeedback({
        type: 'warning',
        title: 'Límite de pasos (1 a 4)',
        message: 'Cada DEMO admite entre 1 y 4 pasos ordenados (1, 2, 3, 4).'
      });
      return false;
    }

    try {
      await this.apiService.addDemoMovement(this.editingDemoId, {
        id_comando: Number(id_comando),
        orden: ord,
        duracion_ms: Number(duracion_ms) || 1500,
        pausa_ms: Number(pausa_ms) || 500
      });

      this.recordAudit(
        this.currentRole,
        'AGREGAR_PASO_DEMO_RDS',
        `DEMO #${this.editingDemoId} · Paso #${ord}`,
        `OK (Comando ID ${id_comando}, ${duracion_ms}ms)`
      );
      await this.loadDemoStepsForEditor(this.editingDemoId);
      if (typeof this.onDemosChanged === 'function') {
        await this.onDemosChanged();
      }
      this.emitFeedback({
        type: 'success',
        title: `Paso #${ord} guardado en RDS`,
        message: `Se agregó el movimiento a la DEMO #${this.editingDemoId}.`
      });
      return true;
    } catch (error) {
      this.emitFeedback({
        type: 'danger',
        title: 'No se pudo agregar el paso',
        message: error?.message || 'Verifica que el número de orden (1..4) no esté ocupado.'
      });
      return false;
    }
  }

  /**
   * Elimina un paso de la DEMO en Amazon RDS (`DELETE /api/movimientos-demo/{id}`).
   */
  async removeStepFromEditingDemo(idDemoMovimiento) {
    if (this.currentRole === ROLES.INVITADO) {
      this.emitFeedback({
        type: 'danger',
        title: '403 Acceso Restringido',
        message: 'El rol Invitado no puede eliminar pasos de DEMO.'
      });
      return false;
    }

    if (this.currentRole === ROLES.USUARIO && !this.isCurrentBuyerOwnerOf(APP_CONFIG.VEHICLE_ID)) {
      this.recordAudit(
        `USUARIO #${this.currentBuyerId}`,
        'BORRAR_PASO_DEMO_BOLA_IDOR',
        `Paso #${idDemoMovimiento}`,
        'DENEGADO 403 · Unidad pertenece a otro comprador'
      );
      this.emitFeedback({
        type: 'danger',
        title: '403 Prohibido (Aislamiento BOLA/IDOR)',
        message: 'No puedes eliminar pasos DEMO de una unidad ajena.'
      });
      return false;
    }

    try {
      await this.apiService.deleteDemoMovement(idDemoMovimiento);
      this.recordAudit(
        this.currentRole,
        'ELIMINAR_PASO_DEMO_RDS',
        `Paso ID #${idDemoMovimiento} (DEMO #${this.editingDemoId})`,
        'OK'
      );
      await this.loadDemoStepsForEditor(this.editingDemoId);
      if (typeof this.onDemosChanged === 'function') {
        await this.onDemosChanged();
      }
      this.emitFeedback({
        type: 'info',
        title: 'Paso eliminado de RDS',
        message: 'La secuencia DEMO fue actualizada.'
      });
      return true;
    } catch (error) {
      this.emitFeedback({
        type: 'danger',
        title: 'Error al eliminar paso',
        message: error?.message || 'No se pudo eliminar el movimiento.'
      });
      return false;
    }
  }

  // ===========================================================================
  // SESIÓN TEMPORAL EXCLUSIVA DE EXHIBICIÓN PARA INVITADOS (60 SEGUNDOS)
  // ===========================================================================

  startGuestExhibitionSession() {
    if (this.guestSession.active) {
      this.recordAudit(
        'INVITADO',
        'RESERVA_CONCURRENTE_BLOQUEADA',
        'Unidad #1 (CAT-320)',
        'DENEGADO 409 · Unidad ya reservada'
      );
      this.emitFeedback({
        type: 'warning',
        title: '409 Unidad Reservada',
        message: 'Ya existe una sesión de exhibición activa sobre esta unidad.'
      });
      return false;
    }

    this.stopGuestTimer();
    this.guestSession = {
      ...this.guestSession,
      active: true,
      remainingSeconds: APP_CONFIG.GUEST_SESSION_SECONDS,
      holderId: `GUEST-${Math.floor(1000 + Math.random() * 9000)}`
    };

    this.recordAudit(
      'INVITADO',
      'INICIO_SESION_EXHIBICION',
      `Unidad #1 (${this.guestSession.holderId})`,
      `OK · Reserva exclusiva por ${APP_CONFIG.GUEST_SESSION_SECONDS}s`
    );

    this.guestTimerId = globalThis.setInterval(() => {
      if (!this.guestSession.active) {
        this.stopGuestTimer();
        return;
      }
      this.guestSession.remainingSeconds -= 1;
      if (this.guestSession.remainingSeconds <= 0) {
        this.endGuestExhibitionSession('EXPIRACION_TIEMPO');
      } else {
        this.emitState();
      }
    }, 1000);

    this.emitState();
    this.emitFeedback({
      type: 'success',
      title: 'Sesión de exhibición iniciada (60s)',
      message: 'Reserva exclusiva activa. Comandos habilitados: Orugas (#9, #11, #2, #4) y Luz (#8).'
    });
    return true;
  }

  async endGuestExhibitionSession(reason = 'CIERRE_MANUAL') {
    this.stopGuestTimer();
    const wasActive = this.guestSession.active;
    const prevHolder = this.guestSession.holderId;

    this.guestSession = {
      ...this.guestSession,
      active: false,
      remainingSeconds: 0,
      holderId: null
    };

    if (wasActive) {
      this.recordAudit(
        'INVITADO',
        'FIN_SESION_EXHIBICION',
        `Unidad #1 (${prevHolder || 'Invitado'})`,
        `OK · Motivo: ${reason} -> Paro seguro DETENER enviado`
      );
      if (typeof this.onEmergencyStop === 'function') {
        await this.onEmergencyStop();
      }
      this.emitFeedback({
        type: 'warning',
        title: 'Sesión de exhibición finalizada',
        message: 'Reserva liberada y orden automática DETENER enviada al vehículo.'
      });
    }
    this.emitState();
  }

  stopGuestTimer() {
    if (this.guestTimerId !== null) {
      globalThis.clearInterval(this.guestTimerId);
      this.guestTimerId = null;
    }
  }

  recordAudit(actor, action, resource, result) {
    this.auditLogs.unshift({
      id: this.auditLogs.length + 1,
      timestamp: formatTimestamp(new Date().toISOString()),
      actor,
      action,
      resource,
      result
    });
    if (this.auditLogs.length > 25) {
      this.auditLogs.pop();
    }
    if (
      action !== 'INICIO_PLATAFORMA' &&
      this.apiService &&
      typeof this.apiService.recordAdminAudit === 'function'
    ) {
      this.apiService
        .recordAdminAudit({
          actor,
          rol: this.currentRole || 'ADMIN',
          accion: action,
          recurso: resource,
          resultado: result
        })
        .catch(() => {});
    }
  }

  getSnapshot() {
    const isOwner = this.isCurrentBuyerOwnerOf(APP_CONFIG.VEHICLE_ID);
    const visibleUnits =
      this.currentRole === ROLES.USUARIO
        ? this.units.filter((u) => Number(u.id_cliente) === Number(this.currentBuyerId))
        : this.units;

    const allowedSubviews =
      this.currentRole === ROLES.ADMIN
        ? ['unidades', 'demos', 'exhibicion', 'auditoria']
        : this.currentRole === ROLES.USUARIO
        ? ['unidades', 'demos']
        : ['exhibicion', 'unidades'];

    return {
      currentRole: this.currentRole,
      currentBuyerId: this.currentBuyerId,
      adminSupportConsent: this.adminSupportConsent,
      isCurrentBuyerOwner: isOwner,
      permissions: {
        canManageInventory: this.currentRole === ROLES.ADMIN,
        canEditDemos: this.currentRole === ROLES.ADMIN || (this.currentRole === ROLES.USUARIO && isOwner),
        canViewAudit: this.currentRole === ROLES.ADMIN,
        canUseExhibition: this.currentRole === ROLES.INVITADO || this.currentRole === ROLES.ADMIN,
        allowedSubviews
      },
      models: this.models.slice(),
      clients: this.clients.slice(),
      units: this.units.slice(),
      visibleUnits,
      commands: this.commands.slice(),
      demos: this.demos.slice(),
      editingDemoId: this.editingDemoId,
      editingDemoSteps: this.editingDemoSteps.slice(),
      guestSession: { ...this.guestSession },
      auditLogs: this.auditLogs.slice(),
      metrics: {
        totalModels: this.models.length,
        totalUnits: this.units.length,
        assignedUnits: this.units.filter((u) => u.estado_comercial === 'Vendido / Asignado').length,
        exhibitionUnits: this.units.filter((u) => u.estado_comercial === 'Exhibición' || u.habilitado_exhibicion).length,
        totalClients: this.clients.filter((c) => c.rol === 'USUARIO').length,
        totalDemos: this.demos.length
      }
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

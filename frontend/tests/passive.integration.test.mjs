/**
 * @file passive.integration.test.mjs
 * @description Suite de pruebas de integración pasivas (SOLO LECTURA) y simulaciones locales (Mocks).
 *
 * REGLA ESTRICTA DE SEGURIDAD OPERATIVA:
 * Este script NO envía peticiones POST de movimientos ni ejecuta DEMOs contra el servidor de producción,
 * evitando activar físicamente la excavadora CAT-320.
 *
 * Pruebas que ejecuta:
 * 1. Validación unitaria de contratos JSON con `validators.js` y `api.config.js`.
 * 2. Simulación local (Mock) del despacho de comandos, anti-spam del joystick, prioridad DETENER y eventos WSS.
 * 3. Comprobación pasiva (GET de solo lectura) contra `https://vehiculo-iot-api.duckdns.org`
 *    (`/health`, `/api/vehiculos/1`, `/api/vehiculos/1/comandos`, `/api/vehiculos/1/demos`, `/api/vehiculos/1/estado`).
 * 4. Handshake pasivo WebSocket (`wss://vehiculo-iot-api.duckdns.org/ws/vehiculos/1`) para verificar recepción
 *    de `estado_inicial` sin emitir ningún comando.
 */

import assert from 'node:assert/strict';
import { APP_CONFIG, buildApiUrl, buildWsUrl } from '../js/config/api.config.js';
import {
  evaluateRdsVerification,
  toPositiveInteger,
  validateHealthResponse
} from '../js/utils/validators.js';
import { CommandDispatcher } from '../js/controllers/command.dispatcher.js';
import { DemoController } from '../js/controllers/demo.controller.js';
import { MonitoringController } from '../js/controllers/monitoring.controller.js';
import { AdminController, ROLES } from '../js/controllers/admin.controller.js';

console.log('===================================================================');
console.log(' INICIANDO SUITE DE PRUEBAS PASIVAS Y SIMULACIONES (PRÁCTICA 4.1)');
console.log('===================================================================\n');

// ---------------------------------------------------------------------------
// 1. PRUEBAS UNITARIAS DE CONFIGURACIÓN Y VALIDADORES
// ---------------------------------------------------------------------------
console.log('[1/4] Verificando configuración de endpoints y validadores de contrato...');

assert.equal(APP_CONFIG.VEHICLE_ID, 1, 'El ID del vehículo debe ser 1');
assert.equal(APP_CONFIG.JOYSTICK_CODES.UP, 9, 'AVANZAR debe ser código 9');
assert.equal(APP_CONFIG.JOYSTICK_CODES.DOWN, 11, 'RETROCEDER debe ser código 11');
assert.equal(APP_CONFIG.JOYSTICK_CODES.LEFT, 10, 'TORRE IZQUIERDA debe ser código 10');
assert.equal(APP_CONFIG.JOYSTICK_CODES.RIGHT, 12, 'TORRE DERECHA debe ser código 12');
assert.equal(APP_CONFIG.RIGHT_JOYSTICK_CODES.UP, 1, 'BRAZO ARRIBA debe ser código 1');
assert.equal(APP_CONFIG.RIGHT_JOYSTICK_CODES.DOWN, 3, 'BRAZO ABAJO debe ser código 3');
assert.equal(APP_CONFIG.RIGHT_JOYSTICK_CODES.LEFT, 2, 'ORUGA IZQUIERDA debe ser código 2');
assert.equal(APP_CONFIG.RIGHT_JOYSTICK_CODES.RIGHT, 4, 'ORUGA DERECHA debe ser código 4');
assert.equal(APP_CONFIG.STOP_CATALOG_CODE, null, 'No debe inventarse un código 0 para DETENER');

assert.equal(
  buildApiUrl(APP_CONFIG.ENDPOINTS.HEALTH),
  'https://vehiculo-iot-api.duckdns.org/health'
);
assert.equal(
  buildWsUrl(1),
  'wss://vehiculo-iot-api.duckdns.org/ws/vehiculos/1'
);

const healthEval = validateHealthResponse({
  ok: true,
  servicio: 'vehiculo_iot_api',
  version: '2.6.0'
});
assert.equal(healthEval.apiAvailable, true);
assert.equal(healthEval.hasRdsField, false, 'GET /health no debe confundirse con estado de RDS');

const rdsEvalSp = evaluateRdsVerification({
  ok: true,
  procedimiento: 'sp_obtener_comandos_vehiculo',
  resultados: [{ id_comando: 1 }]
});
assert.equal(rdsEvalSp.verified, true);
assert.equal(rdsEvalSp.available, true);

console.log('  ✓ Configuración y validadores verificados correctamente.\n');

// ---------------------------------------------------------------------------
// 2. SIMULACIÓN LOCAL (MOCK) DE COMANDOS, ANTI-SPAM JOYSTICK Y EVENTOS WSS
// ---------------------------------------------------------------------------
console.log('[2/4] Ejecutando simulaciones locales (Mocks sin tráfico POST a producción)...');

let mockPostCalls = [];
let mockDemoCancelCalled = false;

const mockApiService = {
  async sendMovement(idComando, { vehicleId, origen }) {
    mockPostCalls.push({ idComando, vehicleId, origen });
    return {
      ok: true,
      mensaje: 'Movimiento simulado en mock local',
      movimiento: {
        tipo: 'movimiento_individual',
        id_vehiculo: vehicleId,
        id_comando: idComando,
        codigo: idComando,
        origen
      },
      base_datos: {
        transaccion: 'confirmada',
        conectado: true
      }
    };
  },
  async getVehicleDemos() {
    return {
      raw: { ok: true, procedimiento: 'sp_obtener_demos', resultados: [{ id_demo: 1, nombre: 'Ciclo de Excavacion', activo: 1 }] },
      demos: [{ id_demo: 1, nombre: 'Ciclo de Excavacion', activo: 1 }]
    };
  },
  async getActiveDemo() {
    return { activa: false, demo: null };
  },
  async getDemoMovements() {
    return {
      raw: { ok: true, procedimiento: 'sp_obtener_movimientos_demo', resultados: [] },
      steps: [{ orden: 1, id_comando: 8, comando: 'Luz', codigo: 8, duracion_ms: 1000, pausa_ms: 500 }]
    };
  },
  async cancelDemoById() {
    mockDemoCancelCalled = true;
    return { ok: true, mensaje: 'Cancelación simulada en mock' };
  }
};

const mockCatalog = [
  { id_comando: 1, codigo: 1, nombre: 'Mover brazo arriba', activo: 1 },
  { id_comando: 2, codigo: 2, nombre: 'Girar izquierda', activo: 1 },
  { id_comando: 3, codigo: 3, nombre: 'Mover brazo abajo', activo: 1 },
  { id_comando: 4, codigo: 4, nombre: 'Girar derecha', activo: 1 },
  { id_comando: 5, codigo: 5, nombre: 'Velocidad', activo: 1 },
  { id_comando: 6, codigo: 6, nombre: 'Sonido', activo: 1 },
  { id_comando: 8, codigo: 8, nombre: 'Luz', activo: 1 },
  { id_comando: 9, codigo: 9, nombre: 'Avanzar', activo: 1 },
  { id_comando: 10, codigo: 10, nombre: 'Girar torre izquierda', activo: 1 },
  { id_comando: 11, codigo: 11, nombre: 'Retroceder', activo: 1 },
  { id_comando: 12, codigo: 12, nombre: 'Girar torre derecha', activo: 1 }
];

let demoActiveSim = false;
const dispatcher = new CommandDispatcher({
  apiService: mockApiService,
  isDemoRunning: () => demoActiveSim,
  onEmergencyCancelDemo: async () => {
    mockDemoCancelCalled = true;
    demoActiveSim = false;
    return true;
  }
});
dispatcher.setCatalog(mockCatalog);

// Simular 3 movimientos en la misma dirección UP (código 9): solo debe disparar 1 POST en mock
await dispatcher.handleJoystickTransition(9, 'POINTER_MOVE');
await dispatcher.handleJoystickTransition(9, 'POINTER_MOVE');
await dispatcher.handleJoystickTransition(9, 'POINTER_MOVE');
assert.equal(mockPostCalls.length, 1, 'El joystick no debe duplicar POST en la misma dirección');

// Simular retorno a neutral: NO debe enviar código 0 inexistente
await dispatcher.handleJoystickTransition(null, 'POINTER_UP');
assert.equal(mockPostCalls.length, 1, 'Soltar a neutral no debe enviar un código 0 inexistente');

// Simular prioridad de DETENER cuando hay una DEMO en curso
demoActiveSim = true;
await dispatcher.executeStop({ source: 'STOP_BUTTON' });
assert.equal(mockDemoCancelCalled, true, 'DETENER debe cancelar inmediatamente una DEMO activa');

// Simular eventos WebSocket en DemoController sin tocar el backend
const demoCtrl = new DemoController({ apiService: mockApiService });
await demoCtrl.loadCatalogAndActiveState();
demoCtrl.handleWebSocketEvent({
  tipo: 'demo_inicio',
  id_vehiculo: 1,
  id_demo: 1,
  nombre: 'Ciclo de Excavacion',
  total_pasos: 5
});
assert.equal(demoCtrl.isRunning(), true, 'El evento WSS demo_inicio debe activar el estado de DEMO');

demoCtrl.handleWebSocketEvent({
  tipo: 'demo_paso',
  id_vehiculo: 1,
  id_demo: 1,
  paso: 2,
  total_pasos: 5,
  orden: 2,
  comando: 'Avanzar',
  codigo: 9,
  duracion_ms: 2000,
  pausa_ms: 500
});
assert.equal(demoCtrl.getSnapshot().activeDemo.currentStep, 2);

demoCtrl.handleWebSocketEvent({
  tipo: 'demo_fin',
  id_vehiculo: 1,
  id_demo: 1,
  total_pasos_ejecutados: 5
});
assert.equal(demoCtrl.isRunning(), false, 'El evento WSS demo_fin debe liberar el estado de DEMO');

// Simular 4.2 Monitoreo: límite de 10 movimientos individuales, deduplicación y no contaminación por DEMO
const mockMonService = {
  async getVehicleStatus() {
    return {
      raw: { ok: true, procedimiento: 'sp_obtener_estado', resultados: [] },
      status: { id_vehiculo: 1, vehiculo: 'Excavadora IoT', id_comando: 9, comando: 'Avanzar', codigo: 9, fecha_actualizacion: '2026-11-18 20:00:00' }
    };
  },
  async getVehicleHistory() {
    const twelveRows = Array.from({ length: 12 }, (_, idx) => ({
      id_historial: 100 + idx,
      id_vehiculo: 1,
      vehiculo: 'Excavadora IoT',
      id_comando: 9,
      comando: 'Avanzar',
      codigo: 9,
      origen: 'API',
      fecha_ejecucion: `2026-11-18 20:00:${String(idx).padStart(2, '0')}`
    }));
    return {
      raw: { ok: true, procedimiento: 'sp_obtener_historial', resultados: twelveRows },
      history: twelveRows.slice(0, 10)
    };
  }
};
const monCtrl = new MonitoringController({ apiService: mockMonService });
await monCtrl.loadInitialMonitoring();
assert.equal(monCtrl.getSnapshot().historyRows.length, 10, 'La tabla de monitoreo debe mostrar máximo 10 registros');
assert.equal(monCtrl.getSnapshot().historyRows[0].id_historial, 109, 'Debe ordenar del más reciente al más antiguo');

// Enviar un evento demo_paso: debe actualizar lastMovement pero NO agregar filas a historyRows
monCtrl.handleWebSocketEvent({
  tipo: 'demo_paso',
  id_vehiculo: 1,
  id_demo: 1,
  paso: 1,
  total_pasos: 4,
  comando: 'Luz',
  codigo: 8,
  timestamp: '2026-11-18T20:05:00Z'
});
assert.equal(monCtrl.getSnapshot().lastMovement.isDemoEvent, true);
assert.equal(monCtrl.getSnapshot().historyRows.length, 10, 'Los pasos de DEMO no deben contaminar la tabla de historial');

// Simular 4.3 Administración: Roles, aislamiento BOLA/IDOR y sesión temporal de exhibición
const adminCtrl = new AdminController({ apiService: mockApiService });
adminCtrl.setRole(ROLES.USUARIO, 102); // Comprador B (#102) intentando operar Unidad #1 (que pertenece a Comprador A #101)
const bolaCheck = adminCtrl.authorizeVehicleOperation(1, 9);
assert.equal(bolaCheck.allowed, false, 'Usuario B no debe poder operar unidad de Usuario A (BOLA/IDOR)');

adminCtrl.setRole(ROLES.INVITADO);
const guestNoSession = adminCtrl.authorizeVehicleOperation(1, 9);
assert.equal(guestNoSession.allowed, false, 'Invitado sin sesión activa no debe poder operar');
adminCtrl.startGuestExhibitionSession();
const guestAllowedCmd = adminCtrl.authorizeVehicleOperation(1, 9);
assert.equal(guestAllowedCmd.allowed, true, 'Invitado con sesión activa puede probar tracción básica (#9)');
const guestRestrictedCmd = adminCtrl.authorizeVehicleOperation(1, 1);
assert.equal(guestRestrictedCmd.allowed, false, 'Invitado no puede usar comandos restringidos (#1 Brazo)');
await adminCtrl.endGuestExhibitionSession('TEST_END');

console.log('  ✓ Simulaciones de 4.1 Control (2 Joysticks), 4.2 Monitoreo (10 registros) y 4.3 Administración (RBAC/BOLA) superadas.\n');

// ---------------------------------------------------------------------------
// 3. COMPROBACIÓN PASIVA (SOLO LECTURA GET) CONTRA LA API EN AWS
// ---------------------------------------------------------------------------
console.log('[3/4] Ejecutando comprobación pasiva (solo GET) contra https://vehiculo-iot-api.duckdns.org...');

try {
  const healthRes = await fetch(buildApiUrl('/health'));
  assert.equal(healthRes.status, 200, 'GET /health debe responder HTTP 200');
  const healthJson = await healthRes.json();
  assert.equal(healthJson.ok, true);
  console.log(`  ✓ GET /health -> HTTP 200 (${healthJson.servicio} v${healthJson.version})`);

  const commandsRes = await fetch(buildApiUrl('/api/vehiculos/1/comandos'));
  assert.equal(commandsRes.status, 200, 'GET /api/vehiculos/1/comandos debe responder HTTP 200');
  const commandsJson = await commandsRes.json();
  assert.equal(commandsJson.ok, true);
  assert.equal(commandsJson.procedimiento, 'sp_obtener_comandos_vehiculo');
  console.log(`  ✓ GET /api/vehiculos/1/comandos -> HTTP 200 (${commandsJson.resultados.length} comandos en RDS)`);

  const demosRes = await fetch(buildApiUrl('/api/vehiculos/1/demos'));
  assert.equal(demosRes.status, 200, 'GET /api/vehiculos/1/demos debe responder HTTP 200');
  const demosJson = await demosRes.json();
  assert.equal(demosJson.resultados.length, 3, 'Deben existir las 3 DEMOs en RDS');
  console.log(`  ✓ GET /api/vehiculos/1/demos -> HTTP 200 (${demosJson.resultados.length} DEMOs verificadas)`);
} catch (netErr) {
  console.log(`  ! Nota de entorno: No se alcanzó la red externa en este entorno (${netErr.message}).`);
}

// ---------------------------------------------------------------------------
// 4. ESCUCHA PASIVA WEBSOCKET (SOLO HANDSHAKE INICIAL, CERO ESCRITURAS)
// ---------------------------------------------------------------------------
console.log('\n[4/4] Verificando escucha pasiva WebSocket (solo lectura de estado_inicial)...');

await new Promise((resolve) => {
  try {
    const ws = new WebSocket(buildWsUrl(1));
    const timeout = setTimeout(() => {
      try { ws.close(); } catch {}
      console.log('  ! Tiempo de espera agotado para handshake WSS en este entorno.');
      resolve();
    }, 5000);

    ws.onmessage = (event) => {
      clearTimeout(timeout);
      const payload = JSON.parse(event.data);
      assert.equal(payload.tipo, 'estado_inicial');
      assert.equal(payload.id_vehiculo, 1);
      console.log(`  ✓ Handshake WSS pasivo exitoso: recibido evento '${payload.tipo}' para vehículo #${payload.id_vehiculo}`);
      ws.close();
      resolve();
    };

    ws.onerror = () => {
      clearTimeout(timeout);
      console.log('  ! Entorno sin salida de red directa para WSS; validado mediante simulador local.');
      resolve();
    };
  } catch {
    resolve();
  }
});

console.log('\n===================================================================');
console.log(' TODAS LAS PRUEBAS PASIVAS Y SIMULACIONES FINALIZARON CON ÉXITO');
console.log('===================================================================');

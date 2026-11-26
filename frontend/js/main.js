/**
 * @file main.js
 * @description Orquestador principal de la Plataforma Integral IoT (Excavadora CAT-320):
 *   - Ventana 1 (4.1 CONTROL): Doble Joystick RC, botones táctiles, DETENER y ejecución DEMO.
 *   - Ventana 2 (4.2 MONITOREO): Tarjeta destacada del último movimiento en vivo y tabla de los 10
 *     movimientos individuales persistidos en Amazon RDS, sincronizados por WSS + REST sin recargar.
 *   - Ventana 3 (4.3 ADMINISTRACIÓN): Plataforma comercial multiusuario (Roles Administrador,
 *     Usuario Comprador e Invitado), Inventario de Modelos y Unidades, Clientes, Editor Visual
 *     de DEMOs en RDS, Sesión Temporal de Exhibición (60s) y Bitácora de Auditoría.
 */

import { APP_CONFIG } from './config/api.config.js';
import { iotApiService } from './services/iot.api.service.js';
import { VehicleWsClient, WS_CONNECTION_STATES } from './services/ws.client.js';
import { ConnectivityMonitor } from './controllers/connectivity.monitor.js';
import { CommandDispatcher } from './controllers/command.dispatcher.js';
import { JoystickController, RIGHT_ARM_DIRECTION_MAP } from './controllers/joystick.controller.js';
import { DemoController } from './controllers/demo.controller.js';
import { MonitoringController } from './controllers/monitoring.controller.js';
import { AdminController } from './controllers/admin.controller.js';
import { UiRenderer } from './ui/ui.renderer.js';

class ExcavatorControlApp {
  constructor() {
    this.ui = new UiRenderer();
    this.activeView = 'control';
    this.wasWsDisconnected = false;

    // 1. Monitor de Conectividad de 3 Indicadores (WSS, AWS API, Amazon RDS)
    this.connectivityMonitor = new ConnectivityMonitor({
      apiService: iotApiService,
      onUpdate: (snapshot) => this.ui.renderConnectivity(snapshot)
    });

    // 2. Controlador de Monitoreo en Tiempo Real (Práctica 4.2)
    this.monitoringController = new MonitoringController({
      apiService: iotApiService,
      onStateChange: (monSnapshot) => this.ui.renderMonitoringView(monSnapshot),
      onRdsEvidence: (rawPayload) => this.connectivityMonitor.recordRdsEvidenceFromPayload(rawPayload)
    });

    // 3. Controlador de secuencias DEMO
    this.demoController = new DemoController({
      apiService: iotApiService,
      onStateChange: (demoState) => {
        this.ui.renderDemoPanel(demoState);
        const locked = Boolean(demoState.activeDemo);
        if (this.joystickController) {
          this.joystickController.setDisabled(locked);
        }
        if (this.rightJoystickController) {
          this.rightJoystickController.setDisabled(locked);
        }
      },
      onFeedback: (feedback) => this.ui.renderOperationFeedback(feedback),
      onRdsEvidence: (rawPayload) => this.connectivityMonitor.recordRdsEvidenceFromPayload(rawPayload)
    });

    // 4. Controlador de Administración Comercial Multiusuario (Práctica 4.3)
    this.adminController = new AdminController({
      apiService: iotApiService,
      onStateChange: (adminSnapshot) => {
        this.ui.renderAdminView(adminSnapshot);
        if (adminSnapshot.currentRole === 'INVITADO') {
          if (this.activeView === 'control' || this.activeView === 'administracion') {
            this.activeView = 'monitoreo';
            this.ui.switchActiveView('monitoreo');
          }
        }
      },
      onFeedback: (feedback) => this.ui.renderOperationFeedback(feedback),
      onRdsEvidence: (rawPayload) => this.connectivityMonitor.recordRdsEvidenceFromPayload(rawPayload),
      onDemosChanged: async () => {
        await this.demoController.loadCatalogAndActiveState();
      },
      onEmergencyStop: async () => {
        await this.commandDispatcher.executeStop({ source: 'STOP_BUTTON' });
      }
    });

    // 5. Despachador único de comandos (2 Joysticks + Botones + Prioridad DETENER + RBAC 4.3)
    this.commandDispatcher = new CommandDispatcher({
      apiService: iotApiService,
      onControlStateChange: (stateEvt) => this.ui.updateControlVisualState(stateEvt),
      onOperationFeedback: (feedback) => {
        this.ui.renderOperationFeedback(feedback);
        if (feedback.command) {
          this.ui.renderLastKnownStatus({
            comando: feedback.command.nombre,
            codigo: feedback.command.codigo,
            timestamp: feedback.timestamp
          });
        }
      },
      onRdsEvidence: (rawPayload) => this.connectivityMonitor.recordRdsEvidenceFromPayload(rawPayload),
      isDemoRunning: () => this.demoController.isRunning(),
      onEmergencyCancelDemo: () => this.demoController.cancelActiveDemo(),
      authorizeCommand: (vehicleId, code) => this.adminController.authorizeVehicleOperation(vehicleId, code)
    });

    // 6A. Joystick Izquierdo: Tracción / Orugas (Códigos 9, 11, 2, 4)
    this.joystickController = new JoystickController({
      baseElement: document.getElementById('joystick-base'),
      stickElement: document.getElementById('joystick-stick'),
      directionLabelElement: document.getElementById('joystick-direction-label'),
      dispatcher: this.commandDispatcher,
      joystickId: 'LEFT'
    });

    // 6B. Joystick Derecho: Brazo y Torre (Códigos 1, 3, 10, 12)
    this.rightJoystickController = new JoystickController({
      baseElement: document.getElementById('joystick-right-base'),
      stickElement: document.getElementById('joystick-right-stick'),
      directionLabelElement: document.getElementById('joystick-right-direction-label'),
      dispatcher: this.commandDispatcher,
      directionMap: RIGHT_ARM_DIRECTION_MAP,
      joystickId: 'RIGHT'
    });

    // 7. Cliente WebSocket único y compartido por CONTROL, MONITOREO y ADMINISTRACIÓN
    this.wsClient = new VehicleWsClient({
      vehicleId: APP_CONFIG.VEHICLE_ID,
      onStateChange: (wsState, meta) => {
        this.connectivityMonitor.updateWsState(wsState, meta);
        if (wsState === WS_CONNECTION_STATES.DISCONNECTED) {
          this.wasWsDisconnected = true;
        } else if (wsState === WS_CONNECTION_STATES.ONLINE && this.wasWsDisconnected) {
          this.wasWsDisconnected = false;
          // Al reconectar WSS, resincronizar estado e historial desde REST (Regla 7.4 de 4.2)
          this.monitoringController.loadInitialMonitoring({ silent: true });
        }
      },
      onEvent: (wsEvent) => this.handleIncomingWebSocketEvent(wsEvent)
    });
  }

  async init() {
    this.preventMobilePullToRefresh();
    this.bindUiEvents();
    this.joystickController.init();
    this.rightJoystickController.init();

    this.connectivityMonitor.startMonitoring();
    this.wsClient.connect();

    await this.loadInitialData();
  }

  /**
   * Impide el pull-to-refresh (recarga por arrastre hacia abajo) en teléfonos móviles
   * en TODAS las ventanas de la aplicación (Control, Monitoreo y Administración).
   */
  preventMobilePullToRefresh() {
    let touchStartY = 0;

    document.addEventListener(
      'touchstart',
      (e) => {
        if (e.touches && e.touches.length === 1) {
          touchStartY = e.touches[0].clientY;
        }
      },
      { passive: true }
    );

    document.addEventListener(
      'touchmove',
      (e) => {
        // Si está arrastrando dentro de la zona de joysticks, bloquear scroll siempre
        if (e.target.closest('.joystick-arena') || e.target.closest('.joystick-base')) {
          if (e.cancelable) e.preventDefault();
          return;
        }

        // Permitir scroll interno únicamente dentro de tablas o listas con contenido desplazable
        const scrollable = e.target.closest('.table-responsive, .demo-steps-container, .ws-events-feed, .audit-scroll-box');
        if (scrollable && scrollable.scrollHeight > scrollable.clientHeight) {
          const currentY = e.touches && e.touches[0] ? e.touches[0].clientY : touchStartY;
          const pullingDownAtTop = scrollable.scrollTop <= 0 && currentY > touchStartY;
          if (pullingDownAtTop && e.cancelable) {
            e.preventDefault();
          }
          return;
        }

        // En cualquier parte de Control, Monitoreo o Administración, si está en el tope superior y arrastra hacia abajo, bloquear pull-to-refresh
        const currentY = e.touches && e.touches[0] ? e.touches[0].clientY : touchStartY;
        const deltaY = currentY - touchStartY;
        if (window.scrollY <= 0 && deltaY > 0 && e.cancelable) {
          e.preventDefault();
        }
      },
      { passive: false }
    );
  }

  async loadInitialData() {
    try {
      const [vehicleRes, commandsRes, statusRes] = await Promise.all([
        iotApiService.getVehicleInfo(APP_CONFIG.VEHICLE_ID),
        iotApiService.getVehicleCommands(APP_CONFIG.VEHICLE_ID),
        iotApiService.getVehicleStatus(APP_CONFIG.VEHICLE_ID)
      ]);

      this.connectivityMonitor.recordRdsEvidenceFromPayload(vehicleRes.raw);
      this.connectivityMonitor.recordRdsEvidenceFromPayload(commandsRes.raw);
      this.connectivityMonitor.recordRdsEvidenceFromPayload(statusRes.raw);

      this.ui.renderVehicleMetadata(vehicleRes.vehicle);
      this.monitoringController.setVehicleContext(vehicleRes.vehicle);
      this.commandDispatcher.setCatalog(commandsRes.commands);
      this.ui.syncButtonsWithCatalog(commandsRes.commands);

      if (statusRes.status) {
        this.ui.renderLastKnownStatus(statusRes.status);
      }

      await Promise.all([
        this.demoController.loadCatalogAndActiveState(),
        this.monitoringController.loadInitialMonitoring({ silent: true }),
        this.adminController.loadAdminData()
      ]);

      if (!localStorage.getItem('iot_session_user')) {
        this.ui.openLoginModal();
      }

      this.ui.renderOperationFeedback({
        type: 'success',
        stage: 'IDLE',
        title: 'Suite CAT-320 en línea',
        message: `Control, Monitoreo (10 registros) y Administración sincronizados con AWS.`
      });
    } catch (error) {
      this.connectivityMonitor.recordRdsFailure(error);
      this.ui.renderOperationFeedback({
        type: 'danger',
        stage: 'ERROR',
        title: 'Sincronización inicial incompleta',
        message: error?.message || 'No se pudieron recuperar todos los catálogos.'
      });
    }
  }

  bindUiEvents() {
    // 1. Navegación entre las 3 ventanas: CONTROL (4.1) | MONITOREO (4.2) | ADMINISTRACIÓN (4.3)
    document.querySelectorAll('[data-nav-view]').forEach((tabBtn) => {
      tabBtn.addEventListener('click', (event) => {
        event.preventDefault();
        const targetView = tabBtn.getAttribute('data-nav-view');
        if (!targetView) return;
        this.activeView = targetView;
        this.ui.switchActiveView(targetView);

        if (targetView === 'monitoreo') {
          this.monitoringController.loadInitialMonitoring({ silent: true });
        } else if (targetView === 'administracion') {
          this.adminController.loadAdminData();
        }
      });
    });

    // 2. Botones de comando del catálogo en CONTROL
    document.querySelectorAll('.manual-cmd-btn[data-command-code]').forEach((btn) => {
      btn.addEventListener('click', (event) => {
        event.preventDefault();
        const code = Number(btn.getAttribute('data-command-code'));
        this.commandDispatcher.dispatchCommand(code, 'BUTTON');
      });
    });

    // 3. Botón prioritario DETENER
    const btnStop = document.getElementById('btn-emergency-stop');
    if (btnStop) {
      btnStop.addEventListener('click', (event) => {
        event.preventDefault();
        if (this.joystickController) {
          this.joystickController.resetToNeutral('STOP_BUTTON');
        }
        if (this.rightJoystickController) {
          this.rightJoystickController.resetToNeutral('STOP_BUTTON');
        }
        this.commandDispatcher.executeStop({ source: 'STOP_BUTTON' });
      });
    }

    // 4. Controles del módulo DEMO en CONTROL
    const demoSelect = document.getElementById('demo-select');
    if (demoSelect) {
      demoSelect.addEventListener('change', (event) => {
        const selectedId = Number(event.target.value);
        this.demoController.selectDemo(selectedId);
      });
    }

    const btnStartDemo = document.getElementById('btn-start-demo');
    if (btnStartDemo) {
      btnStartDemo.addEventListener('click', (event) => {
        event.preventDefault();
        const auth = this.adminController.authorizeVehicleOperation(APP_CONFIG.VEHICLE_ID, 99);
        if (!auth.allowed) {
          this.ui.renderOperationFeedback({
            type: 'danger',
            stage: 'ERROR',
            title: 'Ejecución DEMO restringida',
            message: auth.reason
          });
          return;
        }
        this.demoController.startSelectedDemo();
      });
    }

    const btnCancelDemo = document.getElementById('btn-cancel-demo');
    if (btnCancelDemo) {
      btnCancelDemo.addEventListener('click', (event) => {
        event.preventDefault();
        this.demoController.cancelActiveDemo();
      });
    }

    // Botón "Demo" en la fila de FUNCIONES de la cabina central (muestra/oculta pasos o inicia DEMO)
    const btnToggleInlineDemo = document.getElementById('btn-toggle-inline-demo');
    const demoStepsList = document.getElementById('demo-steps-list');
    if (btnToggleInlineDemo && demoStepsList) {
      btnToggleInlineDemo.addEventListener('click', (event) => {
        event.preventDefault();
        demoStepsList.hidden = !demoStepsList.hidden;
      });
    }

    // Botones de Ejecutar / Cancelar en la pestaña "Secuencias"
    const btnSeqStartDemo = document.getElementById('btn-seq-start-demo');
    if (btnSeqStartDemo) {
      btnSeqStartDemo.addEventListener('click', (event) => {
        event.preventDefault();
        const auth = this.adminController.authorizeVehicleOperation(APP_CONFIG.VEHICLE_ID, null);
        if (!auth.allowed) {
          this.ui.renderOperationFeedback({
            type: 'danger',
            stage: 'ERROR',
            title: 'Ejecución DEMO restringida',
            message: auth.reason
          });
          return;
        }
        this.demoController.startSelectedDemo();
      });
    }

    const btnSeqCancelDemo = document.getElementById('btn-seq-cancel-demo');
    if (btnSeqCancelDemo) {
      btnSeqCancelDemo.addEventListener('click', (event) => {
        event.preventDefault();
        this.demoController.cancelActiveDemo();
      });
    }

    // 5. Botones de conectividad y resincronización en MONITOREO
    const btnReconnectWs = document.getElementById('btn-reconnect-ws');
    if (btnReconnectWs) {
      btnReconnectWs.addEventListener('click', () => {
        this.wsClient.reconnectNow();
      });
    }

    const btnCheckHealth = document.getElementById('btn-check-health');
    if (btnCheckHealth) {
      btnCheckHealth.addEventListener('click', async () => {
        await this.connectivityMonitor.checkAwsApiHealth();
        await this.connectivityMonitor.verifyRdsReadOnly();
      });
    }

    const btnRefreshMonitoring = document.getElementById('btn-refresh-monitoring');
    if (btnRefreshMonitoring) {
      btnRefreshMonitoring.addEventListener('click', () => {
        this.monitoringController.loadInitialMonitoring({ silent: false });
      });
    }

    const btnClearFeed = document.getElementById('btn-clear-ws-feed');
    if (btnClearFeed) {
      btnClearFeed.addEventListener('click', () => {
        const feed = document.getElementById('ws-events-feed');
        if (feed) {
          feed.replaceChildren();
          const empty = document.createElement('div');
          empty.className = 'ws-feed-empty text-secondary small';
          empty.textContent = 'Bitácora limpia. Esperando eventos WSS...';
          feed.appendChild(empty);
        }
      });
    }

    // 6. Eventos de la Vista 4.3 ADMINISTRACIÓN (Sub-pestañas sin deslizar + Roles)
    document.querySelectorAll('[data-admin-subview]').forEach((subBtn) => {
      subBtn.addEventListener('click', (e) => {
        e.preventDefault();
        const targetSub = subBtn.getAttribute('data-admin-subview');
        document.querySelectorAll('[data-admin-subview]').forEach((b) => {
          b.classList.toggle('active', b.getAttribute('data-admin-subview') === targetSub);
        });
        ['unidades', 'demos', 'exhibicion', 'auditoria'].forEach((subName) => {
          const el = document.getElementById(`admin-subview-${subName}`);
          if (el) {
            const isMatch = subName === targetSub;
            if (isMatch) {
              el.removeAttribute('hidden');
              el.classList.add('active-subview');
            } else {
              el.setAttribute('hidden', '');
              el.classList.remove('active-subview');
            }
          }
        });
      });
    });

    document.querySelectorAll('[data-role-btn]').forEach((btn) => {
      btn.addEventListener('click', () => {
        const nextRole = btn.getAttribute('data-role-btn');
        this.adminController.setRole(nextRole);
      });
    });

    const buyerSelect = document.getElementById('admin-buyer-account-select');
    if (buyerSelect) {
      buyerSelect.addEventListener('change', (e) => {
        this.adminController.setBuyerAccount(Number(e.target.value));
      });
    }

    const supportSwitch = document.getElementById('admin-support-consent-switch');
    if (supportSwitch) {
      supportSwitch.addEventListener('change', (e) => {
        this.adminController.setAdminSupportConsent(e.target.checked);
      });
    }

    // 7. Eventos de Autenticación, Login y Selección de Perfiles RBAC (Práctica 4.3)
    const openLogin = () => this.ui.openLoginModal();
    const closeLogin = () => this.ui.closeLoginModal();

    document.getElementById('header-user-btn')?.addEventListener('click', openLogin);
    document.getElementById('btn-quick-login-toggle')?.addEventListener('click', openLogin);
    document.getElementById('btn-notice-switch-user')?.addEventListener('click', openLogin);
    document.getElementById('btn-close-login-modal')?.addEventListener('click', closeLogin);

    // Clic en tarjetas de selección rápida de perfil
    document.querySelectorAll('[data-login-profile]').forEach((card) => {
      card.addEventListener('click', async (e) => {
        e.preventDefault();
        document.querySelectorAll('[data-login-profile]').forEach((c) => c.classList.remove('active'));
        card.classList.add('active');

        const email = card.getAttribute('data-email');
        const role = card.getAttribute('data-role');
        const buyerId = card.getAttribute('data-buyer-id');

        const emailInput = document.getElementById('login-email-input');
        if (emailInput && email) emailInput.value = email;

        await this.adminController.loginUser({
          correo: email,
          rol: role,
          id_cliente: buyerId
        });

        if (role === 'INVITADO') {
          this.activeView = 'monitoreo';
          this.ui.switchActiveView('monitoreo');
        } else if (this.activeView === 'exhibicion') {
          this.activeView = 'control';
          this.ui.switchActiveView('control');
        }

        this.ui.closeLoginModal();
      });
    });

    // Formulario de inicio de sesión manual
    const loginForm = document.getElementById('auth-login-form');
    if (loginForm) {
      loginForm.addEventListener('submit', async (e) => {
        e.preventDefault();
        const email = document.getElementById('login-email-input')?.value?.trim();
        const password = document.getElementById('login-password-input')?.value;
        try {
          const user = await this.adminController.loginUser({ correo: email, password });
          if (user?.rol === 'INVITADO') {
            this.activeView = 'monitoreo';
            this.ui.switchActiveView('monitoreo');
          }
          this.ui.closeLoginModal();
        } catch (err) {
          this.ui.showLoginAlert(err?.message || 'Error al iniciar sesión.');
        }
      });
    }

    // Botón de acceso rápido como Invitado
    document.getElementById('btn-login-as-guest-fast')?.addEventListener('click', async () => {
      await this.adminController.loginUser({ rol: 'INVITADO' });
      this.activeView = 'monitoreo';
      this.ui.switchActiveView('monitoreo');
      this.ui.closeLoginModal();
    });

    // Formulario Alta de Unidad Física en RDS
    const formNewUnit = document.getElementById('form-admin-new-unit');
    if (formNewUnit) {
      formNewUnit.addEventListener('submit', async (e) => {
        e.preventDefault();
        const nombre = document.getElementById('new-unit-name')?.value;
        const modelo = document.getElementById('new-unit-model')?.value;
        const microcontrolador = document.getElementById('new-unit-mcu')?.value;
        const descripcion = document.getElementById('new-unit-desc')?.value;
        const id_cliente = document.getElementById('new-unit-client')?.value;
        const estado_comercial = document.getElementById('new-unit-status')?.value;

        const ok = await this.adminController.registerNewUnit({
          nombre,
          modelo,
          descripcion,
          microcontrolador,
          id_cliente,
          estado_comercial
        });
        if (ok) formNewUnit.reset();
      });
    }

    // Formulario Alta de Cliente Comprador
    const formNewClient = document.getElementById('form-admin-new-client');
    if (formNewClient) {
      formNewClient.addEventListener('submit', (e) => {
        e.preventDefault();
        const nombre = document.getElementById('new-client-name')?.value;
        const correo = document.getElementById('new-client-email')?.value;
        if (this.adminController.registerClient({ nombre, correo })) {
          formNewClient.reset();
        }
      });
    }

    // Formulario Asignación de Unidad a Comprador
    const formAssignUnit = document.getElementById('form-admin-assign-unit');
    if (formAssignUnit) {
      formAssignUnit.addEventListener('submit', (e) => {
        e.preventDefault();
        const id_vehiculo = document.getElementById('assign-unit-select')?.value;
        const id_cliente = document.getElementById('assign-client-select')?.value;
        const estado_comercial = document.getElementById('assign-status-select')?.value;
        this.adminController.assignUnitToClient({ id_vehiculo, id_cliente, estado_comercial });
      });
    }

    // Editor Visual de DEMOs en RDS
    const adminDemoSelect = document.getElementById('admin-demo-select');
    if (adminDemoSelect) {
      adminDemoSelect.addEventListener('change', (e) => {
        this.adminController.loadDemoStepsForEditor(Number(e.target.value));
      });
    }

    const formNewDemo = document.getElementById('form-admin-new-demo');
    if (formNewDemo) {
      formNewDemo.addEventListener('submit', async (e) => {
        e.preventDefault();
        const nombre = document.getElementById('new-demo-name')?.value;
        const descripcion = document.getElementById('new-demo-desc')?.value;
        if (await this.adminController.createNewDemo({ nombre, descripcion })) {
          formNewDemo.reset();
        }
      });
    }

    const formAddDemoStep = document.getElementById('form-admin-add-demo-step');
    if (formAddDemoStep) {
      formAddDemoStep.addEventListener('submit', async (e) => {
        e.preventDefault();
        const id_comando = document.getElementById('admin-step-cmd-select')?.value;
        const orden = document.getElementById('admin-step-order')?.value;
        const duracion_ms = document.getElementById('admin-step-duration')?.value;
        const pausa_ms = document.getElementById('admin-step-pause')?.value;

        await this.adminController.addStepToEditingDemo({
          id_comando,
          orden,
          duracion_ms,
          pausa_ms
        });
      });
    }

    const demoStepsTbody = document.getElementById('admin-demo-steps-tbody');
    if (demoStepsTbody) {
      demoStepsTbody.addEventListener('click', (e) => {
        const delBtn = e.target.closest('[data-delete-demo-step]');
        if (delBtn) {
          const stepId = Number(delBtn.getAttribute('data-delete-demo-step'));
          this.adminController.removeStepFromEditingDemo(stepId);
        }
      });
    }

    // Sesión temporal de exhibición para Invitado
    const btnStartGuest = document.getElementById('btn-start-guest-session');
    if (btnStartGuest) {
      btnStartGuest.addEventListener('click', () => {
        this.adminController.startGuestExhibitionSession();
      });
    }

    const btnEndGuest = document.getElementById('btn-end-guest-session');
    if (btnEndGuest) {
      btnEndGuest.addEventListener('click', () => {
        this.adminController.endGuestExhibitionSession('FINALIZADA_POR_INVITADO');
      });
    }

    // Botones de verificación rápida de seguridad (P12, P13, P14)
    document.querySelectorAll('[data-sec-test]').forEach((btn) => {
      btn.addEventListener('click', () => {
        const scenario = btn.getAttribute('data-sec-test');
        this.adminController.runSecurityVerificationScenario(scenario);
      });
    });
  }

  /**
   * Procesa los mensajes recibidos desde `wss://vehiculo-iot-api.duckdns.org/ws/vehiculos/1`
   * y los distribuye hacia CONTROL (4.1) y MONITOREO (4.2).
   * @param {object} wsEvent Evento JSON
   */
  handleIncomingWebSocketEvent(wsEvent) {
    this.ui.appendWsEventLog(wsEvent);
    this.monitoringController.handleWebSocketEvent(wsEvent);

    if (wsEvent.tipo === 'estado_inicial') {
      const firstRecord = Array.isArray(wsEvent.estado) && wsEvent.estado[0] ? wsEvent.estado[0] : null;
      if (firstRecord) {
        this.ui.renderLastKnownStatus({
          comando: firstRecord.comando,
          codigo: firstRecord.codigo,
          timestamp: firstRecord.fecha_actualizacion || wsEvent.timestamp
        });
      }
      return;
    }

    if (wsEvent.tipo === 'movimiento_individual') {
      this.ui.markWsEventStageReceived();
      this.ui.renderLastKnownStatus({
        comando: wsEvent.comando,
        codigo: wsEvent.codigo,
        timestamp: wsEvent.timestamp
      });
      return;
    }

    if (wsEvent.tipo === 'detener') {
      this.ui.markWsEventStageReceived();
      this.ui.renderLastKnownStatus({
        comando: wsEvent.comando || 'Detener',
        codigo: wsEvent.codigo ?? 0,
        timestamp: wsEvent.timestamp
      });
      return;
    }

    if (wsEvent.tipo && wsEvent.tipo.startsWith('demo_')) {
      this.ui.markWsEventStageReceived();
      if (wsEvent.tipo === 'demo_paso') {
        this.ui.renderLastKnownStatus({
          comando: `${wsEvent.comando} [DEMO]`,
          codigo: wsEvent.codigo,
          timestamp: wsEvent.timestamp
        });
      }
      this.demoController.handleWebSocketEvent(wsEvent);
    }
  }
}

document.addEventListener('DOMContentLoaded', () => {
  const app = new ExcavatorControlApp();
  app.init();
});

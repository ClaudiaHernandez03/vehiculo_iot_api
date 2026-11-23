/**
 * @file ui.renderer.js
 * @description Módulo responsable de actualizar el DOM de forma segura (sin inyección HTML / XSS)
 * para las TRES vistas integradas de la plataforma:
 *   1. CONTROL (4.1): Doble Joystick, botones táctiles, DETENER, DEMO y pipeline de 3 etapas.
 *   2. MONITOREO (4.2): Tarjeta destacada del último movimiento y tabla de los 10 movimientos individuales.
 *   3. ADMINISTRACIÓN (4.3): Dashboard comercial, Roles (Admin/Usuario/Invitado), Unidades, Clientes,
 *      Editor visual de DEMOs en RDS, Sesión temporal de exhibición (60s) y Bitácora de Auditoría.
 */

import { API_STATES, RDS_STATES } from '../controllers/connectivity.monitor.js';
import { ROLES } from '../controllers/admin.controller.js';
import { WS_CONNECTION_STATES } from '../services/ws.client.js';
import { formatTimestamp, safeString } from '../utils/validators.js';

export class UiRenderer {
  constructor() {
    // Cabecera y metadatos del vehículo
    this.elVehicleName = document.getElementById('vehicle-name');
    this.elVehicleModel = document.getElementById('vehicle-model');
    this.elVehicleMcu = document.getElementById('vehicle-mcu');
    this.elVehicleLastCmd = document.getElementById('vehicle-last-cmd');
    this.elVehicleLastTime = document.getElementById('vehicle-last-time');
    this.elHeaderRoleBadge = document.getElementById('header-active-role-badge');

    // Panel compartido de 3 indicadores de conectividad
    this.elWsBadge = document.getElementById('status-ws-badge');
    this.elWsDetail = document.getElementById('status-ws-detail');
    this.elWsCard = document.getElementById('card-status-ws');

    this.elApiBadge = document.getElementById('status-api-badge');
    this.elApiDetail = document.getElementById('status-api-detail');
    this.elApiCard = document.getElementById('card-status-api');

    this.elRdsBadge = document.getElementById('status-rds-badge');
    this.elRdsDetail = document.getElementById('status-rds-detail');
    this.elRdsCard = document.getElementById('card-status-rds');

    // Etapas de trazabilidad de la orden (1. Solicitado -> 2. Aceptado por API -> 3. Evento recibido)
    this.elStageRequested = document.getElementById('stage-requested');
    this.elStageAccepted = document.getElementById('stage-accepted');
    this.elStageWsEvent = document.getElementById('stage-ws-event');

    // Banner de retroalimentación de operación
    this.elFeedbackBanner = document.getElementById('operation-feedback-banner');
    this.elFeedbackTitle = document.getElementById('operation-feedback-title');
    this.elFeedbackMessage = document.getElementById('operation-feedback-message');
    this.elFeedbackTime = document.getElementById('operation-feedback-time');

    // Módulo DEMO en vista CONTROL
    this.elDemoSelect = document.getElementById('demo-select');
    this.elDemoDescription = document.getElementById('demo-description');
    this.elDemoStepsList = document.getElementById('demo-steps-list');
    this.elDemoActiveBanner = document.getElementById('demo-active-banner');
    this.elDemoActiveTitle = document.getElementById('demo-active-title');
    this.elDemoActiveStep = document.getElementById('demo-active-step');
    this.elDemoProgressBar = document.getElementById('demo-progress-bar');
    this.elBtnStartDemo = document.getElementById('btn-start-demo');
    this.elBtnCancelDemo = document.getElementById('btn-cancel-demo');

    // Bitácora de eventos WSS en tiempo real
    this.elWsEventsFeed = document.getElementById('ws-events-feed');

    // Elementos de la Vista 4.2 MONITOREO
    this.elMonLastCmdName = document.getElementById('mon-last-cmd-name');
    this.elMonLastCmdCode = document.getElementById('mon-last-cmd-code');
    this.elMonLastVehicle = document.getElementById('mon-last-vehicle');
    this.elMonLastDate = document.getElementById('mon-last-date');
    this.elMonLastTime = document.getElementById('mon-last-time');
    this.elMonLastOrigin = document.getElementById('mon-last-origin');
    this.elMonLastStageBadge = document.getElementById('mon-last-stage-badge');
    this.elMonSyncTime = document.getElementById('mon-sync-time');
    this.elMonHistoryCount = document.getElementById('mon-history-count');
    this.elMonHistoryTbody = document.getElementById('mon-history-tbody');

    // Elementos de la Vista 4.3 ADMINISTRACIÓN
    this.elMetricModels = document.getElementById('metric-models');
    this.elMetricUnits = document.getElementById('metric-units');
    this.elMetricAssigned = document.getElementById('metric-assigned');
    this.elMetricExhibition = document.getElementById('metric-exhibition');
    this.elMetricClients = document.getElementById('metric-clients');
    this.elMetricDemos = document.getElementById('metric-demos');

    this.elAdminRoleBanner = document.getElementById('admin-role-context-text');
    this.elAdminUnitsTitle = document.getElementById('admin-units-section-title');
    this.elAdminUnitsTbody = document.getElementById('admin-units-tbody');
    this.elAdminModelsTbody = document.getElementById('admin-models-tbody');
    this.elAdminClientsTbody = document.getElementById('admin-clients-tbody');
    this.elAssignUnitSelect = document.getElementById('assign-unit-select');
    this.elAssignClientSelect = document.getElementById('assign-client-select');
    this.elNewUnitClientSelect = document.getElementById('new-unit-client');

    this.elAdminDemoSelect = document.getElementById('admin-demo-select');
    this.elAdminStepCmdSelect = document.getElementById('admin-step-cmd-select');
    this.elAdminDemoStepsTbody = document.getElementById('admin-demo-steps-tbody');

    this.elGuestSessionBadge = document.getElementById('guest-session-badge');
    this.elGuestTimerDisplay = document.getElementById('guest-timer-display');
    this.elBtnStartGuest = document.getElementById('btn-start-guest-session');
    this.elBtnEndGuest = document.getElementById('btn-end-guest-session');
    this.elAdminAuditTbody = document.getElementById('admin-audit-tbody');
  }

  /**
   * Cambia la pestaña/ventana activa (`control`, `monitoreo`, `administracion`) sin recargar la página.
   * @param {'control'|'monitoreo'|'administracion'} viewName
   */
  switchActiveView(viewName) {
    const views = ['control', 'monitoreo', 'administracion'];
    for (const v of views) {
      const panel = document.getElementById(`view-${v}`);
      const tabBtn = document.querySelector(`[data-nav-view="${v}"]`);
      const isTarget = v === viewName;

      if (panel) {
        if (isTarget) {
          panel.removeAttribute('hidden');
          panel.classList.add('active-view');
        } else {
          panel.setAttribute('hidden', '');
          panel.classList.remove('active-view');
        }
      }
      if (tabBtn) {
        tabBtn.classList.toggle('active', isTarget);
        tabBtn.setAttribute('aria-selected', String(isTarget));
      }
    }
  }

  renderVehicleMetadata(vehicle) {
    if (!vehicle) return;
    if (this.elVehicleName) {
      this.elVehicleName.textContent = 'Vehículos y Equipos';
    }
    if (this.elVehicleModel) {
      const mod = safeString(vehicle.modelo, 'CAT-320');
      this.elVehicleModel.textContent = mod.includes('Excavadora') ? mod : `Excavadora ${mod}`;
    }
    if (this.elVehicleMcu) {
      this.elVehicleMcu.textContent = `${safeString(vehicle.microcontrolador, 'ESP8266')} · #${safeString(
        vehicle.id_vehiculo,
        '1'
      )}`;
    }
  }

  renderLastKnownStatus(statusInfo) {
    if (!statusInfo) return;
    const cmdName = safeString(statusInfo.comando, 'Sin comando');
    const code = statusInfo.codigo ?? statusInfo.id_comando ?? '—';
    const timeStr = formatTimestamp(statusInfo.timestamp || statusInfo.fecha_actualizacion);

    if (this.elVehicleLastCmd) {
      this.elVehicleLastCmd.textContent = `${cmdName} (#${code})`;
    }
    if (this.elVehicleLastTime) {
      this.elVehicleLastTime.textContent = timeStr;
    }
  }

  renderConnectivity(snapshot) {
    if (!snapshot) return;

    if (this.elWsBadge && this.elWsDetail && this.elWsCard) {
      this.elWsBadge.textContent = snapshot.ws.label;
      this.elWsDetail.textContent = snapshot.ws.detail;
      this.applyCardTheme(
        this.elWsCard,
        this.elWsBadge,
        snapshot.ws.status === WS_CONNECTION_STATES.ONLINE
          ? 'ok'
          : snapshot.ws.status === WS_CONNECTION_STATES.CONNECTING
          ? 'warn'
          : 'err'
      );
    }

    if (this.elApiBadge && this.elApiDetail && this.elApiCard) {
      this.elApiBadge.textContent = snapshot.api.label;
      this.elApiDetail.textContent = snapshot.api.detail;
      this.applyCardTheme(
        this.elApiCard,
        this.elApiBadge,
        snapshot.api.status === API_STATES.AVAILABLE
          ? 'ok'
          : snapshot.api.status === API_STATES.CHECKING
          ? 'warn'
          : 'err'
      );
    }

    if (this.elRdsBadge && this.elRdsDetail && this.elRdsCard) {
      this.elRdsBadge.textContent = snapshot.rds.label;
      this.elRdsDetail.textContent = snapshot.rds.detail;
      this.applyCardTheme(
        this.elRdsCard,
        this.elRdsBadge,
        snapshot.rds.status === RDS_STATES.AVAILABLE
          ? 'ok'
          : snapshot.rds.status === RDS_STATES.UNVERIFIED
          ? 'neutral'
          : 'err'
      );
    }
  }

  applyCardTheme(cardEl, badgeEl, level) {
    cardEl.classList.remove('conn-ok', 'conn-warn', 'conn-err', 'conn-neutral');
    badgeEl.classList.remove('badge-ok', 'badge-warn', 'badge-err', 'badge-neutral');

    cardEl.classList.add(`conn-${level}`);
    badgeEl.classList.add(`badge-${level}`);
  }

  syncButtonsWithCatalog(commands = []) {
    const activeByCode = new Map();
    if (Array.isArray(commands)) {
      for (const cmd of commands) {
        if (Number(cmd?.activo) !== 0 && Number.isInteger(Number(cmd?.codigo))) {
          activeByCode.set(Number(cmd.codigo), cmd);
        }
      }
    }

    const defaultNames = {
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

    const commandButtons = document.querySelectorAll('.manual-cmd-btn[data-command-code]');
    commandButtons.forEach((btn) => {
      const code = Number(btn.getAttribute('data-command-code'));
      const catalogItem = activeByCode.get(code);
      const titleEl = btn.querySelector('.cmd-btn-label');

      btn.disabled = false;
      btn.setAttribute('data-state', 'normal');

      if (catalogItem) {
        btn.setAttribute('title', `${catalogItem.nombre}: ${catalogItem.descripcion || ''}`);
        if (titleEl && !btn.hasAttribute('data-short-label')) {
          titleEl.textContent = catalogItem.nombre;
        }
      } else {
        const fallbackTitle = defaultNames[code] || `Comando #${code}`;
        btn.setAttribute('title', fallbackTitle);
      }
    });
  }

  updateControlVisualState({ code, visualState }) {
    if (code === 'ALL') {
      document.querySelectorAll('[data-command-code]').forEach((btn) => {
        if (!btn.disabled) {
          btn.setAttribute('data-state', 'normal');
        }
      });
      return;
    }

    const targetElements = document.querySelectorAll(`[data-command-code="${code}"]`);
    targetElements.forEach((el) => {
      el.setAttribute('data-state', visualState);
    });

    if (visualState === 'aceptado' || visualState === 'error') {
      window.setTimeout(() => {
        targetElements.forEach((el) => {
          if (el.getAttribute('data-state') === visualState && !el.disabled) {
            el.setAttribute('data-state', 'normal');
          }
        });
      }, 900);
    }
  }

  updateOrderStages(stage) {
    if (!this.elStageRequested || !this.elStageAccepted || !this.elStageWsEvent) return;

    this.elStageRequested.classList.remove('stage-active', 'stage-done', 'stage-error');
    this.elStageAccepted.classList.remove('stage-active', 'stage-done', 'stage-error');
    this.elStageWsEvent.classList.remove('stage-active', 'stage-done', 'stage-error');

    if (stage === 'SOLICITADO') {
      this.elStageRequested.classList.add('stage-active');
    } else if (stage === 'ACEPTADO_API') {
      this.elStageRequested.classList.add('stage-done');
      this.elStageAccepted.classList.add('stage-done');
    } else if (stage === 'EVENTO_WSS') {
      this.elStageRequested.classList.add('stage-done');
      this.elStageAccepted.classList.add('stage-done');
      this.elStageWsEvent.classList.add('stage-done');
    } else if (stage === 'ERROR') {
      this.elStageRequested.classList.add('stage-done');
      this.elStageAccepted.classList.add('stage-error');
    }
  }

  markWsEventStageReceived() {
    this.updateOrderStages('EVENTO_WSS');
  }

  renderOperationFeedback({ type = 'info', stage = null, title = '', message = '', timestamp = null }) {
    if (stage) {
      this.updateOrderStages(stage);
    }

    if (!this.elFeedbackBanner) return;

    this.elFeedbackBanner.classList.remove('fb-info', 'fb-success', 'fb-warning', 'fb-danger');
    this.elFeedbackBanner.classList.add(`fb-${type}`);

    if (this.elFeedbackTitle) {
      this.elFeedbackTitle.textContent = safeString(title, 'Estado');
    }
    if (this.elFeedbackMessage) {
      this.elFeedbackMessage.textContent = safeString(message, '');
    }
    if (this.elFeedbackTime) {
      this.elFeedbackTime.textContent = formatTimestamp(timestamp || new Date().toISOString());
    }
  }

  renderDemoPanel(demoSnapshot) {
    if (!demoSnapshot) return;

    if (this.elDemoSelect) {
      const currentOptionsCount = this.elDemoSelect.options.length;
      if (currentOptionsCount !== demoSnapshot.demos.length && demoSnapshot.demos.length > 0) {
        this.elDemoSelect.replaceChildren();
        for (const demo of demoSnapshot.demos) {
          const opt = document.createElement('option');
          opt.value = String(demo.id_demo);
          opt.textContent = `DEMO ${demo.id_demo}: ${demo.nombre}`;
          this.elDemoSelect.appendChild(opt);
        }
      }
      if (demoSnapshot.selectedDemoId) {
        this.elDemoSelect.value = String(demoSnapshot.selectedDemoId);
      }
      this.elDemoSelect.disabled = Boolean(demoSnapshot.activeDemo) || demoSnapshot.requestInFlight;
    }

    if (this.elDemoDescription) {
      this.elDemoDescription.textContent = demoSnapshot.selectedDemo
        ? safeString(demoSnapshot.selectedDemo.descripcion, 'Rutina temporizada por servidor.')
        : 'Selecciona una rutina.';
    }

    if (this.elDemoStepsList) {
      this.elDemoStepsList.replaceChildren();

      if (demoSnapshot.loadingSteps) {
        const li = document.createElement('li');
        li.className = 'demo-step-item text-secondary';
        li.textContent = 'Cargando pasos...';
        this.elDemoStepsList.appendChild(li);
      } else if (demoSnapshot.selectedDemoSteps.length === 0) {
        const li = document.createElement('li');
        li.className = 'demo-step-item text-secondary';
        li.textContent = 'Sin pasos registrados.';
        this.elDemoStepsList.appendChild(li);
      } else {
        const activeOrder = demoSnapshot.activeDemo?.currentOrder || null;
        const seenOrders = new Set();

        for (const step of demoSnapshot.selectedDemoSteps) {
          const ordNum = Number(step.orden);
          if (seenOrders.has(ordNum)) continue;
          seenOrders.add(ordNum);

          const li = document.createElement('li');
          li.className = 'demo-step-item';
          if (
            demoSnapshot.activeDemo &&
            Number(demoSnapshot.activeDemo.id_demo) === Number(demoSnapshot.selectedDemoId) &&
            ordNum === Number(activeOrder)
          ) {
            li.classList.add('demo-step-running');
          }

          const badgeOrder = document.createElement('span');
          badgeOrder.className = 'step-order-badge';
          badgeOrder.textContent = `#${step.orden}`;

          const nameSpan = document.createElement('span');
          nameSpan.className = 'step-cmd-name';
          nameSpan.textContent = `${safeString(step.comando)} (Cód. ${step.codigo})`;

          const timingSpan = document.createElement('span');
          timingSpan.className = 'step-timing-meta';
          timingSpan.textContent = `${step.duracion_ms}ms / ${step.pausa_ms}ms`;

          li.append(badgeOrder, nameSpan, timingSpan);
          this.elDemoStepsList.appendChild(li);
        }
      }
    }

    const isRunning = Boolean(demoSnapshot.activeDemo);
    if (this.elDemoActiveBanner) {
      this.elDemoActiveBanner.hidden = !isRunning;
      if (isRunning) {
        const active = demoSnapshot.activeDemo;
        const pct =
          active.total_pasos > 0
            ? Math.min(100, Math.round((Number(active.currentStep || 0) / active.total_pasos) * 100))
            : 15;

        if (this.elDemoActiveTitle) {
          this.elDemoActiveTitle.textContent = `${active.nombre} — ${active.statusText}`;
        }
        if (this.elDemoActiveStep) {
          this.elDemoActiveStep.textContent = active.currentCommand || 'Ejecutando...';
        }
        if (this.elDemoProgressBar) {
          this.elDemoProgressBar.style.width = `${Math.max(10, pct)}%`;
          this.elDemoProgressBar.setAttribute('aria-valuenow', String(pct));
        }
      }
    }

    if (this.elBtnStartDemo) {
      this.elBtnStartDemo.disabled = isRunning || demoSnapshot.requestInFlight || !demoSnapshot.selectedDemoId;
    }
    if (this.elBtnCancelDemo) {
      this.elBtnCancelDemo.disabled = !isRunning || demoSnapshot.requestInFlight;
    }

    document.querySelectorAll('.manual-cmd-btn').forEach((btn) => {
      btn.classList.toggle('locked-by-demo', isRunning);
    });
  }

  // ===========================================================================
  // RENDERIZADO DE LA VISTA 4.2: MONITOREO EN TIEMPO REAL
  // ===========================================================================
  renderMonitoringView(monSnapshot) {
    if (!monSnapshot) return;

    if (this.elMonSyncTime) {
      this.elMonSyncTime.textContent = monSnapshot.lastSyncAt
        ? `Sincronizado: ${formatTimestamp(monSnapshot.lastSyncAt)}`
        : 'Sincronizando...';
    }

    // 1. Tarjeta destacada del Último Movimiento / Estado Reportado
    const last = monSnapshot.lastMovement;
    if (last) {
      if (this.elMonLastCmdName) {
        this.elMonLastCmdName.textContent = safeString(last.comando, 'Sin movimientos');
      }
      if (this.elMonLastCmdCode) {
        this.elMonLastCmdCode.textContent = `Código #${safeString(last.codigo, '—')}`;
      }
      if (this.elMonLastVehicle) {
        this.elMonLastVehicle.textContent = `${safeString(last.vehiculo, 'Excavadora IoT')} (ID #${safeString(
          last.id_vehiculo,
          '1'
        )})`;
      }
      if (this.elMonLastDate) {
        this.elMonLastDate.textContent = safeString(last.fecha, '—');
      }
      if (this.elMonLastTime) {
        this.elMonLastTime.textContent = safeString(last.hora, '—');
      }
      if (this.elMonLastOrigin) {
        this.elMonLastOrigin.textContent = safeString(last.origen, 'API / WSS');
      }
      if (this.elMonLastStageBadge) {
        this.elMonLastStageBadge.textContent = safeString(
          last.stageLabel,
          'Confirmado en Amazon RDS'
        );
      }
    } else {
      if (this.elMonLastCmdName) {
        this.elMonLastCmdName.textContent = monSnapshot.loading ? 'Cargando estado...' : 'Sin movimientos';
      }
      if (this.elMonLastCmdCode) this.elMonLastCmdCode.textContent = 'Código —';
      if (this.elMonLastDate) this.elMonLastDate.textContent = '—';
      if (this.elMonLastTime) this.elMonLastTime.textContent = '—';
    }

    // 2. Tabla dinámica de los 10 Movimientos Individuales Más Recientes Persistidos en RDS
    const rows = monSnapshot.historyRows || [];
    if (this.elMonHistoryCount) {
      this.elMonHistoryCount.textContent = `${rows.length} / 10 registros`;
    }

    if (!this.elMonHistoryTbody) return;
    this.elMonHistoryTbody.replaceChildren();

    if (monSnapshot.loading && rows.length === 0) {
      this.appendTablePlaceholderRow(this.elMonHistoryTbody, 6, 'Cargando historial desde Amazon RDS...');
      return;
    }

    if (monSnapshot.error && rows.length === 0) {
      this.appendTablePlaceholderRow(this.elMonHistoryTbody, 6, `Error: ${monSnapshot.error}`);
      return;
    }

    if (rows.length === 0) {
      this.appendTablePlaceholderRow(
        this.elMonHistoryTbody,
        6,
        'Sin movimientos individuales registrados en Amazon RDS.'
      );
      return;
    }

    rows.forEach((item, index) => {
      const tr = document.createElement('tr');

      const tdNum = document.createElement('td');
      tdNum.className = 'fw-bold text-purple-bright';
      tdNum.textContent = String(index + 1);

      const tdMov = document.createElement('td');
      tdMov.className = 'fw-semibold text-white';
      tdMov.textContent = safeString(item.comando);

      const tdCode = document.createElement('td');
      const codeBadge = document.createElement('span');
      codeBadge.className = 'cmd-code-pill';
      codeBadge.textContent = `#${safeString(item.codigo, '—')}`;
      tdCode.appendChild(codeBadge);

      const tdOrigin = document.createElement('td');
      tdOrigin.className = 'text-secondary small font-monospace d-none d-md-table-cell';
      tdOrigin.textContent = safeString(item.origen, 'API');

      const tdDate = document.createElement('td');
      tdDate.className = 'text-light';
      tdDate.textContent = safeString(item.fecha, '—');

      const tdTime = document.createElement('td');
      tdTime.className = 'text-purple-light font-monospace fw-semibold';
      tdTime.textContent = safeString(item.hora, '—');

      tr.append(tdNum, tdMov, tdCode, tdOrigin, tdDate, tdTime);
      this.elMonHistoryTbody.appendChild(tr);
    });
  }

  // ===========================================================================
  // RENDERIZADO DE LA VISTA 4.3: ADMINISTRACIÓN COMERCIAL MULTIUSUARIO
  // ===========================================================================
  renderAdminView(adminSnapshot) {
    if (!adminSnapshot) return;

    const role = adminSnapshot.currentRole;

    // Actualizar botones de selector de rol y badge superior
    document.querySelectorAll('[data-role-btn]').forEach((btn) => {
      const btnRole = btn.getAttribute('data-role-btn');
      btn.classList.toggle('active', btnRole === role);
    });

    if (this.elHeaderRoleBadge) {
      this.elHeaderRoleBadge.textContent =
        role === ROLES.ADMIN
          ? 'Rol: Administrador'
          : role === ROLES.USUARIO
          ? `Rol: Usuario (#${adminSnapshot.currentBuyerId})`
          : 'Rol: Invitado';
    }

    if (this.elAdminRoleBanner) {
      if (role === ROLES.ADMIN) {
        this.elAdminRoleBanner.textContent =
          'Vista Empresa Comercializadora: Gestión de inventario de unidades, modelos, clientes, asignaciones y editor de DEMOs.';
      } else if (role === ROLES.USUARIO) {
        this.elAdminRoleBanner.textContent =
          'Vista Comprador ("Mis Vehículos"): Operación aislada de tus unidades asignadas y diseño de tus secuencias DEMO.';
      } else {
        this.elAdminRoleBanner.textContent =
          'Vista Invitado ("Prueba nuestros vehículos IoT"): Catálogo de exhibición y sesión temporal exclusiva de 60 segundos.';
      }
    }

    if (this.elAdminUnitsTitle) {
      this.elAdminUnitsTitle.textContent =
        role === ROLES.USUARIO
          ? 'Mis Vehículos Asignados (Aislamiento BOLA/IDOR)'
          : role === ROLES.INVITADO
          ? 'Catálogo Público en Sala de Exhibición'
          : 'Unidades Físicas en Inventario (Amazon RDS)';
    }

    // Mostrar/ocultar paneles exclusivos de Administrador
    document.querySelectorAll('[data-admin-only="true"]').forEach((el) => {
      el.hidden = role !== ROLES.ADMIN;
    });

    // Selector de Cuenta de Comprador (solo visible en rol USUARIO)
    const buyerSelectEl = document.getElementById('admin-buyer-account-select');
    if (buyerSelectEl) {
      buyerSelectEl.hidden = role !== ROLES.USUARIO;
      const buyerClients = (adminSnapshot.clients || []).filter((c) => c.rol === 'USUARIO');
      if (buyerClients.length > 0) {
        this.populateSelectIfChanged(
          buyerSelectEl,
          buyerClients.map((c) => ({
            value: String(c.id_cliente),
            label: `#${c.id_cliente} · ${c.nombre.split(' (')[0]}`
          })),
          String(adminSnapshot.currentBuyerId || '101')
        );
      }
    }

    // Switch de Consentimiento de Soporte Técnico (visible en ADMIN y USUARIO propietario)
    const supportSwitchEl = document.getElementById('admin-support-consent-switch');
    if (supportSwitchEl) {
      supportSwitchEl.checked = Boolean(adminSnapshot.adminSupportConsent);
      if (supportSwitchEl.parentElement) {
        supportSwitchEl.parentElement.hidden =
          role === ROLES.INVITADO || (role === ROLES.USUARIO && !adminSnapshot.isCurrentBuyerOwner);
      }
    }

    // Restringir sub-pestañas de Administración según los permisos reales del rol activo
    const allowedSubviews = adminSnapshot.permissions?.allowedSubviews || [
      'unidades',
      'demos',
      'exhibicion',
      'auditoria'
    ];
    let activeSubtabAllowed = false;
    document.querySelectorAll('[data-admin-subview]').forEach((btn) => {
      const target = btn.getAttribute('data-admin-subview');
      const isAllowed = allowedSubviews.includes(target);
      btn.hidden = !isAllowed;
      btn.disabled = !isAllowed;
      if (target === 'unidades') {
        btn.innerHTML =
          role === ROLES.USUARIO
            ? '<i class="bi bi-truck-flatbed me-1"></i>Mis Vehículos'
            : role === ROLES.INVITADO
            ? '<i class="bi bi-eye me-1"></i>Catálogo Público'
            : '<i class="bi bi-truck-flatbed me-1"></i>Unidades y Clientes';
      }
      if (btn.classList.contains('active') && isAllowed) {
        activeSubtabAllowed = true;
      }
    });

    // Si la sub-pestaña actual quedó prohibida para el rol seleccionado, cambiar a la primera permitida
    if (!activeSubtabAllowed && allowedSubviews.length > 0) {
      const fallbackSub = allowedSubviews[0];
      const subviews = {
        unidades: document.getElementById('admin-subview-unidades'),
        demos: document.getElementById('admin-subview-demos'),
        exhibicion: document.getElementById('admin-subview-exhibicion'),
        auditoria: document.getElementById('admin-subview-auditoria')
      };
      document.querySelectorAll('[data-admin-subview]').forEach((b) => {
        b.classList.toggle('active', b.getAttribute('data-admin-subview') === fallbackSub);
      });
      Object.entries(subviews).forEach(([key, panel]) => {
        if (!panel) return;
        const isMatch = key === fallbackSub;
        panel.hidden = !isMatch;
        panel.classList.toggle('active-subview', isMatch);
      });
    }

    // Bloquear formularios del Editor DEMO si el rol/comprador no es propietario de la unidad (BOLA/IDOR)
    const canEditDemos = Boolean(adminSnapshot.permissions?.canEditDemos);
    ['form-admin-add-demo-step', 'form-admin-new-demo'].forEach((formId) => {
      const formEl = document.getElementById(formId);
      if (formEl) {
        formEl.querySelectorAll('input, select, button').forEach((ctrl) => {
          ctrl.disabled = !canEditDemos;
        });
      }
    });

    // Reflejar permisos visualmente en los botones de la ventana Control
    this.applyControlRoleRestrictions(adminSnapshot);

    // Métricas del Dashboard
    if (this.elMetricModels) this.elMetricModels.textContent = String(adminSnapshot.metrics.totalModels);
    if (this.elMetricUnits) this.elMetricUnits.textContent = String(adminSnapshot.metrics.totalUnits);
    if (this.elMetricAssigned) this.elMetricAssigned.textContent = String(adminSnapshot.metrics.assignedUnits);
    if (this.elMetricExhibition) this.elMetricExhibition.textContent = String(adminSnapshot.metrics.exhibitionUnits);
    if (this.elMetricClients) this.elMetricClients.textContent = String(adminSnapshot.metrics.totalClients);
    if (this.elMetricDemos) this.elMetricDemos.textContent = String(adminSnapshot.metrics.totalDemos);

    // Tabla de Unidades Físicas / Mis Vehículos
    if (this.elAdminUnitsTbody) {
      this.elAdminUnitsTbody.replaceChildren();
      if (adminSnapshot.visibleUnits.length === 0) {
        this.appendTablePlaceholderRow(
          this.elAdminUnitsTbody,
          6,
          'No tienes unidades asignadas a esta cuenta de comprador.'
        );
      } else {
        for (const u of adminSnapshot.visibleUnits) {
          const tr = document.createElement('tr');

          const tdId = document.createElement('td');
          tdId.className = 'fw-bold text-purple-bright';
          tdId.textContent = `#${u.id_vehiculo}`;

          const tdName = document.createElement('td');
          tdName.className = 'fw-semibold text-white';
          tdName.textContent = safeString(u.nombre);

          const tdModel = document.createElement('td');
          tdModel.textContent = safeString(u.modelo);

          const tdMcu = document.createElement('td');
          tdMcu.className = 'small text-secondary d-none d-sm-table-cell';
          tdMcu.textContent = safeString(u.microcontrolador);

          const tdStatus = document.createElement('td');
          const stBadge = document.createElement('span');
          stBadge.className = 'cmd-code-pill';
          stBadge.textContent = safeString(u.estado_comercial, 'Disponible');
          tdStatus.appendChild(stBadge);

          const tdOwner = document.createElement('td');
          tdOwner.className = 'small text-light';
          tdOwner.textContent =
            role === ROLES.INVITADO ? 'Público / Exhibición' : safeString(u.propietario, 'Sin asignar');

          tr.append(tdId, tdName, tdModel, tdMcu, tdStatus, tdOwner);
          this.elAdminUnitsTbody.appendChild(tr);
        }
      }
    }

    // Tabla de Modelos Comerciales
    if (this.elAdminModelsTbody) {
      this.elAdminModelsTbody.replaceChildren();
      for (const m of adminSnapshot.models) {
        const tr = document.createElement('tr');
        const tdCode = document.createElement('td');
        tdCode.className = 'fw-bold text-purple-bright';
        tdCode.textContent = m.codigo_modelo;

        const tdName = document.createElement('td');
        tdName.className = 'fw-semibold text-white';
        tdName.textContent = m.nombre;

        const tdMcu = document.createElement('td');
        tdMcu.className = 'small text-secondary d-none d-sm-table-cell';
        tdMcu.textContent = m.microcontrolador;

        const tdCap = document.createElement('td');
        tdCap.className = 'small text-light';
        tdCap.textContent = m.capacidades;

        tr.append(tdCode, tdName, tdMcu, tdCap);
        this.elAdminModelsTbody.appendChild(tr);
      }
    }

    // Tabla de Clientes y Selectores de Asignación
    if (this.elAdminClientsTbody) {
      this.elAdminClientsTbody.replaceChildren();
      for (const c of adminSnapshot.clients) {
        const tr = document.createElement('tr');
        const tdId = document.createElement('td');
        tdId.className = 'fw-bold text-purple-bright';
        tdId.textContent = `#${c.id_cliente}`;

        const tdName = document.createElement('td');
        tdName.className = 'text-white';
        tdName.textContent = c.nombre;

        const tdMail = document.createElement('td');
        tdMail.className = 'small text-secondary';
        tdMail.textContent = c.correo;

        const tdRole = document.createElement('td');
        tdRole.className = 'small text-purple-light';
        tdRole.textContent = c.rol;

        tr.append(tdId, tdName, tdMail, tdRole);
        this.elAdminClientsTbody.appendChild(tr);
      }
    }

    this.populateSelectIfChanged(
      this.elAssignUnitSelect,
      adminSnapshot.units.map((u) => ({
        value: String(u.id_vehiculo),
        label: `Unidad #${u.id_vehiculo} · ${u.nombre} (${u.modelo})`
      }))
    );

    const clientOptions = adminSnapshot.clients.map((c) => ({
      value: String(c.id_cliente),
      label: `#${c.id_cliente} · ${c.nombre}`
    }));
    this.populateSelectIfChanged(this.elAssignClientSelect, clientOptions);
    this.populateSelectIfChanged(this.elNewUnitClientSelect, clientOptions);

    // Editor Visual de DEMOs en RDS
    this.populateSelectIfChanged(
      this.elAdminDemoSelect,
      adminSnapshot.demos.map((d) => ({
        value: String(d.id_demo),
        label: `DEMO #${d.id_demo}: ${d.nombre}`
      })),
      String(adminSnapshot.editingDemoId || '')
    );

    this.populateSelectIfChanged(
      this.elAdminStepCmdSelect,
      adminSnapshot.commands.map((c) => ({
        value: String(c.id_comando),
        label: `${c.nombre} (Código #${c.codigo})`
      }))
    );

    if (this.elAdminDemoStepsTbody) {
      this.elAdminDemoStepsTbody.replaceChildren();
      if (adminSnapshot.editingDemoSteps.length === 0) {
        this.appendTablePlaceholderRow(
          this.elAdminDemoStepsTbody,
          4,
          'Sin pasos configurados en esta DEMO. Agrega pasos del 1 al 4.'
        );
      } else {
        for (const step of adminSnapshot.editingDemoSteps) {
          const tr = document.createElement('tr');

          const tdOrd = document.createElement('td');
          tdOrd.className = 'fw-bold text-purple-bright';
          tdOrd.textContent = `Paso #${step.orden}`;

          const tdCmd = document.createElement('td');
          tdCmd.className = 'text-white';
          tdCmd.textContent = `${safeString(step.comando)} (Cód. #${step.codigo})`;

          const tdTime = document.createElement('td');
          tdTime.className = 'small text-secondary font-monospace';
          tdTime.textContent = `${step.duracion_ms}ms · pausa ${step.pausa_ms}ms`;

          const tdAct = document.createElement('td');
          tdAct.className = 'text-end';
          const btnDel = document.createElement('button');
          btnDel.type = 'button';
          btnDel.className = 'btn btn-sm btn-outline-danger py-0 px-2';
          btnDel.textContent = 'Quitar';
          btnDel.disabled = !canEditDemos;
          btnDel.setAttribute('data-delete-demo-step', String(step.id_demo_movimiento));
          tdAct.appendChild(btnDel);

          tr.append(tdOrd, tdCmd, tdTime, tdAct);
          this.elAdminDemoStepsTbody.appendChild(tr);
        }
      }
    }

    // Sesión Temporal de Exhibición para Invitados
    const guest = adminSnapshot.guestSession;
    if (this.elGuestSessionBadge) {
      this.elGuestSessionBadge.textContent = guest.active
        ? `RESERVA ACTIVA (${guest.holderId})`
        : 'DISPONIBLE PARA PRUEBA';
      this.elGuestSessionBadge.className = guest.active ? 'conn-badge badge-ok' : 'conn-badge badge-neutral';
    }
    if (this.elGuestTimerDisplay) {
      const secs = Math.max(0, Number(guest.remainingSeconds || 0));
      const mm = String(Math.floor(secs / 60)).padStart(2, '0');
      const ss = String(secs % 60).padStart(2, '0');
      this.elGuestTimerDisplay.textContent = `${mm}:${ss}`;
    }
    if (this.elBtnStartGuest) {
      this.elBtnStartGuest.disabled = guest.active;
    }
    if (this.elBtnEndGuest) {
      this.elBtnEndGuest.disabled = !guest.active;
    }

    // Bitácora de Auditoría
    if (this.elAdminAuditTbody) {
      this.elAdminAuditTbody.replaceChildren();
      for (const log of adminSnapshot.auditLogs.slice(0, 10)) {
        const tr = document.createElement('tr');

        const tdTime = document.createElement('td');
        tdTime.className = 'small text-secondary font-monospace';
        tdTime.textContent = log.timestamp;

        const tdActor = document.createElement('td');
        tdActor.className = 'fw-bold text-purple-light small';
        tdActor.textContent = log.actor;

        const tdAction = document.createElement('td');
        tdAction.className = 'small text-white';
        tdAction.textContent = log.action;

        const tdRes = document.createElement('td');
        tdRes.className = 'small text-secondary d-none d-sm-table-cell';
        tdRes.textContent = log.resource;

        const tdOut = document.createElement('td');
        tdOut.className = log.result.includes('DENEGADO')
          ? 'small text-danger fw-bold'
          : 'small text-success fw-semibold';
        tdOut.textContent = log.result;

        tr.append(tdTime, tdActor, tdAction, tdRes, tdOut);
        this.elAdminAuditTbody.appendChild(tr);
      }
    }
  }

  /**
   * Refleja visualmente en la ventana `Control` las restricciones RBAC/BOLA del rol activo:
   * - `INVITADO`: Solo Orugas (#9,#11,#2,#4), Luz (#8) y DETENER (#0) permitidos cuando su sesión de 60s está activa;
   *   Brazo/Torre (#1,#3,#10,#12), Velocidad (#5), Sonido (#6) y DEMO quedan bloqueados visualmente (403).
   * - `USUARIO` sin propiedad (ej. Comprador #102 sobre Unidad #1): Bloqueo BOLA/IDOR (403).
   * - `ADMIN` sin consentimiento de soporte sobre unidad vendida: Bloqueo de operación (403).
   */
  applyControlRoleRestrictions(adminSnapshot) {
    if (!adminSnapshot || typeof document === 'undefined') return;
    const role = adminSnapshot.currentRole;
    const isOwner = Boolean(adminSnapshot.isCurrentBuyerOwner);
    const supportOk = Boolean(adminSnapshot.adminSupportConsent);
    const guest = adminSnapshot.guestSession || {};
    const allowedGuestCodes = guest.allowedCodes || [9, 11, 2, 4, 8];

    document.querySelectorAll('[data-command-code]').forEach((btn) => {
      const code = Number(btn.getAttribute('data-command-code'));
      let restricted = false;

      if (role === ROLES.ADMIN && !supportOk) {
        restricted = true;
      } else if (role === ROLES.USUARIO && !isOwner) {
        restricted = true;
      } else if (role === ROLES.INVITADO) {
        restricted = !guest.active || !allowedGuestCodes.includes(code);
      }

      btn.style.opacity = restricted ? '0.38' : '1';
      btn.style.filter = restricted ? 'grayscale(0.65)' : 'none';
    });

    const rightZone = document.getElementById('right-joystick-zone');
    const leftZone = document.getElementById('left-joystick-zone');
    const demoRunBtn = document.getElementById('btn-func-demo');

    const fullRestricted =
      (role === ROLES.ADMIN && !supportOk) ||
      (role === ROLES.USUARIO && !isOwner) ||
      (role === ROLES.INVITADO && !guest.active);

    if (leftZone) {
      leftZone.style.opacity = fullRestricted ? '0.45' : '1';
    }
    if (rightZone) {
      rightZone.style.opacity = fullRestricted || role === ROLES.INVITADO ? '0.38' : '1';
    }
    if (demoRunBtn) {
      const demoRestricted = fullRestricted || role === ROLES.INVITADO;
      demoRunBtn.style.opacity = demoRestricted ? '0.38' : '1';
    }
  }

  populateSelectIfChanged(selectEl, options = [], selectedVal = null) {
    if (!selectEl || !Array.isArray(options)) return;
    const currentSignature = Array.from(selectEl.options)
      .map((o) => `${o.value}:${o.textContent}`)
      .join('|');
    const nextSignature = options.map((o) => `${o.value}:${o.label}`).join('|');

    if (currentSignature !== nextSignature) {
      const prevVal = selectedVal || selectEl.value;
      selectEl.replaceChildren();
      for (const item of options) {
        const opt = document.createElement('option');
        opt.value = item.value;
        opt.textContent = item.label;
        selectEl.appendChild(opt);
      }
      if (prevVal && options.some((o) => o.value === prevVal)) {
        selectEl.value = prevVal;
      }
    } else if (selectedVal && selectEl.value !== selectedVal) {
      selectEl.value = selectedVal;
    }
  }

  appendTablePlaceholderRow(tbody, colSpan, text) {
    const tr = document.createElement('tr');
    const td = document.createElement('td');
    td.colSpan = colSpan;
    td.className = 'text-center text-secondary py-3 small';
    td.textContent = text;
    tr.appendChild(td);
    tbody.appendChild(tr);
  }

  appendWsEventLog(wsEvent) {
    if (!this.elWsEventsFeed || !wsEvent) return;

    const emptyPlaceholder = this.elWsEventsFeed.querySelector('.ws-feed-empty');
    if (emptyPlaceholder) {
      emptyPlaceholder.remove();
    }

    const row = document.createElement('div');
    row.className = 'ws-event-row';

    const typeBadge = document.createElement('span');
    typeBadge.className = 'ws-event-type';
    typeBadge.textContent = safeString(wsEvent.tipo, 'evento');

    const desc = document.createElement('span');
    desc.className = 'ws-event-summary';

    if (wsEvent.tipo === 'movimiento_individual') {
      desc.textContent = `Veh #${wsEvent.id_vehiculo} → ${wsEvent.comando} (Cód. ${wsEvent.codigo})`;
    } else if (wsEvent.tipo === 'detener') {
      desc.textContent = `Veh #${wsEvent.id_vehiculo} → DETENER (Cód. 0 · Prioridad máxima)`;
    } else if (wsEvent.tipo === 'demo_inicio') {
      desc.textContent = `Inicio DEMO #${wsEvent.id_demo}: ${wsEvent.nombre} (${wsEvent.total_pasos} pasos)`;
    } else if (wsEvent.tipo === 'demo_paso') {
      desc.textContent = `Paso ${wsEvent.paso}/${wsEvent.total_pasos}: ${wsEvent.comando} (Cód. ${wsEvent.codigo}, ${wsEvent.duracion_ms}ms)`;
    } else if (wsEvent.tipo === 'demo_fin') {
      desc.textContent = `DEMO #${wsEvent.id_demo} finalizada (${wsEvent.total_pasos_ejecutados} pasos)`;
    } else if (wsEvent.tipo === 'demo_cancelada') {
      desc.textContent = `DEMO #${wsEvent.id_demo} cancelada en paso #${wsEvent.paso_interrumpido ?? '—'}`;
    } else if (wsEvent.tipo === 'estado_inicial') {
      const first = Array.isArray(wsEvent.estado) && wsEvent.estado[0] ? wsEvent.estado[0] : {};
      desc.textContent = `Conectado · Estado: ${safeString(first.comando, '—')} (Cód. ${first.codigo ?? '—'})`;
    } else {
      desc.textContent = JSON.stringify(wsEvent);
    }

    const timeEl = document.createElement('time');
    timeEl.className = 'ws-event-time';
    timeEl.textContent = formatTimestamp(wsEvent.timestamp);

    row.append(typeBadge, desc, timeEl);
    this.elWsEventsFeed.prepend(row);

    while (this.elWsEventsFeed.children.length > 8) {
      this.elWsEventsFeed.lastElementChild.remove();
    }
  }
}

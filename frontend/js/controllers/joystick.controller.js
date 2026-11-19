/**
 * @file joystick.controller.js
 * @description Controlador reutilizable de Joystick Circular Táctil basado en Pointer Events API
 * (compatible con operación dual simultánea en pantallas táctiles móviles, tabletas, mouse y teclado).
 *
 * Instancias soportadas en la Cabina CAT-320:
 * 1. Joystick Izquierdo (Orugas / Tracción):
 *    - Arriba: Avanzar (Código 9)
 *    - Abajo: Retroceder (Código 11)
 *    - Izquierda: Girar izquierda (Código 2)
 *    - Derecha: Girar derecha (Código 4)
 * 2. Joystick Derecho (Brazo y Torre):
 *    - Arriba: Mover brazo arriba (Código 1)
 *    - Abajo: Mover brazo abajo (Código 3)
 *    - Izquierda: Girar torre izquierda (Código 10)
 *    - Derecha: Girar torre derecha (Código 12)
 */

import { APP_CONFIG } from '../config/api.config.js';

export const JOYSTICK_DIRECTIONS = Object.freeze({
  NEUTRAL: 'NEUTRAL',
  UP: 'UP',
  DOWN: 'DOWN',
  LEFT: 'LEFT',
  RIGHT: 'RIGHT'
});

const DEFAULT_LEFT_DIRECTION_MAP = Object.freeze({
  [JOYSTICK_DIRECTIONS.NEUTRAL]: {
    label: 'NEUTRAL',
    code: null
  },
  [JOYSTICK_DIRECTIONS.UP]: {
    label: 'AVANZAR · #9',
    code: APP_CONFIG.JOYSTICK_CODES.UP
  },
  [JOYSTICK_DIRECTIONS.DOWN]: {
    label: 'RETROCEDER · #11',
    code: APP_CONFIG.JOYSTICK_CODES.DOWN
  },
  [JOYSTICK_DIRECTIONS.LEFT]: {
    label: 'ORUGA IZQ · #2',
    code: APP_CONFIG.JOYSTICK_CODES.LEFT
  },
  [JOYSTICK_DIRECTIONS.RIGHT]: {
    label: 'ORUGA DER · #4',
    code: APP_CONFIG.JOYSTICK_CODES.RIGHT
  }
});

export const RIGHT_ARM_DIRECTION_MAP = Object.freeze({
  [JOYSTICK_DIRECTIONS.NEUTRAL]: {
    label: 'NEUTRAL',
    code: null
  },
  [JOYSTICK_DIRECTIONS.UP]: {
    label: 'BRAZO ARRIBA · #1',
    code: APP_CONFIG.RIGHT_JOYSTICK_CODES.UP
  },
  [JOYSTICK_DIRECTIONS.DOWN]: {
    label: 'BRAZO ABAJO · #3',
    code: APP_CONFIG.RIGHT_JOYSTICK_CODES.DOWN
  },
  [JOYSTICK_DIRECTIONS.LEFT]: {
    label: 'TORRE IZQ · #10',
    code: APP_CONFIG.RIGHT_JOYSTICK_CODES.LEFT
  },
  [JOYSTICK_DIRECTIONS.RIGHT]: {
    label: 'TORRE DER · #12',
    code: APP_CONFIG.RIGHT_JOYSTICK_CODES.RIGHT
  }
});

export class JoystickController {
  /**
   * @param {object} options
   * @param {HTMLElement} options.baseElement Contenedor circular exterior del joystick
   * @param {HTMLElement} options.stickElement Pomo móvil central del joystick
   * @param {HTMLElement} [options.directionLabelElement] Etiqueta visual de dirección actual
   * @param {import('./command.dispatcher.js').CommandDispatcher} options.dispatcher
   * @param {object} [options.directionMap] Mapa de direcciones y códigos
   * @param {string} [options.joystickId='LEFT'] Identificador de la palanca ('LEFT' o 'RIGHT')
   */
  constructor({
    baseElement,
    stickElement,
    directionLabelElement = null,
    dispatcher,
    directionMap = DEFAULT_LEFT_DIRECTION_MAP,
    joystickId = 'LEFT'
  }) {
    this.baseElement = baseElement;
    this.stickElement = stickElement;
    this.directionLabelElement = directionLabelElement;
    this.dispatcher = dispatcher;
    this.directionMap = directionMap || DEFAULT_LEFT_DIRECTION_MAP;
    this.joystickId = joystickId;

    this.activePointerId = null;
    this.currentDirection = JOYSTICK_DIRECTIONS.NEUTRAL;
    this.disabled = false;
    this.deadZoneRatio = 0.30; // 30% del radio interior actúa como zona neutral segura
  }

  /**
   * Inicializa los listeners de Pointer Events, bloqueo anti-refresh móvil y soporte de teclado.
   */
  init() {
    if (!this.baseElement || !this.stickElement) return;

    // Bloqueo explícito de gestos de scroll/pull-to-refresh del navegador móvil sobre el joystick
    this.baseElement.addEventListener(
      'touchstart',
      (e) => {
        if (!this.disabled && e.cancelable) {
          e.preventDefault();
        }
      },
      { passive: false }
    );
    this.baseElement.addEventListener(
      'touchmove',
      (e) => {
        if (e.cancelable) {
          e.preventDefault();
        }
      },
      { passive: false }
    );

    this.baseElement.addEventListener('pointerdown', (e) => this.handlePointerDown(e));
    this.baseElement.addEventListener('pointermove', (e) => this.handlePointerMove(e));
    this.baseElement.addEventListener('pointerup', (e) => this.handlePointerUpOrCancel(e, 'POINTER_UP'));
    this.baseElement.addEventListener('pointercancel', (e) => this.handlePointerUpOrCancel(e, 'POINTER_CANCEL'));
    this.baseElement.addEventListener('lostpointercapture', (e) =>
      this.handlePointerUpOrCancel(e, 'LOST_CAPTURE')
    );

    // Si la ventana pierde foco mientras se arrastra el joystick, forzar retorno a neutral
    window.addEventListener('blur', () => {
      if (this.activePointerId !== null || this.currentDirection !== JOYSTICK_DIRECTIONS.NEUTRAL) {
        this.resetToNeutral('WINDOW_BLUR');
      }
    });

    // Soporte de teclado accesible cuando el joystick tiene el foco
    this.baseElement.addEventListener('keydown', (e) => this.handleKeyDown(e));
    this.baseElement.addEventListener('keyup', (e) => this.handleKeyUp(e));
  }

  /**
   * Habilita o deshabilita el joystick (ej. durante una DEMO activa).
   * @param {boolean} isDisabled
   */
  setDisabled(isDisabled) {
    this.disabled = Boolean(isDisabled);
    if (this.baseElement) {
      this.baseElement.classList.toggle('joystick-disabled', this.disabled);
      this.baseElement.setAttribute('aria-disabled', String(this.disabled));
    }
    if (this.disabled && this.currentDirection !== JOYSTICK_DIRECTIONS.NEUTRAL) {
      this.resetToNeutral('DEMO_LOCK');
    }
  }

  handlePointerDown(event) {
    if (this.disabled) return;
    if (this.activePointerId !== null) return;

    event.preventDefault();
    this.activePointerId = event.pointerId;
    try {
      this.baseElement.setPointerCapture(event.pointerId);
    } catch {
      // Fallback en caso de puntero sintético
    }

    this.baseElement.classList.add('joystick-active');
    this.evaluatePointerPosition(event.clientX, event.clientY);
  }

  handlePointerMove(event) {
    if (this.disabled) return;
    if (this.activePointerId === null || event.pointerId !== this.activePointerId) return;

    event.preventDefault();
    this.evaluatePointerPosition(event.clientX, event.clientY);
  }

  handlePointerUpOrCancel(event, reason = 'RELEASE') {
    if (this.activePointerId !== null && event.pointerId !== this.activePointerId) {
      return;
    }

    if (this.activePointerId !== null) {
      try {
        if (this.baseElement.hasPointerCapture(this.activePointerId)) {
          this.baseElement.releasePointerCapture(this.activePointerId);
        }
      } catch {
        // Ignorar si ya fue liberado
      }
    }

    this.resetToNeutral(reason);
  }

  /**
   * Calcula el desplazamiento polar respecto al centro del joystick, posiciona el pomo visualmente
   * y determina si hubo una transición real de dirección para evitar POST repetitivos por píxel.
   * @param {number} clientX Coordenada X del puntero
   * @param {number} clientY Coordenada Y del puntero
   */
  evaluatePointerPosition(clientX, clientY) {
    const rect = this.baseElement.getBoundingClientRect();
    const centerX = rect.left + rect.width / 2;
    const centerY = rect.top + rect.height / 2;

    const deltaX = clientX - centerX;
    const deltaY = clientY - centerY;
    const distance = Math.hypot(deltaX, deltaY);

    const maxRadius = Math.max((rect.width - this.stickElement.offsetWidth) / 2, 26);
    const clampedDistance = Math.min(distance, maxRadius);
    const angleRad = Math.atan2(deltaY, deltaX);

    const visualX = Math.cos(angleRad) * clampedDistance;
    const visualY = Math.sin(angleRad) * clampedDistance;

    // Actualizar posición visual suave sin disparar red por cada frame
    this.stickElement.style.transform = `translate3d(${visualX.toFixed(1)}px, ${visualY.toFixed(1)}px, 0)`;

    const normalizedMagnitude = distance / maxRadius;
    let nextDirection = JOYSTICK_DIRECTIONS.NEUTRAL;

    if (normalizedMagnitude >= this.deadZoneRatio) {
      const angleDeg = (angleRad * 180) / Math.PI; // Rango [-180, 180], -90 es Arriba, 90 es Abajo
      if (angleDeg >= -135 && angleDeg < -45) {
        nextDirection = JOYSTICK_DIRECTIONS.UP;
      } else if (angleDeg >= 45 && angleDeg < 135) {
        nextDirection = JOYSTICK_DIRECTIONS.DOWN;
      } else if (angleDeg >= -45 && angleDeg < 45) {
        nextDirection = JOYSTICK_DIRECTIONS.RIGHT;
      } else {
        nextDirection = JOYSTICK_DIRECTIONS.LEFT;
      }
    }

    this.applyDirectionTransition(nextDirection, 'POINTER_MOVE');
  }

  /**
   * Restaura el pomo al centro y notifica la transición a NEUTRAL.
   * @param {string} [reason='RESET']
   */
  resetToNeutral(reason = 'RESET') {
    this.activePointerId = null;
    if (this.baseElement) {
      this.baseElement.classList.remove('joystick-active');
    }
    if (this.stickElement) {
      this.stickElement.style.transform = 'translate3d(0px, 0px, 0)';
    }
    this.applyDirectionTransition(JOYSTICK_DIRECTIONS.NEUTRAL, reason);
  }

  /**
   * Ejecuta acciones únicamente cuando la dirección cambia respecto a la anterior.
   * @param {string} nextDirection Nueva dirección calculada
   * @param {string} reason Causa de la transición
   */
  applyDirectionTransition(nextDirection, reason) {
    if (this.currentDirection === nextDirection) {
      return;
    }

    this.currentDirection = nextDirection;
    const meta = this.directionMap[nextDirection] || this.directionMap[JOYSTICK_DIRECTIONS.NEUTRAL];

    if (this.baseElement) {
      this.baseElement.setAttribute('data-direction', nextDirection.toLowerCase());
    }

    if (this.directionLabelElement) {
      this.directionLabelElement.textContent = meta.label;
    }

    if (this.dispatcher) {
      this.dispatcher.handleJoystickTransition(meta.code, reason, this.joystickId);
    }
  }

  /**
   * Soporte accesible de teclado sobre el contenedor del joystick.
   * @param {KeyboardEvent} event
   */
  handleKeyDown(event) {
    if (this.disabled) return;
    if (event.repeat) return;

    const offsetPx = 36;
    switch (event.key) {
      case 'ArrowUp':
      case 'w':
      case 'W':
        event.preventDefault();
        this.stickElement.style.transform = `translate3d(0px, -${offsetPx}px, 0)`;
        this.applyDirectionTransition(JOYSTICK_DIRECTIONS.UP, 'KEYBOARD');
        break;
      case 'ArrowDown':
      case 's':
      case 'S':
        event.preventDefault();
        this.stickElement.style.transform = `translate3d(0px, ${offsetPx}px, 0)`;
        this.applyDirectionTransition(JOYSTICK_DIRECTIONS.DOWN, 'KEYBOARD');
        break;
      case 'ArrowLeft':
      case 'a':
      case 'A':
        event.preventDefault();
        this.stickElement.style.transform = `translate3d(-${offsetPx}px, 0px, 0)`;
        this.applyDirectionTransition(JOYSTICK_DIRECTIONS.LEFT, 'KEYBOARD');
        break;
      case 'ArrowRight':
      case 'd':
      case 'D':
        event.preventDefault();
        this.stickElement.style.transform = `translate3d(${offsetPx}px, 0px, 0)`;
        this.applyDirectionTransition(JOYSTICK_DIRECTIONS.RIGHT, 'KEYBOARD');
        break;
      case ' ':
      case 'Escape':
        event.preventDefault();
        this.resetToNeutral('KEYBOARD_STOP');
        break;
      default:
        break;
    }
  }

  handleKeyUp(event) {
    const navKeys = ['ArrowUp', 'ArrowDown', 'ArrowLeft', 'ArrowRight', 'w', 'W', 'a', 'A', 's', 'S', 'd', 'D'];
    if (navKeys.includes(event.key)) {
      event.preventDefault();
      this.resetToNeutral('KEYBOARD_RELEASE');
    }
  }
}

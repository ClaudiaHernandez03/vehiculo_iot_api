/**
 * @file validators.js
 * @description Funciones puras de validación de contratos JSON remotos, sanitización de cadenas
 * y verificación de comandos contra el catálogo cargado desde Amazon RDS.
 */

/**
 * Comprueba si un valor es un objeto plano no nulo.
 * @param {*} value Valor a evaluar
 * @returns {boolean}
 */
export function isPlainObject(value) {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

/**
 * Sanitiza un texto remoto para evitar cualquier problema visual o de formato.
 * Nota: El renderizado en el DOM siempre utiliza textContent (nunca innerHTML con datos remotos).
 * @param {*} value Valor de entrada
 * @param {string} [fallback='—'] Valor por defecto si es nulo o vacío
 * @returns {string}
 */
export function safeString(value, fallback = '—') {
  if (value === null || value === undefined) return fallback;
  const text = String(value).trim();
  return text.length > 0 ? text : fallback;
}

/**
 * Convierte un valor a entero positivo válido (> 0), cumpliendo con exclusiveMinimum: 0 de OpenAPI.
 * @param {*} value Valor a evaluar
 * @returns {number|null}
 */
export function toPositiveInteger(value) {
  const num = Number(value);
  if (Number.isInteger(num) && num > 0) {
    return num;
  }
  return null;
}

/**
 * Valida la respuesta real de GET /health
 * Contrato real: {"ok": true, "servicio": "vehiculo_iot_api", "version": "2.6.0"}
 * @param {*} payload Respuesta JSON
 * @returns {{ apiAvailable: boolean, serviceName: string, version: string, hasRdsField: boolean, rdsAvailable: boolean|null }}
 */
export function validateHealthResponse(payload) {
  if (!isPlainObject(payload)) {
    return {
      apiAvailable: false,
      serviceName: 'Desconocido',
      version: '—',
      hasRdsField: false,
      rdsAvailable: null
    };
  }

  const apiAvailable = payload.ok === true;
  const serviceName = safeString(payload.servicio, 'vehiculo_iot_api');
  const version = safeString(payload.version, '—');

  // Comprueba si /health incluye explícitamente un diagnóstico de base de datos
  const hasExplicitDb =
    'base_datos' in payload ||
    'rds' in payload ||
    'database' in payload ||
    'db' in payload;

  let rdsAvailable = null;
  if (hasExplicitDb) {
    const dbInfo = payload.base_datos ?? payload.rds ?? payload.database ?? payload.db;
    if (typeof dbInfo === 'boolean') {
      rdsAvailable = dbInfo;
    } else if (isPlainObject(dbInfo)) {
      rdsAvailable = dbInfo.ok === true || dbInfo.conectado === true || dbInfo.transaccion === 'confirmada';
    }
  }

  return {
    apiAvailable,
    serviceName,
    version,
    hasRdsField: hasExplicitDb,
    rdsAvailable
  };
}

/**
 * Valida si una respuesta de endpoint basado en Stored Procedure confirma acceso real a Amazon RDS.
 * Los endpoints de FastAPI que ejecutan SPs en RDS devuelven:
 * { "ok": true, "procedimiento": "sp_...", "resultados": [...] }
 * Y POST /api/movimientos devuelve:
 * { "ok": true, ..., "base_datos": { "transaccion": "confirmada", "conectado": true, ... } }
 *
 * @param {*} payload Objeto JSON devuelto por el backend
 * @returns {{ verified: boolean, available: boolean, procedure: string|null, detail: string }}
 */
export function evaluateRdsVerification(payload) {
  if (!isPlainObject(payload)) {
    return {
      verified: false,
      available: false,
      procedure: null,
      detail: 'Respuesta de diagnóstico no válida'
    };
  }

  // Caso 1: Respuesta de transacción de movimiento (POST /api/movimientos)
  if (isPlainObject(payload.base_datos)) {
    const confirmed =
      payload.base_datos.transaccion === 'confirmada' ||
      payload.base_datos.conectado === true;
    return {
      verified: true,
      available: confirmed,
      procedure: 'Transacción RDS (sp_agregar_movimiento)',
      detail: confirmed
        ? 'Transacción confirmada en Amazon RDS MySQL'
        : 'La transacción en RDS no pudo confirmarse'
    };
  }

  // Caso 2: Respuesta de consulta que ejecutó un Stored Procedure en Amazon RDS
  if (payload.ok === true && typeof payload.procedimiento === 'string' && Array.isArray(payload.resultados)) {
    return {
      verified: true,
      available: true,
      procedure: payload.procedimiento,
      detail: `Verificado vía ${payload.procedimiento} (${payload.resultados.length} reg.)`
    };
  }

  return {
    verified: false,
    available: false,
    procedure: null,
    detail: 'Sin comprobación explícita de RDS en este endpoint'
  };
}

/**
 * Formatea una marca temporal ISO 8601 a hora local legible.
 * @param {string} isoString Fecha en formato ISO
 * @returns {string}
 */
export function formatTimestamp(isoString) {
  if (!isoString) return 'Sin registro';
  try {
    const raw = String(isoString).trim();
    const normalized = raw.includes('Z') || /[+-]\d{2}:\d{2}$/.test(raw) ? raw : `${raw}Z`;
    const date = new Date(normalized);
    if (Number.isNaN(date.getTime())) {
      return safeString(isoString);
    }
    return date.toLocaleTimeString('es-MX', {
      hour12: false,
      hour: '2-digit',
      minute: '2-digit',
      second: '2-digit'
    });
  } catch {
    return safeString(isoString);
  }
}

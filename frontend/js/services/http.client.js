/**
 * @file http.client.js
 * @description Cliente HTTP modular basado en Fetch API y async/await.
 * Centraliza URLs, métodos HTTP, cabeceras, AbortController con timeout configurable,
 * parseo seguro de JSON, clasificación de errores de red/HTTP y mensajes amigables para el operador.
 */

import { APP_CONFIG, buildApiUrl } from '../config/api.config.js';
import { isPlainObject, safeString } from '../utils/validators.js';

/**
 * Error personalizado para operaciones HTTP de la aplicación IoT.
 */
export class ApiHttpError extends Error {
  /**
   * @param {string} message Mensaje legible para el operador
   * @param {object} [options] Metadatos adicionales del fallo
   * @param {number} [options.status=0] Código HTTP (0 si fue error de red o CORS)
   * @param {string} [options.code='NETWORK_ERROR'] Categoría interna del error
   * @param {string} [options.endpoint=''] Endpoint invocado
   * @param {*} [options.details=null] Payload de error devuelto por FastAPI
   */
  constructor(message, { status = 0, code = 'NETWORK_ERROR', endpoint = '', details = null } = {}) {
    super(message);
    this.name = 'ApiHttpError';
    this.status = status;
    this.code = code;
    this.endpoint = endpoint;
    this.details = details;
  }
}

export class HttpClient {
  /**
   * @param {number} [defaultTimeoutMs=APP_CONFIG.HTTP_TIMEOUT_MS] Tiempo máximo de espera por petición
   */
  constructor(defaultTimeoutMs = APP_CONFIG.HTTP_TIMEOUT_MS) {
    this.defaultTimeoutMs = defaultTimeoutMs;
  }

  /**
   * Ejecuta una petición HTTP hacia la API FastAPI en AWS.
   * @param {string} path Ruta relativa del endpoint (ej. '/api/movimientos')
   * @param {object} [options] Opciones de configuración
   * @param {'GET'|'POST'|'PUT'|'DELETE'} [options.method='GET'] Método HTTP
   * @param {object|null} [options.body=null] Cuerpo JSON para métodos de escritura
   * @param {number} [options.timeoutMs] Timeout específico para esta llamada
   * @param {AbortSignal|null} [options.externalSignal=null] Señal externa para cancelar la petición
   * @returns {Promise<any>} Datos JSON parseados
   */
  async request(path, { method = 'GET', body = null, timeoutMs = this.defaultTimeoutMs, externalSignal = null } = {}) {
    const url = buildApiUrl(path);
    const controller = new AbortController();
    let timedOut = false;

    const onExternalAbort = () => controller.abort();
    if (externalSignal) {
      if (externalSignal.aborted) {
        throw new ApiHttpError('Operación cancelada por prioridad del operador.', {
          status: 0,
          code: 'ABORTED_BY_OPERATOR',
          endpoint: path
        });
      }
      externalSignal.addEventListener('abort', onExternalAbort, { once: true });
    }

    const timerId = window.setTimeout(() => {
      timedOut = true;
      controller.abort();
    }, timeoutMs);

    const headers = {
      Accept: 'application/json'
    };

    const fetchOptions = {
      method,
      headers,
      signal: controller.signal,
      mode: 'cors',
      credentials: 'omit'
    };

    if (body !== null && method !== 'GET') {
      headers['Content-Type'] = 'application/json';
      fetchOptions.body = JSON.stringify(body);
    }

    if (method !== 'GET') {
      try {
        const runtimeApiKey =
          (typeof globalThis.IOT_API_KEY === 'string' && globalThis.IOT_API_KEY.trim()) ||
          (typeof globalThis.sessionStorage !== 'undefined' && globalThis.sessionStorage.getItem('IOT_API_KEY')?.trim()) ||
          (typeof globalThis.localStorage !== 'undefined' && globalThis.localStorage.getItem('IOT_API_KEY')?.trim()) ||
          '';
        if (runtimeApiKey) {
          headers['X-API-Key'] = runtimeApiKey;
        }
      } catch {
        // Ignorar si sessionStorage/localStorage no están disponibles en el entorno actual
      }
    }

    try {
      const response = await window.fetch(url, fetchOptions);
      const contentType = response.headers.get('content-type') || '';
      let parsedData = null;

      if (contentType.includes('application/json')) {
        try {
          parsedData = await response.json();
        } catch {
          throw new ApiHttpError('El servidor devolvió un JSON malformado.', {
            status: response.status,
            code: 'INVALID_JSON',
            endpoint: path
          });
        }
      } else {
        const rawText = await response.text();
        parsedData = { raw: rawText };
      }

      if (!response.ok) {
        const serverDetail = this.extractFastApiErrorDetail(parsedData, response.status);
        throw new ApiHttpError(serverDetail, {
          status: response.status,
          code: `HTTP_${response.status}`,
          endpoint: path,
          details: parsedData
        });
      }

      return parsedData;
    } catch (error) {
      if (error instanceof ApiHttpError) {
        throw error;
      }

      if (error && error.name === 'AbortError') {
        if (timedOut) {
          throw new ApiHttpError(
            `Tiempo de espera agotado (${Math.round(timeoutMs / 1000)}s) al conectar con ${path}.`,
            { status: 0, code: 'TIMEOUT', endpoint: path }
          );
        }
        throw new ApiHttpError('Solicitud en vuelo cancelada por transición o botón DETENER.', {
          status: 0,
          code: 'ABORTED',
          endpoint: path
        });
      }

      throw new ApiHttpError(
        'No fue posible conectar con el servidor AWS (verifique conexión a Internet o CORS).',
        { status: 0, code: 'NETWORK_ERROR', endpoint: path, details: error?.message || null }
      );
    } finally {
      window.clearTimeout(timerId);
      if (externalSignal) {
        externalSignal.removeEventListener('abort', onExternalAbort);
      }
    }
  }

  /**
   * Consulta un recurso mediante HTTP GET.
   * @param {string} path Ruta del endpoint
   * @param {object} [options] Opciones adicionales
   */
  async get(path, options = {}) {
    return this.request(path, { ...options, method: 'GET', body: null });
  }

  /**
   * Envía una acción o registro mediante HTTP POST.
   * @param {string} path Ruta del endpoint
   * @param {object|null} [body=null] Cuerpo de la solicitud
   * @param {object} [options] Opciones adicionales
   */
  async post(path, body = null, options = {}) {
    return this.request(path, { ...options, method: 'POST', body });
  }

  /**
   * Actualiza un recurso existente mediante HTTP PUT.
   * @param {string} path Ruta del endpoint
   * @param {object|null} [body=null] Cuerpo JSON
   * @param {object} [options] Opciones adicionales
   */
  async put(path, body = null, options = {}) {
    return this.request(path, { ...options, method: 'PUT', body });
  }

  /**
   * Elimina un recurso mediante HTTP DELETE.
   * @param {string} path Ruta del endpoint
   * @param {object} [options] Opciones adicionales
   */
  async delete(path, options = {}) {
    return this.request(path, { ...options, method: 'DELETE', body: null });
  }

  /**
   * Extrae un mensaje legible a partir de errores estándar de FastAPI (422, 409, 404, 500).
   * @param {*} data Respuesta JSON de error
   * @param {number} status Código de estado HTTP
   * @returns {string}
   */
  extractFastApiErrorDetail(data, status) {
    if (isPlainObject(data)) {
      if (typeof data.mensaje === 'string' && data.mensaje.trim()) {
        return data.mensaje.trim();
      }
      if (typeof data.detail === 'string' && data.detail.trim()) {
        return data.detail.trim();
      }
      if (Array.isArray(data.detail) && data.detail.length > 0) {
        const firstIssue = data.detail[0];
        const field = Array.isArray(firstIssue?.loc) ? firstIssue.loc.slice(-1)[0] : 'campo';
        const msg = safeString(firstIssue?.msg, 'valor inválido');
        return `Error de validación en '${field}': ${msg}`;
      }
    }

    switch (status) {
      case 400:
        return 'Solicitud inválida (HTTP 400). Verifique los parámetros enviados.';
      case 401:
      case 403:
        return 'Operación restringida por permisos del servidor (HTTP 401/403).';
      case 404:
        return 'El recurso o endpoint solicitado no existe en el servidor (HTTP 404).';
      case 409:
        return 'Conflicto en el servidor: ya existe una DEMO o acción en curso (HTTP 409).';
      case 422:
        return 'Los datos enviados no cumplen con el contrato OpenAPI del servidor (HTTP 422).';
      case 500:
      case 502:
      case 503:
        return `Error interno o de disponibilidad en el servidor AWS/RDS (HTTP ${status}).`;
      default:
        return `El servidor respondió con un código inesperado (HTTP ${status}).`;
    }
  }
}

export const httpClient = new HttpClient();

/*
 * ============================================================================
 * PROYECTO: Plataforma de Control Remoto IoT - Excavadora CAT-320
 * ARCHIVO:  Excavadora_ESP8266_WSS.ino
 * PLACA:    NodeMCU 1.0 (ESP-12E Module) / ESP8266 + 2x 74HC595
 * ============================================================================
 * AJUSTES NUEVOS INTEGRADOS (CONFIRMADOS CON DIAGRAMA FÍSICO):
 *
 * 1. NUEVA ASIGNACIÓN DE PINES 74HC595 (SEGÚN TU DIAGRAMA MANUSCRITO):
 *    - PRIMER 74HC595 (primerbyte):
 *        Q1 (1 << 1) -> Retroceder            (Código 11) [Joystick Izquierdo Abajo]
 *        Q2 (1 << 2) -> Avanzar               (Código 9)  [Joystick Izquierdo Arriba]
 *        Q3 (1 << 3) -> Girar torre Derecha   (Código 12) [Joystick Derecho Derecha]
 *        Q4 (1 << 4) -> Girar torre Izquierda (Código 10) [Joystick Derecho Izquierda]
 *
 *    - SEGUNDO 74HC595 (segundobyte):
 *        Q0 (1 << 0) -> Izquierda             (Código 2)  [Joystick Izquierdo Izquierda]
 *        Q1 (1 << 1) -> Subir brazo           (Código 1)  [Joystick Derecho Arriba]
 *        Q2 (1 << 2) -> Bajar brazo           (Código 3)  [Joystick Derecho Abajo]
 *        Q3 (1 << 3) -> Derecha               (Código 4)  [Joystick Izquierdo Derecha]
 *        Q4 (1 << 4) -> Sonido                (Código 6)  [Boton Toggle Sonido]
 *        Q5 (1 << 5) -> Demo                  (Código 7)  [Boton Funcion Demo]
 *        Q6 (1 << 6) -> Velocidad             (Código 5)  [Boton Funcion Velocidad]
 *        Q7 (1 << 7) -> Luz                   (Código 8)  [Boton Toggle Luz]
 *
 * 2. BOTÓN FÍSICO DE DETENER / PARO DE EMERGENCIA (PIN D2 / GPIO4):
 *    - Configurado con INPUT_PULLUP interno.
 *    - No requiere botón conectado para funcionar normalmente (se mantiene en HIGH).
 *    - Si se conecta un pulsador entre D2 y GND (o se puentea con un cable para la
 *      revisión del profesor), al detectar LOW ejecuta DETENER inmediato en los 595
 *      y notifica el evento al servidor WebSocket.
 *
 * 3. CONTROL TOGGLE DE LUZ Y SONIDO (PRENDER / APAGAR CON EL MISMO BOTÓN):
 *    - Cada pulsación conmuta su estado (Encendido <-> Apagado).
 *    - El estado de la Luz y del Sonido se conserva mientras mueves los Joysticks
 *      (los movimientos de orugas, brazo y torre no apagan la luz ni el sonido).
 *
 * 4. DETENCIÓN PRIORITARIA Y ACK OFICIAL:
 *    - Apaga motores de inmediato y envía "detencion_confirmada" a FastAPI en AWS.
 * ============================================================================
 */

#include <Arduino.h>
#include <ESP8266WiFi.h>
#include <DNSServer.h>
#include <ESP8266WebServer.h>
#include <WiFiManager.h>
#include <WebSocketsClient.h>
#include <ArduinoJson.h>

// ======================================================
// 1. CONFIGURACIÓN SERVIDOR AWS / DUCKDNS (WSS PUERTO 443)
// ======================================================
const char*    WS_HOST     = "vehiculo-iot-api.duckdns.org";
const uint16_t WS_PORT     = 443;
const char*    WS_PATH     = "/ws/vehiculos/1";
const int      VEHICULO_ID = 1;

// ======================================================
// 2. PINES HARDWARE (2x 74HC595 + BOTONES FÍSICOS)
// ======================================================
const int CLOCK_PIN       = D5; // GPIO14 - SH_CP (Reloj de desplazamiento)
const int LATCH_PIN       = D6; // GPIO12 - ST_CP (Almacenamiento / Latch)
const int DATA_PIN        = D7; // GPIO13 - DS    (Línea serial de datos)
const int RESET_WIFI_PIN  = D1; // GPIO5  - Pulsador a GND (3s) para borrar WiFi
const int STOP_BUTTON_PIN = D2; // GPIO4  - BOTÓN FÍSICO DETENER a GND (Pull-Up interno)

// ======================================================
// 3. TIEMPOS Y WATCHDOG DE SEGURIDAD
// ======================================================
const unsigned long DURACION_PULSO_AUX_MS        = 300;   // Pulso para Velocidad (#5) y Demo (#7)
const unsigned long WATCHDOG_MOVIMIENTO_MAX_MS   = 15000; // Paro automático de seguridad si no llega DETENER en 15s
const unsigned long INTERVALO_REINTENTO_WIFI_MS  = 5000;
const unsigned long INTERVALO_REINTENTO_WS_MS    = 3000;
const unsigned long INTERVALO_PING_MS            = 20000;
const unsigned long TIEMPO_BOTON_RESET_WIFI_MS   = 3000;
const unsigned long DEBOUNCE_STOP_MS             = 250;

// ======================================================
// 4. ESTADO GLOBAL DE SALIDAS Y DISPOSITIVO
// ======================================================
WebSocketsClient webSocket;

uint8_t primerbyte  = 0b00000000; // Primer 74HC595  (Retroceder, Avanzar, Torre Der, Torre Izq)
uint8_t segundobyte = 0b00000000; // Segundo 74HC595 (Izq, Brazo Arr, Brazo Abj, Der, Sonido, Demo, Vel, Luz)

// Estados toggle independientes para Luz y Sonido (Prende / Apaga con el mismo botón)
bool estadoLuz    = false; // Q7 del 2do 595
bool estadoSonido = false; // Q4 del 2do 595

bool wsConectado               = false;
bool pulsoAuxiliarActivo       = false;
bool pasoDemoTemporizadoActivo = false;
bool movimientoManualActivo    = false;

unsigned long tiempoInicioAccion = 0;
unsigned long duracionAccionMs   = 0;
unsigned long ultimoIntentoWiFi  = 0;
unsigned long ultimoPingMs       = 0;
unsigned long inicioBotonReset   = 0;
unsigned long ultimoDisparoStopFisico = 0;
bool botonResetPresionado        = false;

// Secuencia monótona (orden_id) para rechazar paquetes obsoletos posteriores a un DETENER
uint32_t ultimoOrdenIdAplicado = 0;
uint32_t ultimoStopOrdenId     = 0;

// ======================================================
// 5. PROTOTIPOS
// ======================================================
void configurarWiFi();
void verificarWiFi();
void verificarBotonFisicoDetener();
void verificarComandosSerialYReset();
void borrarCredencialesWiFi();
void configurarWebSocket();
void webSocketEvent(WStype_t type, uint8_t* payload, size_t length);
void enviarIdentificacionEsp8266();
void enviarPingJson();
void enviarConfirmacionDetener(uint32_t ordenId, const char* origen);
void procesarMensajeJson(const char* payload, size_t length);
void ejecutarPorCodigoONombre(int codigo, const String& nombre, unsigned long duracionPasoMs, bool esPasoDemo);
int  resolverCodigoDesdeNombre(String texto);
void aplicarMascara595(uint8_t pByte, uint8_t sByteMotores);
void detenerTodo(const char* motivo = "DETENER", bool conservarLuzYSonido = true);
void imprimirBinario(uint8_t valor);

// ======================================================
// 6. CONTROL FÍSICO DE LOS DOS REGISTROS 74HC595
// ======================================================
void imprimirBinario(uint8_t valor) {
  for (int i = 7; i >= 0; i--) {
    Serial.print(bitRead(valor, i));
  }
}

/**
 * Envía los bits en cascada a los dos 74HC595:
 *   - pByte va al PRIMER 74HC595
 *   - sByteMotores se combina con estadoLuz (Q7) y estadoSonido (Q4) para ir al SEGUNDO 74HC595.
 */
void aplicarMascara595(uint8_t pByte, uint8_t sByteMotores) {
  primerbyte = pByte;

  // Combinar los bits de movimiento/pulso con los estados persistentes de Luz y Sonido
  segundobyte = sByteMotores;
  if (estadoLuz) {
    segundobyte |= (1 << 7); // Q7 = Luz
  }
  if (estadoSonido) {
    segundobyte |= (1 << 4); // Q4 = Sonido
  }

  digitalWrite(LATCH_PIN, LOW);
  shiftOut(DATA_PIN, CLOCK_PIN, MSBFIRST, primerbyte);  // Entra primero y se desplaza al chip encadenado (Primer 74HC: Q1..Q4)
  shiftOut(DATA_PIN, CLOCK_PIN, MSBFIRST, segundobyte); // Se queda en el chip conectado directo al ESP8266 (Segundo 74HC: Q0..Q7)
  digitalWrite(LATCH_PIN, HIGH);

  Serial.print(F("[74HC595] 1er="));
  imprimirBinario(primerbyte);
  Serial.print(F(" | 2do="));
  imprimirBinario(segundobyte);
  Serial.printf(" [Luz=%s, Sonido=%s]\n", estadoLuz ? "ON" : "OFF", estadoSonido ? "ON" : "OFF");
}

/**
 * FUNCIÓN DETENER PRIORITARIA:
 * Apaga inmediatamente todos los motores de tracción, brazo y torre.
 * Por defecto conserva la luz y el sonido si ya estaban encendidos.
 */
void detenerTodo(const char* motivo, bool conservarLuzYSonido) {
  pulsoAuxiliarActivo       = false;
  pasoDemoTemporizadoActivo = false;
  movimientoManualActivo    = false;
  duracionAccionMs          = 0;

  if (!conservarLuzYSonido) {
    estadoLuz    = false;
    estadoSonido = false;
  }

  aplicarMascara595(0b00000000, 0b00000000);
  Serial.printf("[DETENER] Salidas en neutro (%s)\n", motivo);
}

// ======================================================
// 7. MAPEO EXACTO DE COMANDOS (SEGÚN TU NUEVO DIAGRAMA)
// ======================================================
int resolverCodigoDesdeNombre(String texto) {
  texto.trim();
  texto.toLowerCase();
  texto.replace("á", "a");
  texto.replace("é", "e");
  texto.replace("í", "i");
  texto.replace("ó", "o");
  texto.replace("ú", "u");

  if (texto == "detener" || texto == "stop" || texto == "paro" || texto == "detenido") return 0;
  if (texto == "mover brazo arriba" || texto == "brazo arriba" || texto == "subir brazo") return 1;
  if (texto == "girar izquierda" || texto == "izquierda")                              return 2;
  if (texto == "mover brazo abajo" || texto == "brazo abajo" || texto == "bajar brazo") return 3;
  if (texto == "girar derecha" || texto == "derecha")                                  return 4;
  if (texto == "velocidad")                                                            return 5;
  if (texto == "sonido" || texto == "claxon" || texto == "bocina")                     return 6;
  if (texto == "demo")                                                                 return 7;
  if (texto == "luz" || texto == "luces")                                              return 8;
  if (texto == "avanzar" || texto == "adelante")                                       return 9;
  if (texto == "girar torre izquierda" || texto == "torre izquierda")                  return 10;
  if (texto == "retroceder" || texto == "atras" || texto == "reversa")                 return 11;
  if (texto == "girar torre derecha" || texto == "torre derecha")                      return 12;
  return -1;
}

void ejecutarPorCodigoONombre(int codigo, const String& nombre, unsigned long duracionPasoMs, bool esPasoDemo) {
  int codEfectivo = codigo;
  if (codEfectivo < 0 || (codEfectivo == 0 && nombre.length() > 0 && resolverCodigoDesdeNombre(nombre) > 0)) {
    int inferido = resolverCodigoDesdeNombre(nombre);
    if (inferido >= 0) {
      codEfectivo = inferido;
    }
  }

  uint8_t pByte = 0b00000000;
  uint8_t sByte = 0b00000000;
  bool esAuxiliarPulso = false;

  switch (codEfectivo) {
    case 0: // DETENER
      detenerTodo("CODIGO_0_DETENER", true);
      return;

    // -------------------------------------------------------------
    // SEGUNDO 74HC595 (según tu hoja):
    // Q0 = Izquierda | Q1 = Subir brazo | Q2 = Bajar brazo | Q3 = Derecha
    // Q4 = Sonido    | Q5 = Demo        | Q6 = Velocidad   | Q7 = Luz
    // -------------------------------------------------------------
    case 1: // Subir brazo (Joystick Derecho Arriba) -> 2do 595 Q1
      Serial.println(F("[CMD #1] Subir brazo -> 2do 595 Q1"));
      sByte = (1 << 1);
      break;

    case 2: // Izquierda (Joystick Izquierdo Izquierda) -> 2do 595 Q0
      Serial.println(F("[CMD #2] Izquierda -> 2do 595 Q0"));
      sByte = (1 << 0);
      break;

    case 3: // Bajar brazo (Joystick Derecho Abajo) -> 2do 595 Q2
      Serial.println(F("[CMD #3] Bajar brazo -> 2do 595 Q2"));
      sByte = (1 << 2);
      break;

    case 4: // Derecha (Joystick Izquierdo Derecha) -> 2do 595 Q3
      Serial.println(F("[CMD #4] Derecha -> 2do 595 Q3"));
      sByte = (1 << 3);
      break;

    case 5: // Velocidad -> 2do 595 Q6 (pulso)
      Serial.println(F("[CMD #5] Velocidad -> 2do 595 Q6"));
      sByte = (1 << 6);
      esAuxiliarPulso = true;
      break;

    case 6: // Sonido -> 2do 595 Q4 (Toggle: prende / apaga con el mismo botón)
      estadoSonido = !estadoSonido;
      Serial.printf("[CMD #6] Sonido TOGGLE -> %s (2do 595 Q4)\n", estadoSonido ? "ENCENDIDO" : "APAGADO");
      aplicarMascara595(primerbyte, 0);
      return;

    case 7: // Demo -> 2do 595 Q5 (pulso)
      Serial.println(F("[CMD #7] Demo -> 2do 595 Q5"));
      sByte = (1 << 5);
      esAuxiliarPulso = true;
      break;

    case 8: // Luz -> 2do 595 Q7 (Toggle: prende / apaga con el mismo botón)
      estadoLuz = !estadoLuz;
      Serial.printf("[CMD #8] Luz TOGGLE -> %s (2do 595 Q7)\n", estadoLuz ? "ENCENDIDA" : "APAGADA");
      aplicarMascara595(primerbyte, 0);
      return;

    // -------------------------------------------------------------
    // PRIMER 74HC595 (según tu hoja):
    // Q1 = Retroceder | Q2 = Avanzar | Q3 = Girar torre Der | Q4 = Girar torre Izq
    // -------------------------------------------------------------
    case 9: // Avanzar (Joystick Izquierdo Arriba) -> 1er 595 Q2
      Serial.println(F("[CMD #9] Avanzar -> 1er 595 Q2"));
      pByte = (1 << 2);
      break;

    case 10: // Girar torre Izquierda (Joystick Derecho Izquierda) -> 1er 595 Q4
      Serial.println(F("[CMD #10] Girar torre Izquierda -> 1er 595 Q4"));
      pByte = (1 << 4);
      break;

    case 11: // Retroceder (Joystick Izquierdo Abajo) -> 1er 595 Q1
      Serial.println(F("[CMD #11] Retroceder -> 1er 595 Q1"));
      pByte = (1 << 1);
      break;

    case 12: // Girar torre Derecha (Joystick Derecho Derecha) -> 1er 595 Q3
      Serial.println(F("[CMD #12] Girar torre Derecha -> 1er 595 Q3"));
      pByte = (1 << 3);
      break;

    default:
      Serial.printf("[WARN] Código no reconocido (%d). Salidas en neutro.\n", codEfectivo);
      detenerTodo("CODIGO_DESCONOCIDO", true);
      return;
  }

  aplicarMascara595(pByte, sByte);
  tiempoInicioAccion = millis();

  if (esAuxiliarPulso) {
    pulsoAuxiliarActivo       = true;
    pasoDemoTemporizadoActivo = false;
    movimientoManualActivo    = false;
    duracionAccionMs          = (duracionPasoMs > 0) ? duracionPasoMs : DURACION_PULSO_AUX_MS;
  } else if (esPasoDemo && duracionPasoMs > 0) {
    pulsoAuxiliarActivo       = false;
    pasoDemoTemporizadoActivo = true;
    movimientoManualActivo    = false;
    duracionAccionMs          = duracionPasoMs;
  } else {
    // Movimiento manual por Joystick o botón: activo hasta recibir DETENER o vencer el watchdog de 15s
    pulsoAuxiliarActivo       = false;
    pasoDemoTemporizadoActivo = false;
    movimientoManualActivo    = true;
    duracionAccionMs          = (duracionPasoMs > 0) ? duracionPasoMs : WATCHDOG_MOVIMIENTO_MAX_MS;
  }
}

// ======================================================
// 8. MENSAJES Y PROTOCOLO WEBSOCKET (JSON)
// ======================================================
void enviarIdentificacionEsp8266() {
  if (!wsConectado) return;
  StaticJsonDocument<128> doc;
  doc["tipo"]        = "identificar";
  doc["rol"]         = "esp8266";
  doc["id_vehiculo"] = VEHICULO_ID;

  String msg;
  serializeJson(doc, msg);
  webSocket.sendTXT(msg);
  Serial.printf("[WSS -> IDENTIFICAR] %s\n", msg.c_str());
}

void enviarPingJson() {
  if (!wsConectado) return;
  StaticJsonDocument<128> doc;
  doc["tipo"]        = "ping";
  doc["rol"]         = "esp8266";
  doc["id_vehiculo"] = VEHICULO_ID;

  String msg;
  serializeJson(doc, msg);
  webSocket.sendTXT(msg);
}

void enviarConfirmacionDetener(uint32_t ordenId, const char* origen) {
  if (!wsConectado) return;
  StaticJsonDocument<256> ack;
  ack["tipo"]            = "detencion_confirmada";
  ack["id_vehiculo"]     = VEHICULO_ID;
  ack["orden_id"]        = (ordenId > 0) ? ordenId : 1;
  ack["codigo"]          = 0;
  ack["salidas_activas"] = false;
  ack["estado_fisico"]   = "detenido";
  ack["origen"]          = origen;

  String salida;
  serializeJson(ack, salida);
  webSocket.sendTXT(salida);
  Serial.printf("[WSS -> ACK DETENER] %s\n", salida.c_str());
}

void procesarMensajeJson(const char* payload, size_t length) {
  if (strcmp(payload, "pong") == 0) return;

  StaticJsonDocument<1024> doc;
  DeserializationError err = deserializeJson(doc, payload, length);
  if (err) {
    Serial.printf("[JSON] Error al parsear: %s\n", err.c_str());
    return;
  }

  const char* tipo   = doc["tipo"]    | "";
  const char* accion = doc["accion"]  | "";
  String nombreCmd   = doc["comando"] | "";
  int codigo         = doc["codigo"].isNull() ? -1 : doc["codigo"].as<int>();
  uint32_t ordenId   = doc["orden_id"] | 0;

  if (strcmp(tipo, "pong") == 0 || strcmp(tipo, "identificado") == 0 ||
      strcmp(tipo, "detencion_confirmada") == 0 || strcmp(tipo, "ack_rechazado") == 0) {
    return;
  }

  // 1. PRIORIDAD MÁXIMA: DETENER (Botón rojo, joystick en neutro, timeout o paro)
  if (strcmp(tipo, "detener") == 0 || strcmp(accion, "DETENER") == 0 ||
      codigo == 0 || resolverCodigoDesdeNombre(nombreCmd) == 0) {
    if (ordenId > ultimoStopOrdenId) {
      ultimoStopOrdenId = ordenId;
    }
    if (ordenId > ultimoOrdenIdAplicado) {
      ultimoOrdenIdAplicado = ordenId;
    }
    detenerTodo("EVENTO_WSS_DETENER", true);
    if (ordenId > 0) {
      enviarConfirmacionDetener(ordenId, "WSS");
    }
    return;
  }

  // 2. BARRERA DE SECUENCIA MONÓTONA: descarta órdenes obsoletas <= ultimoStopOrdenId
  if (ordenId > 0) {
    if (ordenId <= ultimoStopOrdenId || ordenId <= ultimoOrdenIdAplicado) {
      Serial.printf("[SECUENCIA] Ignorando orden obsoleta orden_id=%u (último=%u, stop=%u)\n",
                    ordenId, ultimoOrdenIdAplicado, ultimoStopOrdenId);
      return;
    }
    ultimoOrdenIdAplicado = ordenId;
  }

  // 3. Eventos DEMO (inicio, paso, fin, cancelación)
  if (strcmp(tipo, "demo_inicio") == 0) {
    detenerTodo("INICIO_DEMO", true);
    Serial.printf("[DEMO] Iniciando secuencia '%s'\n", (const char*)(doc["nombre"] | doc["nombre_demo"] | ""));
    return;
  }

  if (strcmp(tipo, "demo_fin") == 0 || strcmp(tipo, "demo_cancelada") == 0) {
    detenerTodo(tipo, true);
    return;
  }

  if (strcmp(tipo, "demo_paso") == 0) {
    unsigned long duracionMs = doc["duracion_ms"] | 1000UL;
    ejecutarPorCodigoONombre(codigo, nombreCmd, duracionMs, true);
    return;
  }

  // 4. Movimiento individual (Joysticks o Funciones)
  if (strcmp(tipo, "movimiento_individual") == 0) {
    unsigned long duracionMs = doc["duracion_ms"] | 0UL;
    ejecutarPorCodigoONombre(codigo, nombreCmd, duracionMs, false);
    return;
  }

  // 5. Sincronización de estado inicial
  if (strcmp(tipo, "estado_inicial") == 0 || strcmp(tipo, "estado_actualizado") == 0) {
    if (codigo == 0 || resolverCodigoDesdeNombre(nombreCmd) == 0) {
      detenerTodo(tipo, true);
    }
    return;
  }
}

// ======================================================
// 9. BOTÓN FÍSICO DETENER (D2) Y GESTIÓN DE WIFI
// ======================================================

/**
 * REQUERIMIENTO DEL PROFESOR: BOTÓN FÍSICO DE DETENER
 * Pin D2 (GPIO4) con INPUT_PULLUP. Al presionarse (GND) dispara el paro en seco
 * de ambos 74HC595 y transmite la confirmación inmediata al servidor.
 */
void verificarBotonFisicoDetener() {
  if (digitalRead(STOP_BUTTON_PIN) == LOW) {
    unsigned long ahora = millis();
    if (ahora - ultimoDisparoStopFisico >= DEBOUNCE_STOP_MS) {
      ultimoDisparoStopFisico = ahora;
      Serial.println(F("\n[BOTON FISICO D2] !!! PARO DE EMERGENCIA DETECTADO !!!"));
      detenerTodo("BOTON_FISICO_D2", true);
      enviarConfirmacionDetener(ultimoStopOrdenId > 0 ? ultimoStopOrdenId : 1, "BOTON_FISICO_D2");
    }
  }
}

void configurarWiFi() {
  WiFiManager wm;
  wm.setDebugOutput(true);
  wm.setConfigPortalTimeout(180);
  wm.setConnectTimeout(20);

  Serial.println(F("[WIFI] Conectando a la red WiFi guardada..."));
  bool conectado = wm.autoConnect("Excavadora-IoT-Config", "12345678");

  if (!conectado) {
    Serial.println(F("[WIFI] Tiempo agotado en portal. Reiniciando..."));
    delay(2000);
    ESP.restart();
  }

  Serial.print(F("[WIFI] Conectado a SSID: "));
  Serial.println(WiFi.SSID());
  Serial.print(F("[WIFI] IP local: "));
  Serial.println(WiFi.localIP());
}

void verificarWiFi() {
  if (WiFi.status() == WL_CONNECTED) {
    return;
  }
  if (millis() - ultimoIntentoWiFi >= INTERVALO_REINTENTO_WIFI_MS) {
    ultimoIntentoWiFi = millis();
    Serial.println(F("[WIFI] Pérdida de señal -> Paro seguro de motores y reconectando..."));
    detenerTodo("PERDIDA_WIFI", true);
    WiFi.reconnect();
  }
}

void verificarComandosSerialYReset() {
  if (Serial.available() > 0) {
    char c = Serial.read();
    if (c == 'R' || c == 'r') {
      borrarCredencialesWiFi();
    } else if (c == '0' || c == 'S' || c == 's') {
      detenerTodo("PARO_SERIAL", true);
    }
  }

  if (digitalRead(RESET_WIFI_PIN) == LOW) {
    if (!botonResetPresionado) {
      botonResetPresionado = true;
      inicioBotonReset = millis();
    } else if (millis() - inicioBotonReset >= TIEMPO_BOTON_RESET_WIFI_MS) {
      borrarCredencialesWiFi();
    }
  } else {
    botonResetPresionado = false;
  }
}

void borrarCredencialesWiFi() {
  Serial.println(F("[WIFI] Borrando credenciales WiFi..."));
  detenerTodo("RESET_WIFI", false);

  WiFiManager wm;
  wm.resetSettings();
  WiFi.disconnect(true);
  delay(1000);

  Serial.println(F("[WIFI] Reiniciando en portal Excavadora-IoT-Config..."));
  ESP.restart();
}

// ======================================================
// 10. CONEXIÓN WEBSOCKET SEGURA WSS (PUERTO 443)
// ======================================================
void configurarWebSocket() {
  Serial.printf("[WSS] Conectando a wss://%s:%u%s\n", WS_HOST, WS_PORT, WS_PATH);
  webSocket.beginSSL(WS_HOST, WS_PORT, WS_PATH);
  webSocket.onEvent(webSocketEvent);
  webSocket.setReconnectInterval(INTERVALO_REINTENTO_WS_MS);
  webSocket.enableHeartbeat(15000, 3000, 2);
}

void webSocketEvent(WStype_t type, uint8_t* payload, size_t length) {
  switch (type) {
    case WStype_DISCONNECTED:
      wsConectado = false;
      detenerTodo("DESCONEXION_WSS", true);
      break;

    case WStype_CONNECTED:
      wsConectado = true;
      detenerTodo("CONEXION_WSS_ESTABLECIDA", true);
      Serial.printf("[WSS] Conectado exitosamente a %s\n", (char*)payload);
      enviarIdentificacionEsp8266();
      break;

    case WStype_TEXT:
      Serial.printf("[WSS <-] %s\n", (char*)payload);
      procesarMensajeJson((const char*)payload, length);
      break;

    case WStype_ERROR:
      Serial.println(F("[WSS] Error en el canal seguro."));
      break;

    default:
      break;
  }
}

// ======================================================
// 11. SETUP Y LOOP PRINCIPAL
// ======================================================
void setup() {
  pinMode(CLOCK_PIN, OUTPUT);
  pinMode(LATCH_PIN, OUTPUT);
  pinMode(DATA_PIN,  OUTPUT);
  pinMode(RESET_WIFI_PIN,  INPUT_PULLUP);
  pinMode(STOP_BUTTON_PIN, INPUT_PULLUP); // Botón físico de DETENER con pull-up interno

  // Salidas en reposo seguro desde el primer milisegundo
  detenerTodo("ARRANQUE", false);

  Serial.begin(115200);
  delay(200);
  Serial.println();
  Serial.println(F("=========================================================="));
  Serial.println(F(" EXCAVADORA IoT CAT-320 | NUEVA MATRIZ 74HC595 + PARO D2 "));
  Serial.println(F("=========================================================="));

  WiFi.mode(WIFI_STA);
  WiFi.persistent(true);
  WiFi.setAutoConnect(true);
  WiFi.setAutoReconnect(true);

  configurarWiFi();
  configurarWebSocket();
}

void loop() {
  verificarBotonFisicoDetener(); // Monitorea el botón físico de DETENER en pin D2
  verificarComandosSerialYReset();
  verificarWiFi();

  if (WiFi.status() == WL_CONNECTED) {
    webSocket.loop();
  }

  unsigned long ahora = millis();

  // Apagar pulso auxiliar corto (Velocidad o Demo)
  if (pulsoAuxiliarActivo && duracionAccionMs > 0 && (ahora - tiempoInicioAccion >= duracionAccionMs)) {
    detenerTodo("FIN_PULSO_AUXILIAR", true);
  }

  // Apagar paso individual de una secuencia DEMO temporizada
  if (pasoDemoTemporizadoActivo && duracionAccionMs > 0 && (ahora - tiempoInicioAccion >= duracionAccionMs)) {
    detenerTodo("FIN_PASO_DEMO", true);
  }

  // Watchdog de seguridad (15s máximo si por fallo de red se pierde la orden DETENER)
  if (movimientoManualActivo && duracionAccionMs > 0 && (ahora - tiempoInicioAccion >= duracionAccionMs)) {
    detenerTodo("WATCHDOG_15S_SEGURIDAD", true);
  }

  // Heartbeat JSON periódico al servidor cada 20s
  if (wsConectado && (ahora - ultimoPingMs >= INTERVALO_PING_MS)) {
    ultimoPingMs = ahora;
    enviarPingJson();
  }
}

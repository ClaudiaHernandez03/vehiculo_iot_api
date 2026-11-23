/*
 * ============================================================================
 * PROYECTO: Plataforma de Control Remoto IoT - Excavadora CAT-320
 *           (Prácticas 4.1 Control, 4.2 Monitoreo y 4.3 Administración)
 * ARCHIVO:  Excavadora_ESP8266_WSS.ino
 * PLACA:    NodeMCU 1.0 (ESP-12E Module) / ESP8266 + 2x 74HC595
 * ============================================================================
 * FUNCIONES INTEGRADAS CON AWS EC2 + AMAZON RDS + GITHUB PAGES:
 *   1. Portal WiFiManager ("Excavadora-IoT-Config", clave "12345678") con
 *      reconexion automatica y borrado de red con 'R' en Serial o boton en D1.
 *   2. Conexion WebSocket Segura (WSS puerto 443) hacia:
 *      wss://vehiculo-iot-api.duckdns.org/ws/vehiculos/1
 *   3. Identificacion automatica al conectar:
 *      {"tipo":"identificar","rol":"esp8266","id_vehiculo":1}
 *   4. Heartbeat JSON valido cada 20s:
 *      {"tipo":"ping","rol":"esp8266","id_vehiculo":1}
 *   5. Funcion DETENER prioritaria (Codigo 0 / boton DETENER / soltar Joystick /
 *      fin de sesion de Invitado 60s / cancelar DEMO):
 *      - Apaga de inmediato todas las salidas de ambos 74HC595 (0b00000000).
 *      - Bloquea ordenes rezagadas por secuencia monotona (orden_id).
 *      - Responde ACK oficial al servidor FastAPI:
 *        {"tipo":"detencion_confirmada","id_vehiculo":1,"orden_id":...,
 *         "codigo":0,"salidas_activas":false,"estado_fisico":"detenido"}
 *   6. Control de los 2 Joysticks + 4 Funciones + Secuencias DEMO (1 a 4 pasos):
 *      - CLOCK = D5 (GPIO14) | LATCH = D6 (GPIO12) | DATA = D7 (GPIO13)
 *
 *      Primer 74HC595 (primerbyte):
 *        Q1 (bit 1) -> Avanzar               (codigo 9)  [Joystick Izquierdo Arriba]
 *        Q2 (bit 2) -> Girar torre izquierda (codigo 10) [Joystick Derecho Izquierda]
 *        Q3 (bit 3) -> Retroceder            (codigo 11) [Joystick Izquierdo Abajo]
 *        Q4 (bit 4) -> Girar torre derecha   (codigo 12) [Joystick Derecho Derecha]
 *
 *      Segundo 74HC595 (segundobyte):
 *        Q0 (bit 0) -> Mover brazo arriba    (codigo 1)  [Joystick Derecho Arriba]
 *        Q1 (bit 1) -> Girar izquierda       (codigo 2)  [Joystick Izquierdo Izquierda]
 *        Q2 (bit 2) -> Mover brazo abajo     (codigo 3)  [Joystick Derecho Abajo]
 *        Q3 (bit 3) -> Girar derecha         (codigo 4)  [Joystick Izquierdo Derecha]
 *        Q4 (bit 4) -> Velocidad             (codigo 5)  [Boton Funcion Velocidad]
 *        Q5 (bit 5) -> Sonido                (codigo 6)  [Boton Funcion Sonido]
 *        Q6 (bit 6) -> Demo                  (codigo 7)  [Boton Funcion Demo]
 *        Q7 (bit 7) -> Luz                   (codigo 8)  [Boton Funcion Luz]
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
// 1. CONFIGURACION SERVIDOR AWS / DUCKDNS (WSS)
// ======================================================
const char*    WS_HOST     = "vehiculo-iot-api.duckdns.org";
const uint16_t WS_PORT     = 443;
const char*    WS_PATH     = "/ws/vehiculos/1";
const int      VEHICULO_ID = 1;

// ======================================================
// 2. PINES HARDWARE (2x 74HC595 + BOTON RESET WIFI)
// ======================================================
const int CLOCK_PIN      = D5; // GPIO14 - SH_CP (Reloj)
const int LATCH_PIN      = D6; // GPIO12 - ST_CP (Latch)
const int DATA_PIN       = D7; // GPIO13 - DS    (Datos)
const int RESET_WIFI_PIN = D1; // GPIO5  - Boton a GND por 3s para borrar WiFi

// ======================================================
// 3. TIEMPOS DE OPERACION Y SEGURIDAD (WATCHDOG)
// ======================================================
const unsigned long DURACION_PULSO_AUX_MS        = 300;   // Pulso para Velocidad/Sonido/Demo/Luz (5..8)
const unsigned long WATCHDOG_MOVIMIENTO_MAX_MS   = 15000; // Paro automatico si en 15s no llega DETENER
const unsigned long INTERVALO_REINTENTO_WIFI_MS  = 5000;
const unsigned long INTERVALO_REINTENTO_WS_MS    = 3000;
const unsigned long INTERVALO_PING_MS            = 20000;
const unsigned long TIEMPO_BOTON_RESET_WIFI_MS   = 3000;

// ======================================================
// 4. VARIABLES GLOBALES DE ESTADO
// ======================================================
WebSocketsClient webSocket;

uint8_t primerbyte  = 0b00000000; // Primer 74HC595  (Q1..Q4)
uint8_t segundobyte = 0b00000000; // Segundo 74HC595 (Q0..Q7)

bool wsConectado               = false;
bool pulsoAuxiliarActivo       = false;
bool pasoDemoTemporizadoActivo = false;
bool movimientoManualActivo    = false;

unsigned long tiempoInicioAccion = 0;
unsigned long duracionAccionMs   = 0;
unsigned long ultimoIntentoWiFi  = 0;
unsigned long ultimoPingMs       = 0;
unsigned long inicioBotonReset   = 0;
bool botonResetPresionado        = false;

// Control de secuencia monotona (orden_id) para que ningun paquete atrasado reactive motores tras DETENER
uint32_t ultimoOrdenIdAplicado = 0;
uint32_t ultimoStopOrdenId     = 0;

// ======================================================
// 5. PROTOTIPOS
// ======================================================
void configurarWiFi();
void verificarWiFi();
void verificarComandosSerialYReset();
void borrarCredencialesWiFi();
void configurarWebSocket();
void webSocketEvent(WStype_t type, uint8_t* payload, size_t length);
void enviarIdentificacionEsp8266();
void enviarPingJson();
void enviarConfirmacionDetener(uint32_t ordenId);
void procesarMensajeJson(const char* payload, size_t length);
void ejecutarPorCodigoONombre(int codigo, const String& nombre, unsigned long duracionPasoMs, bool esPasoDemo);
int  resolverCodigoDesdeNombre(String texto);
void aplicarMascara595(uint8_t pByte, uint8_t sByte);
void detenerTodo(const char* motivo = "DETENER");
void imprimirBinario(uint8_t valor);

// ======================================================
// 6. CONTROL FISICO DE LOS DOS 74HC595 Y FUNCION DETENER
// ======================================================
void imprimirBinario(uint8_t valor) {
  for (int i = 7; i >= 0; i--) {
    Serial.print(bitRead(valor, i));
  }
}

void aplicarMascara595(uint8_t pByte, uint8_t sByte) {
  primerbyte  = pByte;
  segundobyte = sByte;

  digitalWrite(LATCH_PIN, LOW);
  shiftOut(DATA_PIN, CLOCK_PIN, MSBFIRST, segundobyte);
  shiftOut(DATA_PIN, CLOCK_PIN, MSBFIRST, primerbyte);
  digitalWrite(LATCH_PIN, HIGH);

  Serial.print(F("[74HC595] 1er="));
  imprimirBinario(primerbyte);
  Serial.print(F(" | 2do="));
  imprimirBinario(segundobyte);
  Serial.println();
}

/**
 * FUNCION DETENER PRIORITARIA:
 * Pone inmediatamente todas las salidas de ambos 74HC595 en 0 (motores, brazo, torre y funciones apagados)
 * y limpia cualquier temporizador de movimiento manual, pulso o paso de DEMO.
 */
void detenerTodo(const char* motivo) {
  pulsoAuxiliarActivo       = false;
  pasoDemoTemporizadoActivo = false;
  movimientoManualActivo    = false;
  duracionAccionMs          = 0;
  aplicarMascara595(0b00000000, 0b00000000);
  Serial.printf("[DETENER] Vehiculo detenido (%s) -> Todas las salidas en 0\n", motivo);
}

// ======================================================
// 7. MAPEO DE LOS 12 COMANDOS + DETENER (CODIGOS 0..12)
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
  if (texto == "mover brazo arriba" || texto == "brazo arriba")                        return 1;
  if (texto == "girar izquierda")                                                      return 2;
  if (texto == "mover brazo abajo" || texto == "brazo abajo")                          return 3;
  if (texto == "girar derecha")                                                        return 4;
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
      detenerTodo("CODIGO_0_DETENER");
      return;

    // --- Segundo 74HC595 (Q0..Q7) ---
    case 1: // Mover brazo arriba (Joystick Derecho Arriba) -> 2do 595 Q0
      Serial.println(F("[CMD #1] Mover brazo arriba (2do 595 Q0)"));
      sByte = (1 << 0);
      break;

    case 2: // Girar izquierda (Joystick Izquierdo Izquierda) -> 2do 595 Q1
      Serial.println(F("[CMD #2] Girar izquierda (2do 595 Q1)"));
      sByte = (1 << 1);
      break;

    case 3: // Mover brazo abajo (Joystick Derecho Abajo) -> 2do 595 Q2
      Serial.println(F("[CMD #3] Mover brazo abajo (2do 595 Q2)"));
      sByte = (1 << 2);
      break;

    case 4: // Girar derecha (Joystick Izquierdo Derecha) -> 2do 595 Q3
      Serial.println(F("[CMD #4] Girar derecha (2do 595 Q3)"));
      sByte = (1 << 3);
      break;

    case 5: // Velocidad -> 2do 595 Q4 (pulso)
      Serial.println(F("[CMD #5] Velocidad (2do 595 Q4)"));
      sByte = (1 << 4);
      esAuxiliarPulso = true;
      break;

    case 6: // Sonido -> 2do 595 Q5 (pulso)
      Serial.println(F("[CMD #6] Sonido (2do 595 Q5)"));
      sByte = (1 << 5);
      esAuxiliarPulso = true;
      break;

    case 7: // Demo -> 2do 595 Q6 (pulso)
      Serial.println(F("[CMD #7] Demo (2do 595 Q6)"));
      sByte = (1 << 6);
      esAuxiliarPulso = true;
      break;

    case 8: // Luz -> 2do 595 Q7 (pulso)
      Serial.println(F("[CMD #8] Luz (2do 595 Q7)"));
      sByte = (1 << 7);
      esAuxiliarPulso = true;
      break;

    // --- Primer 74HC595 (Q1..Q4) ---
    case 9: // Avanzar (Joystick Izquierdo Arriba) -> 1er 595 Q1
      Serial.println(F("[CMD #9] Avanzar (1er 595 Q1)"));
      pByte = (1 << 1);
      break;

    case 10: // Girar torre izquierda (Joystick Derecho Izquierda) -> 1er 595 Q2
      Serial.println(F("[CMD #10] Girar torre izquierda (1er 595 Q2)"));
      pByte = (1 << 2);
      break;

    case 11: // Retroceder (Joystick Izquierdo Abajo) -> 1er 595 Q3
      Serial.println(F("[CMD #11] Retroceder (1er 595 Q3)"));
      pByte = (1 << 3);
      break;

    case 12: // Girar torre derecha (Joystick Derecho Derecha) -> 1er 595 Q4
      Serial.println(F("[CMD #12] Girar torre derecha (1er 595 Q4)"));
      pByte = (1 << 4);
      break;

    default:
      Serial.printf("[WARN] Codigo no reconocido (%d). Se detiene por seguridad.\n", codEfectivo);
      detenerTodo("CODIGO_DESCONOCIDO");
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
    // Movimiento manual desde Joystick o boton: permanece activo hasta recibir DETENER
    // (o hasta que se cumpla el watchdog de seguridad de 15 segundos)
    pulsoAuxiliarActivo       = false;
    pasoDemoTemporizadoActivo = false;
    movimientoManualActivo    = true;
    duracionAccionMs          = (duracionPasoMs > 0) ? duracionPasoMs : WATCHDOG_MOVIMIENTO_MAX_MS;
  }
}

// ======================================================
// 8. MENSAJES SALIENTES Y ENTRANTES WEBSOCKET (JSON)
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

/**
 * Envia el ACK oficial "detencion_confirmada" con los campos exactos que valida
 * FastAPI en websocket_manager.py (_handle_detencion_confirmada):
 *   id_vehiculo, orden_id, codigo=0, salidas_activas=false, estado_fisico="detenido"
 */
void enviarConfirmacionDetener(uint32_t ordenId) {
  if (!wsConectado) return;
  StaticJsonDocument<256> ack;
  ack["tipo"]            = "detencion_confirmada";
  ack["id_vehiculo"]     = VEHICULO_ID;
  ack["orden_id"]        = ordenId;
  ack["codigo"]          = 0;
  ack["salidas_activas"] = (primerbyte != 0 || segundobyte != 0); // false cuando esta detenido
  ack["estado_fisico"]   = "detenido";

  String salida;
  serializeJson(ack, salida);
  webSocket.sendTXT(salida);
  Serial.printf("[WSS -> ACK DETENER] %s\n", salida.c_str());
}

void procesarMensajeJson(const char* payload, size_t length) {
  if (strcmp(payload, "pong") == 0) {
    return;
  }

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

  // Ignorar respuestas internas de control del socket
  if (strcmp(tipo, "pong") == 0 || strcmp(tipo, "identificado") == 0 ||
      strcmp(tipo, "detencion_confirmada") == 0 || strcmp(tipo, "ack_rechazado") == 0) {
    return;
  }

  // 1. PRIORIDAD MAXIMA: orden explicita de DETENER (boton rojo, soltar joystick, fin sesion 60s o paro de emergencia)
  if (strcmp(tipo, "detener") == 0 || strcmp(accion, "DETENER") == 0 ||
      codigo == 0 || resolverCodigoDesdeNombre(nombreCmd) == 0) {
    if (ordenId > ultimoStopOrdenId) {
      ultimoStopOrdenId = ordenId;
    }
    if (ordenId > ultimoOrdenIdAplicado) {
      ultimoOrdenIdAplicado = ordenId;
    }
    detenerTodo("EVENTO_WSS_DETENER");
    if (ordenId > 0) {
      enviarConfirmacionDetener(ordenId);
    }
    return;
  }

  // 2. BARRERA DE SECUENCIA: descartar paquetes antiguos con orden_id <= ultimoStopOrdenId
  if (ordenId > 0) {
    if (ordenId <= ultimoStopOrdenId || ordenId <= ultimoOrdenIdAplicado) {
      Serial.printf("[SECUENCIA] Ignorando orden antigua orden_id=%u (ultimo=%u, stop=%u)\n",
                    ordenId, ultimoOrdenIdAplicado, ultimoStopOrdenId);
      return;
    }
    ultimoOrdenIdAplicado = ordenId;
  }

  // 3. Eventos de secuencias DEMO (inicio, paso temporizado, fin y cancelacion)
  if (strcmp(tipo, "demo_inicio") == 0) {
    detenerTodo("INICIO_DEMO");
    Serial.printf("[DEMO] Iniciando DEMO '%s'\n", (const char*)(doc["nombre"] | doc["nombre_demo"] | ""));
    return;
  }

  if (strcmp(tipo, "demo_fin") == 0 || strcmp(tipo, "demo_cancelada") == 0) {
    detenerTodo(tipo);
    return;
  }

  if (strcmp(tipo, "demo_paso") == 0) {
    unsigned long duracionMs = doc["duracion_ms"] | 1000UL;
    ejecutarPorCodigoONombre(codigo, nombreCmd, duracionMs, true);
    return;
  }

  // 4. Movimiento individual (Joystick Izquierdo, Joystick Derecho o Botones de Funcion)
  if (strcmp(tipo, "movimiento_individual") == 0) {
    unsigned long duracionMs = doc["duracion_ms"] | 0UL;
    ejecutarPorCodigoONombre(codigo, nombreCmd, duracionMs, false);
    return;
  }

  // 5. Estado inicial al conectar o estado actualizado
  if (strcmp(tipo, "estado_inicial") == 0 || strcmp(tipo, "estado_actualizado") == 0) {
    if (codigo == 0 || resolverCodigoDesdeNombre(nombreCmd) == 0) {
      detenerTodo(tipo);
    }
    return;
  }
}

// ======================================================
// 9. WIFI MANAGER Y COMANDOS POR MONITOR SERIAL
// ======================================================
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
    Serial.println(F("[WIFI] Senal perdida -> Deteniendo salidas y reconectando..."));
    detenerTodo("PERDIDA_WIFI");
    WiFi.reconnect();
  }
}

void verificarComandosSerialYReset() {
  if (Serial.available() > 0) {
    char c = Serial.read();
    if (c == 'R' || c == 'r') {
      borrarCredencialesWiFi();
    } else if (c == '0' || c == 'S' || c == 's') {
      detenerTodo("PARO_MONITOR_SERIAL");
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
  Serial.println(F("[WIFI] Borrando credenciales WiFi guardadas..."));
  detenerTodo("RESET_WIFI");

  WiFiManager wm;
  wm.resetSettings();
  WiFi.disconnect(true);
  delay(1000);

  Serial.println(F("[WIFI] Reiniciando en portal Excavadora-IoT-Config..."));
  ESP.restart();
}

// ======================================================
// 10. CONFIGURACION WEBSOCKET SEGURO (WSS PUERTO 443)
// ======================================================
void configurarWebSocket() {
  Serial.printf("[WSS] Conectando a wss://%s:%u%s\n", WS_HOST, WS_PORT, WS_PATH);

  // Conexion SSL/TLS hacia Caddy + FastAPI en AWS (vehiculo-iot-api.duckdns.org:443)
  webSocket.beginSSL(WS_HOST, WS_PORT, WS_PATH);
  webSocket.onEvent(webSocketEvent);
  webSocket.setReconnectInterval(INTERVALO_REINTENTO_WS_MS);
  webSocket.enableHeartbeat(15000, 3000, 2);
}

void webSocketEvent(WStype_t type, uint8_t* payload, size_t length) {
  switch (type) {
    case WStype_DISCONNECTED:
      wsConectado = false;
      detenerTodo("DESCONEXION_WSS");
      break;

    case WStype_CONNECTED:
      wsConectado = true;
      detenerTodo("CONEXION_WSS_SEGURA");
      Serial.printf("[WSS] Conectado a %s\n", (char*)payload);
      enviarIdentificacionEsp8266();
      break;

    case WStype_TEXT:
      Serial.printf("[WSS <-] %s\n", (char*)payload);
      procesarMensajeJson((const char*)payload, length);
      break;

    case WStype_ERROR:
      Serial.println(F("[WSS] Error en el canal WebSocket."));
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
  pinMode(RESET_WIFI_PIN, INPUT_PULLUP);

  // Apagar todas las salidas fisicas desde el primer milisegundo de encendido
  detenerTodo("ARRANQUE_INICIAL");

  Serial.begin(115200);
  delay(200);
  Serial.println();
  Serial.println(F("=========================================================="));
  Serial.println(F(" EXCAVADORA IoT CAT-320 - ESP8266 WSS + DETENER + 74HC595 "));
  Serial.println(F("=========================================================="));

  WiFi.mode(WIFI_STA);
  WiFi.persistent(true);
  WiFi.setAutoConnect(true);
  WiFi.setAutoReconnect(true);

  configurarWiFi();
  configurarWebSocket();
}

void loop() {
  verificarComandosSerialYReset();
  verificarWiFi();

  if (WiFi.status() == WL_CONNECTED) {
    webSocket.loop();
  }

  unsigned long ahora = millis();

  // 1. Apagar pulso auxiliar corto (Velocidad #5, Sonido #6, Demo #7, Luz #8)
  if (pulsoAuxiliarActivo && duracionAccionMs > 0 && (ahora - tiempoInicioAccion >= duracionAccionMs)) {
    detenerTodo("FIN_PULSO_AUXILIAR");
  }

  // 2. Apagar paso individual de una secuencia DEMO al cumplirse su duracion_ms
  if (pasoDemoTemporizadoActivo && duracionAccionMs > 0 && (ahora - tiempoInicioAccion >= duracionAccionMs)) {
    detenerTodo("FIN_PASO_DEMO");
  }

  // 3. Watchdog de seguridad para movimientos manuales (15s maximo si no llega DETENER)
  if (movimientoManualActivo && duracionAccionMs > 0 && (ahora - tiempoInicioAccion >= duracionAccionMs)) {
    detenerTodo("WATCHDOG_SEGURIDAD_15S");
  }

  // 4. Heartbeat JSON hacia FastAPI cada 20 segundos
  if (wsConectado && (ahora - ultimoPingMs >= INTERVALO_PING_MS)) {
    ultimoPingMs = ahora;
    enviarPingJson();
  }
}

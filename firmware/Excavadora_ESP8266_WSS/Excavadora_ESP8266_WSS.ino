/*
 * ============================================================================
 * PROYECTO: Vehiculo IoT - Excavadora CAT-320 (ESP8266 + 2x 74HC595 + WSS)
 * ARCHIVO:  Excavadora_ESP8266_WSS.ino
 * ============================================================================
 * CARACTERISTICAS RECUPERADAS Y VERIFICADAS:
 *   1. Configuracion WiFi con WiFiManager (portal cautivo "Excavadora-IoT-Config").
 *   2. Conexion automatica a la ultima red guardada y persistencia en flash.
 *   3. Reconexion automatica si pierde la senal WiFi.
 *   4. Reinicio de credenciales WiFi por monitor serial ('R'/'r') o boton fisico
 *      en D1 (GPIO5) presionado a GND durante 3 segundos.
 *   5. Conexion WebSocket segura (WSS) en puerto 443 hacia:
 *      wss://vehiculo-iot-api.duckdns.org/ws/vehiculos/1
 *   6. Lectura de mensajes JSON con ArduinoJson:
 *      - Eventos soportados: movimiento_individual, demo_inicio, demo_paso,
 *        demo_fin, demo_cancelada, detener, estado_inicial, estado_actualizado.
 *      - Resolucion tanto por codigo numerico (0..12) como por nombre de comando.
 *   7. Proteccion de secuencia monotona (orden_id uint32_t) y envio de ACK
 *      "detencion_confirmada" al servidor tras ejecutar DETENER.
 *   8. Mapeo fisico confirmado de los dos registros de desplazamiento 74HC595:
 *      - CLOCK = D5 (GPIO14)
 *      - LATCH = D6 (GPIO12)
 *      - DATA  = D7 (GPIO13)
 *
 *      Primer 74HC595 (primerbyte):
 *        Q1 (bit 1) -> Avanzar               (codigo 9)
 *        Q2 (bit 2) -> Girar torre izquierda (codigo 10)
 *        Q3 (bit 3) -> Retroceder            (codigo 11)
 *        Q4 (bit 4) -> Girar torre derecha   (codigo 12)
 *
 *      Segundo 74HC595 (segundobyte):
 *        Q0 (bit 0) -> Mover brazo arriba    (codigo 1)
 *        Q1 (bit 1) -> Girar izquierda       (codigo 2)
 *        Q2 (bit 2) -> Mover brazo abajo     (codigo 3)
 *        Q3 (bit 3) -> Girar derecha         (codigo 4)
 *        Q4 (bit 4) -> Velocidad             (codigo 5)
 *        Q5 (bit 5) -> Sonido                (codigo 6)
 *        Q6 (bit 6) -> Demo                  (codigo 7)
 *        Q7 (bit 7) -> Luz                   (codigo 8)
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
const char*    WS_HOST       = "vehiculo-iot-api.duckdns.org";
const uint16_t WS_PORT       = 443;
const char*    WS_PATH       = "/ws/vehiculos/1";
const int      VEHICULO_ID   = 1;

// ======================================================
// 2. PINES HARDWARE CONFIRMADOS (2x 74HC595 + RESET WIFI)
// ======================================================
const int CLOCK_PIN      = D5; // GPIO14 - SH_CP
const int LATCH_PIN      = D6; // GPIO12 - ST_CP
const int DATA_PIN       = D7; // GPIO13 - DS
const int RESET_WIFI_PIN = D1; // GPIO5  - Boton fisico a GND (3 s) para borrar WiFi

// ======================================================
// 3. TIEMPOS DE CONTROL Y RECONEXION
// ======================================================
const unsigned long DURACION_PULSO_AUX_MS       = 300;   // Pulso para Velocidad/Sonido/Demo/Luz (codigos 5..8)
const unsigned long INTERVALO_REINTENTO_WIFI_MS = 5000;
const unsigned long INTERVALO_REINTENTO_WS_MS   = 3000;
const unsigned long INTERVALO_PING_MS           = 20000;
const unsigned long TIEMPO_BOTON_RESET_WIFI_MS  = 3000;

// ======================================================
// 4. ESTADO GLOBAL
// ======================================================
WebSocketsClient webSocket;

uint8_t primerbyte  = 0b00000000; // Primer 74HC595  (Q1..Q4)
uint8_t segundobyte = 0b00000000; // Segundo 74HC595 (Q0..Q7)

bool wsConectado              = false;
bool pulsoAuxiliarActivo      = false;
bool pasoDemoTemporizadoActivo = false;

unsigned long tiempoInicioAccion   = 0;
unsigned long duracionAccionMs     = 0;
unsigned long ultimoIntentoWiFi    = 0;
unsigned long ultimoPingMs         = 0;
unsigned long inicioBotonReset     = 0;
bool botonResetPresionado          = false;

// Control de secuencia monotona para garantizar que ninguna orden anterior a DETENER se ejecute
uint32_t ultimoOrdenIdAplicado = 0;
uint32_t ultimoStopOrdenId     = 0;

// ======================================================
// 5. PROTOTIPOS DE FUNCIONES
// ======================================================
void configurarWiFi();
void verificarWiFi();
void verificarResetWiFi();
void borrarCredencialesWiFi();
void configurarWebSocket();
void webSocketEvent(WStype_t type, uint8_t* payload, size_t length);
void procesarMensajeJson(const char* payload, size_t length);
void enviarConfirmacionDetener(uint32_t ordenId);
void ejecutarPorCodigoONombre(int codigo, const String& nombre, unsigned long duracionPasoMs, bool esPasoDemo);
int  resolverCodigoDesdeNombre(String nombre);
void aplicarMascara595(uint8_t pByte, uint8_t sByte);
void detenerTodo();
void imprimirBinario(uint8_t valor);

// ======================================================
// 6. REGISTROS DE DESPLAZAMIENTO 74HC595
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

void detenerTodo() {
  pulsoAuxiliarActivo       = false;
  pasoDemoTemporizadoActivo = false;
  duracionAccionMs          = 0;
  aplicarMascara595(0b00000000, 0b00000000);
}

// ======================================================
// 7. MAPEO DE LOS 12 COMANDOS + DETENER (CODIGO 0..12)
// ======================================================
int resolverCodigoDesdeNombre(String texto) {
  texto.trim();
  texto.toLowerCase();
  texto.replace("á", "a");
  texto.replace("é", "e");
  texto.replace("í", "i");
  texto.replace("ó", "o");
  texto.replace("ú", "u");

  if (texto == "detener" || texto == "stop" || texto == "paro")          return 0;
  if (texto == "mover brazo arriba" || texto == "brazo arriba")          return 1;
  if (texto == "girar izquierda")                                        return 2;
  if (texto == "mover brazo abajo" || texto == "brazo abajo")            return 3;
  if (texto == "girar derecha")                                          return 4;
  if (texto == "velocidad")                                              return 5;
  if (texto == "sonido" || texto == "claxon" || texto == "bocina")       return 6;
  if (texto == "demo")                                                   return 7;
  if (texto == "luz" || texto == "luces")                                return 8;
  if (texto == "avanzar" || texto == "adelante")                         return 9;
  if (texto == "girar torre izquierda" || texto == "torre izquierda")    return 10;
  if (texto == "retroceder" || texto == "atras" || texto == "reversa")   return 11;
  if (texto == "girar torre derecha" || texto == "torre derecha")        return 12;
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
    case 0: // Detener
      Serial.println(F("[CMD] 0 -> DETENER (todas las salidas a 0)"));
      detenerTodo();
      return;

    // --- Segundo 74HC595 (Q0..Q7) ---
    case 1: // Mover brazo arriba -> 2do 74HC595 Q0
      Serial.println(F("[CMD] 1 -> Mover brazo arriba (2do 595 Q0)"));
      sByte = (1 << 0);
      break;

    case 2: // Girar izquierda -> 2do 74HC595 Q1
      Serial.println(F("[CMD] 2 -> Girar izquierda (2do 595 Q1)"));
      sByte = (1 << 1);
      break;

    case 3: // Mover brazo abajo -> 2do 74HC595 Q2
      Serial.println(F("[CMD] 3 -> Mover brazo abajo (2do 595 Q2)"));
      sByte = (1 << 2);
      break;

    case 4: // Girar derecha -> 2do 74HC595 Q3
      Serial.println(F("[CMD] 4 -> Girar derecha (2do 595 Q3)"));
      sByte = (1 << 3);
      break;

    case 5: // Velocidad -> 2do 74HC595 Q4 (pulso)
      Serial.println(F("[CMD] 5 -> Velocidad (2do 595 Q4)"));
      sByte = (1 << 4);
      esAuxiliarPulso = true;
      break;

    case 6: // Sonido -> 2do 74HC595 Q5 (pulso)
      Serial.println(F("[CMD] 6 -> Sonido (2do 595 Q5)"));
      sByte = (1 << 5);
      esAuxiliarPulso = true;
      break;

    case 7: // Demo -> 2do 74HC595 Q6 (pulso)
      Serial.println(F("[CMD] 7 -> Demo (2do 595 Q6)"));
      sByte = (1 << 6);
      esAuxiliarPulso = true;
      break;

    case 8: // Luz -> 2do 74HC595 Q7 (pulso)
      Serial.println(F("[CMD] 8 -> Luz (2do 595 Q7)"));
      sByte = (1 << 7);
      esAuxiliarPulso = true;
      break;

    // --- Primer 74HC595 (Q1..Q4) ---
    case 9: // Avanzar -> 1er 74HC595 Q1
      Serial.println(F("[CMD] 9 -> Avanzar (1er 595 Q1)"));
      pByte = (1 << 1);
      break;

    case 10: // Girar torre izquierda -> 1er 74HC595 Q2
      Serial.println(F("[CMD] 10 -> Girar torre izquierda (1er 595 Q2)"));
      pByte = (1 << 2);
      break;

    case 11: // Retroceder -> 1er 74HC595 Q3
      Serial.println(F("[CMD] 11 -> Retroceder (1er 595 Q3)"));
      pByte = (1 << 3);
      break;

    case 12: // Girar torre derecha -> 1er 74HC595 Q4
      Serial.println(F("[CMD] 12 -> Girar torre derecha (1er 595 Q4)"));
      pByte = (1 << 4);
      break;

    default:
      Serial.printf("[WARN] Codigo de comando no reconocido (%d). Se mantienen salidas apagadas.\n", codEfectivo);
      detenerTodo();
      return;
  }

  aplicarMascara595(pByte, sByte);
  tiempoInicioAccion = millis();

  if (esAuxiliarPulso) {
    pulsoAuxiliarActivo       = true;
    pasoDemoTemporizadoActivo = false;
    duracionAccionMs          = (duracionPasoMs > 0) ? duracionPasoMs : DURACION_PULSO_AUX_MS;
  } else if (esPasoDemo && duracionPasoMs > 0) {
    // Los pasos de DEMO respetan su duracion_ms configurada en base de datos
    pulsoAuxiliarActivo       = false;
    pasoDemoTemporizadoActivo = true;
    duracionAccionMs          = duracionPasoMs;
  } else {
    // Movimientos manuales sostenidos: permanecen activos hasta recibir DETENER o nuevo comando
    pulsoAuxiliarActivo       = false;
    pasoDemoTemporizadoActivo = false;
    duracionAccionMs          = 0;
  }
}

// ======================================================
// 8. PROCESAMIENTO JSON DE EVENTOS WEBSOCKET
// ======================================================
void enviarConfirmacionDetener(uint32_t ordenId) {
  StaticJsonDocument<192> ack;
  ack["tipo"]        = "detencion_confirmada";
  ack["id_vehiculo"] = VEHICULO_ID;
  ack["orden_id"]    = ordenId;
  ack["salidas_off"] = (primerbyte == 0 && segundobyte == 0);

  String salida;
  serializeJson(ack, salida);
  webSocket.sendTXT(salida);
  Serial.printf("[WS -> ACK] %s\n", salida.c_str());
}

void procesarMensajeJson(const char* payload, size_t length) {
  if (strcmp(payload, "pong") == 0) {
    return;
  }

  StaticJsonDocument<1024> doc;
  DeserializationError err = deserializeJson(doc, payload, length);
  if (err) {
    Serial.printf("[JSON] Error al parsear mensaje: %s\n", err.c_str());
    return;
  }

  const char* tipo   = doc["tipo"]    | "";
  const char* accion = doc["accion"]  | "";
  String nombreCmd   = doc["comando"] | "";
  int codigo         = doc["codigo"].isNull() ? -1 : doc["codigo"].as<int>();
  uint32_t ordenId   = doc["orden_id"] | 0;

  // 1. PRIORIDAD MAXIMA: orden explicita de DETENER
  if (strcmp(tipo, "detener") == 0 || strcmp(accion, "DETENER") == 0 || codigo == 0 ||
      resolverCodigoDesdeNombre(nombreCmd) == 0) {
    if (ordenId > ultimoStopOrdenId) {
      ultimoStopOrdenId = ordenId;
    }
    if (ordenId > ultimoOrdenIdAplicado) {
      ultimoOrdenIdAplicado = ordenId;
    }
    detenerTodo();
    Serial.printf("[PRIORIDAD DETENER] Salidas desactivadas inmediatamente (orden_id=%u)\n", ordenId);
    enviarConfirmacionDetener(ordenId);
    return;
  }

  // 2. Descartar eventos rezagados con orden_id <= ultimoStopOrdenId o <= ultimoOrdenIdAplicado
  if (ordenId > 0) {
    if (ordenId <= ultimoStopOrdenId || ordenId <= ultimoOrdenIdAplicado) {
      Serial.printf("[SECUENCIA] Ignorando orden antigua/rezagada orden_id=%u (ultimo=%u, stop=%u)\n",
                    ordenId, ultimoOrdenIdAplicado, ultimoStopOrdenId);
      return;
    }
    ultimoOrdenIdAplicado = ordenId;
  }

  // 3. Eventos de ciclo de vida DEMO
  if (strcmp(tipo, "demo_inicio") == 0) {
    detenerTodo();
    Serial.printf("[DEMO] Iniciando DEMO '%s'\n", (const char*)(doc["nombre_demo"] | ""));
    return;
  }

  if (strcmp(tipo, "demo_fin") == 0 || strcmp(tipo, "demo_cancelada") == 0) {
    detenerTodo();
    Serial.printf("[DEMO] %s -> Salidas en 0\n", tipo);
    return;
  }

  if (strcmp(tipo, "demo_paso") == 0) {
    unsigned long duracionMs = doc["duracion_ms"] | 1000UL;
    ejecutarPorCodigoONombre(codigo, nombreCmd, duracionMs, true);
    return;
  }

  // 4. Movimiento individual sostenido
  if (strcmp(tipo, "movimiento_individual") == 0) {
    unsigned long duracionMs = doc["duracion_ms"] | 0UL;
    ejecutarPorCodigoONombre(codigo, nombreCmd, duracionMs, false);
    return;
  }

  // 5. Estado inicial / Estado actualizado
  if (strcmp(tipo, "estado_inicial") == 0) {
    // Al conectar por primera vez, garantizar que las salidas esten en reposo seguro
    if (codigo == 0 || resolverCodigoDesdeNombre(nombreCmd) == 0) {
      detenerTodo();
    }
    return;
  }

  if (strcmp(tipo, "estado_actualizado") == 0) {
    if (codigo == 0 || resolverCodigoDesdeNombre(nombreCmd) == 0) {
      detenerTodo();
    }
    return;
  }
}

// ======================================================
// 9. WIFI MANAGER Y GESTION DE CREDENCIALES
// ======================================================
void configurarWiFi() {
  WiFiManager wm;
  wm.setDebugOutput(true);
  wm.setConfigPortalTimeout(180);
  wm.setConnectTimeout(20);

  Serial.println(F("[WIFI] Conectando a la ultima red WiFi guardada..."));
  bool conectado = wm.autoConnect("Excavadora-IoT-Config", "12345678");

  if (!conectado) {
    Serial.println(F("[WIFI] Tiempo agotado en portal de configuracion. Reiniciando..."));
    delay(2000);
    ESP.restart();
  }

  Serial.print(F("[WIFI] Conectado a SSID: "));
  Serial.println(WiFi.SSID());
  Serial.print(F("[WIFI] Direccion IP: "));
  Serial.println(WiFi.localIP());
}

void verificarWiFi() {
  if (WiFi.status() == WL_CONNECTED) {
    return;
  }
  if (millis() - ultimoIntentoWiFi >= INTERVALO_REINTENTO_WIFI_MS) {
    ultimoIntentoWiFi = millis();
    Serial.println(F("[WIFI] Conexion perdida. Deteniendo salidas y reconectando..."));
    detenerTodo();
    WiFi.reconnect();
  }
}

void verificarResetWiFi() {
  if (Serial.available() > 0) {
    char c = Serial.read();
    if (c == 'R' || c == 'r') {
      borrarCredencialesWiFi();
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
  Serial.println(F("[WIFI] Borrando credenciales WiFi almacenadas..."));
  detenerTodo();

  WiFiManager wm;
  wm.resetSettings();
  WiFi.disconnect(true);
  delay(1000);

  Serial.println(F("[WIFI] Credenciales eliminadas. Reiniciando en portal AP..."));
  ESP.restart();
}

// ======================================================
// 10. CONFIGURACION Y EVENTOS WEBSOCKET SEGURO (WSS)
// ======================================================
void configurarWebSocket() {
  Serial.printf("[WSS] Configurando wss://%s:%u%s\n", WS_HOST, WS_PORT, WS_PATH);

  // Conexion TLS/SSL (WSS) en puerto 443 compatible con certificado Let's Encrypt en Nginx
  webSocket.beginSSL(WS_HOST, WS_PORT, WS_PATH);
  webSocket.onEvent(webSocketEvent);
  webSocket.setReconnectInterval(INTERVALO_REINTENTO_WS_MS);
  webSocket.enableHeartbeat(15000, 3000, 2);
}

void webSocketEvent(WStype_t type, uint8_t* payload, size_t length) {
  switch (type) {
    case WStype_DISCONNECTED:
      wsConectado = false;
      detenerTodo();
      Serial.println(F("[WSS] Desconectado del servidor. Salidas apagadas por seguridad."));
      break;

    case WStype_CONNECTED:
      wsConectado = true;
      Serial.printf("[WSS] Conectado exitosamente a %s\n", (char*)payload);
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
// 11. SETUP & LOOP PRINCIPAL
// ======================================================
void setup() {
  pinMode(CLOCK_PIN, OUTPUT);
  pinMode(LATCH_PIN, OUTPUT);
  pinMode(DATA_PIN,  OUTPUT);
  pinMode(RESET_WIFI_PIN, INPUT_PULLUP);

  // Asegurar estado fisico 0 en ambos 74HC595 desde el arranque
  detenerTodo();

  Serial.begin(115200);
  delay(200);
  Serial.println();
  Serial.println(F("=========================================================="));
  Serial.println(F(" EXCAVADORA IoT CAT-320 - ESP8266 WSS + WiFiManager + 595 "));
  Serial.println(F("=========================================================="));

  WiFi.mode(WIFI_STA);
  WiFi.persistent(true);
  WiFi.setAutoConnect(true);
  WiFi.setAutoReconnect(true);

  configurarWiFi();
  configurarWebSocket();
}

void loop() {
  verificarResetWiFi();
  verificarWiFi();

  if (WiFi.status() == WL_CONNECTED) {
    webSocket.loop();
  }

  unsigned long ahora = millis();

  // Apagar pulso auxiliar corto (Velocidad, Sonido, Demo, Luz)
  if (pulsoAuxiliarActivo && duracionAccionMs > 0 && (ahora - tiempoInicioAccion >= duracionAccionMs)) {
    detenerTodo();
    Serial.println(F("[PULSO] Fin de pulso auxiliar -> salidas en 0"));
  }

  // Apagar paso individual de una secuencia DEMO al cumplirse su duracion_ms
  if (pasoDemoTemporizadoActivo && duracionAccionMs > 0 && (ahora - tiempoInicioAccion >= duracionAccionMs)) {
    detenerTodo();
    Serial.println(F("[DEMO PASO] Fin de duracion_ms del paso -> salidas en 0"));
  }

  // Keep-alive de aplicacion ("ping") cada 20 s
  if (wsConectado && (ahora - ultimoPingMs >= INTERVALO_PING_MS)) {
    ultimoPingMs = ahora;
    webSocket.sendTXT("ping");
  }
}

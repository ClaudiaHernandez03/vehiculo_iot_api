#include <ESP8266WiFi.h>
#include <DNSServer.h>
#include <ESP8266WebServer.h>
#include <WiFiManager.h>
#include <WebSocketsClient.h>
#include <ArduinoJson.h>

// ======================================================
// CONFIGURACION SERVIDOR AWS / DUCKDNS
// ======================================================
const char* WS_HOST = "vehiculo-iot-api.duckdns.org";
const uint16_t WS_PORT = 443;
const char* WS_PATH = "/ws/vehiculos/1";

// ======================================================
// PINES 74HC595
// ======================================================
const int DATA_PIN  = D7;   // GPIO13
const int CLOCK_PIN = D5;   // GPIO14
const int LATCH_PIN = D6;   // GPIO12

// Boton opcional para borrar WiFi manteniendo presionado 3 segundos
const int RESET_WIFI_PIN = D1; // GPIO5

// ======================================================
// TIEMPOS
// ======================================================
const unsigned long TIEMPO_PULSO_FUNCION_MS = 350;
const unsigned long TIEMPO_MAXIMO_MOVIMIENTO_MS = 1500;
const unsigned long INTERVALO_REINTENTO_WIFI_MS = 5000;
const unsigned long INTERVALO_REINTENTO_WS_MS = 5000;
const unsigned long TIEMPO_BOTON_RESET_WIFI_MS = 3000;

// ======================================================
// VARIABLES GLOBALES
// ======================================================
WebSocketsClient webSocket;

byte reg1 = 0; // Primer 74HC595: Q1..Q4
byte reg2 = 0; // Segundo 74HC595: Q0..Q7

bool wsConectado = false;
bool pulsoActivo = false;
bool movimientoTemporalActivo = false;

unsigned long tiempoInicioAccion = 0;
unsigned long duracionAccionActual = 0;
unsigned long ultimoIntentoWiFi = 0;
unsigned long inicioBotonReset = 0;
bool botonResetPresionado = false;

String ultimoComandoRecibido = "NINGUNO";

// ======================================================
// PROTOTIPOS
// ======================================================
void configurarWiFi();
void verificarWiFi();
void verificarResetWiFi();
void borrarCredencialesWiFi();
void configurarWebSocket();
void webSocketEvent(WStype_t type, uint8_t * payload, size_t length);
void procesarMensajeJson(const char* jsonText);
String normalizarTexto(String texto);
void ejecutarComando(String comando, unsigned long duracionMs = 0);
void activarPulso(byte mascaraReg1, byte mascaraReg2, unsigned long duracionMs);
void mantenerMovimiento(byte mascaraReg1, byte mascaraReg2, unsigned long duracionMs);
void detenerTodo();
void actualizar595();
void imprimirEstadoBinario();

// ======================================================
// SETUP
// ======================================================
void setup() {
  Serial.begin(115200);
  delay(300);

  Serial.println();
  Serial.println("==========================================");
  Serial.println("EXCAVADORA IOT CAT-320 - ESP8266 INICIANDO");
  Serial.println("==========================================");

  pinMode(DATA_PIN, OUTPUT);
  pinMode(CLOCK_PIN, OUTPUT);
  pinMode(LATCH_PIN, OUTPUT);
  pinMode(RESET_WIFI_PIN, INPUT_PULLUP);

  detenerTodo();

  WiFi.mode(WIFI_STA);
  WiFi.persistent(true);
  WiFi.setAutoConnect(true);
  WiFi.setAutoReconnect(true);

  configurarWiFi();
  configurarWebSocket();
}

// ======================================================
// LOOP
// ======================================================
void loop() {
  verificarResetWiFi();
  verificarWiFi();

  if (WiFi.status() == WL_CONNECTED) {
    webSocket.loop();
  }

  // Finalizar pulsos cortos: luz, sonido, velocidad, demo
  if (pulsoActivo && (millis() - tiempoInicioAccion >= duracionAccionActual)) {
    detenerTodo();
    Serial.println("[CONTROL] Fin de pulso corto -> salidas apagadas");
  }

  // Finalizar movimientos con duracion definida o seguridad
  if (movimientoTemporalActivo && (millis() - tiempoInicioAccion >= duracionAccionActual)) {
    detenerTodo();
    Serial.println("[CONTROL] Fin de movimiento -> salidas apagadas");
  }
}

// ======================================================
// WIFI MANAGER
// ======================================================
void configurarWiFi() {
  WiFiManager wm;

  wm.setDebugOutput(true);
  wm.setConfigPortalTimeout(180); // 3 minutos para configurar si no encuentra red
  wm.setConnectTimeout(20);       // 20 segundos intentando conectar a la red guardada

  Serial.println("[WIFI] Intentando conectar a la ultima red guardada...");

  // Si hay red guardada se conecta sola.
  // Si no puede conectarse, abre el AP: Excavadora-IoT-Config
  bool conectado = wm.autoConnect("Excavadora-IoT-Config", "12345678");

  if (!conectado) {
    Serial.println("[WIFI] No se pudo conectar en el tiempo limite. Reiniciando...");
    delay(2000);
    ESP.restart();
  }

  Serial.println("[WIFI] Conectado correctamente");
  Serial.print("[WIFI] Red: ");
  Serial.println(WiFi.SSID());
  Serial.print("[WIFI] IP: ");
  Serial.println(WiFi.localIP());
}

void verificarWiFi() {
  if (WiFi.status() == WL_CONNECTED) {
    return;
  }

  if (millis() - ultimoIntentoWiFi >= INTERVALO_REINTENTO_WIFI_MS) {
    ultimoIntentoWiFi = millis();
    Serial.println("[WIFI] Conexion perdida. Reintentando con red guardada...");
    detenerTodo();
    WiFi.reconnect();
  }
}

void verificarResetWiFi() {
  // Opcion 1: escribir R o r en monitor serial para borrar red guardada
  if (Serial.available() > 0) {
    char c = Serial.read();
    if (c == 'R' || c == 'r') {
      borrarCredencialesWiFi();
    }
  }

  // Opcion 2: mantener D1 a GND durante 3 segundos
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
  Serial.println("[WIFI] Borrando credenciales WiFi guardadas...");
  detenerTodo();

  WiFiManager wm;
  wm.resetSettings();
  WiFi.disconnect(true);
  delay(1000);

  Serial.println("[WIFI] Credenciales borradas. Reiniciando en modo configuracion...");
  ESP.restart();
}

// ======================================================
// WEBSOCKET
// ======================================================
void configurarWebSocket() {
  Serial.println("[WS] Configurando WebSocket seguro WSS...");
  Serial.print("[WS] Host: ");
  Serial.println(WS_HOST);
  Serial.print("[WS] Ruta: ");
  Serial.println(WS_PATH);

  // Conexion WSS sin validar huella del certificado para facilitar pruebas con Let's Encrypt
  webSocket.beginSSL(WS_HOST, WS_PORT, WS_PATH);
  webSocket.onEvent(webSocketEvent);
  webSocket.setReconnectInterval(INTERVALO_REINTENTO_WS_MS);
  webSocket.enableHeartbeat(15000, 3000, 2);
}

void webSocketEvent(WStype_t type, uint8_t * payload, size_t length) {
  switch (type) {
    case WStype_DISCONNECTED:
      wsConectado = false;
      detenerTodo();
      Serial.println("[WS] Desconectado del servidor. Vehiculo detenido por seguridad.");
      break;

    case WStype_CONNECTED:
      wsConectado = true;
      Serial.print("[WS] Conectado a: ");
      Serial.println((char*)payload);
      break;

    case WStype_TEXT:
      Serial.print("[WS] Mensaje recibido: ");
      Serial.println((char*)payload);
      procesarMensajeJson((const char*)payload);
      break;

    case WStype_ERROR:
      Serial.println("[WS] Error en WebSocket");
      break;

    default:
      break;
  }
}

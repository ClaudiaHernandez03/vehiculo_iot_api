/*
 * ============================================================================
 * PROYECTO: Vehiculo IoT - Excavadora CAT-320 (ESP8266 + 2x 74HC595 + WSS)
 * ARCHIVO:  sketch_oct29a_original.ino (Respaldo de referencia original)
 * ============================================================================
 * MAPA DE HARDWARE (2x 74HC595):
 *   CLOCK_PIN = D5 (GPIO14)
 *   LATCH_PIN = D6 (GPIO12)
 *   DATA_PIN  = D7 (GPIO13)
 *
 *   Primer 74HC595 (primerbyte):
 *     Q1 (1<<1) -> Avanzar               (codigo 9)
 *     Q2 (1<<2) -> Girar torre izquierda (codigo 10)
 *     Q3 (1<<3) -> Retroceder            (codigo 11)
 *     Q4 (1<<4) -> Girar torre derecha   (codigo 12)
 *
 *   Segundo 74HC595 (segundobyte):
 *     Q0 (1<<0) -> Mover brazo arriba    (codigo 1)
 *     Q1 (1<<1) -> Girar izquierda       (codigo 2)
 *     Q2 (1<<2) -> Mover brazo abajo     (codigo 3)
 *     Q3 (1<<3) -> Girar derecha         (codigo 4)
 *     Q4 (1<<4) -> Velocidad             (codigo 5)
 *     Q5 (1<<5) -> Sonido                (codigo 6)
 *     Q6 (1<<6) -> Demo                  (codigo 7)
 *     Q7 (1<<7) -> Luz                   (codigo 8)
 * ============================================================================
 */

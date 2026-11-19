-- =============================================================================
-- ARCHIVO: 02_recuperacion_datos_y_migracion_idempotente.sql
-- PROPÓSITO: Recuperar de manera idempotente todos los registros originales de
--            la Excavadora IoT (CAT-320) y ejecutar la migración segura para
--            recuperar el registro principal de la DEMO 3 (`id_demo = 3`),
--            reconciliando sus 5 movimientos huérfanos en `demo_movimientos`
--            sin eliminar los registros duplicados en `orden = 4`.
--
-- NOTA DE DOCUMENTACIÓN DE DATOS PROVISIONALES / AUDITADOS:
--   1. En la base de datos local existían 5 movimientos en `demo_movimientos`
--      (`id_demo_movimiento` IN (3, 8, 9, 10, 11)) asociados a `id_demo = 3`,
--      pero faltaba la fila `id_demo = 3` en la tabla `demos`.
--      Se reconstruye `id_demo = 3` con:
--        - `id_vehiculo = 1`
--        - `nombre = 'Ciclo de Excavacion'` (recuperado de `limpiar_base_excavadora.py`
--          y del selector de `frontend/index.html`)
--        - `descripcion = 'Secuencia de excavacion recuperada: rotacion de torre y control de brazo (5 movimientos conservados)'`
--        - `activo = 1`
--   2. Se conservan íntegramente los 13 comandos (`id_comando` 1 al 13):
--      `id_comando = 3` restituido a `Mover brazo abajo` (`codigo = 3`) y
--      `id_comando = 13` como identificador exclusivo de `Detener` (`codigo = 0`).
--      Se conservan los 15 movimientos en `demo_movimientos` (incluyendo los duplicados en `orden = 4`).
-- =============================================================================

USE `vehiculo_iot`;

START TRANSACTION;

-- 1. Vehículo original (ID = 1: Excavadora IoT CAT-320)
INSERT INTO `vehiculos` (`id_vehiculo`, `nombre`, `modelo`, `descripcion`, `microcontrolador`, `activo`, `fecha_registro`)
VALUES (1, 'Excavadora IoT', 'CAT-320', 'Excavadora IoT con 12 movimientos', 'ESP8266', 1, '2026-11-10 18:58:20')
ON DUPLICATE KEY UPDATE
  `nombre` = VALUES(`nombre`),
  `modelo` = VALUES(`modelo`),
  `descripcion` = VALUES(`descripcion`),
  `microcontrolador` = VALUES(`microcontrolador`),
  `activo` = VALUES(`activo`);

-- 2. Los 13 registros originales de `comandos` (conservados sin pérdida de datos)
INSERT INTO `comandos` (`id_comando`, `id_vehiculo`, `nombre`, `codigo`, `descripcion`, `activo`, `fecha_registro`) VALUES
(1,  1, 'Mover brazo arriba',    1,  'Subir el brazo principal de la excavadora', 1, '2026-11-10 18:59:45'),
(2,  1, 'Girar izquierda',       2,  'Girar las orugas hacia la izquierda', 1, '2026-11-10 18:59:52'),
(3,  1, 'Mover brazo abajo',     3,  'Bajar el brazo principal de la excavadora', 1, '2026-11-10 19:00:12'),
(4,  1, 'Girar derecha',         4,  'Girar las orugas hacia la derecha', 1, '2026-11-10 19:01:50'),
(5,  1, 'Velocidad',             5,  'Cambiar nivel de velocidad de la excavadora', 1, '2026-11-10 19:01:50'),
(6,  1, 'Sonido',                6,  'Activar o desactivar sonido de motor y bocina', 1, '2026-11-10 19:01:50'),
(7,  1, 'Demo',                  7,  'Modo demostracion de la excavadora', 1, '2026-11-10 19:01:50'),
(8,  1, 'Luz',                   8,  'Encender o apagar luces de trabajo', 1, '2026-11-10 19:01:50'),
(9,  1, 'Avanzar',               9,  'Desplazar la excavadora hacia adelante', 1, '2026-11-10 19:01:50'),
(10, 1, 'Girar torre izquierda', 10, 'Rotar la cabina/torre hacia la izquierda', 1, '2026-11-10 19:01:50'),
(11, 1, 'Retroceder',            11, 'Desplazar la excavadora hacia atras', 1, '2026-11-10 19:01:50'),
(12, 1, 'Girar torre derecha',   12, 'Rotar la cabina/torre hacia la derecha', 1, '2026-11-10 19:01:50'),
(13, 1, 'Detener',               0,  'Detener inmediatamente todos los movimientos activos del vehiculo', 1, '2026-11-10 19:01:50')
ON DUPLICATE KEY UPDATE
  `id_vehiculo` = VALUES(`id_vehiculo`),
  `nombre` = VALUES(`nombre`),
  `codigo` = VALUES(`codigo`),
  `descripcion` = VALUES(`descripcion`),
  `activo` = VALUES(`activo`);

-- 3. Demostraciones 1 y 2 originales + Recuperación idempotente de DEMO 3 (padre de los 5 movimientos huérfanos)
INSERT INTO `demos` (`id_demo`, `id_vehiculo`, `nombre`, `descripcion`, `activo`, `fecha_registro`) VALUES
(1, 1, 'Inspeccion Completa de Torre y Brazo', 'Prueba de rotacion de torre y control de brazo', 1, '2026-11-10 19:02:47'),
(2, 1, 'Recorrido y Giro de Orugas', 'Maniobra de desplazamiento: avanzar, girar izquierda, girar derecha y retroceder', 1, '2026-11-10 19:02:56'),
(3, 1, 'Ciclo de Excavacion', 'Secuencia de excavacion recuperada (registro principal reconstruido para conservar sus 5 movimientos)', 1, '2026-11-10 19:03:00')
ON DUPLICATE KEY UPDATE
  `id_vehiculo` = VALUES(`id_vehiculo`),
  `nombre` = VALUES(`nombre`),
  `descripcion` = VALUES(`descripcion`),
  `activo` = VALUES(`activo`);

-- 4. Los 15 movimientos de demostración originales (5 en Demo 1, 5 en Demo 2, 5 en Demo 3;
--    incluyendo los duplicados en orden=4 sin eliminarlos automáticamente)
INSERT INTO `demo_movimientos` (`id_demo_movimiento`, `id_demo`, `id_comando`, `orden`, `duracion_ms`, `pausa_ms`) VALUES
(1,  1, 10, 4, 1500, 500),
(2,  2, 11, 4, 2000, 500),
(3,  3, 3,  4, 1500, 500),
(4,  1, 9,  1, 2000, 500),
(5,  1, 3,  2, 2000, 500),
(6,  1, 1,  3, 2000, 500),
(7,  1, 10, 4, 1500, 500),
(8,  3, 10, 1, 1500, 500),
(9,  3, 12, 2, 1500, 500),
(10, 3, 1,  3, 1500, 500),
(11, 3, 3,  4, 1500, 500),
(12, 2, 9,  1, 2000, 500),
(13, 2, 2,  2, 1500, 500),
(14, 2, 4,  3, 1500, 500),
(15, 2, 11, 4, 2000, 500)
ON DUPLICATE KEY UPDATE
  `id_demo` = VALUES(`id_demo`),
  `id_comando` = VALUES(`id_comando`),
  `orden` = VALUES(`orden`),
  `duracion_ms` = VALUES(`duracion_ms`),
  `pausa_ms` = VALUES(`pausa_ms`);

-- 5. Estado actual del vehículo 1
INSERT INTO `estado_vehiculo` (`id_estado`, `id_vehiculo`, `id_comando`, `conectado`, `fecha_actualizacion`)
VALUES (1, 1, 13, 1, '2026-11-10 18:58:41')
ON DUPLICATE KEY UPDATE
  `id_comando` = VALUES(`id_comando`),
  `conectado` = VALUES(`conectado`);

COMMIT;

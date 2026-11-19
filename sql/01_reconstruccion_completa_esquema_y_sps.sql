-- =============================================================================
-- ARCHIVO: 01_reconstruccion_completa_esquema_y_sps.sql
-- PROPÓSITO: Reconstrucción completa e idempotente del esquema `vehiculo_iot`
--            (6 tablas con sus claves foráneas y reglas ON DELETE / ON UPDATE)
--            y los 21 Procedimientos Almacenados (Stored Procedures) originales.
--
-- COMPATIBILIDAD CON AMAZON RDS:
--   Se omite la cláusula `DEFINER='root'@'localhost'` para que Amazon RDS asigne
--   automáticamente el usuario maestro (`admin`@`%`) sin requerir privilegio SUPER.
-- =============================================================================

CREATE DATABASE IF NOT EXISTS `vehiculo_iot`
  DEFAULT CHARACTER SET utf8mb4
  COLLATE utf8mb4_unicode_ci;

USE `vehiculo_iot`;

-- =============================================================================
-- 1. CREACIÓN DE LAS 6 TABLAS DEL ESQUEMA (CON CLAVES FORÁNEAS ORIGINALES)
-- =============================================================================

CREATE TABLE IF NOT EXISTS `vehiculos` (
  `id_vehiculo` int NOT NULL AUTO_INCREMENT,
  `nombre` varchar(80) COLLATE utf8mb4_unicode_ci NOT NULL,
  `modelo` varchar(80) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `descripcion` varchar(255) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `microcontrolador` varchar(50) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `activo` tinyint(1) NOT NULL DEFAULT '1',
  `fecha_registro` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`id_vehiculo`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS `comandos` (
  `id_comando` int NOT NULL AUTO_INCREMENT,
  `id_vehiculo` int NOT NULL,
  `nombre` varchar(60) COLLATE utf8mb4_unicode_ci NOT NULL,
  `codigo` int NOT NULL,
  `descripcion` varchar(255) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `activo` tinyint(1) NOT NULL DEFAULT '1',
  `fecha_registro` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`id_comando`),
  KEY `fk_comandos_vehiculo` (`id_vehiculo`),
  CONSTRAINT `fk_comandos_vehiculo` FOREIGN KEY (`id_vehiculo`) REFERENCES `vehiculos` (`id_vehiculo`) ON DELETE CASCADE ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS `estado_vehiculo` (
  `id_estado` int NOT NULL AUTO_INCREMENT,
  `id_vehiculo` int NOT NULL,
  `id_comando` int NOT NULL,
  `conectado` tinyint(1) NOT NULL DEFAULT '0',
  `fecha_actualizacion` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`id_estado`),
  UNIQUE KEY `id_vehiculo` (`id_vehiculo`),
  KEY `fk_estado_comando` (`id_comando`),
  CONSTRAINT `fk_estado_comando` FOREIGN KEY (`id_comando`) REFERENCES `comandos` (`id_comando`) ON DELETE RESTRICT ON UPDATE CASCADE,
  CONSTRAINT `fk_estado_vehiculo` FOREIGN KEY (`id_vehiculo`) REFERENCES `vehiculos` (`id_vehiculo`) ON DELETE CASCADE ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS `historial_comandos` (
  `id_historial` int NOT NULL AUTO_INCREMENT,
  `id_vehiculo` int NOT NULL,
  `id_comando` int NOT NULL,
  `origen` varchar(30) COLLATE utf8mb4_unicode_ci NOT NULL DEFAULT 'API',
  `fecha_ejecucion` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`id_historial`),
  KEY `fk_historial_vehiculo` (`id_vehiculo`),
  KEY `fk_historial_comando` (`id_comando`),
  CONSTRAINT `fk_historial_comando` FOREIGN KEY (`id_comando`) REFERENCES `comandos` (`id_comando`) ON DELETE RESTRICT ON UPDATE CASCADE,
  CONSTRAINT `fk_historial_vehiculo` FOREIGN KEY (`id_vehiculo`) REFERENCES `vehiculos` (`id_vehiculo`) ON DELETE CASCADE ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS `demos` (
  `id_demo` int NOT NULL AUTO_INCREMENT,
  `id_vehiculo` int NOT NULL,
  `nombre` varchar(80) COLLATE utf8mb4_unicode_ci NOT NULL,
  `descripcion` varchar(255) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `activo` tinyint(1) NOT NULL DEFAULT '1',
  `fecha_registro` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`id_demo`),
  KEY `fk_demos_vehiculo` (`id_vehiculo`),
  CONSTRAINT `fk_demos_vehiculo` FOREIGN KEY (`id_vehiculo`) REFERENCES `vehiculos` (`id_vehiculo`) ON DELETE CASCADE ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS `demo_movimientos` (
  `id_demo_movimiento` int NOT NULL AUTO_INCREMENT,
  `id_demo` int NOT NULL,
  `id_comando` int NOT NULL,
  `orden` int NOT NULL,
  `duracion_ms` int NOT NULL DEFAULT '1000',
  `pausa_ms` int NOT NULL DEFAULT '500',
  PRIMARY KEY (`id_demo_movimiento`),
  KEY `fk_demomov_demo` (`id_demo`),
  KEY `fk_demomov_comando` (`id_comando`),
  CONSTRAINT `fk_demomov_comando` FOREIGN KEY (`id_comando`) REFERENCES `comandos` (`id_comando`) ON DELETE RESTRICT ON UPDATE CASCADE,
  CONSTRAINT `fk_demomov_demo` FOREIGN KEY (`id_demo`) REFERENCES `demos` (`id_demo`) ON DELETE CASCADE ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- =============================================================================
-- 2. CREACIÓN DE LOS 21 PROCEDIMIENTOS ALMACENADOS (STORED PROCEDURES)
-- =============================================================================

DROP PROCEDURE IF EXISTS `sp_obtener_vehiculos`;
DROP PROCEDURE IF EXISTS `sp_obtener_vehiculo`;
DROP PROCEDURE IF EXISTS `sp_crear_vehiculo`;
DROP PROCEDURE IF EXISTS `sp_actualizar_vehiculo`;
DROP PROCEDURE IF EXISTS `sp_eliminar_vehiculo`;
DROP PROCEDURE IF EXISTS `sp_crear_comando`;
DROP PROCEDURE IF EXISTS `sp_obtener_comandos_vehiculo`;
DROP PROCEDURE IF EXISTS `sp_actualizar_comando`;
DROP PROCEDURE IF EXISTS `sp_eliminar_comando`;
DROP PROCEDURE IF EXISTS `sp_crear_demo`;
DROP PROCEDURE IF EXISTS `sp_obtener_demos`;
DROP PROCEDURE IF EXISTS `sp_actualizar_demo`;
DROP PROCEDURE IF EXISTS `sp_eliminar_demo`;
DROP PROCEDURE IF EXISTS `sp_agregar_movimiento_demo`;
DROP PROCEDURE IF EXISTS `sp_obtener_movimientos_demo`;
DROP PROCEDURE IF EXISTS `sp_actualizar_movimiento_demo`;
DROP PROCEDURE IF EXISTS `sp_eliminar_movimiento_demo`;
DROP PROCEDURE IF EXISTS `sp_actualizar_estado`;
DROP PROCEDURE IF EXISTS `sp_obtener_estado`;
DROP PROCEDURE IF EXISTS `sp_obtener_historial`;
DROP PROCEDURE IF EXISTS `sp_registrar_historial`;

DELIMITER $$

-- 1. sp_obtener_vehiculos
CREATE PROCEDURE `sp_obtener_vehiculos`()
BEGIN
    SELECT id_vehiculo, nombre, modelo, descripcion, microcontrolador, activo, fecha_registro
    FROM vehiculos
    ORDER BY id_vehiculo ASC;
END$$

-- 2. sp_obtener_vehiculo
CREATE PROCEDURE `sp_obtener_vehiculo`(IN p_id_vehiculo INT)
BEGIN
    SELECT id_vehiculo, nombre, modelo, descripcion, microcontrolador, activo, fecha_registro
    FROM vehiculos
    WHERE id_vehiculo = p_id_vehiculo;
END$$

-- 3. sp_crear_vehiculo
CREATE PROCEDURE `sp_crear_vehiculo`(
    IN p_nombre VARCHAR(80),
    IN p_modelo VARCHAR(80),
    IN p_descripcion VARCHAR(255),
    IN p_microcontrolador VARCHAR(50)
)
BEGIN
    INSERT INTO vehiculos (nombre, modelo, descripcion, microcontrolador, activo)
    VALUES (p_nombre, p_modelo, p_descripcion, p_microcontrolador, 1);
    SELECT LAST_INSERT_ID() AS id_vehiculo;
END$$

-- 4. sp_actualizar_vehiculo
CREATE PROCEDURE `sp_actualizar_vehiculo`(
    IN p_id_vehiculo INT,
    IN p_nombre VARCHAR(80),
    IN p_modelo VARCHAR(80),
    IN p_descripcion VARCHAR(255),
    IN p_microcontrolador VARCHAR(50),
    IN p_activo TINYINT
)
BEGIN
    UPDATE vehiculos
    SET nombre = p_nombre,
        modelo = p_modelo,
        descripcion = p_descripcion,
        microcontrolador = p_microcontrolador,
        activo = p_activo
    WHERE id_vehiculo = p_id_vehiculo;
    SELECT ROW_COUNT() AS filas_afectadas;
END$$

-- 5. sp_eliminar_vehiculo
CREATE PROCEDURE `sp_eliminar_vehiculo`(IN p_id_vehiculo INT)
BEGIN
    DELETE FROM vehiculos WHERE id_vehiculo = p_id_vehiculo;
    SELECT ROW_COUNT() AS filas_afectadas;
END$$

-- 6. sp_crear_comando
CREATE PROCEDURE `sp_crear_comando`(
    IN p_id_vehiculo INT,
    IN p_nombre VARCHAR(60),
    IN p_codigo INT,
    IN p_descripcion VARCHAR(255)
)
BEGIN
    INSERT INTO comandos (id_vehiculo, nombre, codigo, descripcion, activo)
    VALUES (p_id_vehiculo, p_nombre, p_codigo, p_descripcion, 1);
    SELECT LAST_INSERT_ID() AS id_comando;
END$$

-- 7. sp_obtener_comandos_vehiculo
CREATE PROCEDURE `sp_obtener_comandos_vehiculo`(IN p_id_vehiculo INT)
BEGIN
    SELECT id_comando, id_vehiculo, nombre, codigo, descripcion, activo, fecha_registro
    FROM comandos
    WHERE id_vehiculo = p_id_vehiculo
    ORDER BY id_comando ASC;
END$$

-- 8. sp_actualizar_comando
CREATE PROCEDURE `sp_actualizar_comando`(
    IN p_id_comando INT,
    IN p_nombre VARCHAR(60),
    IN p_codigo INT,
    IN p_descripcion VARCHAR(255),
    IN p_activo TINYINT
)
BEGIN
    UPDATE comandos
    SET nombre = p_nombre,
        codigo = p_codigo,
        descripcion = p_descripcion,
        activo = p_activo
    WHERE id_comando = p_id_comando;
    SELECT ROW_COUNT() AS filas_afectadas;
END$$

-- 9. sp_eliminar_comando
CREATE PROCEDURE `sp_eliminar_comando`(IN p_id_comando INT)
BEGIN
    DELETE FROM comandos WHERE id_comando = p_id_comando;
    SELECT ROW_COUNT() AS filas_afectadas;
END$$

-- 10. sp_crear_demo
CREATE PROCEDURE `sp_crear_demo`(
    IN p_id_vehiculo INT,
    IN p_nombre VARCHAR(80),
    IN p_descripcion VARCHAR(255)
)
BEGIN
    INSERT INTO demos (id_vehiculo, nombre, descripcion, activo)
    VALUES (p_id_vehiculo, p_nombre, p_descripcion, 1);
    SELECT LAST_INSERT_ID() AS id_demo;
END$$

-- 11. sp_obtener_demos
CREATE PROCEDURE `sp_obtener_demos`(IN p_id_vehiculo INT)
BEGIN
    SELECT id_demo, id_vehiculo, nombre, descripcion, activo, fecha_registro
    FROM demos
    WHERE id_vehiculo = p_id_vehiculo
    ORDER BY id_demo ASC;
END$$

-- 12. sp_actualizar_demo
CREATE PROCEDURE `sp_actualizar_demo`(
    IN p_id_demo INT,
    IN p_nombre VARCHAR(80),
    IN p_descripcion VARCHAR(255),
    IN p_activo TINYINT
)
BEGIN
    UPDATE demos
    SET nombre = p_nombre,
        descripcion = p_descripcion,
        activo = p_activo
    WHERE id_demo = p_id_demo;
    SELECT ROW_COUNT() AS filas_afectadas;
END$$

-- 13. sp_eliminar_demo
CREATE PROCEDURE `sp_eliminar_demo`(IN p_id_demo INT)
BEGIN
    DELETE FROM demos WHERE id_demo = p_id_demo;
    SELECT ROW_COUNT() AS filas_afectadas;
END$$

-- 14. sp_agregar_movimiento_demo
CREATE PROCEDURE `sp_agregar_movimiento_demo`(
    IN p_id_demo INT,
    IN p_id_comando INT,
    IN p_orden INT,
    IN p_duracion_ms INT,
    IN p_pausa_ms INT
)
BEGIN
    INSERT INTO demo_movimientos (id_demo, id_comando, orden, duracion_ms, pausa_ms)
    VALUES (p_id_demo, p_id_comando, p_orden, p_duracion_ms, p_pausa_ms);
    SELECT LAST_INSERT_ID() AS id_demo_movimiento;
END$$

-- 15. sp_obtener_movimientos_demo
CREATE PROCEDURE `sp_obtener_movimientos_demo`(IN p_id_demo INT)
BEGIN
    SELECT
        dm.id_demo_movimiento,
        dm.id_demo,
        dm.id_comando,
        c.nombre AS comando,
        c.codigo,
        dm.orden,
        dm.duracion_ms,
        dm.pausa_ms
    FROM demo_movimientos dm
    INNER JOIN comandos c ON c.id_comando = dm.id_comando
    WHERE dm.id_demo = p_id_demo
    ORDER BY dm.orden ASC;
END$$

-- 16. sp_actualizar_movimiento_demo
CREATE PROCEDURE `sp_actualizar_movimiento_demo`(
    IN p_id_demo_movimiento INT,
    IN p_id_comando INT,
    IN p_orden INT,
    IN p_duracion_ms INT,
    IN p_pausa_ms INT
)
BEGIN
    UPDATE demo_movimientos
    SET id_comando = p_id_comando,
        orden = p_orden,
        duracion_ms = p_duracion_ms,
        pausa_ms = p_pausa_ms
    WHERE id_demo_movimiento = p_id_demo_movimiento;
    SELECT ROW_COUNT() AS filas_afectadas;
END$$

-- 17. sp_eliminar_movimiento_demo
CREATE PROCEDURE `sp_eliminar_movimiento_demo`(IN p_id_demo_movimiento INT)
BEGIN
    DELETE FROM demo_movimientos WHERE id_demo_movimiento = p_id_demo_movimiento;
    SELECT ROW_COUNT() AS filas_afectadas;
END$$

-- 18. sp_actualizar_estado
CREATE PROCEDURE `sp_actualizar_estado`(
    IN p_id_vehiculo INT,
    IN p_id_comando INT,
    IN p_conectado TINYINT
)
BEGIN
    INSERT INTO estado_vehiculo (id_vehiculo, id_comando, conectado)
    VALUES (p_id_vehiculo, p_id_comando, p_conectado)
    ON DUPLICATE KEY UPDATE
        id_comando = VALUES(id_comando),
        conectado = VALUES(conectado),
        fecha_actualizacion = CURRENT_TIMESTAMP;
END$$

-- 19. sp_obtener_estado
CREATE PROCEDURE `sp_obtener_estado`(IN p_id_vehiculo INT)
BEGIN
    SELECT
        ev.id_estado,
        ev.id_vehiculo,
        v.nombre AS vehiculo,
        ev.id_comando,
        c.nombre AS comando,
        c.codigo,
        ev.conectado,
        ev.fecha_actualizacion
    FROM estado_vehiculo ev
    INNER JOIN vehiculos v ON v.id_vehiculo = ev.id_vehiculo
    INNER JOIN comandos c ON c.id_comando = ev.id_comando
    WHERE ev.id_vehiculo = p_id_vehiculo;
END$$

-- 20. sp_obtener_historial
CREATE PROCEDURE `sp_obtener_historial`(IN p_id_vehiculo INT)
BEGIN
    SELECT
        h.id_historial,
        h.id_vehiculo,
        v.nombre AS vehiculo,
        h.id_comando,
        c.nombre AS comando,
        c.codigo,
        h.origen,
        h.fecha_ejecucion
    FROM historial_comandos h
    INNER JOIN vehiculos v ON v.id_vehiculo = h.id_vehiculo
    INNER JOIN comandos c ON c.id_comando = h.id_comando
    WHERE h.id_vehiculo = p_id_vehiculo
    ORDER BY h.id_historial DESC
    LIMIT 10;
END$$

-- 21. sp_registrar_historial
CREATE PROCEDURE `sp_registrar_historial`(
    IN p_id_vehiculo INT,
    IN p_id_comando INT,
    IN p_origen VARCHAR(30)
)
BEGIN
    INSERT INTO historial_comandos (id_vehiculo, id_comando, origen)
    VALUES (p_id_vehiculo, p_id_comando, p_origen);
    SELECT LAST_INSERT_ID() AS id_historial;
END$$

DELIMITER ;

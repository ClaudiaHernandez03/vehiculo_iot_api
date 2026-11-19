-- =============================================================================
-- ARCHIVO: 04_rbac_administracion_iot.sql
-- PROPÓSITO: Tablas y datos semilla en Amazon RDS (`vehiculo_iot`) para la
--            Actividad 4.3 (Aplicación de Administración IoT):
--              1. `usuarios_iot`: Roles ADMIN, USUARIO (Compradores A y B), INVITADO
--              2. `asignaciones_vehiculo`: Propiedad BOLA/IDOR, estado comercial y
--                 consentimiento de soporte técnico por vehículo.
--              3. `bitacora_auditoria_iot`: Registro verificable en RDS de accesos,
--                 cambios de propietario y rechazos 401 / 403 / 409.
-- =============================================================================

USE `vehiculo_iot`;

CREATE TABLE IF NOT EXISTS `usuarios_iot` (
  `id_usuario` INT NOT NULL AUTO_INCREMENT,
  `nombre` VARCHAR(100) COLLATE utf8mb4_unicode_ci NOT NULL,
  `correo` VARCHAR(120) COLLATE utf8mb4_unicode_ci NOT NULL,
  `rol` ENUM('ADMIN', 'USUARIO', 'INVITADO') COLLATE utf8mb4_unicode_ci NOT NULL DEFAULT 'USUARIO',
  `permisos` VARCHAR(255) COLLATE utf8mb4_unicode_ci NOT NULL,
  `activo` TINYINT(1) NOT NULL DEFAULT 1,
  `fecha_registro` TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`id_usuario`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS `asignaciones_vehiculo` (
  `id_vehiculo` INT NOT NULL,
  `id_usuario` INT NOT NULL,
  `estado_comercial` VARCHAR(40) COLLATE utf8mb4_unicode_ci NOT NULL DEFAULT 'Vendido / Asignado',
  `consentimiento_soporte` TINYINT(1) NOT NULL DEFAULT 1,
  `habilitado_exhibicion` TINYINT(1) NOT NULL DEFAULT 1,
  `fecha_actualizacion` TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`id_vehiculo`),
  KEY `fk_asig_usuario` (`id_usuario`),
  CONSTRAINT `fk_asig_vehiculo` FOREIGN KEY (`id_vehiculo`) REFERENCES `vehiculos` (`id_vehiculo`) ON DELETE CASCADE ON UPDATE CASCADE,
  CONSTRAINT `fk_asig_usuario` FOREIGN KEY (`id_usuario`) REFERENCES `usuarios_iot` (`id_usuario`) ON DELETE RESTRICT ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS `bitacora_auditoria_iot` (
  `id_auditoria` INT NOT NULL AUTO_INCREMENT,
  `actor` VARCHAR(80) COLLATE utf8mb4_unicode_ci NOT NULL,
  `rol` VARCHAR(30) COLLATE utf8mb4_unicode_ci NOT NULL,
  `accion` VARCHAR(80) COLLATE utf8mb4_unicode_ci NOT NULL,
  `recurso` VARCHAR(120) COLLATE utf8mb4_unicode_ci NOT NULL,
  `resultado` VARCHAR(160) COLLATE utf8mb4_unicode_ci NOT NULL,
  `fecha_evento` TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`id_auditoria`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Usuarios y roles iniciales (Idempotente)
INSERT INTO `usuarios_iot` (`id_usuario`, `nombre`, `correo`, `rol`, `permisos`, `activo`) VALUES
  (100, 'Administrador General (Empresa IoT)', 'admin@cat320-iot.mx', 'ADMIN', 'CRUD Inventario, Clientes, Asignaciones, Editor DEMO Fábrica, Auditoría Global', 1),
  (101, 'Claudia América Hernández (Comprador A)', 'claudia.comprador@empresa-iot.mx', 'USUARIO', 'Control Total y Monitoreo de Mis Vehículos (#1), Editor DEMO Propia (1-4 pasos)', 1),
  (102, 'Constructora Industrial del Centro (Comprador B)', 'operaciones@constructora-centro.mx', 'USUARIO', 'Control y Editor DEMO solo en sus unidades asignadas (Aislamiento BOLA/IDOR)', 1),
  (103, 'Visitante Sala de Exhibición (Prospecto)', 'exhibicion@cat320-iot.mx', 'INVITADO', 'Catálogo Público y Prueba Temporal Exclusiva 60s (Orugas #9,#11,#2,#4 + Luz #8)', 1)
ON DUPLICATE KEY UPDATE
  `nombre` = VALUES(`nombre`),
  `rol` = VALUES(`rol`),
  `permisos` = VALUES(`permisos`);

-- Asignación inicial de la Excavadora #1 a Claudia América Hernández (#101)
INSERT INTO `asignaciones_vehiculo` (`id_vehiculo`, `id_usuario`, `estado_comercial`, `consentimiento_soporte`, `habilitado_exhibicion`)
SELECT 1, 101, 'Vendido / Asignado', 1, 1
FROM `vehiculos` WHERE `id_vehiculo` = 1
ON DUPLICATE KEY UPDATE
  `habilitado_exhibicion` = VALUES(`habilitado_exhibicion`);

-- Limpieza de pasos duplicados (orden 4) en demo_movimientos para cumplir límite estricto 1..4 pasos
DELETE FROM `demo_movimientos` WHERE `id_demo_movimiento` IN (1, 2, 3);

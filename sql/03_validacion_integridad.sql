-- =============================================================================
-- ARCHIVO: 03_validacion_integridad.sql
-- PROPÓSITO: Verificar en MySQL (Local o Amazon RDS) la existencia de las
--            6 tablas, los 21 Stored Procedures, los conteos exactos de datos
--            recuperados y la ausencia total de claves foráneas huérfanas.
-- =============================================================================

USE `vehiculo_iot`;

-- 1. Verificar existencia de las 6 tablas
SELECT
    COUNT(*) AS total_tablas,
    GROUP_CONCAT(table_name ORDER BY table_name SEPARATOR ', ') AS tablas_encontradas
FROM information_schema.tables
WHERE table_schema = 'vehiculo_iot'
  AND table_type = 'BASE TABLE';

-- 2. Verificar existencia de los 21 procedimientos almacenados
SELECT
    COUNT(*) AS total_procedimientos
FROM information_schema.routines
WHERE routine_schema = 'vehiculo_iot'
  AND routine_type = 'PROCEDURE';

SELECT
    routine_name AS procedimiento,
    created,
    last_altered
FROM information_schema.routines
WHERE routine_schema = 'vehiculo_iot'
  AND routine_type = 'PROCEDURE'
ORDER BY routine_name ASC;

-- 3. Verificar conteo de registros en cada tabla
SELECT
    (SELECT COUNT(*) FROM vehiculos)          AS total_vehiculos,
    (SELECT COUNT(*) FROM comandos)           AS total_comandos,
    (SELECT COUNT(*) FROM demos)              AS total_demos,
    (SELECT COUNT(*) FROM demo_movimientos)   AS total_demo_movimientos,
    (SELECT COUNT(*) FROM estado_vehiculo)    AS total_estado_vehiculo,
    (SELECT COUNT(*) FROM historial_comandos) AS total_historial_comandos;

-- 4. Verificar que NO existan movimientos huérfanos en `demo_movimientos`
SELECT
    COUNT(*) AS movimientos_huerfanos_sin_demo
FROM demo_movimientos dm
LEFT JOIN demos d ON d.id_demo = dm.id_demo
WHERE d.id_demo IS NULL;

SELECT
    COUNT(*) AS movimientos_huerfanos_sin_comando
FROM demo_movimientos dm
LEFT JOIN comandos c ON c.id_comando = dm.id_comando
WHERE c.id_comando IS NULL;

-- 5. Verificar resumen de las 3 DEMOs y sus 5 movimientos cada una
SELECT
    d.id_demo,
    d.nombre,
    d.activo,
    COUNT(dm.id_demo_movimiento) AS cantidad_movimientos
FROM demos d
LEFT JOIN demo_movimientos dm ON dm.id_demo = d.id_demo
GROUP BY d.id_demo, d.nombre, d.activo
ORDER BY d.id_demo ASC;

-- 6. Verificar correspondencia exacta de id_comando = 3 y id_comando = 13 y ausencia de duplicados
SELECT id_comando, nombre, codigo, activo
FROM comandos
WHERE id_vehiculo = 1 AND id_comando IN (3, 13)
ORDER BY id_comando ASC;

SELECT
    (SELECT COUNT(*) FROM (
        SELECT id_vehiculo, codigo FROM comandos GROUP BY id_vehiculo, codigo HAVING COUNT(*) > 1
    ) dup_cod) AS codigos_duplicados,
    (SELECT COUNT(*) FROM (
        SELECT id_vehiculo, nombre FROM comandos GROUP BY id_vehiculo, nombre HAVING COUNT(*) > 1
    ) dup_nom) AS nombres_duplicados;

-- 7. Verificar ausencia de huérfanos en estado_vehiculo e historial_comandos
SELECT
    (SELECT COUNT(*) FROM estado_vehiculo ev LEFT JOIN vehiculos v ON v.id_vehiculo = ev.id_vehiculo LEFT JOIN comandos c ON c.id_comando = ev.id_comando WHERE v.id_vehiculo IS NULL OR c.id_comando IS NULL) AS estado_huerfanos,
    (SELECT COUNT(*) FROM historial_comandos h LEFT JOIN vehiculos v ON v.id_vehiculo = h.id_vehiculo LEFT JOIN comandos c ON c.id_comando = h.id_comando WHERE v.id_vehiculo IS NULL OR c.id_comando IS NULL) AS historial_huerfanos;

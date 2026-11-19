# ==============================================================================
# SCRIPT: limpiar_base_excavadora.py
# PROPÓSITO: Limpiar por completo la base de datos vehiculo_iot en Amazon RDS
#            y registrar únicamente el vehículo ID=1 ("Excavadora IoT") con sus
#            12 movimientos (ID=1 al 12) y 3 demostraciones completas (ID=1 al 3).
# ==============================================================================

import json
from database import Database

MOVIMIENTOS_EXCAVADORA = [
    (1, "Mover brazo arriba", 1, "Subir el brazo principal de la excavadora"),
    (2, "Girar izquierda", 2, "Girar las orugas hacia la izquierda"),
    (3, "Mover brazo abajo", 3, "Bajar el brazo principal de la excavadora"),
    (4, "Girar derecha", 4, "Girar las orugas hacia la derecha"),
    (5, "Velocidad", 5, "Cambiar nivel de velocidad de la excavadora"),
    (6, "Sonido", 6, "Activar o desactivar sonido de motor y bocina"),
    (7, "Demo", 7, "Modo demostracion de la excavadora"),
    (8, "Luz", 8, "Encender o apagar luces de trabajo"),
    (9, "Avanzar", 9, "Desplazar la excavadora hacia adelante"),
    (10, "Girar torre izquierda", 10, "Rotar la cabina/torre hacia la izquierda"),
    (11, "Retroceder", 11, "Desplazar la excavadora hacia atras"),
    (12, "Girar torre derecha", 12, "Rotar la cabina/torre hacia la derecha"),
]

# Cada demo: (id_demo_esperado, nombre, descripcion, [(id_comando, orden, duracion_ms, pausa_ms), ...])
DEMOS_EXCAVADORA = [
    (
        1,
        "Ciclo de Excavacion",
        "Secuencia de excavacion: encender luz, avanzar, bajar brazo, subir brazo y girar torre",
        [
            (8, 1, 1000, 500),   # Luz
            (9, 2, 2000, 500),   # Avanzar
            (3, 3, 2000, 500),   # Mover brazo abajo
            (1, 4, 2000, 500),   # Mover brazo arriba
            (10, 5, 1500, 500),  # Girar torre izquierda
        ],
    ),
    (
        2,
        "Recorrido y Giro de Orugas",
        "Maniobra de desplazamiento: sonido, avanzar, girar izquierda, girar derecha y retroceder",
        [
            (6, 1, 1000, 500),   # Sonido
            (9, 2, 2000, 500),   # Avanzar
            (2, 3, 1500, 500),   # Girar izquierda
            (4, 4, 1500, 500),   # Girar derecha
            (11, 5, 2000, 500),  # Retroceder
        ],
    ),
    (
        3,
        "Inspeccion Completa de Torre y Brazo",
        "Prueba de rotacion de torre, control de brazo, velocidad y luces",
        [
            (8, 1, 1000, 400),   # Luz
            (5, 2, 1000, 400),   # Velocidad
            (10, 3, 1500, 500),  # Girar torre izquierda
            (12, 4, 1500, 500),  # Girar torre derecha
            (1, 5, 1500, 500),   # Mover brazo arriba
            (3, 6, 1500, 500),   # Mover brazo abajo
        ],
    ),
]


def limpiar_y_cargar():
    db = Database()
    conn = db._connect()
    cur = conn.cursor(dictionary=True)
    try:
        print("1. Limpiando tablas existentes y reiniciando contadores AUTO_INCREMENT...")
        cur.execute("SET FOREIGN_KEY_CHECKS = 0")
        for tabla in [
            "demo_movimientos",
            "demos",
            "historial_comandos",
            "estado_vehiculo",
            "comandos",
            "vehiculos",
        ]:
            cur.execute(f"DELETE FROM {tabla}")
            cur.execute(f"ALTER TABLE {tabla} AUTO_INCREMENT = 1")
        cur.execute("SET FOREIGN_KEY_CHECKS = 1")

        print("2. Registrando vehiculo ID=1 ('Excavadora IoT')...")
        cur.callproc(
            "sp_crear_vehiculo",
            [
                "Excavadora IoT",
                "CAT-320",
                "Excavadora IoT con 12 movimientos",
                "ESP8266",
            ],
        )
        db._collect_results(cur)

        print("3. Registrando los 12 movimientos con ID del 1 al 12...")
        for _, nombre, codigo, descripcion in MOVIMIENTOS_EXCAVADORA:
            cur.callproc("sp_crear_comando", [1, nombre, codigo, descripcion])
            db._collect_results(cur)

        print("4. Registrando 3 demos diferentes con sus secuencias de movimientos...")
        for id_demo, nombre_demo, desc_demo, pasos in DEMOS_EXCAVADORA:
            cur.callproc("sp_crear_demo", [1, nombre_demo, desc_demo])
            db._collect_results(cur)
            for id_cmd, orden, duracion_ms, pausa_ms in pasos:
                cur.callproc(
                    "sp_agregar_movimiento_demo",
                    [id_demo, id_cmd, orden, duracion_ms, pausa_ms],
                )
                db._collect_results(cur)

        print("5. Inicializando estado del vehiculo ID=1...")
        cur.callproc("sp_actualizar_estado", [1, 9, 1])
        db._collect_results(cur)

        conn.commit()
        print("Transaccion confirmada exitosamente en Amazon RDS!\n")
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()
        conn.close()

    vehiculos = db.ejecutar_sp("sp_obtener_vehiculos")["resultados"]
    comandos = db.ejecutar_sp("sp_obtener_comandos_vehiculo", [1])["resultados"]
    demos = db.ejecutar_sp("sp_obtener_demos", [1])["resultados"]

    print(f"=== VEHICULOS EN LA BASE ({len(vehiculos)}) ===")
    for v in vehiculos:
        print(" ", json.dumps(v, ensure_ascii=False))

    print(f"\n=== COMANDOS DEL VEHICULO 1 ({len(comandos)}) ===")
    for c in comandos:
        print(f"  ID={c['id_comando']} | Codigo={c['codigo']} | Nombre={c['nombre']}")

    print(f"\n=== DEMOS DEL VEHICULO 1 ({len(demos)}) ===")
    for d in demos:
        did = d["id_demo"]
        movs = db.ejecutar_sp("sp_obtener_movimientos_demo", [did])["resultados"]
        print(f"  Demo ID={did}: {d['nombre']} ({len(movs)} movimientos)")
        for m in movs:
            print(
                f"    Orden {m['orden']}: [{m['id_comando']}] {m['comando']} "
                f"(duracion={m['duracion_ms']}ms, pausa={m['pausa_ms']}ms)"
            )


if __name__ == "__main__":
    limpiar_y_cargar()

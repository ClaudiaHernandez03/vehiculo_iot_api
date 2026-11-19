# ==============================================================================
# MÓDULO: database.py
# PROPÓSITO: Encapsular mediante Programación Orientada a Objetos (clase Database)
#            toda la comunicación con MySQL en Amazon RDS, la gestión del ciclo
#            de vida de conexiones/transacciones y la ejecución de los
#            21 Procedimientos Almacenados (Stored Procedures) de vehiculo_iot.
# ==============================================================================

from contextlib import contextmanager
from typing import Any
import mysql.connector

from settings import Settings, settings


class Database:
    """Repositorio POO de acceso a Amazon RDS mediante los 21 Stored Procedures de vehiculo_iot."""

    # Lista blanca con los 21 procedimientos almacenados permitidos.
    # Evita que se ejecute cualquier procedimiento no autorizado.
    SP_PERMITIDOS = {
        "sp_actualizar_comando", "sp_actualizar_demo", "sp_actualizar_estado",
        "sp_actualizar_movimiento_demo", "sp_actualizar_vehiculo",
        "sp_agregar_movimiento_demo", "sp_crear_comando", "sp_crear_demo",
        "sp_crear_vehiculo", "sp_eliminar_comando", "sp_eliminar_demo",
        "sp_eliminar_movimiento_demo", "sp_eliminar_vehiculo",
        "sp_obtener_comandos_vehiculo", "sp_obtener_demos", "sp_obtener_estado",
        "sp_obtener_historial", "sp_obtener_movimientos_demo",
        "sp_obtener_vehiculo", "sp_obtener_vehiculos", "sp_registrar_historial",
    }

    def __init__(self, config: Settings = settings) -> None:
        self.settings = config

    def _connect(self):
        """Abre una conexión nueva hacia MySQL en Amazon RDS con autocommit desactivado
        para manejar transacciones manualmente (COMMIT / ROLLBACK)."""
        return mysql.connector.connect(**self.settings.get_db_config())

    @contextmanager
    def _cursor_session(self, commit: bool = False, dictionary: bool = True):
        """Administra de forma centralizada el ciclo de vida de la conexión y el cursor,
        garantizando COMMIT o ROLLBACK transaccional y el cierre seguro de recursos."""
        conn = self._connect()
        cursor = conn.cursor(dictionary=dictionary)
        try:
            yield cursor
            if commit:
                conn.commit()
            else:
                conn.rollback()
        except Exception:
            conn.rollback()
            raise
        finally:
            cursor.close()
            conn.close()

    @staticmethod
    def _json_safe(row: dict[str, Any]) -> dict[str, Any]:
        """Convierte objetos de fecha/hora de MySQL a texto ISO-8601 para que FastAPI
        pueda devolverlos en formato JSON sin errores."""
        return {k: v.isoformat() if hasattr(v, "isoformat") else v
                for k, v in row.items()}

    @classmethod
    def _collect_results(cls, cursor):
        """Recorre y extrae todas las filas devueltas por un Stored Procedure."""
        rows = []
        for result in cursor.stored_results():
            try:
                if result.with_rows:
                    rows.extend(cls._json_safe(r) for r in result.fetchall())
            finally:
                result.close()
        return rows

    def verificar_conexion(self) -> dict[str, Any]:
        """Comprueba de manera segura la disponibilidad de Amazon RDS sin exponer credenciales."""
        with self._cursor_session(commit=False, dictionary=False) as cursor:
            cursor.execute("SELECT DATABASE(), VERSION()")
            resultado = cursor.fetchone()
            return {
                "ok": True,
                "base_datos": resultado[0] if resultado else None,
                "version_mysql": resultado[1] if resultado else None,
            }

    def ejecutar_sp(self, nombre, parametros=None, escritura=False):
        """Ejecuta un SP permitido. Los parámetros deben ir en el orden de MySQL.
        Si 'escritura' es True guarda los cambios con COMMIT; si ocurre un error hace ROLLBACK."""
        if nombre not in self.SP_PERMITIDOS:
            raise ValueError("Stored Procedure no permitido.")
        with self._cursor_session(commit=escritura, dictionary=True) as cursor:
            cursor.callproc(nombre, parametros or [])
            filas = self._collect_results(cursor)
            return {"ok": True, "procedimiento": nombre, "resultados": filas}

    def registrar_movimiento(self, id_vehiculo, id_comando, origen):
        """Valida vehículo/comando y guarda estado e historial en una sola transacción:
        1. Verifica que el vehículo exista y esté activo.
        2. Verifica que el comando exista, esté activo y pertenezca a ese vehículo.
        3. Ejecuta sp_actualizar_estado y sp_registrar_historial juntos antes de hacer COMMIT."""
        with self._cursor_session(commit=True, dictionary=True) as cursor:
            # 1. Validar que el vehículo exista y esté activo (bloqueando la fila durante la transacción)
            cursor.execute(
                "SELECT activo FROM vehiculos WHERE id_vehiculo=%s FOR UPDATE",
                (id_vehiculo,))
            vehiculo = cursor.fetchone()
            if vehiculo is None:
                raise LookupError("El vehículo no existe.")
            if not vehiculo["activo"]:
                raise ValueError("El vehículo está inactivo.")

            # 2. Validar que el comando pertenezca al vehículo y esté activo
            cursor.execute(
                """SELECT * FROM comandos
                   WHERE id_comando=%s AND id_vehiculo=%s FOR UPDATE""",
                (id_comando, id_vehiculo))
            comando = cursor.fetchone()
            if comando is None:
                raise ValueError("El comando no existe o no pertenece al vehículo.")
            if not comando["activo"]:
                raise ValueError("El comando está inactivo.")
            nombre_comando = comando.get("comando") or comando.get("nombre")
            codigo_comando = comando.get("codigo")

            # 3. Consultar si el vehículo estaba marcado como conectado
            cursor.execute(
                "SELECT conectado FROM estado_vehiculo WHERE id_vehiculo=%s FOR UPDATE",
                (id_vehiculo,))
            anterior = cursor.fetchone()
            conectado = bool(anterior["conectado"]) if anterior else False

            # 4. Actualizar el último estado y registrar en el historial dentro de la misma transacción
            cursor.callproc("sp_actualizar_estado",
                            [id_vehiculo, id_comando, conectado])
            self._collect_results(cursor)
            cursor.callproc("sp_registrar_historial",
                            [id_vehiculo, id_comando, origen])
            self._collect_results(cursor)
            id_historial = self._obtener_max_id_historial_en_cursor(cursor, id_vehiculo)
            return {
                "transaccion": "confirmada",
                "conectado": conectado,
                "id_comando": id_comando,
                "comando": nombre_comando,
                "codigo": codigo_comando,
                "id_historial": id_historial,
            }

    @staticmethod
    def _obtener_max_id_historial_en_cursor(cursor, id_vehiculo: int) -> int:
        """Obtiene el último id_historial (AUTO_INCREMENT de historial_comandos) asignado al vehículo.
        NOTA: id_historial NO equivale a epoca_orden ni a orden_id; el esquema actual de RDS no
        almacena orden_id, por lo que este valor se usa únicamente como referencia de consistencia."""
        cursor.execute(
            "SELECT COALESCE(MAX(id_historial), 0) AS max_id FROM historial_comandos WHERE id_vehiculo=%s",
            (id_vehiculo,),
        )
        fila = cursor.fetchone()
        if not fila or fila.get("max_id") is None:
            return 0
        return int(fila["max_id"])

    def obtener_max_id_historial(self, id_vehiculo: int) -> int:
        """Consulta de solo lectura para obtener el máximo id_historial del vehículo en RDS.
        No debe usarse como epoca_orden ni como orden_id; sirve para detectar discrepancias
        entre los registros históricos de RDS y el archivo de secuencia local."""
        with self._cursor_session(commit=False, dictionary=True) as cursor:
            return self._obtener_max_id_historial_en_cursor(cursor, id_vehiculo)

    def _obtener_o_registrar_comando_detener_en_cursor(
        self, cursor, id_vehiculo: int
    ) -> dict[str, Any]:
        """Localiza de forma idempotente el comando DETENER (codigo=0) del vehículo
        o lo registra mediante sp_crear_comando si aún no existe, recuperando su ID real."""
        cursor.execute(
            "SELECT * FROM comandos WHERE id_vehiculo=%s FOR UPDATE",
            (id_vehiculo,),
        )
        comandos_vehiculo = cursor.fetchall() or []

        candidatos = []
        for cmd in comandos_vehiculo:
            if int(cmd.get("id_vehiculo", id_vehiculo)) != int(id_vehiculo):
                continue
            codigo_val = cmd.get("codigo")
            nombre_val = str(cmd.get("nombre") or cmd.get("comando") or "").strip().upper()
            if (codigo_val is not None and int(codigo_val) == 0) or nombre_val == "DETENER":
                prioridad_codigo = 0 if (codigo_val is not None and int(codigo_val) == 0) else 1
                candidatos.append((prioridad_codigo, int(cmd["id_comando"]), cmd))

        if candidatos:
            candidatos.sort(key=lambda item: (item[0], item[1]))
            encontrado = candidatos[0][2]
            id_comando = int(encontrado["id_comando"])
            nombre_cmd = encontrado.get("nombre") or encontrado.get("comando") or "Detener"
            desc_cmd = (
                encontrado.get("descripcion")
                or "Detener inmediatamente todos los movimientos del vehículo"
            )
            if not encontrado.get("activo", 1):
                cursor.callproc(
                    "sp_actualizar_comando",
                    [id_comando, nombre_cmd, 0, desc_cmd, 1],
                )
                self._collect_results(cursor)
            return {
                "id_comando": id_comando,
                "comando": nombre_cmd,
                "codigo": 0,
                "creado": False,
            }

        descripcion_detener = "Detener inmediatamente todos los movimientos del vehículo"
        cursor.callproc(
            "sp_crear_comando",
            [id_vehiculo, "Detener", 0, descripcion_detener],
        )
        filas_sp = self._collect_results(cursor)

        cursor.execute(
            "SELECT * FROM comandos WHERE id_vehiculo=%s FOR UPDATE",
            (id_vehiculo,),
        )
        comandos_post = cursor.fetchall() or []
        creado_row = None
        for cmd in comandos_post:
            if int(cmd.get("id_vehiculo", id_vehiculo)) != int(id_vehiculo):
                continue
            codigo_val = cmd.get("codigo")
            nombre_val = str(cmd.get("nombre") or cmd.get("comando") or "").strip().upper()
            if (codigo_val is not None and int(codigo_val) == 0) or nombre_val == "DETENER":
                creado_row = cmd
                break

        if creado_row is None and filas_sp:
            posible = filas_sp[0]
            if "id_comando" in posible:
                creado_row = {
                    "id_comando": int(posible["id_comando"]),
                    "id_vehiculo": id_vehiculo,
                    "nombre": posible.get("nombre") or posible.get("comando") or "Detener",
                    "codigo": 0,
                }

        if creado_row is None or int(creado_row.get("id_vehiculo", id_vehiculo)) != int(id_vehiculo):
            raise RuntimeError(
                f"No fue posible recuperar el id_comando real de DETENER para el vehículo {id_vehiculo}."
            )

        return {
            "id_comando": int(creado_row["id_comando"]),
            "comando": creado_row.get("nombre") or creado_row.get("comando") or "Detener",
            "codigo": 0,
            "creado": True,
        }

    def obtener_o_registrar_comando_detener(self, id_vehiculo: int) -> dict[str, Any]:
        """Obtiene o crea de forma transaccional e idempotente el comando DETENER del vehículo."""
        with self._cursor_session(commit=True, dictionary=True) as cursor:
            cursor.execute(
                "SELECT activo FROM vehiculos WHERE id_vehiculo=%s FOR UPDATE",
                (id_vehiculo,),
            )
            vehiculo = cursor.fetchone()
            if vehiculo is None:
                raise LookupError("El vehículo no existe.")
            if not vehiculo["activo"]:
                raise ValueError("El vehículo está inactivo.")
            return self._obtener_o_registrar_comando_detener_en_cursor(cursor, id_vehiculo)

    def registrar_detencion(self, id_vehiculo: int, origen: str = "API") -> dict[str, Any]:
        """Valida que el vehículo exista y esté activo, obtiene dinámicamente su comando DETENER
        y actualiza estado_vehiculo e historial_comandos en una sola transacción."""
        with self._cursor_session(commit=True, dictionary=True) as cursor:
            cursor.execute(
                "SELECT activo FROM vehiculos WHERE id_vehiculo=%s FOR UPDATE",
                (id_vehiculo,),
            )
            vehiculo = cursor.fetchone()
            if vehiculo is None:
                raise LookupError("El vehículo no existe.")
            if not vehiculo["activo"]:
                raise ValueError("El vehículo está inactivo.")

            cmd_detener = self._obtener_o_registrar_comando_detener_en_cursor(
                cursor, id_vehiculo
            )
            id_comando = cmd_detener["id_comando"]
            nombre_comando = cmd_detener["comando"]

            cursor.execute(
                "SELECT conectado FROM estado_vehiculo WHERE id_vehiculo=%s FOR UPDATE",
                (id_vehiculo,),
            )
            anterior = cursor.fetchone()
            conectado = bool(anterior["conectado"]) if anterior else False

            cursor.callproc(
                "sp_actualizar_estado",
                [id_vehiculo, id_comando, int(conectado)],
            )
            self._collect_results(cursor)
            cursor.callproc(
                "sp_registrar_historial",
                [id_vehiculo, id_comando, (origen or "API")[:30]],
            )
            self._collect_results(cursor)
            id_historial = self._obtener_max_id_historial_en_cursor(cursor, id_vehiculo)
            return {
                "transaccion": "confirmada",
                "conectado": conectado,
                "id_comando": id_comando,
                "comando": nombre_comando,
                "codigo": 0,
                "id_historial": id_historial,
                "comando_creado": cmd_detener["creado"],
            }

    def registrar_apagado_demo(self, id_vehiculo: int) -> dict[str, Any]:
        """Actualiza estado_vehiculo al comando DETENER (codigo=0) al finalizar o cancelar
        una DEMO, usando sp_actualizar_estado y SIN insertar en historial_comandos."""
        with self._cursor_session(commit=True, dictionary=True) as cursor:
            cursor.execute(
                "SELECT activo FROM vehiculos WHERE id_vehiculo=%s FOR UPDATE",
                (id_vehiculo,),
            )
            vehiculo = cursor.fetchone()
            if vehiculo is None:
                raise LookupError("El vehículo no existe.")
            cmd_detener = self._obtener_o_registrar_comando_detener_en_cursor(
                cursor, id_vehiculo
            )
            id_comando = cmd_detener["id_comando"]
            cursor.execute(
                "SELECT conectado FROM estado_vehiculo WHERE id_vehiculo=%s FOR UPDATE",
                (id_vehiculo,),
            )
            anterior = cursor.fetchone()
            conectado = (
                bool(anterior["conectado"])
                if anterior and anterior.get("conectado") is not None
                else True
            )
            cursor.callproc(
                "sp_actualizar_estado", [id_vehiculo, id_comando, int(conectado)]
            )
            self._collect_results(cursor)
            return {
                "transaccion": "confirmada",
                "id_vehiculo": id_vehiculo,
                "id_comando": id_comando,
                "comando": cmd_detener["comando"],
                "codigo": 0,
            }

    def obtener_paso_demo_por_id(
        self, id_demo_movimiento: int
    ) -> dict[str, Any] | None:
        """Obtiene la fila de demo_movimientos correspondiente a id_demo_movimiento."""
        with self._cursor_session(commit=False, dictionary=True) as cursor:
            cursor.execute(
                "SELECT id_demo_movimiento, id_demo, id_comando, orden, duracion_ms, pausa_ms "
                "FROM demo_movimientos WHERE id_demo_movimiento = %s",
                (id_demo_movimiento,),
            )
            return cursor.fetchone()

    def obtener_historial(self, id_vehiculo):
        """Consulta sp_obtener_historial y devuelve máximo los 10 movimientos más recientes."""
        return self.ejecutar_sp("sp_obtener_historial", [id_vehiculo])["resultados"][:10]

    def obtener_estado(self, id_vehiculo):
        """Consulta el último estado del vehículo mediante sp_obtener_estado."""
        return self.ejecutar_sp("sp_obtener_estado", [id_vehiculo])["resultados"]

    def obtener_estados_globales(self) -> list[dict[str, Any]]:
        """Consulta todos los vehículos y su último estado actual en RDS para el monitor WebSocket global."""
        vehiculos = self.ejecutar_sp("sp_obtener_vehiculos")["resultados"]
        resumen = []
        for v in vehiculos:
            vid = v.get("id_vehiculo")
            estado = self.obtener_estado(vid) if vid is not None else []
            resumen.append({
                "id_vehiculo": vid,
                "nombre": v.get("nombre"),
                "modelo": v.get("modelo"),
                "activo": v.get("activo"),
                "estado": estado,
            })
        return resumen

    def obtener_info_demo(self, id_demo: int) -> dict[str, Any] | None:
        """Obtiene la información básica de una DEMO (nombre, vehículo asociado y si está activa)."""
        with self._cursor_session(commit=False, dictionary=True) as cursor:
            cursor.execute(
                "SELECT id_demo, id_vehiculo, nombre, descripcion, activo FROM demos WHERE id_demo = %s",
                (id_demo,),
            )
            return cursor.fetchone()

    def obtener_vehiculo_activo(self, id_vehiculo: int) -> dict[str, Any] | None:
        """Verifica si el vehículo existe y está activo antes de iniciar una DEMO."""
        with self._cursor_session(commit=False, dictionary=True) as cursor:
            cursor.execute(
                "SELECT id_vehiculo, nombre, activo FROM vehiculos WHERE id_vehiculo = %s",
                (id_vehiculo,),
            )
            return cursor.fetchone()

    def obtener_movimientos_demo(self, id_demo: int) -> list[dict[str, Any]]:
        """Obtiene la lista de pasos de una DEMO mediante sp_obtener_movimientos_demo."""
        return self.ejecutar_sp("sp_obtener_movimientos_demo", [id_demo])["resultados"]

    def actualizar_estado_paso_demo(self, id_vehiculo: int, id_comando: int) -> dict[str, Any]:
        """Actualiza el último estado del vehículo en RDS durante cada paso de una DEMO
        usando sp_actualizar_estado, pero SIN insertar en historial_comandos."""
        with self._cursor_session(commit=True, dictionary=True) as cursor:
            cursor.execute(
                "SELECT conectado FROM estado_vehiculo WHERE id_vehiculo=%s FOR UPDATE",
                (id_vehiculo,),
            )
            anterior = cursor.fetchone()
            conectado = bool(anterior["conectado"]) if anterior and anterior.get("conectado") is not None else True
            cursor.callproc("sp_actualizar_estado", [id_vehiculo, id_comando, int(conectado)])
            self._collect_results(cursor)
            return {"transaccion": "confirmada", "id_vehiculo": id_vehiculo, "id_comando": id_comando}

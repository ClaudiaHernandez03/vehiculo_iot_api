# ==============================================================================
# MÓDULO: database.py
# PROPÓSITO: Encapsular mediante Programación Orientada a Objetos (clase Database)
#            toda la comunicación con MySQL en Amazon RDS y la ejecución de los
#            21 Procedimientos Almacenados (Stored Procedures) de vehiculo_iot.
# ==============================================================================

import os
from typing import Any
import mysql.connector
from dotenv import load_dotenv

# Carga las credenciales de RDS desde el archivo .env
load_dotenv()


class Database:
    """Acceso a Amazon RDS mediante los Stored Procedures de vehiculo_iot."""

    # Lista blanca con los 21 procedimientos almacenados permitidos en el examen.
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

    def _connect(self):
        """Abre una conexión nueva hacia MySQL en Amazon RDS con autocommit desactivado
        para poder manejar transacciones manualmente (COMMIT / ROLLBACK)."""
        return mysql.connector.connect(
            host=os.environ["DB_HOST"],
            port=int(os.getenv("DB_PORT", "3306")),
            user=os.environ["DB_USER"],
            password=os.environ["DB_PASSWORD"],
            database=os.getenv("DB_NAME", "vehiculo_iot"),
            connection_timeout=10,
            autocommit=False,
        )

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

    def ejecutar_sp(self, nombre, parametros=None, escritura=False):
        """Ejecuta un SP permitido. Los parámetros deben ir en el orden de MySQL.
        Si 'escritura' es True guarda los cambios con COMMIT; si ocurre un error hace ROLLBACK."""
        if nombre not in self.SP_PERMITIDOS:
            raise ValueError("Stored Procedure no permitido.")
        conn = self._connect()
        cursor = conn.cursor(dictionary=True)
        try:
            cursor.callproc(nombre, parametros or [])
            filas = self._collect_results(cursor)
            if escritura:
                conn.commit()
            else:
                conn.rollback()
            return {"ok": True, "procedimiento": nombre, "resultados": filas}
        except Exception:
            conn.rollback()
            raise
        finally:
            cursor.close()
            conn.close()

    def registrar_movimiento(self, id_vehiculo, id_comando, origen):
        """Valida vehículo/comando y guarda estado e historial en una sola transacción:
        1. Verifica que el vehículo exista y esté activo.
        2. Verifica que el comando exista, esté activo y pertenezca a ese vehículo.
        3. Ejecuta sp_actualizar_estado y sp_registrar_historial juntos antes de hacer COMMIT."""
        conn = self._connect()
        cursor = conn.cursor(dictionary=True)
        try:
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
                """SELECT activo FROM comandos
                   WHERE id_comando=%s AND id_vehiculo=%s FOR UPDATE""",
                (id_comando, id_vehiculo))
            comando = cursor.fetchone()
            if comando is None:
                raise ValueError("El comando no existe o no pertenece al vehículo.")
            if not comando["activo"]:
                raise ValueError("El comando está inactivo.")

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
            conn.commit()
            return {"transaccion": "confirmada", "conectado": conectado}
        except Exception:
            conn.rollback()
            raise
        finally:
            cursor.close()
            conn.close()

    def obtener_historial(self, id_vehiculo):
        """Consulta sp_obtener_historial y devuelve máximo los 10 movimientos más recientes."""
        return self.ejecutar_sp("sp_obtener_historial", [id_vehiculo])["resultados"][:10]

    def obtener_estado(self, id_vehiculo):
        """Consulta el último estado del vehículo mediante sp_obtener_estado."""
        return self.ejecutar_sp("sp_obtener_estado", [id_vehiculo])["resultados"]

    def obtener_info_demo(self, id_demo: int) -> dict[str, Any] | None:
        """Obtiene la información básica de una DEMO (nombre, vehículo asociado y si está activa)."""
        conn = self._connect()
        cursor = conn.cursor(dictionary=True)
        try:
            cursor.execute(
                "SELECT id_demo, id_vehiculo, nombre, descripcion, activo FROM demos WHERE id_demo = %s",
                (id_demo,),
            )
            return cursor.fetchone()
        finally:
            cursor.close()
            conn.close()

    def obtener_vehiculo_activo(self, id_vehiculo: int) -> dict[str, Any] | None:
        """Verifica si el vehículo existe y está activo antes de iniciar una DEMO."""
        conn = self._connect()
        cursor = conn.cursor(dictionary=True)
        try:
            cursor.execute(
                "SELECT id_vehiculo, nombre, activo FROM vehiculos WHERE id_vehiculo = %s",
                (id_vehiculo,),
            )
            return cursor.fetchone()
        finally:
            cursor.close()
            conn.close()

    def obtener_movimientos_demo(self, id_demo: int) -> list[dict[str, Any]]:
        """Obtiene la lista de pasos de una DEMO mediante sp_obtener_movimientos_demo."""
        return self.ejecutar_sp("sp_obtener_movimientos_demo", [id_demo])["resultados"]

    def actualizar_estado_paso_demo(self, id_vehiculo: int, id_comando: int) -> dict[str, Any]:
        """Actualiza el último estado del vehículo en RDS durante cada paso de una DEMO
        usando sp_actualizar_estado, pero SIN insertar en historial_comandos."""
        conn = self._connect()
        cursor = conn.cursor(dictionary=True)
        try:
            cursor.execute(
                "SELECT conectado FROM estado_vehiculo WHERE id_vehiculo=%s FOR UPDATE",
                (id_vehiculo,),
            )
            anterior = cursor.fetchone()
            conectado = bool(anterior["conectado"]) if anterior and anterior.get("conectado") is not None else True
            cursor.callproc("sp_actualizar_estado", [id_vehiculo, id_comando, int(conectado)])
            self._collect_results(cursor)
            conn.commit()
            return {"transaccion": "confirmada", "id_vehiculo": id_vehiculo, "id_comando": id_comando}
        except Exception:
            conn.rollback()
            raise
        finally:
            cursor.close()
            conn.close()

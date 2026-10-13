
import os
from typing import Any
import mysql.connector


class Database:
    """Acceso a Amazon RDS mediante los Stored Procedures de vehiculo_iot."""

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
        return {k: v.isoformat() if hasattr(v, "isoformat") else v
                for k, v in row.items()}

    @classmethod
    def _collect_results(cls, cursor):
        rows = []
        for result in cursor.stored_results():
            try:
                if result.with_rows:
                    rows.extend(cls._json_safe(r) for r in result.fetchall())
            finally:
                result.close()
        return rows

    def ejecutar_sp(self, nombre, parametros=None, escritura=False):
        """Ejecuta un SP permitido. Los parámetros deben ir en el orden de MySQL."""
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
        """Valida vehículo/comando y guarda estado e historial en una transacción."""
        conn = self._connect()
        cursor = conn.cursor(dictionary=True)
        try:
            cursor.execute(
                "SELECT activo FROM vehiculos WHERE id_vehiculo=%s FOR UPDATE",
                (id_vehiculo,))
            vehiculo = cursor.fetchone()
            if vehiculo is None:
                raise LookupError("El vehículo no existe.")
            if not vehiculo["activo"]:
                raise ValueError("El vehículo está inactivo.")

            cursor.execute(
                """SELECT activo FROM comandos
                   WHERE id_comando=%s AND id_vehiculo=%s FOR UPDATE""",
                (id_comando, id_vehiculo))
            comando = cursor.fetchone()
            if comando is None:
                raise ValueError("El comando no existe o no pertenece al vehículo.")
            if not comando["activo"]:
                raise ValueError("El comando está inactivo.")

            cursor.execute(
                "SELECT conectado FROM estado_vehiculo WHERE id_vehiculo=%s FOR UPDATE",
                (id_vehiculo,))
            anterior = cursor.fetchone()
            conectado = bool(anterior["conectado"]) if anterior else False

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
        return self.ejecutar_sp("sp_obtener_historial", [id_vehiculo])["resultados"][:10]

    def obtener_estado(self, id_vehiculo):
        return self.ejecutar_sp("sp_obtener_estado", [id_vehiculo])["resultados"]

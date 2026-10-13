
import os
from typing import Any

import mysql.connector


class Database:
    """Acceso a Amazon RDS mediante Stored Procedures."""

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
    def _drain_results(cursor):
        """Libera los resultados de una llamada a un Stored Procedure."""
        try:
            for result in cursor.stored_results():
                result.close()
        except (AttributeError, mysql.connector.Error):
            pass

    def registrar_movimiento(
        self,
        id_vehiculo: int,
        id_comando: int,
        origen: str,
    ):
        """Valida y registra un movimiento individual en una transacción."""
        conn = self._connect()
        cursor = conn.cursor(dictionary=True)

        try:
            # Validar que el vehículo exista y esté activo.
            cursor.execute(
                """
                SELECT activo
                FROM vehiculos
                WHERE id_vehiculo = %s
                FOR UPDATE
                """,
                (id_vehiculo,),
            )
            vehiculo = cursor.fetchone()

            if vehiculo is None:
                raise LookupError("El vehículo no existe.")

            if not vehiculo["activo"]:
                raise ValueError("El vehículo está inactivo.")

            # Validar que el comando pertenezca al vehículo.
            cursor.execute(
                """
                SELECT activo
                FROM comandos
                WHERE id_comando = %s
                  AND id_vehiculo = %s
                FOR UPDATE
                """,
                (id_comando, id_vehiculo),
            )
            comando = cursor.fetchone()

            if comando is None:
                raise ValueError(
                    "El comando no existe o no pertenece al vehículo."
                )

            if not comando["activo"]:
                raise ValueError("El comando está inactivo.")

            # Conservar el estado de conexión conocido.
            cursor.execute(
                """
                SELECT conectado
                FROM estado_vehiculo
                WHERE id_vehiculo = %s
                FOR UPDATE
                """,
                (id_vehiculo,),
            )
            estado_anterior = cursor.fetchone()

            conectado = (
                bool(estado_anterior["conectado"])
                if estado_anterior is not None
                else False
            )

            # SP confirmado: tres parámetros.
            cursor.callproc(
                "sp_actualizar_estado",
                [id_vehiculo, id_comando, conectado],
            )
            self._drain_results(cursor)

            # SP confirmado: origen de máximo 30 caracteres.
            cursor.callproc(
                "sp_registrar_historial",
                [id_vehiculo, id_comando, origen],
            )
            self._drain_results(cursor)

            # Ambas operaciones se confirman juntas.
            conn.commit()

            return {
                "transaccion": "confirmada",
                "conectado": conectado,
            }

        except Exception:
            conn.rollback()
            raise

        finally:
            cursor.close()
            conn.close()

    def obtener_historial(
        self, id_vehiculo: int
    ) -> list[dict[str, Any]]:
        """Obtiene como máximo los diez movimientos más recientes."""
        conn = self._connect()
        cursor = conn.cursor(dictionary=True)

        try:
            cursor.callproc(
                "sp_obtener_historial",
                [id_vehiculo],
            )

            rows = []
            for result in cursor.stored_results():
                if result.with_rows:
                    rows.extend(result.fetchall())
                result.close()

            return [
                self._json_safe(row)
                for row in rows[:10]
            ]

        finally:
            cursor.close()
            conn.close()

    def obtener_estado(
        self, id_vehiculo: int
    ) -> list[dict[str, Any]]:
        """Consulta el último estado disponible del vehículo."""
        conn = self._connect()
        cursor = conn.cursor(dictionary=True)

        try:
            cursor.callproc(
                "sp_obtener_estado",
                [id_vehiculo],
            )

            rows = []
            for result in cursor.stored_results():
                if result.with_rows:
                    rows.extend(result.fetchall())
                result.close()

            return [
                self._json_safe(row)
                for row in rows
            ]

        finally:
            cursor.close()
            conn.close()

    @staticmethod
    def _json_safe(row):
        """Convierte fechas a cadenas compatibles con JSON."""
        return {
            key: value.isoformat()
            if hasattr(value, "isoformat")
            else value
            for key, value in row.items()
        }
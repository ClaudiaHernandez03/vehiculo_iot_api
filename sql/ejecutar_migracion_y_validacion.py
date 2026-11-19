"""
Script utilitario para ejecutar la reconstruccion de esquema/SPs (01),
la recuperacion idempotente de datos (02) y la validacion de integridad (03)
sobre la base de datos MySQL configurada en .env (local o Amazon RDS).
"""

import os
import re
import sys
from pathlib import Path

import mysql.connector
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
SQL_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")


def parse_sql_statements(sql_text: str) -> list[str]:
    """Divide un archivo SQL con bloques DELIMITER $$ ... DELIMITER ; en sentencias individuales."""
    statements: list[str] = []
    delimiter = ";"
    lines = sql_text.splitlines()
    buffer: list[str] = []

    for raw_line in lines:
        stripped = raw_line.strip()
        if not stripped or stripped.startswith("--"):
            continue
        if stripped.upper().startswith("DELIMITER "):
            if buffer:
                stmt = "\n".join(buffer).strip()
                if stmt:
                    statements.append(stmt)
                buffer = []
            delimiter = stripped.split(None, 1)[1].strip()
            continue

        if delimiter != ";":
            if stripped == delimiter or stripped.endswith(delimiter):
                line_without_delim = raw_line[: raw_line.rfind(delimiter)].rstrip()
                if line_without_delim:
                    buffer.append(line_without_delim)
                stmt = "\n".join(buffer).strip()
                if stmt:
                    statements.append(stmt)
                buffer = []
            else:
                buffer.append(raw_line)
        else:
            buffer.append(raw_line)
            joined = "\n".join(buffer).strip()
            if joined.endswith(";"):
                stmt = joined[:-1].strip()
                if stmt:
                    statements.append(stmt)
                buffer = []

    if buffer:
        stmt = "\n".join(buffer).strip()
        if stmt:
            statements.append(stmt)

    return statements


def execute_sql_file(cursor, filepath: Path, print_results: bool = False) -> None:
    print(f"\n============================================================")
    print(f"Ejecutando: {filepath.name}")
    print(f"============================================================")
    sql_text = filepath.read_text(encoding="utf-8")
    statements = parse_sql_statements(sql_text)
    for idx, stmt in enumerate(statements, start=1):
        try:
            cursor.execute(stmt)
        except mysql.connector.Error as err:
            # En MySQL 8.4 local, los SPs importados con DEFINER='root'@'localhost'
            # requieren privilegio SYSTEM_USER (errno 1227) para hacer DROP desde 'admin',
            # y el CREATE subsecuente reporta que el SP ya existe (errno 1304).
            upper_stmt = stmt.upper().strip()
            if err.errno in (1227, 1304) and (
                upper_stmt.startswith("DROP PROCEDURE") or upper_stmt.startswith("CREATE PROCEDURE")
            ):
                continue
            raise
        while True:
            if cursor.with_rows:
                rows = cursor.fetchall()
                if print_results and rows is not None:
                    cols = [col[0] for col in cursor.description]
                    print(f"\n--- Resultado consulta #{idx}: {cols} ---")
                    for r in rows:
                        print("  ", dict(zip(cols, r)))
            if not cursor.nextset():
                break


def main() -> int:
    host = os.getenv("DB_HOST", "127.0.0.1").strip()
    port = int(os.getenv("DB_PORT", "3306"))
    user = os.getenv("DB_USER", "admin").strip()
    password = os.getenv("DB_PASSWORD", "")
    database = os.getenv("DB_NAME", "vehiculo_iot").strip()

    print(f"Conectando a MySQL en {host}:{port} (usuario={user}, esquema={database})...")
    conn = mysql.connector.connect(
        host=host,
        port=port,
        user=user,
        password=password,
        autocommit=True,
    )
    try:
        cursor = conn.cursor()
        execute_sql_file(
            cursor,
            SQL_DIR / "01_reconstruccion_completa_esquema_y_sps.sql",
            print_results=False,
        )
        execute_sql_file(
            cursor,
            SQL_DIR / "02_recuperacion_datos_y_migracion_idempotente.sql",
            print_results=False,
        )
        execute_sql_file(
            cursor,
            SQL_DIR / "03_validacion_integridad.sql",
            print_results=True,
        )
        cursor.close()
        print("\n[OK] Reconstruccion, migracion idempotente y validacion completadas con exito.")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())

# ==============================================================================
# MÓDULO: probar_conexion.py
# PROPÓSITO: Script independiente que utiliza la clase POO Database para
#            verificar que las variables del archivo .env permiten conectarse
#            correctamente a MySQL en Amazon RDS.
# ==============================================================================

import mysql.connector
from database import Database


def main() -> None:
    db = Database()
    try:
        info = db.verificar_conexion()
        print("CONEXIÓN EXITOSA A AMAZON RDS")
        print("Base de datos:", info.get("base_datos"))
        print("Versión de MySQL:", info.get("version_mysql"))
    except mysql.connector.Error as error:
        print("ERROR DE CONEXIÓN A AMAZON RDS")
        print("Código:", error.errno)
        print("Mensaje:", error.msg)


if __name__ == "__main__":
    main()

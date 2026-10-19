# ==============================================================================
# MÓDULO: probar_conexion.py
# PROPÓSITO: Script independiente para verificar que las variables del archivo
#            .env permiten conectarse correctamente a MySQL en Amazon RDS.
# ==============================================================================

import os
import mysql.connector
from dotenv import load_dotenv

# Cargar variables de entorno desde el archivo .env
load_dotenv()

try:
    # Intentar abrir conexión hacia la instancia de Amazon RDS
    conexion = mysql.connector.connect(
        host=os.getenv("DB_HOST"),
        port=int(os.getenv("DB_PORT", "3306")),
        user=os.getenv("DB_USER"),
        password=os.getenv("DB_PASSWORD"),
        database=os.getenv("DB_NAME"),
        connection_timeout=10
    )

    if conexion.is_connected():
        cursor = conexion.cursor()
        # Consultar el nombre de la base activa y la versión del motor MySQL
        cursor.execute("SELECT DATABASE(), VERSION()")
        resultado = cursor.fetchone()

        print("CONEXIÓN EXITOSA A AMAZON RDS")
        print("Base de datos:", resultado[0])
        print("Versión de MySQL:", resultado[1])

        cursor.close()
        conexion.close()

except mysql.connector.Error as error:
    print("ERROR DE CONEXIÓN A AMAZON RDS")
    print("Código:", error.errno)
    print("Mensaje:", error.msg)

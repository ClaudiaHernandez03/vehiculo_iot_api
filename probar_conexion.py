
import os
import mysql.connector
from dotenv import load_dotenv

load_dotenv()

try:
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

# ==============================================================================
# MÓDULO: settings.py
# PROPÓSITO: Cargar la configuración general desde el archivo .env para no
#            escribir contraseñas ni direcciones directamente en el código.
# ==============================================================================

import os
from dotenv import load_dotenv

# Carga las variables definidas en el archivo .env hacia el entorno del sistema
load_dotenv()


class Settings:
    """Clase POO que centraliza los parámetros de configuración de la API."""

    # Clave opcional para proteger endpoints de escritura mediante el header x-api-key
    api_key = os.getenv("API_KEY", "")

    # Datos de conexión hacia la base de datos MySQL en Amazon RDS
    db_host = os.getenv("DB_HOST", "")
    db_port = int(os.getenv("DB_PORT", "3306"))
    db_name = os.getenv("DB_NAME", "vehiculo_iot")


# Instancia única de configuración reutilizada por toda la aplicación
settings = Settings()

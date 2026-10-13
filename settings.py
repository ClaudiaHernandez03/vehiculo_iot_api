import os
from dotenv import load_dotenv

load_dotenv()


class Settings:
    api_key = os.getenv("API_KEY", "")
    db_host = os.getenv("DB_HOST", "")
    db_port = int(os.getenv("DB_PORT", "3306"))
    db_name = os.getenv("DB_NAME", "vehiculo_iot")


settings = Settings()

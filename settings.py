# ==============================================================================
# MÓDULO: settings.py
# PROPÓSITO: Encapsular mediante Programación Orientada a Objetos (clase Settings)
#            la configuración general de la API, los parámetros de conexión hacia
#            Amazon RDS y la política de orígenes autorizados para CORS.
# ==============================================================================

import os
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from dotenv import load_dotenv

# Directorio raíz del paquete backend para resolver rutas de forma independiente al CWD
BASE_DIR = Path(__file__).resolve().parent

# Carga las variables definidas en el archivo .env hacia el entorno del sistema
load_dotenv(BASE_DIR / ".env")


class Settings:
    """Clase POO que centraliza y valida los parámetros de configuración de la API."""

    # Orígenes explícitos autorizados por defecto (desarrollo local y dominio HTTPS propio)
    DEFAULT_CORS_ORIGINS = (
        "http://localhost:3000",
        "http://127.0.0.1:3000",
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:5500",
        "http://127.0.0.1:5500",
        "http://localhost:8000",
        "http://127.0.0.1:8000",
        "http://localhost:8080",
        "http://127.0.0.1:8080",
        "https://vehiculo-iot-api.duckdns.org",
        "https://claudiahernandez03.github.io",
    )

    # Placeholder documentado donde se puede fijar en código o en .env la URL de GitHub Pages
    # Ejemplo: "https://tu-usuario.github.io"
    GITHUB_PAGES_ORIGIN_PLACEHOLDER = "https://claudiahernandez03.github.io"

    def __init__(self) -> None:
        # Metadatos de la aplicación
        self.app_title: str = "API Vehículo IoT"
        self.app_version: str = "2.6.0"
        self.static_dir: Path = BASE_DIR / "static"
        self.frontend_dir: Path = BASE_DIR / "frontend"

        # Seguridad por API Key para endpoints de escritura (activa siempre que API_KEY esté definida
        # y REQUIRE_API_KEY no se deshabilite explícitamente)
        self.api_key: str = os.getenv("API_KEY", "").strip()
        self.require_api_key: bool = os.getenv("REQUIRE_API_KEY", "true").strip().lower() not in (
            "0",
            "false",
            "no",
        )

        # Parámetros de conexión hacia MySQL en Amazon RDS
        self.db_host: str = os.getenv("DB_HOST", "").strip()
        self.db_port: int = int(os.getenv("DB_PORT", "3306"))
        self.db_name: str = os.getenv("DB_NAME", "vehiculo_iot").strip()
        self.db_user: str = os.getenv("DB_USER", "").strip()
        self.db_password: str = os.getenv("DB_PASSWORD", "")
        self.db_connect_timeout: int = int(os.getenv("DB_CONNECT_TIMEOUT", "10"))

        # Parámetros de control de movimientos sostenidos, DETENER, secuencias y DEMO
        self.control_watchdog_seconds: float = float(
            os.getenv("CONTROL_WATCHDOG_SECONDS", "15.0")
        )
        self.stop_ack_timeout_ms: int = int(os.getenv("STOP_ACK_TIMEOUT_MS", "1500"))
        self.max_pasos_demo: int = 4
        self.order_seq_state_file: Path = Path(
            os.getenv("ORDER_SEQ_STATE_FILE", str(BASE_DIR / ".orden_seq_state.json"))
        )

        # Configuración explícita y segura de CORS (sin comodín "*")
        self.frontend_github_pages_url: str = os.getenv(
            "FRONTEND_GITHUB_PAGES_URL", self.GITHUB_PAGES_ORIGIN_PLACEHOLDER
        ).strip()
        self.cors_allow_credentials: bool = os.getenv(
            "CORS_ALLOW_CREDENTIALS", "false"
        ).strip().lower() in ("1", "true", "yes")
        self.cors_allow_methods: list[str] = ["GET", "POST", "PUT", "DELETE", "OPTIONS"]
        self.cors_allow_headers: list[str] = [
            "Accept",
            "Content-Type",
            "Origin",
            "X-Requested-With",
            "X-API-Key",
            "X-Control-Session",
            "X-Client-Role",
            "Authorization",
        ]
        self.cors_max_age: int = 600
        self.cors_origins: list[str] = self._build_cors_origins()

    @staticmethod
    def _normalize_origin(raw_origin: str) -> str | None:
        """Valida y normaliza un origen HTTP/HTTPS al formato 'esquema://host[:puerto]'.
        Rechaza comodines ('*') u orígenes mal formados."""
        candidate = (raw_origin or "").strip().rstrip("/")
        if not candidate or candidate == "*":
            return None
        parsed = urlparse(candidate)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            return None
        return f"{parsed.scheme}://{parsed.netloc}"

    def _build_cors_origins(self) -> list[str]:
        """Construye la lista explícita y sin duplicados de orígenes autorizados para CORS."""
        origins: list[str] = []

        raw_candidates: list[str] = list(self.DEFAULT_CORS_ORIGINS)
        if self.frontend_github_pages_url:
            raw_candidates.append(self.frontend_github_pages_url)

        extra_env = os.getenv("CORS_ORIGINS", "")
        if extra_env:
            raw_candidates.extend(part.strip() for part in extra_env.split(",") if part.strip())

        for item in raw_candidates:
            normalized = self._normalize_origin(item)
            if normalized and normalized not in origins:
                origins.append(normalized)

        return origins

    def get_db_config(self) -> dict[str, Any]:
        """Devuelve el diccionario de configuración para mysql.connector.connect()."""
        return {
            "host": os.getenv("DB_HOST", self.db_host),
            "port": int(os.getenv("DB_PORT", str(self.db_port))),
            "user": os.getenv("DB_USER", self.db_user),
            "password": os.getenv("DB_PASSWORD", self.db_password),
            "database": os.getenv("DB_NAME", self.db_name),
            "connection_timeout": self.db_connect_timeout,
            "autocommit": False,
        }


# Instancia única de configuración reutilizada por toda la aplicación
settings = Settings()

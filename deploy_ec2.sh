#!/usr/bin/env bash
# Script de despliegue automatizado en Amazon EC2 (Amazon Linux 2023 / Ubuntu 22.04+)
# Configura dependencias del sistema, entorno virtual, systemd y Nginx

set -e

echo "=== Despliegue de API Vehículo IoT en Amazon EC2 ==="

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$APP_DIR"

# 1. Comprobación estricta de variables de entorno
if [ ! -f ".env" ]; then
    echo "ERROR: Archivo .env no encontrado."
    echo "Crea el archivo .env con las credenciales de Amazon RDS antes de continuar:"
    echo "  cp .env.example .env"
    echo "  nano .env"
    exit 1
fi

echo "Archivo .env detectado. No se modificará ni sobrescribirá."

# 2. Instalación de paquetes del sistema operativo
echo "Comprobando e instalando paquetes del sistema..."
if command -v dnf &> /dev/null; then
    # Amazon Linux 2023 / Fedora
    sudo dnf update -y
    sudo dnf install -y python3 python3-pip nginx
elif command -v apt-get &> /dev/null; then
    # Ubuntu / Debian
    sudo apt-get update -y
    sudo apt-get install -y python3 python3-pip python3-venv nginx certbot python3-certbot-nginx
fi

# 3. Preparación del entorno virtual Python
if [ ! -d ".venv" ]; then
    echo "Creando entorno virtual Python (.venv)..."
    python3 -m venv .venv
fi

echo "Instalando dependencias en el entorno virtual..."
./.venv/bin/pip install --upgrade pip
./.venv/bin/pip install -r requirements.txt

# 3.5. Reconstrucción e inicialización idempotente de la base de datos en Amazon RDS
echo "Verificando conexión y aplicando reconstrucción idempotente en MySQL / Amazon RDS..."
./.venv/bin/python sql/ejecutar_migracion_y_validacion.py
./.venv/bin/python probar_conexion.py

# 4. Configuración y habilitación del servicio systemd
echo "Configurando servicio systemd vehiculo_iot_api.service..."
SERVICE_PATH="/etc/systemd/system/vehiculo_iot_api.service"

sudo cp vehiculo_iot_api.service "$SERVICE_PATH"

CURRENT_USER=$(whoami)
sudo sed -i "s|/home/ec2-user/vehiculo_iot_api/vehiculo_iot_api|$APP_DIR|g" "$SERVICE_PATH"
sudo sed -i "s|User=ec2-user|User=$CURRENT_USER|g" "$SERVICE_PATH"

sudo systemctl daemon-reload
sudo systemctl enable vehiculo_iot_api
sudo systemctl restart vehiculo_iot_api

echo "Estado del servicio FastAPI:"
sudo systemctl status vehiculo_iot_api --no-pager

# 5. Configuración de Nginx
echo ""
echo "=== Instrucciones finales para Nginx y HTTPS / WSS ==="
echo "1. Copia la configuración de Nginx:"
echo "   sudo cp nginx.conf /etc/nginx/conf.d/vehiculo_iot.conf"
echo ""
echo "2. Obtén tu certificado SSL gratuito con Let's Encrypt / Certbot:"
echo "   sudo certbot --nginx -d tu-dominio.com"
echo ""
echo "3. Reinicia Nginx para aplicar cambios:"
echo "   sudo systemctl restart nginx"
echo ""
echo "=== Despliegue completado exitosamente ==="

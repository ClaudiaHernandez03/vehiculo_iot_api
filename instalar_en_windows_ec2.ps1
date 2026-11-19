# ==============================================================================
# SCRIPT: instalar_en_windows_ec2.ps1
# PROPÓSITO: Automatizar de principio a fin la configuración de la nueva
#            instancia EC2 (Windows Server 2025 - 3.208.178.74) y la nueva
#            base de datos Amazon RDS (instancia-rds-iot).
# ==============================================================================

$ErrorActionPreference = "Continue"
$ProgressPreference = "SilentlyContinue"
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

$AppDir = "C:\Users\Administrator\Documents\vehiculo_iot_api"
$TempDir = "C:\instaladores_temp"
New-Item -ItemType Directory -Force -Path $TempDir | Out-Null
Set-Location $AppDir

Write-Host "==============================================================" -ForegroundColor Cyan
Write-Host " 1. Verificando instalacion de Python 3.12 en Windows Server..." -ForegroundColor Cyan
Write-Host "==============================================================" -ForegroundColor Cyan

$PythonExe = "C:\Program Files\Python312\python.exe"
if (-not (Test-Path $PythonExe)) {
    Write-Host "Descargando e instalando Python 3.12.8 (espera ~40 segundos)..." -ForegroundColor Yellow
    $pyInstaller = "$TempDir\python-3.12.8-amd64.exe"
    Invoke-WebRequest -Uri "https://www.python.org/ftp/python/3.12.8/python-3.12.8-amd64.exe" -OutFile $pyInstaller -UseBasicParsing
    Start-Process -FilePath $pyInstaller -ArgumentList "/quiet InstallAllUsers=1 PrependPath=1 Include_test=0" -Wait
}
if (-not (Test-Path $PythonExe)) {
    Write-Error "No se encontro Python en $PythonExe"
    exit 1
}
Write-Host "Usando Python: $PythonExe" -ForegroundColor Green

Write-Host "`n==============================================================" -ForegroundColor Cyan
Write-Host " 2. Creando entorno virtual (.venv) e instalando dependencias..." -ForegroundColor Cyan
Write-Host "==============================================================" -ForegroundColor Cyan

if (-not (Test-Path "$AppDir\.venv\Scripts\python.exe")) {
    & $PythonExe -m venv "$AppDir\.venv"
}
$VenvPython = "$AppDir\.venv\Scripts\python.exe"
& $VenvPython -m pip install --upgrade pip
& $VenvPython -m pip install -r "$AppDir\requirements.txt"

Write-Host "`n==============================================================" -ForegroundColor Cyan
Write-Host " 3. Configurando .env y limpiando secuencia previa..." -ForegroundColor Cyan
Write-Host "==============================================================" -ForegroundColor Cyan

$EnvContent = @"
DB_HOST=instancia-rds-iot.cnrw3tavcvgq.us-east-1.rds.amazonaws.com
DB_PORT=3306
DB_NAME=vehiculo_iot
DB_USER=admin
DB_PASSWORD=Admin1234#
DB_CONNECT_TIMEOUT=10
API_KEY=IFXe70lwnF80nsg_8HE0YTLclOmtP8zuwwSJxgZmmMI
REQUIRE_API_KEY=true
FRONTEND_GITHUB_PAGES_URL=https://claudiahernandez03.github.io
CORS_ORIGINS=http://localhost:5500,http://127.0.0.1:5500,http://localhost:8000,http://127.0.0.1:8000,https://vehiculo-iot-api.duckdns.org,https://claudiahernandez03.github.io
CORS_ALLOW_CREDENTIALS=false
"@
[System.IO.File]::WriteAllText("$AppDir\.env", $EnvContent, (New-Object System.Text.UTF8Encoding($false)))

Remove-Item -Path "$AppDir\.orden_seq_state.json*" -Force -ErrorAction SilentlyContinue

Write-Host "`n==============================================================" -ForegroundColor Cyan
Write-Host " 4. Reconstruyendo BD vehiculo_iot (6 tablas y 21 SPs) en RDS..." -ForegroundColor Cyan
Write-Host "==============================================================" -ForegroundColor Cyan

& $VenvPython "$AppDir\sql\ejecutar_migracion_y_validacion.py"
if ($LASTEXITCODE -ne 0) {
    Write-Error "Error al conectar o migrar Amazon RDS. Verifica que EC2 este conectada a RDS en el puerto 3306."
    exit 1
}
& $VenvPython "$AppDir\probar_conexion.py"

Write-Host "`n==============================================================" -ForegroundColor Cyan
Write-Host " 5. Configurando servicio Windows 'VehiculoIoTAPI' con NSSM..." -ForegroundColor Cyan
Write-Host "==============================================================" -ForegroundColor Cyan

$NssmDir = "C:\nssm"
$NssmExe = "$NssmDir\nssm.exe"
if (-not (Test-Path $NssmExe)) {
    New-Item -ItemType Directory -Force -Path $NssmDir | Out-Null
    $nssmZip = "$TempDir\nssm.zip"
    Invoke-WebRequest -Uri "https://nssm.cc/release/nssm-2.24.zip" -OutFile $nssmZip -UseBasicParsing
    Expand-Archive -Path $nssmZip -DestinationPath "$TempDir\nssm_extracted" -Force
    Copy-Item "$TempDir\nssm_extracted\nssm-2.24\win64\nssm.exe" -Destination $NssmExe -Force
}

if (Get-Service -Name "VehiculoIoTAPI" -ErrorAction SilentlyContinue) {
    Stop-Service -Name "VehiculoIoTAPI" -Force -ErrorAction SilentlyContinue
    & $NssmExe remove VehiculoIoTAPI confirm
}

New-Item -ItemType Directory -Force -Path "$AppDir\logs" | Out-Null
& $NssmExe install VehiculoIoTAPI $VenvPython "-m uvicorn main:app --host 127.0.0.1 --port 8000 --workers 1"
& $NssmExe set VehiculoIoTAPI AppDirectory $AppDir
& $NssmExe set VehiculoIoTAPI Start SERVICE_AUTO_START
& $NssmExe set VehiculoIoTAPI AppStdout "$AppDir\logs\service_out.log"
& $NssmExe set VehiculoIoTAPI AppStderr "$AppDir\logs\service_err.log"
Start-Service -Name "VehiculoIoTAPI"

Write-Host "`n==============================================================" -ForegroundColor Cyan
Write-Host " 6. Configurando Caddy (HTTPS y WSS) como servicio Windows..." -ForegroundColor Cyan
Write-Host "==============================================================" -ForegroundColor Cyan

$CaddyDir = "C:\caddy"
$CaddyExe = "$CaddyDir\caddy.exe"
if (-not (Test-Path $CaddyExe)) {
    New-Item -ItemType Directory -Force -Path $CaddyDir | Out-Null
    $caddyZip = "$env:TEMP\caddy.zip"
    Invoke-WebRequest -Uri "https://github.com/caddyserver/caddy/releases/download/v2.8.4/caddy_2.8.4_windows_amd64.zip" -OutFile $caddyZip -UseBasicParsing
    Expand-Archive -Path $caddyZip -DestinationPath $CaddyDir -Force
}

$CaddyfileContent = @"
vehiculo-iot-api.duckdns.org {
    reverse_proxy 127.0.0.1:8000
}
"@
[System.IO.File]::WriteAllText("$CaddyDir\Caddyfile", $CaddyfileContent, (New-Object System.Text.UTF8Encoding($false)))

if (Get-Service -Name "Caddy" -ErrorAction SilentlyContinue) {
    Stop-Service -Name "Caddy" -Force -ErrorAction SilentlyContinue
    & $NssmExe remove Caddy confirm
}

& $NssmExe install Caddy $CaddyExe "run --config C:\caddy\Caddyfile --adapter caddyfile"
& $NssmExe set Caddy AppDirectory $CaddyDir
& $NssmExe set Caddy Start SERVICE_AUTO_START
& $NssmExe set Caddy AppStdout "$CaddyDir\caddy_out.log"
& $NssmExe set Caddy AppStderr "$CaddyDir\caddy_err.log"

New-NetFirewallRule -DisplayName "Caddy HTTP 80" -Direction Inbound -LocalPort 80 -Protocol TCP -Action Allow -ErrorAction SilentlyContinue | Out-Null
New-NetFirewallRule -DisplayName "Caddy HTTPS 443" -Direction Inbound -LocalPort 443 -Protocol TCP -Action Allow -ErrorAction SilentlyContinue | Out-Null

Start-Service -Name "Caddy"

Start-Sleep -Seconds 3
Get-CimInstance Win32_Service -Filter "Name='VehiculoIoTAPI' OR Name='Caddy'" | Select-Object Name, State, StartMode | Format-Table -AutoSize

Write-Host "`n[OK] Despliegue completado exitosamente." -ForegroundColor Green

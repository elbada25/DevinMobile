#!/bin/bash
# Instala Devin Mobile Dashboard en Linux/Ubuntu/Proxmox
# Ejecutar como root o con sudo

set -e

SERVICE_NAME=devin-mobile
INSTALL_DIR=/opt/devin-mobile
SERVICE_USER=devin

echo "========================================"
echo " Devin Mobile Dashboard - Instalacion Linux"
echo "========================================"
echo ""

# Verificar root
if [ "$EUID" -ne 0 ]; then
    echo "ERROR: Ejecuta este script como root o con sudo"
    exit 1
fi

# Crear usuario si no existe
if ! id "$SERVICE_USER" &>/dev/null; then
    echo "Creando usuario $SERVICE_USER..."
    useradd -r -s /bin/bash -d "$INSTALL_DIR" "$SERVICE_USER"
fi

# Crear directorio
echo "Creando directorio $INSTALL_DIR..."
mkdir -p "$INSTALL_DIR"
mkdir -p "$INSTALL_DIR/logs"

# Copiar archivos
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
echo "Copiando archivos desde $SCRIPT_DIR..."
cp "$SCRIPT_DIR/server.py" "$INSTALL_DIR/"
cp "$SCRIPT_DIR/index.html" "$INSTALL_DIR/"
cp "$SCRIPT_DIR/requirements.txt" "$INSTALL_DIR/"

# Config
if [ ! -f "$INSTALL_DIR/config.json" ]; then
    cat > "$INSTALL_DIR/config.json" << 'EOF'
{
  "username": "admin",
  "password": "cambia-esta-contrasena"
}
EOF
    echo "AVISO: Edita $INSTALL_DIR/config.json con tu usuario y contrasena"
fi

# Permisos
chown -R "$SERVICE_USER:$SERVICE_USER" "$INSTALL_DIR"

# Virtualenv + dependencias
echo "Creando entorno virtual..."
sudo -u "$SERVICE_USER" python3 -m venv "$INSTALL_DIR/venv"
sudo -u "$SERVICE_USER" "$INSTALL_DIR/venv/bin/pip" install --upgrade pip
sudo -u "$SERVICE_USER" "$INSTALL_DIR/venv/bin/pip" install fastapi uvicorn psutil

# Instalar Devin CLI si no esta
if ! command -v devin &>/dev/null; then
    echo "Instalando Devin CLI..."
    sudo -u "$SERVICE_USER" "$INSTALL_DIR/venv/bin/pip" install devin-cli 2>/dev/null || true
    echo "AVISO: Si devin-cli no se instalo, instalo manualmente:"
    echo "  pip install devin-cli"
    echo "  devin auth login"
fi

# Instalar servicio systemd
echo "Instalando servicio systemd..."
cp "$SCRIPT_DIR/packaging/linux/devin-mobile.service" /etc/systemd/system/
systemctl daemon-reload
systemctl enable "$SERVICE_NAME"

# Iniciar
echo "Iniciando servicio..."
systemctl start "$SERVICE_NAME"

echo ""
echo "Instalacion completada."
echo "Dashboard disponible en: http://localhost:8787"
echo ""
echo "Comandos utiles:"
echo "  sudo systemctl status $SERVICE_NAME"
echo "  sudo systemctl restart $SERVICE_NAME"
echo "  sudo journalctl -u $SERVICE_NAME -f"
echo ""
echo "IMPORTANTE:"
echo "  1. Edita $INSTALL_DIR/config.json"
echo "  2. Instala Tailscale: curl -fsSL https://tailscale.com/install.sh | sh"
echo "  3. Ejecuta: sudo tailscale up"
echo "  4. Accede desde el movil: http://<IP-TAILSCALE>:8787"

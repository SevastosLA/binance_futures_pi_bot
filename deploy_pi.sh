#!/bin/bash
# ==============================================================================
# Script de Despliegue y Lanzamiento Perpetuo para Raspberry Pi 4B
# Estrategia Cuantitativa Híbrida Cripto (La Campeona 2% + El Francotirador 1%)
# ==============================================================================
set -e

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$REPO_DIR"

echo "=================================================================="
echo " 🚀 DESPLIEGUE Y LANZAMIENTO DESDE CERO EN RASPBERRY PI 4B"
echo "=================================================================="

# 1. Asegurar la última versión de GitHub
echo "\n[1/5] Actualizando código desde GitHub (origin/main)..."
git fetch origin main
git reset --hard origin/main

# 2. Configuración del Entorno Virtual Python (.venv)
echo "\n[2/5] Verificando entorno virtual Python..."
if [ ! -d ".venv" ]; then
    echo "  📦 Creando entorno virtual .venv..."
    python3 -m venv .venv
fi
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt

# 3. Reinicializar Base de Datos SQLite WAL desde Cero
echo "\n[3/5] Reinicializando base de datos SQLite WAL desde cero..."
rm -f data/bot_state.db*
python3 -c "from storage.database import DatabaseManager; db = DatabaseManager(); print('  ✅ Base de datos reinicializada con $100 USD iniciales ($20 por activo):', list(db.get_all_subwallets().keys()))"

# 4. Validar Suite de Pruebas Cuantitativas Híbridas
echo "\n[4/5] Ejecutando suite de validación cuantitativa (9 pruebas)..."
python3 test_hybrid_strategy.py

# 5. Configurar e Iniciar Servicio Systemd Perpetuo
echo "\n[5/5] Activando servicio en segundo plano (Systemd 24/7)..."
# Ajustar ruta de trabajo si no es /home/pi
CURRENT_USER=$(whoami)
SERVICE_TMP="/tmp/antigravity-futures.service"
sed "s|/home/pi/binance_futures_pi_bot|$REPO_DIR|g" systemd/antigravity-futures.service > "$SERVICE_TMP"
sed -i "s|User=pi|User=$CURRENT_USER|g" "$SERVICE_TMP"

sudo cp "$SERVICE_TMP" /etc/systemd/system/antigravity-futures.service
sudo systemctl daemon-reload
sudo systemctl enable antigravity-futures.service
sudo systemctl restart antigravity-futures.service

echo "\n=================================================================="
echo " 🎉 ¡BOT HÍBRIDO LANZADO CON ÉXITO EN MODO PERPETUO!"
echo "=================================================================="
sudo systemctl status antigravity-futures.service --no-pager || true

echo "\n📌 Comandos útiles para monitorear tu Raspberry Pi 4B:"
echo " • Ver logs en vivo: journalctl -u antigravity-futures.service -f"
echo " • Ver estado:       sudo systemctl status antigravity-futures.service"
echo " • Reiniciar bot:    sudo systemctl restart antigravity-futures.service"
echo " • Detener bot:      sudo systemctl stop antigravity-futures.service"

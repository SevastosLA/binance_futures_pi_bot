#!/usr/bin/env python3
"""
Script Interactivo: Listener de Comandos de Telegram en Tiempo Real.
Permite enviar comandos desde tu aplicación de Telegram (móvil o escritorio)
y recibir las respuestas y archivos CSV al instante.

Comandos disponibles para probar:
• /help     - Menú de ayuda y panel de control del bot
• /status   - Estado del fondo, saldos, HWM, ROI y órdenes vivas
• /wallets  - Descargar subwallets.csv (saldos por activo y HWM)
• /orders   - Descargar active_orders.csv (órdenes límite y posiciones)
• /trades   - Descargar trade_history.csv (historial con execution_type)
• /csv_all  - Descargar todas las tablas en archivos CSV separados
"""

import os
import sys
import time
import signal
import logging

# Asegurar path raíz del proyecto
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)

from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
from storage.database import DatabaseManager
from notifier.telegram_bot import TelegramNotifier

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s]: %(message)s"
)
logger = logging.getLogger("TelegramListener")

running = True

def handle_sigint(signum, frame):
    global running
    print("\n🛑 Deteniendo listener de comandos...")
    running = False

def run_interactive_listener():
    global running
    signal.signal(signal.SIGINT, handle_sigint)
    signal.signal(signal.SIGTERM, handle_sigint)

    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("❌ Error: TELEGRAM_BOT_TOKEN o TELEGRAM_CHAT_ID no configurados en .env")
        return False

    db = DatabaseManager()
    notifier = TelegramNotifier(db)

    print("=" * 75)
    print(" 📡 LISTENER DE COMANDOS TELEGRAM EN VIVO")
    print("=" * 75)
    print(f"  🔑 Chat ID autorizado: {TELEGRAM_CHAT_ID}")
    print("  💬 Abre tu chat de Telegram y envía cualquiera de los siguientes comandos:")
    print("     • /help     -> Panel general de ayuda")
    print("     • /status   -> Resumen de saldo, HWM y posiciones")
    print("     • /csv_all  -> Descargar todos los CSVs (subwallets, orders, trades)")
    print("     • /wallets  -> Descargar saldos por subcartera (CSV)")
    print("     • /orders   -> Descargar órdenes activas (CSV)")
    print("     • /trades   -> Descargar historial de operaciones (CSV)")
    print("=" * 75)
    print("  ⏳ Escuchando comandos en tiempo real... (Presiona Ctrl+C para detener)")

    # Enviar mensaje de invitación a Telegram
    welcome_msg = (
        "🤖 <b>LISTENER DE COMANDOS ACTIVADO</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "Puedes probar enviando cualquiera de los siguientes comandos:\n"
        "• <code>/help</code> — Panel de ayuda\n"
        "• <code>/status</code> — Estado de cuenta y bot\n"
        "• <code>/csv_all</code> — Descargar todas las bases de datos en CSV\n"
        "• <code>/wallets</code> — Saldos y HWM por activo\n"
        "• <code>/orders</code> — Órdenes pendientes y vivas\n"
        "• <code>/trades</code> — Historial de operaciones completadas\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "⚡ <i>Esperando tu comando...</i>"
    )
    notifier.send_message(welcome_msg)
    notifier.start_command_listener()

    try:
        while running:
            time.sleep(1)
    finally:
        notifier.stop_command_listener()
        print("\n✅ Listener detenido de forma limpia.")

    return True

if __name__ == "__main__":
    run_interactive_listener()

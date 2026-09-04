#!/usr/bin/env python3
"""
Script de Ejemplo y Demostración: Comandos Telegram y Exportación CSV (UTC+0).
Estrategia Híbrida Cripto (La Campeona 2% + El Francotirador 1%).

Demuestra:
1. Exportación de bases de datos SQLite en formato CSV con timestamps en UTC+0.
2. Envío de documentos CSV por Telegram con send_document.
3. Procesamiento interactivo de comandos (/help, /status, /csv_all).
"""

import os
import sys
import logging
import pandas as pd

# Asegurar path raíz del proyecto
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)

from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
from storage.database import DatabaseManager
from notifier.telegram_bot import TelegramNotifier

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s]: %(message)s")
logger = logging.getLogger("ExampleTelegramCommands")

def run_examples():
    print("=" * 75)
    print(" 🛠️ EJEMPLO: COMANDOS TELEGRAM Y EXPORTACIÓN CSV (UTC+0)")
    print("=" * 75)

    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("❌ Error: TELEGRAM_BOT_TOKEN o TELEGRAM_CHAT_ID no configurados.")
        return False

    db = DatabaseManager()
    notifier = TelegramNotifier(db)

    # 1. VERIFICAR EXPORTACIÓN CSV
    print("\n[1/3] Generando CSV de subwallets...")
    buf_wallets = notifier.export_table_to_csv("subwallets")
    if buf_wallets:
        csv_text = buf_wallets.getvalue().decode("utf-8-sig")
        print("  ✅ CSV generado en memoria exitosamente:")
        for line in csv_text.strip().split("\n")[:4]:
            print(f"     {line}")
    else:
        print("  ❌ Error generando CSV de subwallets")
        return False

    # 2. ENVIAR ARCHIVO CSV POR TELEGRAM CON send_document
    print("\n[2/3] Enviando subwallets.csv directamente a Telegram...")
    sent = notifier.send_document(
        buf_wallets,
        filename="subwallets.csv",
        caption="💼 <b>Ejemplo de Exportación</b>: Subcarteras y Saldos HWM (CSV)"
    )
    if sent:
        print("  ✅ Archivo CSV entregado exitosamente al chat de Telegram.")
    else:
        print("  ❌ Error enviando documento a Telegram.")
        return False

    # 3. PROBAR EJECUCIÓN DE COMANDOS /help Y /status
    print("\n[3/3] Simulando procesamiento de comandos (/help y /status)...")
    try:
        notifier.handle_command("/help", TELEGRAM_CHAT_ID)
        notifier.handle_command("/status", TELEGRAM_CHAT_ID)
        print("  ✅ Comandos procesados y respuestas enviadas a Telegram.")
    except Exception as e:
        print(f"  ❌ Error procesando comandos: {e}")
        return False

    print("\n" + "=" * 75)
    print(" 🎉 ¡EJEMPLO DE COMANDOS Y EXPORTACIÓN CSV COMPLETADO CON ÉXITO!")
    print(" Revisa tu Telegram: debes haber recibido el archivo CSV, el menú de ayuda y el estado.")
    print("=" * 75)
    return True

if __name__ == "__main__":
    success = run_examples()
    sys.exit(0 if success else 1)

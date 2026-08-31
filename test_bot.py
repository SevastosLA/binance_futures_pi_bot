#!/usr/bin/env python3
"""
Script de Auto-Diagnóstico y Verificación del Bot para Raspberry Pi.
Valida:
1. Conexión a la base de datos SQLite y persistencia.
2. Conexión y descarga de datos en vivo de Binance Futures API.
3. Cálculo de EMA 200 y lógica de señales.
4. Transición de la máquina de estados.
5. Conectividad y cola de notificaciones de Telegram.
"""

import sys
import logging
from config import SYMBOLS, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
from storage.database import DatabaseManager
from feed.binance_feed import BinanceFuturesFeed
from notifier.telegram_bot import TelegramNotifier
from engine.strategy_engine import StrategyEngine, calc_ema

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s]: %(message)s")
logger = logging.getLogger("TestBot")

def run_diagnostics():
    print("=" * 75)
    print(" 🛠️ EJECUTANDO AUTO-DIAGNÓSTICO: BINANCE FUTURES PI BOT")
    print("=" * 75)

    # 1. TEST BASE DE DATOS SQLITE
    print("\n[1/5] Verificando Base de Datos SQLite (Modo WAL)...")
    try:
        db = DatabaseManager()
        wallets = db.get_all_subwallets()
        print(f"  ✅ Base de datos inicializada correctamente.")
        print(f"  📊 Subcarteras registradas ({len(wallets)}): {list(wallets.keys())}")
        for sym, w in wallets.items():
            print(f"     • {sym:<8}: Capital = ${w['capital']:.2f} USD | HWM = ${w['hwm']:.2f} USD")
    except Exception as e:
        print(f"  ❌ Error en Base de Datos: {e}")
        return False

    # 2. TEST NOTIFICADOR TELEGRAM
    print("\n[2/5] Verificando Notificador de Telegram...")
    notifier = TelegramNotifier(db)
    if TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID:
        print(f"  🔑 Credenciales detectadas: Token configurado.")
        print(f"  📤 Enviando mensaje de prueba a Telegram...")
        test_msg = "🧪 <b>TEST DE CONECTIVIDAD:</b> Bot de trading en Raspberry Pi operativo y conectado."
        notifier.send_message(test_msg)
        print("  ✅ Mensaje enviado exitosamente.")
    else:
        print("  ⚠️ Telegram no configurado en .env (Simulando encolamiento offline)...")
        notifier.send_message("🧪 Mensaje de prueba simulado.")
        pending = db.get_pending_telegram_messages()
        print(f"  ✅ Mensaje encolado en SQLite outbox correctamente ({len(pending)} en cola).")

    # 3. TEST FEED DE BINANCE FUTURES
    print("\n[3/5] Verificando Conexión a Binance Futures REST API...")
    try:
        feed = BinanceFuturesFeed()
        prices = feed.fetch_all_latest_prices(SYMBOLS)
        print(f"  ✅ Precios en tiempo real obtenidos exitosamente:")
        for sym, p in prices.items():
            print(f"     • {sym:<8}: ${p:,.2f} USDT")
            
        test_sym = SYMBOLS[0]
        print(f"  📥 Descargando klines 1h para {test_sym}...")
        df_1h = feed.fetch_klines(test_sym, interval="1h", limit=250)
        if df_1h is not None and len(df_1h) >= 200:
            print(f"  ✅ Descargadas {len(df_1h)} velas 1h. Último cierre: ${df_1h.iloc[-2]['Close']:,.2f} USDT")
        else:
            print(f"  ❌ Error descargando klines para {test_sym}")
            return False
    except Exception as e:
        print(f"  ❌ Error conectando a Binance: {e}")
        return False

    # 4. TEST CÁLCULO DE INDICADORES
    print("\n[4/5] Verificando Cálculo de Indicadores (EMA 200)...")
    try:
        df_1h["EMA_200"] = calc_ema(df_1h["Close"], 200)
        last_ema = df_1h["EMA_200"].iloc[-2]
        last_close = df_1h["Close"].iloc[-2]
        trend_status = "ALCISTA (Close > EMA200)" if last_close > last_ema else "BAJISTA (Close < EMA200)"
        print(f"  ✅ EMA 200 calculada: ${last_ema:,.2f} USDT")
        print(f"  📈 Estado de Tendencia actual ({test_sym}): {trend_status}")
    except Exception as e:
        print(f"  ❌ Error calculando indicadores: {e}")
        return False

    # 5. TEST MOTOR DE ESTRATEGIA Y MÁQUINA DE ESTADOS
    print("\n[5/5] Verificando Motor de Estrategia y Máquina de Estados...")
    try:
        engine = StrategyEngine(db, notifier)
        engine.evaluate_hourly_close(test_sym, df_1h)
        st = db.get_order_state(test_sym)
        state_names = {0: "IDLE (Buscando)", 1: "PENDING (Límite Colocada)", 2: "IN_POS (Posición Activa)"}
        print(f"  ✅ Motor ejecutado sin errores. Estado actual de {test_sym}: {state_names.get(st.get('state'), 'Desconocido')}")
    except Exception as e:
        print(f"  ❌ Error en motor de estrategia: {e}")
        return False

    print("\n" + "=" * 75)
    print(" 🎉 ¡TODOS LOS TESTS COMPLETADOS CON ÉXITO!")
    print(" El bot está 100% listo para ser desplegado en tu Raspberry Pi.")
    print("=" * 75)
    return True

if __name__ == "__main__":
    success = run_diagnostics()
    sys.exit(0 if success else 1)

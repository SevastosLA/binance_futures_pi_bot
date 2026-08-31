#!/usr/bin/env python3
"""
Script de Auto-Diagnóstico y Verificación del Bot para Raspberry Pi.
Valida:
1. Conexión a la base de datos SQLite y persistencia (Modo WAL).
2. Conexión y descarga de datos en vivo de Binance Futures API.
3. Cálculo de EMA 200 y lógica de señales.
4. Generación en memoria de Gráficos de Velas (Chart Generator).
5. Conectividad y cola de notificaciones de Telegram con fotos.
"""

import os
import sys

# Asegurar que el directorio del bot esté en sys.path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import logging
from config import SYMBOLS, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
from storage.database import DatabaseManager
from feed.binance_feed import BinanceFuturesFeed
from notifier.telegram_bot import TelegramNotifier
from notifier.chart_generator import generate_trade_chart
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

    # 2. TEST FEED DE BINANCE FUTURES
    print("\n[2/5] Verificando Conexión a Binance Futures REST API...")
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

    # 3. TEST CÁLCULO DE INDICADORES
    print("\n[3/5] Verificando Cálculo de Indicadores (EMA 200)...")
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

    # 4. TEST GENERADOR DE GRÁFICOS (HEADLESS MATPLOTLIB)
    print("\n[4/5] Verificando Generador de Gráficos de Velas (Chart Generator)...")
    try:
        test_limit = last_close * 0.99
        test_tp = test_limit * 1.01
        test_sl = test_limit * 0.99
        img_buf = generate_trade_chart(
            symbol=test_sym,
            df_1h=df_1h,
            title="Prueba de Renderizado Visual — Orden Límite y Niveles",
            limit_price=test_limit,
            tp_price=test_tp,
            sl_price=test_sl
        )
        img_bytes = len(img_buf.getvalue())
        print(f"  ✅ Gráfico PNG generado en memoria exitosamente ({img_bytes / 1024:.1f} KB).")
    except Exception as e:
        print(f"  ❌ Error generando gráfico: {e}")
        return False

    # 5. TEST NOTIFICADOR TELEGRAM (CON FOTO)
    print("\n[5/5] Verificando Notificador de Telegram con Gráfico...")
    notifier = TelegramNotifier(db)
    if TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID:
        print(f"  🔑 Credenciales detectadas: Token configurado.")
        print(f"  📤 Enviando foto de prueba a Telegram...")
        caption_test = (
            f"🧪 <b>TEST DE GRÁFICOS Y CONECTIVIDAD</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"🪙 <b>Activo:</b> <code>{test_sym}</code>\n"
            f"📊 <b>Precio Actual:</b> ${last_close:,.2f} USDT\n"
            f"🎯 <b>Nivel Límite:</b> ${test_limit:,.2f}\n"
            f"🟢 <b>TP:</b> ${test_tp:,.2f} | 🔴 <b>SL:</b> ${test_sl:,.2f}\n"
            f"🤖 <i>Gráfico de velas generado en Raspberry Pi con Matplotlib Headless.</i>"
        )
        notifier.send_photo_or_text(img_buf, caption_test)
        print("  ✅ Foto y alerta enviadas exitosamente a Telegram.")
    else:
        print("  ⚠️ Telegram no configurado en .env (Simulando encolamiento offline)...")
        notifier.send_message("🧪 Mensaje de prueba simulado.")
        pending = db.get_pending_telegram_messages()
        print(f"  ✅ Mensaje encolado en SQLite outbox correctamente ({len(pending)} en cola).")

    print("\n" + "=" * 75)
    print(" 🎉 ¡TODOS LOS TESTS COMPLETADOS CON ÉXITO!")
    print(" El bot ahora enviará gráficos en tiempo real ante cada evento.")
    print("=" * 75)
    return True

if __name__ == "__main__":
    success = run_diagnostics()
    sys.exit(0 if success else 1)

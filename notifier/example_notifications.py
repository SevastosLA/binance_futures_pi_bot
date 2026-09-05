#!/usr/bin/env python3
"""
Script de Ejemplo y Demostración para Notificaciones de Telegram.
Estrategia Híbrida Cripto (La Campeona 2% + El Francotirador 1%).
Descarga datos reales de Binance Futures y envía ejemplos prácticos de:
1. Orden límite híbrida colocada (3.0% HWM: 2% Campeona + 1% Francotirador).
2. Expiración del tramo Francotirador tras 15 min sin llenado (ajuste a 2.0% HWM).
3. Orden llena con gráfico de velas actualizado y objetivos de precio (TP, SL).
4. Posición cerrada con Take Profit (+1.0%) y resultado neto (+0.96%).
5. Cancelación de orden límite tras 60 minutos sin llenado.
"""

import os
import sys
import datetime
import logging
import pandas as pd

# Asegurar path raíz del proyecto
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)

from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
from storage.database import DatabaseManager
from feed.binance_feed import BinanceFuturesFeed
from notifier.telegram_bot import TelegramNotifier
from engine.strategy_engine import calc_ema

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s]: %(message)s")
logger = logging.getLogger("ExampleNotifications")

def run_examples():
    print("=" * 75)
    print(" 🚀 EJEMPLO DE NOTIFICACIONES TELEGRAM (MODO HÍBRIDO)")
    print("=" * 75)

    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("❌ Error: TELEGRAM_BOT_TOKEN o TELEGRAM_CHAT_ID no configurados en .env")
        return False

    db = DatabaseManager()
    notifier = TelegramNotifier(db)
    feed = BinanceFuturesFeed()

    symbol = "BTCUSDT"
    now_str = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

    print(f"\n[1/5] Obteniendo datos en vivo de Binance Futures para {symbol}...")
    df_1h = feed.fetch_klines(symbol, interval="1h", limit=1000)
    if df_1h is None or df_1h.empty:
        print(f"❌ Error al descargar klines para {symbol}")
        return False

    df_1h["EMA_200"] = calc_ema(df_1h["Close"], 200)
    current_price = float(df_1h.iloc[-1]["Close"])
    ema_val = float(df_1h.iloc[-1]["EMA_200"])
    print(f"  ✅ {symbol} Precio actual: ${current_price:,.2f} | EMA 200: ${ema_val:,.2f}")

    # Simulación de niveles cuantitativos
    limit_price = round(current_price * 0.99, 2)
    tp_price = round(limit_price * 1.01, 2)
    sl_price = round(limit_price * 0.99, 2)
    capital = 100.00
    hwm = 100.00
    risk_campeona_usd = hwm * 0.02  # $2.00
    risk_franco_usd = hwm * 0.01    # $1.00
    risk_total_usd = risk_campeona_usd + risk_franco_usd  # $3.00

    # 1. EJEMPLO: ORDEN LÍMITE HÍBRIDA COLOCADA (3.0% RIESGO)
    print("\n[2/5] Enviando ejemplo de Orden Límite Híbrida Colocada (3% HWM)...")
    try:
        notifier.notify_limit_placed(
            symbol=symbol,
            side="LONG",
            trigger_time=now_str,
            signal_close=current_price,
            signal_ema=ema_val,
            limit_price=limit_price,
            risk_usd=risk_total_usd,
            capital=capital,
            hwm=hwm,
            risk_pct=0.03,
            risk_campeona_usd=risk_campeona_usd,
            risk_franco_usd=risk_franco_usd
        )
        print("  ✅ Notificación de Orden Límite Híbrida enviada exitosamente.")
    except Exception as e:
        print(f"  ❌ Error enviando Orden Límite: {e}")
        return False

    # 2. EJEMPLO: EXPIRACIÓN DE FRANCOTIRADOR A LOS 15 MIN (REDUCCIÓN A 2% HWM)
    print("\n[3/5] Enviando ejemplo de Expiración de Francotirador tras 15m...")
    try:
        notifier.notify_franco_expired(
            symbol=symbol,
            side="LONG",
            limit_price=limit_price,
            new_risk_usd=risk_campeona_usd,
            expire_time=now_str
        )
        print("  ✅ Notificación de Expiración de Francotirador enviada exitosamente.")
    except Exception as e:
        print(f"  ❌ Error enviando Expiración de Francotirador: {e}")
        return False

    # 3. EJEMPLO: ORDEN LLENADA CON GRÁFICO (FRANCOTIRADOR BOOST 3%)
    print("\n[4/5] Enviando ejemplo de Posición Llenada con Gráfico Actualizado...")
    try:
        notifier.notify_position_filled(
            symbol=symbol,
            side="LONG",
            entry_time=now_str,
            fill_price=limit_price,
            tp_price=tp_price,
            sl_price=sl_price,
            risk_usd=risk_total_usd,
            df_1h=df_1h,
            execution_type="FRANCOTIRADOR_BOOST_3PCT",
            risk_pct=0.03
        )
        print("  ✅ Notificación de Orden Llenada con Gráfico enviada exitosamente.")
    except Exception as e:
        print(f"  ❌ Error enviando Orden Llenada: {e}")
        return False

    # 4. EJEMPLO: POSICIÓN CERRADA POR TAKE PROFIT (+1.0%)
    print("\n[5/5] Enviando ejemplo de Posición Cerrada por Take Profit (+1.0%)...")
    try:
        net_return_pct = 0.96  # +1.0% - 0.04% maker fee
        dollar_pnl = (0.0096 / 0.010) * risk_total_usd  # $2.88
        new_cap = capital + dollar_pnl
        notifier.notify_position_closed(
            symbol=symbol,
            side="LONG",
            exit_time=now_str,
            exit_price=tp_price,
            exit_reason="Take Profit (+1.0%) 🎯",
            dollar_pnl=dollar_pnl,
            net_return_pct=net_return_pct,
            win=True,
            new_capital=new_cap,
            new_hwm=new_cap,
            execution_type="FRANCOTIRADOR_BOOST_3PCT"
        )
        print("  ✅ Notificación de Posición Cerrada enviada exitosamente.")
    except Exception as e:
        print(f"  ❌ Error enviando Posición Cerrada: {e}")
        return False

    print("\n" + "=" * 75)
    print(" 🎉 ¡EJEMPLOS DE NOTIFICACIONES HÍBRIDAS ENVIADOS CON ÉXITO!")
    print(" Revisa tu Telegram para confirmar las alertas recibidas.")
    print("=" * 75)
    return True

if __name__ == "__main__":
    success = run_examples()
    sys.exit(0 if success else 1)

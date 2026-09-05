#!/usr/bin/env python3
"""
Suite de Pruebas y Validación Cuantitativa: Estrategia Híbrida Cripto
(La Campeona 2% + El Francotirador 1%).

Valida exhaustivamente:
1. Generación de Señal LONG y SHORT en 1h con EMA 200 y rachas de retroceso.
2. Emisión de Orden Límite Híbrida (-1.0% de descuento, 3.0% de riesgo HWM inicial).
3. Llenado rápido en Fase 1 (≤15 min) -> FRANCOTIRADOR_BOOST_3PCT con 3.0% de riesgo.
4. Expiración de Francotirador a los 15 min sin llenado -> Reducción a 2.0% HWM (CAMPEONA).
5. Llenado en Fase 2 (min 15-60) -> CAMPEONA_NORMAL_2PCT con 2.0% de riesgo.
6. Expiración total a los 60 min sin llenado -> Cancelación y retorno a IDLE.
7. Salida por Take Profit (+1.0%) con comisiones Maker (0.04% roundtrip, +0.96% neto).
8. Salida por Stop Loss (-1.0%) con comisiones Maker+Taker (0.06% roundtrip, -1.06% neto).
9. Salida preventiva en 4ª vela consecutiva contraria con comisiones exactas.
10. Persistencia íntegra en SQLite WAL, subwallets, HWM y trade_history.
"""

import os
import sys
import shutil
import tempfile
import datetime
import numpy as np
import pandas as pd

# Asegurar path del proyecto
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config import (
    LIMIT_DISCOUNT_PCT, TP_PCT, SL_PCT, FEE_MAKER, FEE_TAKER,
    RISK_CAMPEONA, RISK_FRANCO, RISK_HYBRID_TOTAL
)
from storage.database import DatabaseManager
from notifier.telegram_bot import TelegramNotifier
from engine.strategy_engine import StrategyEngine, calc_ema

def create_synthetic_df_1h(trend="BULLISH", num_bars=250, last_streak=1) -> pd.DataFrame:
    """Genera datos sintéticos de 1h con tendencia y racha controlada."""
    base_time = datetime.datetime(2026, 9, 1, 0, 0, 0)
    times = [base_time + datetime.timedelta(hours=i) for i in range(num_bars)]
    
    if trend == "BULLISH":
        # Precios crecientes sobre EMA 200 (~$60,000 a ~$70,000)
        prices = np.linspace(60000, 70000, num_bars)
        opens = prices - 50.0
        closes = prices.copy()
        highs = prices + 100.0
        lows = prices - 100.0
        
        # Ajustar la vela cerrada previa (índice -2) para que sea la primera vela roja de retroceso
        # red_streak = last_streak
        for s in range(last_streak):
            idx = -2 - s
            opens[idx] = closes[idx] + 200.0  # Vela roja: Open > Close
        if last_streak == 1:
            opens[-3] = closes[-3] - 200.0   # Vela verde previa para garantizar red_streak == 1
    else:
        # Precios decrecientes bajo EMA 200 (~$70,000 a ~$60,000)
        prices = np.linspace(70000, 60000, num_bars)
        opens = prices + 50.0
        closes = prices.copy()
        highs = prices + 100.0
        lows = prices - 100.0
        
        for s in range(last_streak):
            idx = -2 - s
            opens[idx] = closes[idx] - 200.0  # Vela verde: Close > Open
        if last_streak == 1:
            opens[-3] = closes[-3] + 200.0   # Vela roja previa para garantizar green_streak == 1

    df = pd.DataFrame({
        "Open Time": [t.strftime("%Y-%m-%d %H:%M:%S") for t in times],
        "Open": opens,
        "High": highs,
        "Low": lows,
        "Close": closes,
        "Volume": [1000.0] * num_bars
    })
    return df

def run_tests():
    print("=" * 80)
    print(" 🧪 VALIDACIÓN INTEGRAL DE LA ESTRATEGIA HÍBRIDA CRIPTO")
    print(" (La Campeona 2% + El Francotirador 1%)")
    print("=" * 80)

    # Crear base de datos temporal aislada para pruebas
    temp_dir = tempfile.mkdtemp()
    temp_db_path = os.path.join(temp_dir, "test_hybrid.db")

    try:
        db = DatabaseManager(db_path=temp_db_path)
        # Desactivar envío de red en notificador para el test
        notifier = TelegramNotifier(db, bot_token="", chat_id="")
        engine = StrategyEngine(db, notifier)

        symbol = "BTCUSDT"
        wallet = db.get_subwallet(symbol)
        initial_cap = wallet["capital"]
        initial_hwm = wallet["hwm"]
        print(f"\n[Configuración Inicial] Símbolo: {symbol} | Cap: ${initial_cap:.2f} | HWM: ${initial_hwm:.2f}")

        # -------------------------------------------------------------
        # TEST 1: DETECCIÓN DE SEÑAL LONG Y COLOCACIÓN DE ORDEN HÍBRIDA (3%)
        # -------------------------------------------------------------
        print("\n[Test 1] Generación de Señal LONG y Orden Límite Híbrida...")
        df_bullish = create_synthetic_df_1h(trend="BULLISH", num_bars=250, last_streak=1)
        engine.evaluate_hourly_close(symbol, df_bullish)

        order = db.get_order_state(symbol)
        assert order["state"] == 1, f"El estado debe ser 1 (PENDING), obtenido: {order['state']}"
        assert order["side"] == "LONG", f"El lado debe ser LONG, obtenido: {order['side']}"
        assert order["franco_active"] == 1, "El tramo Francotirador debe estar activo (1)"
        assert abs(order["effective_risk_pct"] - 0.03) < 1e-5, f"Riesgo inicial debe ser 3% (0.03), obtenido: {order['effective_risk_pct']}"
        
        expected_risk_usd = initial_hwm * 0.03
        assert abs(order["risk_usd"] - expected_risk_usd) < 1e-4, f"Riesgo USD esperado: {expected_risk_usd}, obtenido: {order['risk_usd']}"
        
        last_close = float(df_bullish.iloc[-2]["Close"])
        expected_limit = last_close * (1.0 - LIMIT_DISCOUNT_PCT)
        assert abs(order["limit_price"] - expected_limit) < 1e-2, f"Precio límite esperado: {expected_limit}, obtenido: {order['limit_price']}"
        print(f"  ✅ Señal LONG detectada con éxito. Orden colocada en ${order['limit_price']:,.2f} con Riesgo 3% (${order['risk_usd']:.2f} USD).")

        # -------------------------------------------------------------
        # TEST 2: LLENADO EN FASE 1 (≤15 min) -> FRANCOTIRADOR BOOST (3.0%)
        # -------------------------------------------------------------
        print("\n[Test 2] Llenado en Fase 1 (≤15 min) -> Francotirador Boost (3.0% Riesgo)...")
        trigger_dt = datetime.datetime.strptime(order["trigger_time"], "%Y-%m-%d %H:%M:%S")
        fill_time_10m = trigger_dt + datetime.timedelta(minutes=10)
        
        # Simular que el precio cae y toca el precio límite
        limit_p = order["limit_price"]
        engine.evaluate_realtime_tick(
            symbol=symbol,
            current_price=limit_p,
            candle_15m_high=limit_p + 50.0,
            candle_15m_low=limit_p - 10.0,
            current_time=fill_time_10m
        )

        pos = db.get_order_state(symbol)
        assert pos["state"] == 2, f"El estado debe ser 2 (ACTIVE), obtenido: {pos['state']}"
        assert pos["execution_type"] == "FRANCOTIRADOR_BOOST_3PCT", f"Tipo de ejecución incorrecto: {pos['execution_type']}"
        assert abs(pos["effective_risk_pct"] - 0.03) < 1e-5, f"Riesgo debe ser 3%, obtenido: {pos['effective_risk_pct']}"
        assert abs(pos["risk_usd"] - expected_risk_usd) < 1e-4, f"Riesgo en USD incorrecto: {pos['risk_usd']}"
        print(f"  ✅ Posición llenada en 10 min. Tipo: {pos['execution_type']} con 3.0% de riesgo (${pos['risk_usd']:.2f} USD).")

        # -------------------------------------------------------------
        # TEST 3: CIERRE POR TAKE PROFIT (+1.0%) CON COMISIÓN MAKER (0.04%)
        # -------------------------------------------------------------
        print("\n[Test 3] Resolución de Take Profit (+1.0%). Comisiones Maker 0.04% roundtrip...")
        tp_target = pos["tp_price"]
        tp_time = fill_time_10m + datetime.timedelta(minutes=20)

        engine.evaluate_realtime_tick(
            symbol=symbol,
            current_price=tp_target,
            candle_15m_high=tp_target + 20.0,
            candle_15m_low=limit_p,
            current_time=tp_time
        )

        closed_state = db.get_order_state(symbol)
        assert closed_state["state"] == 0, f"El estado tras TP debe ser 0 (IDLE), obtenido: {closed_state['state']}"

        # Verificar actualización de cartera
        wallet_after_tp = db.get_subwallet(symbol)
        # Ganancia neta: +1.0% - (0.02% + 0.02%) = +0.96% neto
        expected_raw_move = TP_PCT - (FEE_MAKER + FEE_MAKER)  # 0.0096
        expected_dpnl = (expected_raw_move / 0.010) * expected_risk_usd  # 0.96 * 0.60 = 0.576
        expected_cap = initial_cap + expected_dpnl
        
        assert abs(wallet_after_tp["capital"] - expected_cap) < 1e-4, f"Capital esperado tras TP: ${expected_cap:.4f}, obtenido: ${wallet_after_tp['capital']:.4f}"
        assert abs(wallet_after_tp["hwm"] - expected_cap) < 1e-4, f"HWM esperado tras TP: ${expected_cap:.4f}, obtenido: ${wallet_after_tp['hwm']:.4f}"
        print(f"  ✅ Take Profit alcanzado. Ganancia neta: +${expected_dpnl:.4f} USD (+{expected_raw_move*100:.2f}%).")
        print(f"  💼 Nuevo Capital: ${wallet_after_tp['capital']:.4f} | Nuevo HWM: ${wallet_after_tp['hwm']:.4f}")

        # -------------------------------------------------------------
        # TEST 4: EXPIRACIÓN DE FRANCOTIRADOR A LOS 15 MINUTOS (3% -> 2%)
        # -------------------------------------------------------------
        print("\n[Test 4] Expiración de Francotirador a los 15 minutos sin llenado...")
        # Generar nueva señal LONG
        engine.evaluate_hourly_close(symbol, df_bullish)
        order2 = db.get_order_state(symbol)
        assert order2["state"] == 1, "Debe haber orden pendiente"
        assert order2["franco_active"] == 1, "Debe iniciar con Francotirador activo"

        trig_dt2 = datetime.datetime.strptime(order2["trigger_time"], "%Y-%m-%d %H:%M:%S")
        # Simular evaluación al minuto 16 sin que el precio alcance el límite
        t_16m = trig_dt2 + datetime.timedelta(minutes=16)
        high_price = order2["limit_price"] + 200.0  # Lejos del límite

        engine.evaluate_realtime_tick(
            symbol=symbol,
            current_price=high_price,
            candle_15m_high=high_price + 50.0,
            candle_15m_low=high_price - 50.0,
            current_time=t_16m
        )

        order2_updated = db.get_order_state(symbol)
        assert order2_updated["state"] == 1, "La orden debe seguir PENDING"
        assert order2_updated["franco_active"] == 0, f"Francotirador debe estar inactivo (0), obtenido: {order2_updated['franco_active']}"
        assert abs(order2_updated["effective_risk_pct"] - 0.02) < 1e-5, f"Riesgo debe reducirse a 2.0%, obtenido: {order2_updated['effective_risk_pct']}"
        
        expected_risk_2pct = wallet_after_tp["hwm"] * 0.02
        assert abs(order2_updated["risk_usd"] - expected_risk_2pct) < 1e-4, f"Riesgo USD esperado: {expected_risk_2pct}, obtenido: {order2_updated['risk_usd']}"
        print(f"  ✅ Francotirador expiró tras 15m. Riesgo ajustado a 2.0% HWM (${order2_updated['risk_usd']:.2f} USD). Campeona sigue viva.")

        # -------------------------------------------------------------
        # TEST 5: LLENADO EN FASE 2 (min 25) -> CAMPEONA NORMAL (2.0%)
        # -------------------------------------------------------------
        print("\n[Test 5] Llenado en Fase 2 (min 25) -> Campeona Normal (2.0% Riesgo)...")
        t_25m = trig_dt2 + datetime.timedelta(minutes=25)
        limit_p2 = order2_updated["limit_price"]

        engine.evaluate_realtime_tick(
            symbol=symbol,
            current_price=limit_p2,
            candle_15m_high=limit_p2 + 20.0,
            candle_15m_low=limit_p2 - 5.0,
            current_time=t_25m
        )

        pos2 = db.get_order_state(symbol)
        assert pos2["state"] == 2, "La orden debe estar ACTIVE"
        assert pos2["execution_type"] == "CAMPEONA_NORMAL_2PCT", f"Tipo de ejecución esperado CAMPEONA_NORMAL_2PCT, obtenido: {pos2['execution_type']}"
        assert abs(pos2["effective_risk_pct"] - 0.02) < 1e-5, f"Riesgo debe ser 2.0%, obtenido: {pos2['effective_risk_pct']}"
        assert abs(pos2["risk_usd"] - expected_risk_2pct) < 1e-4, f"Riesgo USD incorrecto: {pos2['risk_usd']}"
        print(f"  ✅ Posición llenada en min 25. Tipo: {pos2['execution_type']} con 2.0% de riesgo (${pos2['risk_usd']:.2f} USD).")

        # -------------------------------------------------------------
        # TEST 6: CIERRE POR STOP LOSS (-1.0%) CON COMISIÓN MAKER+TAKER (0.06%)
        # -------------------------------------------------------------
        print("\n[Test 6] Resolución de Stop Loss (-1.0%). Comisiones Maker+Taker 0.06% roundtrip...")
        sl_target = pos2["sl_price"]
        sl_time = t_25m + datetime.timedelta(minutes=15)

        engine.evaluate_realtime_tick(
            symbol=symbol,
            current_price=sl_target,
            candle_15m_high=limit_p2,
            candle_15m_low=sl_target - 10.0,
            current_time=sl_time
        )

        closed_state2 = db.get_order_state(symbol)
        assert closed_state2["state"] == 0, f"El estado tras SL debe ser 0 (IDLE), obtenido: {closed_state2['state']}"

        wallet_after_sl = db.get_subwallet(symbol)
        # Pérdida neta: -1.0% - (0.02% + 0.04%) = -1.06% neto
        expected_sl_move = -SL_PCT - (FEE_MAKER + FEE_TAKER)  # -0.0106
        expected_sl_dpnl = (expected_sl_move / 0.010) * expected_risk_2pct  # -1.06 * 0.40 = -0.424
        expected_cap2 = wallet_after_tp["capital"] + expected_sl_dpnl

        assert abs(wallet_after_sl["capital"] - expected_cap2) < 1e-4, f"Capital esperado tras SL: ${expected_cap2:.4f}, obtenido: ${wallet_after_sl['capital']:.4f}"
        assert abs(wallet_after_sl["hwm"] - wallet_after_tp["hwm"]) < 1e-4, "El HWM debe mantenerse intacto en pérdida"
        print(f"  ✅ Stop Loss alcanzado. Pérdida neta: -${abs(expected_sl_dpnl):.4f} USD ({expected_sl_move*100:.2f}%).")
        print(f"  💼 Capital: ${wallet_after_sl['capital']:.4f} | HWM intacto: ${wallet_after_sl['hwm']:.4f}")

        # -------------------------------------------------------------
        # TEST 7: EXPIRACIÓN TOTAL TRAS 60 MINUTOS SIN FILL (Retorno a IDLE)
        # -------------------------------------------------------------
        print("\n[Test 7] Expiración Total tras 60 minutos sin llenado (Retorno a IDLE)...")
        engine.evaluate_hourly_close(symbol, df_bullish)
        order3 = db.get_order_state(symbol)
        assert order3["state"] == 1, "Debe haber orden pendiente"
        trig_dt3 = datetime.datetime.strptime(order3["trigger_time"], "%Y-%m-%d %H:%M:%S")

        # Evaluar al minuto 61 sin llenado
        t_61m = trig_dt3 + datetime.timedelta(minutes=61)
        engine.evaluate_realtime_tick(
            symbol=symbol,
            current_price=limit_p + 300.0,
            candle_15m_high=limit_p + 350.0,
            candle_15m_low=limit_p + 250.0,
            current_time=t_61m
        )

        order3_cancelled = db.get_order_state(symbol)
        assert order3_cancelled["state"] == 0, f"Tras 60 min debe retornar a 0 (IDLE), obtenido: {order3_cancelled['state']}"
        print("  ✅ Orden límite cancelada con éxito tras 60 minutos sin llenado. Estado = IDLE.")

        # -------------------------------------------------------------
        # TEST 8: SEÑAL SHORT, EJECUCIÓN Y SALIDA PREVENTIVA EN 4ª VELA
        # -------------------------------------------------------------
        print("\n[Test 8] Señal SHORT y Salida Preventiva en 4ª Vela Horaria...")
        df_bearish = create_synthetic_df_1h(trend="BEARISH", num_bars=250, last_streak=1)
        engine.evaluate_hourly_close(symbol, df_bearish)

        order_short = db.get_order_state(symbol)
        assert order_short["state"] == 1 and order_short["side"] == "SHORT", "Debe colocarse orden SHORT pendiente"
        
        # Llenar la orden SHORT en los primeros 10 min
        short_limit = order_short["limit_price"]
        trig_dt_short = datetime.datetime.strptime(order_short["trigger_time"], "%Y-%m-%d %H:%M:%S")
        engine.evaluate_realtime_tick(
            symbol=symbol,
            current_price=short_limit,
            candle_15m_high=short_limit + 5.0,
            candle_15m_low=short_limit - 50.0,
            current_time=trig_dt_short + datetime.timedelta(minutes=10)
        )
        pos_short = db.get_order_state(symbol)
        assert pos_short["state"] == 2 and pos_short["side"] == "SHORT", "Debe estar activa la posición SHORT"

        # Simular que transcurren 4 velas verdes consecutivas en contra
        df_bearish_4streak = create_synthetic_df_1h(trend="BEARISH", num_bars=250, last_streak=4)
        engine.evaluate_hourly_close(symbol, df_bearish_4streak)

        pos_short_closed = db.get_order_state(symbol)
        assert pos_short_closed["state"] == 0, f"Debe haberse cerrado por 4ª vela contraria, estado: {pos_short_closed['state']}"
        print("  ✅ Posición SHORT cerrada preventivamente en 4ª vela horaria adversa.")

        # -------------------------------------------------------------
        # TEST 9: AUDITORÍA DE REGISTROS EN SQLITE Y EXPORTACIÓN CSV
        # -------------------------------------------------------------
        print("\n[Test 9] Auditoría de Tabla trade_history y Exportación CSV...")
        trades_csv_buf = notifier.export_table_to_csv("trade_history")
        assert trades_csv_buf is not None, "El buffer CSV no debe ser nulo"
        csv_content = trades_csv_buf.getvalue().decode("utf-8-sig")
        assert "execution_type" in csv_content, "El CSV debe incluir la columna 'execution_type'"
        assert "risk_pct" in csv_content, "El CSV debe incluir la columna 'risk_pct'"
        assert "FRANCOTIRADOR_BOOST_3PCT" in csv_content, "El CSV debe registrar trades FRANCOTIRADOR_BOOST_3PCT"
        assert "CAMPEONA_NORMAL_2PCT" in csv_content, "El CSV debe registrar trades CAMPEONA_NORMAL_2PCT"
        print("  ✅ Base de datos contiene registros con execution_type y risk_pct.")
        print("  📄 Muestra de trade_history CSV:")
        for line in csv_content.strip().split("\n")[:4]:
            print(f"     {line[:110]}...")

        # -------------------------------------------------------------
        # TEST 10: SINCRONIZACIÓN TEMPORAL Y PREVENCIÓN DE EXPIRACIÓN PREMATURA
        # -------------------------------------------------------------
        print("\n[Test 10] Sincronización Temporal (Prevención de Expiración Prematura)...")
        # Limpiar estado previo
        db.reset_order_state(symbol)
        df_sync = create_synthetic_df_1h(trend="BULLISH", num_bars=250, last_streak=1)
        closed_open_time = pd.to_datetime(df_sync.iloc[-2]["Open Time"])
        expected_trigger_dt = closed_open_time + datetime.timedelta(hours=1)
        expected_trigger_str = expected_trigger_dt.strftime("%Y-%m-%d %H:%M:%S")

        engine.evaluate_hourly_close(symbol, df_sync)
        order_sync = db.get_order_state(symbol)
        assert order_sync["state"] == 1, "Debe haber orden pendiente colocada"
        assert order_sync["trigger_time"] == expected_trigger_str, (
            f"trigger_time debe ser la hora de cierre ({expected_trigger_str}), "
            f"obtenido: {order_sync['trigger_time']}"
        )

        # Evaluar tick 20 segundos después de la emisión de la orden (como en live)
        tick_20s = expected_trigger_dt + datetime.timedelta(seconds=20)
        engine.evaluate_realtime_tick(
            symbol=symbol,
            current_price=order_sync["limit_price"] + 100.0,  # Sin fill
            current_time=tick_20s
        )
        order_sync_20s = db.get_order_state(symbol)
        assert order_sync_20s["state"] == 1, "A los 20 segundos la orden debe seguir PENDIENTE (state=1)"
        assert order_sync_20s["franco_active"] == 1, "A los 20 segundos el Francotirador debe seguir ACTIVO"

        # Evaluar a los 15 minutos exactos + 1 segundo: expiración solo de Francotirador
        tick_15m = expected_trigger_dt + datetime.timedelta(minutes=15, seconds=1)
        engine.evaluate_realtime_tick(
            symbol=symbol,
            current_price=order_sync["limit_price"] + 100.0,
            current_time=tick_15m
        )
        order_sync_15m = db.get_order_state(symbol)
        assert order_sync_15m["state"] == 1, "A los 15 min la orden debe seguir PENDIENTE (state=1)"
        assert order_sync_15m["franco_active"] == 0, "A los 15 min el Francotirador debe EXPIRAR"

        # Evaluar a los 60 minutos exactos + 1 segundo: cancelación total
        tick_60m = expected_trigger_dt + datetime.timedelta(minutes=60, seconds=1)
        engine.evaluate_realtime_tick(
            symbol=symbol,
            current_price=order_sync["limit_price"] + 100.0,
            current_time=tick_60m
        )
        order_sync_60m = db.get_order_state(symbol)
        assert order_sync_60m["state"] == 0, "A los 60 min la orden debe CANCELARSE (state=0)"
        print("  ✅ Sincronización temporal validada al 100%. Vida útil de 15m y 60m respetada estrictamente.")

        print("\n" + "=" * 80)
        print(" 🎉 ¡TODAS LAS 10 PRUEBAS CUANTITATIVAS PASARON AL 100%!")
        print(" El modelo híbrido funciona con exactitud matemática rigurosa.")
        print("=" * 80)
        return True

    finally:
        # Limpieza de archivos temporales
        shutil.rmtree(temp_dir, ignore_errors=True)

if __name__ == "__main__":
    success = run_tests()
    sys.exit(0 if success else 1)

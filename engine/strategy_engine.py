"""
Motor de Estrategia Cuantitativa de Reversión en Pullback con Entrada Límite y HWM.
Maneja la lógica de señales en 1h, ejecución límite en 15m/tiempo real, TP, SL y gestión monetaria.
"""

import logging
import datetime
import pandas as pd
import numpy as np
from typing import Dict, Any, Optional

from config import (
    EMA_PERIOD, LIMIT_DISCOUNT_PCT, TP_PCT, SL_PCT,
    MAX_STREAK_EXIT, FEE_RATE, RISK_PCT_HWM
)
from storage.database import DatabaseManager
from notifier.telegram_bot import TelegramNotifier

logger = logging.getLogger("StrategyEngine")

def calc_ema(series: pd.Series, length: int) -> pd.Series:
    return series.ewm(span=length, adjust=False).mean()

class StrategyEngine:
    def __init__(self, db: DatabaseManager, notifier: TelegramNotifier):
        self.db = db
        self.notifier = notifier

    def evaluate_hourly_close(self, symbol: str, df_1h: pd.DataFrame):
        """
        Se ejecuta inmediatamente tras el cierre de cada vela horaria.
        Evalúa señales de entrada, cancelaciones de órdenes pendientes o salidas por 4ª vela.
        """
        if len(df_1h) < EMA_PERIOD + 10:
            logger.warning(f"{symbol}: Datos insuficientes para calcular EMA {EMA_PERIOD} (filas: {len(df_1h)})")
            return

        # Calcular EMA 200
        df_1h = df_1h.copy()
        df_1h["EMA_200"] = calc_ema(df_1h["Close"], EMA_PERIOD)
        
        # Última vela cerrada (índice -2 si la última fila -1 es la vela actualmente abierta)
        closed_candle = df_1h.iloc[-2]
        c_1h = float(closed_candle["Close"])
        o_1h = float(closed_candle["Open"])
        ema_val = float(closed_candle["EMA_200"])
        candle_time_str = pd.to_datetime(closed_candle["Open Time"]).strftime("%Y-%m-%d %H:%M:%S")

        # Calcular racha de velas consecutivas cerradas
        closes = df_1h["Close"].values[:-1]
        opens = df_1h["Open"].values[:-1]
        is_red = closes < opens
        is_green = closes > opens
        
        red_streak = 0
        for val in reversed(is_red):
            if val: red_streak += 1
            else: break
            
        green_streak = 0
        for val in reversed(is_green):
            if val: green_streak += 1
            else: break

        order_state = self.db.get_order_state(symbol)
        state = order_state.get("state", 0)
        wallet = self.db.get_subwallet(symbol)
        if not wallet:
            logger.error(f"No se encontró subcartera para {symbol}")
            return

        # -------------------------------------------------------------
        # ESTADO 2: POSICIÓN ACTIVA (Evaluación de salida por 4ª vela)
        # -------------------------------------------------------------
        if state == 2:
            side = order_state["side"]
            entry_p = float(order_state["fill_price"] or order_state["limit_price"])
            risk_usd = float(order_state["risk_usd"])
            trigger_t = order_state["trigger_time"]
            entry_t = order_state["entry_time"] or candle_time_str

            should_exit = False
            exit_reason = None
            raw_move = 0.0

            if side == "LONG" and red_streak >= MAX_STREAK_EXIT:
                should_exit = True
                exit_reason = f"Salida por Tiempo ({MAX_STREAK_EXIT}ª Vela Roja Consecutiva)"
                raw_move = ((c_1h - entry_p) / entry_p) - 2 * FEE_RATE
            elif side == "SHORT" and green_streak >= MAX_STREAK_EXIT:
                should_exit = True
                exit_reason = f"Salida por Tiempo ({MAX_STREAK_EXIT}ª Vela Verde Consecutiva)"
                raw_move = ((entry_p - c_1h) / entry_p) - 2 * FEE_RATE

            if should_exit:
                dpnl = (raw_move / 0.01) * risk_usd
                cap_before = wallet["capital"]
                new_cap = cap_before + dpnl
                new_hwm = max(wallet["hwm"], new_cap)
                self.db.update_subwallet_capital(symbol, new_cap, new_hwm)
                
                trade_record = {
                    "symbol": symbol, "side": side, "trigger_time": trigger_t, "entry_time": entry_t,
                    "exit_time": candle_time_str, "entry_price": entry_p, "exit_price": c_1h,
                    "tp_price": float(order_state["tp_price"] or entry_p * 1.01),
                    "sl_price": float(order_state["sl_price"] or entry_p * 0.99),
                    "exit_reason": exit_reason, "raw_return_pct": round(((c_1h - entry_p)/entry_p * 100) if side=="LONG" else ((entry_p - c_1h)/entry_p * 100), 2),
                    "net_return_pct": round(raw_move * 100, 4), "risk_usd": risk_usd,
                    "dollar_pnl": round(dpnl, 4), "win": dpnl > 0, "capital_before": round(cap_before, 2),
                    "capital_after": round(new_cap, 2), "wallet_hwm": round(new_hwm, 2),
                    "cum_deposited_usd": round(wallet["cum_deposited"], 2)
                }
                self.db.record_completed_trade(trade_record)
                self.db.reset_order_state(symbol)
                
                self.notifier.notify_position_closed(
                    symbol=symbol, side=side, exit_time=candle_time_str, exit_price=c_1h,
                    exit_reason=exit_reason, dollar_pnl=dpnl, net_return_pct=raw_move*100,
                    win=dpnl > 0, new_capital=new_cap, new_hwm=new_hwm
                )
                logger.info(f"🏁 {symbol} Posición cerrada por 4ª vela. PnL: ${dpnl:+.4f} USD.")
            return

        # -------------------------------------------------------------
        # ESTADO 1: ORDEN LÍMITE PENDIENTE (Cancelación si cambia color o expira)
        # -------------------------------------------------------------
        if state == 1:
            side = order_state["side"]
            limit_p = float(order_state["limit_price"])
            cancel_order = False
            cancel_reason = None

            if side == "LONG":
                if is_green[-1]:
                    cancel_order = True
                    cancel_reason = "Vela horaria cerró Verde (Rebote sin tocar orden límite)"
                elif red_streak >= MAX_STREAK_EXIT:
                    cancel_order = True
                    cancel_reason = f"Alcanzó {MAX_STREAK_EXIT} velas rojas sin ser ejecutada"
            elif side == "SHORT":
                if is_red[-1]:
                    cancel_order = True
                    cancel_reason = "Vela horaria cerró Roja (Caída sin tocar orden límite)"
                elif green_streak >= MAX_STREAK_EXIT:
                    cancel_order = True
                    cancel_reason = f"Alcanzó {MAX_STREAK_EXIT} velas verdes sin ser ejecutada"

            if cancel_order:
                self.db.reset_order_state(symbol)
                self.notifier.notify_order_cancelled(
                    symbol=symbol, side=side, reason=cancel_reason,
                    limit_price=limit_p, cancel_time=candle_time_str
                )
                logger.info(f"❌ {symbol} Orden límite cancelada: {cancel_reason}")
            return

        # -------------------------------------------------------------
        # ESTADO 0: BÚSQUEDA DE NUEVA SEÑAL
        # -------------------------------------------------------------
        if state == 0:
            trade_risk = min(wallet["hwm"] * RISK_PCT_HWM, max(wallet["capital"] * 0.10, 0.20))
            
            # Señal LONG: Primera vela roja cerrada + Cierre > EMA 200
            if red_streak == 1 and c_1h > ema_val:
                limit_p = c_1h * (1.0 - LIMIT_DISCOUNT_PCT)
                self.db.set_pending_order(
                    symbol=symbol, side="LONG", trigger_time=candle_time_str,
                    limit_price=limit_p, risk_usd=trade_risk,
                    signal_ema=ema_val, signal_close=c_1h
                )
                self.notifier.notify_limit_placed(
                    symbol=symbol, side="LONG", trigger_time=candle_time_str,
                    signal_close=c_1h, signal_ema=ema_val, limit_price=limit_p,
                    risk_usd=trade_risk, capital=wallet["capital"], hwm=wallet["hwm"]
                )
                logger.info(f"🔔 {symbol} Señal LONG detectada. Orden límite colocada en ${limit_p:,.2f}")

            # Señal SHORT: Primera vela verde cerrada + Cierre < EMA 200
            elif green_streak == 1 and c_1h < ema_val:
                limit_p = c_1h * (1.0 + LIMIT_DISCOUNT_PCT)
                self.db.set_pending_order(
                    symbol=symbol, side="SHORT", trigger_time=candle_time_str,
                    limit_price=limit_p, risk_usd=trade_risk,
                    signal_ema=ema_val, signal_close=c_1h
                )
                self.notifier.notify_limit_placed(
                    symbol=symbol, side="SHORT", trigger_time=candle_time_str,
                    signal_close=c_1h, signal_ema=ema_val, limit_price=limit_p,
                    risk_usd=trade_risk, capital=wallet["capital"], hwm=wallet["hwm"]
                )
                logger.info(f"🔔 {symbol} Señal SHORT detectada. Orden límite colocada en ${limit_p:,.2f}")

    def evaluate_realtime_tick(self, symbol: str, current_price: float, candle_15m_high: Optional[float] = None, candle_15m_low: Optional[float] = None):
        """
        Se ejecuta cada 15 segundos con el precio actual o al cierre de sub-velas de 15m.
        Evalúa si la orden límite se llena o si una posición abierta toca TP o SL.
        """
        order_state = self.db.get_order_state(symbol)
        state = order_state.get("state", 0)
        if state == 0:
            return  # Nada que evaluar si está en búsqueda de señales

        now_str = datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
        h_price = candle_15m_high if candle_15m_high is not None else current_price
        l_price = candle_15m_low if candle_15m_low is not None else current_price
        wallet = self.db.get_subwallet(symbol)

        # -------------------------------------------------------------
        # EVALUAR LLENADO DE ORDEN LÍMITE PENDIENTE (State 1 -> 2)
        # -------------------------------------------------------------
        if state == 1:
            side = order_state["side"]
            limit_p = float(order_state["limit_price"])
            risk_usd = float(order_state["risk_usd"])
            filled = False

            if side == "LONG" and l_price <= limit_p:
                filled = True
                tp_p = limit_p * (1.0 + TP_PCT)
                sl_p = limit_p * (1.0 - SL_PCT)
            elif side == "SHORT" and h_price >= limit_p:
                filled = True
                tp_p = limit_p * (1.0 - TP_PCT)
                sl_p = limit_p * (1.0 + SL_PCT)

            if filled:
                self.db.set_position_filled(symbol, entry_time=now_str, fill_price=limit_p, tp_price=tp_p, sl_price=sl_p)
                self.notifier.notify_position_filled(
                    symbol=symbol, side=side, entry_time=now_str,
                    fill_price=limit_p, tp_price=tp_p, sl_price=sl_p, risk_usd=risk_usd
                )
                logger.info(f"🎯 {symbol} Orden {side} llenada en ${limit_p:,.2f}. TP: ${tp_p:,.2f} | SL: ${sl_p:,.2f}")
            return

        # -------------------------------------------------------------
        # EVALUAR CIERRE POR TAKE PROFIT O STOP LOSS (State 2 -> 0)
        # -------------------------------------------------------------
        if state == 2:
            side = order_state["side"]
            entry_p = float(order_state["fill_price"] or order_state["limit_price"])
            tp_p = float(order_state["tp_price"])
            sl_p = float(order_state["sl_price"])
            risk_usd = float(order_state["risk_usd"])
            trigger_t = order_state["trigger_time"]
            entry_t = order_state["entry_time"] or now_str

            closed = False
            exit_reason = None
            exit_p = None
            raw_move = 0.0

            if side == "LONG":
                hit_tp = h_price >= tp_p
                hit_sl = l_price <= sl_p
                if hit_tp and hit_sl:
                    closed = True; exit_reason = "Stop Loss (-1.0%) [Simultáneo en barra]"; exit_p = sl_p; raw_move = -0.01 - 2*FEE_RATE
                elif hit_tp:
                    closed = True; exit_reason = "Take Profit (+1.0%) 🎯"; exit_p = tp_p; raw_move = 0.01 - 2*FEE_RATE
                elif hit_sl:
                    closed = True; exit_reason = "Stop Loss (-1.0%) 🛑"; exit_p = sl_p; raw_move = -0.01 - 2*FEE_RATE
            elif side == "SHORT":
                hit_tp = l_price <= tp_p
                hit_sl = h_price >= sl_p
                if hit_tp and hit_sl:
                    closed = True; exit_reason = "Stop Loss (-1.0%) [Simultáneo en barra]"; exit_p = sl_p; raw_move = -0.01 - 2*FEE_RATE
                elif hit_tp:
                    closed = True; exit_reason = "Take Profit (+1.0%) 🎯"; exit_p = tp_p; raw_move = 0.01 - 2*FEE_RATE
                elif hit_sl:
                    closed = True; exit_reason = "Stop Loss (-1.0%) 🛑"; exit_p = sl_p; raw_move = -0.01 - 2*FEE_RATE

            if closed:
                dpnl = (raw_move / 0.01) * risk_usd
                cap_before = wallet["capital"]
                new_cap = cap_before + dpnl
                new_hwm = max(wallet["hwm"], new_cap)
                self.db.update_subwallet_capital(symbol, new_cap, new_hwm)
                
                trade_record = {
                    "symbol": symbol, "side": side, "trigger_time": trigger_t, "entry_time": entry_t,
                    "exit_time": now_str, "entry_price": entry_p, "exit_price": exit_p,
                    "tp_price": tp_p, "sl_price": sl_p, "exit_reason": exit_reason,
                    "raw_return_pct": round(((exit_p - entry_p)/entry_p * 100) if side=="LONG" else ((entry_p - exit_p)/entry_p * 100), 2),
                    "net_return_pct": round(raw_move * 100, 4), "risk_usd": risk_usd,
                    "dollar_pnl": round(dpnl, 4), "win": dpnl > 0, "capital_before": round(cap_before, 2),
                    "capital_after": round(new_cap, 2), "wallet_hwm": round(new_hwm, 2),
                    "cum_deposited_usd": round(wallet["cum_deposited"], 2)
                }
                self.db.record_completed_trade(trade_record)
                self.db.reset_order_state(symbol)
                
                self.notifier.notify_position_closed(
                    symbol=symbol, side=side, exit_time=now_str, exit_price=exit_p,
                    exit_reason=exit_reason, dollar_pnl=dpnl, net_return_pct=raw_move*100,
                    win=dpnl > 0, new_capital=new_cap, new_hwm=new_hwm
                )
                logger.info(f"🏁 {symbol} Posición cerrada por {exit_reason}. PnL: ${dpnl:+.4f} USD.")

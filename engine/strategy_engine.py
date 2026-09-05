"""
Motor de Estrategia Cuantitativa Híbrida Cripto (La Campeona 2% + El Francotirador 1%).
Implementa la lógica oficial de:
1. Filtro macro 1h con EMA 200 y racha de color (Trend-Following Pullback Reversion).
2. Orden límite con descuento del -1.0% respecto al cierre de 1h.
3. Dimensionamiento Asimétrico Híbrido:
   - Fase 1 (≤15m): Riesgo 3.0% HWM (2% Campeona + 1% Francotirador).
   - Expiración de Francotirador a los 15m sin llenado: Reducción de orden a 2.0% HWM.
   - Fase 2 (15m a 60m): Riesgo 2.0% HWM (Campeona).
   - Expiración total a los 60m sin llenado: Cancelación de orden (Retorno a IDLE).
4. Salidas por TP (+1.0%), SL (-1.0%) o salida preventiva en 4ª vela consecutiva contraria.
5. Comisiones realistas de Binance Futures: Maker 0.02% (Límite TP y entrada), Taker 0.04% (SL y mercado).
"""

import logging
import datetime
import pandas as pd
import numpy as np
from typing import Dict, Any, Optional

from config import (
    EMA_PERIOD, LIMIT_DISCOUNT_PCT, TP_PCT, SL_PCT,
    MAX_STREAK_EXIT, FEE_MAKER, FEE_TAKER,
    RISK_CAMPEONA, RISK_FRANCO, RISK_HYBRID_TOTAL,
    MAX_FILL_MINUTES_FRANCO, MAX_FILL_MINUTES_CAMPEONA
)
from storage.database import DatabaseManager
from notifier.telegram_bot import TelegramNotifier

logger = logging.getLogger("StrategyEngine")

def calc_ema(series: pd.Series, length: int) -> pd.Series:
    return series.ewm(span=length, adjust=False).mean()

class StrategyEngine:
    def __init__(self, db: DatabaseManager, notifier: TelegramNotifier, feed: Optional[Any] = None):
        self.db = db
        self.notifier = notifier
        self.feed = feed
        self.latest_dfs_1h: Dict[str, pd.DataFrame] = {}

    def evaluate_hourly_close(self, symbol: str, df_1h: pd.DataFrame):
        """
        Se ejecuta inmediatamente tras el cierre de cada vela horaria (:00:00 UTC).
        Evalúa señales de entrada híbrida, expiraciones de 60m o salidas por 4ª vela contraria.
        """
        if len(df_1h) < EMA_PERIOD + 10:
            logger.warning(f"{symbol}: Datos insuficientes para calcular EMA {EMA_PERIOD} (filas: {len(df_1h)})")
            return

        # Calcular EMA 200 sobre velas horarias
        df_1h = df_1h.copy()
        df_1h["EMA_200"] = calc_ema(df_1h["Close"], EMA_PERIOD)
        self.latest_dfs_1h[symbol] = df_1h
        
        # Última vela horaria cerrada (índice -2 si la última fila -1 es la vela actualmente en curso)
        closed_candle = df_1h.iloc[-2]
        c_1h = float(closed_candle["Close"])
        o_1h = float(closed_candle["Open"])
        ema_val = float(closed_candle["EMA_200"])
        # La señal y la orden límite se originan al CIERRE de la vela horaria (Open Time + 1h),
        # que es cuando la orden entra al mercado y comienzan a correr los 15 min (Francotirador) y 60 min (Campeona).
        candle_close_dt = pd.to_datetime(closed_candle["Open Time"]) + pd.Timedelta(hours=1)
        candle_time_str = candle_close_dt.strftime("%Y-%m-%d %H:%M:%S")

        # Calcular rachas de color de velas consecutivas cerradas
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
        # ESTADO 2: POSICIÓN ACTIVA (Evaluación de salida preventiva por 4ª vela)
        # -------------------------------------------------------------
        if state == 2:
            side = order_state["side"]
            entry_p = float(order_state["fill_price"] or order_state["limit_price"])
            risk_usd = float(order_state["risk_usd"])
            execution_type = order_state.get("execution_type", "CAMPEONA_NORMAL_2PCT")
            effective_risk_pct = float(order_state.get("effective_risk_pct", RISK_CAMPEONA))
            trigger_t = order_state["trigger_time"]
            entry_t = order_state["entry_time"] or candle_time_str

            should_exit = False
            exit_reason = None
            raw_move = 0.0

            # Salida forzosa al acumular 4 velas horarias consecutivas adversas
            if side == "LONG" and red_streak >= MAX_STREAK_EXIT:
                should_exit = True
                exit_reason = f"Salida Preventiva ({MAX_STREAK_EXIT}ª Vela Roja 1h)"
                raw_move = ((c_1h - entry_p) / entry_p) - (FEE_MAKER + FEE_TAKER)
            elif side == "SHORT" and green_streak >= MAX_STREAK_EXIT:
                should_exit = True
                exit_reason = f"Salida Preventiva ({MAX_STREAK_EXIT}ª Vela Verde 1h)"
                raw_move = ((entry_p - c_1h) / entry_p) - (FEE_MAKER + FEE_TAKER)

            if should_exit:
                dpnl = (raw_move / 0.010) * risk_usd
                cap_before = wallet["capital"]
                new_cap = cap_before + dpnl
                new_hwm = max(wallet["hwm"], new_cap)
                self.db.update_subwallet_capital(symbol, new_cap, new_hwm)
                
                trade_record = {
                    "symbol": symbol, "side": side, "trigger_time": trigger_t, "entry_time": entry_t,
                    "exit_time": candle_time_str, "entry_price": entry_p, "exit_price": c_1h,
                    "tp_price": float(order_state["tp_price"] or entry_p * (1.0 + TP_PCT if side == "LONG" else 1.0 - TP_PCT)),
                    "sl_price": float(order_state["sl_price"] or entry_p * (1.0 - SL_PCT if side == "LONG" else 1.0 + SL_PCT)),
                    "exit_reason": exit_reason,
                    "raw_return_pct": round(((c_1h - entry_p)/entry_p * 100) if side=="LONG" else ((entry_p - c_1h)/entry_p * 100), 2),
                    "net_return_pct": round(raw_move * 100, 4), "risk_usd": risk_usd,
                    "dollar_pnl": round(dpnl, 4), "win": dpnl > 0, "capital_before": round(cap_before, 2),
                    "capital_after": round(new_cap, 2), "wallet_hwm": round(new_hwm, 2),
                    "cum_deposited_usd": round(wallet["cum_deposited"], 2),
                    "execution_type": execution_type,
                    "risk_pct": effective_risk_pct
                }
                self.db.record_completed_trade(trade_record)
                self.db.reset_order_state(symbol)
                
                self.notifier.notify_position_closed(
                    symbol=symbol, side=side, exit_time=candle_time_str, exit_price=c_1h,
                    exit_reason=exit_reason, dollar_pnl=dpnl, net_return_pct=raw_move*100,
                    win=dpnl > 0, new_capital=new_cap, new_hwm=new_hwm,
                    df_1h=df_1h, entry_price=entry_p, execution_type=execution_type
                )
                logger.info(f"🏁 {symbol} Posición cerrada por 4ª vela contraria. PnL: ${dpnl:+.4f} USD.")
            return

        # -------------------------------------------------------------
        # ESTADO 1: ORDEN LÍMITE PENDIENTE (Expiración de 60 minutos al cierre horario)
        # -------------------------------------------------------------
        if state == 1:
            side = order_state["side"]
            limit_p = float(order_state["limit_price"])
            cancel_reason = "Expiración tras 60 minutos sin llenado (Fin de ventana Campeona)"

            self.db.reset_order_state(symbol)
            self.notifier.notify_order_cancelled(
                symbol=symbol, side=side, reason=cancel_reason,
                limit_price=limit_p, cancel_time=candle_time_str, df_1h=df_1h
            )
            logger.info(f"❌ {symbol} Orden límite híbrida cancelada tras 60m: {cancel_reason}")
            return

        # -------------------------------------------------------------
        # ESTADO 0: BÚSQUEDA DE NUEVA SEÑAL MACRO (1h)
        # -------------------------------------------------------------
        if state == 0:
            hwm_val = wallet["hwm"]
            risk_campeona = hwm_val * RISK_CAMPEONA
            risk_franco = hwm_val * RISK_FRANCO
            initial_total_risk = risk_campeona + risk_franco  # 3.0% HWM
            
            # Señal LONG: Cierre 1h > EMA 200 y primera vela roja de retroceso (red_streak == 1)
            if red_streak == 1 and c_1h > ema_val:
                limit_p = c_1h * (1.0 - LIMIT_DISCOUNT_PCT)  # -1.0% de descuento
                self.db.set_pending_order(
                    symbol=symbol, side="LONG", trigger_time=candle_time_str,
                    limit_price=limit_p, risk_usd=initial_total_risk,
                    signal_ema=ema_val, signal_close=c_1h,
                    franco_active=1, candles_15m_elapsed=0,
                    effective_risk_pct=RISK_HYBRID_TOTAL
                )
                self.notifier.notify_limit_placed(
                    symbol=symbol, side="LONG", trigger_time=candle_time_str,
                    signal_close=c_1h, signal_ema=ema_val, limit_price=limit_p,
                    risk_usd=initial_total_risk, capital=wallet["capital"], hwm=hwm_val,
                    df_1h=df_1h, risk_pct=RISK_HYBRID_TOTAL,
                    risk_campeona_usd=risk_campeona, risk_franco_usd=risk_franco
                )
                logger.info(f"🔔 {symbol} Señal LONG detectada. Orden híbrida colocada en ${limit_p:,.2f} (Riesgo inicial 3%: ${initial_total_risk:,.2f})")

            # Señal SHORT: Cierre 1h < EMA 200 y primera vela verde de retroceso (green_streak == 1)
            elif green_streak == 1 and c_1h < ema_val:
                limit_p = c_1h * (1.0 + LIMIT_DISCOUNT_PCT)  # +1.0% de recargo
                self.db.set_pending_order(
                    symbol=symbol, side="SHORT", trigger_time=candle_time_str,
                    limit_price=limit_p, risk_usd=initial_total_risk,
                    signal_ema=ema_val, signal_close=c_1h,
                    franco_active=1, candles_15m_elapsed=0,
                    effective_risk_pct=RISK_HYBRID_TOTAL
                )
                self.notifier.notify_limit_placed(
                    symbol=symbol, side="SHORT", trigger_time=candle_time_str,
                    signal_close=c_1h, signal_ema=ema_val, limit_price=limit_p,
                    risk_usd=initial_total_risk, capital=wallet["capital"], hwm=hwm_val,
                    df_1h=df_1h, risk_pct=RISK_HYBRID_TOTAL,
                    risk_campeona_usd=risk_campeona, risk_franco_usd=risk_franco
                )
                logger.info(f"🔔 {symbol} Señal SHORT detectada. Orden híbrida colocada en ${limit_p:,.2f} (Riesgo inicial 3%: ${initial_total_risk:,.2f})")

    def evaluate_realtime_tick(
        self, symbol: str, current_price: float,
        candle_15m_high: Optional[float] = None,
        candle_15m_low: Optional[float] = None,
        current_time: Optional[datetime.datetime] = None
    ):
        """
        Se ejecuta cada 15 segundos o al cierre de sub-velas de 15m.
        Gestiona:
        1. Expiración del tramo Francotirador a los 15 minutos (reduce riesgo a 2% HWM).
        2. Expiración total a los 60 minutos (cancela orden y regresa a IDLE).
        3. Llenado límite según Fase 1 (≤15m, Francotirador Boost 3%) o Fase 2 (15m-60m, Campeona Normal 2%).
        4. Resolución de Take Profit (+1.0%) y Stop Loss (-1.0%).
        """
        order_state = self.db.get_order_state(symbol)
        state = order_state.get("state", 0)
        if state == 0:
            return

        now_dt = current_time if current_time is not None else datetime.datetime.utcnow()
        now_str = now_dt.strftime("%Y-%m-%d %H:%M:%S")
        h_price = candle_15m_high if candle_15m_high is not None else current_price
        l_price = candle_15m_low if candle_15m_low is not None else current_price
        wallet = self.db.get_subwallet(symbol)
        df_1h = self.latest_dfs_1h.get(symbol)

        # -------------------------------------------------------------
        # EVALUAR ESTADO 1: ORDEN LÍMITE PENDIENTE (Expiraciones y Llenado)
        # -------------------------------------------------------------
        if state == 1:
            side = order_state["side"]
            limit_p = float(order_state["limit_price"])
            trigger_t_str = order_state["trigger_time"]
            franco_active = int(order_state.get("franco_active", 1) or 1)
            
            # Calcular tiempo transcurrido desde la emisión de la señal
            try:
                trigger_dt = datetime.datetime.strptime(trigger_t_str, "%Y-%m-%d %H:%M:%S")
                elapsed_seconds = max(0.0, (now_dt - trigger_dt).total_seconds())
            except Exception:
                elapsed_seconds = 0.0

            # 1. Expiración del Tramo Francotirador (tras 15 minutos sin fill)
            if elapsed_seconds >= (MAX_FILL_MINUTES_FRANCO * 60) and franco_active == 1:
                new_risk_usd = wallet["hwm"] * RISK_CAMPEONA  # Reducir a 2.0% HWM
                self.db.expire_franco_tranche(symbol, new_risk_usd)
                franco_active = 0
                order_state["franco_active"] = 0
                order_state["risk_usd"] = new_risk_usd
                order_state["effective_risk_pct"] = RISK_CAMPEONA

                self.notifier.notify_franco_expired(
                    symbol=symbol, side=side, limit_price=limit_p,
                    new_risk_usd=new_risk_usd, expire_time=now_str
                )
                logger.info(f"🎯 [{symbol}] Tramo Francotirador (1%) expirado tras 15m. Orden ajustada a 2.0% HWM (${new_risk_usd:,.2f} USD).")

            # 2. Expiración Total tras 60 minutos sin fill (Fin de ventana Campeona)
            if elapsed_seconds >= (MAX_FILL_MINUTES_CAMPEONA * 60):
                self.db.reset_order_state(symbol)
                self.notifier.notify_order_cancelled(
                    symbol=symbol, side=side,
                    reason="Expiración tras 60 minutos sin llenado",
                    limit_price=limit_p, cancel_time=now_str, df_1h=df_1h
                )
                logger.info(f"❌ [{symbol}] Orden límite cancelada tras 60 minutos sin llenado. Retorno a IDLE.")
                return

            # 3. Comprobar si el precio de mercado llena la orden límite
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
                # Determinar si fue llenado rápido en Fase 1 (≤15m) o en Fase 2 (15m-60m)
                if franco_active == 1 and elapsed_seconds < (MAX_FILL_MINUTES_FRANCO * 60):
                    execution_type = "FRANCOTIRADOR_BOOST_3PCT"
                    effective_risk = RISK_HYBRID_TOTAL
                    risk_usd = wallet["hwm"] * RISK_HYBRID_TOTAL
                else:
                    execution_type = "CAMPEONA_NORMAL_2PCT"
                    effective_risk = RISK_CAMPEONA
                    risk_usd = wallet["hwm"] * RISK_CAMPEONA

                # Refrescar klines para garantizar que el gráfico contenga la última acción del precio
                if self.feed is not None:
                    try:
                        fresh_df = self.feed.fetch_klines(symbol, interval="1h", limit=1000)
                        if fresh_df is not None and not fresh_df.empty:
                            fresh_df["EMA_200"] = calc_ema(fresh_df["Close"], EMA_PERIOD)
                            df_1h = fresh_df
                            self.latest_dfs_1h[symbol] = fresh_df
                    except Exception as e:
                        logger.warning(f"No se pudieron actualizar klines en vivo para {symbol}: {e}")

                self.db.set_position_filled(
                    symbol=symbol, entry_time=now_str, fill_price=limit_p,
                    tp_price=tp_p, sl_price=sl_p, execution_type=execution_type,
                    effective_risk_pct=effective_risk, risk_usd=risk_usd
                )
                self.notifier.notify_position_filled(
                    symbol=symbol, side=side, entry_time=now_str,
                    fill_price=limit_p, tp_price=tp_p, sl_price=sl_p, risk_usd=risk_usd,
                    df_1h=df_1h, execution_type=execution_type, risk_pct=effective_risk
                )
                logger.info(f"🎯 [{symbol}] Posición {side} LLENADA ({execution_type}). Riesgo: {effective_risk*100:.1f}% (${risk_usd:,.2f} USD). TP: ${tp_p:,.2f} | SL: ${sl_p:,.2f}")
            return

        # -------------------------------------------------------------
        # EVALUAR ESTADO 2: POSICIÓN ACTIVA (Resolución TP / SL)
        # -------------------------------------------------------------
        if state == 2:
            side = order_state["side"]
            entry_p = float(order_state["fill_price"] or order_state["limit_price"])
            tp_p = float(order_state["tp_price"])
            sl_p = float(order_state["sl_price"])
            risk_usd = float(order_state["risk_usd"])
            execution_type = order_state.get("execution_type", "CAMPEONA_NORMAL_2PCT")
            effective_risk_pct = float(order_state.get("effective_risk_pct", RISK_CAMPEONA))
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
                    # En toque simultáneo, asumir la ejecución conservadora del Stop Loss
                    closed = True
                    exit_reason = "Stop Loss (-1.0%) [Simultáneo]"
                    exit_p = sl_p
                    raw_move = -SL_PCT - (FEE_MAKER + FEE_TAKER)  # -1.06%
                elif hit_tp:
                    closed = True
                    exit_reason = "Take Profit (+1.0%) 🎯"
                    exit_p = tp_p
                    raw_move = TP_PCT - (FEE_MAKER + FEE_MAKER)    # +0.96%
                elif hit_sl:
                    closed = True
                    exit_reason = "Stop Loss (-1.0%) 🛑"
                    exit_p = sl_p
                    raw_move = -SL_PCT - (FEE_MAKER + FEE_TAKER)  # -1.06%
            elif side == "SHORT":
                hit_tp = l_price <= tp_p
                hit_sl = h_price >= sl_p
                if hit_tp and hit_sl:
                    closed = True
                    exit_reason = "Stop Loss (-1.0%) [Simultáneo]"
                    exit_p = sl_p
                    raw_move = -SL_PCT - (FEE_MAKER + FEE_TAKER)  # -1.06%
                elif hit_tp:
                    closed = True
                    exit_reason = "Take Profit (+1.0%) 🎯"
                    exit_p = tp_p
                    raw_move = TP_PCT - (FEE_MAKER + FEE_MAKER)    # +0.96%
                elif hit_sl:
                    closed = True
                    exit_reason = "Stop Loss (-1.0%) 🛑"
                    exit_p = sl_p
                    raw_move = -SL_PCT - (FEE_MAKER + FEE_TAKER)  # -1.06%

            if closed:
                dpnl = (raw_move / 0.010) * risk_usd
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
                    "cum_deposited_usd": round(wallet["cum_deposited"], 2),
                    "execution_type": execution_type,
                    "risk_pct": effective_risk_pct
                }
                self.db.record_completed_trade(trade_record)
                self.db.reset_order_state(symbol)
                
                self.notifier.notify_position_closed(
                    symbol=symbol, side=side, exit_time=now_str, exit_price=exit_p,
                    exit_reason=exit_reason, dollar_pnl=dpnl, net_return_pct=raw_move*100,
                    win=dpnl > 0, new_capital=new_cap, new_hwm=new_hwm,
                    df_1h=df_1h, entry_price=entry_p, execution_type=execution_type
                )
                logger.info(f"🏁 [{symbol}] Posición cerrada por {exit_reason}. PnL: ${dpnl:+.4f} USD ({execution_type}).")

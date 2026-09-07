import logging
import datetime
import pandas as pd
import numpy as np
from typing import Dict, Any, Optional

from config import (
    RISK_PCT, ORDER_TIMEOUT_MINUTES, FEE_MAKER, FEE_TAKER
)
from storage.database import DatabaseManager
from notifier.telegram_bot import TelegramNotifier

logger = logging.getLogger("StrategyEngine")

def calc_ema(series: pd.Series, length: int) -> pd.Series:
    return series.ewm(span=length, adjust=False).mean()

def calc_rsi(series: pd.Series, periods: int = 14) -> pd.Series:
    delta = series.diff()
    up = delta.clip(lower=0)
    down = -1 * delta.clip(upper=0)
    ema_up = up.ewm(com=periods - 1, adjust=False).mean()
    ema_down = down.ewm(com=periods - 1, adjust=False).mean()
    rs = ema_up / ema_down
    return 100 - (100 / (1 + rs))

class StrategyEngine:
    def __init__(self, db: DatabaseManager, notifier: TelegramNotifier, feed: Optional[Any] = None):
        self.db = db
        self.notifier = notifier
        self.feed = feed
        self.last_evaluated_1h: Dict[str, str] = {}

    def evaluate_klines(self, symbol: str, df_15m: pd.DataFrame, df_1h: pd.DataFrame, df_4h: pd.DataFrame):
        if len(df_15m) < 55 or len(df_1h) < 55 or len(df_4h) < 55:
            return

        # Validamos que estemos leyendo la vela cerrada
        c_15m = float(df_15m.iloc[-2]["Close"])
        c_1h = float(df_1h.iloc[-2]["Close"])
        c_4h = float(df_4h.iloc[-2]["Close"])

        ema50_1h = calc_ema(df_1h["Close"], 50).iloc[-2]
        ema50_4h = calc_ema(df_4h["Close"], 50).iloc[-2]

        rsi14_1h = calc_rsi(df_1h["Close"], 14).iloc[-2]
        rsi7_1h = calc_rsi(df_1h["Close"], 7).iloc[-2]
        
        rsi14_15m = calc_rsi(df_15m["Close"], 14).iloc[-2]
        rsi7_15m = calc_rsi(df_15m["Close"], 7).iloc[-2]
        
        candle_15m_dt = pd.to_datetime(df_15m.iloc[-2]["Open Time"]) + pd.Timedelta(minutes=15)
        candle_15m_str = candle_15m_dt.strftime("%Y-%m-%d %H:%M:%S")

        candle_1h_dt = pd.to_datetime(df_1h.iloc[-2]["Open Time"]) + pd.Timedelta(hours=1)
        candle_1h_str = candle_1h_dt.strftime("%Y-%m-%d %H:%M:%S")

        wallet = self.db.get_subwallet(symbol)
        if not wallet:
            return
        
        hwm_val = wallet["hwm"]
        risk_usd = hwm_val * RISK_PCT

        is_new_1h = (self.last_evaluated_1h.get(symbol) != candle_1h_str)

        # Evaluate E1 (1h) - Trend: C_4h vs EMA50_4h
        if is_new_1h:
            state_e1 = self.db.get_order_state(symbol, "E1").get("state", 0)
            if state_e1 == 0:
                if c_4h > ema50_4h and rsi14_1h < 38: # LONG
                    self._place_order(symbol, "E1", "LONG", candle_1h_str, c_1h * 0.997, risk_usd, c_1h)
                elif c_4h < ema50_4h and rsi14_1h > 62: # SHORT
                    self._place_order(symbol, "E1", "SHORT", candle_1h_str, c_1h * 1.003, risk_usd, c_1h)
                
        # Evaluate E2 (15m)
        state_e2 = self.db.get_order_state(symbol, "E2").get("state", 0)
        if state_e2 == 0:
            if c_4h > ema50_4h and c_1h > ema50_1h and rsi14_15m < 30: # LONG
                self._place_order(symbol, "E2", "LONG", candle_15m_str, c_15m * 0.998, risk_usd, c_15m)
            elif c_4h < ema50_4h and c_1h < ema50_1h and rsi14_15m > 70: # SHORT
                self._place_order(symbol, "E2", "SHORT", candle_15m_str, c_15m * 1.002, risk_usd, c_15m)

        # Evaluate E3 (1h)
        if is_new_1h:
            state_e3 = self.db.get_order_state(symbol, "E3").get("state", 0)
            if state_e3 == 0:
                if c_4h > ema50_4h and rsi7_1h < 25: # LONG
                    self._place_order(symbol, "E3", "LONG", candle_1h_str, c_1h * 0.998, risk_usd, c_1h)
                elif c_4h < ema50_4h and rsi7_1h > 75: # SHORT
                    self._place_order(symbol, "E3", "SHORT", candle_1h_str, c_1h * 1.002, risk_usd, c_1h)

        # Evaluate E4 (15m)
        state_e4 = self.db.get_order_state(symbol, "E4").get("state", 0)
        if state_e4 == 0:
            if c_4h > ema50_4h and c_1h > ema50_1h and rsi7_15m < 20: # LONG
                self._place_order(symbol, "E4", "LONG", candle_15m_str, c_15m * 0.998, risk_usd, c_15m)
            elif c_4h < ema50_4h and c_1h < ema50_1h and rsi7_15m > 80: # SHORT
                self._place_order(symbol, "E4", "SHORT", candle_15m_str, c_15m * 1.002, risk_usd, c_15m)

        # Evaluate E5 (15m) - Market Order
        state_e5 = self.db.get_order_state(symbol, "E5").get("state", 0)
        if state_e5 == 0:
            if c_4h > ema50_4h and c_1h > ema50_1h and rsi14_15m < 35: # LONG
                self._fill_market_order(symbol, "E5", "LONG", candle_15m_str, c_15m, risk_usd)
            elif c_4h < ema50_4h and c_1h < ema50_1h and rsi14_15m > 65: # SHORT
                self._fill_market_order(symbol, "E5", "SHORT", candle_15m_str, c_15m, risk_usd)
                
        if is_new_1h:
            self.last_evaluated_1h[symbol] = candle_1h_str

    def _place_order(self, symbol, strategy_id, side, trigger_time, limit_price, risk_usd, signal_close):
        self.db.set_pending_order(symbol, strategy_id, side, trigger_time, limit_price, risk_usd)
        logger.info(f"[{symbol}] {strategy_id} Señal {side} detectada. Orden límite en ${limit_price:,.2f} (Riesgo: ${risk_usd:,.2f})")
        wallet = self.db.get_subwallet(symbol)
        self.notifier.notify_limit_placed(symbol, strategy_id, side, trigger_time, limit_price, risk_usd, wallet["capital"], wallet["hwm"])
        
    def _fill_market_order(self, symbol, strategy_id, side, trigger_time, fill_price, risk_usd):
        tp_pct = 0.018
        sl_pct = 0.010
        tp_price = fill_price * (1.0 + tp_pct) if side == "LONG" else fill_price * (1.0 - tp_pct)
        sl_price = fill_price * (1.0 - sl_pct) if side == "LONG" else fill_price * (1.0 + sl_pct)
        
        self.db.set_pending_order(symbol, strategy_id, side, trigger_time, fill_price, risk_usd)
        self.db.set_position_filled(symbol, strategy_id, trigger_time, fill_price, tp_price, sl_price)
        logger.info(f"🎯 [{symbol}] {strategy_id} Posición {side} MARKET LLENADA a ${fill_price:,.2f}. TP: ${tp_price:,.2f} | SL: ${sl_price:,.2f}")
        self.notifier.notify_position_filled(symbol, strategy_id, side, trigger_time, fill_price, tp_price, sl_price, risk_usd)

    def evaluate_realtime_tick(self, symbol: str, current_price: float, current_time: Optional[datetime.datetime] = None):
        orders = self.db.get_all_orders_for_symbol(symbol)
        now_dt = current_time if current_time is not None else datetime.datetime.utcnow()
        now_str = now_dt.strftime("%Y-%m-%d %H:%M:%S")
        wallet = self.db.get_subwallet(symbol)

        strat_configs = {
            "E1": {"tp": 0.022, "sl": 0.012},
            "E2": {"tp": 0.018, "sl": 0.010},
            "E3": {"tp": 0.020, "sl": 0.014},
            "E4": {"tp": 0.018, "sl": 0.012},
            "E5": {"tp": 0.018, "sl": 0.010},
        }

        for order in orders:
            state = order["state"]
            if state == 0:
                continue

            strategy_id = order["strategy_id"]
            side = order["side"]
            
            if state == 1:
                limit_p = float(order["limit_price"])
                trigger_t_str = order["trigger_time"]
                try:
                    trigger_dt = datetime.datetime.strptime(trigger_t_str, "%Y-%m-%d %H:%M:%S")
                    elapsed_seconds = max(0.0, (now_dt - trigger_dt).total_seconds())
                except Exception:
                    elapsed_seconds = 0.0

                if elapsed_seconds >= (ORDER_TIMEOUT_MINUTES * 60):
                    self.db.reset_order_state(symbol, strategy_id)
                    logger.info(f"❌ [{symbol}] {strategy_id} Orden límite expirada tras {ORDER_TIMEOUT_MINUTES} minutos.")
                    self.notifier.notify_order_cancelled(symbol, strategy_id, side, f"Expirada tras {ORDER_TIMEOUT_MINUTES} min", limit_p, now_str)
                    continue

                filled = False
                if side == "LONG" and current_price <= limit_p:
                    filled = True
                elif side == "SHORT" and current_price >= limit_p:
                    filled = True

                if filled:
                    tp_pct = strat_configs[strategy_id]["tp"]
                    sl_pct = strat_configs[strategy_id]["sl"]
                    
                    tp_price = limit_p * (1.0 + tp_pct) if side == "LONG" else limit_p * (1.0 - tp_pct)
                    sl_price = limit_p * (1.0 - sl_pct) if side == "LONG" else limit_p * (1.0 + sl_pct)

                    self.db.set_position_filled(symbol, strategy_id, now_str, limit_p, tp_price, sl_price)
                    logger.info(f"🎯 [{symbol}] {strategy_id} Posición {side} LLENADA. Entrada: ${limit_p:,.2f}. TP: ${tp_price:,.2f} | SL: ${sl_price:,.2f}")
                    self.notifier.notify_position_filled(symbol, strategy_id, side, now_str, limit_p, tp_price, sl_price, risk_usd)

            elif state == 2:
                entry_p = float(order["fill_price"])
                tp_p = float(order["tp_price"])
                sl_p = float(order["sl_price"])
                risk_usd = float(order["risk_usd"])

                hit_tp = False
                hit_sl = False

                if side == "LONG":
                    if current_price >= tp_p: hit_tp = True
                    if current_price <= sl_p: hit_sl = True
                elif side == "SHORT":
                    if current_price <= tp_p: hit_tp = True
                    if current_price >= sl_p: hit_sl = True

                if hit_tp or hit_sl:
                    tp_pct = strat_configs[strategy_id]["tp"]
                    sl_pct = strat_configs[strategy_id]["sl"]

                    closed = True
                    if hit_tp and hit_sl:
                        exit_reason = f"Stop Loss (-{sl_pct*100}%) [Simultáneo]"
                        exit_p = sl_p
                        fee_impact = FEE_MAKER + FEE_TAKER if strategy_id != "E5" else FEE_TAKER * 2
                        raw_move = -sl_pct - fee_impact
                    elif hit_tp:
                        exit_reason = f"Take Profit (+{tp_pct*100}%) 🎯"
                        exit_p = tp_p
                        fee_impact = FEE_MAKER + FEE_TAKER if strategy_id != "E5" else FEE_TAKER * 2
                        raw_move = tp_pct - fee_impact
                    elif hit_sl:
                        exit_reason = f"Stop Loss (-{sl_pct*100}%) 🛑"
                        exit_p = sl_p
                        fee_impact = FEE_MAKER + FEE_TAKER if strategy_id != "E5" else FEE_TAKER * 2
                        raw_move = -sl_pct - fee_impact

                    perdida_total_esperada = sl_pct + fee_impact
                    dpnl = (raw_move / perdida_total_esperada) * risk_usd

                    cap_before = wallet["capital"]
                    new_cap = cap_before + dpnl
                    new_hwm = max(wallet["hwm"], new_cap)
                    self.db.update_subwallet_capital(symbol, new_cap, new_hwm)

                    trade_record = {
                        "symbol": symbol, "strategy_id": strategy_id, "side": side, 
                        "trigger_time": order["trigger_time"], "entry_time": order["entry_time"],
                        "exit_time": now_str, "entry_price": entry_p, "exit_price": exit_p,
                        "tp_price": tp_p, "sl_price": sl_p, "exit_reason": exit_reason,
                        "raw_return_pct": round(((exit_p - entry_p)/entry_p * 100) if side=="LONG" else ((entry_p - exit_p)/entry_p * 100), 2),
                        "net_return_pct": round(raw_move * 100, 4), "risk_usd": risk_usd,
                        "dollar_pnl": round(dpnl, 4), "win": dpnl > 0, "capital_before": round(cap_before, 2),
                        "capital_after": round(new_cap, 2), "wallet_hwm": round(new_hwm, 2),
                        "cum_deposited_usd": round(wallet["cum_deposited"], 2)
                    }
                    self.db.record_completed_trade(trade_record)
                    self.db.reset_order_state(symbol, strategy_id)
                    logger.info(f"🏁 [{symbol}] {strategy_id} Posición cerrada por {exit_reason}. PnL: ${dpnl:+.4f} USD. Capital actual: ${new_cap:,.2f}")
                    self.notifier.notify_position_closed(symbol, strategy_id, side, now_str, exit_p, exit_reason, dpnl, raw_move * 100, dpnl > 0, new_cap, new_hwm)

"""
Módulo de Persistencia en Base de Datos SQLite (Modo WAL).
Garantiza tolerancia a fallos, cortes de energía y reinicios abruptos en la Raspberry Pi.
"""

import sqlite3
import datetime
import logging
from typing import Dict, List, Optional, Any
from config import DB_PATH, SYMBOLS, INITIAL_CAPITAL, WEEKLY_DEPOSIT, MAX_DEPOSIT_TOTAL

logger = logging.getLogger("DatabaseManager")

class DatabaseManager:
    def __init__(self, db_path: str = DB_PATH):
        self.db_path = db_path
        self._init_db()

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=30.0)
        conn.row_factory = sqlite3.Row
        # Activar modo WAL para máxima concurrencia y protección ante cortes de energía
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA synchronous=NORMAL;")
        return conn

    def _init_db(self):
        """Crea las tablas necesarias si no existen."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            
            # 1. Tabla de Subcarteras y Gestión HWM
            cursor.execute("""
            CREATE TABLE IF NOT EXISTS subwallets (
                symbol TEXT PRIMARY KEY,
                capital REAL NOT NULL,
                hwm REAL NOT NULL,
                cum_deposited REAL NOT NULL,
                last_deposit_time TEXT,
                updated_at TEXT NOT NULL
            );
            """)

            # 2. Tabla de Estado de Órdenes y Posiciones Activas
            cursor.execute("""
            CREATE TABLE IF NOT EXISTS active_orders (
                symbol TEXT PRIMARY KEY,
                state INTEGER NOT NULL DEFAULT 0, -- 0: IDLE, 1: PENDING, 2: IN_POS
                side TEXT,                        -- LONG / SHORT
                trigger_time TEXT,
                entry_time TEXT,
                limit_price REAL,
                tp_price REAL,
                sl_price REAL,
                risk_usd REAL,
                signal_ema REAL,
                signal_close REAL,
                fill_price REAL,
                updated_at TEXT NOT NULL
            );
            """)

            # 3. Tabla Histórica de Transacciones Completadas
            cursor.execute("""
            CREATE TABLE IF NOT EXISTS trade_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL,
                side TEXT NOT NULL,
                trigger_time TEXT NOT NULL,
                entry_time TEXT NOT NULL,
                exit_time TEXT NOT NULL,
                entry_price REAL NOT NULL,
                exit_price REAL NOT NULL,
                tp_price REAL NOT NULL,
                sl_price REAL NOT NULL,
                exit_reason TEXT NOT NULL,
                raw_return_pct REAL NOT NULL,
                net_return_pct REAL NOT NULL,
                risk_usd REAL NOT NULL,
                dollar_pnl REAL NOT NULL,
                win INTEGER NOT NULL,
                capital_before REAL NOT NULL,
                capital_after REAL NOT NULL,
                wallet_hwm REAL NOT NULL,
                cum_deposited_usd REAL NOT NULL,
                created_at TEXT NOT NULL
            );
            """)

            # 4. Tabla Cola de Mensajes Telegram Offline
            cursor.execute("""
            CREATE TABLE IF NOT EXISTS telegram_outbox (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                message TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'PENDING', -- PENDING, SENT, FAILED
                retry_count INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                sent_at TEXT
            );
            """)
            conn.commit()

        # Inicializar subcarteras si la BD es nueva
        self._bootstrap_subwallets()

    def _bootstrap_subwallets(self):
        """Inicializa las subcarteras para los símbolos configurados si no existen."""
        n_symbols = len(SYMBOLS)
        sub_init = INITIAL_CAPITAL / n_symbols if n_symbols > 0 else 20.0
        now_str = datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")

        with self._get_connection() as conn:
            cursor = conn.cursor()
            for sym in SYMBOLS:
                cursor.execute("SELECT symbol FROM subwallets WHERE symbol = ?", (sym,))
                if not cursor.fetchone():
                    cursor.execute("""
                    INSERT INTO subwallets (symbol, capital, hwm, cum_deposited, last_deposit_time, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """, (sym, sub_init, sub_init, sub_init, now_str, now_str))
                
                cursor.execute("SELECT symbol FROM active_orders WHERE symbol = ?", (sym,))
                if not cursor.fetchone():
                    cursor.execute("""
                    INSERT INTO active_orders (symbol, state, updated_at)
                    VALUES (?, 0, ?)
                    """, (sym, now_str))
            conn.commit()

    # -------------------------------------------------------------
    # GESTIÓN DE SUBCARTERAS Y DCA
    # -------------------------------------------------------------
    def get_subwallet(self, symbol: str) -> Optional[Dict[str, Any]]:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM subwallets WHERE symbol = ?", (symbol,))
            row = cursor.fetchone()
            return dict(row) if row else None

    def get_all_subwallets(self) -> Dict[str, Dict[str, Any]]:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM subwallets")
            return {row["symbol"]: dict(row) for row in cursor.fetchall()}

    def update_subwallet_capital(self, symbol: str, new_capital: float, new_hwm: float):
        now_str = datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
            UPDATE subwallets
            SET capital = ?, hwm = ?, updated_at = ?
            WHERE symbol = ?
            """, (new_capital, new_hwm, now_str, symbol))
            conn.commit()

    def process_weekly_dca(self) -> bool:
        """Inyecta los aportes semanales si han transcurrido 7 días desde el último aporte."""
        all_wallets = self.get_all_subwallets()
        if not all_wallets:
            return False

        sample_wallet = next(iter(all_wallets.values()))
        tot_deposited = sum(w["cum_deposited"] for w in all_wallets.values())

        if tot_deposited >= MAX_DEPOSIT_TOTAL:
            return False

        last_dep = datetime.datetime.strptime(sample_wallet["last_deposit_time"], "%Y-%m-%d %H:%M:%S")
        now = datetime.datetime.utcnow()

        if (now - last_dep).total_seconds() >= 7 * 86400:
            n_symbols = len(all_wallets)
            sub_weekly = WEEKLY_DEPOSIT / n_symbols
            now_str = now.strftime("%Y-%m-%d %H:%M:%S")

            with self._get_connection() as conn:
                cursor = conn.cursor()
                for sym, w in all_wallets.items():
                    new_cap = w["capital"] + sub_weekly
                    new_hwm = w["hwm"] + sub_weekly
                    new_cum = w["cum_deposited"] + sub_weekly
                    cursor.execute("""
                    UPDATE subwallets
                    SET capital = ?, hwm = ?, cum_deposited = ?, last_deposit_time = ?, updated_at = ?
                    WHERE symbol = ?
                    """, (new_cap, new_hwm, new_cum, now_str, now_str, sym))
                conn.commit()
            logger.info(f"💵 Aporte Semanal DCA ejecutado: +${WEEKLY_DEPOSIT:.2f} USD distribuidos entre {n_symbols} carteras.")
            return True
        return False

    # -------------------------------------------------------------
    # GESTIÓN DE ÓRDENES Y POSICIONES ACTIVAS
    # -------------------------------------------------------------
    def get_order_state(self, symbol: str) -> Dict[str, Any]:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM active_orders WHERE symbol = ?", (symbol,))
            row = cursor.fetchone()
            if row:
                return dict(row)
            return {"symbol": symbol, "state": 0}

    def set_pending_order(self, symbol: str, side: str, trigger_time: str, limit_price: float, risk_usd: float, signal_ema: float, signal_close: float):
        now_str = datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
            UPDATE active_orders
            SET state = 1, side = ?, trigger_time = ?, limit_price = ?, risk_usd = ?,
                signal_ema = ?, signal_close = ?, entry_time = NULL, fill_price = NULL,
                tp_price = NULL, sl_price = NULL, updated_at = ?
            WHERE symbol = ?
            """, (side, trigger_time, limit_price, risk_usd, signal_ema, signal_close, now_str, symbol))
            conn.commit()

    def set_position_filled(self, symbol: str, entry_time: str, fill_price: float, tp_price: float, sl_price: float):
        now_str = datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
            UPDATE active_orders
            SET state = 2, entry_time = ?, fill_price = ?, tp_price = ?, sl_price = ?, updated_at = ?
            WHERE symbol = ?
            """, (entry_time, fill_price, tp_price, sl_price, now_str, symbol))
            conn.commit()

    def reset_order_state(self, symbol: str):
        now_str = datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
            UPDATE active_orders
            SET state = 0, side = NULL, trigger_time = NULL, entry_time = NULL,
                limit_price = NULL, tp_price = NULL, sl_price = NULL, risk_usd = NULL,
                signal_ema = NULL, signal_close = NULL, fill_price = NULL, updated_at = ?
            WHERE symbol = ?
            """, (now_str, symbol))
            conn.commit()

    # -------------------------------------------------------------
    # REGISTRO HISTÓRICO DE TRADES
    # -------------------------------------------------------------
    def record_completed_trade(self, trade_data: Dict[str, Any]):
        now_str = datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
            INSERT INTO trade_history (
                symbol, side, trigger_time, entry_time, exit_time, entry_price, exit_price,
                tp_price, sl_price, exit_reason, raw_return_pct, net_return_pct, risk_usd,
                dollar_pnl, win, capital_before, capital_after, wallet_hwm, cum_deposited_usd, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                trade_data["symbol"], trade_data["side"], trade_data["trigger_time"],
                trade_data["entry_time"], trade_data["exit_time"], trade_data["entry_price"],
                trade_data["exit_price"], trade_data["tp_price"], trade_data["sl_price"],
                trade_data["exit_reason"], trade_data["raw_return_pct"], trade_data["net_return_pct"],
                trade_data["risk_usd"], trade_data["dollar_pnl"], 1 if trade_data["win"] else 0,
                trade_data["capital_before"], trade_data["capital_after"], trade_data["wallet_hwm"],
                trade_data["cum_deposited_usd"], now_str
            ))
            conn.commit()

    # -------------------------------------------------------------
    # COLA OFFLINE DE TELEGRAM (Resiliencia ante caídas de internet)
    # -------------------------------------------------------------
    def enqueue_telegram_message(self, message: str):
        now_str = datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
            INSERT INTO telegram_outbox (message, status, created_at)
            VALUES (?, 'PENDING', ?)
            """, (message, now_str))
            conn.commit()

    def get_pending_telegram_messages(self, limit: int = 10) -> List[Dict[str, Any]]:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
            SELECT * FROM telegram_outbox
            WHERE status = 'PENDING'
            ORDER BY id ASC
            LIMIT ?
            """, (limit,))
            return [dict(r) for r in cursor.fetchall()]

    def mark_telegram_message_sent(self, msg_id: int):
        now_str = datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
            UPDATE telegram_outbox
            SET status = 'SENT', sent_at = ?
            WHERE id = ?
            """, (now_str, msg_id))
            conn.commit()

    def increment_telegram_retry(self, msg_id: int):
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
            UPDATE telegram_outbox
            SET retry_count = retry_count + 1
            WHERE id = ?
            """, (msg_id,))
            conn.commit()

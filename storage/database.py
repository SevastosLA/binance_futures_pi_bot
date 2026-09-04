"""
Módulo de Persistencia en Base de Datos SQLite (Modo WAL).
Garantiza tolerancia a fallos, cortes de energía y evita bloqueos de base de datos.
Soporta el modelo de dimensionamiento asimétrico híbrido (La Campeona 2% + El Francotirador 1%).
"""

import sqlite3
import datetime
import logging
from contextlib import contextmanager
from typing import Dict, List, Optional, Any, Generator
from config import DB_PATH, SYMBOLS, INITIAL_CAPITAL, WEEKLY_DEPOSIT, MAX_DEPOSIT_TOTAL

logger = logging.getLogger("DatabaseManager")

class DatabaseManager:
    def __init__(self, db_path: str = DB_PATH):
        self.db_path = db_path
        self._init_db()

    @contextmanager
    def _get_connection(self) -> Generator[sqlite3.Connection, None, None]:
        """Crea una conexión con timeout largo y garantiza su cierre al finalizar."""
        conn = sqlite3.connect(self.db_path, timeout=60.0)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _init_db(self):
        """Inicializa la base de datos en modo WAL y crea/migra las tablas."""
        init_conn = sqlite3.connect(self.db_path, timeout=60.0)
        init_conn.execute("PRAGMA journal_mode=WAL;")
        init_conn.execute("PRAGMA synchronous=NORMAL;")
        init_conn.close()

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

            # 2. Tabla de Estado de Órdenes y Posiciones Activas (Soporte Híbrido)
            cursor.execute("""
            CREATE TABLE IF NOT EXISTS active_orders (
                symbol TEXT PRIMARY KEY,
                state INTEGER NOT NULL DEFAULT 0,
                side TEXT,
                trigger_time TEXT,
                entry_time TEXT,
                limit_price REAL,
                tp_price REAL,
                sl_price REAL,
                risk_usd REAL,
                signal_ema REAL,
                signal_close REAL,
                fill_price REAL,
                franco_active INTEGER DEFAULT 1,
                candles_15m_elapsed INTEGER DEFAULT 0,
                effective_risk_pct REAL DEFAULT 0.03,
                execution_type TEXT,
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
                execution_type TEXT,
                risk_pct REAL,
                created_at TEXT NOT NULL
            );
            """)

            # 4. Cola de Mensajes Offline para Telegram
            cursor.execute("""
            CREATE TABLE IF NOT EXISTS telegram_outbox (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                message TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'PENDING',
                retry_count INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                sent_at TEXT
            );
            """)

            # Migraciones automáticas y seguras para bases de datos existentes
            columns_to_add_orders = [
                ("franco_active", "INTEGER DEFAULT 1"),
                ("candles_15m_elapsed", "INTEGER DEFAULT 0"),
                ("effective_risk_pct", "REAL DEFAULT 0.03"),
                ("execution_type", "TEXT")
            ]
            for col_name, col_def in columns_to_add_orders:
                try:
                    cursor.execute(f"ALTER TABLE active_orders ADD COLUMN {col_name} {col_def};")
                except sqlite3.OperationalError:
                    pass  # Ya existe

            columns_to_add_history = [
                ("execution_type", "TEXT"),
                ("risk_pct", "REAL")
            ]
            for col_name, col_def in columns_to_add_history:
                try:
                    cursor.execute(f"ALTER TABLE trade_history ADD COLUMN {col_name} {col_def};")
                except sqlite3.OperationalError:
                    pass

            # 5. Inicializar Subcarteras y Estados si no existen
            now_str = datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
            sub_init = INITIAL_CAPITAL / len(SYMBOLS)

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

    def process_weekly_dca(self) -> bool:
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
            logger.info(f"💵 Aporte Semanal DCA ejecutado: +${WEEKLY_DEPOSIT:.2f} USD.")
            return True
        return False

    def get_order_state(self, symbol: str) -> Dict[str, Any]:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM active_orders WHERE symbol = ?", (symbol,))
            row = cursor.fetchone()
            if row:
                return dict(row)
            return {"symbol": symbol, "state": 0}

    def set_pending_order(
        self, symbol: str, side: str, trigger_time: str, limit_price: float,
        risk_usd: float, signal_ema: float, signal_close: float,
        franco_active: int = 1, candles_15m_elapsed: int = 0,
        effective_risk_pct: float = 0.03
    ):
        """Registra una orden pendiente híbrida (3% inicial: Campeona 2% + Francotirador 1%)."""
        now_str = datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
            UPDATE active_orders
            SET state = 1, side = ?, trigger_time = ?, limit_price = ?, risk_usd = ?,
                signal_ema = ?, signal_close = ?, entry_time = NULL, fill_price = NULL,
                tp_price = NULL, sl_price = NULL, franco_active = ?, candles_15m_elapsed = ?,
                effective_risk_pct = ?, execution_type = NULL, updated_at = ?
            WHERE symbol = ?
            """, (side, trigger_time, limit_price, risk_usd, signal_ema, signal_close,
                  franco_active, candles_15m_elapsed, effective_risk_pct, now_str, symbol))

    def expire_franco_tranche(self, symbol: str, new_risk_usd: float):
        """Expira el tramo Francotirador tras 15 minutos sin fill, reduciendo el riesgo a 2.0% HWM."""
        now_str = datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
            UPDATE active_orders
            SET franco_active = 0, effective_risk_pct = 0.02, risk_usd = ?, updated_at = ?
            WHERE symbol = ? AND state = 1
            """, (new_risk_usd, now_str, symbol))

    def update_candles_elapsed(self, symbol: str, count: int):
        """Actualiza el contador de periodos/velas de 15m transcurridos."""
        now_str = datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
            UPDATE active_orders
            SET candles_15m_elapsed = ?, updated_at = ?
            WHERE symbol = ? AND state = 1
            """, (count, now_str, symbol))

    def set_position_filled(
        self, symbol: str, entry_time: str, fill_price: float,
        tp_price: float, sl_price: float,
        execution_type: str = "CAMPEONA_NORMAL_2PCT",
        effective_risk_pct: float = 0.02,
        risk_usd: Optional[float] = None
    ):
        """Registra la posición activa con su tipo de ejecución (Francotirador 3% vs Campeona 2%)."""
        now_str = datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
        with self._get_connection() as conn:
            cursor = conn.cursor()
            if risk_usd is not None:
                cursor.execute("""
                UPDATE active_orders
                SET state = 2, entry_time = ?, fill_price = ?, tp_price = ?, sl_price = ?,
                    execution_type = ?, effective_risk_pct = ?, risk_usd = ?, updated_at = ?
                WHERE symbol = ?
                """, (entry_time, fill_price, tp_price, sl_price, execution_type, effective_risk_pct, risk_usd, now_str, symbol))
            else:
                cursor.execute("""
                UPDATE active_orders
                SET state = 2, entry_time = ?, fill_price = ?, tp_price = ?, sl_price = ?,
                    execution_type = ?, effective_risk_pct = ?, updated_at = ?
                WHERE symbol = ?
                """, (entry_time, fill_price, tp_price, sl_price, execution_type, effective_risk_pct, now_str, symbol))

    def reset_order_state(self, symbol: str):
        """Restablece el estado de un símbolo a IDLE (0)."""
        now_str = datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
            UPDATE active_orders
            SET state = 0, side = NULL, trigger_time = NULL, entry_time = NULL,
                limit_price = NULL, tp_price = NULL, sl_price = NULL, risk_usd = NULL,
                signal_ema = NULL, signal_close = NULL, fill_price = NULL, franco_active = 1,
                candles_15m_elapsed = 0, effective_risk_pct = 0.03, execution_type = NULL, updated_at = ?
            WHERE symbol = ?
            """, (now_str, symbol))

    def record_completed_trade(self, trade_data: Dict[str, Any]):
        """Registra una operación completada en la tabla histórica con métricas y tipo de ejecución."""
        now_str = datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
        execution_type = trade_data.get("execution_type", "CAMPEONA_NORMAL_2PCT")
        risk_pct = trade_data.get("risk_pct", 0.02)
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
            INSERT INTO trade_history (
                symbol, side, trigger_time, entry_time, exit_time, entry_price, exit_price,
                tp_price, sl_price, exit_reason, raw_return_pct, net_return_pct, risk_usd,
                dollar_pnl, win, capital_before, capital_after, wallet_hwm, cum_deposited_usd,
                execution_type, risk_pct, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                trade_data["symbol"], trade_data["side"], trade_data["trigger_time"],
                trade_data["entry_time"], trade_data["exit_time"], trade_data["entry_price"],
                trade_data["exit_price"], trade_data["tp_price"], trade_data["sl_price"],
                trade_data["exit_reason"], trade_data["raw_return_pct"], trade_data["net_return_pct"],
                trade_data["risk_usd"], trade_data["dollar_pnl"], 1 if trade_data["win"] else 0,
                trade_data["capital_before"], trade_data["capital_after"], trade_data["wallet_hwm"],
                trade_data["cum_deposited_usd"], execution_type, risk_pct, now_str
            ))

    def enqueue_telegram_message(self, message: str):
        now_str = datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
            INSERT INTO telegram_outbox (message, status, created_at)
            VALUES (?, 'PENDING', ?)
            """, (message, now_str))

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

    def increment_telegram_retry(self, msg_id: int):
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
            UPDATE telegram_outbox
            SET retry_count = retry_count + 1
            WHERE id = ?
            """, (msg_id,))

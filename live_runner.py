"""
Ejecutable Principal del Bot Cuantitativo 24/7 para Raspberry Pi.
Estrategia HWM Multi-Cartera (E1-E5).
Orquesta la ingesta de datos, evaluación de señales en múltiples temporalidades (15m, 1h, 4h),
gestión intrabarra en tiempo real (polling), persistencia SQLite WAL y alertas de Telegram.
"""

import os
import sys
import time
import signal
import psutil
import logging
import datetime
from typing import Dict, Optional

from config import (
    SYMBOLS, TRADING_MODE, POLLING_INTERVAL_SECONDS,
    HEARTBEAT_INTERVAL_HOURS, LOGS_DIR
)
from storage.database import DatabaseManager
from feed.binance_feed import BinanceFuturesFeed
from notifier.telegram_bot import TelegramNotifier
from engine.strategy_engine import StrategyEngine

# Configuración de Logging con rotación diaria en archivo y salida por consola
log_filename = os.path.join(LOGS_DIR, f"bot_{datetime.datetime.utcnow().strftime('%Y%m%d')}.log")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] (%(name)s): %(message)s",
    handlers=[
        logging.FileHandler(log_filename, encoding="utf-8"),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger("LiveRunner")

class BotRunner:
    def __init__(self):
        self.running = True
        self.start_time = datetime.datetime.utcnow()
        self.last_heartbeat = self.start_time
        
        # Inicializar Componentes Modulares
        self.db = DatabaseManager()
        self.notifier = TelegramNotifier(self.db)
        self.feed = BinanceFuturesFeed()
        self.engine = StrategyEngine(self.db, self.notifier, feed=self.feed)
        
        # Estado en memoria para sincronización de velas de 15m
        self.last_processed_15m: Dict[str, Optional[datetime.datetime]] = {s: None for s in SYMBOLS}
        
        # Captura de señales del SO (SIGINT / SIGTERM) para apagado limpio
        signal.signal(signal.SIGINT, self._handle_shutdown)
        signal.signal(signal.SIGTERM, self._handle_shutdown)

    def _handle_shutdown(self, signum, frame):
        logger.info(f"Señal de terminación recibida ({signum}). Iniciando apagado seguro...")
        self.running = False

    def _get_system_telemetry(self) -> Dict[str, str]:
        """Obtiene métricas de hardware de la Raspberry Pi / Linux."""
        cpu_temp = "N/A"
        try:
            if os.path.exists("/sys/class/thermal/thermal_zone0/temp"):
                with open("/sys/class/thermal/thermal_zone0/temp", "r") as f:
                    cpu_temp = f"{float(f.read().strip()) / 1000.0:.1f}°C"
            elif hasattr(psutil, "sensors_temperatures"):
                temps = psutil.sensors_temperatures()
                if "coretemp" in temps and temps["coretemp"]:
                    cpu_temp = f"{temps['coretemp'][0].current:.1f}°C"
                elif "cpu_thermal" in temps and temps["cpu_thermal"]:
                    cpu_thermal = temps["cpu_thermal"]
                    if cpu_thermal:
                        cpu_temp = f"{cpu_thermal[0].current:.1f}°C"
        except Exception:
            pass

        mem = psutil.virtual_memory()
        ram_str = f"{mem.used / (1024**2):.0f}MB / {mem.total / (1024**2):.0f}MB ({mem.percent}%)"

        delta = datetime.datetime.utcnow() - self.start_time
        days = delta.days
        hours, rem = divmod(delta.seconds, 3600)
        minutes, seconds = divmod(rem, 60)
        uptime_str = f"{days}d {hours}h {minutes}m {seconds}s" if days > 0 else f"{hours}h {minutes}m {seconds}s"

        return {
            "cpu_temp": cpu_temp,
            "ram_usage": ram_str,
            "uptime": uptime_str
        }

    def startup(self):
        """Mensaje inicial y verificación de estado en el arranque."""
        logger.info("=" * 70)
        logger.info(" 🤖 INICIANDO ANTIGRAVITY BINANCE FUTURES BOT (MODO MULTI-CARTERA E1-E5)")
        logger.info(f" Modo: {TRADING_MODE} | Activos: {', '.join(SYMBOLS)}")
        logger.info(" Estrategia: 5 Sub-Bots (E1-E5) por Moneda Independientes")
        logger.info("=" * 70)

        startup_msg = (
            f"🚀 <b>BOT INICIADO / REANUDADO CON ÉXITO</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"🤖 <b>Modo:</b> {TRADING_MODE} (Binance Futures)\n"
            f"🪙 <b>Activos Vigilados:</b> <code>{', '.join(SYMBOLS)}</code>\n"
            f"🏹 <b>Estrategia:</b> Multi-Cartera Concurrente (E1-E5)\n"
            f"🛡️ <b>Gestión:</b> High-Water Mark Independiente por Moneda\n"
            f"⏱️ <b>Hora Arranque:</b> {self.start_time.strftime('%Y-%m-%d %H:%M:%S')} UTC"
        )
        self.notifier.send_message(startup_msg)
        self.notifier.start_command_listener()

    def run(self):
        self.startup()

        while self.running:
            try:
                now = datetime.datetime.utcnow()

                # 1. Drenar cola offline de Telegram
                self.notifier.process_outbox_queue()

                # 2. Verificar Aporte Semanal DCA
                if self.db.process_weekly_dca():
                    total_wallets = self.db.get_all_subwallets()
                    tot_cap = sum(w["capital"] for w in total_wallets.values())
                    tot_dep = sum(w["cum_deposited"] for w in total_wallets.values())
                    self.notifier.send_message(
                        f"💵 <b>APORTE SEMANAL DCA EJECUTADO</b>\n"
                        f"━━━━━━━━━━━━━━━━━━━━\n"
                        f"📈 <b>Capital Total Depositado:</b> ${tot_dep:,.2f} USD\n"
                        f"💼 <b>Nuevo Balance Total:</b> ${tot_cap:,.2f} USD\n"
                        f"👑 <i>High-Water Mark incrementado equitativamente.</i>"
                    )

                # 3. Sincronizar y Evaluar Velas (15m, 1h, 4h)
                for sym in SYMBOLS:
                    try:
                        df_15m = self.feed.fetch_klines(sym, interval="15m", limit=100)
                        if df_15m is not None and not df_15m.empty:
                            last_closed_time = df_15m.iloc[-2]["Open Time"]
                            if self.last_processed_15m.get(sym) != last_closed_time:
                                # Hay una nueva vela de 15m cerrada, bajamos el resto para evaluación
                                df_1h = self.feed.fetch_klines(sym, interval="1h", limit=200)
                                df_4h = self.feed.fetch_klines(sym, interval="4h", limit=200)
                                if df_1h is not None and df_4h is not None:
                                    self.engine.evaluate_klines(sym, df_15m, df_1h, df_4h)
                                    self.last_processed_15m[sym] = last_closed_time
                    except Exception as e:
                        logger.error(f"Error procesando klines para {sym}: {e}")

                # 4. Evaluar Ejecución Intrabarra / Precios en Tiempo Real
                try:
                    current_prices = self.feed.fetch_all_latest_prices(SYMBOLS)
                    for sym in SYMBOLS:
                        price = current_prices.get(sym)
                        if price:
                            self.engine.evaluate_realtime_tick(sym, current_price=price, current_time=now)
                except Exception as e:
                    logger.error(f"Error en evaluación intrabarra en tiempo real: {e}")

                # 5. Emisión de Heartbeat
                if (now - self.last_heartbeat).total_seconds() >= HEARTBEAT_INTERVAL_HOURS * 3600:
                    telemetry = self._get_system_telemetry()
                    wallets = self.db.get_all_subwallets()
                    tot_equity = sum(w["capital"] for w in wallets.values())
                    tot_dep = sum(w["cum_deposited"] for w in wallets.values())
                    
                    # Contar ordenes de las 5 estrategias
                    active_pos_cnt = 0
                    pending_cnt = 0
                    for sym in SYMBOLS:
                        for order in self.db.get_all_orders_for_symbol(sym):
                            if order["state"] == 2: active_pos_cnt += 1
                            elif order["state"] == 1: pending_cnt += 1

                    self.notifier.notify_heartbeat(
                        uptime_str=telemetry["uptime"],
                        cpu_temp=telemetry["cpu_temp"],
                        ram_usage=telemetry["ram_usage"],
                        total_equity=tot_equity,
                        cum_deposited=tot_dep,
                        active_pos_count=active_pos_cnt,
                        pending_orders_count=pending_cnt
                    )
                    self.last_heartbeat = now

            except Exception as e:
                logger.critical(f"Error inesperado en el loop principal: {e}", exc_info=True)
                self.notifier.notify_error(context="Loop Principal 24/7", error_msg=str(e))

            time.sleep(POLLING_INTERVAL_SECONDS)

        logger.info("Bot detenido de forma segura.")
        self.notifier.stop_command_listener()
        self.notifier.send_message(
            f"🛑 <b>BOT DETENIDO MANUALMENTE</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"⏱️ <b>Hora:</b> {datetime.datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')} UTC\n"
            f"💾 <i>El estado de todas las posiciones ha quedado asegurado en SQLite.</i>"
        )
        self.notifier.process_outbox_queue()

if __name__ == "__main__":
    runner = BotRunner()
    runner.run()

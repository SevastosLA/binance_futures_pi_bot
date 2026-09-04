"""
Módulo de Notificaciones de Telegram con Gráficos de Velas y Cola Offline.
Envía fotos con texto enriquecido al colocar orden límite, llenarse la posición,
tocar TP/SL, cancelar orden o expirar el tramo Francotirador.
Soporta el modelo cuantitativo híbrido (La Campeona 2% + El Francotirador 1%).
"""

import io
import time
import sqlite3
import logging
import requests
import datetime
import threading
import pandas as pd
from typing import Optional, Dict, Any, List

from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
from storage.database import DatabaseManager
from notifier.chart_generator import generate_trade_chart

logger = logging.getLogger("TelegramNotifier")

class TelegramNotifier:
    def __init__(self, db: DatabaseManager, bot_token: str = TELEGRAM_BOT_TOKEN, chat_id: str = TELEGRAM_CHAT_ID):
        self.db = db
        self.bot_token = bot_token
        self.chat_id = chat_id
        self.base_msg_url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage" if self.bot_token else None
        self.base_photo_url = f"https://api.telegram.org/bot{self.bot_token}/sendPhoto" if self.bot_token else None
        self.base_doc_url = f"https://api.telegram.org/bot{self.bot_token}/sendDocument" if self.bot_token else None
        self.base_updates_url = f"https://api.telegram.org/bot{self.bot_token}/getUpdates" if self.bot_token else None
        self.last_update_id = 0
        self._listener_running = False
        self._listener_thread: Optional[threading.Thread] = None

    def _send_raw_http(self, text: str) -> bool:
        """Envía un mensaje de texto a Telegram."""
        if not self.bot_token or not self.chat_id:
            logger.debug("Telegram credentials not configured. Message stored in log.")
            return False

        payload = {
            "chat_id": self.chat_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True
        }
        try:
            resp = requests.post(self.base_msg_url, json=payload, timeout=10.0)
            return resp.status_code == 200
        except Exception as e:
            logger.warning(f"Error conectando a Telegram (Texto): {e}")
            return False

    def _send_photo_http(self, photo_buf: io.BytesIO, caption: str) -> bool:
        """Envía una imagen (gráfico) con caption enriquecido a Telegram."""
        if not self.bot_token or not self.chat_id:
            return False

        data = {
            "chat_id": self.chat_id,
            "caption": caption,
            "parse_mode": "HTML"
        }
        files = {
            "photo": ("chart.png", photo_buf.getvalue(), "image/png")
        }
        try:
            resp = requests.post(self.base_photo_url, data=data, files=files, timeout=15.0)
            if resp.status_code == 200:
                return True
            else:
                logger.warning(f"Telegram API sendPhoto falló (Status {resp.status_code}): {resp.text}")
                return self._send_raw_http(caption)
        except Exception as e:
            logger.warning(f"Error enviando foto a Telegram: {e}")
            return self._send_raw_http(caption)

    def send_message(self, text: str):
        """Envía mensaje de texto; encola en SQLite si la red está caída."""
        success = self._send_raw_http(text)
        if not success:
            logger.info("Encolando mensaje en SQLite outbox para reintento posterior.")
            self.db.enqueue_telegram_message(text)

    def send_photo_or_text(self, photo_buf: Optional[io.BytesIO], text: str):
        """Envía imagen con texto o hace fallback a texto encolado."""
        if photo_buf is not None:
            success = self._send_photo_http(photo_buf, text)
            if not success:
                self.db.enqueue_telegram_message(text)
        else:
            self.send_message(text)

    def process_outbox_queue(self):
        """Drena la cola de mensajes pendientes tras recuperar la conexión."""
        pending_messages = self.db.get_pending_telegram_messages(limit=5)
        for msg in pending_messages:
            success = self._send_raw_http(msg["message"])
            if success:
                self.db.mark_telegram_message_sent(msg["id"])
                logger.info(f"Mensaje encolado #{msg['id']} entregado exitosamente a Telegram.")
            else:
                self.db.increment_telegram_retry(msg["id"])
                break

    def send_document(self, doc_buf: io.BytesIO, filename: str, caption: str = "") -> bool:
        """Envía un archivo/documento a Telegram."""
        if not self.bot_token or not self.chat_id or not self.base_doc_url:
            return False

        data = {
            "chat_id": self.chat_id,
            "caption": caption,
            "parse_mode": "HTML"
        }
        files = {
            "document": (filename, doc_buf.getvalue(), "text/csv")
        }
        try:
            resp = requests.post(self.base_doc_url, data=data, files=files, timeout=20.0)
            return resp.status_code == 200
        except Exception as e:
            logger.warning(f"Error enviando documento {filename} a Telegram: {e}")
            return False

    def export_table_to_csv(self, table_name: str) -> Optional[io.BytesIO]:
        """
        Exporta una tabla de SQLite a un buffer CSV en memoria con codificación UTF-8
        y timestamps en formato estándar ISO limpio (YYYY-MM-DD HH:MM:SS).
        """
        allowed_tables = ["trade_history", "subwallets", "active_orders"]
        if table_name not in allowed_tables:
            logger.error(f"Tabla no permitida para exportar: {table_name}")
            return None

        try:
            with sqlite3.connect(self.db.db_path, timeout=60.0) as conn:
                df = pd.read_sql_query(f"SELECT * FROM {table_name}", conn)

            csv_buf = io.BytesIO()
            # utf-8-sig (con BOM) para compatibilidad nativa con Excel / Numbers
            df.to_csv(csv_buf, index=False, encoding="utf-8-sig")
            csv_buf.seek(0)
            return csv_buf
        except Exception as e:
            logger.error(f"Error exportando tabla {table_name} a CSV: {e}")
            return None

    def handle_command(self, text: str, from_chat_id: str):
        """Procesa comandos interactivos enviados por Telegram."""
        if str(from_chat_id) != str(self.chat_id):
            logger.warning(f"Comando ignorado de chat_id no autorizado: {from_chat_id}")
            return

        cmd = text.strip().split()[0].lower() if text else ""
        if "@" in cmd:
            cmd = cmd.split("@")[0]

        logger.info(f"Comando de Telegram recibido: {cmd}")

        if cmd in ["/start", "/help", "/ayuda"]:
            help_msg = (
                "🤖 <b>PANEL DE CONTROL — BOT HÍBRIDO CRIPTO</b>\n"
                "<i>(La Campeona 2% + El Francotirador 1%)</i>\n"
                "━━━━━━━━━━━━━━━━━━━━\n"
                "📥 <b>Descarga de Bases de Datos en CSV:</b>\n"
                "• /trades o /csv_trades — Historial de operaciones cerradas\n"
                "• /wallets o /csv_wallets — Saldos y HWM por subcartera\n"
                "• /orders o /csv_orders — Estado de órdenes vivas\n"
                "• /csv_all — Descargar todas las tablas en CSV\n\n"
                "📊 <b>Consultas de Estado:</b>\n"
                "• /status — Resumen ejecutivo de fondos y salud\n"
                "• /help — Muestra este menú de comandos"
            )
            self.send_message(help_msg)

        elif cmd in ["/trades", "/csv_trades", "/historial"]:
            buf = self.export_table_to_csv("trade_history")
            if buf:
                self.send_document(
                    buf, filename="trade_history.csv",
                    caption="📊 <b>Historial de Operaciones Híbridas</b> (CSV)"
                )
            else:
                self.send_message("❌ Error generando CSV de historial de trades.")

        elif cmd in ["/wallets", "/csv_wallets", "/carteras"]:
            buf = self.export_table_to_csv("subwallets")
            if buf:
                self.send_document(
                    buf, filename="subwallets.csv",
                    caption="💼 <b>Saldos y Subcarteras</b> (CSV)"
                )
            else:
                self.send_message("❌ Error generando CSV de subcarteras.")

        elif cmd in ["/orders", "/csv_orders", "/ordenes"]:
            buf = self.export_table_to_csv("active_orders")
            if buf:
                self.send_document(
                    buf, filename="active_orders.csv",
                    caption="📈 <b>Órdenes y Posiciones Activas</b> (CSV)"
                )
            else:
                self.send_message("❌ Error generando CSV de órdenes activas.")

        elif cmd in ["/csv_all", "/descargar_todo"]:
            self.send_message("⏳ Generando y enviando bases de datos en CSV...")
            tables = [
                ("subwallets", "subwallets.csv", "💼 Subcarteras y Saldos"),
                ("active_orders", "active_orders.csv", "📈 Órdenes Vivas"),
                ("trade_history", "trade_history.csv", "📊 Historial de Trades")
            ]
            for tbl, fname, title in tables:
                b = self.export_table_to_csv(tbl)
                if b:
                    self.send_document(b, filename=fname, caption=f"{title} (CSV)")
                    time.sleep(0.5)

        elif cmd in ["/status", "/estado"]:
            wallets = self.db.get_all_subwallets()
            tot_cap = sum(w["capital"] for w in wallets.values())
            tot_dep = sum(w["cum_deposited"] for w in wallets.values())
            roi_pct = ((tot_cap - tot_dep) / tot_dep) * 100 if tot_dep > 0 else 0.0

            active_cnt = sum(1 for sym in wallets if self.db.get_order_state(sym).get("state") == 2)
            pending_cnt = sum(1 for sym in wallets if self.db.get_order_state(sym).get("state") == 1)

            now_utc = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC+0")
            msg = (
                "📊 <b>ESTADO DE LA CARTERA Y BOT HÍBRIDO</b>\n"
                "━━━━━━━━━━━━━━━━━━━━\n"
                f"💼 <b>Capital Total:</b> ${tot_cap:,.2f} USD\n"
                f"💵 <b>Aportes Acumulados:</b> ${tot_dep:,.2f} USD ({roi_pct:+.2f}% ROI)\n"
                f"🎯 <b>Posiciones Activas:</b> {active_cnt}\n"
                f"⏳ <b>Órdenes Pendientes:</b> {pending_cnt}\n"
                f"⏱️ <b>Hora Actual:</b> {now_utc}\n"
                "━━━━━━━━━━━━━━━━━━━━\n"
                "💡 <i>Usa /csv_all para descargar todos los datos en CSV.</i>"
            )
            self.send_message(msg)

        else:
            self.send_message(
                f"❓ Comando no reconocido: <code>{cmd}</code>\n"
                "Escribe /help para ver la lista de comandos disponibles."
            )

    def start_command_listener(self):
        """Inicia el listener de comandos en segundo plano si hay credenciales."""
        if not self.bot_token or not self.chat_id or not self.base_updates_url:
            logger.info("Telegram no configurado. Listener de comandos omitido.")
            return

        if self._listener_thread and self._listener_thread.is_alive():
            return

        self._listener_running = True
        self._listener_thread = threading.Thread(target=self._command_poll_loop, daemon=True, name="TelegramCmdListener")
        self._listener_thread.start()
        logger.info("📡 Listener interactivo de comandos de Telegram iniciado en segundo plano.")

    def stop_command_listener(self):
        """Detiene el listener de comandos."""
        self._listener_running = False

    def _command_poll_loop(self):
        """Bucle en segundo plano para escuchar comandos entrantes vía Long Polling."""
        try:
            init_resp = requests.get(self.base_updates_url, params={"offset": -1}, timeout=5.0)
            if init_resp.status_code == 200:
                init_data = init_resp.json()
                if init_data.get("ok") and init_data.get("result"):
                    self.last_update_id = init_data["result"][-1]["update_id"]
        except Exception:
            pass

        while self._listener_running:
            try:
                params = {
                    "offset": self.last_update_id + 1,
                    "timeout": 10,
                    "allowed_updates": ["message"]
                }
                resp = requests.get(self.base_updates_url, params=params, timeout=15.0)
                if resp.status_code == 200:
                    data = resp.json()
                    if data.get("ok"):
                        for update in data.get("result", []):
                            self.last_update_id = update["update_id"]
                            msg = update.get("message")
                            if msg and "text" in msg:
                                chat_id = str(msg["chat"]["id"])
                                text = msg["text"]
                                self.handle_command(text, chat_id)
                elif resp.status_code == 409:
                    time.sleep(5)
            except Exception as e:
                logger.debug(f"Aviso en loop de comandos Telegram: {e}")
                time.sleep(3)

    # -------------------------------------------------------------
    # EVENTOS DE TRADING HÍBRIDO
    # -------------------------------------------------------------
    def notify_limit_placed(
        self, symbol: str, side: str, trigger_time: str, signal_close: float,
        signal_ema: float, limit_price: float, risk_usd: float, capital: float,
        hwm: float, df_1h: Optional[pd.DataFrame] = None,
        risk_pct: float = 0.03, risk_campeona_usd: Optional[float] = None,
        risk_franco_usd: Optional[float] = None
    ):
        """Notificación de orden límite híbrida colocada (La Campeona 2% + El Francotirador 1%)."""
        side_icon = "🟢 LONG" if side == "LONG" else "🔴 SHORT"
        c_usd = risk_campeona_usd if risk_campeona_usd is not None else (hwm * 0.02)
        f_usd = risk_franco_usd if risk_franco_usd is not None else (hwm * 0.01)

        text = (
            f"📝 <b>ORDEN LÍMITE HÍBRIDA COLOCADA</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"🪙 <b>Activo:</b> <code>{symbol}</code> ({side_icon})\n"
            f"🎯 <b>Precio Límite (-1.0%):</b> ${limit_price:,.2f}\n"
            f"💰 <b>Riesgo Inicial Combinado:</b> ${risk_usd:,.2f} USD ({risk_pct*100:.1f}% HWM)\n"
            f"   • 🛡️ <b>La Campeona (2%):</b> ${c_usd:,.2f} USD (hasta 60 min)\n"
            f"   • 🎯 <b>El Francotirador (1%):</b> ${f_usd:,.2f} USD (expira en 15 min)\n"
            f"💼 <b>Capital Subcartera:</b> ${capital:,.2f} USD (HWM: ${hwm:,.2f})\n"
            f"⏱️ <b>Hora Señal:</b> {trigger_time} UTC\n"
            f"⏳ <i>Esperando retroceso en sub-velas de 15m...</i>"
        )
        self.send_message(text)

    def notify_franco_expired(
        self, symbol: str, side: str, limit_price: float,
        new_risk_usd: float, expire_time: str
    ):
        """Notificación de expiración del tramo Francotirador (15 min sin llenado)."""
        side_icon = "🟢 LONG" if side == "LONG" else "🔴 SHORT"
        text = (
            f"🎯 <b>FRANCOTIRADOR EXPIRADO (15 min)</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"🪙 <b>Activo:</b> <code>{symbol}</code> ({side_icon})\n"
            f"⏳ <i>Transcurrieron 15 minutos sin llenado en primera vela.</i>\n"
            f"📉 <b>Riesgo Reducido:</b> de 3.0% a <b>2.0% HWM</b> (${new_risk_usd:,.2f} USD)\n"
            f"🎯 <b>Precio Límite Esperado:</b> ${limit_price:,.2f}\n"
            f"🛡️ <i>Tramo Campeona (2%) permanece activo hasta los 60 min.</i>\n"
            f"⏱️ <b>Hora:</b> {expire_time} UTC"
        )
        self.send_message(text)

    def notify_position_filled(
        self, symbol: str, side: str, entry_time: str, fill_price: float,
        tp_price: float, sl_price: float, risk_usd: float,
        df_1h: Optional[pd.DataFrame] = None,
        execution_type: str = "CAMPEONA_NORMAL_2PCT",
        risk_pct: float = 0.02
    ):
        """Notificación estructurada de orden ejecutada con insignia visual de ejecución."""
        side_icon = "🟢 LONG" if side == "LONG" else "🔴 SHORT"
        if execution_type == "FRANCOTIRADOR_BOOST_3PCT":
            badge = "⚡ <b>EJECUCIÓN: FRANCOTIRADOR BOOST (3.0% Riesgo)</b>"
            subtitle = "🎯 <i>Llenado en ≤15m (Absorción veloz, prob. histórica ~77%)</i>"
        else:
            badge = "⚖️ <b>EJECUCIÓN: CAMPEONA NORMAL (2.0% Riesgo)</b>"
            subtitle = "🛡️ <i>Llenado ordinario entre minutos 15 y 60</i>"

        text = (
            f"🎯 <b>ORDEN LLENADA — POSICIÓN ACTIVA</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"{badge}\n"
            f"{subtitle}\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"🪙 <b>Activo:</b> <code>{symbol}</code> ({side_icon})\n"
            f"💵 <b>Precio Entrada:</b> ${fill_price:,.2f}\n"
            f"🟢 <b>Take Profit (+1.0%):</b> ${tp_price:,.2f}\n"
            f"🔴 <b>Stop Loss (-1.0%):</b> ${sl_price:,.2f}\n"
            f"💰 <b>Riesgo en Juego:</b> ${risk_usd:,.2f} USD ({risk_pct*100:.1f}% HWM)\n"
            f"⏱️ <b>Hora Entrada:</b> {entry_time} UTC\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"🛡️ <i>Monitoreando TP (+1%) / SL (-1%) o salida en 4ª vela</i>"
        )
        
        photo_buf = None
        if df_1h is not None and not df_1h.empty:
            try:
                photo_buf = generate_trade_chart(
                    symbol=symbol, df_1h=df_1h,
                    title=f"Posición Activa {side} — Entrada en ${fill_price:,.2f}",
                    limit_price=fill_price, tp_price=tp_price, sl_price=sl_price
                )
            except Exception as e:
                logger.error(f"Error generando gráfico de posición llenada: {e}")
                
        self.send_photo_or_text(photo_buf, text)

    def notify_position_closed(
        self, symbol: str, side: str, exit_time: str, exit_price: float,
        exit_reason: str, dollar_pnl: float, net_return_pct: float,
        win: bool, new_capital: float, new_hwm: float,
        df_1h: Optional[pd.DataFrame] = None, entry_price: Optional[float] = None,
        execution_type: Optional[str] = None
    ):
        """Notificación de cierre de posición con resultado y tipo de ejecución."""
        icon = "🏆 <b>TAKE PROFIT ALCANZADO (+1.0%)</b>" if win else "🛑 <b>POSICIÓN CERRADA</b>"
        pnl_sign = "+" if dollar_pnl >= 0 else ""
        pnl_color = "🟢" if win else "🔴"

        exec_badge = ""
        if execution_type == "FRANCOTIRADOR_BOOST_3PCT":
            exec_badge = "⚡ <b>Tipo Operación:</b> Francotirador Boost (3.0% Riesgo)\n"
        elif execution_type == "CAMPEONA_NORMAL_2PCT":
            exec_badge = "⚖️ <b>Tipo Operación:</b> Campeona Normal (2.0% Riesgo)\n"

        text = (
            f"{icon}\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"🪙 <b>Activo:</b> <code>{symbol}</code> ({side})\n"
            f"{exec_badge}"
            f"💵 <b>Precio Cierre:</b> ${exit_price:,.2f}\n"
            f"📋 <b>Motivo:</b> {exit_reason}\n"
            f"{pnl_color} <b>Resultado:</b> {pnl_sign}${dollar_pnl:,.4f} USD ({pnl_sign}{net_return_pct:.2f}%)\n"
            f"💼 <b>Nuevo Capital:</b> ${new_capital:,.2f} USD (HWM: ${new_hwm:,.2f})\n"
            f"⏱️ <b>Hora:</b> {exit_time} UTC\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"🔄 <i>Estado restablecido a búsqueda de oportunidades (IDLE)</i>"
        )
        self.send_message(text)

    def notify_order_cancelled(
        self, symbol: str, side: str, reason: str, limit_price: float,
        cancel_time: str, df_1h: Optional[pd.DataFrame] = None
    ):
        """Notificación breve y concisa de cancelación de orden límite (Solo Texto)."""
        side_icon = "🟢 LONG" if side == "LONG" else "🔴 SHORT"
        text = (
            f"❌ <b>ORDEN LÍMITE CANCELADA</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"🪙 <b>Activo:</b> <code>{symbol}</code> ({side_icon})\n"
            f"🎯 <b>Precio esperado:</b> ${limit_price:,.2f}\n"
            f"⚠️ <b>Motivo:</b> {reason}\n"
            f"⏱️ <b>Hora:</b> {cancel_time} UTC\n"
            f"🔄 <i>Buscando nueva oportunidad (IDLE)</i>"
        )
        self.send_message(text)

    def notify_heartbeat(self, uptime_str: str, cpu_temp: str, ram_usage: str, total_equity: float, cum_deposited: float, active_pos_count: int, pending_orders_count: int):
        roi_pct = ((total_equity - cum_deposited) / cum_deposited) * 100 if cum_deposited > 0 else 0.0
        text = (
            f"💚 <b>ESTADO DEL SISTEMA (HEARTBEAT)</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"🤖 <b>Bot Status:</b> Operativo 24/7 (Raspberry Pi — Modo Híbrido)\n"
            f"⏱️ <b>Uptime:</b> {uptime_str}\n"
            f"🌡️ <b>Temp CPU:</b> {cpu_temp} | <b>RAM:</b> {ram_usage}\n"
            f"💼 <b>Capital Total Fondo:</b> ${total_equity:,.2f} USD ({roi_pct:+.2f}% ROI)\n"
            f"💵 <b>Aportes Acumulados:</b> ${cum_deposited:,.2f} USD\n"
            f"📊 <b>Posiciones Activas:</b> {active_pos_count} | <b>Órdenes Pendientes:</b> {pending_orders_count}\n"
            f"📶 <b>Conectividad:</b> Sincronizado con Binance Futures API"
        )
        self.send_message(text)

    def notify_error(self, context: str, error_msg: str):
        text = (
            f"⚠️ <b>ALERTA DE ERROR EN EL BOT</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"📍 <b>Contexto:</b> {context}\n"
            f"❌ <b>Detalle:</b> <code>{error_msg}</code>\n"
            f"🔄 <i>El sistema intentará auto-recuperarse automáticamente.</i>"
        )
        self.send_message(text)

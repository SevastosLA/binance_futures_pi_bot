"""
Módulo de Notificaciones de Telegram con Cola de Mensajes Offline y Formato Enriquecido.
Permite operar de forma desatendida y recibir el ciclo de vida completo de cada operación.
"""

import logging
import requests
import datetime
from typing import Optional, Dict, Any
from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
from storage.database import DatabaseManager

logger = logging.getLogger("TelegramNotifier")

class TelegramNotifier:
    def __init__(self, db: DatabaseManager, bot_token: str = TELEGRAM_BOT_TOKEN, chat_id: str = TELEGRAM_CHAT_ID):
        self.db = db
        self.bot_token = bot_token
        self.chat_id = chat_id
        self.base_url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage" if self.bot_token else None

    def _send_raw_http(self, text: str) -> bool:
        """Envía directamente un mensaje a Telegram con timeout corto."""
        if not self.bot_token or not self.chat_id:
            logger.debug("Telegram credentials not configured. Message stored in log only.")
            return False

        payload = {
            "chat_id": self.chat_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True
        }
        try:
            resp = requests.post(self.base_url, json=payload, timeout=8.0)
            if resp.status_code == 200:
                return True
            else:
                logger.warning(f"Telegram API responded with status {resp.status_code}: {resp.text}")
                return False
        except Exception as e:
            logger.warning(f"Error connecting to Telegram API: {e}")
            return False

    def send_message(self, text: str):
        """Intenta enviar el mensaje inmediatamente; si falla la red, lo encola en SQLite."""
        success = self._send_raw_http(text)
        if not success:
            logger.info("Encolando mensaje en SQLite outbox para reintento posterior.")
            self.db.enqueue_telegram_message(text)

    def process_outbox_queue(self):
        """Procesa y vacía la cola de mensajes pendientes cuando se restablece la conexión."""
        pending_messages = self.db.get_pending_telegram_messages(limit=5)
        for msg in pending_messages:
            success = self._send_raw_http(msg["message"])
            if success:
                self.db.mark_telegram_message_sent(msg["id"])
                logger.info(f"Mensaje encolado #{msg['id']} entregado exitosamente a Telegram.")
            else:
                self.db.increment_telegram_retry(msg["id"])
                break  # Si aún no hay conexión, detener el drenaje para este ciclo

    # -------------------------------------------------------------
    # FORMATOS DE MENSAJES DE TRADING
    # -------------------------------------------------------------
    def notify_limit_placed(self, symbol: str, side: str, trigger_time: str, signal_close: float, signal_ema: float, limit_price: float, risk_usd: float, capital: float, hwm: float):
        side_icon = "🟢 LONG (Compra con Descuento)" if side == "LONG" else "🔴 SHORT (Venta con Descuento)"
        text = (
            f"🔔 <b>NUEVA ORDEN LÍMITE COLOCADA</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"🪙 <b>Activo:</b> <code>{symbol}</code> ({side_icon})\n"
            f"⏱️ <b>Hora Señal:</b> {trigger_time} UTC\n"
            f"📊 <b>Cierre 1h:</b> ${signal_close:,.2f} | <b>EMA 200:</b> ${signal_ema:,.2f}\n"
            f"🎯 <b>Precio Límite (-1.0%):</b> ${limit_price:,.2f}\n"
            f"💰 <b>Riesgo Asignado:</b> ${risk_usd:,.2f} USD (2% HWM)\n"
            f"💼 <b>Capital Subcartera:</b> ${capital:,.2f} USD (HWM: ${hwm:,.2f})\n"
            f"⏳ <i>Esperando retroceso para ejecución en 15m...</i>"
        )
        self.send_message(text)

    def notify_order_cancelled(self, symbol: str, side: str, reason: str, limit_price: float, cancel_time: str):
        text = (
            f"❌ <b>ORDEN LÍMITE CANCELADA</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"🪙 <b>Activo:</b> <code>{symbol}</code> ({side})\n"
            f"⏱️ <b>Hora Cancelación:</b> {cancel_time} UTC\n"
            f"🎯 <b>Precio que esperaba:</b> ${limit_price:,.2f}\n"
            f"⚠️ <b>Motivo:</b> {reason}\n"
            f"🔄 <i>Estado restablecido a búsqueda de oportunidades (IDLE).</i>"
        )
        self.send_message(text)

    def notify_position_filled(self, symbol: str, side: str, entry_time: str, fill_price: float, tp_price: float, sl_price: float, risk_usd: float):
        side_icon = "🟢 LONG" if side == "LONG" else "🔴 SHORT"
        text = (
            f"🎯 <b>ORDEN LLENADA — POSICIÓN ACTIVA</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"🪙 <b>Activo:</b> <code>{symbol}</code> ({side_icon})\n"
            f"⏱️ <b>Hora Entrada:</b> {entry_time} UTC\n"
            f"💵 <b>Precio de Entrada:</b> ${fill_price:,.2f}\n"
            f"🟢 <b>Take Profit (+1.0%):</b> ${tp_price:,.2f}\n"
            f"🔴 <b>Stop Loss (-1.0%):</b> ${sl_price:,.2f}\n"
            f"💰 <b>Riesgo en Juego:</b> ${risk_usd:,.2f} USD\n"
            f"🛡️ <i>Vigilando resolución TP / SL o salida en 4ª vela...</i>"
        )
        self.send_message(text)

    def notify_position_closed(self, symbol: str, side: str, exit_time: str, exit_price: float, exit_reason: str, dollar_pnl: float, net_return_pct: float, win: bool, new_capital: float, new_hwm: float):
        icon = "🏆 <b>TAKE PROFIT ALCANZADO</b>" if win else "🛑 <b>CIERRE DE POSICIÓN</b>"
        pnl_sign = "+" if dollar_pnl >= 0 else ""
        pnl_color = "🟢" if win else "🔴"

        text = (
            f"{icon}\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"🪙 <b>Activo:</b> <code>{symbol}</code> ({side})\n"
            f"⏱️ <b>Hora Cierre:</b> {exit_time} UTC\n"
            f"📋 <b>Motivo de Salida:</b> {exit_reason}\n"
            f"💵 <b>Precio de Cierre:</b> ${exit_price:,.2f}\n"
            f"{pnl_color} <b>Resultado Neto:</b> {pnl_sign}${dollar_pnl:,.4f} USD ({pnl_sign}{net_return_pct:.2f}%)\n"
            f"💼 <b>Nuevo Capital Subcartera:</b> ${new_capital:,.2f} USD\n"
            f"👑 <b>High-Water Mark (HWM):</b> ${new_hwm:,.2f} USD"
        )
        self.send_message(text)

    def notify_heartbeat(self, uptime_str: str, cpu_temp: str, ram_usage: str, total_equity: float, cum_deposited: float, active_pos_count: int, pending_orders_count: int):
        roi_pct = ((total_equity - cum_deposited) / cum_deposited) * 100 if cum_deposited > 0 else 0.0
        text = (
            f"💚 <b>ESTADO DEL SISTEMA (HEARTBEAT)</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"🤖 <b>Bot Status:</b> Operativo 24/7 (Raspberry Pi)\n"
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

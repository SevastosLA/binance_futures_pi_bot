"""
Módulo de Notificaciones de Telegram con Gráficos de Velas y Cola Offline.
Envía fotos con texto enriquecido al colocar orden límite, llenarse la posición,
tocar TP/SL o cancelar la orden.
"""

import io
import logging
import requests
import datetime
import pandas as pd
from typing import Optional, Dict, Any

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
                # Fallback a texto si la foto falla
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

    # -------------------------------------------------------------
    # EVENTOS CON GRÁFICO INCORPORADO
    # -------------------------------------------------------------
    def notify_limit_placed(
        self, symbol: str, side: str, trigger_time: str, signal_close: float,
        signal_ema: float, limit_price: float, risk_usd: float, capital: float,
        hwm: float, df_1h: Optional[pd.DataFrame] = None
    ):
        side_icon = "🟢 LONG (Compra Límite)" if side == "LONG" else "🔴 SHORT (Venta Límite)"
        text = (
            f"🔔 <b>NUEVA ORDEN LÍMITE COLOCADA</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"🪙 <b>Activo:</b> <code>{symbol}</code> ({side_icon})\n"
            f"⏱️ <b>Hora Señal:</b> {trigger_time} UTC\n"
            f"📊 <b>Cierre 1h:</b> ${signal_close:,.2f} | <b>EMA 200:</b> ${signal_ema:,.2f}\n"
            f"🎯 <b>Precio Límite (-1.0%):</b> ${limit_price:,.2f}\n"
            f"💰 <b>Riesgo Asignado:</b> ${risk_usd:,.2f} USD (2% HWM)\n"
            f"💼 <b>Capital Subcartera:</b> ${capital:,.2f} USD (HWM: ${hwm:,.2f})\n"
            f"⏳ <i>Esperando retroceso en 15m para ejecución...</i>"
        )
        
        photo_buf = None
        if df_1h is not None and not df_1h.empty:
            try:
                photo_buf = generate_trade_chart(
                    symbol=symbol, df_1h=df_1h,
                    title=f"Nueva Señal {side} — Orden Límite Colocada",
                    limit_price=limit_price
                )
            except Exception as e:
                logger.error(f"Error generando gráfico de orden límite: {e}")
                
        self.send_photo_or_text(photo_buf, text)

    def notify_position_filled(
        self, symbol: str, side: str, entry_time: str, fill_price: float,
        tp_price: float, sl_price: float, risk_usd: float,
        df_1h: Optional[pd.DataFrame] = None
    ):
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
        df_1h: Optional[pd.DataFrame] = None, entry_price: Optional[float] = None
    ):
        icon = "🏆 <b>TAKE PROFIT ALCANZADO (+1.0%)</b>" if win else "🛑 <b>CIERRE DE POSICIÓN</b>"
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
        
        photo_buf = None
        if df_1h is not None and not df_1h.empty:
            try:
                photo_buf = generate_trade_chart(
                    symbol=symbol, df_1h=df_1h,
                    title=f"Posición Cerrada ({exit_reason}) — PnL: {pnl_sign}${dollar_pnl:.2f}",
                    limit_price=entry_price, exit_price=exit_price
                )
            except Exception as e:
                logger.error(f"Error generando gráfico de posición cerrada: {e}")
                
        self.send_photo_or_text(photo_buf, text)

    def notify_order_cancelled(
        self, symbol: str, side: str, reason: str, limit_price: float,
        cancel_time: str, df_1h: Optional[pd.DataFrame] = None
    ):
        text = (
            f"❌ <b>ORDEN LÍMITE CANCELADA</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"🪙 <b>Activo:</b> <code>{symbol}</code> ({side})\n"
            f"⏱️ <b>Hora Cancelación:</b> {cancel_time} UTC\n"
            f"🎯 <b>Precio que esperaba:</b> ${limit_price:,.2f}\n"
            f"⚠️ <b>Motivo:</b> {reason}\n"
            f"🔄 <i>Estado restablecido a búsqueda de oportunidades (IDLE).</i>"
        )
        
        photo_buf = None
        if df_1h is not None and not df_1h.empty:
            try:
                photo_buf = generate_trade_chart(
                    symbol=symbol, df_1h=df_1h,
                    title=f"Orden Límite Cancelada — {symbol}",
                    limit_price=limit_price
                )
            except Exception as e:
                logger.error(f"Error generando gráfico de cancelación: {e}")
                
        self.send_photo_or_text(photo_buf, text)

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

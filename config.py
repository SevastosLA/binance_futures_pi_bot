"""
Módulo de Configuración Centralizada para el Bot en Raspberry Pi.
Carga variables de entorno desde .env y expone constantes tipadas.
"""

import os
from dotenv import load_dotenv

# Rutas de Archivos Locales
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# Cargar variables de entorno desde .env del proyecto
load_dotenv(os.path.join(BASE_DIR, ".env"))

# Credenciales de Telegram
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()

# Modo de Trading
TRADING_MODE = os.getenv("TRADING_MODE", "PAPER").upper()

# Lista de Tickers
symbols_raw = os.getenv("SYMBOLS", "BTCUSDT,ETHUSDT,SOLUSDT,BNBUSDT,XRPUSDT")
SYMBOLS = [s.strip().upper() for s in symbols_raw.split(",") if s.strip()]

# Parámetros del Modelo Cuantitativo HWM & DCA
INITIAL_CAPITAL = float(os.getenv("INITIAL_CAPITAL", "100.0"))
WEEKLY_DEPOSIT = float(os.getenv("WEEKLY_DEPOSIT", "20.0"))
MAX_DEPOSIT_TOTAL = float(os.getenv("MAX_DEPOSIT_TOTAL", "1000.0"))

# Gestión de Riesgo Asimétrico Híbrido (La Campeona 2% + El Francotirador 1%)
RISK_CAMPEONA = float(os.getenv("RISK_CAMPEONA", "0.02"))  # 2.0% HWM
RISK_FRANCO = float(os.getenv("RISK_FRANCO", "0.01"))      # 1.0% HWM
RISK_HYBRID_TOTAL = RISK_CAMPEONA + RISK_FRANCO             # 3.0% HWM inicial
RISK_PCT_HWM = RISK_HYBRID_TOTAL                            # Alias por compatibilidad

# Parámetros de la Estrategia
EMA_PERIOD = 200
LIMIT_DISCOUNT_PCT = 0.01   # 1.0% de descuento en el retroceso
TP_PCT = 0.01               # +1.0% Take Profit
SL_PCT = 0.01               # -1.0% Stop Loss
MAX_STREAK_EXIT = 4         # Cierre a mercado en la 4ª vela consecutiva

# Ciclo de Vida de Órdenes Híbridas (Minutos y Velas 15m)
MAX_FILL_MINUTES_FRANCO = 15
MAX_FILL_MINUTES_CAMPEONA = 60
MAX_FILL_CANDLES_15M_FRANCO = 1
MAX_FILL_CANDLES_15M_CAMPEONA = 4

# Estructura de Comisiones Binance Futures
FEE_MAKER = 0.0002          # 0.02% para órdenes Limit (Entrada y Take Profit)
FEE_TAKER = 0.0004          # 0.04% para órdenes Taker/Market (Stop Loss y Salidas Forzosas)
FEE_RATE = FEE_TAKER        # Alias por compatibilidad

# Configuración del Intervalo y Polling
POLLING_INTERVAL_SECONDS = int(os.getenv("POLLING_INTERVAL_SECONDS", "15"))
HEARTBEAT_INTERVAL_HOURS = int(os.getenv("HEARTBEAT_INTERVAL_HOURS", "24"))
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()

# Rutas de Archivos Locales
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
LOGS_DIR = os.path.join(BASE_DIR, "logs")
DB_PATH = os.path.join(DATA_DIR, "bot_state.db")

# Crear directorios necesarios
os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(LOGS_DIR, exist_ok=True)

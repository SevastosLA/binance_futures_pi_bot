# 🤖 Binance Futures Pi Bot — Algorithmic Pullback Strategy with HWM

Sistema de trading algorítmico y monitoreo cuantitativo 24/7 para **Binance Futures**, diseñado específicamente para operar de forma eficiente y ultra-resiliente en una **Raspberry Pi** (o cualquier servidor Linux/Debian).

Operando actualmente en **Modo Paper-Trading en Vivo**: consume datos de mercado en tiempo real, evalúa el ciclo de vida completo de cada operación, persiste el estado ante cortes de energía y notifica cada evento en tiempo real a través de **Telegram**.

---

## 🌟 Características Principales

* 📊 **Estrategia Cuantitativa Validada**:
  * Filtro de tendencia macro con **$EMA_{200}$ en temporalidad de 1 Hora ($1\text{h}$)**.
  * Entrada mediante orden límite con **$1.0\%$ de descuento en pullback**.
  * Take Profit ($+1.0\%$), Stop Loss ($-1.0\%$) y salida temporal en la 4ª vela consecutiva evaluados intrabarra ($15\text{m}$ / ticks de 15s).
* 🛡️ **Gestión Monetaria High-Water Mark (HWM) + DCA**:
  * Riesgo del **$2.0\%$ anclado al pico histórico de capital** por sub-cartera (sin martingala).
  * Soporte para inyecciones graduales semanales de capital (DCA).
* 🔋 **Persistencia Total contra Apagones (SQLite WAL Mode)**:
  * Todas las órdenes, posiciones y balances se escriben atómicamente en disco.
  * Si la Raspberry Pi se desconecta de la corriente o se reinicia, reanuda exactamente donde se quedó sin duplicar operaciones.
* 📱 **Notificaciones Enriquecidas en Telegram**:
  * 🔔 Alertas de nuevas órdenes límite colocadas con niveles de precio y riesgo.
  * ❌ Alertas de órdenes canceladas (si la vela opuesta invalida el retroceso).
  * 🎯 Alertas de ejecución (*fills*) y fijación de TP/SL.
  * 🏁 Alertas de cierre con cálculo exacto de PnL en USD y nuevo récord HWM.
  * 💚 *Heartbeat* diario con telemetría de hardware (temperatura CPU, uso de RAM, uptime).
* 📶 **Cola de Mensajes Offline**:
  * Si se interrumpe la conexión Wi-Fi/Ethernet, las alertas se encolan localmente y se despachan en cuanto vuelve internet.
* 🪶 **Consumo Ultraligero**:
  * Consume menos de **$60\text{ MB}$ de memoria RAM** y prácticamente 0% de uso continuo de CPU.

---

## 📁 Estructura del Proyecto

```
binance_futures_pi_bot/
├── README.md                          # Este archivo
├── requirements.txt                   # Dependencias de Python
├── .env.example                       # Plantilla de variables de entorno
├── .gitignore                         # Archivos ignorados por Git
├── config.py                          # Configuración centralizada
├── live_runner.py                     # Demonio principal 24/7
├── test_bot.py                        # Script de auto-diagnóstico
├── systemd/
│   └── antigravity-futures.service    # Servicio para auto-arranque en Linux
├── feed/
│   └── binance_feed.py                # Cliente de datos en vivo de Binance Futures
├── engine/
│   └── strategy_engine.py             # Motor de ejecución cuantitativa
├── storage/
│   └── database.py                    # Gestor de base de datos SQLite (WAL)
└── notifier/
    └── telegram_bot.py                # Notificador con cola outbox para Telegram
```

---

## 🚀 Guía de Instalación y Despliegue en Raspberry Pi

### 1. Preparar la Raspberry Pi
Conéctate por SSH a tu Raspberry Pi y actualiza los paquetes del sistema:
```bash
sudo apt update && sudo apt upgrade -y
sudo apt install -y python3-pip python3-venv git sqlite3
```

### 2. Clonar el Repositorio
```bash
git clone https://github.com/TU_USUARIO/binance_futures_pi_bot.git ~/binance_futures_pi_bot
cd ~/binance_futures_pi_bot
```

### 3. Crear el Entorno Virtual de Python
```bash
python3 -m venv .env
source .env/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
```

### 4. Configurar Variables de Entorno (`.env`)
Copia la plantilla y edita tus credenciales:
```bash
cp .env.example .env
nano .env
```
Rellena las variables:
```ini
TELEGRAM_BOT_TOKEN=tu_token_aqui
TELEGRAM_CHAT_ID=tu_chat_id_aqui
TRADING_MODE=PAPER
SYMBOLS=BTCUSDT,ETHUSDT,SOLUSDT,BNBUSDT,XRPUSDT
INITIAL_CAPITAL=100.0
WEEKLY_DEPOSIT=20.0
MAX_DEPOSIT_TOTAL=1000.0
RISK_PCT_HWM=0.02
```

> **¿Cómo obtener las credenciales de Telegram?**
> 1. Crea un bot hablando con [@BotFather](https://t.me/BotFather) en Telegram y copia el `API TOKEN`.
> 2. Inicia conversación con tu bot y luego consulta tu Chat ID con [@userinfobot](https://t.me/userinfobot).

---

## 🧪 Verificación y Diagnóstico

Antes de activar el servicio en segundo plano, ejecuta el auto-test de diagnóstico:
```bash
python3 test_bot.py
```
Este comando validará:
1. Conexión y creación de tablas en SQLite.
2. Envío de mensaje de prueba a Telegram.
3. Descarga de datos en tiempo real de Binance Futures API.
4. Cálculo de la $EMA_{200}$ en vivo.
5. Funcionamiento de la máquina de estados.

---

## ⚙️ Despliegue 24/7 con Systemd

Para que el bot funcione de forma desatendida y arranque automáticamente tras reinicios o cortes de luz:

```bash
# 1. Copiar el archivo de servicio a systemd
sudo cp systemd/antigravity-futures.service /etc/systemd/system/

# 2. Recargar el demonio de systemd
sudo systemctl daemon-reload

# 3. Habilitar el servicio para arranque automático al encender
sudo systemctl enable antigravity-futures.service

# 4. Iniciar el bot
sudo systemctl start antigravity-futures.service

# 5. Comprobar el estado en tiempo real
sudo systemctl status antigravity-futures.service
```

### Comandos Útiles de Mantenimiento:
* **Ver logs en vivo**: `journalctl -u antigravity-futures.service -f`
* **Reiniciar el bot**: `sudo systemctl restart antigravity-futures.service`
* **Detener el bot**: `sudo systemctl stop antigravity-futures.service`

---

## 🛡️ Licencia

Este proyecto está bajo la Licencia MIT.

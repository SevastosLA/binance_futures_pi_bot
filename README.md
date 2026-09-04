# 🤖 Binance Futures Pi Bot — Estrategia Híbrida Cuantitativa (La Campeona 2% + El Francotirador 1%)

Sistema de trading algorítmico y monitoreo cuantitativo 24/7 para **Binance Futures Perpetuals (USDT-M)**, diseñado específicamente para operar de forma perpetua, eficiente y ultra-resiliente en una **Raspberry Pi 4B** (o cualquier servidor Linux/Debian).

Operando actualmente en **Modo Paper-Trading en Vivo**: consume datos de mercado en tiempo real, evalúa el ciclo de vida completo de cada operación, persiste el estado ante cortes de energía y notifica cada evento en tiempo real con gráficos institucionales a través de **Telegram**.

---

## 🌟 Características Principales

* 📊 **Estrategia Híbrida Cuantitativa Ponderada por Probabilidad**:
  * **Filtro de Tendencia Macro**: $EMA_{200}$ sobre velas horarias cerradas ($1\text{h}$) con confirmación de retroceso en primera vela (`red_streak == 1` para LONG, `green_streak == 1` para SHORT).
  * **Entrada Límite Asimétrica**: Descuento del $-1.0\%$ respecto al cierre de 1h ($Close \times 0.99$ para Long, $Close \times 1.01$ para Short).
  * **Fase 1 (0 a 15 min / Vela 1 de 15m)**: Riesgo del **$3.0\%$ del High-Water Mark (HWM)** ($2.0\%$ Campeona + $1.0\%$ Francotirador). Si llena en $\le 15$m, se opera con 3% de riesgo aprovechando el **$76.94\%$ Win Rate** histórico verificado.
  * **Expiración de Francotirador (Minuto 15)**: Si no se llena tras 15m, el tramo del 1% se cancela y la orden pendiente se reduce automáticamente al **$2.0\%$ del HWM** (`CAMPEONA`).
  * **Fase 2 (15 a 60 min / Velas 2 a 4 de 15m)**: Riesgo del **$2.0\%$ del HWM**.
  * **Expiración Total a los 60 min**: Si no se llena tras 60m, la orden se cancela por completo y el bot regresa a `IDLE`.
* 🛡️ **Fricción Realista y Objetivos de Salida**:
  * **Take Profit (+1.0%)**: Salida límite Maker (comisión $0.04\%$ *roundtrip*, retorno neto $\mathbf{+0.96\%}$).
  * **Stop Loss (-1.0%)**: Salida stop-market Taker (comisión $0.06\%$ *roundtrip*, retorno neto $\mathbf{-1.06\%}$).
  * **Salida Preventiva en 4ª Vela Contraria (1h)**: Cierre a mercado ($0.06\%$ *roundtrip*) para cortar pérdidas si se acumulan 4 velas consecutivas en contra.
* 🔋 **Persistencia Total contra Cortes de Energía (SQLite WAL Mode)**:
  * Todas las órdenes, posiciones y balances se escriben atómicamente en disco bajo modo Write-Ahead Logging (WAL).
  * Si la Raspberry Pi se desconecta de la corriente o se reinicia, reanuda exactamente en el estado en que se quedó sin duplicar órdenes.
* 📱 **Notificaciones Enriquecidas y Gráficos en Telegram**:
  * 🔔 Alertas de órdenes colocadas con desglose del tramo Campeona (2%) y Francotirador (1%).
  * 🎯 Alertas de llenado con insignias visuales (`⚡ FRANCOTIRADOR BOOST` vs `⚖️ CAMPEONA NORMAL`) y gráfico institucional renderizado en memoria con Matplotlib Headless.
  * 🏆 Alertas de cierre con desglose de PnL neto en USD y actualización del nuevo récord HWM.
  * 💚 *Heartbeat* diario con telemetría de hardware de la Raspberry Pi (temperatura CPU, uso de RAM, uptime).
* 📥 **Comandos Interactivos y Descarga de CSVs**:
  * `/status`: Resumen ejecutivo de fondos, saldos y órdenes activas.
  * `/csv_all`: Descarga en un toque de todas las bases de datos en CSV (`subwallets.csv`, `active_orders.csv`, `trade_history.csv`).
  * `/wallets`, `/orders`, `/trades`: Descarga individual de cada tabla.
* 🪶 **Consumo Ultraligero**:
  * Diseñado específicamente para Raspberry Pi 4B: consume menos de **$60\text{ MB}$ de RAM** y prácticamente 0% de uso continuo de CPU.

---

## 📁 Estructura del Proyecto

```
binance_futures_pi_bot/
├── README.md                          # Documentación completa y guía de despliegue
├── requirements.txt                   # Dependencias de Python
├── .env.example                       # Plantilla de variables de entorno
├── .gitignore                         # Archivos ignorados por Git
├── config.py                          # Configuración centralizada de estrategia y riesgos
├── live_runner.py                     # Demonio principal 24/7 para producción perpetua
├── test_hybrid_strategy.py            # Suite de 9 pruebas cuantitativas de la estrategia híbrida
├── test_bot.py                        # Script de auto-diagnóstico de hardware, feed y gráficos
├── systemd/
│   └── antigravity-futures.service    # Archivo de servicio systemd para auto-arranque en Linux
├── feed/
│   └── binance_feed.py                # Cliente de datos en vivo de Binance Futures
├── engine/
│   └── strategy_engine.py             # Motor de estrategia cuantitativa híbrida
├── storage/
│   └── database.py                    # Gestor de base de datos SQLite en modo WAL
└── notifier/
    ├── telegram_bot.py                # Notificador y manejador interactivo de comandos
    ├── chart_generator.py             # Generador de gráficos oscuros de velas en memoria
    ├── example_notifications.py       # Demostración práctica de alertas de Telegram
    ├── example_telegram_commands.py   # Demostración de comandos y exportación CSV
    └── listen_telegram_commands.py    # Listener interactivo de comandos en tiempo real
```

---

## 🚀 Despliegue Perpetuo en Raspberry Pi 4B

### 1. Preparar la Raspberry Pi 4B
Conéctate por SSH a tu Raspberry Pi 4B:
```bash
sudo apt update && sudo apt upgrade -y
sudo apt install -y python3-pip python3-venv git sqlite3
```

### 2. Clonar el Repositorio
```bash
git clone https://github.com/SevastosLA/binance_futures_pi_bot.git ~/binance_futures_pi_bot
cd ~/binance_futures_pi_bot
```

### 3. Crear el Entorno Virtual de Python e Instalar Dependencias
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
```

### 4. Configurar Variables de Entorno (`.env`)
```bash
cp .env.example .env
nano .env
```
Asegura tus credenciales y configuración:
```ini
TELEGRAM_BOT_TOKEN=tu_bot_token_aqui
TELEGRAM_CHAT_ID=tu_chat_id_aqui
TRADING_MODE=PAPER
SYMBOLS=BTCUSDT,ETHUSDT,SOLUSDT,BNBUSDT,XRPUSDT
INITIAL_CAPITAL=100.0
WEEKLY_DEPOSIT=20.0
MAX_DEPOSIT_TOTAL=1000.0
RISK_CAMPEONA=0.02
RISK_FRANCO=0.01
RISK_HYBRID_TOTAL=0.03
```

---

## 🧪 Verificación Antes de Lanzar

Ejecuta las dos suites de pruebas para verificar que todo el entorno en la Raspberry Pi esté 100% operativo:

```bash
# 1. Validar la estrategia cuantitativa híbrida (9 pruebas matemáticas)
python3 test_hybrid_strategy.py

# 2. Validar diagnóstico integral (hardware, feed Binance, gráficos Matplotlib, SQLite WAL)
python3 test_bot.py
```

---

## ⚙️ Activación del Servicio Perpetuo (Systemd)

Para que el bot funcione de forma continua e indefinida, arrancando automáticamente al encender la Raspberry Pi y auto-recuperándose ante cualquier falla:

```bash
# 1. Ajustar el usuario si tu usuario en la Pi es diferente de 'pi' (opcional)
# nano systemd/antigravity-futures.service

# 2. Instalar el servicio en systemd
sudo cp systemd/antigravity-futures.service /etc/systemd/system/

# 3. Recargar systemd
sudo systemctl daemon-reload

# 4. Habilitar el servicio para arranque automático tras encendidos/reinicios
sudo systemctl enable antigravity-futures.service

# 5. Iniciar el bot inmediatamente
sudo systemctl start antigravity-futures.service

# 6. Comprobar el estado del bot
sudo systemctl status antigravity-futures.service
```

### Comandos Útiles de Monitoreo:
* **Ver logs en tiempo real**: `journalctl -u antigravity-futures.service -f`
* **Reiniciar el bot**: `sudo systemctl restart antigravity-futures.service`
* **Detener el bot**: `sudo systemctl stop antigravity-futures.service`

---

## 🛡️ Licencia

Distribuido bajo la Licencia MIT.

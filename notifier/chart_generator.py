"""
Generador de Gráficos de Velas Ultraligero para Raspberry Pi (Headless Matplotlib).
Dibuja gráficos de velas oscuros de alta estética con líneas de Orden Límite, TP, SL y EMA 200.
Genera imágenes en memoria (BytesIO) listas para enviarse por Telegram.
"""

import io
import matplotlib
matplotlib.use("Agg")  # Backend headless sin servidor gráfico X11
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import pandas as pd
import numpy as np
from typing import Optional

def generate_trade_chart(
    symbol: str,
    df_1h: pd.DataFrame,
    title: str,
    limit_price: Optional[float] = None,
    tp_price: Optional[float] = None,
    sl_price: Optional[float] = None,
    exit_price: Optional[float] = None,
    num_candles: int = 35
) -> io.BytesIO:
    """
    Genera un gráfico de velas profesional de las últimas `num_candles` barras de 1h
    junto con la curva de la EMA 200 y las líneas horizontales de niveles clave.
    Retorna un buffer en memoria io.BytesIO con formato PNG.
    """
    df_sub = df_1h.tail(num_candles).copy().reset_index(drop=True)
    
    # Configuración de estilo visual oscuro
    fig, ax = plt.subplots(figsize=(10, 5.5), dpi=120)
    fig.patch.set_facecolor("#070a12")
    ax.set_facecolor("#0d131f")
    
    # Eje X con índices enteros para evitar gaps de fin de semana
    indices = np.arange(len(df_sub))
    opens = df_sub["Open"].values
    highs = df_sub["High"].values
    lows = df_sub["Low"].values
    closes = df_sub["Close"].values
    
    width = 0.6
    width2 = 0.12
    
    # Dibujar velas
    for i in range(len(df_sub)):
        op = opens[i]; cl = closes[i]; hi = highs[i]; lo = lows[i]
        col = "#00ff88" if cl >= op else "#ff0055"
        
        # Mecha (High-Low)
        ax.plot([i, i], [lo, hi], color=col, linewidth=1.2, zorder=2)
        # Cuerpo (Open-Close)
        rect_bottom = min(op, cl)
        rect_height = max(abs(cl - op), (hi - lo) * 0.01) # Mínima altura visible para dojis
        rect = plt.Rectangle((i - width/2, rect_bottom), width, rect_height, facecolor=col, edgecolor=col, zorder=3)
        ax.add_patch(rect)
        
    # Dibujar EMA 200 si existe en el DataFrame
    if "EMA_200" in df_sub.columns:
        ax.plot(indices, df_sub["EMA_200"].values, color="#00f0ff", linewidth=1.8, label="EMA 200", zorder=4)
        
    # Dibujar Niveles Clave
    xmin, xmax = -0.5, len(df_sub) + 4.5
    
    if limit_price is not None:
        ax.axhline(limit_price, color="#00f0ff", linestyle="--", linewidth=1.5, label=f"Límite: ${limit_price:,.2f}", zorder=5)
        ax.text(xmax - 0.5, limit_price, f" Limite (${limit_price:,.2f})", color="#00f0ff", verticalalignment="center", fontsize=9, fontweight="bold")
        
    if tp_price is not None:
        ax.axhline(tp_price, color="#00ff88", linestyle="--", linewidth=1.5, label=f"TP (+1.0%): ${tp_price:,.2f}", zorder=5)
        ax.text(xmax - 0.5, tp_price, f" TP (${tp_price:,.2f})", color="#00ff88", verticalalignment="center", fontsize=9, fontweight="bold")
        
    if sl_price is not None:
        ax.axhline(sl_price, color="#ff0055", linestyle="--", linewidth=1.5, label=f"SL (-1.0%): ${sl_price:,.2f}", zorder=5)
        ax.text(xmax - 0.5, sl_price, f" SL (${sl_price:,.2f})", color="#ff0055", verticalalignment="center", fontsize=9, fontweight="bold")
        
    if exit_price is not None:
        ax.axhline(exit_price, color="#ffb703", linestyle=":", linewidth=1.5, label=f"Cierre: ${exit_price:,.2f}", zorder=5)
        ax.text(xmax - 0.5, exit_price, f" Salida (${exit_price:,.2f})", color="#ffb703", verticalalignment="center", fontsize=9, fontweight="bold")
        
    # Configurar Eje X con timestamps legibles cada 5 velas
    step = max(1, len(df_sub) // 6)
    ticks = indices[::step]
    labels = [pd.to_datetime(df_sub["Open Time"].iloc[idx]).strftime("%d-%b %H:%M") for idx in ticks]
    ax.set_xticks(ticks)
    ax.set_xticklabels(labels, color="#8a99ad", fontsize=8, rotation=15)
    
    # Formato general
    ax.set_xlim(xmin, xmax)
    ax.tick_params(colors="#8a99ad", labelsize=9)
    ax.grid(True, linestyle=":", alpha=0.2, color="#ffffff")
    
    # Título y Leyenda
    ax.set_title(f"{symbol} — {title}", color="#ffffff", fontsize=12, fontweight="bold", pad=12)
    legend = ax.legend(loc="upper left", facecolor="#070a12", edgecolor="#2a3b5c", fontsize=8)
    for text in legend.get_texts():
        text.set_color("#ffffff")
        
    # Ajustar bordes
    for spine in ax.spines.values():
        spine.set_color("#2a3b5c")
        
    plt.tight_layout()
    
    # Guardar en buffer de memoria
    buf = io.BytesIO()
    plt.savefig(buf, format="png", facecolor=fig.get_facecolor(), edgecolor="none", dpi=120)
    plt.close(fig)
    buf.seek(0)
    return buf

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
    junto con la curva de la EMA 200, zonas sombreadas y niveles clave (Entrada, TP, SL).
    Retorna un buffer en memoria io.BytesIO con formato PNG.
    """
    df_sub = df_1h.tail(num_candles).copy().reset_index(drop=True)
    
    # Configuración de estilo visual oscuro institucional
    fig, ax = plt.subplots(figsize=(10.5, 6), dpi=130)
    fig.patch.set_facecolor("#080c14")
    ax.set_facecolor("#0d131f")
    
    indices = np.arange(len(df_sub))
    opens = df_sub["Open"].values
    highs = df_sub["High"].values
    lows = df_sub["Low"].values
    closes = df_sub["Close"].values
    
    width = 0.6
    
    # Dibujar velas
    for i in range(len(df_sub)):
        op = opens[i]; cl = closes[i]; hi = highs[i]; lo = lows[i]
        col = "#00e676" if cl >= op else "#ff1744"
        
        # Mecha (High-Low)
        ax.plot([i, i], [lo, hi], color=col, linewidth=1.3, zorder=2)
        # Cuerpo (Open-Close)
        rect_bottom = min(op, cl)
        rect_height = max(abs(cl - op), (hi - lo) * 0.015)
        rect = plt.Rectangle((i - width/2, rect_bottom), width, rect_height, facecolor=col, edgecolor=col, zorder=3)
        ax.add_patch(rect)
        
    # Dibujar EMA 200 si existe en el DataFrame
    if "EMA_200" in df_sub.columns:
        ax.plot(indices, df_sub["EMA_200"].values, color="#ffab00", linewidth=1.8, label="EMA 200", zorder=4)
        
    # Rango eje X con espacio extra a la derecha para etiquetas legibles
    xmin, xmax = -0.8, len(df_sub) + 5.2
    
    # Zonas sombreadas de objetivos
    if limit_price is not None and tp_price is not None:
        y1, y2 = min(limit_price, tp_price), max(limit_price, tp_price)
        ax.axhspan(y1, y2, color="#00e676", alpha=0.07, zorder=1)
    if limit_price is not None and sl_price is not None:
        y1, y2 = min(limit_price, sl_price), max(limit_price, sl_price)
        ax.axhspan(y1, y2, color="#ff1744", alpha=0.07, zorder=1)

    # Dibujar Niveles Clave con Insignias (Badges)
    if limit_price is not None:
        lbl_text = f" Entrada (${limit_price:,.2f})"
        ax.axhline(limit_price, color="#00d4ff", linestyle="--", linewidth=1.6, label=f"Entrada: ${limit_price:,.2f}", zorder=5)
        ax.text(
            len(df_sub) + 0.2, limit_price, lbl_text,
            color="#00d4ff", verticalalignment="center", fontsize=8.5, fontweight="bold",
            bbox=dict(boxstyle="round,pad=0.25", fc="#08101e", ec="#00d4ff", lw=1.1, alpha=0.95),
            zorder=6
        )
        
    if tp_price is not None:
        lbl_text = f" TP (+1.0%): ${tp_price:,.2f}"
        ax.axhline(tp_price, color="#00e676", linestyle="--", linewidth=1.6, label=f"TP (+1.0%): ${tp_price:,.2f}", zorder=5)
        ax.text(
            len(df_sub) + 0.2, tp_price, lbl_text,
            color="#00e676", verticalalignment="center", fontsize=8.5, fontweight="bold",
            bbox=dict(boxstyle="round,pad=0.25", fc="#071b12", ec="#00e676", lw=1.1, alpha=0.95),
            zorder=6
        )
        
    if sl_price is not None:
        lbl_text = f" SL (-1.0%): ${sl_price:,.2f}"
        ax.axhline(sl_price, color="#ff1744", linestyle="--", linewidth=1.6, label=f"SL (-1.0%): ${sl_price:,.2f}", zorder=5)
        ax.text(
            len(df_sub) + 0.2, sl_price, lbl_text,
            color="#ff1744", verticalalignment="center", fontsize=8.5, fontweight="bold",
            bbox=dict(boxstyle="round,pad=0.25", fc="#20080e", ec="#ff1744", lw=1.1, alpha=0.95),
            zorder=6
        )
        
    if exit_price is not None:
        lbl_text = f" Salida: ${exit_price:,.2f}"
        ax.axhline(exit_price, color="#ffb703", linestyle=":", linewidth=1.6, label=f"Cierre: ${exit_price:,.2f}", zorder=5)
        ax.text(
            len(df_sub) + 0.2, exit_price, lbl_text,
            color="#ffb703", verticalalignment="center", fontsize=8.5, fontweight="bold",
            bbox=dict(boxstyle="round,pad=0.25", fc="#1f1807", ec="#ffb703", lw=1.1, alpha=0.95),
            zorder=6
        )
        
    # Auto-Ajuste Inteligente de Escala Y (Garantiza que todos los objetivos sean visibles)
    all_prices = list(lows) + list(highs)
    for p in [limit_price, tp_price, sl_price, exit_price]:
        if p is not None:
            all_prices.append(p)
    if "EMA_200" in df_sub.columns:
        valid_ema = [v for v in df_sub["EMA_200"].values if not np.isnan(v)]
        if valid_ema:
            all_prices.extend(valid_ema)
            
    p_min = min(all_prices)
    p_max = max(all_prices)
    y_margin = max((p_max - p_min) * 0.12, p_min * 0.005)
    ax.set_ylim(p_min - y_margin, p_max + y_margin)
    
    # Configurar Eje X con marcas de tiempo legibles
    step = max(1, len(df_sub) // 6)
    ticks = indices[::step]
    labels = [pd.to_datetime(df_sub["Open Time"].iloc[idx]).strftime("%d-%b %H:%M") for idx in ticks]
    ax.set_xticks(ticks)
    ax.set_xticklabels(labels, color="#8a99ad", fontsize=8.5, rotation=12)
    
    # Formato general de ejes y rejilla
    ax.set_xlim(xmin, xmax)
    ax.tick_params(colors="#8a99ad", labelsize=9)
    ax.grid(True, linestyle=":", alpha=0.18, color="#94a3b8")
    
    # Título y Leyenda
    ax.set_title(f"{symbol} — {title}", color="#f8fafc", fontsize=12, fontweight="bold", pad=14)
    legend = ax.legend(loc="upper left", facecolor="#0b1322", edgecolor="#1e293b", fontsize=8)
    for text in legend.get_texts():
        text.set_color("#e2e8f0")
        
    # Ajustar bordes
    for spine in ax.spines.values():
        spine.set_color("#1e293b")
        
    plt.tight_layout()
    
    # Guardar en buffer de memoria
    buf = io.BytesIO()
    plt.savefig(buf, format="png", facecolor=fig.get_facecolor(), edgecolor="none", dpi=130)
    plt.close(fig)
    buf.seek(0)
    return buf

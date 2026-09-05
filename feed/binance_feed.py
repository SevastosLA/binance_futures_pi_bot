"""
Módulo de Recolección de Datos en Vivo desde Binance Futures API.
Obtiene Klines históricas (1h / 15m) y precios en tiempo real con reintentos automáticos y backoff.
"""

import time
import logging
import requests
import pandas as pd
import numpy as np
from typing import Optional, Dict, Any, List

logger = logging.getLogger("BinanceFeed")

class BinanceFuturesFeed:
    BASE_URL = "https://fapi.binance.com"

    def __init__(self, session: Optional[requests.Session] = None):
        self.session = session or requests.Session()
        self.session.headers.update({
            "User-Agent": "AntigravityFuturesBot/1.0 (RaspberryPi)"
        })

    def _get_with_retry(self, endpoint: str, params: Dict[str, Any], max_retries: int = 5) -> Optional[Any]:
        url = f"{self.BASE_URL}{endpoint}"
        delay = 1.0
        for attempt in range(1, max_retries + 1):
            try:
                resp = self.session.get(url, params=params, timeout=10.0)
                if resp.status_code == 200:
                    return resp.json()
                elif resp.status_code == 429:
                    logger.warning(f"Rate limit alcanzado (HTTP 429). Esperando {delay*2}s...")
                    time.sleep(delay * 2)
                else:
                    logger.warning(f"Binance API retornó status {resp.status_code}: {resp.text}")
            except (requests.RequestException, Exception) as e:
                logger.warning(f"Intento {attempt}/{max_retries} falló conectando a Binance: {e}")
            
            time.sleep(delay)
            delay = min(delay * 2.0, 30.0)
        logger.error(f"Fallo crítico al consultar endpoint {endpoint} tras {max_retries} reintentos.")
        return None

    def fetch_klines(self, symbol: str, interval: str = "1h", limit: int = 1000) -> Optional[pd.DataFrame]:
        params = {
            "symbol": symbol.upper(),
            "interval": interval,
            "limit": limit
        }
        data = self._get_with_retry("/fapi/v1/klines", params)
        if not data:
            return None

        rows = []
        for k in data:
            rows.append({
                "Open Time": pd.to_datetime(k[0], unit="ms"),
                "Open": float(k[1]),
                "High": float(k[2]),
                "Low": float(k[3]),
                "Close": float(k[4]),
                "Volume": float(k[5]),
                "Close Time": pd.to_datetime(k[6], unit="ms")
            })

        df = pd.DataFrame(rows).sort_values("Open Time").reset_index(drop=True)
        return df

    def fetch_latest_price(self, symbol: str) -> Optional[float]:
        params = {"symbol": symbol.upper()}
        data = self._get_with_retry("/fapi/v1/ticker/price", params)
        if data and "price" in data:
            return float(data["price"])
        return None

    def fetch_all_latest_prices(self, symbols: List[str]) -> Dict[str, float]:
        data = self._get_with_retry("/fapi/v1/ticker/price", {})
        prices = {}
        if data:
            symbol_set = set(s.upper() for s in symbols)
            for item in data:
                sym = item.get("symbol")
                if sym in symbol_set:
                    prices[sym] = float(item["price"])
        return prices

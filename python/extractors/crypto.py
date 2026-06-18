"""
extractors/crypto.py
────────────────────
Fetches near-real-time cryptocurrency metrics using ccxt (async).
Normalized into VisionEvent schema. Checks >2% shift thresholds.
"""

import logging
import os
from typing import Any, Dict, List, Optional

import ccxt
from core.base import BaseExtractor
from core.schema import VisionEvent
from core.utils import stable_id, to_iso, utcnow_iso

logger = logging.getLogger("vision_i.extractor.crypto")

DEFAULT_MARKETS = ["BTC/USDT", "ETH/USDT", "SOL/USDT"]
DEFAULT_EXCHANGE = "binance"
DEFAULT_FALLBACKS = ["kraken", "coinbase", "kucoin", "bitfinex"]
EXCHANGE_URLS = {
    "binance": "https://www.binance.com/en/trade/{asset}_USDT",
    "kraken": "https://pro.kraken.com/app/trade/{asset}-USDT",
    "coinbase": "https://www.coinbase.com/price/{asset}",
    "kucoin": "https://www.kucoin.com/trade/{asset}-USDT",
    "bitfinex": "https://trading.bitfinex.com/t/{asset}:USD",
}

class CryptoExtractor(BaseExtractor):
    """
    Fetches latest ticker data utilizing CCXT async framework.
    Normalizes outputs to Market Events and triggers signal anomalies.
    """
    
    source_name = "crypto"
    
    def fetch(self, symbols: Optional[List[str]] = None, **kwargs) -> List[Dict]:
        """Fetch crypto tickers with a resilient exchange fallback chain."""
        if os.getenv("CRYPTO_DISABLED", "").lower() in {"1", "true", "yes"}:
            return []

        markets = symbols if symbols else DEFAULT_MARKETS
        primary = os.getenv("CRYPTO_EXCHANGE", DEFAULT_EXCHANGE).strip().lower()
        fallback_env = os.getenv("CRYPTO_EXCHANGE_FALLBACKS", "")
        fallbacks = [e.strip().lower() for e in fallback_env.split(",") if e.strip()] or DEFAULT_FALLBACKS
        exchange_order = [primary] + [e for e in fallbacks if e != primary]

        last_error: Optional[Exception] = None
        for exchange_id in exchange_order:
            try:
                results = self._fetch_with_exchange(exchange_id, markets)
                if results:
                    self.source_name = exchange_id
                    return results
            except Exception as exc:
                last_error = exc
                self.logger.warning("Crypto fetch failed on %s: %s", exchange_id, exc)

        if last_error:
            self.logger.error("Crypto fetch failed for all exchanges: %s", last_error)
        return []

    def _fetch_with_exchange(self, exchange_id: str, markets: List[str]) -> List[Dict]:
        if not hasattr(ccxt, exchange_id):
            raise ValueError(f"Unsupported exchange: {exchange_id}")

        results: List[Dict] = []
        exchange_cls = getattr(ccxt, exchange_id)
        exchange = exchange_cls({
            "enableRateLimit": True,
            "timeout": 15000,
        })
        try:
            tickers = exchange.fetch_tickers(markets)
            for symbol, data in tickers.items():
                if not data:
                    continue

                last = data.get("last", 0)
                open_price = data.get("open", last)
                change_pct = data.get("percentage", 0.0)
                change = data.get("change", 0.0)

                ts_str = utcnow_iso()
                if data.get("timestamp"):
                    import datetime
                    ts_str = datetime.datetime.fromtimestamp(
                        data["timestamp"] / 1000.0,
                        tz=datetime.timezone.utc,
                    ).isoformat()

                results.append({
                    "symbol": symbol,
                    "name": symbol.replace("/USDT", ""),
                    "timestamp": ts_str,
                    "open": float(open_price) if open_price else float(last),
                    "close": float(last),
                    "high": float(data.get("high") or last),
                    "low": float(data.get("low") or last),
                    "volume": float(data.get("baseVolume", 0)),
                    "change": float(change),
                    "change_pct": float(change_pct),
                })
        finally:
            try:
                exchange.close()
            except Exception:
                pass

        return results

    def normalize(self, item: Any) -> VisionEvent:
        symbol     = item["symbol"]
        name       = item["name"]
        change_pct = item["change_pct"]
        close      = item["close"]
        change     = item["change"]
        
        direction = "up" if change >= 0 else "down"

        # Explicit Threshold matching >2% logic
        tags = ["crypto", "market", "finance", name.lower()]
        if change_pct > 2.0:
            sentiment = {"label": "POSITIVE", "score": 0.85}
            tags.append("market_anomaly_bullish")
        elif change_pct < -2.0:
            sentiment = {"label": "NEGATIVE", "score": 0.20}
            tags.append("market_anomaly_bearish")
        else:
            sentiment = {"label": "NEUTRAL",  "score": 0.5}

        # Format beautiful payload for JARVIS
        desc = (
            f"{symbol} trading at ${close:,.2f}, "
            f"moved {direction} ${abs(change):,.2f} "
            f"({change_pct:+.2f}%). Vol: {item['volume']:,.0f}."
        )

        exchange_name = self.source_name or "crypto"
        exchange_url = EXCHANGE_URLS.get(exchange_name)
        url = exchange_url.format(asset=name) if exchange_url else None

        return VisionEvent(
            event_id   = stable_id(self.source_name, f"{symbol}_{item['timestamp']}"),
            source     = self.source_name,
            source_id  = f"{symbol}-{item['timestamp']}",
            event_type = "market",
            title      = f"Crypto Market: {symbol} {change_pct:+.2f}%",
            description= desc,
            body       = desc,
            url        = url,
            language   = "en",
            timestamp  = to_iso(item["timestamp"]),
            ingest_time= utcnow_iso(),
            actors     = [{"name": name, "type": "ASSET"}],
            location   = None,
            sentiment  = sentiment,
            tags       = tags,
            extras     = {
                "symbol":     symbol,
                "close":      close,
                "change_pct": change_pct,
                "exchange":   exchange_name,
            },
            raw = item,
        )

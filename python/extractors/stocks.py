"""
extractors/stocks.py
────────────────────
Fetches near-real-time stock prices using yfinance (Yahoo Finance).

yfinance is a third-party library that scrapes Yahoo Finance.
No API key required.
"""

from typing import Any, Dict, List, Optional

import yfinance as yf

from core.base import BaseExtractor
from core.market_entities import default_ticker_names, market_actor_payloads
from core.schema import VisionEvent
from core.utils import stable_id, to_iso, utcnow_iso

# Default watchlist — extend or override via fetch(tickers=...)
DEFAULT_TICKERS: Dict[str, str] = default_ticker_names()


class StockExtractor(BaseExtractor):
    """
    Fetches the latest 1-minute bar for each tracked ticker.

    fetch() params:
        tickers   dict[str, str]  override default watchlist {symbol: display_name}
        limit     int             max number of tickers to fetch (default: all)
    """

    source_name = "yahoo_finance"

    def fetch(
        self,
        tickers: Optional[Dict[str, str]] = None,
        limit: int = 100,
        **_,
    ) -> List[Dict]:
        watchlist = tickers or DEFAULT_TICKERS
        results: List[Dict] = []

        for symbol, name in list(watchlist.items())[:limit]:
            try:
                ticker = yf.Ticker(symbol)
                hist   = ticker.history(period="1d", interval="1m")

                if hist.empty:
                    self.logger.warning("No data for %s", symbol)
                    continue

                last = hist.iloc[-1]
                open_price  = hist.iloc[0]["Open"]
                close_price = float(last["Close"])
                change      = round(close_price - open_price, 4)

                # Pandas Timestamp may be tz-aware or tz-naive depending on yfinance version
                ts_raw = last.name
                try:
                    if hasattr(ts_raw, "tzinfo") and ts_raw.tzinfo is not None:
                        ts_str = ts_raw.isoformat()
                    else:
                        ts_str = ts_raw.isoformat() + "Z"
                except Exception:
                    from core.utils import utcnow_iso
                    ts_str = utcnow_iso()

                results.append({
                    "symbol":     symbol,
                    "name":       name,
                    "timestamp":  ts_str,
                    "open":       round(float(open_price),  4),
                    "close":      round(close_price,         4),
                    "high":       round(float(last["High"]), 4),
                    "low":        round(float(last["Low"]),  4),
                    "volume":     int(last["Volume"]),
                    "change":     change,
                    "change_pct": round((change / open_price) * 100, 4) if open_price else 0,
                })

            except Exception as exc:
                self.logger.error("Stock fetch failed for %s: %s", symbol, exc)

        return results

    def normalize(self, item: Any) -> VisionEvent:
        symbol     = item["symbol"]
        name       = item["name"]
        change_pct = item.get("change_pct", 0.0)
        close      = item["close"]
        change     = item["change"]

        direction = "up" if change >= 0 else "down"
        market_match = {
            "symbol": symbol,
            "name": name,
            "aliases": [symbol, f"${symbol}", name],
            "sector": None,
        }

        if change_pct > 1.0:
            sentiment = {"label": "POSITIVE", "score": 0.8}
        elif change_pct < -1.0:
            sentiment = {"label": "NEGATIVE", "score": 0.2}
        else:
            sentiment = {"label": "NEUTRAL",  "score": 0.5}

        return VisionEvent(
            event_id   = stable_id(self.source_name, f"{symbol}_{item['timestamp']}"),
            source     = self.source_name,
            source_id  = f"{symbol}-{item['timestamp']}",
            event_type = "market",
            title      = f"{name} ({symbol}) {direction} {abs(change_pct):.2f}%",
            description= (
                f"{name} closed at ${close:.2f}, "
                f"{'up' if change >= 0 else 'down'} ${abs(change):.2f} "
                f"({change_pct:+.2f}%). Volume: {item['volume']:,}."
            ),
            body       = (
                f"Market movement for {name}. "
                f"Open: ${item['open']:.2f}, Close: ${close:.2f}, "
                f"High: ${item['high']:.2f}, Low: ${item['low']:.2f}. "
                f"Volume: {item['volume']:,}."
            ),
            url        = f"https://finance.yahoo.com/quote/{symbol}",
            language   = "en",
            timestamp  = to_iso(item["timestamp"]),
            ingest_time= utcnow_iso(),
            actors     = market_actor_payloads([market_match]),
            location   = None,
            sentiment  = sentiment,
            tags       = ["stock", "market", "finance", symbol.lower(), name.lower()],
            extras     = {
                "symbol":     symbol,
                "company":    name,
                "market_entity": market_match,
                "open":       item["open"],
                "close":      close,
                "high":       item["high"],
                "low":        item["low"],
                "volume":     item["volume"],
                "change":     change,
                "change_pct": change_pct,
            },
            raw = item,
        )

"""
intelligence/predictor.py
─────────────────────────
Lightweight time-series forecasting for narrative strength and event volume.

Uses statsmodels ARIMA when available; falls back to a moving-average heuristic
if statsmodels is not installed (so the predictor never crashes the API).

Public API:
    forecast_series(values: list[float], horizon: int = 12) -> ForecastResult
    forecast_narrative(narrative_id: str, db_session) -> ForecastResult
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional

try:
    from statsmodels.tsa.arima.model import ARIMA  # type: ignore
    _HAS_STATSMODELS = True
except Exception:
    _HAS_STATSMODELS = False


@dataclass
class ForecastResult:
    horizon: int
    history: List[float]
    forecast: List[float]
    lower: List[float] = field(default_factory=list)
    upper: List[float] = field(default_factory=list)
    method: str = "naive"
    confidence: float = 0.5


def _moving_average_forecast(values: List[float], horizon: int) -> ForecastResult:
    if not values:
        return ForecastResult(horizon=horizon, history=[], forecast=[0.0] * horizon, method="empty", confidence=0.0)
    window = min(len(values), 5)
    base = sum(values[-window:]) / window
    drift = (values[-1] - values[0]) / max(len(values) - 1, 1)
    forecast = [max(0.0, base + drift * (i + 1)) for i in range(horizon)]
    spread = max(0.05 * base, 0.5)
    return ForecastResult(
        horizon=horizon,
        history=values,
        forecast=forecast,
        lower=[max(0.0, f - spread) for f in forecast],
        upper=[f + spread for f in forecast],
        method="moving_average",
        confidence=0.55,
    )


def forecast_series(values: List[float], horizon: int = 12) -> ForecastResult:
    """Forecast `horizon` future steps from a 1-D series."""
    cleaned = [float(v) for v in values if v is not None]
    if len(cleaned) < 6 or not _HAS_STATSMODELS:
        return _moving_average_forecast(cleaned, horizon)
    try:
        model = ARIMA(cleaned, order=(1, 1, 1))
        fit = model.fit()
        pred = fit.get_forecast(steps=horizon)
        mean = [float(v) for v in pred.predicted_mean]
        conf = pred.conf_int(alpha=0.2)
        lower = [float(row[0]) for row in conf]
        upper = [float(row[1]) for row in conf]
        return ForecastResult(
            horizon=horizon,
            history=cleaned,
            forecast=mean,
            lower=lower,
            upper=upper,
            method="arima(1,1,1)",
            confidence=0.78,
        )
    except Exception:
        return _moving_average_forecast(cleaned, horizon)


def forecast_narrative(narrative_id: str, history: Optional[List[float]] = None, horizon: int = 12) -> ForecastResult:
    """Convenience wrapper for forecasting a narrative-strength time series."""
    return forecast_series(history or [], horizon=horizon)

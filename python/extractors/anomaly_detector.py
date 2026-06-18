"""
extractors/anomaly_detector.py
───────────────────────────────
Hybrid Rule-based and ML Anomaly Detection for Air and Maritime domains.
Uses IsolationForest for ML predictions combined with hard tactical rules.
"""

import logging
from typing import Optional, Dict, Any, List

try:
    from sklearn.ensemble import IsolationForest
    import numpy as np
    ML_AVAILABLE = True
except ImportError:
    ML_AVAILABLE = False


logger = logging.getLogger("vision_i.extractors.anomaly")


class HybridAnomalyDetector:
    """
    ML/Rule-hybrid detection for transport assets (Air, Sea).
    Keeps a rolling buffer of vectors to train an Isolation Forest on the fly.
    """

    def __init__(self, domain: str, buffer_size: int = 1000, contamination: float = 0.01):
        self.domain = domain.lower()
        self.buffer_size = buffer_size
        self.contamination = contamination
        
        self._buffer: List[List[float]] = []
        self._model = IsolationForest(contamination=contamination, random_state=42) if ML_AVAILABLE else None
        self._is_fitted = False

    def check_air_anomaly(self, squawk: Optional[str], alt: Optional[float], grounded: bool, vel: Optional[float]) -> Optional[str]:
        # 1. Rule-based checks (Hard thresholds)
        if squawk:
            sq = str(squawk).strip()
            if sq == "7500": return "Squawk 7500 — Hijack"
            if sq == "7600": return "Squawk 7600 — Radio failure"
            if sq == "7700": return "Squawk 7700 — General emergency"

        if not grounded and alt is not None:
            if alt < 300 and alt > 0:
                return f"Critical low altitude anomaly ({alt:.0f}m)"

        if vel is not None and vel > 340: # Approaching Mach 1 for civilian track is anomalous
            return f"Kinematic velocity anomaly ({vel:.0f}m/s)"

        # 2. ML-based check (Isolation Forest)
        if ML_AVAILABLE and vel is not None and alt is not None:
            vector = [float(vel), float(alt)]
            self._buffer.append(vector)
            
            # Maintain buffer size
            if len(self._buffer) > self.buffer_size:
                self._buffer.pop(0)
                
            # Train model if enough samples
            if len(self._buffer) >= 50:
                # Refit periodically (every 10 new samples to avoid overhead)
                if len(self._buffer) % 10 == 0:
                    try:
                        self._model.fit(self._buffer) # type: ignore
                        self._is_fitted = True
                    except Exception:
                        pass
                
                if self._is_fitted:
                    pred = self._model.predict([vector]) # type: ignore
                    if pred[0] == -1:
                        return f"ML Detection: Unusal flight profile (Alt: {alt:.0f}m, Vel: {vel:.0f}m/s)"
                        
        return None

    def check_sea_anomaly(self, speed: Optional[float], nav_status: Optional[int], has_latlon: bool = True) -> Optional[str]:
        # 1. Rule-based checks
        if not has_latlon:
            return "Dark Track (Missing AIS Positional Data)"
            
        if nav_status is not None:
            if nav_status == 12: # "Reserved for regional use" but often used for emergencies/anomalies in AIS specs
                return "Nav status protocol breach"
                
        if speed is not None:
            if speed > 60:
                return f"Anomalous kinematic velocity ({speed:.0f} knots)"
            # A vessel reporting zero speed but nav_status == 0 (under way using engine)
            if speed == 0 and nav_status == 0:
                return "Kinematic/Nav state mismatch (Dead in water but reporting underway)"

        # 2. ML-based check
        if ML_AVAILABLE and speed is not None:
            vector = [float(speed), float(nav_status if nav_status else 0)]
            self._buffer.append(vector)
            
            if len(self._buffer) > self.buffer_size:
                self._buffer.pop(0)
                
            if len(self._buffer) >= 50:
                if len(self._buffer) % 10 == 0:
                    try:
                        self._model.fit(self._buffer) # type: ignore
                        self._is_fitted = True
                    except Exception:
                        pass
                        
                if self._is_fitted:
                    pred = self._model.predict([vector]) # type: ignore
                    if pred[0] == -1:
                        return f"ML Detection: Unusual maritime behavior (Spd: {speed:.0f}kt, Stat: {nav_status})"

        return None

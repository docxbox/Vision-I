"""
intelligence/escalation_scorer.py
â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
Multi-domain composite escalation-probability scorer per world region.

Six signals are drawn from existing PostgreSQL tables and combined into a
single escalation score for each of seven geopolitical regions:

  1. ais_anomaly        â€” unusual vessel clustering near chokepoints
                          (events where source LIKE '%ais%' with lat/lon)
  2. hate_speech_spike  â€” events tagged hate_speech/propaganda, z-score vs baseline
  3. sortie_rate        â€” aircraft events/hour vs baseline
                          (source LIKE '%opensky%' OR '%flight%')
  4. market_volatility  â€” events where event_type='market' and
                          |sentiment_score - 0.5| > 0.3
  5. narrative_intensityâ€” count of active HIGH/CRITICAL narratives in the
                          narratives table (gracefully skipped if absent)
  6. source_silence     â€” drop in normally-active regional sources
                          (inverse of the source-silencing anomaly)

Composite score = weighted sum of normalised signals (each 0.0â€“1.0):
  Weights: ais=0.20, hate_speech=0.25, sortie=0.20, market=0.15,
           narrative=0.15, silence=0.05

Risk thresholds:
  â‰¥ 0.75 â†’ CRITICAL  |  â‰¥ 0.55 â†’ HIGH  |  â‰¥ 0.35 â†’ ELEVATED  |  < 0.35 â†’ LOW

Regions: MENA, EUROPE, INDO_PACIFIC, EASTERN_EUROPE, SUB_SAHARAN_AFRICA,
         LATIN_AMERICA, SOUTH_ASIA

Usage:
    scorer  = EscalationScorer(session)
    results = await scorer.score_all_regions(window_hours=6)
"""

from __future__ import annotations

import logging
import math
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Set

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from storage.database import EventModel

logger = logging.getLogger("vision_i.intelligence.escalation_scorer")
_W_AIS        = 0.20
_W_HATE       = 0.25
_W_SORTIE     = 0.20
_W_MARKET     = 0.15
_W_NARRATIVE  = 0.15
_W_SILENCE    = 0.05

# Baseline days for z-score comparison
_BASELINE_DAYS = 7

# Sentiment extremity threshold for market volatility
_MARKET_SENTIMENT_EXTREMITY = 0.3

# Risk thresholds
_RISK_CRITICAL  = 0.75
_RISK_HIGH      = 0.55
_RISK_ELEVATED  = 0.35

# Regions
ALL_REGIONS: List[str] = [
    "MENA",
    "EUROPE",
    "INDO_PACIFIC",
    "EASTERN_EUROPE",
    "SUB_SAHARAN_AFRICA",
    "LATIN_AMERICA",
    "SOUTH_ASIA",
]
COUNTRY_TO_REGION: Dict[str, str] = {
    # MENA (Middle East & North Africa)
    "Egypt":        "MENA",
    "Syria":        "MENA",
    "Iraq":         "MENA",
    "Iran":         "MENA",
    "Israel":       "MENA",
    "Palestine":    "MENA",
    "Lebanon":      "MENA",
    "Jordan":       "MENA",
    "Saudi Arabia": "MENA",
    "Yemen":        "MENA",
    "Libya":        "MENA",
    "Tunisia":      "MENA",
    "Algeria":      "MENA",
    "Morocco":      "MENA",
    "Oman":         "MENA",
    "Qatar":        "MENA",
    "Kuwait":       "MENA",
    "Bahrain":      "MENA",
    "UAE":          "MENA",
    # EUROPE (Western)
    "France":          "EUROPE",
    "Germany":         "EUROPE",
    "United Kingdom":  "EUROPE",
    "UK":              "EUROPE",
    "Italy":           "EUROPE",
    "Spain":           "EUROPE",
    "Netherlands":     "EUROPE",
    "Belgium":         "EUROPE",
    "Sweden":          "EUROPE",
    "Norway":          "EUROPE",
    "Denmark":         "EUROPE",
    "Finland":         "EUROPE",
    "Switzerland":     "EUROPE",
    "Austria":         "EUROPE",
    "Greece":          "EUROPE",
    "Portugal":        "EUROPE",
    # EASTERN EUROPE
    "Ukraine":         "EASTERN_EUROPE",
    "Russia":          "EASTERN_EUROPE",
    "Belarus":         "EASTERN_EUROPE",
    "Poland":          "EASTERN_EUROPE",
    "Hungary":         "EASTERN_EUROPE",
    "Romania":         "EASTERN_EUROPE",
    "Moldova":         "EASTERN_EUROPE",
    "Czech Republic":  "EASTERN_EUROPE",
    "Slovakia":        "EASTERN_EUROPE",
    "Bulgaria":        "EASTERN_EUROPE",
    "Serbia":          "EASTERN_EUROPE",
    "Kosovo":          "EASTERN_EUROPE",
    "Georgia":         "EASTERN_EUROPE",
    "Armenia":         "EASTERN_EUROPE",
    "Azerbaijan":      "EASTERN_EUROPE",
    # INDO-PACIFIC
    "China":           "INDO_PACIFIC",
    "Japan":           "INDO_PACIFIC",
    "South Korea":     "INDO_PACIFIC",
    "North Korea":     "INDO_PACIFIC",
    "Taiwan":          "INDO_PACIFIC",
    "Philippines":     "INDO_PACIFIC",
    "Vietnam":         "INDO_PACIFIC",
    "Indonesia":       "INDO_PACIFIC",
    "Malaysia":        "INDO_PACIFIC",
    "Australia":       "INDO_PACIFIC",
    "New Zealand":     "INDO_PACIFIC",
    "Singapore":       "INDO_PACIFIC",
    "Thailand":        "INDO_PACIFIC",
    "Myanmar":         "INDO_PACIFIC",
    "Cambodia":        "INDO_PACIFIC",
    # SOUTH ASIA
    "India":           "SOUTH_ASIA",
    "Pakistan":        "SOUTH_ASIA",
    "Bangladesh":      "SOUTH_ASIA",
    "Afghanistan":     "SOUTH_ASIA",
    "Sri Lanka":       "SOUTH_ASIA",
    "Nepal":           "SOUTH_ASIA",
    # LATIN AMERICA
    "Brazil":          "LATIN_AMERICA",
    "Mexico":          "LATIN_AMERICA",
    "Venezuela":       "LATIN_AMERICA",
    "Colombia":        "LATIN_AMERICA",
    "Argentina":       "LATIN_AMERICA",
    "Peru":            "LATIN_AMERICA",
    "Chile":           "LATIN_AMERICA",
    "Cuba":            "LATIN_AMERICA",
    "Bolivia":         "LATIN_AMERICA",
    "Ecuador":         "LATIN_AMERICA",
    "Haiti":           "LATIN_AMERICA",
    "Nicaragua":       "LATIN_AMERICA",
    "El Salvador":     "LATIN_AMERICA",
    "Honduras":        "LATIN_AMERICA",
    "Guatemala":       "LATIN_AMERICA",
    # SUB-SAHARAN AFRICA
    "Sudan":           "SUB_SAHARAN_AFRICA",
    "South Sudan":     "SUB_SAHARAN_AFRICA",
    "Ethiopia":        "SUB_SAHARAN_AFRICA",
    "Somalia":         "SUB_SAHARAN_AFRICA",
    "Nigeria":         "SUB_SAHARAN_AFRICA",
    "Mali":            "SUB_SAHARAN_AFRICA",
    "Niger":           "SUB_SAHARAN_AFRICA",
    "Burkina Faso":    "SUB_SAHARAN_AFRICA",
    "Democratic Republic of the Congo": "SUB_SAHARAN_AFRICA",
    "Congo":           "SUB_SAHARAN_AFRICA",
    "Mozambique":      "SUB_SAHARAN_AFRICA",
    "Zimbabwe":        "SUB_SAHARAN_AFRICA",
    "Kenya":           "SUB_SAHARAN_AFRICA",
    "Chad":            "SUB_SAHARAN_AFRICA",
    "Cameroon":        "SUB_SAHARAN_AFRICA",
    "Central African Republic": "SUB_SAHARAN_AFRICA",
}


def _location_to_region(location_name: Optional[str]) -> Optional[str]:
    """
    Map a free-text location name to one of the seven regions.
    Looks for any country name contained in the string.
    Returns None if no match is found.
    """
    if not location_name:
        return None
    lower = location_name.lower()
    for country, region in COUNTRY_TO_REGION.items():
        if country.lower() in lower:
            return region
    return None


def _risk_level(score: float) -> str:
    if score >= _RISK_CRITICAL:
        return "CRITICAL"
    if score >= _RISK_HIGH:
        return "HIGH"
    if score >= _RISK_ELEVATED:
        return "ELEVATED"
    return "LOW"

@dataclass
class EscalationScore:
    region:      str
    score:       float            # 0.0 â€“ 1.0
    risk_level:  str              # LOW | ELEVATED | HIGH | CRITICAL
    drivers:     List[str]        # top contributing signal names, sorted descending
    confidence:  float            # 0.0 â€“ 1.0 (based on data volume)
    event_count: int
    computed_at: str              # ISO timestamp

    def to_dict(self) -> Dict:
        return {
            "region":      self.region,
            "score":       round(self.score, 4),
            "risk_level":  self.risk_level,
            "drivers":     self.drivers,
            "confidence":  round(self.confidence, 4),
            "event_count": self.event_count,
            "computed_at": self.computed_at,
        }

class EscalationScorer:
    """
    Reads from PostgreSQL and returns EscalationScore objects per region.

    One instance lives on app.state; all methods are stateless between calls.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def score_all_regions(
        self,
        window_hours: int = 6,
    ) -> List[EscalationScore]:
        """
        Compute escalation scores for all seven regions.

        Returns a list sorted by score descending.
        """
        results: List[EscalationScore] = []
        try:
            for region in ALL_REGIONS:
                result = await self.score_region(region, window_hours)
                results.append(result)

            results.sort(key=lambda r: r.score, reverse=True)
            logger.info(
                "EscalationScorer: scored %d regions (window=%dh)",
                len(results), window_hours,
            )

        except Exception as exc:
            logger.error("EscalationScorer.score_all_regions() failed: %s", exc)

        return results

    async def score_region(
        self,
        region:       str,
        window_hours: int = 6,
    ) -> EscalationScore:
        """
        Compute the escalation score for a single region.

        Returns a zeroed EscalationScore on any error so callers can always
        depend on a valid return value.
        """
        now            = datetime.now(timezone.utc)
        window_start   = now - timedelta(hours=window_hours)
        baseline_start = now - timedelta(days=_BASELINE_DAYS)

        _zero = EscalationScore(
            region=region, score=0.0, risk_level="LOW",
            drivers=[], confidence=0.0, event_count=0,
            computed_at=now.isoformat() + "Z",
        )

        try:
            recent_evs   = await self._fetch_regional_events(region, window_start, now)
            baseline_evs = await self._fetch_regional_events(region, baseline_start, window_start)

            total_recent   = len(recent_evs)
            total_baseline = len(baseline_evs)
            sig_ais      = self._signal_ais_anomaly(recent_evs, baseline_evs, window_hours)
            sig_hate     = self._signal_hate_speech_spike(recent_evs, baseline_evs, window_hours)
            sig_sortie   = self._signal_sortie_rate(recent_evs, baseline_evs, window_hours)
            sig_market   = self._signal_market_volatility(recent_evs)
            sig_narrative= await self._signal_narrative_intensity(region, window_start, now)
            sig_silence  = self._signal_source_silence(recent_evs, baseline_evs, window_hours)

            weighted_signals = {
                "ais_anomaly":       (_W_AIS,       sig_ais),
                "hate_speech_spike": (_W_HATE,      sig_hate),
                "sortie_rate":       (_W_SORTIE,    sig_sortie),
                "market_volatility": (_W_MARKET,    sig_market),
                "narrative_intensity":(_W_NARRATIVE, sig_narrative),
                "source_silence":    (_W_SILENCE,   sig_silence),
            }

            composite = sum(w * s for w, s in weighted_signals.values())
            composite  = max(0.0, min(1.0, composite))

            # Drivers: signal names that contribute most (signal * weight > 0.05)
            drivers = sorted(
                [name for name, (w, s) in weighted_signals.items() if w * s > 0.05],
                key=lambda n: weighted_signals[n][0] * weighted_signals[n][1],
                reverse=True,
            )

            # Confidence: based on data volume in the window
            confidence = min(1.0, total_recent / max(50.0, 1.0))

            return EscalationScore(
                region      = region,
                score       = round(composite, 4),
                risk_level  = _risk_level(composite),
                drivers     = drivers[:4],
                confidence  = round(confidence, 4),
                event_count = total_recent,
                computed_at = now.isoformat() + "Z",
            )

        except Exception as exc:
            logger.error("EscalationScorer.score_region(%r) failed: %s", region, exc)
            # Rollback the aborted transaction so the shared session can be reused
            try:
                await self._session.rollback()
            except Exception:
                pass
            return _zero

    async def _fetch_regional_events(
        self,
        region:     str,
        from_time:  datetime,
        to_time:    datetime,
    ) -> List[Dict]:
        """
        Fetch events within the time window and filter to those that match
        the requested region via their location_name field.

        Returns a flat list of dicts with the fields we need.
        """
        rows = (
            await self._session.execute(
                select(
                    EventModel.event_id,
                    EventModel.title,
                    EventModel.source,
                    EventModel.event_type,
                    EventModel.timestamp,
                    EventModel.sentiment_score,
                    EventModel.location_name,
                    EventModel.location_lat,
                    EventModel.location_lon,
                    EventModel.tags,
                    EventModel.extras,
                )
                .where(
                    EventModel.timestamp >= from_time,
                    EventModel.timestamp <= to_time,
                )
                .order_by(EventModel.timestamp.desc())
                .limit(20_000)
            )
        ).fetchall()

        results = []
        for row in rows:
            row_region = _location_to_region(row.location_name)
            if row_region != region:
                continue
            results.append({
                "event_id":       row.event_id,
                "title":          row.title or "",
                "source":         row.source or "",
                "event_type":     row.event_type or "",
                "timestamp":      row.timestamp,
                "sentiment_score":row.sentiment_score,
                "location_name":  row.location_name,
                "lat":            row.location_lat,
                "lon":            row.location_lon,
                "tags":           row.tags or [],
                "extras":         row.extras or {},
            })
        return results

    @staticmethod
    def _signal_ais_anomaly(
        recent:       List[Dict],
        baseline:     List[Dict],
        window_hours: int,
    ) -> float:
        """
        Ratio of AIS vessel events in current window vs. normalised baseline.
        High ratio with lat/lon data â†’ suspicious clustering.
        """
        def ais_count(evs: List[Dict]) -> int:
            return sum(
                1 for e in evs
                if "ais" in e["source"].lower()
                and e.get("lat") is not None
                and e.get("lon") is not None
            )

        current_ais  = ais_count(recent)
        baseline_ais = ais_count(baseline)

        if baseline_ais == 0 and current_ais == 0:
            return 0.0

        baseline_hours = _BASELINE_DAYS * 24
        expected = (baseline_ais / max(baseline_hours, 1)) * window_hours
        if expected < 1:
            # No baseline â€” treat presence as moderate signal
            return min(1.0, current_ais / 10.0)

        std_dev = math.sqrt(max(expected, 1.0))
        z_score = (current_ais - expected) / std_dev
        return max(0.0, min(1.0, z_score / 5.0))

    @staticmethod
    def _signal_hate_speech_spike(
        recent:       List[Dict],
        baseline:     List[Dict],
        window_hours: int,
    ) -> float:
        """
        Z-score of hate_speech / propaganda tagged events, normalised to [0,1].
        """
        _hate_tags = {"hate_speech", "propaganda", "disinformation", "incitement"}

        def hate_count(evs: List[Dict]) -> int:
            return sum(
                1 for e in evs
                if any(t in _hate_tags for t in (e.get("tags") or []))
            )

        current_hate  = hate_count(recent)
        baseline_hate = hate_count(baseline)

        baseline_hours = _BASELINE_DAYS * 24
        expected = (baseline_hate / max(baseline_hours, 1)) * window_hours
        if expected < 1:
            return min(1.0, current_hate / 5.0)

        std_dev = math.sqrt(max(expected, 1.0))
        z_score = (current_hate - expected) / std_dev
        return max(0.0, min(1.0, z_score / 5.0))

    @staticmethod
    def _signal_sortie_rate(
        recent:       List[Dict],
        baseline:     List[Dict],
        window_hours: int,
    ) -> float:
        """
        Z-score for aircraft / OpenSky events per hour vs. baseline.
        """
        _flight_keys = {"opensky", "flight", "aviation", "aircraft"}

        def flight_count(evs: List[Dict]) -> int:
            return sum(
                1 for e in evs
                if any(k in e["source"].lower() for k in _flight_keys)
            )

        current_flights  = flight_count(recent)
        baseline_flights = flight_count(baseline)

        baseline_hours = _BASELINE_DAYS * 24
        expected = (baseline_flights / max(baseline_hours, 1)) * window_hours
        if expected < 1:
            return min(1.0, current_flights / 10.0)

        std_dev = math.sqrt(max(expected, 1.0))
        z_score = (current_flights - expected) / std_dev
        return max(0.0, min(1.0, z_score / 5.0))

    @staticmethod
    def _signal_market_volatility(recent: List[Dict]) -> float:
        """
        Fraction of market-type events with extreme sentiment (|score-0.5| > 0.3).
        """
        market_evs = [
            e for e in recent
            if e["event_type"] == "market"
            and e.get("sentiment_score") is not None
        ]
        if not market_evs:
            return 0.0
        extreme = sum(
            1 for e in market_evs
            if abs((e["sentiment_score"] or 0.5) - 0.5) > _MARKET_SENTIMENT_EXTREMITY
        )
        return extreme / len(market_evs)

    async def _signal_narrative_intensity(
        self,
        region:      str,
        from_time:   datetime,
        to_time:     datetime,
    ) -> float:
        """
        Count of HIGH/CRITICAL narratives in the narratives table that overlap
        with this region's keywords. Gracefully returns 0.0 if the table is absent.
        """
        try:
            result = await self._session.execute(
                text("""
                    SELECT COUNT(*) AS cnt
                    FROM narratives
                    WHERE detected_at BETWEEN :from_time AND :to_time
                      AND severity IN ('high', 'critical')
                      AND status = 'active'
                """),
                {"from_time": from_time, "to_time": to_time},
            )
            row = result.fetchone()
            count = row.cnt if row else 0
            # Normalise: 10+ HIGH/CRITICAL narratives â†’ 1.0
            return min(1.0, count / 10.0)

        except Exception as exc:
            logger.debug(
                "EscalationScorer._signal_narrative_intensity: narratives table "
                "unavailable (%s) â€” skipping", exc,
            )
            return 0.0

    @staticmethod
    def _signal_source_silence(
        recent:       List[Dict],
        baseline:     List[Dict],
        window_hours: int,
    ) -> float:
        """
        Detects a drop in normally-active regional sources.
        Returns a high value when previously-active sources have gone quiet.
        Inverse of the source-activity ratio.
        """
        baseline_sources: Dict[str, int] = defaultdict(int)
        for e in baseline:
            baseline_sources[e["source"]] += 1

        if not baseline_sources:
            return 0.0

        baseline_hours = _BASELINE_DAYS * 24
        recent_sources: Dict[str, int] = defaultdict(int)
        for e in recent:
            recent_sources[e["source"]] += 1

        silence_scores: List[float] = []
        for src, baseline_cnt in baseline_sources.items():
            expected = (baseline_cnt / max(baseline_hours, 1)) * window_hours
            if expected < 1:
                continue
            actual = recent_sources.get(src, 0)
            ratio  = actual / expected
            if ratio < 0.2:   # below 20% â†’ silence
                silence_scores.append(1.0 - ratio)

        if not silence_scores:
            return 0.0
        return min(1.0, sum(silence_scores) / max(len(baseline_sources), 1))


"""
intelligence/unrest_engine.py
-----------------------------
Builds a coherent unrest and influence watch view from existing event,
narrative, alert, and actor data.

The goal is not to predict with false certainty. It is to surface where
pressure is rising, what is driving it, who is involved, and how much of
that picture is corroborated across sources.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from statistics import mean
from typing import Any, Dict, Iterable, List, Optional, Sequence

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from core.geo import resolve_event_country
from storage.database import AlertModel, EventModel, NarrativeModel, OntologyActorModel

_SOCIAL_SOURCES = {"reddit", "telegram", "youtube", "twitter", "hackernews"}
_PROTEST_KEYWORDS = {
    "protest", "demonstration", "riot", "strike", "march", "uprising",
    "curfew", "clash", "mobilization", "shutdown", "blackout", "boycott",
    "unrest", "police", "violence", "security forces",
}


def _safe_text(*parts: Optional[str]) -> str:
    return " ".join(part.strip() for part in parts if isinstance(part, str) and part.strip())


def _clip(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return round(min(max(value, low), high), 4)


def _severity_weight(severity: Optional[str]) -> float:
    return {
        "critical": 1.0,
        "high": 0.75,
        "medium": 0.45,
        "low": 0.2,
    }.get((severity or "").lower(), 0.2)


def _trend_label(current: float, previous: float) -> str:
    if previous <= 0 and current > 0:
        return "rising"
    if previous <= 0:
        return "stable"
    ratio = current / previous
    if ratio >= 1.35:
        return "rising"
    if ratio <= 0.75:
        return "falling"
    return "stable"


def _top_list(counter: Counter, limit: int = 3) -> List[str]:
    return [name for name, _ in counter.most_common(limit)]


def _recommended_action(score: float, default: str = "monitor") -> str:
    if score >= 0.75:
        return "escalate to operations"
    if score >= 0.55:
        return "triage now"
    if score >= 0.35:
        return "investigate"
    return default


def _recommended_action_code(score: float, default: str = "monitor") -> str:
    if score >= 0.75:
        return "escalate_to_operations"
    if score >= 0.55:
        return "triage_now"
    if score >= 0.35:
        return "investigate"
    return default.replace(" ", "_")


def _driver_code(label: str) -> str:
    return (
        label.strip().lower()
        .replace("-", " ")
        .replace("/", " ")
        .replace("  ", " ")
        .replace(" ", "_")
    )


@dataclass
class _EventShape:
    event_id: str
    title: str
    description: str
    body: str
    source: str
    timestamp: Optional[datetime]
    risk_score: float
    sentiment_score: Optional[float]
    location_name: Optional[str]
    region: str
    actors: List[Dict[str, Any]]
    tags: List[str]
    extras: Dict[str, Any]

    @property
    def text(self) -> str:
        return _safe_text(self.title, self.description, self.body)

    @property
    def actor_names(self) -> List[str]:
        names: List[str] = []
        for actor in self.actors or []:
            name = (actor.get("canonical") or actor.get("name") or "").strip()
            if name:
                names.append(name)
        return names


class UnrestWatchEngine:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def build_watch(self, window_hours: int = 72) -> Dict[str, Any]:
        now = datetime.now(timezone.utc)
        current_start = now - timedelta(hours=max(window_hours, 6))
        previous_start = current_start - timedelta(hours=max(window_hours, 6))

        recent_events, previous_events = await self._load_events(current_start, previous_start)
        narratives = await self._load_narratives(current_start)
        alerts = await self._load_alerts(current_start)
        actors = await self._load_actors()

        regions = self._build_regions(recent_events, previous_events, narratives, alerts)
        actor_watch = self._build_actor_watch(actors, recent_events, narratives, alerts)
        narrative_watch = self._build_narrative_watch(narratives, recent_events, previous_events)
        alert_watch = self._build_alert_watch(alerts, narratives, recent_events)
        overview = self._build_overview(regions, narrative_watch, actor_watch, alert_watch)

        return {
            "generated_at": now.isoformat(),
            "window_hours": window_hours,
            "overview": overview,
            "regions": regions,
            "narratives": narrative_watch,
            "actors": actor_watch,
            "alerts": alert_watch,
        }

    async def _load_events(
        self,
        current_start: datetime,
        previous_start: datetime,
    ) -> tuple[list[_EventShape], list[_EventShape]]:
        rows = (
            await self._session.execute(
                select(EventModel)
                .where(EventModel.timestamp >= previous_start)
                .order_by(desc(EventModel.timestamp))
                .limit(2500)
            )
        ).scalars().all()

        recent: List[_EventShape] = []
        previous: List[_EventShape] = []
        for row in rows:
            region = self._resolve_region(row)
            shaped = _EventShape(
                event_id=row.event_id,
                title=row.title or "",
                description=row.description or "",
                body=row.body or "",
                source=row.source or "unknown",
                timestamp=row.timestamp,
                risk_score=float(row.risk_score or 0.0),
                sentiment_score=float(row.sentiment_score) if row.sentiment_score is not None else None,
                location_name=row.location_name,
                region=region,
                actors=row.actors or [],
                tags=[str(tag) for tag in (row.tags or []) if str(tag).strip()],
                extras=row.extras or {},
            )
            if row.timestamp and row.timestamp >= current_start:
                recent.append(shaped)
            else:
                previous.append(shaped)
        return recent, previous

    async def _load_narratives(self, current_start: datetime) -> List[NarrativeModel]:
        rows = (
            await self._session.execute(
                select(NarrativeModel)
                .where(NarrativeModel.detected_at >= current_start)
                .order_by(desc(NarrativeModel.detected_at))
                .limit(200)
            )
        ).scalars().all()
        return list(rows)

    async def _load_alerts(self, current_start: datetime) -> List[AlertModel]:
        rows = (
            await self._session.execute(
                select(AlertModel)
                .where(AlertModel.detected_at >= current_start)
                .order_by(desc(AlertModel.detected_at))
                .limit(200)
            )
        ).scalars().all()
        return list(rows)

    async def _load_actors(self) -> List[OntologyActorModel]:
        rows = (
            await self._session.execute(
                select(OntologyActorModel)
                .order_by(desc(OntologyActorModel.influence_score), desc(OntologyActorModel.mention_count))
                .limit(120)
            )
        ).scalars().all()
        return list(rows)

    def _resolve_region(self, row: EventModel) -> str:
        if row.location_name:
            return row.location_name
        event = {
            "location": {"name": row.location_name, "country": None},
            "actors": row.actors or [],
            "extras": row.extras or {},
        }
        country = resolve_event_country(event)
        return country or "Unspecified"

    def _build_regions(
        self,
        recent_events: Sequence[_EventShape],
        previous_events: Sequence[_EventShape],
        narratives: Sequence[NarrativeModel],
        alerts: Sequence[AlertModel],
    ) -> List[Dict[str, Any]]:
        previous_counts = Counter(event.region for event in previous_events if event.region)
        narrative_regions = defaultdict(list)
        for narrative in narratives:
            meta = narrative.meta_data or {}
            spread = meta.get("geographic_spread") or {}
            if isinstance(spread, dict):
                for region in spread.keys():
                    narrative_regions[str(region)].append(narrative)

        alert_regions = defaultdict(list)
        for alert in alerts:
            if alert.location:
                alert_regions[alert.location].append(alert)

        grouped = defaultdict(list)
        for event in recent_events:
            grouped[event.region].append(event)

        regions: List[Dict[str, Any]] = []
        for region, events in grouped.items():
            sentiments = [event.sentiment_score for event in events if event.sentiment_score is not None]
            avg_sentiment = mean(sentiments) if sentiments else 0.5
            negative_ratio = (
                sum(1 for score in sentiments if score <= 0.4) / len(sentiments)
                if sentiments else 0.0
            )
            avg_risk = mean([event.risk_score for event in events]) if events else 0.0
            actor_counter = Counter(
                actor for event in events for actor in event.actor_names
            )
            source_count = len({event.source for event in events if event.source})
            protest_signal = self._keyword_pressure(event.text for event in events)
            current_count = len(events)
            previous_count = previous_counts.get(region, 0)
            momentum = _trend_label(current_count, previous_count)
            narrative_count = len(narrative_regions.get(region, []))
            alert_count = len(alert_regions.get(region, []))
            top_topics = [n.topic for n in sorted(
                narrative_regions.get(region, []),
                key=lambda row: (row.strength or 0.0, row.event_count or 0),
                reverse=True,
            )[:3]]

            unrest_score = _clip(
                avg_risk * 0.35
                + negative_ratio * 0.2
                + min(current_count / 12.0, 1.0) * 0.15
                + min(narrative_count / 4.0, 1.0) * 0.15
                + min(alert_count / 3.0, 1.0) * 0.1
                + protest_signal * 0.05
            )
            driver = (
                "sentiment deterioration" if negative_ratio >= 0.45 else
                "corroborated alerts" if alert_count >= 2 else
                "narrative acceleration" if narrative_count >= 2 else
                "incident spike" if current_count >= 6 else
                "mixed pressure"
            )

            reason = (
                f"{current_count} events, {narrative_count} active narratives, "
                f"{alert_count} alert(s), and {negative_ratio:.0%} negative sentiment."
            )
            regions.append({
                "indicator_kind": "region",
                "evidence_kind": "correlated",
                "assessment_kind": "region_pressure",
                "region": region,
                "event_count": current_count,
                "avg_sentiment": round(avg_sentiment, 4),
                "negative_ratio": round(negative_ratio, 4),
                "avg_risk": round(avg_risk, 4),
                "source_count": source_count,
                "actor_count": len(actor_counter),
                "narrative_count": narrative_count,
                "alert_count": alert_count,
                "top_topics": top_topics,
                "top_actors": _top_list(actor_counter, 4),
                "momentum": momentum,
                "trajectory": momentum,
                "trajectory_code": momentum,
                "driver": driver,
                "driver_code": _driver_code(driver),
                "unrest_score": unrest_score,
                "recommended_action": _recommended_action(unrest_score),
                "recommended_action_code": _recommended_action_code(unrest_score),
                "geographic_confidence": 0.9 if region != "Unspecified" else 0.35,
                "observation_summary": f"{current_count} recent event(s) across {source_count} source(s) were observed in {region}.",
                "assessment_summary": f"Negative sentiment ratio is {negative_ratio:.0%} with average risk {avg_risk:.2f}, producing unrest score {unrest_score:.2f}.",
                "correlation_summary": f"{narrative_count} narrative(s), {alert_count} alert(s), and {len(actor_counter)} actor(s) are reinforcing this regional watch item.",
                "watch_reason": reason,
            })

        regions.sort(key=lambda item: item["unrest_score"], reverse=True)
        return regions[:12]

    def _build_narrative_watch(
        self,
        narratives: Sequence[NarrativeModel],
        recent_events: Sequence[_EventShape],
        previous_events: Sequence[_EventShape],
    ) -> List[Dict[str, Any]]:
        watch: List[Dict[str, Any]] = []
        for narrative in narratives:
            topic = (narrative.topic or "").strip()
            if not topic:
                continue
            recent_matches = self._match_topic_events(topic, recent_events, narrative)
            previous_matches = self._match_topic_events(topic, previous_events, narrative)
            momentum = _trend_label(len(recent_matches), len(previous_matches))

            geo_spread = {}
            meta = narrative.meta_data or {}
            if isinstance(meta.get("geographic_spread"), dict):
                geo_spread = {str(k): float(v) for k, v in meta["geographic_spread"].items()}

            top_region = next(iter(sorted(geo_spread.items(), key=lambda item: item[1], reverse=True)), (None, 0.0))[0]
            if top_region is None and recent_matches:
                top_region = Counter(event.region for event in recent_matches if event.region).most_common(1)[0][0]

            protest_signal = self._keyword_pressure(
                [topic] + list(narrative.sample_titles or [])
            )
            geo_concentration = max(geo_spread.values()) if geo_spread else 0.0
            unrest_score = _clip(
                float(narrative.strength or 0.0) * 0.35
                + float(narrative.confidence or 0.0) * 0.2
                + min((narrative.event_count or len(recent_matches)) / 10.0, 1.0) * 0.15
                + min((narrative.source_count or 0) / 5.0, 1.0) * 0.1
                + geo_concentration * 0.1
                + protest_signal * 0.1
            )
            driver = (
                "protest language" if protest_signal >= 0.35 else
                "geographic spread" if geo_concentration >= 0.45 else
                "source breadth" if (narrative.source_count or 0) >= 4 else
                "narrative acceleration"
            )

            reason = (
                f"{narrative.event_count or len(recent_matches)} linked events across "
                f"{narrative.source_count or 0} sources, momentum is {momentum}, "
                f"and the strongest geographic pressure is in {top_region or 'mixed regions'}."
            )
            watch.append({
                "indicator_kind": "narrative",
                "evidence_kind": "inferred",
                "assessment_kind": "narrative_spread",
                "narrative_id": narrative.narrative_id,
                "topic": topic,
                "signal_type": narrative.signal_type,
                "severity": narrative.severity,
                "strength": round(float(narrative.strength or 0.0), 4),
                "confidence": round(float(narrative.confidence or 0.0), 4),
                "event_count": int(narrative.event_count or len(recent_matches)),
                "source_count": int(narrative.source_count or 0),
                "actor_count": len(narrative.actors or []),
                "actors": list(narrative.actors or [])[:5],
                "top_region": top_region,
                "geographic_spread": geo_spread,
                "momentum": momentum,
                "trajectory": momentum,
                "trajectory_code": momentum,
                "driver": driver,
                "driver_code": _driver_code(driver),
                "unrest_score": unrest_score,
                "protest_signal": round(protest_signal, 4),
                "recommended_action": _recommended_action(unrest_score, "watch narrative"),
                "recommended_action_code": _recommended_action_code(unrest_score, "watch_narrative"),
                "observation_summary": f"{narrative.event_count or len(recent_matches)} linked event(s) were observed across {narrative.source_count or 0} source(s).",
                "assessment_summary": f"Narrative strength is {float(narrative.strength or 0.0):.2f}, confidence is {float(narrative.confidence or 0.0):.2f}, and momentum is {momentum}.",
                "correlation_summary": f"{len(narrative.actors or [])} actor(s) and {len(geo_spread)} region(s) are connected to this narrative cluster.",
                "watch_reason": reason,
            })

        watch.sort(key=lambda item: (item["unrest_score"], item["strength"]), reverse=True)
        return watch[:20]

    def _build_actor_watch(
        self,
        actors: Sequence[OntologyActorModel],
        recent_events: Sequence[_EventShape],
        narratives: Sequence[NarrativeModel],
        alerts: Sequence[AlertModel],
    ) -> List[Dict[str, Any]]:
        event_actor_index = defaultdict(list)
        for event in recent_events:
            for name in event.actor_names:
                event_actor_index[name.lower()].append(event)

        narrative_actor_index = defaultdict(list)
        for narrative in narratives:
            for actor in narrative.actors or []:
                narrative_actor_index[str(actor).strip().lower()].append(narrative)

        alert_entity_index = defaultdict(list)
        for alert in alerts:
            if alert.entity:
                alert_entity_index[alert.entity.strip().lower()].append(alert)

        watch: List[Dict[str, Any]] = []
        for actor in actors[:40]:
            name = (actor.canonical_name or "").strip()
            if not name:
                continue
            matched_events = event_actor_index.get(name.lower(), [])
            matched_narratives = narrative_actor_index.get(name.lower(), [])
            matched_alerts = alert_entity_index.get(name.lower(), [])
            sentiments = [event.sentiment_score for event in matched_events if event.sentiment_score is not None]
            regions = Counter(event.region for event in matched_events if event.region)
            avg_risk = mean([event.risk_score for event in matched_events]) if matched_events else 0.0
            avg_sentiment = mean(sentiments) if sentiments else 0.5
            protest_signal = self._keyword_pressure(event.text for event in matched_events)

            unrest_score = _clip(
                float(actor.influence_score or 0.0) * 0.35
                + min(len(matched_events) / 8.0, 1.0) * 0.2
                + min(len(matched_narratives) / 4.0, 1.0) * 0.15
                + min(len(matched_alerts) / 3.0, 1.0) * 0.1
                + avg_risk * 0.1
                + (1.0 - avg_sentiment) * 0.05
                + protest_signal * 0.05
            )
            trajectory = (
                "rising" if len(matched_events) >= 4 or len(matched_narratives) >= 2 else
                "stable"
            )
            driver = (
                "actor amplification" if float(actor.influence_score or 0.0) >= 0.55 else
                "event recurrence" if len(matched_events) >= 3 else
                "narrative attachment" if len(matched_narratives) >= 2 else
                "emerging watch"
            )

            if unrest_score < 0.2 and not matched_events and not matched_narratives:
                continue

            reason = (
                f"{len(matched_events)} recent event(s), {len(matched_narratives)} linked narratives, "
                f"and influence score {float(actor.influence_score or 0.0):.2f}."
            )
            watch.append({
                "indicator_kind": "actor",
                "evidence_kind": "correlated",
                "assessment_kind": "actor_influence",
                "actor_id": actor.actor_id,
                "name": name,
                "type": actor.entity_type,
                "mention_count": int(actor.mention_count or 0),
                "influence_score": round(float(actor.influence_score or 0.0), 4),
                "event_count": len(matched_events),
                "narrative_count": len(matched_narratives),
                "alert_count": len(matched_alerts),
                "avg_risk": round(avg_risk, 4),
                "avg_sentiment": round(avg_sentiment, 4),
                "primary_regions": _top_list(regions, 3),
                "driver": driver,
                "trajectory": trajectory,
                "trajectory_code": trajectory,
                "driver_code": _driver_code(driver),
                "unrest_score": unrest_score,
                "recommended_action": _recommended_action(unrest_score, "review actor"),
                "recommended_action_code": _recommended_action_code(unrest_score, "review_actor"),
                "observation_summary": f"{len(matched_events)} recent event(s) and {len(matched_alerts)} alert(s) mention or implicate {name}.",
                "assessment_summary": f"Influence score is {float(actor.influence_score or 0.0):.2f} with average linked-event risk {avg_risk:.2f}.",
                "correlation_summary": f"{len(matched_narratives)} narrative(s) and {len(regions)} primary region(s) are tied to this actor watch item.",
                "watch_reason": reason,
            })

        watch.sort(key=lambda item: item["unrest_score"], reverse=True)
        return watch[:15]

    def _build_alert_watch(
        self,
        alerts: Sequence[AlertModel],
        narratives: Sequence[NarrativeModel],
        recent_events: Sequence[_EventShape],
    ) -> List[Dict[str, Any]]:
        watch: List[Dict[str, Any]] = []
        for alert in alerts:
            linked_topics = self._match_alert_narratives(alert, narratives)
            source_count = len(alert.sources or [])
            corroboration = _clip(
                min(source_count / 4.0, 1.0) * 0.35
                + min(float(alert.event_count or 0) / 6.0, 1.0) * 0.25
                + min(float(alert.z_score or 0.0) / 5.0, 1.0) * 0.25
                + min(len(linked_topics) / 3.0, 1.0) * 0.15
            )
            severity = _severity_weight(alert.severity)
            unrest_score = _clip(corroboration * 0.6 + severity * 0.4)
            linked_region = alert.location or self._derive_alert_region(alert, recent_events)
            driver = {
                "sentiment_shift": "sentiment deterioration",
                "sentiment_deterioration": "sentiment deterioration",
                "geo_cluster": "geographic clustering",
                "geographic_cluster": "geographic clustering",
                "entity_spike": "actor convergence",
                "source_silence": "source silence",
                "coordinated_amplification": "coordinated amplification",
                "escalation_risk": "escalation pressure",
            }.get((alert.alert_type or "").lower(), "corroborated anomaly")
            reason = (
                f"{source_count} source(s), z-score {float(alert.z_score or 0.0):.1f}, "
                f"and {len(linked_topics)} linked narrative(s)."
            )
            watch.append({
                "indicator_kind": "alert",
                "evidence_kind": "correlated",
                "assessment_kind": "corroborated_alert",
                "alert_id": alert.alert_id,
                "title": alert.title,
                "severity": alert.severity,
                "alert_type": alert.alert_type,
                "event_count": int(alert.event_count or 0),
                "source_count": source_count,
                "corroboration_score": corroboration,
                "unrest_score": unrest_score,
                "linked_region": linked_region,
                "linked_narratives": linked_topics,
                "driver": driver,
                "trajectory": "rising" if corroboration >= 0.55 else "stable",
                "trajectory_code": "rising" if corroboration >= 0.55 else "stable",
                "driver_code": _driver_code(driver),
                "recommended_action": _recommended_action(unrest_score, "review"),
                "recommended_action_code": _recommended_action_code(unrest_score, "review"),
                "observation_summary": f"{source_count} source(s) observed the underlying anomaly with {int(alert.event_count or 0)} linked event(s).",
                "assessment_summary": f"Alert severity is {(alert.severity or 'medium').lower()} with corroboration score {corroboration:.2f}.",
                "correlation_summary": f"{len(linked_topics)} narrative(s) and region {linked_region or 'unknown'} align with this alert.",
                "watch_reason": reason,
            })

        watch.sort(key=lambda item: (item["unrest_score"], _severity_weight(item["severity"])), reverse=True)
        return watch[:15]

    def _build_overview(
        self,
        regions: Sequence[Dict[str, Any]],
        narratives: Sequence[Dict[str, Any]],
        actors: Sequence[Dict[str, Any]],
        alerts: Sequence[Dict[str, Any]],
    ) -> Dict[str, Any]:
        top_region = regions[0]["region"] if regions else None
        top_narrative = narratives[0]["topic"] if narratives else None
        pressure = mean([
            regions[0]["unrest_score"] if regions else 0.0,
            narratives[0]["unrest_score"] if narratives else 0.0,
            alerts[0]["unrest_score"] if alerts else 0.0,
        ])
        level = (
            "critical" if pressure >= 0.75 else
            "high" if pressure >= 0.55 else
            "elevated" if pressure >= 0.35 else
            "low"
        )
        return {
            "unrest_level": level,
            "overall_pressure": round(pressure, 4),
            "hot_region_count": len([item for item in regions if item["unrest_score"] >= 0.45]),
            "rising_narratives": len([item for item in narratives if item["momentum"] == "rising"]),
            "corroborated_alerts": len([item for item in alerts if item["corroboration_score"] >= 0.6]),
            "watched_actors": len([item for item in actors if item["unrest_score"] >= 0.4]),
            "top_region": top_region,
            "top_narrative": top_narrative,
            "recommended_action": (
                f"Focus on {top_region or 'the top risk region'}, validate the "
                f"{top_narrative or 'lead narrative'}, and route corroborated alerts "
                "to triage before escalation pressure spreads."
            ),
        }

    def _keyword_pressure(self, texts: Iterable[str]) -> float:
        score = 0
        for text in texts:
            lower = text.lower()
            score += sum(1 for keyword in _PROTEST_KEYWORDS if keyword in lower)
        return _clip(score / 8.0)

    def _match_topic_events(
        self,
        topic: str,
        events: Sequence[_EventShape],
        narrative: NarrativeModel,
    ) -> List[_EventShape]:
        topic_l = topic.lower()
        actor_names = {str(actor).strip().lower() for actor in (narrative.actors or []) if str(actor).strip()}
        matches: List[_EventShape] = []
        for event in events:
            text = event.text.lower()
            if topic_l in text:
                matches.append(event)
                continue
            if any(actor in {name.lower() for name in event.actor_names} for actor in actor_names):
                matches.append(event)
        return matches

    def _match_alert_narratives(
        self,
        alert: AlertModel,
        narratives: Sequence[NarrativeModel],
    ) -> List[str]:
        entity = (alert.entity or "").strip().lower()
        location = (alert.location or "").strip().lower()
        linked: List[str] = []
        for narrative in narratives:
            topic = (narrative.topic or "").strip()
            if not topic:
                continue
            topic_l = topic.lower()
            if entity and (topic_l == entity or entity in topic_l):
                linked.append(topic)
                continue
            if any(str(actor).strip().lower() == entity for actor in (narrative.actors or [])):
                linked.append(topic)
                continue
            spread = (narrative.meta_data or {}).get("geographic_spread") or {}
            if location and isinstance(spread, dict) and any(str(region).strip().lower() == location for region in spread.keys()):
                linked.append(topic)
        return linked[:4]

    def _derive_alert_region(self, alert: AlertModel, recent_events: Sequence[_EventShape]) -> Optional[str]:
        entity = (alert.entity or "").strip().lower()
        if not entity:
            return None
        regions = Counter()
        for event in recent_events:
            if entity in {name.lower() for name in event.actor_names}:
                regions[event.region] += 1
        return regions.most_common(1)[0][0] if regions else None

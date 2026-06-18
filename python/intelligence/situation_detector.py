"""
intelligence/situation_detector.py
────────────────────────────────────
Groups related events into Situation objects.

A Situation = cluster of ≥2 events that share:
  - common actors (≥1 shared actor name), OR
  - geo proximity (within 500 km)
  AND a time window (within 6 hours of each other)

Each situation gets a risk score derived from member events.
"""

from __future__ import annotations

import hashlib
import logging
import math
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("vision_i.intelligence.situation_detector")

_WINDOW_HOURS  = 6
_MIN_EVENTS    = 2
_GEO_RADIUS_KM = 250.0
_TRACKING_SOURCES = {"ais", "opensky"}
_INTEL_KEYWORDS = {
    "attack", "strike", "military", "troops", "missile", "drone", "sanction", "blockade",
    "navy", "defense", "security", "war", "conflict", "protest", "unrest", "ceasefire",
    "border", "airspace", "hormuz", "iran", "ukraine", "russia", "israel", "china", "taiwan",
    "sudan", "mali", "nato", "embassy", "coup", "terror", "weapon", "fleet", "carrier",
}
_HIGH_SIGNAL_ACTOR_TERMS = {
    "navy", "army", "military", "defense", "guard", "forces", "brigade", "missile",
    "fleet", "embassy", "ministry", "command", "revolutionary guard", "police",
    "hezbollah", "hamas", "nato", "senate", "pentagon", "white house",
}
_TOPIC_FAMILIES = {
    "conflict": {
        "attack", "strike", "military", "troops", "missile", "drone", "defense", "war",
        "conflict", "ceasefire", "border", "navy", "fleet", "carrier", "airspace",
        "bombardment", "munition", "security", "terror", "embassy", "guard",
    },
    "market": {
        "market", "inflation", "supply", "shares", "stocks", "price", "prices", "oil",
        "aluminum", "trade", "growth", "economy", "blockade", "tariff", "loans",
    },
    "rights": {
        "press freedom", "journalists", "custody", "antisemitism", "workers", "unemployment",
        "protest", "unrest", "rights", "abducting", "deaths in custody", "hate crime",
    },
    "politics": {
        "election", "government", "president", "minister", "congress", "senate",
        "administration", "policy", "politics", "commission", "briefing", "council",
    },
}
_SEMANTIC_SPLIT_MIN_EVENTS = 5
_SEMANTIC_SIMILARITY_THRESHOLD = 0.62

_EMBEDDER = None
_EMBEDDER_ATTEMPTED = False

from core.entity_normalizer import canonical_actor_key, is_significant_actor, normalize_actor_name


def _get_embedder():
    global _EMBEDDER, _EMBEDDER_ATTEMPTED
    if _EMBEDDER is not None:
        return _EMBEDDER
    if _EMBEDDER_ATTEMPTED:
        return None
    _EMBEDDER_ATTEMPTED = True
    try:
        from intelligence.embedder import EmbeddingService

        embedder = EmbeddingService()
        embedder.load()
        if embedder.available:
            _EMBEDDER = embedder
            return _EMBEDDER
    except Exception as exc:
        logger.warning("Situation detector embedder unavailable: %s", exc)
    return None


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance between two lat/lon points in km."""
    R = 6371.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2) ** 2
    return R * 2 * math.asin(math.sqrt(min(a, 1.0)))


def _is_high_signal_actor_name(name: str) -> bool:
    key = canonical_actor_key(name)
    return any(term in key for term in _HIGH_SIGNAL_ACTOR_TERMS)


def _actor_entities(event: Dict[str, Any], ignored_keys: Optional[set[str]] = None) -> List[Dict[str, str]]:
    entities: List[Dict[str, str]] = []
    seen: set[str] = set()
    ignored_keys = ignored_keys or set()
    for actor in event.get("actors") or []:
        actor_type = str(actor.get("type") or "").upper()
        if actor_type in {"LOC", "VEHICLE", "ASSET"}:
            continue
        name = normalize_actor_name(actor.get("canonical") or actor.get("name"), actor_type)
        if not name or not is_significant_actor(name, actor_type):
            continue
        key = canonical_actor_key(name)
        if key in seen or key in ignored_keys:
            continue
        seen.add(key)
        entities.append({"key": key, "name": name, "type": actor_type})
    return entities


def _actor_set(event: Dict[str, Any], ignored_keys: Optional[set[str]] = None) -> set:
    return {entity["key"] for entity in _actor_entities(event, ignored_keys)}


def _has_specific_geo(event: Dict[str, Any]) -> bool:
    loc = event.get("location") or {}
    lat, lon = loc.get("lat"), loc.get("lon")
    if lat is None or lon is None:
        return False
    if loc.get("name"):
        return True
    source = str(event.get("source") or "").lower()
    if source.startswith("gdelt"):
        return False
    country = str(loc.get("country") or (event.get("extras") or {}).get("country") or "").strip().lower()
    return bool(country and country not in {"global", "world"})


def _events_related(a: Dict, b: Dict, ignored_actor_keys: Optional[set[str]] = None) -> bool:
    """True if two events are related via actor overlap or geo proximity."""
    actors_a = _actor_set(a, ignored_actor_keys)
    actors_b = _actor_set(b, ignored_actor_keys)
    shared_actors = actors_a & actors_b
    if shared_actors and any((" " in actor or len(actor) >= 8) for actor in shared_actors):
        return True

    loc_a = a.get("location") or {}
    loc_b = b.get("location") or {}
    lat_a, lon_a = loc_a.get("lat"), loc_a.get("lon")
    lat_b, lon_b = loc_b.get("lat"), loc_b.get("lon")
    if _has_specific_geo(a) and _has_specific_geo(b) and all(v is not None for v in (lat_a, lon_a, lat_b, lon_b)):
        try:
            if _haversine_km(float(lat_a), float(lon_a), float(lat_b), float(lon_b)) <= _GEO_RADIUS_KM:
                return True
        except Exception:
            pass

    return False


def _is_caseworthy_event(event: Dict[str, Any], cutoff: datetime) -> bool:
    source = (event.get("source") or "").lower()
    event_type = (event.get("event_type") or "").lower()
    extras = event.get("extras") or {}

    if source in _TRACKING_SOURCES:
        return False

    ts_str = event.get("timestamp")
    if ts_str:
        try:
            ts = datetime.fromisoformat(str(ts_str).replace("Z", "+00:00"))
            if ts < cutoff:
                return False
            if source in {"reddit", "twitter", "youtube", "telegram"} and ts < datetime.now(timezone.utc) - timedelta(days=45):
                return False
        except Exception:
            pass

    if extras.get("trigger_type") == "auto_social" and source in {"reddit", "twitter", "youtube", "telegram"}:
        trigger_event_id = str(extras.get("trigger_event_id") or "")
        if trigger_event_id.startswith("ais:") or trigger_event_id.startswith("opensky:"):
            return False

    return True


def _situation_id(event_ids: List[str]) -> str:
    """Deterministic SHA-1 ID from sorted event IDs."""
    key = ",".join(sorted(event_ids))
    return "sit_" + hashlib.sha1(key.encode()).hexdigest()[:12]


def _generate_title(events: List[Dict], ignored_actor_keys: Optional[set[str]] = None) -> str:
    actor_counts: Dict[str, int] = {}
    actor_display: Dict[str, str] = {}
    regions: Dict[str, int] = {}
    for ev in events:
        for entity in _actor_entities(ev, ignored_actor_keys):
            actor_counts[entity["key"]] = actor_counts.get(entity["key"], 0) + 1
            actor_display.setdefault(entity["key"], entity["name"])
        loc = ev.get("location") or {}
        region = loc.get("country") or loc.get("name")
        if region:
            regions[str(region)] = regions.get(str(region), 0) + 1

    top_region = None
    if regions:
        top_region = sorted(regions.items(), key=lambda item: (-item[1], item[0]))[0][0]

    top_actors = [
        actor_display[key]
        for key, _count in sorted(actor_counts.items(), key=lambda item: (-item[1], item[0]))[:2]
    ]

    if top_region and top_actors:
        return f"{top_region} | {' + '.join(top_actors)} | {len(events)} linked reports"
    if top_region:
        return f"{top_region} | {len(events)} linked reports"
    if top_actors:
        return f"{' + '.join(top_actors)} | {len(events)} linked reports"
    return f"{len(events)} linked reports"


def _cluster_intel_score(cluster_events: List[Dict[str, Any]], avg_risk: float, source_count: int, actor_ids: List[str]) -> float:
    text = " ".join(
        " ".join(
            filter(
                None,
                [
                    str(ev.get("title") or ""),
                    str(ev.get("description") or ""),
                    " ".join(str(tag) for tag in (ev.get("tags") or [])),
                ],
            )
        ).lower()
        for ev in cluster_events
    )
    keyword_hits = sum(1 for kw in _INTEL_KEYWORDS if kw in text)
    defense_sources = sum(
        1 for ev in cluster_events
        if any(token in str(ev.get("source") or "").lower() for token in ("war", "defense", "al_jazeera", "france24", "rt", "un_news", "dw_news"))
    )
    score = (
        min(avg_risk, 1.0) * 0.45 +
        min(source_count, 4) / 4.0 * 0.20 +
        min(keyword_hits, 6) / 6.0 * 0.20 +
        min(len(actor_ids), 4) / 4.0 * 0.10 +
        min(defense_sources, 3) / 3.0 * 0.05
    )
    return round(min(score, 1.0), 4)


def _cluster_keyword_hits(cluster_events: List[Dict[str, Any]]) -> int:
    text = " ".join(
        " ".join(
            filter(
                None,
                [
                    str(ev.get("title") or ""),
                    str(ev.get("description") or ""),
                    " ".join(str(tag) for tag in (ev.get("tags") or [])),
                ],
            )
        ).lower()
        for ev in cluster_events
    )
    return sum(1 for kw in _INTEL_KEYWORDS if kw in text)


def _event_keyword_hits(event: Dict[str, Any]) -> int:
    text = " ".join(
        filter(
            None,
            [
                str(event.get("title") or ""),
                str(event.get("description") or ""),
                " ".join(str(tag) for tag in (event.get("tags") or [])),
            ],
        )
    ).lower()
    return sum(1 for kw in _INTEL_KEYWORDS if kw in text)


def _event_topic_family(event: Dict[str, Any]) -> str:
    text = " ".join(
        filter(
            None,
            [
                str(event.get("title") or ""),
                str(event.get("description") or ""),
                " ".join(str(tag) for tag in (event.get("tags") or [])),
                str((event.get("location") or {}).get("name") or ""),
            ],
        )
    ).lower()
    family_scores: Dict[str, int] = {}
    for family, terms in _TOPIC_FAMILIES.items():
        family_scores[family] = sum(1 for term in terms if term in text)
    best_family = max(family_scores.items(), key=lambda item: item[1])
    if best_family[1] <= 0:
        return "general"
    return best_family[0]


def _event_signal_score(event: Dict[str, Any]) -> float:
    risk = float(event.get("risk_score") or 0.0)
    keyword_hits = _event_keyword_hits(event)
    source = str(event.get("source") or "").lower()
    defense_source = 1.0 if any(token in source for token in ("war", "defense", "al_jazeera", "france24", "rt", "un_news", "dw_news")) else 0.0
    actor_count = min(len(_actor_entities(event)), 4) / 4.0
    score = (
        min(risk, 1.0) * 0.45 +
        min(keyword_hits, 4) / 4.0 * 0.35 +
        defense_source * 0.10 +
        actor_count * 0.10
    )
    return round(min(score, 1.0), 4)


def _event_thread_text(event: Dict[str, Any], ignored_actor_keys: Optional[set[str]] = None) -> str:
    actors = [entity["name"] for entity in _actor_entities(event, ignored_actor_keys)]
    location = event.get("location") or {}
    parts = [
        str(event.get("title") or ""),
        str(event.get("description") or ""),
        " ".join(str(tag) for tag in (event.get("tags") or [])),
        " ".join(actors),
        str(location.get("name") or ""),
        str(location.get("country") or ""),
        str(event.get("source") or ""),
    ]
    return " | ".join(part for part in parts if part).strip()


def _cluster_signal_support(cluster_events: List[Dict[str, Any]]) -> tuple[int, float]:
    scores = [_event_signal_score(ev) for ev in cluster_events]
    if not scores:
        return 0, 0.0
    high_signal_count = sum(1 for score in scores if score >= 0.33)
    avg_signal = sum(scores) / len(scores)
    return high_signal_count, round(avg_signal, 4)


def _cluster_topic_profile(cluster_events: List[Dict[str, Any]]) -> tuple[str, float, Dict[str, int]]:
    counts: Dict[str, int] = {}
    for ev in cluster_events:
        family = _event_topic_family(ev)
        counts[family] = counts.get(family, 0) + 1
    if not counts or not cluster_events:
        return "general", 0.0, counts
    dominant_family, dominant_count = max(counts.items(), key=lambda item: (item[1], item[0]))
    coverage = dominant_count / float(len(cluster_events))
    return dominant_family, round(coverage, 4), counts


def _cosine_similarity(vec_a: List[float], vec_b: List[float]) -> float:
    if not vec_a or not vec_b:
        return 0.0
    return float(sum(a * b for a, b in zip(vec_a, vec_b)))


def _semantic_split_cluster(
    cluster_events: List[Dict[str, Any]],
    ignored_actor_keys: Optional[set[str]] = None,
) -> List[List[Dict[str, Any]]]:
    if len(cluster_events) < _SEMANTIC_SPLIT_MIN_EVENTS:
        return [cluster_events]

    embedder = _get_embedder()
    if not embedder or not getattr(embedder, "available", False):
        return [cluster_events]

    texts = [_event_thread_text(ev, ignored_actor_keys) for ev in cluster_events]
    if len([text for text in texts if text]) < 2:
        return [cluster_events]

    try:
        vectors = embedder.embed_texts(texts)
    except Exception as exc:
        logger.warning("Semantic split embedding failed: %s", exc)
        return [cluster_events]
    if not vectors or len(vectors) != len(cluster_events):
        return [cluster_events]

    parent = list(range(len(cluster_events)))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(x: int, y: int) -> None:
        px, py = find(x), find(y)
        if px != py:
            parent[px] = py

    for i in range(len(cluster_events)):
        for j in range(i + 1, len(cluster_events)):
            if _cosine_similarity(vectors[i], vectors[j]) >= _SEMANTIC_SIMILARITY_THRESHOLD:
                union(i, j)

    groups: Dict[int, List[Dict[str, Any]]] = {}
    for idx, ev in enumerate(cluster_events):
        groups.setdefault(find(idx), []).append(ev)

    refined = [events for events in groups.values() if len(events) >= 2]
    if len(refined) < 2:
        return [cluster_events]

    coherent: List[List[Dict[str, Any]]] = []
    leftovers: List[Dict[str, Any]] = []
    for events in refined:
        dominant_family, family_coverage, _family_counts = _cluster_topic_profile(events)
        source_count = len({(ev.get("source") or "").lower() for ev in events if ev.get("source")})
        if dominant_family == "general" or family_coverage < 0.55 or source_count < 2:
            leftovers.extend(events)
            continue
        coherent.append(events)

    if len(coherent) == 1 and len(leftovers) >= 1:
        return coherent
    if len(coherent) < 2:
        return [cluster_events]
    if len(leftovers) >= 2:
        coherent.append(leftovers)
    return coherent


def _max_actor_coverage(cluster_events: List[Dict[str, Any]]) -> float:
    actor_counts: Dict[str, int] = {}
    for ev in cluster_events:
        seen_in_event = {entity["key"] for entity in _actor_entities(ev)}
        for actor_key in seen_in_event:
            actor_counts[actor_key] = actor_counts.get(actor_key, 0) + 1
    if not actor_counts or not cluster_events:
        return 0.0
    return max(actor_counts.values()) / float(len(cluster_events))


def _dominant_country_coverage(cluster_events: List[Dict[str, Any]]) -> float:
    country_counts: Dict[str, int] = {}
    for ev in cluster_events:
        loc = ev.get("location") or {}
        country = str(loc.get("country") or "").strip()
        if not country:
            continue
        country_counts[country] = country_counts.get(country, 0) + 1
    if not country_counts or not cluster_events:
        return 0.0
    return max(country_counts.values()) / float(len(cluster_events))


def _cluster_actor_counts(
    cluster_events: List[Dict[str, Any]],
    ignored_actor_keys: Optional[set[str]] = None,
) -> tuple[Dict[str, int], Dict[str, str]]:
    counts: Dict[str, int] = {}
    display: Dict[str, str] = {}
    for ev in cluster_events:
        seen = set()
        for entity in _actor_entities(ev, ignored_actor_keys):
            if entity["key"] in seen:
                continue
            seen.add(entity["key"])
            counts[entity["key"]] = counts.get(entity["key"], 0) + 1
            display.setdefault(entity["key"], entity["name"])
    return counts, display


def _split_cluster_by_anchor_actors(
    cluster_events: List[Dict[str, Any]],
    ignored_actor_keys: Optional[set[str]] = None,
) -> List[List[Dict[str, Any]]]:
    if len(cluster_events) < 8:
        return [cluster_events]

    counts, display = _cluster_actor_counts(cluster_events, ignored_actor_keys)
    anchors = [
        key
        for key, count in sorted(counts.items(), key=lambda item: (-item[1], item[0]))
        if count >= 2 and count < len(cluster_events) and _is_high_signal_actor_name(display.get(key, ""))
    ][:4]
    if len(anchors) < 2:
        return [cluster_events]

    anchor_strength = {key: counts[key] for key in anchors}
    buckets: Dict[str, List[Dict[str, Any]]] = {key: [] for key in anchors}
    unassigned: List[Dict[str, Any]] = []

    for ev in cluster_events:
        event_actor_keys = _actor_set(ev, ignored_actor_keys)
        matched = [key for key in anchors if key in event_actor_keys]
        if not matched:
            unassigned.append(ev)
            continue
        best_key = sorted(matched, key=lambda key: (-anchor_strength[key], key))[0]
        buckets[best_key].append(ev)

    refined = [events for events in buckets.values() if len(events) >= 2]
    if len(refined) < 2:
        return [cluster_events]

    if len(unassigned) >= 2:
        refined.append(unassigned)
    return refined


def _split_cluster_by_family(cluster_events: List[Dict[str, Any]]) -> List[List[Dict[str, Any]]]:
    if len(cluster_events) < 6:
        return [cluster_events]
    dominant_family, family_coverage, family_counts = _cluster_topic_profile(cluster_events)
    if dominant_family == "general" or family_coverage >= 0.8 or len(family_counts) < 2:
        return [cluster_events]

    buckets: Dict[str, List[Dict[str, Any]]] = {}
    for ev in cluster_events:
        family = _event_topic_family(ev)
        if family == "general":
            continue
        buckets.setdefault(family, []).append(ev)

    refined = [events for family, events in buckets.items() if family != "general" and len(events) >= 2]
    if len(refined) < 2:
        return [cluster_events]
    return refined


def _refine_cluster_candidates(
    cluster_events: List[Dict[str, Any]],
    ignored_actor_keys: Optional[set[str]] = None,
) -> List[Tuple[str, List[Dict[str, Any]]]]:
    refined: List[Tuple[str, List[Dict[str, Any]]]] = []
    actor_split = _split_cluster_by_anchor_actors(cluster_events, ignored_actor_keys)
    for actor_index, actor_events in enumerate(actor_split):
        family_split = _split_cluster_by_family(actor_events)
        for family_index, family_events in enumerate(family_split):
            semantic_split = _semantic_split_cluster(family_events, ignored_actor_keys)
            if len(semantic_split) == 1:
                refined.append((f"a{actor_index}-f{family_index}", semantic_split[0]))
                continue
            for semantic_index, semantic_events in enumerate(semantic_split):
                refined.append((f"a{actor_index}-f{family_index}-s{semantic_index}", semantic_events))
    return refined or [("a0", cluster_events)]


def _should_skip_weak_cluster(
    cluster_events: List[Dict[str, Any]],
    *,
    avg_risk: float,
    source_count: int,
    actor_ids: List[str],
    keyword_hits: int,
    intel_score: float,
) -> bool:
    if intel_score < 0.18:
        return True
    if keyword_hits == 0 and avg_risk < 0.10 and source_count <= 2 and len(actor_ids) <= 1:
        return True

    gdelt_like = sum(1 for ev in cluster_events if str(ev.get("source") or "").lower().startswith("gdelt"))
    market_like = sum(
        1
        for ev in cluster_events
        if "market" in str(ev.get("event_type") or "").lower()
        or "finance" in str(ev.get("source") or "").lower()
    )
    max_actor_coverage = _max_actor_coverage(cluster_events)
    country_coverage = _dominant_country_coverage(cluster_events)
    high_signal_count, avg_signal = _cluster_signal_support(cluster_events)
    dominant_family, family_coverage, family_counts = _cluster_topic_profile(cluster_events)
    if (
        avg_risk < 0.12
        and intel_score < 0.30
        and source_count <= 2
        and len(actor_ids) <= 1
        and keyword_hits <= 1
        and (gdelt_like + market_like) >= max(len(cluster_events) - 1, 1)
    ):
        return True
    if (
        avg_risk < 0.10
        and intel_score < 0.34
        and source_count <= 2
        and len(actor_ids) <= 1
        and max_actor_coverage < 0.5
        and gdelt_like >= max(len(cluster_events) - 2, 1)
    ):
        return True
    if (
        avg_risk < 0.08
        and intel_score < 0.28
        and source_count <= 2
        and len(actor_ids) == 1
        and gdelt_like >= max(len(cluster_events) - 1, 1)
    ):
        return True
    if len(actor_ids) == 0 and source_count <= 2 and avg_risk < 0.18 and country_coverage < 0.75:
        return True
    if high_signal_count < 2 and avg_signal < 0.30 and avg_risk < 0.18:
        return True
    if gdelt_like >= max(len(cluster_events) // 2, 1) and high_signal_count < 2 and avg_signal < 0.36:
        return True
    if dominant_family == "general" and family_coverage < 0.75 and avg_risk < 0.25:
        return True
    if family_coverage < 0.6 and avg_risk < 0.28 and source_count <= 5:
        return True
    if (
        len(family_counts) >= 3
        and family_coverage < 0.7
        and high_signal_count < 3
        and avg_risk < 0.30
    ):
        return True
    return False


def detect_situations(
    events: List[Dict[str, Any]],
    window_hours: int = _WINDOW_HOURS,
    min_events: int = _MIN_EVENTS,
) -> List[Dict[str, Any]]:
    """
    Cluster events into situations using greedy union-find.

    Returns list of situation dicts:
      situation_id, title, description, event_ids, actor_ids,
      risk_score, severity, region, event_count, detected_at, status, meta
    """
    if not events or len(events) < min_events:
        return []

    cutoff = datetime.now(timezone.utc) - timedelta(hours=window_hours)

    windowed: List[Dict] = [ev for ev in events if _is_caseworthy_event(ev, cutoff)]

    if len(windowed) < min_events:
        return []

    actor_freq: Dict[str, int] = {}
    actor_name_lookup: Dict[str, str] = {}
    for ev in windowed:
        for entity in _actor_entities(ev):
            actor_freq[entity["key"]] = actor_freq.get(entity["key"], 0) + 1
            actor_name_lookup.setdefault(entity["key"], entity["name"])
    ignored_actor_keys = {
        key
        for key, count in actor_freq.items()
        if count >= 4 and not _is_high_signal_actor_name(actor_name_lookup.get(key, ""))
    }

    # Union-Find
    n = len(windowed)
    parent = list(range(n))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(x: int, y: int) -> None:
        px, py = find(x), find(y)
        if px != py:
            parent[px] = py

    for i in range(n):
        for j in range(i + 1, n):
            if _events_related(windowed[i], windowed[j], ignored_actor_keys):
                union(i, j)

    # Group by root
    clusters: Dict[int, List[int]] = {}
    for i in range(n):
        root = find(i)
        clusters.setdefault(root, []).append(i)

    from intelligence.risk_engine import compute_risk_score, severity_from_score

    situations: List[Dict] = []
    now_iso = datetime.now(timezone.utc).isoformat()

    for indices in clusters.values():
        if len(indices) < min_events:
            continue

        cluster_events = [windowed[i] for i in indices]
        parent_event_ids = [ev.get("event_id", "") for ev in cluster_events if ev.get("event_id")]
        parent_situation_id = _situation_id(parent_event_ids)
        for split_label, candidate_events in _refine_cluster_candidates(cluster_events, ignored_actor_keys):
            event_ids = [ev.get("event_id", "") for ev in candidate_events if ev.get("event_id")]

            actor_set: set = set()
            actor_display: Dict[str, str] = {}
            for ev in candidate_events:
                for entity in _actor_entities(ev, ignored_actor_keys):
                    actor_set.add(entity["key"])
                    actor_display.setdefault(entity["key"], entity["name"])
            actor_ids = [actor_display[key] for key in sorted(actor_set)]

            risk_scores = [
                float(ev.get("risk_score") or compute_risk_score(ev))
                for ev in candidate_events
            ]
            source_count = len({(ev.get("source") or "").lower() for ev in candidate_events if ev.get("source")})
            avg_risk = round(sum(risk_scores) / len(risk_scores), 4)
            intel_score = _cluster_intel_score(candidate_events, avg_risk, source_count, actor_ids)
            keyword_hits = _cluster_keyword_hits(candidate_events)
            dominant_family, family_coverage, family_counts = _cluster_topic_profile(candidate_events)

            # Quality gates: cases should be investigable, not giant omnibus clusters
            # or single-source RSS piles.
            if source_count < 2:
                continue
            if len(candidate_events) > 80:
                continue
            if len(actor_ids) > 120:
                continue
            if avg_risk < 0.15 and len(candidate_events) > 25:
                continue
            if _should_skip_weak_cluster(
                candidate_events,
                avg_risk=avg_risk,
                source_count=source_count,
                actor_ids=actor_ids,
                keyword_hits=keyword_hits,
                intel_score=intel_score,
            ):
                continue

            situation_risk = round(min(max(risk_scores) * (1.05 + min(source_count, 4) * 0.05), 1.0), 4)
            severity = severity_from_score(situation_risk)

            locations = [
                (ev.get("location") or {}).get("name", "")
                for ev in candidate_events
                if (ev.get("location") or {}).get("name")
            ]
            region = max(set(locations), key=locations.count) if locations else "GLOBAL"

            situations.append({
                "situation_id": _situation_id(event_ids),
                "title":        _generate_title(candidate_events, ignored_actor_keys),
                "description":  (
                    f"Correlated intelligence situation — "
                    f"{len(candidate_events)} events, {len(actor_ids)} actors"
                ),
                "event_ids":    event_ids,
                "actor_ids":    actor_ids,
                "risk_score":   situation_risk,
                "severity":     severity,
                "region":       region,
                "event_count":  len(candidate_events),
                "detected_at":  now_iso,
                "updated_at":   now_iso,
                "status":       "active",
                "meta": {
                    "parent_situation_id": parent_situation_id if len(candidate_events) != len(cluster_events) else None,
                    "subcase_id": f"{parent_situation_id}:{split_label}" if len(candidate_events) != len(cluster_events) else None,
                    "window_hours": window_hours,
                    "source_count": source_count,
                    "avg_risk": avg_risk,
                    "intel_score": intel_score,
                    "keyword_hits": keyword_hits,
                    "topic_family": dominant_family,
                    "topic_family_coverage": family_coverage,
                    "topic_family_counts": family_counts,
                    "source_mix": sorted({(ev.get("source") or "").lower() for ev in candidate_events if ev.get("source")}),
                },
            })

    # Sort by risk descending
    situations.sort(key=lambda s: -s["risk_score"])
    logger.info("Situation detector: %d situations from %d events", len(situations), len(windowed))
    return situations

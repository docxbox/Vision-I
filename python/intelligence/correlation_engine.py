"""
intelligence/correlation_engine.py
────────────────────────────────────
Signal correlation engine — the core of the intelligence layer.

Correlates signals across three dimensions:
  1. Entity overlap   — shared actors (Jaccard similarity)
  2. Semantic similarity — cosine distance via pgvector embeddings
  3. Temporal proximity — time-distance decay within a window

Correlated signals are grouped into SignalClusters using Union-Find.
Clusters feed into the CompositeEventDetector to produce high-confidence
intelligence events.

Algorithm outline:
  fetch unclustered signals (last N hours)
  → entity overlap pass (build actor → signal index, Jaccard pairs)
  → semantic similarity pass (pgvector nearest-neighbour for candidates)
  → temporal scoring (time-distance decay)
  → composite pair score = 0.35*entity + 0.40*semantic + 0.25*temporal
  → union-find clustering (merge pairs with score > 0.5)
  → return SignalCluster objects
"""

import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set, Tuple

from config.settings import settings

logger = logging.getLogger("vision_i.intelligence.correlation")


@dataclass
class SignalCluster:
    """A group of correlated signals."""
    cluster_id: str
    signal_ids: List[str]
    signals: List[Dict[str, Any]]
    scores: Dict[str, float]  # entity_overlap, semantic, temporal, composite
    shared_actors: List[str]
    sources: List[str]
    representative_signal: Dict[str, Any]
    time_span_hours: float = 0.0


class _UnionFind:
    """Union-Find (disjoint set) for clustering."""

    def __init__(self, n: int) -> None:
        self.parent = list(range(n))
        self.rank = [0] * n

    def find(self, x: int) -> int:
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, a: int, b: int) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra == rb:
            return
        if self.rank[ra] < self.rank[rb]:
            ra, rb = rb, ra
        self.parent[rb] = ra
        if self.rank[ra] == self.rank[rb]:
            self.rank[ra] += 1

    def clusters(self) -> Dict[int, List[int]]:
        groups: Dict[int, List[int]] = {}
        for i in range(len(self.parent)):
            root = self.find(i)
            groups.setdefault(root, []).append(i)
        return groups


def _parse_ts(ts: Any) -> Optional[datetime]:
    """Parse a timestamp string or datetime object to a UTC datetime."""
    if isinstance(ts, datetime):
        if ts.tzinfo is None:
            return ts.replace(tzinfo=timezone.utc)
        return ts
    if isinstance(ts, str):
        try:
            from dateutil.parser import parse as dateparse
            dt = dateparse(ts)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except Exception:
            return None
    return None


def _jaccard(a: Set[str], b: Set[str]) -> float:
    """Jaccard similarity between two sets."""
    if not a or not b:
        return 0.0
    intersection = len(a & b)
    union = len(a | b)
    return intersection / union if union > 0 else 0.0


class CorrelationEngine:
    """
    Correlates signals using entity overlap, semantic similarity,
    and temporal proximity. Returns SignalCluster objects.
    """

    def __init__(self) -> None:
        self._window_hours = settings.correlation_time_window_hours
        self._sim_threshold = settings.correlation_similarity_threshold
        self._pair_threshold = 0.5  # minimum composite pair score to merge

    async def correlate(self, window_hours: Optional[int] = None) -> List[SignalCluster]:
        """
        Run correlation on unclustered signals in the time window.
        Returns a list of SignalCluster objects.
        """
        from storage.signal_repo import SignalRepository

        window = window_hours or self._window_hours
        repo = SignalRepository()

        # Step 1: Fetch unclustered signals
        signals = await repo.find_unclustered(window_hours=window)
        if len(signals) < 2:
            logger.info("Correlation: too few unclustered signals (%d) — skipping", len(signals))
            return []

        logger.info("Correlation: processing %d unclustered signals", len(signals))

        n = len(signals)

        # Build actor sets per signal
        actor_sets: List[Set[str]] = []
        for sig in signals:
            actors = sig.get("actors") or []
            actor_set = set()
            for a in actors:
                if isinstance(a, str) and a.strip():
                    actor_set.add(a.strip().lower())
                elif isinstance(a, dict):
                    name = a.get("name", "").strip().lower()
                    if name:
                        actor_set.add(name)
            actor_sets.append(actor_set)

        # Parse timestamps
        timestamps: List[Optional[datetime]] = [_parse_ts(sig.get("timestamp")) for sig in signals]

        # Step 2: Entity overlap pass — build candidate pairs
        # Build inverted index: actor_name → [signal indices]
        actor_index: Dict[str, List[int]] = {}
        for i, actors in enumerate(actor_sets):
            for actor in actors:
                actor_index.setdefault(actor, []).append(i)

        # Generate candidate pairs from shared actors
        candidate_pairs: Set[Tuple[int, int]] = set()
        for indices in actor_index.values():
            if len(indices) < 2:
                continue
            for x in range(len(indices)):
                for y in range(x + 1, len(indices)):
                    i, j = indices[x], indices[y]
                    pair = (min(i, j), max(i, j))
                    candidate_pairs.add(pair)

        # Step 3: Compute scores for all candidate pairs
        pair_scores: List[Tuple[int, int, float, float, float, float]] = []

        for i, j in candidate_pairs:
            # Entity overlap (Jaccard)
            entity_score = _jaccard(actor_sets[i], actor_sets[j])
            if entity_score < 0.1:
                continue

            # Semantic similarity — use pgvector embeddings
            emb_i = signals[i].get("embedding")
            emb_j = signals[j].get("embedding")
            semantic_score = 0.0
            if emb_i is not None and emb_j is not None:
                # Cosine similarity from normalised embeddings = dot product
                try:
                    if isinstance(emb_i, str):
                        import json
                        emb_i = json.loads(emb_i)
                    if isinstance(emb_j, str):
                        import json
                        emb_j = json.loads(emb_j)
                    dot = sum(a * b for a, b in zip(emb_i, emb_j))
                    semantic_score = max(0.0, min(1.0, dot))
                except Exception:
                    semantic_score = 0.0

            # Temporal proximity
            temporal_score = 0.0
            ts_i, ts_j = timestamps[i], timestamps[j]
            if ts_i and ts_j:
                delta_hours = abs((ts_i - ts_j).total_seconds()) / 3600
                temporal_score = max(0.0, 1.0 - (delta_hours / self._window_hours))

            # Composite score
            composite = (0.35 * entity_score) + (0.40 * semantic_score) + (0.25 * temporal_score)

            if composite >= self._pair_threshold:
                pair_scores.append((i, j, entity_score, semantic_score, temporal_score, composite))

        logger.info(
            "Correlation: %d candidate pairs, %d above threshold",
            len(candidate_pairs), len(pair_scores),
        )

        if not pair_scores:
            return []

        # Step 4: Union-Find clustering
        uf = _UnionFind(n)
        for i, j, *_ in pair_scores:
            uf.union(i, j)

        # Step 5: Build SignalCluster objects
        groups = uf.clusters()
        clusters: List[SignalCluster] = []

        for root, members in groups.items():
            if len(members) < 2:
                continue

            member_signals = [signals[m] for m in members]
            member_ids = [signals[m]["signal_id"] for m in members]

            # Compute cluster-level scores (average of pair scores within cluster)
            member_set = set(members)
            cluster_entity_scores = []
            cluster_semantic_scores = []
            cluster_temporal_scores = []
            cluster_composite_scores = []
            for i, j, es, ss, ts_score, comp in pair_scores:
                if i in member_set and j in member_set:
                    cluster_entity_scores.append(es)
                    cluster_semantic_scores.append(ss)
                    cluster_temporal_scores.append(ts_score)
                    cluster_composite_scores.append(comp)

            avg = lambda lst: sum(lst) / len(lst) if lst else 0.0

            # Shared actors (intersection of all member actor sets)
            shared = actor_sets[members[0]].copy()
            for m in members[1:]:
                shared &= actor_sets[m]

            # Distinct sources
            sources = list(set(signals[m].get("source", "unknown") for m in members))

            # Representative = highest confidence signal
            rep = max(member_signals, key=lambda s: s.get("confidence", 0))

            # Time span
            valid_ts = [timestamps[m] for m in members if timestamps[m]]
            time_span = 0.0
            if len(valid_ts) >= 2:
                time_span = (max(valid_ts) - min(valid_ts)).total_seconds() / 3600

            cluster_id = f"clust:{uuid.uuid4().hex[:12]}"

            clusters.append(SignalCluster(
                cluster_id=cluster_id,
                signal_ids=member_ids,
                signals=member_signals,
                scores={
                    "entity_overlap": round(avg(cluster_entity_scores), 3),
                    "semantic":       round(avg(cluster_semantic_scores), 3),
                    "temporal":       round(avg(cluster_temporal_scores), 3),
                    "composite":      round(avg(cluster_composite_scores), 3),
                },
                shared_actors=sorted(shared),
                sources=sorted(sources),
                representative_signal=rep,
                time_span_hours=round(time_span, 2),
            ))

        # Step 6: Persist cluster assignments
        for cluster in clusters:
            try:
                await repo.update_cluster(cluster.signal_ids, cluster.cluster_id)
            except Exception as exc:
                logger.warning("Failed to update cluster %s: %s", cluster.cluster_id, exc)

        logger.info(
            "Correlation complete: %d clusters from %d signals (avg size %.1f)",
            len(clusters), n,
            sum(len(c.signal_ids) for c in clusters) / max(len(clusters), 1),
        )
        return clusters

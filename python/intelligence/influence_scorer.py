"""
intelligence/influence_scorer.py
──────────────────────────────────
Computes actor influence scores and enriches the Neo4j knowledge graph
with influence relationships.

Influence model:
  - Base score: mention_count (raw frequency in news)
  - Amplification: co_mention count with other high-influence actors
  - Reach: number of distinct event sources that mention the actor
  - Centrality: betweenness proxy from CO_MENTIONED_WITH graph

The scorer runs periodically via APScheduler and writes results back to Neo4j:
  - Adds `influence_score` property to Actor nodes
  - Creates `INFLUENCES` directed edges between actors who co-occur with
    significant temporal overlap

Usage:
    scorer = InfluenceScorer(session, graph)
    await scorer.update_scores(top_k=500)
"""

import logging
import math
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from storage.database import EventModel

logger = logging.getLogger("vision_i.intelligence.influence_scorer")


class InfluenceScorer:
    """
    Computes and persists actor influence scores in Neo4j.

    A score of 1.0 represents the most-mentioned actor in the dataset.
    Scores are normalised relative to the top actor, so they are comparable
    across runs even as the total event count grows.
    """

    def __init__(self, session: AsyncSession, graph=None) -> None:
        self._session = session
        self._graph   = graph

    async def update_scores(
        self,
        top_k:          int = 500,
        window_days:    int = 30,
        min_mentions:   int = 3,
    ) -> Dict[str, float]:
        """
        Compute influence scores and write them to Neo4j.

        Returns {actor_name: score} dict for the top_k actors.
        """
        if not self._graph or not self._graph.available:
            logger.info("InfluenceScorer: Neo4j not available — skipping")
            return {}

        try:
            scores = await self._compute_scores(window_days, min_mentions, top_k)
            if scores:
                self._write_scores_to_graph(scores)
                self._write_influence_edges(scores)
                # Augment with Neo4j GDS PageRank if the GDS plugin is installed.
                pagerank_scores = self._run_gds_pagerank(top_k=top_k)
                if pagerank_scores:
                    blended = self._blend_pagerank(scores, pagerank_scores)
                    self._write_scores_to_graph(blended)
                    scores = blended
                logger.info("InfluenceScorer: updated %d actor scores", len(scores))
            return scores

        except Exception as exc:
            logger.error("InfluenceScorer.update_scores() failed: %s", exc)
            return {}

    async def compute_scores_only(
        self,
        top_k:        int = 200,
        window_days:  int = 30,
        min_mentions: int = 3,
    ) -> Dict[str, float]:
        """
        Compute influence scores without touching Neo4j.
        Useful when Neo4j is unavailable but the API still needs rankings.
        """
        try:
            return await self._compute_scores(window_days, min_mentions, top_k)
        except Exception as exc:
            logger.error("InfluenceScorer.compute_scores_only() failed: %s", exc)
            return {}

    async def _compute_scores(
        self,
        window_days:  int,
        min_mentions: int,
        top_k:        int,
    ) -> Dict[str, float]:
        """
        Multi-factor influence score:
          score = (mention_weight * 0.4) + (reach_weight * 0.3) + (co_mention_weight * 0.3)
        """
        since = datetime.now(timezone.utc) - timedelta(days=window_days)

        rows = (
            await self._session.execute(
                select(EventModel.actors, EventModel.source, EventModel.timestamp)
                .where(EventModel.timestamp >= since)
            )
        ).fetchall()

        mention_counts: Dict[str, int]      = defaultdict(int)
        source_sets:    Dict[str, set]      = defaultdict(set)
        co_mentions:    Dict[str, Counter]  = defaultdict(lambda: defaultdict(int))

        from collections import Counter

        for row in rows:
            actors = row.actors or []
            names = [
                (a.get("name") or "").strip()
                for a in actors
                if (a.get("name") or "").strip()
            ]
            for name in names:
                mention_counts[name] += 1
                source_sets[name].add(row.source or "unknown")

            # Co-mention tracking
            for i in range(len(names)):
                for j in range(i + 1, len(names)):
                    co_mentions[names[i]][names[j]] += 1
                    co_mentions[names[j]][names[i]] += 1

        if not mention_counts:
            return {}

        # Filter by min_mentions
        candidates = {
            name: cnt
            for name, cnt in mention_counts.items()
            if cnt >= min_mentions and len(name) > 2
        }

        if not candidates:
            return {}

        max_mentions = max(candidates.values())
        max_reach    = max(len(source_sets[n]) for n in candidates)
        max_co       = max(
            sum(co_mentions[n].values()) for n in candidates
        ) or 1

        scores: Dict[str, float] = {}
        for name, mentions in candidates.items():
            mention_w   = mentions / max_mentions
            reach_w     = len(source_sets[name]) / max(max_reach, 1)
            co_count    = sum(co_mentions[name].values())
            co_w        = co_count / max_co

            raw_score   = (mention_w * 0.40) + (reach_w * 0.30) + (co_w * 0.30)
            scores[name] = round(raw_score, 4)

        # Return top_k by score
        top = dict(sorted(scores.items(), key=lambda x: x[1], reverse=True)[:top_k])
        return top

    def _write_scores_to_graph(self, scores: Dict[str, float]) -> None:
        """Write influence_score property onto existing Actor nodes in Neo4j."""
        if not self._graph or not self._graph.available:
            return

        actor_updates = [
            {"id": f"actor:{name.lower().replace(' ', '_')}", "score": score}
            for name, score in scores.items()
        ]

        try:
            with self._graph._driver.session() as s:
                s.run("""
                    UNWIND $actors AS a
                    MATCH (n:Actor {id: a.id})
                    SET n.influence_score = a.score,
                        n.score_updated_at = datetime()
                """, actors=actor_updates)
            logger.debug("Wrote influence scores for %d actors", len(actor_updates))
        except Exception as exc:
            logger.error("Failed to write influence scores to Neo4j: %s", exc)

    def _write_influence_edges(self, scores: Dict[str, float]) -> None:
        """
        Create/update INFLUENCES directed edges between actors.
        An actor A INFLUENCES actor B if:
          - A has significantly higher influence score than B
          - They are frequently CO_MENTIONED together
        """
        if not self._graph or not self._graph.available:
            return

        try:
            with self._graph._driver.session() as s:
                # Get top co-mentioned pairs from existing graph
                result = s.run("""
                    MATCH (a:Actor)-[r:CO_MENTIONED_WITH]-(b:Actor)
                    WHERE r.count >= 3
                      AND a.influence_score IS NOT NULL
                      AND b.influence_score IS NOT NULL
                      AND a.influence_score > b.influence_score
                    RETURN a.id AS aid, b.id AS bid,
                           a.influence_score AS a_score,
                           b.influence_score AS b_score,
                           r.count AS co_count
                    ORDER BY r.count DESC
                    LIMIT 1000
                """).data()

                influence_edges = []
                for rec in result:
                    score_diff = rec["a_score"] - rec["b_score"]
                    if score_diff < 0.05:
                        continue
                    strength = min(score_diff * rec["co_count"] / 10.0, 1.0)
                    influence_edges.append({
                        "aid":      rec["aid"],
                        "bid":      rec["bid"],
                        "strength": round(strength, 4),
                    })

                if influence_edges:
                    s.run("""
                        UNWIND $edges AS e
                        MATCH (a:Actor {id: e.aid}), (b:Actor {id: e.bid})
                        MERGE (a)-[r:INFLUENCES]->(b)
                        SET r.strength = e.strength,
                            r.updated_at = datetime()
                    """, edges=influence_edges)

                    logger.debug("Wrote %d INFLUENCES edges", len(influence_edges))

        except Exception as exc:
            logger.error("Failed to write influence edges to Neo4j: %s", exc)

    def _run_gds_pagerank(self, top_k: int = 500) -> Dict[str, float]:
        """
        Run Neo4j Graph Data Science PageRank on the CO_MENTIONED_WITH projection.
        Returns {actor_name: normalized_pagerank} or {} if GDS isn't installed.
        """
        if not self._graph or not self._graph.available:
            return {}
        try:
            with self._graph._driver.session() as s:
                # Drop existing in-memory projection if any.
                s.run("CALL gds.graph.drop('vision_actors', false) YIELD graphName").consume()
                s.run("""
                    CALL gds.graph.project(
                        'vision_actors',
                        'Actor',
                        { CO_MENTIONED_WITH: { orientation: 'UNDIRECTED', properties: 'count' } }
                    )
                """).consume()
                rows = s.run("""
                    CALL gds.pageRank.stream('vision_actors', {
                        relationshipWeightProperty: 'count',
                        maxIterations: 20,
                        dampingFactor: 0.85
                    })
                    YIELD nodeId, score
                    RETURN gds.util.asNode(nodeId).name AS name, score
                    ORDER BY score DESC
                    LIMIT $limit
                """, limit=top_k).data()
                s.run("CALL gds.graph.drop('vision_actors', false) YIELD graphName").consume()
        except Exception as exc:
            logger.info("InfluenceScorer: GDS PageRank unavailable (%s) — skipping", exc)
            return {}

        if not rows:
            return {}
        max_score = max(r["score"] for r in rows) or 1.0
        return {r["name"]: round(r["score"] / max_score, 4) for r in rows if r.get("name")}

    @staticmethod
    def _blend_pagerank(base: Dict[str, float], pagerank: Dict[str, float]) -> Dict[str, float]:
        """Blend the heuristic score with the PageRank centrality (70/30)."""
        blended: Dict[str, float] = {}
        for name, score in base.items():
            pr = pagerank.get(name, 0.0)
            blended[name] = round(score * 0.7 + pr * 0.3, 4)
        return blended

    async def get_top_influencers(
        self,
        limit:       int   = 20,
        entity_type: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """
        Returns actors ranked by influence score from Neo4j.
        Falls back to mention_count ranking if scores not yet computed.
        """
        if not self._graph or not self._graph.available:
            return []

        try:
            with self._graph._driver.session() as s:
                condition = "WHERE a.influence_score IS NOT NULL"
                if entity_type:
                    condition += f" AND a.type = '{entity_type}'"

                result = s.run(f"""
                    MATCH (a:Actor)
                    {condition}
                    RETURN a.id            AS id,
                           a.name          AS name,
                           a.type          AS type,
                           a.influence_score AS score,
                           a.mention_count AS mentions
                    ORDER BY a.influence_score DESC
                    LIMIT $limit
                """, limit=limit).data()

                return [
                    {
                        "id":       r["id"],
                        "name":     r["name"],
                        "type":     r["type"],
                        "score":    r["score"],
                        "mentions": r["mentions"] or 0,
                    }
                    for r in result
                ]
        except Exception as exc:
            logger.error("get_top_influencers failed: %s", exc)
            return []

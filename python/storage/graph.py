"""
storage/graph.py
â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
Neo4j graph writer for Vision-I.

Writes normalised events into the knowledge graph:
  - Nodes:  Actor, Event, Location, Theme
  - Edges:  PARTICIPATED_IN, OCCURRED_IN, CO_MENTIONED_WITH, ASSOCIATED_WITH

Also provides read queries used by the /entities/{id}/graph endpoint.

Design:
  - All writes use MERGE so re-running the same data is idempotent.
  - Batched writes use UNWIND for performance.
  - The driver is synchronous (neo4j official driver v5) â€” called from
    a thread pool in the async FastAPI layer.
"""

import logging
from typing import Any, Dict, List, Optional, Tuple

from config.settings import settings
from core.entity_normalizer import canonical_actor_key, normalize_events
from core.schema import VisionEvent

logger = logging.getLogger("vision_i.storage.graph")


def _slug(value: Any) -> str:
    return canonical_actor_key(str(value or "")).replace(" ", "_")


def _actor_id(name: str) -> str:
    return f"actor:{_slug(name)}"


def _location_id(
    name: Optional[str] = None,
    lat: Optional[float] = None,
    lon: Optional[float] = None,
) -> Optional[str]:
    if name and str(name).strip():
        return f"loc:{_slug(name)}"
    if lat is not None and lon is not None:
        return f"loc:geo_{float(lat):.4f}_{float(lon):.4f}"
    return None


def _theme_id(label: str) -> str:
    return f"theme:{_slug(label)}"


class GraphDB:
    """
    Thin wrapper around the Neo4j driver.
    One instance per application (singleton on app.state).

    Usage:
        graph = GraphDB()
        graph.write_events(events)
        nodes, edges = graph.ego_graph("actor:donald_trump")
    """

    def __init__(self) -> None:
        self._driver = None
        self._available = False

    def connect(self) -> bool:
        """Attempt to connect. Returns True on success."""
        try:
            from neo4j import GraphDatabase
            self._driver = GraphDatabase.driver(
                settings.neo4j_uri,
                auth=(settings.neo4j_user, settings.neo4j_pass),
            )
            self._driver.verify_connectivity()
            self._available = True
            logger.info("Neo4j connected: %s", settings.neo4j_uri)
            return True
        except Exception as exc:
            logger.warning("Neo4j unavailable (%s) â€” graph features disabled", exc)
            self._available = False
            return False

    def close(self) -> None:
        if self._driver:
            self._driver.close()
            logger.info("Neo4j connection closed")

    @property
    def available(self) -> bool:
        return self._available

    def create_indexes(self) -> None:
        """Create uniqueness constraints and indexes. Safe to call repeatedly."""
        if not self._available:
            return
        constraints = [
            "CREATE CONSTRAINT actor_id    IF NOT EXISTS FOR (a:Actor)        REQUIRE a.id IS UNIQUE",
            "CREATE CONSTRAINT event_id    IF NOT EXISTS FOR (e:Event)        REQUIRE e.id IS UNIQUE",
            "CREATE CONSTRAINT loc_id      IF NOT EXISTS FOR (l:Location)     REQUIRE l.id IS UNIQUE",
            "CREATE CONSTRAINT theme_id    IF NOT EXISTS FOR (t:Theme)        REQUIRE t.id IS UNIQUE",
            "CREATE CONSTRAINT narrative_id  IF NOT EXISTS FOR (n:Narrative)   REQUIRE n.id IS UNIQUE",
            "CREATE CONSTRAINT situation_id  IF NOT EXISTS FOR (s:Situation)   REQUIRE s.id IS UNIQUE",
            "CREATE CONSTRAINT org_id      IF NOT EXISTS FOR (o:Organization) REQUIRE o.id IS UNIQUE",
            "CREATE CONSTRAINT platform_id IF NOT EXISTS FOR (p:Platform)     REQUIRE p.id IS UNIQUE",
            "CREATE CONSTRAINT signal_id   IF NOT EXISTS FOR (s:Signal)       REQUIRE s.id IS UNIQUE",
        ]
        indexes = [
            "CREATE INDEX actor_influence IF NOT EXISTS FOR (a:Actor) ON (a.influence_score)",
            "CREATE INDEX actor_mentions  IF NOT EXISTS FOR (a:Actor) ON (a.mention_count)",
            "CREATE INDEX actor_name      IF NOT EXISTS FOR (a:Actor) ON (a.name)",
            "CREATE INDEX actor_type      IF NOT EXISTS FOR (a:Actor) ON (a.type)",
            "CREATE INDEX org_influence   IF NOT EXISTS FOR (o:Organization) ON (o.influence_score)",
            "CREATE INDEX event_time      IF NOT EXISTS FOR (e:Event) ON (e.timestamp)",
            "CREATE INDEX event_type      IF NOT EXISTS FOR (e:Event) ON (e.event_type)",
            "CREATE INDEX event_source    IF NOT EXISTS FOR (e:Event) ON (e.source)",
            "CREATE INDEX location_name   IF NOT EXISTS FOR (l:Location) ON (l.name)",
            "CREATE INDEX theme_label     IF NOT EXISTS FOR (t:Theme) ON (t.label)",
            "CREATE INDEX signal_time     IF NOT EXISTS FOR (s:Signal) ON (s.timestamp)",
            "CREATE INDEX signal_source   IF NOT EXISTS FOR (s:Signal) ON (s.source)",
        ]
        with self._driver.session() as s:
            for cypher in constraints + indexes:
                try:
                    s.run(cypher)
                except Exception as exc:
                    logger.debug("Constraint/index already exists or failed: %s", exc)
        logger.info("Neo4j indexes/constraints ensured")

    async def write_events(self, events: List[VisionEvent]) -> None:
        """
        Batch-write events to Neo4j using a thread pool.
        Idempotent â€” safe to call with the same events multiple times.
        """
        if not self._available or not events:
            return

        import asyncio
        loop = asyncio.get_running_loop()
        try:
            normalize_events(events)
            await loop.run_in_executor(None, self._write_events_sync, events)
            logger.info("Graph: wrote %d events to Neo4j", len(events))
        except Exception as exc:
            logger.error("Graph write failed: %s", exc)

    async def write_signals(self, signals: List[Dict[str, Any]]) -> None:
        """Persist signal nodes and their evidence links into Neo4j."""
        if not self._available or not signals:
            return

        import asyncio
        loop = asyncio.get_running_loop()
        try:
            await loop.run_in_executor(None, self._write_signals_sync, signals)
            logger.info("Graph: wrote %d signals to Neo4j", len(signals))
        except Exception as exc:
            logger.error("Graph signal write failed: %s", exc)

    def write_signal_clusters(self, clusters: List[Any]) -> None:
        """Persist correlation edges between signals that clustered together."""
        if not self._available or not clusters:
            return
        try:
            with self._driver.session() as s:
                s.execute_write(self._write_signal_cluster_edges, clusters)
            logger.info("Graph: wrote %d signal clusters", len(clusters))
        except Exception as exc:
            logger.error("Graph signal cluster write failed: %s", exc)

    def _write_events_sync(self, events: List[VisionEvent]) -> None:
        """Synchronous wrapper for batch-writing events."""
        with self._driver.session() as s:
            s.execute_write(self._write_event_nodes, events)
            s.execute_write(self._write_actor_nodes, events)
            s.execute_write(self._write_location_nodes, events)
            s.execute_write(self._write_theme_nodes, events)
            s.execute_write(self._write_relationships, events)

    def _write_signals_sync(self, signals: List[Dict[str, Any]]) -> None:
        with self._driver.session() as s:
            s.execute_write(self._write_signal_nodes, signals)
            s.execute_write(self._write_signal_relationships, signals)

    @staticmethod
    def _edge_evidence_mode(label: str) -> str:
        relation = (label or "").upper()
        if relation in {"MENTIONS", "LOCATED_IN", "AMPLIFIES"}:
            return "observed"
        if relation in {"DERIVED_FROM", "SUPPORTS"}:
            return "derived"
        if relation in {"CORRELATED_WITH", "CO_MENTIONED_WITH"}:
            return "correlated"
        if relation in {"IMPLICATES", "INFLUENCES", "PART_OF", "CONTAINS", "INVOLVES"}:
            return "inferred"
        return "observed"

    @staticmethod
    def _write_event_nodes(tx, events: List[VisionEvent]) -> None:
        rows = [
            {
                "id":          e["event_id"],
                "title":       e.get("title", ""),
                "source":      e.get("source", ""),
                "event_type":  e.get("event_type", "news"),
                "timestamp":   e.get("timestamp"),
                "url":         e.get("url"),
                "sentiment":   (e.get("sentiment") or {}).get("score"),
                "valid_from":  e.get("timestamp"),
            }
            for e in events
        ]
        tx.run("""
            UNWIND $rows AS row
            MERGE (e:Event {id: row.id})
            SET e.title      = row.title,
                e.source     = row.source,
                e.event_type = row.event_type,
                e.timestamp  = row.timestamp,
                e.url        = row.url,
                e.sentiment  = row.sentiment,
                e.valid_from = row.valid_from
        """, rows=rows)

    @staticmethod
    def _write_actor_nodes(tx, events: List[VisionEvent]) -> None:
        actors = []
        for e in events:
            for actor in e.get("actors") or []:
                name = (actor.get("name") or "").strip()
                if name:
                    actors.append({
                        "id":   _actor_id(name),
                        "name": name,
                        "type": actor.get("type", "UNKNOWN"),
                    })
        if not actors:
            return
        tx.run("""
            UNWIND $actors AS a
            MERGE (n:Actor {id: a.id})
            ON CREATE SET n.name = a.name, n.type = a.type, n.mention_count = 1
            ON MATCH  SET n.mention_count = n.mention_count + 1,
                          n.last_seen = datetime()
        """, actors=actors)

        # Create Organization nodes for ORG-type actors (ontology layer)
        orgs = [a for a in actors if a.get("type") == "ORG"]
        if orgs:
            tx.run("""
                UNWIND $orgs AS o
                MERGE (org:Organization {id: o.id})
                SET org.name = o.name
                WITH org, o
                MATCH (a:Actor {id: o.id})
                MERGE (a)-[:PART_OF]->(org)
            """, orgs=orgs)

    @staticmethod
    def _write_location_nodes(tx, events: List[VisionEvent]) -> None:
        locs = []
        for e in events:
            loc = e.get("location") or {}
            loc_id = _location_id(loc.get("name"), loc.get("lat"), loc.get("lon"))
            if loc_id:
                locs.append({
                    "id":   loc_id,
                    "name": loc.get("name"),
                    "lat":  loc.get("lat"),
                    "lon":  loc.get("lon"),
                })
        if not locs:
            return
        tx.run("""
            UNWIND $locs AS l
            MERGE (n:Location {id: l.id})
            SET n.name = l.name, n.lat = l.lat, n.lon = l.lon
        """, locs=locs)

    @staticmethod
    def _write_theme_nodes(tx, events: List[VisionEvent]) -> None:
        themes = []
        for e in events:
            for tag in e.get("tags") or []:
                if tag:
                    themes.append({
                        "id":    _theme_id(tag),
                        "label": tag,
                    })
        if not themes:
            return
        tx.run("""
            UNWIND $themes AS t
            MERGE (n:Theme {id: t.id})
            SET n.label = t.label
        """, themes=themes)

    @staticmethod
    def _write_relationships(tx, events: List[VisionEvent]) -> None:
        for e in events:
            eid  = e["event_id"]
            actors = e.get("actors") or []
            loc    = e.get("location") or {}

            # Event -[:MENTIONS]-> Actor
            for actor in actors:
                name = (actor.get("name") or "").strip()
                if name:
                    aid = _actor_id(name)
                    tx.run("""
                        MATCH (a:Actor {id: $aid}), (ev:Event {id: $eid})
                        MERGE (ev)-[r:MENTIONS]->(a)
                        ON CREATE SET r.valid_from = datetime($ts), r.weight = 1, r.count = 1
                        ON MATCH SET  r.count = r.count + 1
                        SET r.last_seen = datetime($ts), r.valid_to = null
                    """, aid=aid, eid=eid, ts=e.get("timestamp"))

            # Event -[:LOCATED_IN]-> Location
            lid = _location_id(loc.get("name"), loc.get("lat"), loc.get("lon"))
            if lid:
                tx.run("""
                    MATCH (ev:Event {id: $eid}), (l:Location {id: $lid})
                    MERGE (ev)-[r:LOCATED_IN]->(l)
                    ON CREATE SET r.valid_from = datetime($ts), r.weight = 1, r.count = 1
                    ON MATCH SET  r.count = r.count + 1
                    SET r.last_seen = datetime($ts), r.valid_to = null
                """, eid=eid, lid=lid, ts=e.get("timestamp"))

            # Event -[:AMPLIFIES]-> Theme
            for tag in e.get("tags") or []:
                if tag:
                    tid = _theme_id(tag)
                    tx.run("""
                        MATCH (ev:Event {id: $eid}), (t:Theme {id: $tid})
                        MERGE (ev)-[r:AMPLIFIES]->(t)
                        ON CREATE SET r.valid_from = datetime($ts), r.weight = 1, r.count = 1
                        ON MATCH SET  r.count = r.count + 1
                        SET r.last_seen = datetime($ts), r.valid_to = null
                    """, eid=eid, tid=tid, ts=e.get("timestamp"))

            # Actor -[:CO_MENTIONED_WITH]-> Actor (within same event)
            actor_ids = [
                _actor_id(a.get("name"))
                for a in actors
                if a.get("name")
            ]
            for i in range(len(actor_ids)):
                for j in range(i + 1, len(actor_ids)):
                    tx.run("""
                        MATCH (a:Actor {id: $aid}), (b:Actor {id: $bid})
                        MERGE (a)-[r:CO_MENTIONED_WITH]-(b)
                        ON CREATE SET r.first_co = datetime($ts), r.count = 1
                        ON MATCH SET  r.count = r.count + 1
                        SET r.last_co = datetime($ts), r.strength = toFloat(r.count) / 10.0
                    """, aid=actor_ids[i], bid=actor_ids[j], ts=e.get("timestamp"))

    @staticmethod
    def _write_signal_nodes(tx, signals: List[Dict[str, Any]]) -> None:
        rows = [
            {
                "id": sig.get("signal_id"),
                "source_event_id": sig.get("source_event_id"),
                "source": sig.get("source", ""),
                "signal_type": sig.get("signal_type", "raw"),
                "title": sig.get("title", ""),
                "timestamp": sig.get("timestamp"),
                "confidence": sig.get("confidence"),
                "cluster_id": sig.get("cluster_id"),
            }
            for sig in signals
            if sig.get("signal_id")
        ]
        if not rows:
            return

        tx.run("""
            UNWIND $rows AS row
            MERGE (s:Signal {id: row.id})
            SET s.source_event_id = row.source_event_id,
                s.source = row.source,
                s.signal_type = row.signal_type,
                s.title = row.title,
                s.timestamp = row.timestamp,
                s.confidence = row.confidence,
                s.cluster_id = row.cluster_id
        """, rows=rows)

    @staticmethod
    def _write_signal_relationships(tx, signals: List[Dict[str, Any]]) -> None:
        for sig in signals:
            sid = sig.get("signal_id")
            if not sid:
                continue

            event_id = sig.get("source_event_id")
            if event_id:
                tx.run("""
                    MATCH (s:Signal {id: $sid}), (e:Event {id: $eid})
                    MERGE (s)-[:DERIVED_FROM]->(e)
                    MERGE (e)-[:SUPPORTS]->(s)
                """, sid=sid, eid=event_id)

            for actor_name in sig.get("actors") or []:
                if not actor_name:
                    continue
                aid = _actor_id(actor_name.strip())
                tx.run("""
                    MATCH (s:Signal {id: $sid})
                    MERGE (a:Actor {id: $aid})
                    ON CREATE SET a.name = $name, a.type = 'UNKNOWN', a.mention_count = 1
                    MERGE (s)-[:MENTIONS]->(a)
                """, sid=sid, aid=aid, name=actor_name)

            loc_name = (sig.get("location_name") or "").strip()
            if loc_name:
                lid = _location_id(loc_name)
                tx.run("""
                    MATCH (s:Signal {id: $sid})
                    MERGE (l:Location {id: $lid})
                    ON CREATE SET l.name = $name, l.lat = $lat, l.lon = $lon
                    ON MATCH SET l.lat = coalesce(l.lat, $lat), l.lon = coalesce(l.lon, $lon)
                    MERGE (s)-[:LOCATED_IN]->(l)
                """, sid=sid, lid=lid, name=loc_name, lat=sig.get("location_lat"), lon=sig.get("location_lon"))

            meta = sig.get("meta") or {}
            for tag in meta.get("tags") or []:
                if not tag:
                    continue
                tid = _theme_id(str(tag))
                tx.run("""
                    MATCH (s:Signal {id: $sid})
                    MERGE (t:Theme {id: $tid})
                    ON CREATE SET t.label = $label
                    MERGE (s)-[:AMPLIFIES]->(t)
                """, sid=sid, tid=tid, label=str(tag))

    @staticmethod
    def _write_signal_cluster_edges(tx, clusters: List[Any]) -> None:
        for cluster in clusters:
            signal_ids = list(getattr(cluster, "signal_ids", []) or [])
            cluster_id = getattr(cluster, "cluster_id", None)
            scores = getattr(cluster, "scores", {}) or {}
            shared_actors = list(getattr(cluster, "shared_actors", []) or [])

            for signal_id in signal_ids:
                tx.run("""
                    MATCH (s:Signal {id: $sid})
                    SET s.cluster_id = $cluster_id
                """, sid=signal_id, cluster_id=cluster_id)

            for i in range(len(signal_ids)):
                for j in range(i + 1, len(signal_ids)):
                    tx.run("""
                        MATCH (a:Signal {id: $sid_a}), (b:Signal {id: $sid_b})
                        MERGE (a)-[r:CORRELATED_WITH]-(b)
                        SET r.cluster_id = $cluster_id,
                            r.composite = $composite,
                            r.semantic = $semantic,
                            r.entity_overlap = $entity_overlap,
                            r.temporal = $temporal,
                            r.shared_actors = $shared_actors
                    """,
                    sid_a=signal_ids[i],
                    sid_b=signal_ids[j],
                    cluster_id=cluster_id,
                    composite=scores.get("composite"),
                    semantic=scores.get("semantic"),
                    entity_overlap=scores.get("entity_overlap"),
                    temporal=scores.get("temporal"),
                    shared_actors=shared_actors)

    def write_narrative_nodes(self, narratives: List[Dict]) -> None:
        """
        Persist detected narratives as :Narrative nodes in Neo4j.
        Connects narrative to actors via IMPLICATES relationship.
        """
        if not self._available or not narratives:
            return
        try:
            with self._driver.session() as s:
                s.execute_write(self._write_narrative_batch, narratives)
            logger.info("Graph: wrote %d narrative nodes", len(narratives))
        except Exception as exc:
            logger.error("Graph narrative write failed: %s", exc)

    @staticmethod
    def _write_narrative_batch(tx, narratives: List[Dict]) -> None:
        rows = [
            {
                "id":           n.get("narrative_id", ""),
                "signal_type":  n.get("signal_type", ""),
                "topic":        n.get("topic", ""),
                "strength":     n.get("strength", 0.0),
                "severity":     n.get("severity", "low"),
                "event_count":  n.get("event_count", 0),
                "detected_at":  n.get("detected_at"),
                "valid_from":   n.get("detected_at"),
            }
            for n in narratives
        ]
        tx.run("""
            UNWIND $rows AS row
            MERGE (n:Narrative {id: row.id})
            SET n.signal_type  = row.signal_type,
                n.topic        = row.topic,
                n.strength     = row.strength,
                n.severity     = row.severity,
                n.event_count  = row.event_count,
                n.detected_at  = row.detected_at,
                n.valid_from   = row.valid_from
        """, rows=rows)

        # Connect narratives to actors they implicate
        for n in narratives:
            nid     = n.get("narrative_id", "")
            actors  = n.get("actors", [])
            topic = (n.get("topic") or "").strip()
            spread = n.get("geographic_spread") or {}
            for actor_name in actors:
                if not actor_name:
                    continue
                aid = _actor_id(actor_name)
                tx.run("""
                    MATCH (n:Narrative {id: $nid})
                    MERGE (a:Actor {id: $aid})
                    ON CREATE SET a.name = $name
                    MERGE (n)-[:IMPLICATES]->(a)
                """, nid=nid, aid=aid, name=actor_name)
            if topic:
                tx.run("""
                    MATCH (n:Narrative {id: $nid})
                    MERGE (t:Theme {id: $tid})
                    ON CREATE SET t.label = $topic
                    MERGE (n)-[:AMPLIFIES]->(t)
                """, nid=nid, tid=_theme_id(topic), topic=topic)
            for region_name in spread.keys():
                clean_name = (region_name or "").strip()
                if not clean_name or clean_name.lower() == "other":
                    continue
                tx.run("""
                    MATCH (n:Narrative {id: $nid})
                    MERGE (l:Location {id: $lid})
                    ON CREATE SET l.name = $name
                    MERGE (n)-[:AFFECTS]->(l)
                """, nid=nid, lid=_location_id(clean_name), name=clean_name)

    def write_situation_nodes(self, situations: List[Dict]) -> None:
        """
        Persist detected situations as :Situation nodes in Neo4j.
        Links each Situation to its member Events and Actors.
        """
        if not self._available or not situations:
            return
        try:
            with self._driver.session() as s:
                s.execute_write(self._write_situation_batch, situations)
            logger.info("Graph: wrote %d situation nodes", len(situations))
        except Exception as exc:
            logger.error("Graph situation write failed: %s", exc)

    @staticmethod
    def _write_situation_batch(tx, situations: List[Dict]) -> None:
        rows = [
            {
                "id":          s.get("situation_id", ""),
                "title":       s.get("title", ""),
                "description": s.get("description", ""),
                "risk_score":  s.get("risk_score", 0.0),
                "severity":    s.get("severity", "low"),
                "region":      s.get("region", "GLOBAL"),
                "event_count": s.get("event_count", 0),
                "status":      s.get("status", "active"),
                "detected_at": s.get("detected_at"),
                "valid_from":  s.get("detected_at"),
            }
            for s in situations
        ]
        tx.run("""
            UNWIND $rows AS row
            MERGE (s:Situation {id: row.id})
            SET s.title       = row.title,
                s.description = row.description,
                s.risk_score  = row.risk_score,
                s.severity    = row.severity,
                s.region      = row.region,
                s.event_count = row.event_count,
                s.status      = row.status,
                s.detected_at = row.detected_at,
                s.valid_from  = row.valid_from
        """, rows=rows)

        # Link Situations to Events and Actors
        for sit in situations:
            sid = sit.get("situation_id", "")
            for eid in (sit.get("event_ids") or []):
                tx.run("""
                    MATCH (s:Situation {id: $sid})
                    MATCH (e:Event {id: $eid})
                    MERGE (s)-[:CONTAINS]->(e)
                """, sid=sid, eid=eid)
            for actor_name in (sit.get("actor_ids") or []):
                if actor_name:
                    aid = _actor_id(actor_name)
                    tx.run("""
                        MATCH (s:Situation {id: $sid})
                        MERGE (a:Actor {id: $aid})
                        ON CREATE SET a.name = $name
                        MERGE (s)-[:INVOLVES]->(a)
                    """, sid=sid, aid=aid, name=actor_name)

    def update_actor_risk_weight(self, actor_name: str, outcome: str) -> None:
        """
        Adjust an Actor's risk_weight in Neo4j based on decision outcome feedback.
        effective â†’ lower risk (good outcome)
        ineffective â†’ raise risk (threat persists)
        inconclusive â†’ no change
        """
        if not self._available or not actor_name:
            return
        delta = {"effective": -0.05, "ineffective": 0.05}.get(outcome, 0.0)
        if delta == 0.0:
            return
        aid = _actor_id(actor_name)
        try:
            with self._driver.session() as s:
                s.run("""
                    MATCH (a:Actor {id: $id})
                    SET a.risk_weight = toFloat(
                        CASE WHEN a.risk_weight IS NULL THEN 0.5
                             ELSE a.risk_weight
                        END
                    ) + $delta
                    SET a.risk_weight = CASE
                        WHEN a.risk_weight < 0.0 THEN 0.0
                        WHEN a.risk_weight > 1.0 THEN 1.0
                        ELSE a.risk_weight
                    END
                """, id=aid, delta=delta)
        except Exception as exc:
            logger.error("update_actor_risk_weight failed for %s: %s", actor_name, exc)

    def write_community_memberships(self, communities: Dict[str, int]) -> None:
        """Write community_id to Actor nodes. communities = {actor_id: community_int}"""
        if not self._available or not communities:
            return
        rows = [{"id": k, "community_id": v} for k, v in communities.items()]
        with self._driver.session() as s:
            s.run("""
                UNWIND $rows AS row
                MATCH (a:Actor {id: row.id})
                SET a.community_id = row.community_id
            """, rows=rows)

    def get_temporal_graph(self, since_hours: int = 24, until_hours: int = 0) -> Dict:
        """Return graph snapshot filtered to edges active in [now-since_hours, now-until_hours]."""
        if not self._available:
            return {"nodes": [], "edges": []}
        with self._driver.session() as s:
            result = s.run("""
                MATCH (a:Actor)-[r:CO_MENTIONED_WITH]-(b:Actor)
                WHERE r.last_co IS NOT NULL
                  AND r.last_co >= datetime() - duration({hours: $since_h})
                  AND r.last_co <= datetime() - duration({hours: $until_h})
                RETURN a.id AS src, a.name AS src_name, a.community_id AS src_comm,
                       b.id AS tgt, b.name AS tgt_name, b.community_id AS tgt_comm,
                       r.count AS weight, r.first_co AS first_co, r.last_co AS last_co
                LIMIT 500
            """, since_h=since_hours, until_h=until_hours)
            nodes_map = {}
            edges = []
            for rec in result:
                for nid, nname, ncomm in [(rec["src"], rec["src_name"], rec["src_comm"]),
                                           (rec["tgt"], rec["tgt_name"], rec["tgt_comm"])]:
                    if nid not in nodes_map:
                        nodes_map[nid] = {"id": nid, "label": nname, "community_id": ncomm}
                edges.append({
                    "from": rec["src"], "to": rec["tgt"],
                    "weight": rec["weight"],
                    "first_co": str(rec["first_co"]) if rec["first_co"] else None,
                    "last_co": str(rec["last_co"]) if rec["last_co"] else None,
                })
            return {"nodes": list(nodes_map.values()), "edges": edges}

    def detect_communities(self, min_strength: float = 0.1) -> Dict[str, int]:
        """
        Returns {actor_id: community_int} using connected-components BFS on
        CO_MENTIONED_WITH edges with strength >= min_strength.
        """
        if not self._available:
            return {}
        with self._driver.session() as s:
            result = s.run("""
                MATCH (a:Actor)-[r:CO_MENTIONED_WITH]-(b:Actor)
                WHERE r.strength >= $min_str OR r.count >= 3
                RETURN a.id AS src, b.id AS tgt
            """, min_str=min_strength)
            adjacency: Dict[str, set] = {}
            for rec in result:
                adjacency.setdefault(rec["src"], set()).add(rec["tgt"])
                adjacency.setdefault(rec["tgt"], set()).add(rec["src"])
        # BFS to label communities
        community_map: Dict[str, int] = {}
        community_id = 0
        for node in adjacency:
            if node in community_map:
                continue
            queue = [node]
            while queue:
                cur = queue.pop()
                if cur in community_map:
                    continue
                community_map[cur] = community_id
                queue.extend(adjacency.get(cur, set()) - community_map.keys())
            community_id += 1
        return community_map

    def get_influence_network(
        self,
        limit: int = 200,
        min_strength: float = 0.1,
    ) -> Dict[str, Any]:
        """
        Returns influence network graph data for the UI.
        Includes INFLUENCES edges with strength + CO_MENTIONED_WITH edges.
        """
        if not self._available:
            return {"nodes": [], "edges": [], "error": "Neo4j not available"}

        with self._driver.session() as s:
            result = s.run("""
                MATCH (a:Actor)-[r:INFLUENCES]->(b:Actor)
                WHERE r.strength >= $min_strength
                  AND a.influence_score IS NOT NULL
                RETURN a, r, b
                ORDER BY r.strength DESC
                LIMIT $limit
            """, min_strength=min_strength, limit=limit).data()

        if not result:
            # Fall back to CO_MENTIONED_WITH if no influence edges yet
            with self._driver.session() as s:
                result = s.run("""
                    MATCH (a:Actor)-[r:CO_MENTIONED_WITH]-(b:Actor)
                    WHERE r.count >= 3
                    RETURN a, r, b
                    ORDER BY r.count DESC
                    LIMIT $limit
                """, limit=limit).data()

        nodes: Dict[str, dict] = {}
        edges: List[dict] = []

        for rec in result:
            for key in ("a", "b"):
                node = rec.get(key)
                if not node:
                    continue
                props = node if isinstance(node, dict) else {}
                nid   = props.get("id", "")
                if nid and nid not in nodes:
                    nodes[nid] = {
                        "id":    nid,
                        "label": props.get("name", nid)[:40],
                        "group": "actor",
                        "type":  props.get("type", "PERSON"),
                        "value": props.get("influence_score", 0) * 100 or props.get("mention_count", 1),
                        "influence_score": props.get("influence_score"),
                        "mention_count":   props.get("mention_count", 0),
                    }

            rel = rec.get("r")
            a   = rec.get("a", {})
            b   = rec.get("b", {})
            if a and b:
                a_id = a.get("id", "") if isinstance(a, dict) else ""
                b_id = b.get("id", "") if isinstance(b, dict) else ""
                if a_id and b_id:
                    weight = (rel.get("strength", 0) if isinstance(rel, dict) else 0) or (rel.get("count", 1) / 10.0 if isinstance(rel, dict) else 0)
                    edges.append({
                        "from":   a_id,
                        "to":     b_id,
                        "label":  "INFLUENCES",
                        "weight": round(float(weight), 3),
                        "evidence_mode": self._edge_evidence_mode("INFLUENCES"),
                    })

        return {
            "nodes":      list(nodes.values()),
            "edges":      edges,
            "node_count": len(nodes),
            "edge_count": len(edges),
        }

    def execute_cypher(self, query: str, parameters: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """
        Executes raw Analyst Cypher querying within a rigid READ transaction.
        Secondary guard rails automatically reject explicit state changes (merges, deletes).
        """
        if not self._available:
            return {"nodes": [], "edges": [], "error": "Neo4j not available"}

        # Secondary sanitization for absolute explicit safety. 
        # (Execute_read normally catches this, but failing fast prevents overhead)
        query_upper = query.upper()
        blocked_keywords = ["CREATE ", "MERGE ", "DELETE ", "SET ", "REMOVE ", "DROP ", "DETACH "]
        if any(keyword in query_upper for keyword in blocked_keywords):
            logger.warning(f"Malicious keyword blocked in analyst Cypher execution: {query}")
            return {"error": "Write operations are strictly disabled in Analyst Mode. Use matching queries only."}

        params = parameters or {}
        
        def _read_transaction(tx):
            res = tx.run(query, **params)
            return res.data()
            
        try:
            with self._driver.session() as s:
                records = s.execute_read(_read_transaction)
                return self._build_graph(records)
        except Exception as exc:
            logger.error(f"Cypher execution error: {exc}")
            return {"error": str(exc), "nodes": [], "edges": []}


    def ego_graph(
        self,
        actor_id: str,
        depth: int = 1,
        limit: int = 100,
    ) -> Dict[str, Any]:
        """
        Returns {nodes, edges} for the vis.js / D3 actor graph.
        depth=1 â†’ actor + their events
        depth=2 â†’ actor + events + co-actors on those events
        """
        if not self._available:
            return {"nodes": [], "edges": [], "error": "Neo4j not available"}

        with self._driver.session() as s:
            if depth == 1:
                result = s.run("""
                    MATCH (a:Actor {id: $id})
                    OPTIONAL MATCH (ev:Event)-[:MENTIONS]->(a)
                    OPTIONAL MATCH (ev)-[:LOCATED_IN]->(l:Location)
                    OPTIONAL MATCH (ev)-[:AMPLIFIES]->(t:Theme)
                    RETURN a, ev AS e, l, t
                    LIMIT $limit
                """, id=actor_id, limit=limit)
            else:
                result = s.run("""
                    MATCH (a:Actor {id: $id})
                    OPTIONAL MATCH (e:Event)-[:MENTIONS]->(a)
                    OPTIONAL MATCH (e)-[:MENTIONS]->(b:Actor)
                    WHERE b IS NULL OR b.id <> $id
                    OPTIONAL MATCH (e)-[:LOCATED_IN]->(l:Location)
                    OPTIONAL MATCH (e)-[:AMPLIFIES]->(t:Theme)
                    OPTIONAL MATCH (s:Signal)-[:DERIVED_FROM]->(e)
                    OPTIONAL MATCH (s)-[:CORRELATED_WITH]-(s2:Signal)
                    OPTIONAL MATCH (s2)-[:MENTIONS]->(c:Actor)
                    WHERE c IS NULL OR c.id <> $id
                    OPTIONAL MATCH (n:Narrative)-[:IMPLICATES]->(a)
                    RETURN a, e, b, l, t, s, s2, c, n
                    LIMIT $limit
                """, id=actor_id, limit=limit)

            return self._build_graph(result.data())

    def node_neighbors(self, node_id: str, limit: int = 80) -> Dict[str, Any]:
        """Generic typed adjacency for ANY graph node (event/actor/location/org/theme/
        narrative/signal) keyed by its `id`. Powers the object-explorer "drill anywhere".
        Returns {id, type, label, neighbors:[{type,id,label,rel,direction}], by_type, total}."""
        if not self._available:
            return {"id": node_id, "neighbors": [], "by_type": {}, "total": 0, "error": "Neo4j not available"}

        with self._driver.session() as s:
            rows = s.run("""
                MATCH (n {id: $id})
                OPTIONAL MATCH (n)-[r]-(m)
                WHERE m IS NOT NULL AND m.id IS NOT NULL AND m.id <> $id
                RETURN labels(n)[0] AS self_type,
                       coalesce(n.name, n.title, n.label, n.id) AS self_label,
                       labels(m)[0] AS type,
                       m.id AS id,
                       coalesce(m.name, m.title, m.label, m.id) AS label,
                       type(r) AS rel,
                       startNode(r) = n AS outgoing
                LIMIT $limit
            """, id=node_id, limit=limit).data()

        if not rows:
            return {"id": node_id, "neighbors": [], "by_type": {}, "total": 0}

        self_type = (rows[0].get("self_type") or "").lower()
        self_label = rows[0].get("self_label")
        seen: set = set()
        neighbors: List[Dict[str, Any]] = []
        by_type: Dict[str, int] = {}
        for r in rows:
            nid = r.get("id")
            if not nid:
                continue
            key = (nid, r.get("rel"))
            if key in seen:
                continue
            seen.add(key)
            ntype = (r.get("type") or "node").lower()
            neighbors.append({
                "type": ntype,
                "id": nid,
                "label": r.get("label") or nid,
                "rel": r.get("rel"),
                "direction": "out" if r.get("outgoing") else "in",
            })
            by_type[ntype] = by_type.get(ntype, 0) + 1

        return {
            "id": node_id,
            "type": self_type,
            "label": self_label,
            "neighbors": neighbors,
            "by_type": by_type,
            "total": len(neighbors),
        }

    def get_node(self, node_id: str) -> Optional[Dict[str, Any]]:
        """Fetch any graph node's label + properties by id (for object-explorer headers on
        types that don't live in Postgres: location/theme/signal/organization/narrative)."""
        if not self._available:
            return None
        with self._driver.session() as s:
            rec = s.run(
                "MATCH (n {id: $id}) RETURN labels(n)[0] AS type, properties(n) AS props LIMIT 1",
                id=node_id,
            ).single()
        if not rec:
            return None
        return {"type": (rec["type"] or "node"), "props": dict(rec["props"] or {})}

    def correlated_actors(self, seed_ids: List[str], limit: int = 40) -> List[Dict[str, Any]]:
        """Actor↔actor correlation from the precomputed CO_MENTIONED_WITH edges (computed
        over ALL events, not a workspace subset). Returns [{source, rel, target, weight}]."""
        if not self._available or not seed_ids:
            return []
        with self._driver.session() as s:
            rows = s.run("""
                MATCH (a:Actor)-[r:CO_MENTIONED_WITH]-(b:Actor)
                WHERE a.id IN $ids
                RETURN coalesce(a.name, a.id) AS source,
                       coalesce(b.name, b.id) AS target,
                       coalesce(r.weight, r.count, 1) AS weight
                ORDER BY weight DESC
                LIMIT $limit
            """, ids=seed_ids, limit=limit).data()
        out, seen = [], set()
        for r in rows:
            k = tuple(sorted((str(r["source"]).lower(), str(r["target"]).lower())))
            if k in seen or r["source"] == r["target"]:
                continue
            seen.add(k)
            out.append({"source": r["source"], "rel": "CO_MENTIONED", "target": r["target"], "weight": r["weight"]})
        return out

    def actor_events(self, seed_ids: List[str], limit: int = 40) -> List[Dict[str, Any]]:
        """All events the seed actors are MENTIONS-linked to in the graph (cross-corpus,
        not a workspace window). Returns [{id, title, source, event_type, timestamp, actor}]
        newest first. This is the 'connected events' for workspace correlation."""
        if not self._available or not seed_ids:
            return []
        with self._driver.session() as s:
            rows = s.run("""
                MATCH (a:Actor)<-[:MENTIONS]-(e:Event)
                WHERE a.id IN $ids
                RETURN DISTINCT e.id AS id,
                       e.title AS title,
                       e.source AS source,
                       e.event_type AS event_type,
                       e.timestamp AS timestamp,
                       coalesce(a.name, a.id) AS actor
                ORDER BY e.timestamp DESC
                LIMIT $limit
            """, ids=seed_ids, limit=limit).data()
        out, seen = [], set()
        for r in rows:
            eid = r.get("id")
            if not eid or eid in seen:
                continue
            seen.add(eid)
            out.append({
                "id": eid,
                "title": r.get("title") or eid,
                "source": r.get("source"),
                "event_type": r.get("event_type"),
                "timestamp": r.get("timestamp"),
                "actor": r.get("actor"),
            })
        return out

    def actor_list(
        self,
        entity_type: Optional[str] = None,
        min_mentions: int = 1,
        limit: int = 100,
        offset: int = 0,
    ) -> Tuple[int, List[Dict]]:
        if not self._available:
            return 0, []

        with self._driver.session() as s:
            conditions = "WHERE a.mention_count >= $min_mentions"
            if entity_type:
                conditions += " AND a.type = $type"

            count = s.run(
                f"MATCH (a:Actor) {conditions} RETURN count(a) AS c",
                min_mentions=min_mentions, type=entity_type or ""
            ).single()["c"]

            rows = s.run(
                f"""
                MATCH (a:Actor) {conditions}
                RETURN a
                ORDER BY a.mention_count DESC
                SKIP $skip LIMIT $limit
                """,
                min_mentions=min_mentions, type=entity_type or "",
                skip=offset, limit=limit,
            ).data()

        actors = [
            {
                "id":            r["a"]["id"],
                "name":          r["a"].get("name"),
                "type":          r["a"].get("type"),
                "mention_count": r["a"].get("mention_count", 0),
                "influence_score": r["a"].get("influence_score"),
                "sentiment_score": r["a"].get("sentiment_score"),
                "narrative_count": r["a"].get("narrative_count", 0),
                "event_count": r["a"].get("event_count", r["a"].get("mention_count", 0)),
            }
            for r in rows
        ]
        return count, actors

    def get_actor(self, actor_id: str) -> Optional[Dict[str, Any]]:
        """Fetch a single actor node by id for ontology fallbacks."""
        if not self._available:
            return None

        with self._driver.session() as s:
            row = s.run(
                """
                MATCH (a:Actor {id: $id})
                RETURN a
                LIMIT 1
                """,
                id=actor_id,
            ).single()

        if not row:
            return None

        actor = row.get("a")
        if actor is None:
            return None

        def _prop(key: str, default: Any = None) -> Any:
            try:
                return actor.get(key, default)
            except AttributeError:
                pass
            except TypeError:
                pass
            try:
                return actor[key]
            except Exception:
                return default

        return {
            "id": _prop("id"),
            "name": _prop("name"),
            "type": _prop("type", "UNKNOWN"),
            "mention_count": _prop("mention_count", 0),
            "influence_score": _prop("influence_score"),
        }

    def ensure_actor(
        self,
        actor_id: str,
        name: str,
        entity_type: str = "UNKNOWN",
        *,
        source: str = "graph_click",
    ) -> Dict[str, Any]:
        """Create or refresh a lightweight Actor node for graph-click navigation."""
        if not self._available:
            return {
                "id": actor_id,
                "name": name,
                "type": entity_type or "UNKNOWN",
                "mention_count": 0,
                "mapped": False,
                "graph_recorded": False,
            }

        with self._driver.session() as s:
            row = s.run(
                """
                MERGE (a:Actor {id: $id})
                ON CREATE SET
                    a.name = $name,
                    a.type = $type,
                    a.mention_count = 0,
                    a.first_seen = datetime(),
                    a.source = $source
                SET
                    a.name = coalesce(a.name, $name),
                    a.type = coalesce(a.type, $type),
                    a.last_seen = datetime(),
                    a.mapped_from_graph = true
                RETURN a
                LIMIT 1
                """,
                id=actor_id,
                name=name,
                type=entity_type or "UNKNOWN",
                source=source,
            ).single()

        actor = row.get("a") if row else None
        return {
            "id": actor_id,
            "name": (actor or {}).get("name", name) if isinstance(actor, dict) else name,
            "type": (actor or {}).get("type", entity_type) if isinstance(actor, dict) else entity_type,
            "mention_count": (actor or {}).get("mention_count", 0) if isinstance(actor, dict) else 0,
            "mapped": True,
            "graph_recorded": True,
        }

    @staticmethod
    def _build_graph(records: List[Dict]) -> Dict[str, Any]:
        """
        Convert Neo4j .data() results to {nodes, edges}.

        After .data(), each record value is a plain Python dict of node
        properties â€” there are no nested 'labels' or 'properties' keys.
        Group is inferred from the node id prefix that all Vision-I nodes use.
        """
        nodes: Dict[str, dict] = {}
        edges: List[dict]      = []

        for rec in records:
            for key, val in rec.items():
                if not val or not isinstance(val, dict):
                    continue
                nid = val.get("id", "")
                if not nid or nid in nodes:
                    continue

                # Infer group from id prefix (all Vision-I nodes use prefixed ids)
                if nid.startswith("actor:"):
                    group = "actor"
                elif nid.startswith("loc:"):
                    group = "location"
                elif nid.startswith("theme:"):
                    group = "theme"
                elif nid.startswith("narrative:"):
                    group = "narrative"
                elif nid.startswith("signal:"):
                    group = "signal"
                elif nid.startswith("org:"):
                    group = "organization"
                elif val.get("event_type") or val.get("title"):
                    group = "event"
                else:
                    group = "actor"

                label_raw = val.get("name") or val.get("title") or nid
                nodes[nid] = {
                    "id":    nid,
                    "label": label_raw[:60],
                    "group": group,
                    "value": val.get("mention_count", 1),
                    "type":  val.get("type") or val.get("event_type"),
                    "confidence": val.get("confidence"),
                    "cluster_id": val.get("cluster_id"),
                }

        edge_keys = set()

        def add_edge(src: str, dst: str, label: str, weight: float = 1.0) -> None:
            if not src or not dst:
                return
            key = (src, dst, label)
            if key in edge_keys:
                return
            edge_keys.add(key)
            edges.append({
                "from": src,
                "to": dst,
                "label": label,
                "weight": weight,
                "evidence_mode": GraphDB._edge_evidence_mode(label),
            })

        for rec in records:
            a = rec.get("a") if isinstance(rec.get("a"), dict) else {}
            e = rec.get("e") if isinstance(rec.get("e"), dict) else {}
            b = rec.get("b") if isinstance(rec.get("b"), dict) else {}
            c = rec.get("c") if isinstance(rec.get("c"), dict) else {}
            l = rec.get("l") if isinstance(rec.get("l"), dict) else {}
            t = rec.get("t") if isinstance(rec.get("t"), dict) else {}
            s = rec.get("s") if isinstance(rec.get("s"), dict) else {}
            s2 = rec.get("s2") if isinstance(rec.get("s2"), dict) else {}
            n = rec.get("n") if isinstance(rec.get("n"), dict) else {}

            aid = (a or {}).get("id", "")
            eid = (e or {}).get("id", "")
            bid = (b or {}).get("id", "")
            cid = (c or {}).get("id", "")
            lid = (l or {}).get("id", "")
            tid = (t or {}).get("id", "")
            sid = (s or {}).get("id", "")
            sid2 = (s2 or {}).get("id", "")
            nid = (n or {}).get("id", "")

            if eid and aid:
                add_edge(eid, aid, "MENTIONS")
            if eid and bid:
                add_edge(eid, bid, "MENTIONS")
            if eid and lid:
                add_edge(eid, lid, "LOCATED_IN")
            if eid and tid:
                add_edge(eid, tid, "AMPLIFIES")
            if sid and eid:
                add_edge(sid, eid, "DERIVED_FROM")
            if sid and aid:
                add_edge(sid, aid, "MENTIONS")
            if sid2 and sid:
                add_edge(sid, sid2, "CORRELATED_WITH")
            if sid2 and cid:
                add_edge(sid2, cid, "MENTIONS")
            if nid and aid:
                add_edge(nid, aid, "IMPLICATES")
            if nid and tid:
                add_edge(nid, tid, "AMPLIFIES")

        return {
            "nodes":      list(nodes.values()),
            "edges":      edges,
            "node_count": len(nodes),
            "edge_count": len(edges),
        }


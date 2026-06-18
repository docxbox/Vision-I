"""
api/routers/entities.py
â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
GET /entities              â€” actor/location list
GET /entities/{id}/graph   â€” ego graph (vis.js / D3 compatible)

DB path: queries Neo4j when available.
Memory fallback: derives entities from in-memory job store.
"""

import logging
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from api.routers.ingest import _jobs
from core.entity_normalizer import canonical_actor_key, normalize_actor_name

logger = logging.getLogger("vision_i.api.entities")
router = APIRouter(tags=["Entities"])

# ── Response schemas ───────────────────────────────────────────────────────

class EntityItemSchema(BaseModel):
    model_config = ConfigDict(extra="allow")
    id: Optional[str] = None
    name: Optional[str] = None
    type: Optional[str] = None
    mention_count: int = 0

class EntityListResponse(BaseModel):
    total: int = 0
    limit: int = 100
    offset: int = 0
    entities: List[Any] = Field(default_factory=list)

class EgoGraphResponse(BaseModel):
    model_config = ConfigDict(extra="allow")
    nodes: List[Any] = Field(default_factory=list)
    edges: List[Any] = Field(default_factory=list)
    node_count: int = 0
    edge_count: int = 0
    evidence: Optional[Dict[str, Any]] = None

class EntityMapRequest(BaseModel):
    id: Optional[str] = None
    label: str
    group: Optional[str] = None
    type: Optional[str] = None
    source: Optional[str] = "graph_click"

class EntityMapResponse(BaseModel):
    id: str
    name: str
    type: str
    mapped: bool = True
    graph_recorded: bool = False
    postgres_recorded: bool = False


def _actor_id_from_label(label: str) -> str:
    key = canonical_actor_key(label).replace(" ", "_")
    return f"actor:{key}" if key else "actor:unknown"


def _graph_id_to_label(entity_id: str) -> str:
    raw = str(entity_id or "").split(":", 1)[-1]
    return raw.replace("_", " ").replace("-", " ").strip().title()


def _entity_type_from_group(group: Optional[str], explicit: Optional[str]) -> str:
    value = (explicit or group or "UNKNOWN").strip().upper()
    if value in {"ACTOR", "PERSON"}:
        return "PERSON"
    if value in {"ORG", "ORGANIZATION"}:
        return "ORG"
    if value in {"LOC", "LOCATION"}:
        return "LOC"
    if value in {"EVENT", "THEME", "SIGNAL", "NARRATIVE"}:
        return value
    return value or "UNKNOWN"

def _collect_actors_from_memory(request: Request) -> Dict[str, dict]:
    registry: Dict[str, dict] = {}
    for job in _jobs.values():
        for event in job.get("events") or []:
            for actor in event.get("actors") or []:
                name = (actor.get("name") or "").strip()
                if not name:
                    continue
                key = name.lower()
                if key not in registry:
                    registry[key] = {
                        "id":            f"actor:{key.replace(' ', '_')}",
                        "name":          name,
                        "type":          actor.get("type", "UNKNOWN"),
                        "mention_count": 0,
                        "event_ids":     [],
                    }
                registry[key]["mention_count"] += 1
                eid = event.get("event_id")
                if eid and eid not in registry[key]["event_ids"]:
                    registry[key]["event_ids"].append(eid)
    return registry


def _memory_ego_graph(entity_id: str, depth: int, request: Request) -> Dict:
    registry = _collect_actors_from_memory(request)
    actor = next((a for a in registry.values() if a["id"] == entity_id), None)
    if not actor:
        return None

    all_events = []
    for job in _jobs.values():
        all_events.extend(job.get("events") or [])
    event_lookup = {e["event_id"]: e for e in all_events if e.get("event_id")}

    nodes = [{
        "id":    actor["id"],
        "label": actor["name"],
        "type":  actor["type"],
        "group": "actor",
        "value": actor["mention_count"],
    }]
    edges = []

    for eid in actor.get("event_ids") or []:
        ev = event_lookup.get(eid)
        if not ev:
            continue
        nodes.append({
            "id":    eid,
            "label": (ev.get("title") or "")[:60],
            "type":  ev.get("event_type", "news"),
            "group": "event",
            "value": 1,
        })
        edges.append({"from": actor["id"], "to": eid, "label": "PARTICIPATED_IN"})

        if depth >= 2:
            for co in ev.get("actors") or []:
                co_name = (co.get("name") or "").strip()
                co_id   = f"actor:{co_name.lower().replace(' ', '_')}"
                if not co_name or co_id == actor["id"]:
                    continue
                if not any(n["id"] == co_id for n in nodes):
                    co_data = registry.get(co_name.lower(), {})
                    nodes.append({
                        "id":    co_id,
                        "label": co_name,
                        "type":  co.get("type", "UNKNOWN"),
                        "group": "actor",
                        "value": co_data.get("mention_count", 1),
                    })
                if not any(e["from"] == co_id and e["to"] == eid for e in edges):
                    edges.append({"from": co_id, "to": eid, "label": "PARTICIPATED_IN"})

    return {
        "entity":     {k: v for k, v in actor.items() if k != "event_ids"},
        "nodes":      nodes,
        "edges":      edges,
        "node_count": len(nodes),
        "edge_count": len(edges),
        "evidence": {
            "graph_source": "memory_fallback",
            "actor_count": len([n for n in nodes if n.get("group") == "actor"]),
            "event_count": len([n for n in nodes if n.get("group") == "event"]),
            "location_count": len([n for n in nodes if n.get("group") == "location"]),
            "signal_count": 0,
            "narrative_count": 0,
            "theme_count": 0,
        },
    }

@router.get("", summary="List known actors and locations", response_model=EntityListResponse)
async def list_entities(
    request:      Request,
    entity_type:  Optional[str] = Query(None, alias="type",
                                        description="PERSON | ORG | LOC | VEHICLE | UNKNOWN"),
    min_mentions: int            = Query(1, ge=1),
    limit:        int            = Query(100, ge=1, le=1000),
    offset:       int            = Query(0, ge=0),
):
    import asyncio
    graph = request.app.state.graph
    if graph.available:
        try:
            loop = asyncio.get_running_loop()
            total, actors = await asyncio.wait_for(
                loop.run_in_executor(
                    None,
                    lambda: graph.actor_list(
                        entity_type=entity_type, min_mentions=min_mentions,
                        limit=limit, offset=offset,
                    ),
                ),
                timeout=10.0,
            )
            return {"total": total, "limit": limit, "offset": offset, "entities": actors}
        except asyncio.TimeoutError:
            logger.warning("Neo4j actor_list timed out â€” using memory fallback")
        except Exception as exc:
            logger.warning("Neo4j fallback: %s", exc)
    registry = _collect_actors_from_memory(request)
    entities = list(registry.values())
    if entity_type:
        entities = [e for e in entities if e.get("type") == entity_type.upper()]
    entities = [e for e in entities if e["mention_count"] >= min_mentions]
    entities.sort(key=lambda e: e["mention_count"], reverse=True)
    total = len(entities)
    page  = [{k: v for k, v in e.items() if k != "event_ids"}
             for e in entities[offset: offset + limit]]
    return {"total": total, "limit": limit, "offset": offset, "entities": page}


@router.post("/map", summary="Normalize and record a graph node as an entity", response_model=EntityMapResponse)
async def map_entity_from_graph(request: Request, body: EntityMapRequest):
    raw_label = (body.label or "").strip()
    raw_id = (body.id or "").strip()
    if not raw_label and raw_id:
        raw_label = _graph_id_to_label(raw_id)
    if not raw_label:
        raise HTTPException(status_code=400, detail="Entity label is required")

    entity_type = _entity_type_from_group(body.group, body.type)
    name = normalize_actor_name(raw_label, entity_type) or raw_label
    entity_id = raw_id if raw_id.startswith(("actor:", "org:")) else _actor_id_from_label(name)
    if entity_id.startswith("org:"):
        entity_id = "actor:" + entity_id.split(":", 1)[-1]

    graph_recorded = False
    graph = getattr(request.app.state, "graph", None)
    if graph and getattr(graph, "available", False):
        try:
            graph.ensure_actor(entity_id, name, entity_type, source=body.source or "graph_click")
            graph_recorded = True
        except Exception as exc:
            logger.warning("Graph entity map failed for %s: %s", entity_id, exc)

    postgres_recorded = False
    if getattr(request.app.state, "db_available", False):
        try:
            from storage.database import OntologyActorModel, get_session
            async with get_session() as session:
                result = await session.execute(
                    select(OntologyActorModel).where(OntologyActorModel.actor_id == entity_id)
                )
                existing = result.scalar_one_or_none()
                now = datetime.now(timezone.utc)
                meta = {
                    "mapped_from": body.source or "graph_click",
                    "graph_node_id": raw_id or entity_id,
                    "graph_group": body.group,
                }
                if existing:
                    existing.canonical_name = existing.canonical_name or name
                    existing.entity_type = existing.entity_type or entity_type
                    existing.last_seen = now
                    existing.meta = {**(existing.meta or {}), **meta}
                else:
                    session.add(OntologyActorModel(
                        actor_id=entity_id,
                        canonical_name=name,
                        entity_type=entity_type,
                        aliases=[raw_label] if raw_label != name else [],
                        first_seen=now,
                        last_seen=now,
                        mention_count=0,
                        source_count=0,
                        influence_score=0.0,
                        meta=meta,
                    ))
            postgres_recorded = True
        except Exception as exc:
            logger.warning("Postgres entity map failed for %s: %s", entity_id, exc)

    return {
        "id": entity_id,
        "name": name,
        "type": entity_type,
        "mapped": True,
        "graph_recorded": graph_recorded,
        "postgres_recorded": postgres_recorded,
    }


@router.get("/{entity_id}/graph", summary="Ego graph for one actor", response_model=EgoGraphResponse)
async def entity_graph(
    entity_id: str,
    request:   Request,
    depth:     int = Query(1, ge=1, le=2),
):
    graph = request.app.state.graph
    if graph.available:
        try:
            result = graph.ego_graph(entity_id, depth=depth)
            if result.get("nodes"):
                nodes = result.get("nodes") or []
                result["evidence"] = {
                    "graph_source": "neo4j_ego",
                    "actor_count": len([n for n in nodes if n.get("group") in {"actor", "organization"}]),
                    "event_count": len([n for n in nodes if n.get("group") == "event"]),
                    "location_count": len([n for n in nodes if n.get("group") == "location"]),
                    "signal_count": len([n for n in nodes if n.get("group") == "signal"]),
                    "narrative_count": len([n for n in nodes if n.get("group") == "narrative"]),
                    "theme_count": len([n for n in nodes if n.get("group") == "theme"]),
                }
                return result
        except Exception as exc:
            logger.warning("Neo4j ego_graph fallback: %s", exc)
    result = _memory_ego_graph(entity_id, depth, request)
    if not result:
        raise HTTPException(status_code=404, detail=f"Entity '{entity_id}' not found")
    return result

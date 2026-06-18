"""
api/routers/objects.py
──────────────────────
Unified ontology object read-model. ONE normalized shape for every modeled
object (event | actor | asset) plus its provenance/lineage. The serving layer,
reports, and copilot read objects through here instead of per-feature snapshot
endpoints — this is the "ontology is the source of truth" entrypoint.

  GET /objects/{type}/{id}          → normalized object envelope
  GET /objects/{type}/{id}/lineage  → where it came from (sources, signals, reasoning)
"""

import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict

logger = logging.getLogger("vision_i.api.objects")
router = APIRouter(tags=["Objects"])

_PG_TYPES = {"event", "actor", "asset"}
_GRAPH_TYPES = {"location", "organization", "org", "theme", "signal", "narrative"}
_TYPES = _PG_TYPES | _GRAPH_TYPES


def _graph_object(graph, obj_type: str, obj_id: str):
    """Build a normalized envelope for graph-only node types (location/theme/signal/etc.)."""
    node = graph.get_node(obj_id) if graph and getattr(graph, "available", False) else None
    if not node:
        return None
    p = node["props"]
    label = p.get("name") or p.get("title") or p.get("label") or obj_id
    geo = None
    if p.get("lat") is not None and p.get("lon") is not None:
        geo = {"lat": p.get("lat"), "lon": p.get("lon"), "name": p.get("name")}
    return {
        "id": obj_id,
        "type": obj_type,
        "title": label,
        "summary": f"{node['type']} node in the knowledge graph",
        "risk": None,
        "geo": geo,
        "properties": {k: v for k, v in p.items() if k != "id"},
        "relationships": [],
        "provenance_count": 0,
    }


class ObjectEnvelope(BaseModel):
    model_config = ConfigDict(extra="allow")


def _num(v: Any) -> Optional[float]:
    try:
        return float(v) if v is not None else None
    except Exception:
        return None


def _actor_node_id(name: str) -> str:
    return f"actor:{(name or '').strip().lower().replace(' ', '_')}"


# ── Object envelopes ────────────────────────────────────────────────────────

async def _event_object(session, event_id: str) -> Optional[Dict[str, Any]]:
    from ontology.views import get_event_detail
    detail = await get_event_detail(session, event_id)
    if not detail:
        return None
    ev = detail["event"]
    sit = detail["situation"]
    loc = ev.get("location") or {}
    rels: List[Dict[str, Any]] = [
        {"type": "MENTIONS", "target_type": "actor",
         "target_id": _actor_node_id(a.get("name")), "label": a.get("name")}
        for a in (ev.get("actors") or [])[:8] if a.get("name")
    ]
    if loc.get("name"):
        rels.append({"type": "OCCURRED_IN", "target_type": "location",
                     "target_id": None, "label": loc.get("name")})
    return {
        "id": ev.get("event_id"),
        "type": "event",
        "title": ev.get("title"),
        "summary": ev.get("description") or ev.get("reasoning") or "",
        "risk": _num(ev.get("risk_score")) or _num(sit.get("priority_score")),
        "geo": {"lat": loc.get("lat"), "lon": loc.get("lon"), "name": loc.get("name")}
               if loc.get("lat") is not None else None,
        "properties": {
            "event_type": ev.get("event_type"),
            "source": ev.get("source"),
            "timestamp": ev.get("timestamp"),
            "sentiment": ev.get("sentiment"),
            "confidence_score": ev.get("confidence_score"),
            "influence_score": ev.get("influence_score"),
            "signal_count": ev.get("signal_count"),
        },
        "relationships": rels,
        "provenance_count": len(ev.get("supporting_signals") or []) + (1 if ev.get("source") else 0),
    }


async def _actor_object(session, actor_id: str, request: Request) -> Optional[Dict[str, Any]]:
    from ontology.views import get_actor_detail
    d = await get_actor_detail(session, actor_id, graph=getattr(request.app.state, "graph", None))
    if not d:
        return None
    infl = d.get("influence_score") or 0
    return {
        "id": d.get("id"),
        "type": "actor",
        "title": d.get("name"),
        "summary": f"{d.get('type', 'entity')} · {d.get('mention_count', 0)} mentions · influence {infl:.2f}",
        "risk": _num(d.get("influence_score")),
        "geo": None,
        "properties": {
            "entity_type": d.get("type"),
            "mention_count": d.get("mention_count"),
            "event_count": d.get("event_count"),
            "source_count": d.get("source_count"),
            "influence_score": d.get("influence_score"),
            "sentiment_score": d.get("sentiment_score"),
            "aliases": d.get("aliases"),
        },
        "relationships": [
            {"type": "INVOLVED_IN", "target_type": "event",
             "target_id": e.get("id"), "label": e.get("title")}
            for e in (d.get("recent_events") or [])[:10] if e.get("id")
        ],
        "provenance_count": (d.get("event_count") or 0) + (d.get("signal_count") or 0),
    }


async def _asset_object(asset_id: str) -> Optional[Dict[str, Any]]:
    from storage.asset_repo import AssetRepository
    a = await AssetRepository().get_asset(asset_id)
    if not a:
        return None
    lat, lon = a.get("last_lat"), a.get("last_lon")
    name = a.get("name") or a.get("callsign") or a.get("identifier") or a.get("asset_id")
    return {
        "id": a.get("asset_id"),
        "type": "asset",
        "title": name,
        "summary": f"{a.get('asset_type', 'asset')} · last seen {a.get('last_seen') or 'n/a'}",
        "risk": None,
        "geo": {"lat": lat, "lon": lon, "name": name} if lat is not None else None,
        "properties": {
            "asset_type": a.get("asset_type"),
            "callsign": a.get("callsign"),
            "identifier": a.get("identifier"),
            "origin_country": a.get("origin_country"),
            "last_speed": a.get("last_speed"),
            "last_altitude": a.get("last_altitude"),
            "last_heading": a.get("last_heading"),
            "last_seen": a.get("last_seen"),
            "on_ground": a.get("on_ground"),
        },
        "relationships": [],
        "provenance_count": len(a.get("track_history") or []),
    }


# ── Provenance / lineage ────────────────────────────────────────────────────

async def _event_lineage(session, event_id: str) -> Optional[Dict[str, Any]]:
    from ontology.views import get_event_detail
    detail = await get_event_detail(session, event_id)
    if not detail:
        return None
    ev = detail["event"]
    sources = []
    if ev.get("source"):
        sources.append({"source": ev.get("source"), "ref": ev.get("source_id"),
                        "url": ev.get("url"), "kind": "feed"})
    return {
        "id": ev.get("event_id"), "type": "event", "title": ev.get("title"),
        "sources": sources,
        "supporting_signals": ev.get("supporting_signals") or [],
        "reasoning": ev.get("reasoning"),
        "derived": [
            f"confidence_score={ev.get('confidence_score')}",
            f"risk_score={ev.get('risk_score')}",
            f"influence_score={ev.get('influence_score')}",
        ],
        "related_events": [],
    }


async def _actor_lineage(session, actor_id: str, request: Request) -> Optional[Dict[str, Any]]:
    from ontology.views import get_actor_detail
    d = await get_actor_detail(session, actor_id, graph=getattr(request.app.state, "graph", None))
    if not d:
        return None
    recent = d.get("recent_events") or []
    sources = [
        {"source": (e.get("source_mix") or [None])[0], "ref": e.get("id"),
         "url": None, "kind": "event"}
        for e in recent[:10] if e.get("id")
    ]
    return {
        "id": d.get("id"), "type": "actor", "title": d.get("name"),
        "sources": sources,
        "supporting_signals": d.get("signals") or [],
        "reasoning": None,
        "derived": [
            f"influence_score={d.get('influence_score')}",
            f"mention_count={d.get('mention_count')}",
            f"narrative_count={d.get('narrative_count')}",
        ],
        "related_events": [{"id": e.get("id"), "title": e.get("title")} for e in recent[:10]],
    }


async def _asset_lineage(asset_id: str) -> Optional[Dict[str, Any]]:
    from storage.asset_repo import AssetRepository
    a = await AssetRepository().get_asset(asset_id)
    if not a:
        return None
    track = a.get("track_history") or []
    name = a.get("name") or a.get("callsign") or a.get("identifier") or a.get("asset_id")
    return {
        "id": a.get("asset_id"), "type": "asset", "title": name,
        "sources": [{"source": a.get("asset_type"), "ref": a.get("identifier"),
                     "url": None, "kind": "telemetry"}],
        "supporting_signals": track[-10:],
        "reasoning": None,
        "derived": [f"last_seen={a.get('last_seen')}", f"on_ground={a.get('on_ground')}"],
        "related_events": [],
    }


# ── Routes (lineage defined first so it isn't shadowed) ─────────────────────

@router.get("/{obj_type}/{obj_id}/neighbors", summary="Typed adjacency (drill anywhere)")
async def get_object_neighbors(obj_type: str, obj_id: str, request: Request,
                               limit: int = Query(80, ge=1, le=300)):
    """All linked objects of any graph node (event/actor/location/org/theme/narrative/
    signal). The object-explorer expands these to traverse the full knowledge graph."""
    graph = getattr(request.app.state, "graph", None)
    if graph is None or not getattr(graph, "available", False):
        raise HTTPException(status_code=503, detail="Knowledge graph unavailable")
    return graph.node_neighbors(obj_id, limit=limit)


@router.get("/{obj_type}/{obj_id}/lineage", summary="Object provenance / lineage",
            response_model=ObjectEnvelope)
async def get_object_lineage(obj_type: str, obj_id: str, request: Request):
    t = obj_type.lower()
    if t not in _TYPES:
        raise HTTPException(status_code=400, detail=f"type must be one of {sorted(_TYPES)}")
    if t in _GRAPH_TYPES:
        # Graph-only nodes: minimal lineage (their provenance is their neighbor events).
        node = _graph_object(getattr(request.app.state, "graph", None), t, obj_id)
        if node is None:
            raise HTTPException(status_code=404, detail=f"{obj_type} '{obj_id}' not found")
        return {"id": obj_id, "type": t, "title": node.get("title"),
                "sources": [], "supporting_signals": [], "reasoning": None,
                "derived": [], "related_events": []}
    from storage.database import get_session
    if t == "asset":
        result = await _asset_lineage(obj_id)
    else:
        async with get_session() as session:
            result = await (_event_lineage(session, obj_id) if t == "event"
                            else _actor_lineage(session, obj_id, request))
    if result is None:
        raise HTTPException(status_code=404, detail=f"{obj_type} '{obj_id}' not found")
    return result


@router.get("/{obj_type}/{obj_id}", summary="Unified ontology object",
            response_model=ObjectEnvelope)
async def get_object(obj_type: str, obj_id: str, request: Request):
    t = obj_type.lower()
    if t not in _TYPES:
        raise HTTPException(status_code=400, detail=f"type must be one of {sorted(_TYPES)}")
    if t in _GRAPH_TYPES:
        obj = _graph_object(getattr(request.app.state, "graph", None), t, obj_id)
    elif t == "asset":
        obj = await _asset_object(obj_id)
    else:
        from storage.database import get_session
        async with get_session() as session:
            obj = await (_event_object(session, obj_id) if t == "event"
                         else _actor_object(session, obj_id, request))
    if obj is None:
        raise HTTPException(status_code=404, detail=f"{obj_type} '{obj_id}' not found")
    return obj

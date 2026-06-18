"""
api/routers/ontology.py
-----------------------
Ontology-first serving layer.

These routes expose precomputed views of modeled reality rather than raw source
feeds. They are intended for the .NET serving layer and decision UI.
"""

import logging

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field
from typing import Any, Dict, List, Optional

# Heavy imports deferred to function bodies so this module can be imported
# in test environments without pgvector / Neo4j driver present.
# from ontology.views import ...   ← moved inside route functions below

logger = logging.getLogger("vision_i.api.ontology")
router = APIRouter(tags=["Ontology"])

# ── Response schemas ───────────────────────────────────────────────────────

class OntologyItemSchema(BaseModel):
    model_config = ConfigDict(extra="allow")

class OntologyCypherResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

class CypherQueryRequest(BaseModel):
    query: str
    parameters: Optional[Dict[str, Any]] = None

# Allowlist: only clauses that perform reads are permitted.
_READ_ONLY_STARTERS = frozenset({"MATCH", "CALL", "WITH", "UNWIND", "RETURN", "OPTIONAL"})
_WRITE_KEYWORDS = frozenset({
    "CREATE", "MERGE", "SET", "DELETE", "DETACH", "REMOVE", "DROP", "FOREACH",
    "LOAD", "IMPORT",
})
# APOC procedures that write/mutate data
_APOC_WRITE_PROCS = frozenset({
    "apoc.create", "apoc.merge", "apoc.refactor", "apoc.periodic",
    "apoc.schema", "apoc.trigger", "apoc.bolt", "apoc.export",
    "apoc.import", "apoc.load.json", "apoc.load.csv", "apoc.load.xml",
    "apoc.cypher.runmany", "apoc.cypher.doit", "apoc.nodes.delete",
    "apoc.rels.delete", "apoc.meta.schema",
})
_MAX_QUERY_LEN = 2048


def _validate_cypher_read_only(query: str) -> Optional[str]:
    """Return an error message if the query contains write operations, else None."""
    if not query or not query.strip():
        return "Query must not be empty"
    if len(query) > _MAX_QUERY_LEN:
        return f"Query too long (max {_MAX_QUERY_LEN} chars)"

    upper = query.upper()

    # Block write keywords (whole-word match via token scan)
    upper_tokens = {t.strip("(){}[], \t\n;").upper() for t in query.split() if t.strip()}
    bad = next((kw for kw in _WRITE_KEYWORDS if kw in upper_tokens), None)
    if bad:
        return f"Write operations are not permitted: {bad}"

    # Block APOC write procedures (substring match on lowercased query)
    lower = query.lower()
    bad_apoc = next((p for p in _APOC_WRITE_PROCS if p in lower), None)
    if bad_apoc:
        return f"APOC write procedure not permitted: {bad_apoc}"

    # Must start with a read-only clause
    first = query.strip().split()[0].upper()
    if first not in _READ_ONLY_STARTERS:
        return f"Query must start with a read-only clause (MATCH, CALL, WITH, UNWIND, RETURN, OPTIONAL). Got: {first!r}"

    return None

@router.post("/cypher", summary="Execute raw read-only Cypher queries against the Knowledge Graph", response_model=OntologyCypherResponse)
async def execute_cypher_endpoint(request: Request, payload: CypherQueryRequest):
    graph = getattr(request.app.state, "graph", None)
    if graph is None or not graph.available:
        raise HTTPException(status_code=503, detail="Neo4j Graph Database is currently unavailable.")

    err = _validate_cypher_read_only(payload.query)
    if err:
        raise HTTPException(status_code=403, detail=err)

    result = graph.execute_cypher(payload.query, payload.parameters)
    if "error" in result:
        raise HTTPException(status_code=400, detail=result["error"])

    return result


@router.get("/overview", summary="Priority-ranked emerging situations", response_model=OntologyItemSchema)
async def ontology_overview(limit: int = Query(12, ge=1, le=200)):
    from ontology.views import build_situation_overview
    from storage.database import get_session
    async with get_session() as session:
        return await build_situation_overview(session, limit=limit)


@router.get("/summary", summary="Alias: overview of priority situations")
async def ontology_summary(limit: int = Query(12, ge=1, le=200)):
    return await ontology_overview(limit=limit)


@router.get("/events/{event_id}", summary="Ontology event detail with reasoning", response_model=OntologyItemSchema)
async def ontology_event(event_id: str):
    from ontology.views import get_event_detail
    from storage.database import get_session
    async with get_session() as session:
        result = await get_event_detail(session, event_id)
        if result is None:
            raise HTTPException(status_code=404, detail=f"Event '{event_id}' not found")
        return result


@router.get("/actors/{actor_id}", summary="Ontology actor detail and influence", response_model=OntologyItemSchema)
async def ontology_actor(actor_id: str, request: Request):
    from ontology.views import get_actor_detail
    from storage.database import get_session
    async with get_session() as session:
        result = await get_actor_detail(
            session,
            actor_id,
            graph=getattr(request.app.state, "graph", None),
        )
        if result is None:
            raise HTTPException(status_code=404, detail=f"Actor '{actor_id}' not found")
        return result


@router.get("/graph", summary="Lightweight ontology graph snapshot", response_model=OntologyItemSchema)
async def ontology_graph(limit: int = Query(10, ge=3, le=50)):
    from ontology.views import build_graph_snapshot
    from storage.database import get_session
    async with get_session() as session:
        return await build_graph_snapshot(session, limit=limit)


@router.get("/operations/overview", summary="Decision-oriented operations queue", response_model=OntologyItemSchema)
async def ontology_operations_overview(limit: int = Query(8, ge=1, le=25)):
    from ontology.operations import build_operations_overview
    from storage.database import get_session
    async with get_session() as session:
        return await build_operations_overview(session, limit=limit)

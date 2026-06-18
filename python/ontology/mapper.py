"""
ontology/mapper.py
â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
Maps raw VisionEvents into formal ontology entities.

The OntologyMapper is the bridge between the ingestion/NLP layers (which
produce VisionEvent dicts) and the ontology layer (which stores typed,
validated entities in Neo4j and PostgreSQL).

Pipeline position:
  raw events â†’ NLP enrichment â†’ OntologyMapper â†’ graph/DB write
"""

import logging
from collections import defaultdict
from typing import Any, Dict, List, Optional, Set

from core.schema import VisionEvent
from ontology.schema import (
    EntityType,
    OntologyActor,
    OntologyBatch,
    OntologyEvent,
    OntologyLocation,
    OntologyOrganization,
    OntologyRelationship,
    RelationshipType,
)

logger = logging.getLogger("vision_i.ontology.mapper")


def _actor_id(name: str) -> str:
    return f"actor:{name.lower().replace(' ', '_')}"


def _location_id(name: str) -> str:
    return f"loc:{name.lower().replace(' ', '_')}"


def _entity_type(raw_type: str) -> EntityType:
    mapping = {
        "PERSON": EntityType.PERSON,
        "PER": EntityType.PERSON,
        "ORG": EntityType.ORG,
        "LOC": EntityType.LOCATION,
        "LOCATION": EntityType.LOCATION,
        "VEHICLE": EntityType.VEHICLE,
    }
    return mapping.get(raw_type.upper(), EntityType.UNKNOWN)


class OntologyMapper:
    """
    Maps a batch of VisionEvents into a validated OntologyBatch.

    Usage:
        mapper = OntologyMapper()
        batch = mapper.map_events(events)
        # batch.actors, batch.events, batch.locations, batch.organizations, batch.relationships
    """

    def map_events(self, events: List[VisionEvent]) -> OntologyBatch:
        """
        Transform raw VisionEvents into formal ontology entities.

        Steps:
          1. Extract and deduplicate actors
          2. Extract and deduplicate locations
          3. Classify ORG actors as Organization entities
          4. Map events to ontology events
          5. Build relationship tuples
        """
        actor_map: Dict[str, OntologyActor] = {}
        location_map: Dict[str, OntologyLocation] = {}
        org_map: Dict[str, OntologyOrganization] = {}
        ontology_events: List[OntologyEvent] = []
        relationships: List[OntologyRelationship] = []

        for event in events:
            event_id = event.get("event_id", "")
            if not event_id:
                continue
            actor_ids_for_event: List[str] = []
            sources_for_actors: Dict[str, Set[str]] = defaultdict(set)

            for actor in event.get("actors") or []:
                name = (actor.get("name") or "").strip()
                if not name or len(name) < 2:
                    continue

                aid = _actor_id(name)
                etype = _entity_type(actor.get("type", "UNKNOWN"))
                actor_ids_for_event.append(aid)
                sources_for_actors[aid].add(event.get("source", ""))

                if aid in actor_map:
                    actor_map[aid].mention_count += 1
                    actor_map[aid].last_seen = event.get("timestamp")
                    if name not in actor_map[aid].aliases and name != actor_map[aid].canonical_name:
                        actor_map[aid].aliases.append(name)
                else:
                    actor_map[aid] = OntologyActor(
                        id=aid,
                        canonical_name=name,
                        entity_type=etype,
                        first_seen=event.get("timestamp"),
                        last_seen=event.get("timestamp"),
                        mention_count=1,
                        source_count=1,
                    )

                # Classify ORG actors as organizations
                if etype == EntityType.ORG and aid not in org_map:
                    org_map[aid] = OntologyOrganization(
                        id=aid,
                        canonical_name=name,
                    )
            loc = event.get("location") or {}
            extras = event.get("extras") or {}
            loc_name = (loc.get("name") or "").strip()
            location_id: Optional[str] = None
            if loc_name or (loc.get("lat") and loc.get("lon")):
                lid = _location_id(loc_name or f"{loc.get('lat','')},{loc.get('lon','')}")
                location_id = lid
                if lid in location_map:
                    location_map[lid].event_count += 1
                else:
                    location_map[lid] = OntologyLocation(
                        id=lid,
                        name=loc_name or "Unknown",
                        lat=loc.get("lat"),
                        lon=loc.get("lon"),
                        country=loc.get("country") or extras.get("country"),
                        event_count=1,
                    )
            sentiment = event.get("sentiment") or {}
            theme_ids = [
                f"theme:{t.lower().replace(' ', '_')}"
                for t in (event.get("tags") or [])
                if t
            ]
            ontology_events.append(OntologyEvent(
                id=event_id,
                title=event.get("title", ""),
                source=event.get("source", ""),
                event_type=event.get("event_type", "news"),
                timestamp=event.get("timestamp", ""),
                sentiment_score=sentiment.get("score"),
                actor_ids=actor_ids_for_event,
                location_id=location_id,
                theme_ids=theme_ids,
            ))
            # Actor -> Event: PARTICIPATED_IN
            for aid in actor_ids_for_event:
                relationships.append(OntologyRelationship(
                    source_id=aid,
                    target_id=event_id,
                    relationship_type=RelationshipType.PARTICIPATED_IN,
                ))

            # Event -> Location: OCCURRED_IN
            if location_id:
                relationships.append(OntologyRelationship(
                    source_id=event_id,
                    target_id=location_id,
                    relationship_type=RelationshipType.OCCURRED_IN,
                ))

            # Actor <-> Actor: CO_MENTIONED_WITH (within same event)
            for i in range(len(actor_ids_for_event)):
                for j in range(i + 1, len(actor_ids_for_event)):
                    relationships.append(OntologyRelationship(
                        source_id=actor_ids_for_event[i],
                        target_id=actor_ids_for_event[j],
                        relationship_type=RelationshipType.CO_MENTIONED_WITH,
                    ))

        # Update source_count for actors
        for aid, actor in actor_map.items():
            # Count unique sources across all events mentioning this actor
            # (approximation based on what we tracked)
            actor.source_count = max(1, len(sources_for_actors.get(aid, set())))

        batch = OntologyBatch(
            actors=list(actor_map.values()),
            events=ontology_events,
            locations=list(location_map.values()),
            organizations=list(org_map.values()),
            relationships=relationships,
        )

        logger.info(
            "Ontology mapped: %d actors, %d events, %d locations, %d orgs, %d relationships",
            len(batch.actors), len(batch.events), len(batch.locations),
            len(batch.organizations), len(batch.relationships),
        )
        return batch


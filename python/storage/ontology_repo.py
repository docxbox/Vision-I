"""
storage/ontology_repo.py
─────────────────────────
Persists ontology entities to PostgreSQL tables.

These tables serve as a fast, queryable cache of the Neo4j ontology,
allowing the serving layer to list/filter entities without Neo4j queries.
"""

import logging
from datetime import datetime, timezone
from typing import List

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ontology.schema import OntologyActor, OntologyBatch, OntologyLocation
from storage.database import OntologyActorModel, OntologyLocationModel

logger = logging.getLogger("vision_i.storage.ontology_repo")


class OntologyRepository:
    """Syncs ontology entities from the mapper into PostgreSQL."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def upsert_actors(self, actors: List[OntologyActor]) -> int:
        """Upsert ontology actors. Returns count of upserted rows."""
        count = 0
        for actor in actors:
            result = await self._session.execute(
                select(OntologyActorModel)
                .where(OntologyActorModel.actor_id == actor.id)
            )
            existing = result.scalar_one_or_none()

            if existing:
                existing.canonical_name = actor.canonical_name
                existing.entity_type = actor.entity_type.value
                existing.mention_count = max(existing.mention_count, actor.mention_count)
                existing.source_count = max(existing.source_count, actor.source_count)
                existing.last_seen = datetime.now(timezone.utc)
                if actor.aliases:
                    current = existing.aliases or []
                    merged = list(set(current + actor.aliases))
                    existing.aliases = merged
                if actor.influence_score is not None:
                    existing.influence_score = actor.influence_score
            else:
                self._session.add(OntologyActorModel(
                    actor_id=actor.id,
                    canonical_name=actor.canonical_name,
                    entity_type=actor.entity_type.value,
                    aliases=actor.aliases,
                    first_seen=datetime.now(timezone.utc),
                    last_seen=datetime.now(timezone.utc),
                    mention_count=actor.mention_count,
                    source_count=actor.source_count,
                    influence_score=actor.influence_score,
                ))
            count += 1

        await self._session.flush()
        return count

    async def upsert_locations(self, locations: List[OntologyLocation]) -> int:
        """Upsert ontology locations. Returns count."""
        count = 0
        for loc in locations:
            result = await self._session.execute(
                select(OntologyLocationModel)
                .where(OntologyLocationModel.location_id == loc.id)
            )
            existing = result.scalar_one_or_none()

            if existing:
                existing.event_count = max(existing.event_count, loc.event_count)
                if loc.lat is not None:
                    existing.lat = loc.lat
                if loc.lon is not None:
                    existing.lon = loc.lon
                if loc.country:
                    existing.country = loc.country
            else:
                self._session.add(OntologyLocationModel(
                    location_id=loc.id,
                    name=loc.name,
                    lat=loc.lat,
                    lon=loc.lon,
                    country=loc.country,
                    event_count=loc.event_count,
                ))
            count += 1

        await self._session.flush()
        return count

    async def upsert_batch(self, batch: OntologyBatch) -> dict:
        """Upsert an entire ontology batch. Returns summary counts."""
        actors_count = await self.upsert_actors(batch.actors)
        locations_count = await self.upsert_locations(batch.locations)
        logger.info(
            "Ontology persisted: %d actors, %d locations",
            actors_count, locations_count,
        )
        return {"actors": actors_count, "locations": locations_count}

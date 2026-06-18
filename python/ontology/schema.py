"""
ontology/schema.py
───────────────────
Formal ontology entity definitions for Vision-I.

These Pydantic models define the canonical schema for all entities in the
knowledge graph. Every application layer reads through these types — they
are the "single source of truth" for what an Actor, Event, Location, or
Organization looks like.

This replaces the informal TypedDicts and ad-hoc dict structures that were
previously used for graph writes.
"""

from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, field_validator


class EntityType(str, Enum):
    PERSON = "PERSON"
    ORG = "ORG"
    LOCATION = "LOC"
    VEHICLE = "VEHICLE"
    ASSET = "ASSET"
    SIGNAL = "SIGNAL"
    UNKNOWN = "UNKNOWN"


class RelationshipType(str, Enum):
    PARTICIPATED_IN = "PARTICIPATED_IN"
    OCCURRED_IN = "OCCURRED_IN"
    CO_MENTIONED_WITH = "CO_MENTIONED_WITH"
    ASSOCIATED_WITH = "ASSOCIATED_WITH"
    IS_A = "IS_A"
    INFLUENCES = "INFLUENCES"
    IMPLICATES = "IMPLICATES"
    CAUSED = "CAUSED"
    SUPPORTS = "SUPPORTS"
    AFFECTS = "AFFECTS"
    PART_OF = "PART_OF"


class OntologyActor(BaseModel):
    """An actor (person, organization, vehicle) in the knowledge graph."""
    id: str
    canonical_name: str
    aliases: List[str] = []
    entity_type: EntityType = EntityType.UNKNOWN
    first_seen: Optional[str] = None
    last_seen: Optional[str] = None
    mention_count: int = 1
    source_count: int = 1
    influence_score: Optional[float] = None
    meta: Dict[str, Any] = {}

    @field_validator("canonical_name")
    @classmethod
    def name_not_empty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("canonical_name cannot be empty")
        return v.strip()


class OntologyEvent(BaseModel):
    """An event (news, disaster, social post) in the knowledge graph."""
    id: str
    title: str
    source: str
    event_type: str
    timestamp: str
    sentiment_score: Optional[float] = None
    actor_ids: List[str] = []
    location_id: Optional[str] = None
    theme_ids: List[str] = []


class OntologyLocation(BaseModel):
    """A geographic location in the knowledge graph."""
    id: str
    name: str
    lat: Optional[float] = None
    lon: Optional[float] = None
    country: Optional[str] = None
    event_count: int = 0


class OntologyOrganization(BaseModel):
    """
    An organization entity — a specialisation of Actor with type=ORG.
    Given first-class treatment for sector/country classification.
    """
    id: str
    canonical_name: str
    aliases: List[str] = []
    sector: Optional[str] = None       # government, media, military, tech, finance
    country: Optional[str] = None
    influence_score: Optional[float] = None


class OntologySignal(BaseModel):
    """A normalised, embedded signal in the knowledge graph."""
    id: str
    source: str
    signal_type: str = "raw"
    title: str
    confidence: float = 0.5
    timestamp: Optional[str] = None
    cluster_id: Optional[str] = None
    source_event_id: Optional[str] = None


class OntologyAsset(BaseModel):
    """A tracked physical asset (aircraft, vessel, facility)."""
    id: str
    asset_type: str  # aircraft, vessel, facility
    name: Optional[str] = None
    callsign: Optional[str] = None
    identifier: Optional[str] = None  # ICAO24, MMSI
    origin_country: Optional[str] = None
    last_position: Optional[Dict[str, Any]] = None  # {lat, lon, altitude}


class OntologyRelationship(BaseModel):
    """A typed, weighted edge between two ontology entities."""
    source_id: str
    target_id: str
    relationship_type: RelationshipType
    weight: float = 1.0
    meta: Dict[str, Any] = {}


class OntologyBatch(BaseModel):
    """
    The output of OntologyMapper.map_events().
    Contains all entities and relationships extracted from a batch of events.
    """
    actors: List[OntologyActor] = []
    events: List[OntologyEvent] = []
    locations: List[OntologyLocation] = []
    organizations: List[OntologyOrganization] = []
    signals: List[OntologySignal] = []
    assets: List[OntologyAsset] = []
    relationships: List[OntologyRelationship] = []

    @property
    def total_entities(self) -> int:
        return (
            len(self.actors) + len(self.events) + len(self.locations)
            + len(self.organizations) + len(self.signals) + len(self.assets)
        )

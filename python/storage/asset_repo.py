"""
storage/asset_repo.py
──────────────────────
Asset persistence for tracked physical assets (aircraft, vessels, etc.).
"""

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from sqlalchemy.dialects.postgresql import insert as pg_insert

from core.utils import to_iso
from storage.database import AssetModel, get_session

logger = logging.getLogger("vision_i.storage.asset_repo")


def _asset_list_columns():
    """load_only the columns list/map views need — excludes the heavy track_history JSON."""
    from sqlalchemy.orm import load_only
    return load_only(
        AssetModel.asset_id, AssetModel.asset_type, AssetModel.name,
        AssetModel.callsign, AssetModel.identifier, AssetModel.origin_country,
        AssetModel.last_lat, AssetModel.last_lon, AssetModel.last_altitude,
        AssetModel.last_speed, AssetModel.last_heading, AssetModel.last_seen,
        AssetModel.on_ground, AssetModel.meta,
    )


def _parse_dt(value: Any) -> Optional[datetime]:
    """Coerce timestamp inputs into database-friendly datetimes."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(to_iso(value).replace("Z", "+00:00"))
    except Exception:
        return None


class AssetRepository:
    """CRUD operations for the assets table."""

    async def upsert_assets(self, assets: List[Dict[str, Any]]) -> int:
        """
        Bulk upsert assets. On conflict (asset_id), update position fields.
        Returns the number of rows affected.
        """
        if not assets:
            return 0

        rows = [
            {
                "asset_id":       asset["asset_id"],
                "asset_type":     asset["asset_type"],
                "name":           asset.get("name"),
                "callsign":       asset.get("callsign"),
                "identifier":     asset.get("identifier"),
                "origin_country": asset.get("origin_country"),
                "last_lat":       asset.get("last_lat"),
                "last_lon":       asset.get("last_lon"),
                "last_altitude":  asset.get("last_altitude"),
                "last_speed":     asset.get("last_speed"),
                "last_heading":   asset.get("last_heading"),
                "last_seen":      _parse_dt(asset.get("last_seen")),
                "on_ground":      asset.get("on_ground"),
                "meta":           asset.get("meta", {}),
            }
            for asset in assets
        ]

        async with get_session() as session:
            stmt = pg_insert(AssetModel).values(rows).on_conflict_do_update(
                index_elements=["asset_id"],
                set_={
                    "last_lat":      pg_insert(AssetModel).excluded.last_lat,
                    "last_lon":      pg_insert(AssetModel).excluded.last_lon,
                    "last_altitude": pg_insert(AssetModel).excluded.last_altitude,
                    "last_speed":    pg_insert(AssetModel).excluded.last_speed,
                    "last_heading":  pg_insert(AssetModel).excluded.last_heading,
                    "last_seen":     pg_insert(AssetModel).excluded.last_seen,
                    "on_ground":     pg_insert(AssetModel).excluded.on_ground,
                    "callsign":      pg_insert(AssetModel).excluded.callsign,
                    "meta":          pg_insert(AssetModel).excluded.meta,
                },
            )
            result = await session.execute(stmt)
            count = result.rowcount

        logger.info("Upserted %d assets (batch)", count)
        return count

    async def get_assets(
        self,
        asset_type: Optional[str] = None,
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        """List assets, optionally filtered by type."""
        from sqlalchemy import select

        async with get_session() as session:
            # load_only excludes track_history (a large per-asset JSON array). Loading it for
            # thousands of rows was making this query take ~19s; the list view never uses it.
            stmt = (
                select(AssetModel)
                .options(_asset_list_columns())
                .order_by(AssetModel.last_seen.desc())
                .limit(limit)
            )
            if asset_type:
                stmt = stmt.where(AssetModel.asset_type == asset_type)
            rows = (await session.execute(stmt)).scalars().all()
            return [
                {
                    "asset_id":       r.asset_id,
                    "asset_type":     r.asset_type,
                    "name":           r.name,
                    "callsign":       r.callsign,
                    "identifier":     r.identifier,
                    "origin_country": r.origin_country,
                    "last_lat":       r.last_lat,
                    "last_lon":       r.last_lon,
                    "last_altitude":  r.last_altitude,
                    "last_speed":     r.last_speed,
                    "last_heading":   r.last_heading,
                    "last_seen":      r.last_seen.isoformat() if r.last_seen else None,
                    "on_ground":      r.on_ground,
                    "meta":           r.meta or {},
                }
                for r in rows
            ]

    async def get_asset(self, asset_id: str) -> Optional[Dict[str, Any]]:
        """Get a single asset by ID."""
        from sqlalchemy import select

        async with get_session() as session:
            stmt = select(AssetModel).where(AssetModel.asset_id == asset_id)
            r = (await session.execute(stmt)).scalar_one_or_none()
            if not r:
                return None
            return {
                "asset_id":       r.asset_id,
                "asset_type":     r.asset_type,
                "name":           r.name,
                "callsign":       r.callsign,
                "identifier":     r.identifier,
                "origin_country": r.origin_country,
                "last_lat":       r.last_lat,
                "last_lon":       r.last_lon,
                "last_altitude":  r.last_altitude,
                "last_speed":     r.last_speed,
                "last_heading":   r.last_heading,
                "last_seen":      r.last_seen.isoformat() if r.last_seen else None,
                "on_ground":      r.on_ground,
                "track_history":  r.track_history or [],
                "meta":           r.meta or {},
            }

    async def get_assets_in_bbox(
        self,
        min_lat: Optional[float] = None,
        max_lat: Optional[float] = None,
        min_lon: Optional[float] = None,
        max_lon: Optional[float] = None,
        asset_type: Optional[str] = None,
        limit: int = 500,
    ) -> List[Dict[str, Any]]:
        """List assets whose last known position falls within a bounding box."""
        from sqlalchemy import select, and_

        async with get_session() as session:
            stmt = (
                select(AssetModel)
                .options(_asset_list_columns())
                .order_by(AssetModel.last_seen.desc())
                .limit(limit)
            )
            conditions = [AssetModel.last_lat.isnot(None), AssetModel.last_lon.isnot(None)]
            if min_lat is not None:
                conditions.append(AssetModel.last_lat >= min_lat)
            if max_lat is not None:
                conditions.append(AssetModel.last_lat <= max_lat)
            if min_lon is not None:
                conditions.append(AssetModel.last_lon >= min_lon)
            if max_lon is not None:
                conditions.append(AssetModel.last_lon <= max_lon)
            if asset_type:
                conditions.append(AssetModel.asset_type == asset_type)
            if conditions:
                stmt = stmt.where(and_(*conditions))
            rows = (await session.execute(stmt)).scalars().all()
            return [
                {
                    "asset_id":       r.asset_id,
                    "asset_type":     r.asset_type,
                    "name":           r.name,
                    "callsign":       r.callsign,
                    "identifier":     r.identifier,
                    "origin_country": r.origin_country,
                    "last_lat":       r.last_lat,
                    "last_lon":       r.last_lon,
                    "last_altitude":  r.last_altitude,
                    "last_speed":     r.last_speed,
                    "last_heading":   r.last_heading,
                    "last_seen":      r.last_seen.isoformat() if r.last_seen else None,
                    "on_ground":      r.on_ground,
                    "meta":           r.meta or {},
                }
                for r in rows
            ]

    async def count_assets(self) -> Dict[str, int]:
        """Count assets by type."""
        from sqlalchemy import text

        async with get_session() as session:
            result = await session.execute(text(
                "SELECT asset_type, COUNT(*) FROM assets GROUP BY asset_type"
            ))
            return {row[0]: row[1] for row in result}

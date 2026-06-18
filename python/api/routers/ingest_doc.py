"""
api/routers/ingest_doc.py
-------------------------
Document upload ingestion (PDF/TXT → NLP pipeline → events table).

POST /ingest/document  (multipart, file=<PDF or TXT>)
"""
from __future__ import annotations

import io
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Dict

from fastapi import APIRouter, File, HTTPException, Request, UploadFile
from pydantic import BaseModel, ConfigDict

logger = logging.getLogger("vision_i.api.ingest_doc")
router = APIRouter(tags=["Ingest"])

_MAX_SIZE_BYTES = 10 * 1024 * 1024  # 10 MB
_ALLOWED_TYPES  = {"application/pdf", "text/plain"}


class DocumentIngestResponse(BaseModel):
    model_config = ConfigDict(extra="allow")
    job_id: str
    filename: str
    pages: int
    events_created: int
    status: str


def _extract_text_pdf(data: bytes) -> tuple[str, int]:
    try:
        import pdfplumber
        with pdfplumber.open(io.BytesIO(data)) as pdf:
            pages = len(pdf.pages)
            text = "\n".join(p.extract_text() or "" for p in pdf.pages)
        return text, pages
    except ImportError:
        raise HTTPException(
            status_code=501,
            detail="PDF ingestion requires pdfplumber. Install with: pip install pdfplumber"
        )


def _extract_text_txt(data: bytes) -> tuple[str, int]:
    return data.decode("utf-8", errors="replace"), 1


def _text_to_events(text: str, filename: str, job_id: str) -> list[Dict[str, Any]]:
    # Split into ~500-word chunks — each becomes one event.
    words = text.split()
    chunks, chunk_size = [], 500
    for i in range(0, len(words), chunk_size):
        chunks.append(" ".join(words[i: i + chunk_size]))

    events = []
    for i, chunk in enumerate(chunks[:20]):  # cap at 20 events per document
        events.append({
            "event_id": f"doc-{job_id}-{i}",
            "source":   "document",
            "event_type": "analyst_report",
            "title":    f"[{filename}] segment {i + 1}/{len(chunks)}",
            "body":     chunk,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "meta": {"filename": filename, "chunk_index": i, "job_id": job_id},
        })
    return events


@router.post("/document", response_model=DocumentIngestResponse,
             summary="Upload PDF or TXT document for NLP ingestion")
async def ingest_document(request: Request, file: UploadFile = File(...)):
    if file.content_type not in _ALLOWED_TYPES and not (
        file.filename or "").lower().endswith((".pdf", ".txt")):
        raise HTTPException(status_code=415, detail="Only PDF and TXT files are supported")

    data = await file.read()
    if len(data) > _MAX_SIZE_BYTES:
        raise HTTPException(status_code=413, detail="File too large (max 10 MB)")

    filename = file.filename or "upload"
    job_id   = str(uuid.uuid4())[:16]

    # Extract text
    if filename.lower().endswith(".pdf") or file.content_type == "application/pdf":
        text, pages = _extract_text_pdf(data)
    else:
        text, pages = _extract_text_txt(data)

    if not text.strip():
        raise HTTPException(status_code=422, detail="No text content extracted from document")

    # Build raw events
    raw_events = _text_to_events(text, filename, job_id)

    # Run NLP pipeline if available
    nlp = getattr(request.app.state, "nlp", None)
    if nlp:
        try:
            import asyncio
            loop = asyncio.get_running_loop()
            raw_events = await loop.run_in_executor(None, lambda: nlp.process(raw_events))
        except Exception as exc:
            logger.warning("NLP pipeline failed for document %s: %s", filename, exc)

    # Persist
    events_created = 0
    try:
        from storage.database import AsyncSessionFactory
        from storage.event_repo import EventRepository
        async with AsyncSessionFactory() as session:
            repo = EventRepository(session)
            await repo.upsert_many(raw_events)
            await session.commit()
            events_created = len(raw_events)
    except Exception as exc:
        logger.warning("Document events persist failed: %s", exc)

    return DocumentIngestResponse(
        job_id=job_id,
        filename=filename,
        pages=pages,
        events_created=events_created,
        status="complete" if events_created else "partial",
    )

"""
api/routers/reports.py
----------------------
Intelligence report export.
GET /reports/export?mission_id=&format=pdf  — returns PDF bytes
GET /reports/export?format=json            — returns JSON dump

Uses reportlab for PDF generation. Falls back to JSON if reportlab not installed.
"""
from __future__ import annotations

import io
import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import Response, JSONResponse

logger = logging.getLogger("vision_i.api.reports")
router = APIRouter(tags=["Reports"])


def _build_report_data(request: Request, mission_id: Optional[str]) -> Dict[str, Any]:
    swarm = getattr(request.app.state, "swarm", None)
    mission_data: Dict[str, Any] = {}
    if swarm and mission_id:
        try:
            import asyncio
            loop = asyncio.get_event_loop()
            mission_data = loop.run_until_complete(swarm.get_mission(mission_id)) or {}
        except Exception:
            pass
    return {
        "report_generated": datetime.now(timezone.utc).isoformat(),
        "mission_id": mission_id or "ad-hoc",
        "classification": "UNCLASSIFIED // FOR OFFICIAL USE ONLY",
        "platform": "Vision-I Intelligence Platform",
        "mission": mission_data,
    }


def _generate_pdf(data: Dict[str, Any]) -> bytes:
    try:
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
        from reportlab.lib.units import cm
        from reportlab.lib import colors
        from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
        from reportlab.lib.enums import TA_CENTER, TA_LEFT

        buf = io.BytesIO()
        doc = SimpleDocTemplate(buf, pagesize=A4,
                                rightMargin=2*cm, leftMargin=2*cm,
                                topMargin=2*cm, bottomMargin=2*cm)
        styles = getSampleStyleSheet()

        title_style = ParagraphStyle("VITitle", parent=styles["Title"],
                                     fontSize=18, textColor=colors.HexColor("#a78bfa"),
                                     spaceAfter=4)
        sub_style   = ParagraphStyle("VISub",   parent=styles["Normal"],
                                     fontSize=9,  textColor=colors.grey, spaceAfter=12)
        h2_style    = ParagraphStyle("VIH2",    parent=styles["Heading2"],
                                     textColor=colors.HexColor("#a78bfa"))
        body_style  = styles["BodyText"]

        story = []
        story.append(Paragraph("VISION-I INTELLIGENCE REPORT", title_style))
        story.append(Paragraph(
            f"Mission: {data['mission_id']} &nbsp;|&nbsp; "
            f"Generated: {data['report_generated'][:19].replace('T',' ')} UTC &nbsp;|&nbsp; "
            f"{data['classification']}",
            sub_style))
        story.append(Spacer(1, 0.3*cm))

        mission = data.get("mission", {})
        stages  = mission.get("stages", {})

        # Summary
        story.append(Paragraph("Executive Summary", h2_style))
        summary = mission.get("summary") or "No summary available."
        story.append(Paragraph(str(summary), body_style))
        story.append(Spacer(1, 0.3*cm))

        # Intelligence Brief
        brief = mission.get("intelligence_brief")
        if brief:
            story.append(Paragraph("Intelligence Brief", h2_style))
            for line in str(brief).split("\n"):
                if line.strip():
                    story.append(Paragraph(line.strip(), body_style))
            story.append(Spacer(1, 0.3*cm))

        # Stage metrics table
        if stages:
            story.append(Paragraph("Pipeline Stages", h2_style))
            rows = [["Stage", "Metric", "Value"]]
            for stage, metrics in stages.items():
                if isinstance(metrics, dict):
                    for k, v in metrics.items():
                        rows.append([stage.upper(), k, str(v)])
            tbl = Table(rows, colWidths=[4*cm, 6*cm, 4*cm])
            tbl.setStyle(TableStyle([
                ("BACKGROUND",   (0,0), (-1,0), colors.HexColor("#27272a")),
                ("TEXTCOLOR",    (0,0), (-1,0), colors.HexColor("#a78bfa")),
                ("FONTNAME",     (0,0), (-1,0), "Helvetica-Bold"),
                ("FONTSIZE",     (0,0), (-1,-1), 8),
                ("ROWBACKGROUNDS", (0,1), (-1,-1), [colors.HexColor("#121215"), colors.HexColor("#0f0f12")]),
                ("TEXTCOLOR",    (0,1), (-1,-1), colors.HexColor("#d4d4d8")),
                ("GRID",         (0,0), (-1,-1), 0.25, colors.HexColor("#27272a")),
                ("LEFTPADDING",  (0,0), (-1,-1), 6),
                ("RIGHTPADDING", (0,0), (-1,-1), 6),
                ("TOPPADDING",   (0,0), (-1,-1), 4),
                ("BOTTOMPADDING",(0,0), (-1,-1), 4),
            ]))
            story.append(tbl)

        doc.build(story)
        return buf.getvalue()

    except ImportError:
        raise HTTPException(
            status_code=501,
            detail="PDF export requires reportlab. Install with: pip install reportlab"
        )


@router.get("/export", summary="Export intelligence report as PDF or JSON")
async def export_report(
    request: Request,
    mission_id: Optional[str] = Query(None, description="Mission ID to export"),
    format: str = Query("pdf", description="Output format: pdf or json"),
):
    data = _build_report_data(request, mission_id)

    if format.lower() == "json":
        return JSONResponse(content=data)

    pdf_bytes = _generate_pdf(data)
    filename = f"vision-i-report-{data['mission_id'][:32]}.pdf"
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )

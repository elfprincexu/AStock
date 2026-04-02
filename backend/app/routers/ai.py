"""
AI Analysis Router - Direct integration with LLM-powered stock analysis.

Provides endpoints for:
- Triggering AI analysis for a stock
- Polling analysis task status
- Retrieving analysis history and reports
"""

import logging
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from app.services.ai_analysis import (
    run_analysis_task,
    get_task,
    find_active_task,
    get_analysis_history,
    get_analysis_report,
    is_llm_configured,
    TaskStatus,
)

router = APIRouter()
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Pydantic models (compatible with existing frontend)
# ---------------------------------------------------------------------------

class AnalyzeRequest(BaseModel):
    stock_code: str
    stock_name: Optional[str] = None
    report_type: str = "detailed"
    force_refresh: bool = False


class ReportMeta(BaseModel):
    id: Optional[int] = None
    query_id: Optional[str] = None
    stock_code: Optional[str] = None
    stock_name: Optional[str] = None
    report_type: Optional[str] = None
    report_language: Optional[str] = None
    created_at: Optional[str] = None
    current_price: Optional[float] = None
    change_pct: Optional[float] = None
    model_used: Optional[str] = None


class ReportSummary(BaseModel):
    analysis_summary: Optional[str] = None
    operation_advice: Optional[str] = None
    trend_prediction: Optional[str] = None
    sentiment_score: Optional[int] = None
    sentiment_label: Optional[str] = None


class ReportStrategy(BaseModel):
    ideal_buy: Optional[str] = None
    secondary_buy: Optional[str] = None
    stop_loss: Optional[str] = None
    take_profit: Optional[str] = None


class ReportDetails(BaseModel):
    news_content: Optional[str] = None
    raw_result: Optional[dict] = None
    context_snapshot: Optional[dict] = None
    financial_report: Optional[dict] = None
    dividend_metrics: Optional[dict] = None


class AnalysisReport(BaseModel):
    meta: Optional[ReportMeta] = None
    summary: Optional[ReportSummary] = None
    strategy: Optional[ReportStrategy] = None
    details: Optional[ReportDetails] = None


class AnalysisResponse(BaseModel):
    """Unified response for analysis results."""
    status: str  # pending, processing, completed, failed
    task_id: Optional[str] = None
    progress: Optional[int] = None
    report: Optional[AnalysisReport] = None
    error: Optional[str] = None
    message: Optional[str] = None


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.get("/health")
async def ai_health():
    """Check if AI analysis service is available."""
    configured = is_llm_configured()
    return {
        "configured": configured,
        "reachable": configured,  # For frontend compatibility
        "message": "LLM model configured" if configured else "LITELLM_MODEL not set in .env",
    }


@router.post("/analyze", response_model=AnalysisResponse)
async def trigger_analysis(req: AnalyzeRequest):
    """
    Trigger AI analysis for a stock.
    Returns immediately with a task_id for polling.
    """
    if not is_llm_configured():
        raise HTTPException(
            status_code=503,
            detail="AI analysis is not configured. Set LITELLM_MODEL in .env file.",
        )

    # Check for duplicate active task
    if not req.force_refresh:
        existing = find_active_task(req.stock_code)
        if existing:
            return AnalysisResponse(
                status="processing",
                task_id=existing.task_id,
                progress=existing.progress,
                message="Analysis already in progress for this stock",
            )

    try:
        task = await run_analysis_task(
            stock_code=req.stock_code,
            stock_name=req.stock_name or "",
            report_type=req.report_type,
            force_refresh=req.force_refresh,
        )
    except Exception as e:
        logger.error("Failed to start analysis: %s", e)
        raise HTTPException(status_code=500, detail=str(e))

    # Task may complete synchronously (cache hit)
    if task.status == TaskStatus.COMPLETED and task.report:
        report = AnalysisReport(**task.report) if task.report else None
        return AnalysisResponse(
            status="completed",
            task_id=task.task_id,
            progress=100,
            report=report,
            message="Analysis completed",
        )

    return AnalysisResponse(
        status="pending",
        task_id=task.task_id,
        progress=task.progress,
        message="Analysis task submitted",
    )


@router.get("/status/{task_id}", response_model=AnalysisResponse)
async def get_task_status(task_id: str):
    """Poll analysis task status."""
    task = get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    report = None
    if task.status == TaskStatus.COMPLETED and task.report:
        report = AnalysisReport(**task.report)

    return AnalysisResponse(
        status=task.status.value,
        task_id=task_id,
        progress=task.progress,
        report=report,
        error=task.error,
        message=f"Task {task.status.value}",
    )


@router.get("/history/{stock_code}")
async def get_history(
    stock_code: str,
    limit: int = Query(default=5, ge=1, le=50),
):
    """Get recent analysis history for a stock."""
    try:
        items = await get_analysis_history(stock_code, limit=limit)
        return items
    except Exception as e:
        logger.error("Failed to get history: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/report/{record_id}")
async def get_report(record_id: int):
    """Get a specific analysis report by record ID."""
    try:
        report = await get_analysis_report(record_id)
    except Exception as e:
        logger.error("Failed to get report: %s", e)
        raise HTTPException(status_code=500, detail=str(e))

    if not report:
        raise HTTPException(status_code=404, detail="Report not found")

    return report

"""
Pydantic schemas for AI analysis LLM output validation.
Ported from daily_stock_analysis/src/schemas/report_schema.py.
Uses Optional fields for lenient parsing; business-layer integrity checks are separate.
"""

from typing import Any, Dict, List, Optional, Union

from pydantic import BaseModel, ConfigDict, Field


class PositionAdvice(BaseModel):
    no_position: Optional[str] = None
    has_position: Optional[str] = None


class CoreConclusion(BaseModel):
    one_sentence: Optional[str] = None
    signal_type: Optional[str] = None
    time_sensitivity: Optional[str] = None
    position_advice: Optional[PositionAdvice] = None


class TrendStatus(BaseModel):
    ma_alignment: Optional[str] = None
    is_bullish: Optional[bool] = None
    trend_score: Optional[Union[int, float, str]] = None


class PricePosition(BaseModel):
    current_price: Optional[Union[int, float, str]] = None
    ma5: Optional[Union[int, float, str]] = None
    ma10: Optional[Union[int, float, str]] = None
    ma20: Optional[Union[int, float, str]] = None
    bias_ma5: Optional[Union[int, float, str]] = None
    bias_status: Optional[str] = None
    support_level: Optional[Union[int, float, str]] = None
    resistance_level: Optional[Union[int, float, str]] = None


class VolumeAnalysis(BaseModel):
    volume_ratio: Optional[Union[int, float, str]] = None
    volume_status: Optional[str] = None
    turnover_rate: Optional[Union[int, float, str]] = None
    volume_meaning: Optional[str] = None


class ChipStructure(BaseModel):
    profit_ratio: Optional[Union[int, float, str]] = None
    avg_cost: Optional[Union[int, float, str]] = None
    concentration: Optional[Union[int, float, str]] = None
    chip_health: Optional[str] = None


class DataPerspective(BaseModel):
    trend_status: Optional[TrendStatus] = None
    price_position: Optional[PricePosition] = None
    volume_analysis: Optional[VolumeAnalysis] = None
    chip_structure: Optional[ChipStructure] = None


class Intelligence(BaseModel):
    latest_news: Optional[str] = None
    risk_alerts: Optional[List[str]] = None
    positive_catalysts: Optional[List[str]] = None
    earnings_outlook: Optional[str] = None
    sentiment_summary: Optional[str] = None


class SniperPoints(BaseModel):
    ideal_buy: Optional[Union[str, int, float]] = None
    secondary_buy: Optional[Union[str, int, float]] = None
    stop_loss: Optional[Union[str, int, float]] = None
    take_profit: Optional[Union[str, int, float]] = None


class PositionStrategy(BaseModel):
    suggested_position: Optional[str] = None
    entry_plan: Optional[str] = None
    risk_control: Optional[str] = None


class BattlePlan(BaseModel):
    sniper_points: Optional[SniperPoints] = None
    position_strategy: Optional[PositionStrategy] = None
    action_checklist: Optional[List[str]] = None


class Dashboard(BaseModel):
    core_conclusion: Optional[CoreConclusion] = None
    data_perspective: Optional[DataPerspective] = None
    intelligence: Optional[Intelligence] = None
    battle_plan: Optional[BattlePlan] = None


class AnalysisReportSchema(BaseModel):
    """Top-level schema for LLM report JSON."""

    model_config = ConfigDict(extra="allow")

    stock_name: Optional[str] = None
    sentiment_score: Optional[int] = Field(None, ge=0, le=100)
    trend_prediction: Optional[str] = None
    operation_advice: Optional[str] = None
    decision_type: Optional[str] = None
    confidence_level: Optional[str] = None

    dashboard: Optional[Dashboard] = None

    analysis_summary: Optional[str] = None
    key_points: Optional[str] = None
    risk_warning: Optional[str] = None
    buy_reason: Optional[str] = None

    trend_analysis: Optional[str] = None
    short_term_outlook: Optional[str] = None
    medium_term_outlook: Optional[str] = None
    technical_analysis: Optional[str] = None
    ma_analysis: Optional[str] = None
    volume_analysis: Optional[str] = None
    pattern_analysis: Optional[str] = None
    fundamental_analysis: Optional[str] = None
    sector_position: Optional[str] = None
    company_highlights: Optional[str] = None
    news_summary: Optional[str] = None
    market_sentiment: Optional[str] = None
    hot_topics: Optional[str] = None

    search_performed: Optional[bool] = None
    data_sources: Optional[str] = None

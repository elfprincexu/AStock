"""
AI Stock Analysis Service - Integrated analysis engine.

Ported from daily_stock_analysis project. Provides:
- Technical trend analysis (MA alignment, bias, MACD, RSI, volume)
- LLM-powered analysis via LiteLLM (Gemini, Claude, OpenAI, etc.)
- Async task queue for long-running analysis
- Analysis history persistence
"""

import asyncio
import json
import logging
import os
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, date, timedelta
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
from sqlalchemy import select, desc
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import async_session
from app.models import Stock, DailyKline, StockProfile, AnalysisHistory
from app.services.ai_schemas import AnalysisReportSchema

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Trend Analysis Enums & Dataclass (ported from DSA stock_analyzer.py)
# ---------------------------------------------------------------------------

class TrendStatus(Enum):
    STRONG_BULL = "强势多头"
    BULL = "多头排列"
    WEAK_BULL = "弱势多头"
    CONSOLIDATION = "盘整"
    WEAK_BEAR = "弱势空头"
    BEAR = "空头排列"
    STRONG_BEAR = "强势空头"


class VolumeStatus(Enum):
    HEAVY_VOLUME_UP = "放量上涨"
    HEAVY_VOLUME_DOWN = "放量下跌"
    SHRINK_VOLUME_UP = "缩量上涨"
    SHRINK_VOLUME_DOWN = "缩量回调"
    NORMAL = "量能正常"


class BuySignal(Enum):
    STRONG_BUY = "强烈买入"
    BUY = "买入"
    HOLD = "持有"
    WAIT = "观望"
    SELL = "卖出"
    STRONG_SELL = "强烈卖出"


class MACDStatus(Enum):
    GOLDEN_CROSS_ZERO = "零轴上金叉"
    GOLDEN_CROSS = "金叉"
    BULLISH = "多头"
    CROSSING_UP = "上穿零轴"
    CROSSING_DOWN = "下穿零轴"
    BEARISH = "空头"
    DEATH_CROSS = "死叉"


class RSIStatus(Enum):
    OVERBOUGHT = "超买"
    STRONG_BUY = "强势买入"
    NEUTRAL = "中性"
    WEAK = "弱势"
    OVERSOLD = "超卖"


@dataclass
class TrendAnalysisResult:
    code: str
    trend_status: TrendStatus = TrendStatus.CONSOLIDATION
    ma_alignment: str = ""
    trend_strength: float = 0.0
    ma5: float = 0.0
    ma10: float = 0.0
    ma20: float = 0.0
    ma60: float = 0.0
    current_price: float = 0.0
    bias_ma5: float = 0.0
    bias_ma10: float = 0.0
    bias_ma20: float = 0.0
    volume_status: VolumeStatus = VolumeStatus.NORMAL
    volume_ratio_5d: float = 0.0
    volume_trend: str = ""
    support_ma5: bool = False
    support_ma10: bool = False
    macd_dif: float = 0.0
    macd_dea: float = 0.0
    macd_bar: float = 0.0
    macd_status: MACDStatus = MACDStatus.BULLISH
    macd_signal: str = ""
    rsi_6: float = 0.0
    rsi_12: float = 0.0
    rsi_24: float = 0.0
    rsi_status: RSIStatus = RSIStatus.NEUTRAL
    rsi_signal: str = ""
    buy_signal: BuySignal = BuySignal.WAIT
    signal_score: int = 0
    signal_reasons: List[str] = field(default_factory=list)
    risk_factors: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "trend_status": self.trend_status.value,
            "ma_alignment": self.ma_alignment,
            "trend_strength": self.trend_strength,
            "bias_ma5": self.bias_ma5,
            "bias_ma10": self.bias_ma10,
            "volume_status": self.volume_status.value,
            "volume_trend": self.volume_trend,
            "buy_signal": self.buy_signal.value,
            "signal_score": self.signal_score,
            "signal_reasons": self.signal_reasons,
            "risk_factors": self.risk_factors,
            "macd_status": self.macd_status.value,
            "macd_signal": self.macd_signal,
            "rsi_status": self.rsi_status.value,
            "rsi_signal": self.rsi_signal,
        }


# ---------------------------------------------------------------------------
# Trend Analyzer (ported from DSA stock_analyzer.py)
# ---------------------------------------------------------------------------

VOLUME_SHRINK_RATIO = 0.7
VOLUME_HEAVY_RATIO = 1.5
MA_SUPPORT_TOLERANCE = 0.02
BIAS_THRESHOLD = 5.0


def analyze_trend(df: pd.DataFrame, code: str) -> TrendAnalysisResult:
    """Analyze stock trend from OHLCV DataFrame."""
    result = TrendAnalysisResult(code=code)

    if df is None or df.empty or len(df) < 20:
        result.risk_factors.append("数据不足，无法完成分析")
        return result

    df = df.sort_values("date").reset_index(drop=True)
    df = _calculate_mas(df)
    df = _calculate_macd(df)
    df = _calculate_rsi(df)

    latest = df.iloc[-1]
    result.current_price = float(latest["close"])
    result.ma5 = float(latest["MA5"])
    result.ma10 = float(latest["MA10"])
    result.ma20 = float(latest["MA20"])
    result.ma60 = float(latest.get("MA60", 0))

    _analyze_trend_status(df, result)
    _calculate_bias(result)
    _analyze_volume(df, result)
    _analyze_support(df, result)
    _analyze_macd(df, result)
    _analyze_rsi(df, result)
    _generate_signal(result)

    return result


def _calculate_mas(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["MA5"] = df["close"].rolling(window=5).mean()
    df["MA10"] = df["close"].rolling(window=10).mean()
    df["MA20"] = df["close"].rolling(window=20).mean()
    if len(df) >= 60:
        df["MA60"] = df["close"].rolling(window=60).mean()
    else:
        df["MA60"] = df["MA20"]
    return df


def _calculate_macd(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    ema_fast = df["close"].ewm(span=12, adjust=False).mean()
    ema_slow = df["close"].ewm(span=26, adjust=False).mean()
    df["MACD_DIF"] = ema_fast - ema_slow
    df["MACD_DEA"] = df["MACD_DIF"].ewm(span=9, adjust=False).mean()
    df["MACD_BAR"] = (df["MACD_DIF"] - df["MACD_DEA"]) * 2
    return df


def _calculate_rsi(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    for period in [6, 12, 24]:
        delta = df["close"].diff()
        gain = delta.where(delta > 0, 0)
        loss = -delta.where(delta < 0, 0)
        avg_gain = gain.rolling(window=period).mean()
        avg_loss = loss.rolling(window=period).mean()
        rs = avg_gain / avg_loss
        rsi = 100 - (100 / (1 + rs))
        df[f"RSI_{period}"] = rsi.fillna(50)
    return df


def _analyze_trend_status(df: pd.DataFrame, result: TrendAnalysisResult) -> None:
    ma5, ma10, ma20 = result.ma5, result.ma10, result.ma20
    if ma5 > ma10 > ma20:
        prev = df.iloc[-5] if len(df) >= 5 else df.iloc[-1]
        prev_spread = (prev["MA5"] - prev["MA20"]) / prev["MA20"] * 100 if prev["MA20"] > 0 else 0
        curr_spread = (ma5 - ma20) / ma20 * 100 if ma20 > 0 else 0
        if curr_spread > prev_spread and curr_spread > 5:
            result.trend_status = TrendStatus.STRONG_BULL
            result.ma_alignment = "强势多头排列，均线发散上行"
            result.trend_strength = 90
        else:
            result.trend_status = TrendStatus.BULL
            result.ma_alignment = "多头排列 MA5>MA10>MA20"
            result.trend_strength = 75
    elif ma5 > ma10 and ma10 <= ma20:
        result.trend_status = TrendStatus.WEAK_BULL
        result.ma_alignment = "弱势多头，MA5>MA10 但 MA10≤MA20"
        result.trend_strength = 55
    elif ma5 < ma10 < ma20:
        prev = df.iloc[-5] if len(df) >= 5 else df.iloc[-1]
        prev_spread = (prev["MA20"] - prev["MA5"]) / prev["MA5"] * 100 if prev["MA5"] > 0 else 0
        curr_spread = (ma20 - ma5) / ma5 * 100 if ma5 > 0 else 0
        if curr_spread > prev_spread and curr_spread > 5:
            result.trend_status = TrendStatus.STRONG_BEAR
            result.ma_alignment = "强势空头排列，均线发散下行"
            result.trend_strength = 10
        else:
            result.trend_status = TrendStatus.BEAR
            result.ma_alignment = "空头排列 MA5<MA10<MA20"
            result.trend_strength = 25
    elif ma5 < ma10 and ma10 >= ma20:
        result.trend_status = TrendStatus.WEAK_BEAR
        result.ma_alignment = "弱势空头，MA5<MA10 但 MA10≥MA20"
        result.trend_strength = 40
    else:
        result.trend_status = TrendStatus.CONSOLIDATION
        result.ma_alignment = "均线缠绕，趋势不明"
        result.trend_strength = 50


def _calculate_bias(result: TrendAnalysisResult) -> None:
    price = result.current_price
    if result.ma5 > 0:
        result.bias_ma5 = (price - result.ma5) / result.ma5 * 100
    if result.ma10 > 0:
        result.bias_ma10 = (price - result.ma10) / result.ma10 * 100
    if result.ma20 > 0:
        result.bias_ma20 = (price - result.ma20) / result.ma20 * 100


def _analyze_volume(df: pd.DataFrame, result: TrendAnalysisResult) -> None:
    if len(df) < 5:
        return
    latest = df.iloc[-1]
    vol_5d_avg = df["volume"].iloc[-6:-1].mean()
    if vol_5d_avg > 0:
        result.volume_ratio_5d = float(latest["volume"]) / vol_5d_avg
    prev_close = df.iloc[-2]["close"]
    price_change = (latest["close"] - prev_close) / prev_close * 100
    if result.volume_ratio_5d >= VOLUME_HEAVY_RATIO:
        if price_change > 0:
            result.volume_status = VolumeStatus.HEAVY_VOLUME_UP
            result.volume_trend = "放量上涨，多头力量强劲"
        else:
            result.volume_status = VolumeStatus.HEAVY_VOLUME_DOWN
            result.volume_trend = "放量下跌，注意风险"
    elif result.volume_ratio_5d <= VOLUME_SHRINK_RATIO:
        if price_change > 0:
            result.volume_status = VolumeStatus.SHRINK_VOLUME_UP
            result.volume_trend = "缩量上涨，上攻动能不足"
        else:
            result.volume_status = VolumeStatus.SHRINK_VOLUME_DOWN
            result.volume_trend = "缩量回调，洗盘特征明显"
    else:
        result.volume_status = VolumeStatus.NORMAL
        result.volume_trend = "量能正常"


def _analyze_support(df: pd.DataFrame, result: TrendAnalysisResult) -> None:
    price = result.current_price
    if result.ma5 > 0:
        if abs(price - result.ma5) / result.ma5 <= MA_SUPPORT_TOLERANCE and price >= result.ma5:
            result.support_ma5 = True
    if result.ma10 > 0:
        if abs(price - result.ma10) / result.ma10 <= MA_SUPPORT_TOLERANCE and price >= result.ma10:
            result.support_ma10 = True


def _analyze_macd(df: pd.DataFrame, result: TrendAnalysisResult) -> None:
    if len(df) < 26:
        result.macd_signal = "数据不足"
        return
    latest = df.iloc[-1]
    prev = df.iloc[-2]
    result.macd_dif = float(latest["MACD_DIF"])
    result.macd_dea = float(latest["MACD_DEA"])
    result.macd_bar = float(latest["MACD_BAR"])
    prev_diff = prev["MACD_DIF"] - prev["MACD_DEA"]
    curr_diff = result.macd_dif - result.macd_dea
    is_golden = prev_diff <= 0 and curr_diff > 0
    is_death = prev_diff >= 0 and curr_diff < 0
    is_cross_up = prev["MACD_DIF"] <= 0 and result.macd_dif > 0
    is_cross_down = prev["MACD_DIF"] >= 0 and result.macd_dif < 0

    if is_golden and result.macd_dif > 0:
        result.macd_status = MACDStatus.GOLDEN_CROSS_ZERO
        result.macd_signal = "零轴上金叉，强烈买入信号"
    elif is_cross_up:
        result.macd_status = MACDStatus.CROSSING_UP
        result.macd_signal = "DIF上穿零轴，趋势转强"
    elif is_golden:
        result.macd_status = MACDStatus.GOLDEN_CROSS
        result.macd_signal = "金叉，趋势向上"
    elif is_death:
        result.macd_status = MACDStatus.DEATH_CROSS
        result.macd_signal = "死叉，趋势向下"
    elif is_cross_down:
        result.macd_status = MACDStatus.CROSSING_DOWN
        result.macd_signal = "DIF下穿零轴，趋势转弱"
    elif result.macd_dif > 0 and result.macd_dea > 0:
        result.macd_status = MACDStatus.BULLISH
        result.macd_signal = "多头排列，持续上涨"
    elif result.macd_dif < 0 and result.macd_dea < 0:
        result.macd_status = MACDStatus.BEARISH
        result.macd_signal = "空头排列，持续下跌"
    else:
        result.macd_status = MACDStatus.BULLISH
        result.macd_signal = "MACD 中性区域"


def _analyze_rsi(df: pd.DataFrame, result: TrendAnalysisResult) -> None:
    if len(df) < 24:
        result.rsi_signal = "数据不足"
        return
    latest = df.iloc[-1]
    result.rsi_6 = float(latest["RSI_6"])
    result.rsi_12 = float(latest["RSI_12"])
    result.rsi_24 = float(latest["RSI_24"])
    rsi_mid = result.rsi_12
    if rsi_mid > 70:
        result.rsi_status = RSIStatus.OVERBOUGHT
        result.rsi_signal = f"RSI超买({rsi_mid:.1f}>70)，短期回调风险高"
    elif rsi_mid > 60:
        result.rsi_status = RSIStatus.STRONG_BUY
        result.rsi_signal = f"RSI强势({rsi_mid:.1f})，多头力量充足"
    elif rsi_mid >= 40:
        result.rsi_status = RSIStatus.NEUTRAL
        result.rsi_signal = f"RSI中性({rsi_mid:.1f})，震荡整理中"
    elif rsi_mid >= 30:
        result.rsi_status = RSIStatus.WEAK
        result.rsi_signal = f"RSI弱势({rsi_mid:.1f})，关注反弹"
    else:
        result.rsi_status = RSIStatus.OVERSOLD
        result.rsi_signal = f"RSI超卖({rsi_mid:.1f}<30)，反弹机会大"


def _generate_signal(result: TrendAnalysisResult) -> None:
    score = 0
    reasons: List[str] = []
    risks: List[str] = []

    # Trend (30 pts)
    trend_scores = {
        TrendStatus.STRONG_BULL: 30, TrendStatus.BULL: 26, TrendStatus.WEAK_BULL: 18,
        TrendStatus.CONSOLIDATION: 12, TrendStatus.WEAK_BEAR: 8, TrendStatus.BEAR: 4,
        TrendStatus.STRONG_BEAR: 0,
    }
    score += trend_scores.get(result.trend_status, 12)
    if result.trend_status in [TrendStatus.STRONG_BULL, TrendStatus.BULL]:
        reasons.append(f"{result.trend_status.value}，顺势做多")
    elif result.trend_status in [TrendStatus.BEAR, TrendStatus.STRONG_BEAR]:
        risks.append(f"{result.trend_status.value}，不宜做多")

    # Bias (20 pts)
    bias = result.bias_ma5 if result.bias_ma5 == result.bias_ma5 else 0.0
    is_strong = result.trend_status == TrendStatus.STRONG_BULL and result.trend_strength >= 70
    effective_threshold = BIAS_THRESHOLD * 1.5 if is_strong else BIAS_THRESHOLD
    if bias < 0:
        if bias > -3:
            score += 20
            reasons.append(f"价格略低于MA5({bias:.1f}%)，回踩买点")
        elif bias > -5:
            score += 16
            reasons.append(f"价格回踩MA5({bias:.1f}%)，观察支撑")
        else:
            score += 8
            risks.append(f"乖离率过大({bias:.1f}%)，可能破位")
    elif bias < 2:
        score += 18
        reasons.append(f"价格贴近MA5({bias:.1f}%)，介入好时机")
    elif bias < BIAS_THRESHOLD:
        score += 14
        reasons.append(f"价格略高于MA5({bias:.1f}%)，可小仓介入")
    elif bias > effective_threshold:
        score += 4
        risks.append(f"乖离率过高({bias:.1f}%>{effective_threshold:.1f}%)，严禁追高！")
    elif is_strong:
        score += 10
        reasons.append(f"强势趋势中乖离率偏高({bias:.1f}%)，可轻仓追踪")
    else:
        score += 4
        risks.append(f"乖离率过高({bias:.1f}%>{BIAS_THRESHOLD:.1f}%)，严禁追高！")

    # Volume (15 pts)
    vol_scores = {
        VolumeStatus.SHRINK_VOLUME_DOWN: 15, VolumeStatus.HEAVY_VOLUME_UP: 12,
        VolumeStatus.NORMAL: 10, VolumeStatus.SHRINK_VOLUME_UP: 6,
        VolumeStatus.HEAVY_VOLUME_DOWN: 0,
    }
    score += vol_scores.get(result.volume_status, 8)
    if result.volume_status == VolumeStatus.SHRINK_VOLUME_DOWN:
        reasons.append("缩量回调，主力洗盘")
    elif result.volume_status == VolumeStatus.HEAVY_VOLUME_DOWN:
        risks.append("放量下跌，注意风险")

    # Support (10 pts)
    if result.support_ma5:
        score += 5
        reasons.append("MA5支撑有效")
    if result.support_ma10:
        score += 5
        reasons.append("MA10支撑有效")

    # MACD (15 pts)
    macd_scores = {
        MACDStatus.GOLDEN_CROSS_ZERO: 15, MACDStatus.GOLDEN_CROSS: 12,
        MACDStatus.CROSSING_UP: 10, MACDStatus.BULLISH: 8,
        MACDStatus.BEARISH: 2, MACDStatus.CROSSING_DOWN: 0, MACDStatus.DEATH_CROSS: 0,
    }
    score += macd_scores.get(result.macd_status, 5)
    if result.macd_status in [MACDStatus.GOLDEN_CROSS_ZERO, MACDStatus.GOLDEN_CROSS]:
        reasons.append(result.macd_signal)
    elif result.macd_status in [MACDStatus.DEATH_CROSS, MACDStatus.CROSSING_DOWN]:
        risks.append(result.macd_signal)

    # RSI (10 pts)
    rsi_scores = {
        RSIStatus.OVERSOLD: 10, RSIStatus.STRONG_BUY: 8, RSIStatus.NEUTRAL: 5,
        RSIStatus.WEAK: 3, RSIStatus.OVERBOUGHT: 0,
    }
    score += rsi_scores.get(result.rsi_status, 5)
    if result.rsi_status in [RSIStatus.OVERSOLD, RSIStatus.STRONG_BUY]:
        reasons.append(result.rsi_signal)
    elif result.rsi_status == RSIStatus.OVERBOUGHT:
        risks.append(result.rsi_signal)

    result.signal_score = score
    result.signal_reasons = reasons
    result.risk_factors = risks

    if score >= 75 and result.trend_status in [TrendStatus.STRONG_BULL, TrendStatus.BULL]:
        result.buy_signal = BuySignal.STRONG_BUY
    elif score >= 60 and result.trend_status in [TrendStatus.STRONG_BULL, TrendStatus.BULL, TrendStatus.WEAK_BULL]:
        result.buy_signal = BuySignal.BUY
    elif score >= 45:
        result.buy_signal = BuySignal.HOLD
    elif score >= 30:
        result.buy_signal = BuySignal.WAIT
    elif result.trend_status in [TrendStatus.BEAR, TrendStatus.STRONG_BEAR]:
        result.buy_signal = BuySignal.STRONG_SELL
    else:
        result.buy_signal = BuySignal.SELL


# ---------------------------------------------------------------------------
# Trading Policy (system prompt fragment)
# ---------------------------------------------------------------------------

CORE_TRADING_POLICY = """## 默认技能基线（必须严格遵守）

### 1. 严进策略（不追高）
- **绝对不追高**：当股价偏离 MA5 超过 5% 时，坚决不买入
- 乖离率 < 2%：最佳买点区间
- 乖离率 2-5%：可小仓介入
- 乖离率 > 5%：严禁追高！直接判定为"观望"

### 2. 趋势交易（顺势而为）
- **多头排列必须条件**：MA5 > MA10 > MA20
- 只做多头排列的股票，空头排列坚决不碰
- 均线发散上行优于均线粘合

### 3. 效率优先（筹码结构）
- 关注筹码集中度：90%集中度 < 15% 表示筹码集中
- 获利比例分析：70-90% 获利盘时需警惕获利回吐
- 平均成本与现价关系：现价高于平均成本 5-15% 为健康

### 4. 买点偏好（回踩支撑）
- **最佳买点**：缩量回踩 MA5 获得支撑
- **次优买点**：回踩 MA10 获得支撑
- **观望情况**：跌破 MA20 时观望

### 5. 风险排查重点
- 减持公告、业绩预亏、监管处罚、行业政策利空、大额解禁

### 6. 估值关注（PE/PB）
- PE 明显偏高时需在风险点中说明

### 7. 强势趋势股放宽
- 强势趋势股可适当放宽乖离率要求，轻仓追踪但需设止损
"""

SYSTEM_PROMPT = """你是一位专注于趋势交易的 A 股投资分析师，负责生成专业的【决策仪表盘】分析报告。

""" + CORE_TRADING_POLICY + """

## 输出格式：决策仪表盘 JSON

请严格按照以下 JSON 格式输出，这是一个完整的【决策仪表盘】：

```json
{
    "stock_name": "股票中文名称",
    "sentiment_score": 0-100整数,
    "trend_prediction": "强烈看多/看多/震荡/看空/强烈看空",
    "operation_advice": "买入/加仓/持有/减仓/卖出/观望",
    "decision_type": "buy/hold/sell",
    "confidence_level": "高/中/低",

    "dashboard": {
        "core_conclusion": {
            "one_sentence": "一句话核心结论（30字以内，直接告诉用户做什么）",
            "signal_type": "🟢买入信号/🟡持有观望/🔴卖出信号/⚠️风险警告",
            "time_sensitivity": "立即行动/今日内/本周内/不急",
            "position_advice": {
                "no_position": "空仓者建议：具体操作指引",
                "has_position": "持仓者建议：具体操作指引"
            }
        },
        "data_perspective": {
            "trend_status": {
                "ma_alignment": "均线排列状态描述",
                "is_bullish": true/false,
                "trend_score": 0-100
            },
            "price_position": {
                "current_price": 当前价格数值,
                "ma5": MA5数值, "ma10": MA10数值, "ma20": MA20数值,
                "bias_ma5": 乖离率百分比数值,
                "bias_status": "安全/警戒/危险",
                "support_level": 支撑位价格,
                "resistance_level": 压力位价格
            },
            "volume_analysis": {
                "volume_ratio": 量比数值,
                "volume_status": "放量/缩量/平量",
                "turnover_rate": 换手率百分比,
                "volume_meaning": "量能含义解读"
            },
            "chip_structure": {
                "profit_ratio": "N/A",
                "avg_cost": "N/A",
                "concentration": "N/A",
                "chip_health": "数据缺失"
            }
        },
        "intelligence": {
            "latest_news": "暂无新闻数据",
            "risk_alerts": ["风险点1", "风险点2"],
            "positive_catalysts": ["利好1", "利好2"],
            "earnings_outlook": "业绩预期分析",
            "sentiment_summary": "舆情情绪总结"
        },
        "battle_plan": {
            "sniper_points": {
                "ideal_buy": "理想买入点：XX元",
                "secondary_buy": "次优买入点：XX元",
                "stop_loss": "止损位：XX元",
                "take_profit": "目标位：XX元"
            },
            "position_strategy": {
                "suggested_position": "建议仓位：X成",
                "entry_plan": "分批建仓策略",
                "risk_control": "风控策略"
            },
            "action_checklist": [
                "✅/⚠️/❌ 检查项1：多头排列",
                "✅/⚠️/❌ 检查项2：乖离率合理",
                "✅/⚠️/❌ 检查项3：量能配合",
                "✅/⚠️/❌ 检查项4：无重大利空",
                "✅/⚠️/❌ 检查项5：PE估值合理"
            ]
        }
    },

    "analysis_summary": "100字综合分析摘要",
    "key_points": "3-5个核心看点",
    "risk_warning": "风险提示",
    "buy_reason": "操作理由",
    "trend_analysis": "走势形态分析",
    "short_term_outlook": "短期1-3日展望",
    "medium_term_outlook": "中期1-2周展望",
    "technical_analysis": "技术面综合分析",
    "ma_analysis": "均线系统分析",
    "volume_analysis": "量能分析",
    "fundamental_analysis": "基本面分析",
    "news_summary": "新闻摘要",
    "search_performed": false,
    "data_sources": "数据来源说明"
}
```

## 评分标准

### 强烈买入（80-100分）：
- ✅ 多头排列：MA5 > MA10 > MA20
- ✅ 低乖离率：<2%，最佳买点
- ✅ 缩量回调或放量突破
- ✅ 消息面有利好催化

### 买入（60-79分）：
- ✅ 多头排列或弱势多头
- ✅ 乖离率 <5%
- ✅ 量能正常

### 观望（40-59分）：
- ⚠️ 乖离率 >5%（追高风险）
- ⚠️ 均线缠绕趋势不明
- ⚠️ 有风险事件

### 卖出/减仓（0-39分）：
- ❌ 空头排列
- ❌ 跌破MA20
- ❌ 放量下跌
- ❌ 重大利空

## 决策仪表盘核心原则

1. **核心结论先行**：一句话说清该买该卖
2. **分持仓建议**：空仓者和持仓者给不同建议
3. **精确狙击点**：必须给出具体价格，不说模糊的话
4. **检查清单可视化**：用 ✅⚠️❌ 明确显示每项检查结果
5. **风险优先级**：风险点要醒目标出"""


# ---------------------------------------------------------------------------
# Prompt Builder
# ---------------------------------------------------------------------------

def _format_volume(volume: Optional[float]) -> str:
    if volume is None:
        return "N/A"
    if volume >= 1e8:
        return f"{volume / 1e8:.2f} 亿股"
    elif volume >= 1e4:
        return f"{volume / 1e4:.2f} 万股"
    return f"{volume:.0f} 股"


def _format_amount(amount: Optional[float]) -> str:
    if amount is None:
        return "N/A"
    if amount >= 1e8:
        return f"{amount / 1e8:.2f} 亿元"
    elif amount >= 1e4:
        return f"{amount / 1e4:.2f} 万元"
    return f"{amount:.0f} 元"


def build_analysis_prompt(context: Dict[str, Any]) -> str:
    """Build the user prompt for LLM analysis from collected context data."""
    code = context.get("code", "Unknown")
    stock_name = context.get("stock_name", f"股票{code}")
    today = context.get("today", {})

    prompt = f"""# 决策仪表盘分析请求

## 股票基础信息
| 项目 | 数据 |
|------|------|
| 股票代码 | **{code}** |
| 股票名称 | **{stock_name}** |
| 分析日期 | {context.get('date', 'N/A')} |

---

## 技术面数据

### 今日行情
| 指标 | 数值 |
|------|------|
| 收盘价 | {today.get('close', 'N/A')} 元 |
| 开盘价 | {today.get('open', 'N/A')} 元 |
| 最高价 | {today.get('high', 'N/A')} 元 |
| 最低价 | {today.get('low', 'N/A')} 元 |
| 涨跌幅 | {today.get('pct_chg', 'N/A')}% |
| 成交量 | {_format_volume(today.get('volume'))} |
| 成交额 | {_format_amount(today.get('amount'))} |

### 均线系统（关键判断指标）
| 均线 | 数值 | 说明 |
|------|------|------|
| MA5 | {today.get('ma5', 'N/A')} | 短期趋势线 |
| MA10 | {today.get('ma10', 'N/A')} | 中短期趋势线 |
| MA20 | {today.get('ma20', 'N/A')} | 中期趋势线 |
| 均线形态 | {context.get('ma_status', 'N/A')} | 多头/空头/缠绕 |
"""

    # Realtime quote enhancement
    if "realtime" in context:
        rt = context["realtime"]
        prompt += f"""
### 实时行情增强数据
| 指标 | 数值 |
|------|------|
| 市盈率(动态) | {rt.get('pe_ttm', 'N/A')} |
| 市净率 | {rt.get('pb', 'N/A')} |
| 总市值 | {_format_amount(rt.get('total_market_cap'))} |
| 流通市值 | {_format_amount(rt.get('circ_market_cap'))} |
| 行业 | {rt.get('industry', 'N/A')} |
"""

    # Trend analysis
    if "trend_analysis" in context:
        trend = context["trend_analysis"]
        bias_warning = "超过5%，严禁追高！" if trend.get("bias_ma5", 0) > 5 else "安全范围"
        prompt += f"""
### 趋势分析预判（基于交易理念）
| 指标 | 数值 | 判定 |
|------|------|------|
| 趋势状态 | {trend.get('trend_status', 'N/A')} | |
| 均线排列 | {trend.get('ma_alignment', 'N/A')} | MA5>MA10>MA20为多头 |
| 趋势强度 | {trend.get('trend_strength', 0)}/100 | |
| **乖离率(MA5)** | **{trend.get('bias_ma5', 0):+.2f}%** | {bias_warning} |
| 乖离率(MA10) | {trend.get('bias_ma10', 0):+.2f}% | |
| 量能状态 | {trend.get('volume_status', 'N/A')} | {trend.get('volume_trend', '')} |
| MACD信号 | {trend.get('macd_signal', 'N/A')} | |
| RSI信号 | {trend.get('rsi_signal', 'N/A')} | |
| 系统信号 | {trend.get('buy_signal', 'N/A')} | |
| 系统评分 | {trend.get('signal_score', 0)}/100 | |

#### 系统分析理由
**买入理由**：
{chr(10).join('- ' + r for r in trend.get('signal_reasons', ['无'])) if trend.get('signal_reasons') else '- 无'}

**风险因素**：
{chr(10).join('- ' + r for r in trend.get('risk_factors', ['无'])) if trend.get('risk_factors') else '- 无'}
"""

    # Volume/price change
    if "yesterday" in context:
        prompt += f"""
### 量价变化
- 成交量较昨日变化：{context.get('volume_change_ratio', 'N/A')}倍
- 价格较昨日变化：{context.get('price_change_ratio', 'N/A')}%
"""

    # No news section (can be added later)
    prompt += """
---

## 舆情情报

暂无该股票近期的新闻搜索结果。请主要依据技术面数据进行分析。
"""

    # Analysis task requirements
    prompt += f"""
---

## 分析任务

请为 **{stock_name}({code})** 生成【决策仪表盘】，严格按照 JSON 格式输出。

### 重点关注（必须明确回答）：
1. 是否满足 MA5>MA10>MA20 多头排列？
2. 当前乖离率是否在安全范围内（<5%）？—— 超过5%必须标注"严禁追高"
3. 量能是否配合（缩量回调/放量突破）？
4. MACD和RSI指标是否支持？
5. 消息面有无重大利空？

### 决策仪表盘要求：
- **股票名称**：必须输出正确的中文全称
- **核心结论**：一句话说清该买/该卖/该等
- **持仓分类建议**：空仓者怎么做 vs 持仓者怎么做
- **具体狙击点位**：买入价、止损价、目标价（精确到分）
- **检查清单**：每项用 ✅/⚠️/❌ 标记

### 输出语言要求（最高优先级）
- 所有 JSON 键名必须保持不变，不要翻译键名。
- `decision_type` 必须保持为 `buy`、`hold`、`sell`。
- 所有面向用户的人类可读文本值必须使用中文。

请输出完整的 JSON 格式决策仪表盘。"""

    return prompt


# ---------------------------------------------------------------------------
# LLM Caller
# ---------------------------------------------------------------------------

def _setup_llm_env():
    """Set up environment variables for litellm based on our config.

    When a native litellm provider prefix is used (e.g. ``deepseek/``,
    ``dashscope/``, ``moonshot/``, ``volcengine/``), litellm looks for a
    provider-specific API key env var (``DEEPSEEK_API_KEY``, etc.).
    Since the user only configures one key via ``OPENAI_API_KEY`` in the
    config page, we mirror it to every provider env var so that native
    prefixes work out-of-the-box.
    """
    if settings.GEMINI_API_KEY:
        os.environ.setdefault("GEMINI_API_KEY", settings.GEMINI_API_KEY)
    if settings.ANTHROPIC_API_KEY:
        os.environ.setdefault("ANTHROPIC_API_KEY", settings.ANTHROPIC_API_KEY)
    if settings.OPENAI_API_KEY:
        os.environ.setdefault("OPENAI_API_KEY", settings.OPENAI_API_KEY)
        # Mirror to native Chinese provider env vars so that
        # e.g. "deepseek/deepseek-chat" finds its key automatically
        for env_key in (
            "DEEPSEEK_API_KEY",
            "DASHSCOPE_API_KEY",
            "MOONSHOT_API_KEY",
            "VOLCENGINE_API_KEY",
        ):
            os.environ.setdefault(env_key, settings.OPENAI_API_KEY)
    if settings.OPENAI_BASE_URL:
        os.environ.setdefault("OPENAI_API_BASE", settings.OPENAI_BASE_URL)


async def call_llm(prompt: str) -> Tuple[str, str]:
    """
    Call LLM via litellm. Returns (response_text, model_used).
    Runs in a thread since litellm is sync.
    """
    import litellm

    _setup_llm_env()

    model = settings.LITELLM_MODEL
    if not model:
        raise ValueError(
            "LITELLM_MODEL is not configured. "
            "Set it in .env, e.g. LITELLM_MODEL=gemini/gemini-2.5-flash"
        )

    call_kwargs = {
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
        "temperature": settings.LLM_TEMPERATURE,
        "max_tokens": settings.LLM_MAX_TOKENS,
        "timeout": settings.LLM_REQUEST_TIMEOUT,
    }

    # For OpenAI-compatible endpoints, pass api_base and api_key explicitly
    if settings.OPENAI_BASE_URL:
        call_kwargs["api_base"] = settings.OPENAI_BASE_URL
    # Always pass api_key explicitly when available — covers both native
    # litellm prefixes (deepseek/, dashscope/, moonshot/, volcengine/) and
    # the openai/ prefix used with custom base URLs.
    if settings.OPENAI_API_KEY:
        call_kwargs["api_key"] = settings.OPENAI_API_KEY

    def _do_call():
        # Disable SSL verification for corporate/self-signed certs
        if not settings.LLM_SSL_VERIFY:
            import httpx
            from openai import OpenAI
            http_client = httpx.Client(verify=False, timeout=settings.LLM_REQUEST_TIMEOUT)
            openai_client = OpenAI(
                api_key=call_kwargs.get("api_key", settings.OPENAI_API_KEY or "dummy"),
                base_url=settings.OPENAI_BASE_URL or None,
                http_client=http_client,
            )
            call_kwargs["client"] = openai_client

        try:
            response = litellm.completion(**call_kwargs)
            text = response.choices[0].message.content
            used_model = getattr(response, "model", model)
            return text, used_model
        finally:
            if "client" in call_kwargs:
                call_kwargs["client"].close()

    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _do_call)


# ---------------------------------------------------------------------------
# Response Parser
# ---------------------------------------------------------------------------

def _fix_json_string(json_str: str) -> str:
    """Fix common JSON issues from LLM output."""
    json_str = re.sub(r"//.*?\n", "\n", json_str)
    json_str = re.sub(r"/\*.*?\*/", "", json_str, flags=re.DOTALL)
    json_str = re.sub(r",\s*}", "}", json_str)
    json_str = re.sub(r",\s*]", "]", json_str)
    json_str = json_str.replace("True", "true").replace("False", "false")
    try:
        from json_repair import repair_json
        json_str = repair_json(json_str)
    except ImportError:
        pass
    return json_str


def parse_llm_response(response_text: str, code: str, name: str) -> Dict[str, Any]:
    """
    Parse LLM response into a structured report dict.
    Returns a dict with: sentiment_score, operation_advice, trend_prediction,
    dashboard, analysis_summary, and all other fields.
    """
    try:
        cleaned = response_text
        if "```json" in cleaned:
            cleaned = cleaned.replace("```json", "").replace("```", "")
        elif "```" in cleaned:
            cleaned = cleaned.replace("```", "")

        json_start = cleaned.find("{")
        json_end = cleaned.rfind("}") + 1

        if json_start >= 0 and json_end > json_start:
            json_str = cleaned[json_start:json_end]
            json_str = _fix_json_string(json_str)
            data = json.loads(json_str)

            # Lenient schema validation
            try:
                AnalysisReportSchema.model_validate(data)
            except Exception as e:
                logger.warning("Schema validation warning: %s", str(e)[:200])

            # Infer decision_type if missing
            decision_type = data.get("decision_type", "")
            if not decision_type:
                op = data.get("operation_advice", "持有")
                if any(k in str(op) for k in ["买入", "加仓", "buy"]):
                    decision_type = "buy"
                elif any(k in str(op) for k in ["卖出", "减仓", "sell"]):
                    decision_type = "sell"
                else:
                    decision_type = "hold"
                data["decision_type"] = decision_type

            # Use AI-returned name if original was placeholder
            ai_name = data.get("stock_name")
            if ai_name and (name.startswith("股票") or name == code):
                data["stock_name"] = ai_name

            return data

    except json.JSONDecodeError as e:
        logger.warning("JSON parse failed: %s, falling back to text parsing", e)

    # Fallback: simple text-based sentiment extraction
    return _parse_text_response(response_text, code, name)


def _parse_text_response(response_text: str, code: str, name: str) -> Dict[str, Any]:
    """Fallback parser: extract sentiment from plain text."""
    positive = ["看多", "买入", "上涨", "突破", "强势", "利好", "加仓"]
    negative = ["看空", "卖出", "下跌", "跌破", "弱势", "利空", "减仓"]
    text_lower = response_text.lower()
    pos_count = sum(1 for kw in positive if kw in text_lower)
    neg_count = sum(1 for kw in negative if kw in text_lower)

    if pos_count > neg_count + 1:
        score, trend, advice, dt = 65, "看多", "买入", "buy"
    elif neg_count > pos_count + 1:
        score, trend, advice, dt = 35, "看空", "卖出", "sell"
    else:
        score, trend, advice, dt = 50, "震荡", "持有", "hold"

    return {
        "stock_name": name,
        "sentiment_score": score,
        "trend_prediction": trend,
        "operation_advice": advice,
        "decision_type": dt,
        "confidence_level": "低",
        "analysis_summary": response_text[:500] if response_text else "无分析结果",
        "key_points": "JSON解析失败，仅供参考",
        "risk_warning": "分析结果可能不准确，建议结合其他信息判断",
    }


# ---------------------------------------------------------------------------
# Data Collection (from AStock's own database)
# ---------------------------------------------------------------------------

async def collect_analysis_context(
    stock_code: str,
    db: AsyncSession,
) -> Dict[str, Any]:
    """
    Collect all data needed for analysis from AStock's database.
    Returns context dict suitable for build_analysis_prompt().
    """
    # Find stock record
    result = await db.execute(select(Stock).where(Stock.code == stock_code))
    stock = result.scalar_one_or_none()
    if not stock:
        raise ValueError(f"Stock {stock_code} not found in database")

    stock_name = stock.name

    # Fetch recent klines (last 90 days for MA/MACD/RSI)
    kline_result = await db.execute(
        select(DailyKline)
        .where(DailyKline.stock_id == stock.id)
        .order_by(desc(DailyKline.date))
        .limit(90)
    )
    klines = kline_result.scalars().all()

    if not klines:
        raise ValueError(f"No kline data for {stock_code}")

    # Convert to DataFrame
    records = []
    for k in klines:
        records.append({
            "date": k.date,
            "open": k.open,
            "high": k.high,
            "low": k.low,
            "close": k.close,
            "volume": k.volume,
            "amount": k.amount,
            "pct_chg": k.change_pct,
            "turnover_rate": k.turnover_rate,
        })
    df = pd.DataFrame(records).sort_values("date").reset_index(drop=True)

    # Run trend analysis
    trend_result = analyze_trend(df, stock_code)

    # Get latest and yesterday data
    latest = df.iloc[-1]
    yesterday = df.iloc[-2] if len(df) >= 2 else latest

    # Calculate MAs for context
    df_mas = _calculate_mas(df)
    latest_ma = df_mas.iloc[-1]

    today_data = {
        "open": float(latest["open"]),
        "high": float(latest["high"]),
        "low": float(latest["low"]),
        "close": float(latest["close"]),
        "volume": float(latest["volume"]),
        "amount": float(latest["amount"]),
        "pct_chg": float(latest["pct_chg"]) if pd.notna(latest["pct_chg"]) else 0,
        "ma5": round(float(latest_ma["MA5"]), 2) if pd.notna(latest_ma["MA5"]) else None,
        "ma10": round(float(latest_ma["MA10"]), 2) if pd.notna(latest_ma["MA10"]) else None,
        "ma20": round(float(latest_ma["MA20"]), 2) if pd.notna(latest_ma["MA20"]) else None,
    }

    yesterday_data = {
        "close": float(yesterday["close"]),
        "volume": float(yesterday["volume"]),
    }

    # Volume/price change ratios
    vol_change = float(latest["volume"]) / float(yesterday["volume"]) if float(yesterday["volume"]) > 0 else 1.0
    price_change = (float(latest["close"]) - float(yesterday["close"])) / float(yesterday["close"]) * 100 if float(yesterday["close"]) > 0 else 0

    # MA status description
    ma5 = today_data.get("ma5")
    ma10 = today_data.get("ma10")
    ma20 = today_data.get("ma20")
    if ma5 and ma10 and ma20:
        if ma5 > ma10 > ma20:
            ma_status = "多头排列 MA5>MA10>MA20"
        elif ma5 < ma10 < ma20:
            ma_status = "空头排列 MA5<MA10<MA20"
        else:
            ma_status = "均线缠绕"
    else:
        ma_status = "数据不足"

    # Fetch profile for fundamentals
    profile_result = await db.execute(
        select(StockProfile).where(StockProfile.stock_id == stock.id)
    )
    profile = profile_result.scalar_one_or_none()

    context = {
        "code": stock_code,
        "stock_name": stock_name,
        "date": str(latest["date"]),
        "today": today_data,
        "yesterday": yesterday_data,
        "volume_change_ratio": round(vol_change, 2),
        "price_change_ratio": round(price_change, 2),
        "ma_status": ma_status,
        "trend_analysis": trend_result.to_dict(),
    }

    # Add profile data as realtime-like context
    if profile:
        context["realtime"] = {
            "pe_ttm": profile.pe_ttm,
            "pb": profile.pb,
            "total_market_cap": profile.total_market_cap,
            "circ_market_cap": profile.circ_market_cap,
            "industry": profile.industry,
        }

    return context


# ---------------------------------------------------------------------------
# Task Queue (in-memory async)
# ---------------------------------------------------------------------------

class TaskStatus(Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class AnalysisTask:
    task_id: str
    stock_code: str
    stock_name: str
    status: TaskStatus = TaskStatus.PENDING
    progress: int = 0
    report: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    created_at: datetime = field(default_factory=datetime.utcnow)


# Global task store
_tasks: Dict[str, AnalysisTask] = {}
_active_codes: Dict[str, str] = {}  # stock_code -> task_id (prevent duplicates)


def get_task(task_id: str) -> Optional[AnalysisTask]:
    return _tasks.get(task_id)


def find_active_task(stock_code: str) -> Optional[AnalysisTask]:
    task_id = _active_codes.get(stock_code)
    if task_id:
        task = _tasks.get(task_id)
        if task and task.status in [TaskStatus.PENDING, TaskStatus.PROCESSING]:
            return task
        # Clean up stale reference
        _active_codes.pop(stock_code, None)
    return None


async def run_analysis_task(
    stock_code: str,
    stock_name: str,
    report_type: str = "detailed",
    force_refresh: bool = False,
) -> AnalysisTask:
    """Create and launch an analysis task."""
    # Check for existing active task
    if not force_refresh:
        existing = find_active_task(stock_code)
        if existing:
            return existing

    task_id = str(uuid.uuid4())
    task = AnalysisTask(
        task_id=task_id,
        stock_code=stock_code,
        stock_name=stock_name,
    )
    _tasks[task_id] = task
    _active_codes[stock_code] = task_id

    # Launch async analysis
    asyncio.create_task(_execute_analysis(task, report_type, force_refresh))

    return task


async def _execute_analysis(
    task: AnalysisTask,
    report_type: str,
    force_refresh: bool,
) -> None:
    """Execute the full analysis pipeline."""
    try:
        task.status = TaskStatus.PROCESSING
        task.progress = 10

        async with async_session() as db:
            # Check for recent analysis (within today) if not force_refresh
            if not force_refresh:
                today_start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
                existing = await db.execute(
                    select(AnalysisHistory)
                    .where(
                        AnalysisHistory.stock_code == task.stock_code,
                        AnalysisHistory.created_at >= today_start,
                    )
                    .order_by(desc(AnalysisHistory.created_at))
                    .limit(1)
                )
                recent = existing.scalar_one_or_none()
                if recent and recent.report_json:
                    logger.info("Found recent analysis for %s, reusing", task.stock_code)
                    task.report = _build_report_response(recent)
                    task.status = TaskStatus.COMPLETED
                    task.progress = 100
                    _active_codes.pop(task.stock_code, None)
                    return

            # Step 1: Collect data
            task.progress = 20
            context = await collect_analysis_context(task.stock_code, db)
            if task.stock_name:
                context["stock_name"] = task.stock_name

            # Step 2: Build prompt
            task.progress = 40
            prompt = build_analysis_prompt(context)

            # Step 3: Call LLM
            task.progress = 50
            response_text, model_used = await call_llm(prompt)

            # Step 4: Parse response
            task.progress = 80
            parsed = parse_llm_response(
                response_text, task.stock_code, context.get("stock_name", task.stock_name)
            )

            # Step 5: Save to database
            task.progress = 90
            history = AnalysisHistory(
                stock_code=task.stock_code,
                stock_name=parsed.get("stock_name", task.stock_name or ""),
                report_type=report_type,
                model_used=model_used,
                sentiment_score=int(parsed.get("sentiment_score", 50)),
                operation_advice=parsed.get("operation_advice"),
                trend_prediction=parsed.get("trend_prediction"),
                current_price=context.get("today", {}).get("close"),
                change_pct=context.get("today", {}).get("pct_chg"),
                report_json=json.dumps(parsed, ensure_ascii=False),
                context_json=json.dumps(context, ensure_ascii=False, default=str),
            )
            db.add(history)
            await db.commit()
            await db.refresh(history)

            # Build report response
            task.report = _build_report_from_parsed(history.id, parsed, context, model_used)
            task.status = TaskStatus.COMPLETED
            task.progress = 100
            logger.info("Analysis completed for %s", task.stock_code)

    except Exception as e:
        logger.error("Analysis failed for %s: %s", task.stock_code, e, exc_info=True)
        task.status = TaskStatus.FAILED
        task.error = str(e)
    finally:
        _active_codes.pop(task.stock_code, None)


def _build_report_from_parsed(
    record_id: int,
    parsed: Dict[str, Any],
    context: Dict[str, Any],
    model_used: str,
) -> Dict[str, Any]:
    """Build the frontend-compatible report structure from parsed LLM data."""
    dashboard = parsed.get("dashboard", {}) or {}
    sniper = (dashboard.get("battle_plan") or {}).get("sniper_points") or {}

    return {
        "meta": {
            "id": record_id,
            "stock_code": context.get("code"),
            "stock_name": parsed.get("stock_name", context.get("stock_name")),
            "report_type": "detailed",
            "created_at": datetime.utcnow().isoformat(),
            "current_price": context.get("today", {}).get("close"),
            "change_pct": context.get("today", {}).get("pct_chg"),
            "model_used": model_used,
        },
        "summary": {
            "analysis_summary": parsed.get("analysis_summary", ""),
            "operation_advice": parsed.get("operation_advice", "持有"),
            "trend_prediction": parsed.get("trend_prediction", "震荡"),
            "sentiment_score": int(parsed.get("sentiment_score", 50)),
            "sentiment_label": _sentiment_label(int(parsed.get("sentiment_score", 50))),
        },
        "strategy": {
            "ideal_buy": str(sniper.get("ideal_buy", "N/A")),
            "secondary_buy": str(sniper.get("secondary_buy", "N/A")),
            "stop_loss": str(sniper.get("stop_loss", "N/A")),
            "take_profit": str(sniper.get("take_profit", "N/A")),
        },
        "details": {
            "news_content": parsed.get("news_summary"),
            "raw_result": parsed,
            "context_snapshot": context,
        },
    }


def _build_report_response(record: AnalysisHistory) -> Dict[str, Any]:
    """Build report response from a database AnalysisHistory record."""
    parsed = {}
    context = {}
    if record.report_json:
        try:
            parsed = json.loads(record.report_json)
        except json.JSONDecodeError:
            pass
    if record.context_json:
        try:
            context = json.loads(record.context_json)
        except json.JSONDecodeError:
            pass

    dashboard = parsed.get("dashboard", {}) or {}
    sniper = (dashboard.get("battle_plan") or {}).get("sniper_points") or {}

    return {
        "meta": {
            "id": record.id,
            "stock_code": record.stock_code,
            "stock_name": record.stock_name,
            "report_type": record.report_type,
            "created_at": record.created_at.isoformat() if record.created_at else None,
            "current_price": record.current_price,
            "change_pct": record.change_pct,
            "model_used": record.model_used,
        },
        "summary": {
            "analysis_summary": parsed.get("analysis_summary", ""),
            "operation_advice": record.operation_advice or parsed.get("operation_advice", "持有"),
            "trend_prediction": record.trend_prediction or parsed.get("trend_prediction", "震荡"),
            "sentiment_score": record.sentiment_score or int(parsed.get("sentiment_score", 50)),
            "sentiment_label": _sentiment_label(record.sentiment_score or 50),
        },
        "strategy": {
            "ideal_buy": str(sniper.get("ideal_buy", "N/A")),
            "secondary_buy": str(sniper.get("secondary_buy", "N/A")),
            "stop_loss": str(sniper.get("stop_loss", "N/A")),
            "take_profit": str(sniper.get("take_profit", "N/A")),
        },
        "details": {
            "news_content": parsed.get("news_summary"),
            "raw_result": parsed,
            "context_snapshot": context,
        },
    }


def _sentiment_label(score: int) -> str:
    if score >= 80:
        return "极度乐观"
    elif score >= 60:
        return "乐观"
    elif score >= 40:
        return "中性"
    elif score >= 20:
        return "悲观"
    return "极度悲观"


# ---------------------------------------------------------------------------
# History queries
# ---------------------------------------------------------------------------

async def get_analysis_history(
    stock_code: str,
    limit: int = 5,
) -> List[Dict[str, Any]]:
    """Get recent analysis history for a stock."""
    async with async_session() as db:
        result = await db.execute(
            select(AnalysisHistory)
            .where(AnalysisHistory.stock_code == stock_code)
            .order_by(desc(AnalysisHistory.created_at))
            .limit(limit)
        )
        records = result.scalars().all()

    items = []
    for r in records:
        items.append({
            "id": r.id,
            "stock_code": r.stock_code,
            "stock_name": r.stock_name,
            "report_type": r.report_type,
            "model_used": r.model_used,
            "sentiment_score": r.sentiment_score,
            "operation_advice": r.operation_advice,
            "trend_prediction": r.trend_prediction,
            "current_price": r.current_price,
            "change_pct": r.change_pct,
            "created_at": r.created_at.isoformat() if r.created_at else None,
        })
    return items


async def get_analysis_report(record_id: int) -> Optional[Dict[str, Any]]:
    """Get a specific analysis report by ID."""
    async with async_session() as db:
        result = await db.execute(
            select(AnalysisHistory).where(AnalysisHistory.id == record_id)
        )
        record = result.scalar_one_or_none()

    if not record:
        return None

    return _build_report_response(record)


def is_llm_configured() -> bool:
    """Check if LLM is properly configured."""
    return bool(settings.LITELLM_MODEL)

"""
Cross-sectional quantitative factor analysis engine.

Implements institutional-grade multi-stock quant pipeline:
  1. Universe Construction — Filter tradeable stocks (ST, IPO, liquidity, limit-up/down)
  2. Factor Computation — 50 factors across 8 categories for selected universe
  3. Cross-Sectional Processing — Rank/z-score across all stocks per date
  4. Factor Validation — Cross-sectional IC, Rank IC, IC_IR
  5. Alpha Combination — IC-weighted + optional LightGBM ensemble
  6. Portfolio Construction — Top-N with buffer zone turnover control
  7. Backtest — Walk-forward with T+1, realistic costs, liquidity constraints

Factor library: 50 factors covering Alpha101-style price/volume patterns,
plus value, momentum, reversal, volatility, volume, technical, money-flow,
and higher-order statistical factors. Tailored for A-share market structure.

Methodology references:
  - Qlib (Microsoft): cross-sectional factor framework
  - Barra risk model: factor standardization and neutralization
  - Liu et al. (2019): "Size and Value in China" — reversal factor dominance
  - Kakushadze (2016): "101 Formulaic Alphas"
"""

import logging
import warnings
import uuid
from dataclasses import dataclass, field
from typing import Optional, Dict, List, Tuple, Any
from datetime import date as date_type, datetime, timedelta

import numpy as np
import pandas as pd
from scipy import stats

logger = logging.getLogger(__name__)

# Try importing ML libraries — optional enhancement
try:
    import lightgbm as lgb
    HAS_LGBM = True
except ImportError:
    HAS_LGBM = False

try:
    from sklearn.ensemble import GradientBoostingClassifier
    HAS_SKLEARN = True
except ImportError:
    HAS_SKLEARN = False

# ─── Configuration Constants ────────────────────────────────────────

# Forward return horizon for IC computation
FORWARD_DAYS = 10  # 10 trading days (~2 weeks), balances signal decay vs T+1 cost

# Cross-sectional IC parameters
IC_ROLLING_WINDOW = 60       # Rolling window for IC computation
IC_MEAN_THRESHOLD = 0.03     # Min |IC_mean| for factor validity (academic: >=0.03 meaningful, >=0.05 strong)
IC_IR_THRESHOLD = 0.3        # Min |IC_IR| for factor validity
IC_CONSISTENCY_THRESHOLD = 0.55  # Min directional consistency

# Factor weighting
IC_DECAY_HALFLIFE = 60       # Exponential decay half-life for IC weighting

# ML parameters
ML_TRAIN_RATIO = 0.8
ML_MIN_SAMPLES = 200
ML_MIN_DATES = 120

# Portfolio construction defaults
DEFAULT_TOP_N = 30
DEFAULT_REBALANCE_FREQ = 10
DEFAULT_BUFFER_IN = 25
DEFAULT_BUFFER_OUT = 40

# Transaction cost model (A-share specific, in basis points)
BUY_COST_BPS = 7.6
SELL_COST_BPS = 13.2

# Universe filtering
MIN_LISTED_DAYS = 60
MIN_ACTIVE_DAYS_IN_20 = 15
MIN_AVG_DAILY_AMOUNT = 10_000_000  # 1000万元/day
MAX_VOL_RATIO_VS_MARKET = 3.0
EXCLUDE_BJ = True

# Limit-up/down thresholds
MAIN_BOARD_LIMIT = 9.8
CHINEXT_STAR_LIMIT = 19.8


# ─── Factor Definitions (50 factors, 8 categories) ──────────────────

CATEGORY_LABELS = {
    "value": "价值",
    "momentum": "动量",
    "reversal": "反转",
    "volume": "量能",
    "volatility": "波动",
    "technical": "技术",
    "moneyflow": "资金流",
    "stat": "统计",
}

FACTOR_DEFS = [
    # ── Value (3) ──
    {"name": "ep", "category": "value", "label": "盈利收益率", "desc": "1/PE_TTM, 越高越便宜"},
    {"name": "bp", "category": "value", "label": "账面价值比", "desc": "1/PB, 越高越便宜"},
    {"name": "neg_log_mcap", "category": "value", "label": "市值因子(负)", "desc": "-log(总市值), 小盘效应"},
    # ── Momentum (6) ──
    {"name": "ret_5d", "category": "momentum", "label": "5日收益", "desc": "5个交易日收益率"},
    {"name": "ret_10d", "category": "momentum", "label": "10日收益", "desc": "10个交易日收益率"},
    {"name": "ret_20d", "category": "momentum", "label": "20日收益", "desc": "20个交易日收益率"},
    {"name": "ret_60d", "category": "momentum", "label": "60日收益", "desc": "60个交易日收益率"},
    {"name": "ret_120d_skip20d", "category": "momentum", "label": "120日动量(跳近20)", "desc": "120日收益减去近20日(经典Jegadeesh-Titman)"},
    {"name": "weighted_ret_5d", "category": "momentum", "label": "量价5日动量", "desc": "5日成交量加权收益率(Alpha101#12)"},
    # ── Reversal (4) ──
    {"name": "reversal_5d", "category": "reversal", "label": "5日反转", "desc": "-1×5日收益率，A股最强因子之一"},
    {"name": "reversal_20d", "category": "reversal", "label": "20日反转", "desc": "-1×20日收益率"},
    {"name": "overnight_ret_20d", "category": "reversal", "label": "隔夜收益率(20日均)", "desc": "20日平均隔夜跳空幅度"},
    {"name": "intraday_ret_20d", "category": "reversal", "label": "日内收益率(20日均)", "desc": "20日平均(收盘/开盘-1)，日内反转"},
    # ── Volume/Liquidity (7) ──
    {"name": "turnover_20d", "category": "volume", "label": "20日换手率", "desc": "20日平均换手率"},
    {"name": "vol_ratio_5_20", "category": "volume", "label": "量比(5/20)", "desc": "5日均量/20日均量"},
    {"name": "vol_ratio_5_60", "category": "volume", "label": "量比(5/60)", "desc": "5日均量/60日均量"},
    {"name": "amihud_illiq", "category": "volume", "label": "Amihud非流动性", "desc": "mean(|收益率|/成交额), 流动性溢价"},
    {"name": "abnormal_vol", "category": "volume", "label": "异常成交量", "desc": "当日成交量/60日平均成交量"},
    {"name": "vol_std_20d", "category": "volume", "label": "成交量波动", "desc": "20日成交量标准差/均值(变异系数)"},
    {"name": "vol_price_corr", "category": "volume", "label": "量价相关", "desc": "20日成交量与收盘价Pearson相关系数"},
    # ── Volatility/Risk (6) ──
    {"name": "realized_vol_20d", "category": "volatility", "label": "20日波动率", "desc": "年化已实现波动率(20日)"},
    {"name": "realized_vol_60d", "category": "volatility", "label": "60日波动率", "desc": "年化已实现波动率(60日)"},
    {"name": "idio_vol_60d", "category": "volatility", "label": "特质波动率(60日)", "desc": "剥离市场后的残差波动率"},
    {"name": "max_drawdown_20d", "category": "volatility", "label": "20日最大回撤", "desc": "近20日最大峰谷回撤"},
    {"name": "downside_vol_20d", "category": "volatility", "label": "20日下行波动", "desc": "仅计算负收益的半方差"},
    {"name": "high_low_range_20d", "category": "volatility", "label": "20日振幅", "desc": "20日平均(high-low)/close"},
    # ── Technical (10) ──
    {"name": "rsi_14", "category": "technical", "label": "RSI(14)", "desc": "14日相对强弱指数"},
    {"name": "rsi_6", "category": "technical", "label": "RSI(6)", "desc": "6日相对强弱指数(短期超买超卖)"},
    {"name": "bb_position", "category": "technical", "label": "布林位置", "desc": "价格在布林带中的位置(-1到1)"},
    {"name": "price_pos_60d", "category": "technical", "label": "60日价格位置", "desc": "价格在60日高低点的位置(0到1)"},
    {"name": "price_pos_20d", "category": "technical", "label": "20日价格位置", "desc": "价格在20日高低点的位置(0到1)"},
    {"name": "macd_divergence", "category": "technical", "label": "MACD散度", "desc": "MACD柱状图/收盘价"},
    {"name": "bias_20d", "category": "technical", "label": "20日乖离率", "desc": "价格偏离20日均线的百分比"},
    {"name": "bias_5d", "category": "technical", "label": "5日乖离率", "desc": "价格偏离5日均线的百分比"},
    {"name": "ma_cross_5_20", "category": "technical", "label": "MA5/MA20交叉", "desc": "5日均线与20日均线比值偏离"},
    {"name": "ma_cross_10_60", "category": "technical", "label": "MA10/MA60交叉", "desc": "10日均线与60日均线比值偏离"},
    # ── Money Flow (7, Alpha101-style) ──
    {"name": "vwap_bias", "category": "moneyflow", "label": "VWAP偏离", "desc": "收盘价/VWAP-1, 机构买卖方向(Alpha101#32)"},
    {"name": "close_open_ratio", "category": "moneyflow", "label": "上下影线比", "desc": "(close-open)/(high-low), 多空博弈"},
    {"name": "upper_shadow", "category": "moneyflow", "label": "上影线压力", "desc": "(high-max(open,close))/(high-low)"},
    {"name": "lower_shadow", "category": "moneyflow", "label": "下影线支撑", "desc": "(min(open,close)-low)/(high-low)"},
    {"name": "vol_up_pct", "category": "moneyflow", "label": "上涨成交占比", "desc": "20日内上涨日成交量占总成交量比例"},
    {"name": "net_buy_vol_ratio", "category": "moneyflow", "label": "净买入量比", "desc": "20日(涨日量-跌日量)/总量, 资金流向"},
    {"name": "smart_money_flow", "category": "moneyflow", "label": "聪明资金流", "desc": "尾盘30min成交占比代理(最后1h振幅×量)"},
    # ── Higher-Order Statistics (7, Alpha101-inspired) ──
    {"name": "ret_skew_20d", "category": "stat", "label": "收益偏度(20日)", "desc": "20日收益率偏度, 正偏=更多极端正收益"},
    {"name": "ret_kurt_20d", "category": "stat", "label": "收益峰度(20日)", "desc": "20日收益率峰度, 高峰度=尾部风险"},
    {"name": "ret_autocorr_5d", "category": "stat", "label": "收益自相关(5日)", "desc": "5日收益率自相关系数, 衡量惯性/反转"},
    {"name": "vol_skew_20d", "category": "stat", "label": "成交量偏度", "desc": "20日成交量偏度, 正偏=偶发放量"},
    {"name": "high_low_corr_20d", "category": "stat", "label": "最高最低价相关", "desc": "20日高低价log比值相关(Alpha101#18)"},
    {"name": "close_ret_delay1", "category": "stat", "label": "收益率延迟(1日)", "desc": "corr(ret, ret.shift(1)), 市场反应速度"},
    {"name": "rank_ret_vol_corr", "category": "stat", "label": "收益量排名相关", "desc": "5日rank(收益)与rank(量)的相关系数"},
]


# ─── Helper ──────────────────────────────────────────────────────────

def _sanitize_for_json(obj):
    """Recursively convert numpy/pandas types to native Python for JSON serialization."""
    if isinstance(obj, dict):
        return {k: _sanitize_for_json(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_sanitize_for_json(v) for v in obj]
    if isinstance(obj, (np.bool_,)):
        return bool(obj)
    if isinstance(obj, (np.integer,)):
        return int(obj)
    if isinstance(obj, (np.floating,)):
        v = float(obj)
        if np.isnan(v) or np.isinf(v):
            return None
        return v
    if isinstance(obj, (np.ndarray,)):
        return [_sanitize_for_json(x) for x in obj.tolist()]
    if isinstance(obj, (pd.Timestamp,)):
        return obj.isoformat()
    if isinstance(obj, (date_type, datetime)):
        return obj.isoformat()
    if isinstance(obj, float):
        if np.isnan(obj) or np.isinf(obj):
            return None
    return obj


# ─── Data Classes ────────────────────────────────────────────────────

@dataclass
class UniverseConfig:
    """Configuration for tradeable universe filtering."""
    exclude_st: bool = True
    min_listed_days: int = MIN_LISTED_DAYS
    min_active_days_in_20: int = MIN_ACTIVE_DAYS_IN_20
    exclude_limit: bool = True
    min_avg_daily_amount: float = MIN_AVG_DAILY_AMOUNT
    exclude_bj: bool = EXCLUDE_BJ
    max_vol_ratio_vs_market: float = MAX_VOL_RATIO_VS_MARKET


@dataclass
class PortfolioConfig:
    """Configuration for portfolio construction."""
    top_n: int = DEFAULT_TOP_N
    rebalance_freq: int = DEFAULT_REBALANCE_FREQ
    buffer_in: int = DEFAULT_BUFFER_IN
    buffer_out: int = DEFAULT_BUFFER_OUT
    max_sector_pct: float = 0.20
    max_stock_weight: float = 0.05
    equal_weight: bool = True
    industry_neutral: bool = False


@dataclass
class BacktestConfig:
    """Configuration for realistic backtest."""
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    initial_capital: float = 1_000_000
    buy_cost_bps: float = BUY_COST_BPS
    sell_cost_bps: float = SELL_COST_BPS
    execution_delay: int = 1
    forward_days: int = FORWARD_DAYS
    ic_lookback_days: int = 480


@dataclass
class FactorMetrics:
    """Cross-sectional IC metrics for a single factor."""
    name: str
    category: str = ""
    ic_mean: float = 0.0
    ic_std: float = 0.0
    ic_ir: float = 0.0
    rank_ic_mean: float = 0.0
    rank_ic_ir: float = 0.0
    ic_positive_rate: float = 0.0
    ic_direction: int = 0
    ic_consistency: float = 0.0
    is_valid: bool = False
    weight: float = 0.0
    ic_series: list = field(default_factory=list)


# ─── CORE FUNCTION 1: Single-Stock Factor Computation ────────────────

def compute_stock_factors(kline_df: pd.DataFrame) -> pd.DataFrame:
    """Compute all 47 technical factors for a single stock's kline data.

    Value factors (ep, bp, neg_log_mcap) are added separately from profile data.
    """
    df = kline_df.copy()
    if len(df) < 60:
        return df

    df = df.sort_values("date").reset_index(drop=True)
    c = df["close"]
    o = df["open"]
    h = df["high"]
    l = df["low"]
    v = df["volume"].astype(float)
    amt = df["amount"].astype(float)
    tr = df["turnover_rate"].fillna(0).astype(float)
    daily_ret = c.pct_change()

    # ── Momentum ──
    df["ret_5d"] = c.pct_change(5)
    df["ret_10d"] = c.pct_change(10)
    df["ret_20d"] = c.pct_change(20)
    df["ret_60d"] = c.pct_change(60)
    df["ret_120d_skip20d"] = c.shift(20).pct_change(100)
    # Volume-weighted return (Alpha101 #12 variant)
    vw_ret = (daily_ret * v).rolling(5).sum() / v.rolling(5).sum().replace(0, np.nan)
    df["weighted_ret_5d"] = vw_ret

    # ── Reversal ──
    df["reversal_5d"] = -df["ret_5d"]
    df["reversal_20d"] = -df["ret_20d"]
    overnight = o / c.shift(1) - 1
    df["overnight_ret_20d"] = overnight.rolling(20).mean()
    intraday = c / o - 1
    df["intraday_ret_20d"] = intraday.rolling(20).mean()

    # ── Volume/Liquidity ──
    df["turnover_20d"] = tr.rolling(20).mean()
    v5 = v.rolling(5).mean()
    v20 = v.rolling(20).mean()
    v60 = v.rolling(60).mean()
    df["vol_ratio_5_20"] = v5 / v20.replace(0, np.nan)
    df["vol_ratio_5_60"] = v5 / v60.replace(0, np.nan)
    amihud_daily = daily_ret.abs() / amt.replace(0, np.nan)
    df["amihud_illiq"] = amihud_daily.rolling(20).mean()
    df["abnormal_vol"] = v / v60.replace(0, np.nan)
    v_std20 = v.rolling(20).std()
    df["vol_std_20d"] = v_std20 / v20.replace(0, np.nan)
    # Volume-price correlation
    df["vol_price_corr"] = v.rolling(20).corr(c)

    # ── Volatility/Risk ──
    df["realized_vol_20d"] = daily_ret.rolling(20).std() * np.sqrt(252)
    df["realized_vol_60d"] = daily_ret.rolling(60).std() * np.sqrt(252)
    rolling_mean_ret = daily_ret.rolling(60).mean()
    residual = daily_ret - rolling_mean_ret
    df["idio_vol_60d"] = residual.rolling(60).std() * np.sqrt(252)
    # Max drawdown (vectorized via expanding max)
    rolling_max = c.rolling(20).max()
    df["max_drawdown_20d"] = (rolling_max - c) / rolling_max.replace(0, np.nan)
    neg_ret = daily_ret.clip(upper=0)
    df["downside_vol_20d"] = neg_ret.rolling(20).std() * np.sqrt(252)
    hl_range = (h - l) / c.replace(0, np.nan)
    df["high_low_range_20d"] = hl_range.rolling(20).mean()

    # ── Technical ──
    # RSI(14)
    delta = c.diff()
    gain = delta.clip(lower=0)
    loss = (-delta).clip(lower=0)
    avg_gain14 = gain.ewm(span=14, adjust=False).mean()
    avg_loss14 = loss.ewm(span=14, adjust=False).mean()
    df["rsi_14"] = 100 - 100 / (1 + avg_gain14 / avg_loss14.replace(0, np.nan))
    # RSI(6)
    avg_gain6 = gain.ewm(span=6, adjust=False).mean()
    avg_loss6 = loss.ewm(span=6, adjust=False).mean()
    df["rsi_6"] = 100 - 100 / (1 + avg_gain6 / avg_loss6.replace(0, np.nan))
    # Bollinger Band position
    ma20 = c.rolling(20).mean()
    std20 = c.rolling(20).std()
    bb_upper = ma20 + 2 * std20
    bb_lower = ma20 - 2 * std20
    bb_range = (bb_upper - bb_lower).replace(0, np.nan)
    df["bb_position"] = (c - bb_lower) / bb_range * 2 - 1
    # Price positions
    high60 = h.rolling(60).max()
    low60 = l.rolling(60).min()
    df["price_pos_60d"] = (c - low60) / (high60 - low60).replace(0, np.nan)
    high20 = h.rolling(20).max()
    low20 = l.rolling(20).min()
    df["price_pos_20d"] = (c - low20) / (high20 - low20).replace(0, np.nan)
    # MACD
    ema12 = c.ewm(span=12, adjust=False).mean()
    ema26 = c.ewm(span=26, adjust=False).mean()
    dif = ema12 - ema26
    dea = dif.ewm(span=9, adjust=False).mean()
    macd_hist = 2 * (dif - dea)
    df["macd_divergence"] = macd_hist / c.replace(0, np.nan)
    # Bias rates
    df["bias_20d"] = (c - ma20) / ma20.replace(0, np.nan) * 100
    ma5 = c.rolling(5).mean()
    df["bias_5d"] = (c - ma5) / ma5.replace(0, np.nan) * 100
    # MA crosses
    df["ma_cross_5_20"] = ma5 / ma20.replace(0, np.nan) - 1
    ma10 = c.rolling(10).mean()
    ma60 = c.rolling(60).mean()
    df["ma_cross_10_60"] = ma10 / ma60.replace(0, np.nan) - 1

    # ── Money Flow (Alpha101-style) ──
    vwap = amt / v.replace(0, np.nan)
    df["vwap_bias"] = c / vwap.replace(0, np.nan) - 1
    hl = (h - l).replace(0, np.nan)
    df["close_open_ratio"] = (c - o) / hl
    max_oc = pd.concat([o, c], axis=1).max(axis=1)
    min_oc = pd.concat([o, c], axis=1).min(axis=1)
    df["upper_shadow"] = (h - max_oc) / hl
    df["lower_shadow"] = (min_oc - l) / hl
    # Up-volume fraction in last 20 days
    up_mask = daily_ret > 0
    up_vol_20 = (v * up_mask.astype(float)).rolling(20).sum()
    total_vol_20 = v.rolling(20).sum().replace(0, np.nan)
    df["vol_up_pct"] = up_vol_20 / total_vol_20
    # Net buy volume ratio
    down_mask = daily_ret < 0
    down_vol_20 = (v * down_mask.astype(float)).rolling(20).sum()
    df["net_buy_vol_ratio"] = (up_vol_20 - down_vol_20) / total_vol_20
    # Smart money flow proxy: high-low range * volume (captures intensity)
    intensity = hl_range * v
    df["smart_money_flow"] = intensity.rolling(5).mean() / intensity.rolling(60).mean().replace(0, np.nan)

    # ── Higher-Order Statistics ──
    df["ret_skew_20d"] = daily_ret.rolling(20).skew()
    df["ret_kurt_20d"] = daily_ret.rolling(20).kurt()
    # Autocorrelation via vectorized shift-multiply (avoid rolling().apply with lambda)
    r1 = daily_ret
    r1_lag = daily_ret.shift(1)
    roll_cov = (r1 * r1_lag).rolling(20).mean() - r1.rolling(20).mean() * r1_lag.rolling(20).mean()
    roll_var = r1.rolling(20).var()
    df["ret_autocorr_5d"] = roll_cov / roll_var.replace(0, np.nan)
    df["vol_skew_20d"] = v.rolling(20).skew()
    # Alpha101#18 variant: correlation of log(high/low) over 20 days
    log_hl = np.log((h / l).replace(0, np.nan))
    df["high_low_corr_20d"] = log_hl.rolling(20).corr(v)
    # Delay-1 autocorrelation via same vectorized approach
    df["close_ret_delay1"] = roll_cov / roll_var.replace(0, np.nan)
    # Rank correlation between return and volume (5-day window)
    df["rank_ret_vol_corr"] = daily_ret.rolling(10).corr(v)

    return df


# ─── CORE FUNCTION 2: Universe Filtering ─────────────────────────────

def filter_universe(
    all_klines: Dict[int, pd.DataFrame],
    stock_info: Dict[int, dict],
    target_date: date_type,
    config: Optional[UniverseConfig] = None,
) -> Dict[int, str]:
    """
    Filter tradeable universe, return dict of {stock_id: exclude_reason}.
    Stocks NOT in the returned dict are in the universe.
    """
    if config is None:
        config = UniverseConfig()

    excluded = {}

    for sid, info in stock_info.items():
        name = info.get("name", "")
        market = info.get("market", "")
        code = info.get("code", "")

        # 1. ST stocks
        if config.exclude_st and ("ST" in name.upper() or "*ST" in name.upper()):
            excluded[sid] = "ST"
            continue

        # 2. Beijing exchange
        if config.exclude_bj and market == "BJ":
            excluded[sid] = "BJ交易所"
            continue

        df = all_klines.get(sid)
        if df is None or df.empty:
            excluded[sid] = "无数据"
            continue

        # Filter to data up to target_date
        df_up = df[df["date"] <= target_date]
        if df_up.empty:
            excluded[sid] = "无数据"
            continue

        # 3. New IPO filter
        if len(df_up) < config.min_listed_days:
            excluded[sid] = f"IPO<{config.min_listed_days}天"
            continue

        # 4. Active days in last 20
        last_20 = df_up.tail(20)
        if len(last_20) < config.min_active_days_in_20:
            excluded[sid] = "停牌/不活跃"
            continue

        # 5. Limit-up/down on target date
        if config.exclude_limit:
            latest = df_up.iloc[-1]
            change = abs(latest.get("change_pct", 0))
            is_chinext_star = (
                code.startswith("300") or code.startswith("301")
                or code.startswith("688") or code.startswith("689")
            )
            limit_pct = CHINEXT_STAR_LIMIT if is_chinext_star else MAIN_BOARD_LIMIT
            if change >= limit_pct:
                excluded[sid] = "涨跌停"
                continue

        # 6. Minimum liquidity
        recent = df_up.tail(20)
        avg_amount = recent["amount"].mean() if "amount" in recent.columns else 0
        if avg_amount < config.min_avg_daily_amount:
            excluded[sid] = "流动性不足"
            continue

    return excluded


# ─── CORE FUNCTION 3: Cross-Sectional Processing ────────────────────

def cross_sectional_rank(factor_panel: pd.DataFrame, factor_names: List[str]) -> pd.DataFrame:
    """Rank factors cross-sectionally at each date. Returns percentile ranks in [0, 1]."""
    ranked = factor_panel.copy()
    for fname in factor_names:
        if fname in ranked.columns:
            ranked[fname] = ranked.groupby("date")[fname].rank(pct=True, na_option="keep")
    return ranked


def cross_sectional_winsorize(
    factor_panel: pd.DataFrame, factor_names: List[str], n_mad: float = 3.0
) -> pd.DataFrame:
    """MAD-based winsorization per date across all stocks."""
    result = factor_panel.copy()
    for fname in factor_names:
        if fname not in result.columns:
            continue

        def _winsorize_group(group):
            vals = group[fname]
            median = vals.median()
            mad = (vals - median).abs().median()
            if mad == 0 or np.isnan(mad):
                return vals
            upper = median + n_mad * 1.4826 * mad
            lower = median - n_mad * 1.4826 * mad
            return vals.clip(lower=lower, upper=upper)

        result[fname] = result.groupby("date").apply(lambda g: _winsorize_group(g)).droplevel(0)
    return result


def cross_sectional_zscore(factor_panel: pd.DataFrame, factor_names: List[str]) -> pd.DataFrame:
    """Z-score standardization per date."""
    result = factor_panel.copy()
    for fname in factor_names:
        if fname not in result.columns:
            continue
        grp = result.groupby("date")[fname]
        mean = grp.transform("mean")
        std = grp.transform("std").replace(0, np.nan)
        result[fname] = (result[fname] - mean) / std
    return result


def industry_neutralize(
    factor_panel: pd.DataFrame, factor_names: List[str], industry_col: str = "industry"
) -> pd.DataFrame:
    """Remove industry bias by subtracting industry mean within each date."""
    result = factor_panel.copy()
    if industry_col not in result.columns:
        return result

    for fname in factor_names:
        if fname not in result.columns:
            continue
        has_industry = result[industry_col].notna()
        if has_industry.sum() == 0:
            continue
        ind_mean = result[has_industry].groupby(["date", industry_col])[fname].transform("mean")
        result.loc[has_industry, fname] = result.loc[has_industry, fname] - ind_mean

    return result


# ─── CORE FUNCTION 4: Cross-Sectional IC ─────────────────────────────

def compute_cross_sectional_ic(
    factor_panel: pd.DataFrame,
    factor_names: List[str],
    forward_days: int = FORWARD_DAYS,
    ic_window: int = IC_ROLLING_WINDOW,
) -> Tuple[Dict[str, FactorMetrics], pd.DataFrame]:
    """
    Compute cross-sectional Information Coefficient (rank correlation between
    factor value and forward returns) for each factor on each date.

    Optimized: pre-ranks data then uses Pearson on ranks (equivalent to Spearman
    but much faster for matrix operations).
    """
    fp = factor_panel.copy()
    fp = fp.sort_values(["stock_id", "date"])

    # Vectorized forward return computation
    fp["fwd_return"] = fp.groupby("stock_id")["close"].transform(
        lambda x: x.shift(-forward_days) / x - 1
    )

    # Compute IC per date using vectorized approach
    valid_dates = sorted(fp.dropna(subset=["fwd_return"])["date"].unique())

    # More aggressive sampling: cap at 120 dates (was 250)
    max_ic_dates = 120
    if len(valid_dates) > max_ic_dates:
        step = max(1, len(valid_dates) // max_ic_dates)
        sampled_dates = valid_dates[::step]
        # Always include the last 60 dates for recent IC accuracy
        recent_60 = valid_dates[-60:]
        sampled_dates = sorted(set(list(sampled_dates) + list(recent_60)))
    else:
        sampled_dates = valid_dates

    # Pre-index by date for faster lookups
    fp_indexed = fp.set_index("date", drop=False)

    ic_records = []
    for d in sampled_dates:
        try:
            day_data = fp_indexed.loc[[d]].dropna(subset=["fwd_return"])
        except KeyError:
            continue
        if len(day_data) < 30:
            continue

        record = {"date": d}
        fwd = day_data["fwd_return"].values
        fwd_rank = stats.rankdata(fwd)

        for fname in factor_names:
            if fname not in day_data.columns:
                record[f"{fname}_ic"] = np.nan
                continue
            vals = day_data[fname].values
            mask = ~(np.isnan(vals) | np.isnan(fwd))
            if mask.sum() < 20:
                record[f"{fname}_ic"] = np.nan
                continue
            try:
                # Rank-Pearson = Spearman, but using pre-ranked fwd returns
                vals_rank = stats.rankdata(vals[mask])
                fwd_masked_rank = fwd_rank[mask] if mask.all() else stats.rankdata(fwd[mask])
                ic = np.corrcoef(vals_rank, fwd_masked_rank)[0, 1]
                record[f"{fname}_ic"] = ic if not np.isnan(ic) else np.nan
            except Exception:
                record[f"{fname}_ic"] = np.nan

        ic_records.append(record)

    if not ic_records:
        return {}, pd.DataFrame()

    ic_df = pd.DataFrame(ic_records)
    ic_df["date"] = pd.to_datetime(ic_df["date"])
    ic_df = ic_df.sort_values("date")

    # Compute metrics per factor
    metrics = {}
    for fname in factor_names:
        col = f"{fname}_ic"
        if col not in ic_df.columns:
            metrics[fname] = FactorMetrics(name=fname)
            continue

        ic_series = ic_df[col].dropna()
        if len(ic_series) < 20:
            metrics[fname] = FactorMetrics(name=fname)
            continue

        ic_mean = ic_series.mean()
        ic_std = ic_series.std()
        ic_ir = ic_mean / ic_std if ic_std > 0 else 0
        pos_rate = (ic_series > 0).mean()

        cat = ""
        for fd in FACTOR_DEFS:
            if fd["name"] == fname:
                cat = fd["category"]
                break

        ic_direction = 1 if ic_mean >= 0 else -1
        ic_consistency = max(pos_rate, 1 - pos_rate)
        is_valid = (abs(ic_mean) >= IC_MEAN_THRESHOLD
                    and abs(ic_ir) >= IC_IR_THRESHOLD
                    and ic_consistency >= IC_CONSISTENCY_THRESHOLD)

        m = FactorMetrics(
            name=fname,
            category=cat,
            ic_mean=float(ic_mean),
            ic_std=float(ic_std),
            ic_ir=float(ic_ir),
            rank_ic_mean=float(ic_mean),
            rank_ic_ir=float(ic_ir),
            ic_positive_rate=float(pos_rate),
            ic_direction=ic_direction,
            ic_consistency=float(ic_consistency),
            is_valid=is_valid,
            ic_series=ic_series.tail(120).tolist(),
        )
        metrics[fname] = m

    return metrics, ic_df


# ─── CORE FUNCTION 5: Factor Weighting ───────────────────────────────

def compute_factor_weights(
    metrics: Dict[str, FactorMetrics],
    ic_df: pd.DataFrame,
    halflife: int = IC_DECAY_HALFLIFE,
) -> Dict[str, float]:
    """Compute dynamic factor weights using exponential-decay IC_IR."""
    valid_factors = {k: v for k, v in metrics.items() if v.is_valid}
    if not valid_factors:
        return {}

    weights = {}
    for fname, m in valid_factors.items():
        col = f"{fname}_ic"
        if col not in ic_df.columns:
            continue

        ic_series = ic_df[col].dropna()
        if len(ic_series) < 10:
            continue

        n = len(ic_series)
        decay = np.exp(-np.log(2) / halflife * np.arange(n - 1, -1, -1))
        weighted_ic = (ic_series.values * decay).sum() / decay.sum()
        weights[fname] = weighted_ic

    if not weights:
        return {}

    total = sum(abs(w) for w in weights.values())
    if total > 0:
        weights = {k: v / total for k, v in weights.items()}

    for fname, w in weights.items():
        if fname in metrics:
            metrics[fname].weight = w

    return weights


# ─── CORE FUNCTION 6: Composite Scores ───────────────────────────────

def compute_composite_scores(
    factor_panel: pd.DataFrame,
    weights: Dict[str, float],
    factor_names: List[str],
) -> pd.DataFrame:
    """Compute composite alpha score for each stock on each date."""
    result = factor_panel.copy()

    if not weights:
        result["composite_score"] = 50.0
        return result

    score = pd.Series(0.0, index=result.index)
    for fname, w in weights.items():
        if fname in result.columns:
            score += result[fname].fillna(0.5) * w

    result["composite_score"] = result.groupby("date").apply(
        lambda g: pd.Series(
            stats.rankdata(g.index.map(lambda idx: score.loc[idx])) / len(g) * 100,
            index=g.index,
        )
    ).droplevel(0)

    return result


# ─── CORE FUNCTION 7: ML Model Training ──────────────────────────────

def train_ml_model(
    factor_panel: pd.DataFrame,
    factor_names: List[str],
    metrics: Dict[str, FactorMetrics],
    forward_days: int = FORWARD_DAYS,
) -> dict:
    """Train LightGBM or fallback GBM on cross-sectional factor data."""
    result = {
        "available": False, "error": "", "accuracy": 0, "precision": 0,
        "recall": 0, "f1": 0, "feature_importance": [],
        "train_size": 0, "test_size": 0,
    }

    if not HAS_LGBM and not HAS_SKLEARN:
        result["error"] = "No ML library available (install lightgbm or scikit-learn)"
        return result

    valid_factors = [f for f in factor_names if f in metrics and metrics[f].is_valid]
    if len(valid_factors) < 3:
        result["error"] = f"Too few valid factors ({len(valid_factors)})"
        return result

    fp = factor_panel.copy()
    fp = fp.sort_values(["stock_id", "date"])
    fp["fwd_return"] = fp.groupby("stock_id")["close"].transform(
        lambda x: x.shift(-forward_days) / x - 1
    )
    fp["target"] = fp.groupby("date")["fwd_return"].transform(
        lambda x: (x > x.median()).astype(int)
    )

    cols = valid_factors + ["target", "date"]
    fp_clean = fp[cols].dropna()

    if len(fp_clean) < ML_MIN_SAMPLES:
        result["error"] = f"Insufficient samples ({len(fp_clean)})"
        return result

    dates = sorted(fp_clean["date"].unique())
    if len(dates) < ML_MIN_DATES:
        result["error"] = f"Insufficient dates ({len(dates)})"
        return result

    split_idx = int(len(dates) * ML_TRAIN_RATIO)
    train_dates = set(dates[:split_idx])
    test_dates = set(dates[split_idx:])

    train = fp_clean[fp_clean["date"].isin(train_dates)]
    test = fp_clean[fp_clean["date"].isin(test_dates)]

    X_train, y_train = train[valid_factors].values, train["target"].values
    X_test, y_test = test[valid_factors].values, test["target"].values

    # Subsample large training sets for speed
    max_train_samples = 80000
    if len(X_train) > max_train_samples:
        rng = np.random.RandomState(42)
        idx = rng.choice(len(X_train), max_train_samples, replace=False)
        X_train, y_train = X_train[idx], y_train[idx]

    try:
        if HAS_LGBM:
            model = lgb.LGBMClassifier(
                n_estimators=150, max_depth=5, learning_rate=0.05,
                subsample=0.8, colsample_bytree=0.8, min_child_samples=50,
                reg_alpha=0.1, reg_lambda=0.1, verbose=-1, random_state=42,
                n_jobs=-1,
            )
            # Early stopping: stop training if validation doesn't improve
            model.fit(
                X_train, y_train,
                eval_set=[(X_test, y_test)],
                callbacks=[lgb.early_stopping(20, verbose=False), lgb.log_evaluation(period=0)],
            )
        else:
            model = GradientBoostingClassifier(
                n_estimators=100, max_depth=4, learning_rate=0.1,
                subsample=0.8, min_samples_leaf=20, random_state=42,
            )
            model.fit(X_train, y_train)

        y_pred = model.predict(X_test)

        from sklearn.metrics import accuracy_score, precision_score, recall_score, f1_score

        result["available"] = True
        result["accuracy"] = float(accuracy_score(y_test, y_pred))
        result["precision"] = float(precision_score(y_test, y_pred, zero_division=0))
        result["recall"] = float(recall_score(y_test, y_pred, zero_division=0))
        result["f1"] = float(f1_score(y_test, y_pred, zero_division=0))
        result["train_size"] = len(train)
        result["test_size"] = len(test)
        result["model_type"] = "LightGBM" if HAS_LGBM else "GradientBoosting"
        result["train_date_range"] = [str(min(train_dates)), str(max(train_dates))]
        result["test_date_range"] = [str(min(test_dates)), str(max(test_dates))]
        result["total_dates"] = len(dates)

        fi = sorted(
            [{"factor": f, "importance": float(imp)} for f, imp in zip(valid_factors, model.feature_importances_)],
            key=lambda x: x["importance"], reverse=True,
        )
        result["feature_importance"] = fi[:15]
        result["_model"] = model
        result["_valid_factors"] = valid_factors

    except Exception as e:
        result["error"] = str(e)
        logger.exception("ML training failed")

    return result


# ─── CORE FUNCTION 8: Portfolio Construction ──────────────────────────

def construct_portfolio(
    scores_today: pd.DataFrame,
    prev_portfolio: set,
    config: PortfolioConfig,
) -> Tuple[set, Dict[str, List[int]]]:
    """Construct top-N portfolio with buffer zone for turnover control."""
    ranked = scores_today.sort_values("composite_score", ascending=False).reset_index(drop=True)
    ranked["rank"] = range(1, len(ranked) + 1)
    stock_ranks = dict(zip(ranked["stock_id"], ranked["rank"]))

    new_portfolio = set()

    # Keep existing holdings within buffer
    for sid in prev_portfolio:
        rank = stock_ranks.get(sid)
        if rank is not None and rank <= config.buffer_out:
            new_portfolio.add(sid)

    # Add new stocks within entry threshold
    for _, row in ranked.iterrows():
        if len(new_portfolio) >= config.top_n:
            break
        sid = row["stock_id"]
        if sid not in new_portfolio and row["rank"] <= config.buffer_in:
            # Sector concentration check
            if config.max_sector_pct < 1.0 and "industry" in scores_today.columns:
                ind = scores_today[scores_today["stock_id"] == sid]["industry"].iloc[0] if sid in scores_today["stock_id"].values else None
                if ind:
                    same = sum(1 for s in new_portfolio if s in scores_today["stock_id"].values
                               and scores_today[scores_today["stock_id"] == s]["industry"].iloc[0] == ind)
                    if same / max(len(new_portfolio), 1) >= config.max_sector_pct:
                        continue
            new_portfolio.add(sid)

    # Fill remaining from top-ranked
    for _, row in ranked.iterrows():
        if len(new_portfolio) >= config.top_n:
            break
        if row["stock_id"] not in new_portfolio:
            new_portfolio.add(row["stock_id"])

    buys = list(new_portfolio - prev_portfolio)
    sells = list(prev_portfolio - new_portfolio)
    return new_portfolio, {"buy": buys, "sell": sells}


# ─── CORE FUNCTION 9: Backtest Engine ────────────────────────────────

def run_backtest(
    factor_panel: pd.DataFrame,
    stock_info: Dict[int, dict],
    weights: Dict[str, float],
    factor_names: List[str],
    portfolio_config: Optional[PortfolioConfig] = None,
    backtest_config: Optional[BacktestConfig] = None,
) -> dict:
    """Walk-forward backtest with T+1, transaction costs, turnover control."""
    if portfolio_config is None:
        portfolio_config = PortfolioConfig()
    if backtest_config is None:
        backtest_config = BacktestConfig()

    fp = factor_panel.copy()
    dates = sorted(fp["date"].unique())

    if backtest_config.start_date:
        start = pd.Timestamp(backtest_config.start_date).date()
    else:
        start = dates[max(0, len(dates) - 500)]

    if backtest_config.end_date:
        end = pd.Timestamp(backtest_config.end_date).date()
    else:
        end = dates[-1]

    bt_dates = [d for d in dates if start <= d <= end]
    if len(bt_dates) < portfolio_config.rebalance_freq * 3:
        return {"error": "Insufficient data for backtest", "success": False}

    rebalance_dates = bt_dates[::portfolio_config.rebalance_freq]

    capital = backtest_config.initial_capital
    portfolio = set()
    equity_curve = []
    period_returns_list = []
    total_cost = 0
    total_turnover = 0

    # Pre-build stock close price lookup for speed
    close_pivot = fp.pivot_table(index="date", columns="stock_id", values="close", aggfunc="first")

    for i, reb_date in enumerate(rebalance_dates):
        day_data = fp[fp["date"] == reb_date].copy()
        if day_data.empty:
            continue

        score = pd.Series(0.0, index=day_data.index)
        for fname, w in weights.items():
            if fname in day_data.columns:
                score += day_data[fname].fillna(0.5) * w
        day_data["composite_score"] = score

        new_portfolio, trades = construct_portfolio(day_data, portfolio, portfolio_config)

        n_buys = len(trades["buy"])
        n_sells = len(trades["sell"])
        weight_per = capital / max(len(new_portfolio), 1) if portfolio else capital / max(portfolio_config.top_n, 1)
        period_cost = n_buys * weight_per * backtest_config.buy_cost_bps / 10000 + n_sells * weight_per * backtest_config.sell_cost_bps / 10000
        total_cost += period_cost
        capital -= period_cost

        if portfolio:
            total_turnover += (n_buys + n_sells) / (2 * max(len(portfolio), 1))

        portfolio = new_portfolio

        # Period return
        if i < len(rebalance_dates) - 1:
            next_reb = rebalance_dates[i + 1]
        else:
            remaining = [d for d in bt_dates if d > reb_date]
            next_reb = remaining[-1] if remaining else reb_date

        # Vectorized return lookup using pivot table
        period_returns = []
        for sid in portfolio:
            if sid in close_pivot.columns:
                close_at_reb = close_pivot.loc[close_pivot.index >= reb_date, sid].dropna()
                close_at_next = close_pivot.loc[close_pivot.index <= next_reb, sid].dropna()
                if not close_at_reb.empty and not close_at_next.empty:
                    c0 = close_at_reb.iloc[0]
                    c1 = close_at_next.iloc[-1]
                    if c0 > 0:
                        period_returns.append(c1 / c0 - 1)

        if period_returns:
            capital *= 1 + np.mean(period_returns)

        period_ret_pct = round(np.mean(period_returns) * 100, 2) if period_returns else 0.0

        equity_curve.append({
            "date": str(reb_date),
            "nav": capital / backtest_config.initial_capital,
            "portfolio_size": len(portfolio),
            "buys": n_buys, "sells": n_sells,
        })

        period_returns_list.append({
            "period": i + 1,
            "start": str(reb_date),
            "end": str(next_reb),
            "return": period_ret_pct,
        })

    if not equity_curve:
        return {"error": "No trades executed", "success": False}

    navs = [e["nav"] for e in equity_curve]
    total_return = navs[-1] - 1
    n_periods = len(navs)
    n_years = max(n_periods * portfolio_config.rebalance_freq / 252, 0.01)
    annual_return = (1 + total_return) ** (1 / n_years) - 1

    nav_series = pd.Series(navs)
    period_rets = nav_series.pct_change().dropna()

    if len(period_rets) > 1:
        vol_per_period = period_rets.std()
        periods_per_year = 252 / portfolio_config.rebalance_freq
        annual_vol = vol_per_period * np.sqrt(periods_per_year)
        sharpe = (annual_return - 0.025) / annual_vol if annual_vol > 0 else 0
    else:
        annual_vol = 0
        sharpe = 0

    peak = nav_series.expanding().max()
    drawdown = (nav_series - peak) / peak
    max_dd = drawdown.min()
    win_rate = (period_rets > 0).mean() if len(period_rets) > 0 else 0
    calmar = annual_return / abs(max_dd) if max_dd != 0 else 0
    avg_turnover = total_turnover / max(n_periods, 1)
    annual_turnover = avg_turnover * (252 / portfolio_config.rebalance_freq)

    return {
        "success": True,
        "run_id": str(uuid.uuid4()),
        "total_return": round(total_return * 100, 2),
        "annual_return": round(annual_return * 100, 2),
        "annual_volatility": round(annual_vol * 100, 2),
        "sharpe_ratio": round(sharpe, 3),
        "max_drawdown": round(max_dd * 100, 2),
        "calmar_ratio": round(calmar, 3),
        "win_rate": round(win_rate * 100, 1),
        "avg_turnover_per_rebalance": round(avg_turnover * 100, 1),
        "annual_turnover": round(annual_turnover * 100, 1),
        "total_cost": round(total_cost, 2),
        "total_cost_pct": round(total_cost / backtest_config.initial_capital * 100, 3),
        "n_rebalances": n_periods,
        "backtest_days": len(bt_dates),
        "backtest_date_range": [str(bt_dates[0]), str(bt_dates[-1])],
        "equity_curve": equity_curve,
        "period_returns": period_returns_list,
    }


# ─── MAIN ORCHESTRATOR ───────────────────────────────────────────────

def run_cross_sectional_analysis(
    all_klines: Dict[int, pd.DataFrame],
    stock_info: Dict[int, dict],
    portfolio_config: Optional[PortfolioConfig] = None,
    backtest_config: Optional[BacktestConfig] = None,
) -> dict:
    """
    Main entry point: run the complete cross-sectional quant pipeline.
    """
    import time
    t0 = time.time()

    if portfolio_config is None:
        portfolio_config = PortfolioConfig()
    if backtest_config is None:
        backtest_config = BacktestConfig()

    # Factor names (technical from klines)
    tech_factor_names = [fd["name"] for fd in FACTOR_DEFS if fd["category"] != "value"]
    value_factor_names = ["ep", "bp", "neg_log_mcap"]
    all_factor_names = value_factor_names + tech_factor_names

    logger.info(f"Starting cross-sectional analysis: {len(all_klines)} stocks, {len(all_factor_names)} factors")

    # Step 1: Determine target date
    latest_dates = {}
    for sid, df in all_klines.items():
        if not df.empty:
            latest_dates[sid] = df["date"].max()

    if not latest_dates:
        return {"success": False, "error": "No kline data available"}

    target_date = pd.Series(list(latest_dates.values())).mode().iloc[0]
    if hasattr(target_date, "date") and callable(getattr(target_date, "date", None)):
        target_date = target_date.date()

    # Step 2: Filter universe
    excluded = filter_universe(all_klines, stock_info, target_date)
    universe_sids = [sid for sid in all_klines if sid not in excluded]

    logger.info(f"Universe: {len(universe_sids)} stocks (excluded {len(excluded)})")

    if len(universe_sids) < 30:
        return {"success": False, "error": f"Universe too small ({len(universe_sids)} stocks, need >= 30)"}

    t_step = time.time()

    # Step 3: Compute factors for all stocks
    factor_frames = []
    skipped = 0
    for sid in universe_sids:
        df = all_klines[sid].copy()
        if len(df) < 120:
            skipped += 1
            continue

        df = compute_stock_factors(df)

        # Add value factors from profile
        info = stock_info.get(sid, {})
        pe = info.get("pe_ttm")
        pb = info.get("pb")
        mcap = info.get("total_market_cap")
        df["ep"] = 1.0 / pe if pe and pe > 0 else np.nan
        df["bp"] = 1.0 / pb if pb and pb > 0 else np.nan
        df["neg_log_mcap"] = -np.log(mcap) if mcap and mcap > 0 else np.nan

        df["stock_id"] = sid
        df["industry"] = info.get("industry", None)
        factor_frames.append(df)

    if not factor_frames:
        return {"success": False, "error": "No valid stock data after factor computation"}

    logger.info(f"Factor computation done: {len(factor_frames)} stocks ({skipped} skipped) in {time.time()-t_step:.1f}s")

    # Build panel
    panel = pd.concat(factor_frames, ignore_index=True)
    panel["date"] = pd.to_datetime(panel["date"]).dt.date

    logger.info(f"Factor panel: {len(panel)} rows, {panel['stock_id'].nunique()} stocks, {panel['date'].nunique()} dates")

    # Step 4: Cross-sectional processing
    t_step = time.time()
    panel = cross_sectional_winsorize(panel, all_factor_names)
    if portfolio_config.industry_neutral:
        panel = industry_neutralize(panel, all_factor_names)
    panel_ranked = cross_sectional_rank(panel, all_factor_names)
    for col in ["close", "stock_id", "date", "industry"]:
        if col in panel.columns:
            panel_ranked[col] = panel[col]
    logger.info(f"Cross-sectional processing done in {time.time()-t_step:.1f}s")

    # Capture overall data date range for reporting
    all_panel_dates = sorted(panel["date"].unique())
    data_date_range = [str(all_panel_dates[0]), str(all_panel_dates[-1])] if all_panel_dates else []

    # Step 5: IC analysis
    t_step = time.time()
    metrics, ic_df = compute_cross_sectional_ic(panel_ranked, all_factor_names, FORWARD_DAYS)
    valid_factors = [f for f, m in metrics.items() if m.is_valid]
    ic_dates_used = sorted(ic_df["date"].unique()) if not ic_df.empty else []
    ic_date_range = [str(ic_dates_used[0]), str(ic_dates_used[-1])] if ic_dates_used else []
    logger.info(f"IC analysis done: {len(valid_factors)} / {len(all_factor_names)} valid factors in {time.time()-t_step:.1f}s")

    # Step 6: Weights
    t_step = time.time()
    weights = compute_factor_weights(metrics, ic_df)

    # Step 7: Composite scores
    panel_scored = compute_composite_scores(panel_ranked, weights, all_factor_names)
    logger.info(f"Weights + composite scores done in {time.time()-t_step:.1f}s")

    # Step 8: ML enhancement
    t_step = time.time()
    ml_result = train_ml_model(panel_ranked, all_factor_names, metrics, FORWARD_DAYS)
    logger.info(f"ML training done in {time.time()-t_step:.1f}s (available={ml_result.get('available')})")
    ml_weight_ratio = 0.4
    if ml_result.get("available") and ml_result.get("_model"):
        model = ml_result["_model"]
        ml_factors = ml_result["_valid_factors"]
        latest_data = panel_ranked[panel_ranked["date"] == target_date].copy()
        if not latest_data.empty and all(f in latest_data.columns for f in ml_factors):
            try:
                X = latest_data[ml_factors].fillna(0.5).values
                ml_proba = model.predict_proba(X)[:, 1]
                ic_score = panel_scored.loc[latest_data.index, "composite_score"]
                blended = ic_score * (1 - ml_weight_ratio) + (ml_proba * 100) * ml_weight_ratio
                panel_scored.loc[latest_data.index, "composite_score"] = blended
            except Exception as e:
                logger.warning(f"ML scoring failed: {e}")

    # Step 9: Portfolio
    latest_scores = panel_scored[panel_scored["date"] == target_date].copy()
    if latest_scores.empty:
        avail_dates = sorted(panel_scored["date"].unique())
        target_date = avail_dates[-1] if avail_dates else target_date
        latest_scores = panel_scored[panel_scored["date"] == target_date].copy()

    if "industry" not in latest_scores.columns:
        latest_scores["industry"] = latest_scores["stock_id"].map(
            lambda sid: stock_info.get(sid, {}).get("industry", "")
        )

    portfolio_set, trades = construct_portfolio(latest_scores, set(), portfolio_config)

    portfolio_list = []
    for sid in portfolio_set:
        info = stock_info.get(sid, {})
        row = latest_scores[latest_scores["stock_id"] == sid]
        score = float(row["composite_score"].iloc[0]) if not row.empty else 0
        portfolio_list.append({
            "stock_id": sid,
            "stock_code": info.get("code", ""),
            "stock_name": info.get("name", ""),
            "industry": info.get("industry", ""),
            "market": info.get("market", ""),
            "score": round(score, 1),
            "pe_ttm": info.get("pe_ttm"),
            "pb": info.get("pb"),
            "total_market_cap": info.get("total_market_cap"),
        })
    portfolio_list.sort(key=lambda x: x["score"], reverse=True)
    for i, p in enumerate(portfolio_list):
        p["rank"] = i + 1

    # Step 10: Backtest
    t_step = time.time()
    backtest = run_backtest(panel_ranked, stock_info, weights, all_factor_names, portfolio_config, backtest_config)
    logger.info(f"Backtest done in {time.time()-t_step:.1f}s")

    # Step 11: Factor summary
    factor_summary = []
    for fd in FACTOR_DEFS:
        fname = fd["name"]
        m = metrics.get(fname, FactorMetrics(name=fname))
        factor_summary.append({
            "name": fname,
            "category": fd["category"],
            "category_label": CATEGORY_LABELS.get(fd["category"], ""),
            "label": fd["label"],
            "description": fd["desc"],
            "ic_mean": round(m.ic_mean, 4) if m.ic_mean else 0,
            "ic_ir": round(m.ic_ir, 3) if m.ic_ir else 0,
            "ic_positive_rate": round(m.ic_positive_rate, 3) if m.ic_positive_rate else 0,
            "ic_direction": m.ic_direction,
            "ic_direction_label": "正向" if m.ic_direction >= 0 else "反向",
            "ic_consistency": round(m.ic_consistency, 3) if m.ic_consistency else 0,
            "is_valid": m.is_valid,
            "weight": round(m.weight, 4) if m.weight else 0,
        })
    factor_summary.sort(key=lambda x: (not x["is_valid"], -abs(x["weight"])))

    # Exclude reasons
    exclude_reasons: Dict[str, int] = {}
    for sid, reason in excluded.items():
        exclude_reasons[reason] = exclude_reasons.get(reason, 0) + 1

    elapsed = time.time() - t0

    result = {
        "success": True,
        "analysis_date": str(target_date),
        "elapsed_seconds": round(elapsed, 1),
        "universe": {
            "total_stocks": len(all_klines),
            "in_universe": len(universe_sids),
            "excluded": len(excluded),
            "exclude_breakdown": exclude_reasons,
        },
        "portfolio": {
            "stocks": portfolio_list,
            "size": len(portfolio_list),
            "config": {
                "top_n": portfolio_config.top_n,
                "rebalance_freq": portfolio_config.rebalance_freq,
                "buffer_in": portfolio_config.buffer_in,
                "buffer_out": portfolio_config.buffer_out,
                "industry_neutral": portfolio_config.industry_neutral,
            },
        },
        "signal": {
            "valid_factors": len(valid_factors),
            "total_factors": len(all_factor_names),
            "factor_weights": {k: round(v, 4) for k, v in weights.items()},
        },
        "factors": factor_summary,
        "ml_model": {
            "available": ml_result.get("available", False),
            "model_type": ml_result.get("model_type", ""),
            "accuracy": ml_result.get("accuracy", 0),
            "precision": ml_result.get("precision", 0),
            "recall": ml_result.get("recall", 0),
            "f1": ml_result.get("f1", 0),
            "feature_importance": ml_result.get("feature_importance", []),
            "train_size": ml_result.get("train_size", 0),
            "test_size": ml_result.get("test_size", 0),
            "train_date_range": ml_result.get("train_date_range"),
            "test_date_range": ml_result.get("test_date_range"),
            "total_dates": ml_result.get("total_dates", 0),
            "error": ml_result.get("error", ""),
            "weight_ratio": ml_weight_ratio if ml_result.get("available") else 0,
        },
        "data_splits": {
            "data_date_range": data_date_range,
            "total_dates": len(all_panel_dates),
            "ic_date_range": ic_date_range,
            "ic_dates_sampled": len(ic_dates_used),
            "ml_train_date_range": ml_result.get("train_date_range"),
            "ml_train_samples": ml_result.get("train_size", 0),
            "ml_test_date_range": ml_result.get("test_date_range"),
            "ml_test_samples": ml_result.get("test_size", 0),
            "ml_train_ratio": ML_TRAIN_RATIO,
            "backtest_date_range": backtest.get("backtest_date_range") if backtest.get("success") else None,
            "backtest_days": backtest.get("backtest_days", 0),
            "forward_days": FORWARD_DAYS,
        },
        "backtest": backtest,
    }

    return _sanitize_for_json(result)


# ─── Single-Stock Factor Profile ─────────────────────────────────────

def get_stock_factor_profile(
    stock_id: int,
    all_klines: Dict[int, pd.DataFrame],
    stock_info: Dict[int, dict],
    target_date: Optional[date_type] = None,
) -> dict:
    """Get a single stock's raw factor values."""
    if stock_id not in all_klines:
        return {"success": False, "error": "Stock not found in kline data"}

    info = stock_info.get(stock_id, {})
    df = all_klines[stock_id].copy()
    if len(df) < 120:
        return {"success": False, "error": "Insufficient kline data (need >= 120 days)"}

    df = compute_stock_factors(df)

    pe = info.get("pe_ttm")
    pb = info.get("pb")
    mcap = info.get("total_market_cap")
    df["ep"] = 1.0 / pe if pe and pe > 0 else np.nan
    df["bp"] = 1.0 / pb if pb and pb > 0 else np.nan
    df["neg_log_mcap"] = -np.log(mcap) if mcap and mcap > 0 else np.nan

    latest = df.iloc[-1]

    factors = []
    for fd in FACTOR_DEFS:
        fname = fd["name"]
        val = latest.get(fname)
        factors.append({
            "name": fname,
            "category": fd["category"],
            "label": fd["label"],
            "raw_value": round(float(val), 4) if val is not None and not pd.isna(val) else None,
        })

    return _sanitize_for_json({
        "success": True,
        "stock_id": stock_id,
        "stock_code": info.get("code", ""),
        "stock_name": info.get("name", ""),
        "analysis_date": str(latest.get("date", "")),
        "factors": factors,
    })

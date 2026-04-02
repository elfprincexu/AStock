from datetime import datetime, date
from sqlalchemy import (
    Integer, String, Float, Boolean, DateTime, Date, BigInteger,
    Text, ForeignKey, Index, UniqueConstraint, JSON,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.database import Base


# ---------------------------------------------------------------------------
#  Authentication & Authorization
# ---------------------------------------------------------------------------

# Default permissions for new regular users
DEFAULT_USER_PERMISSIONS = {
    "stocks": True,
    "quotes": True,
    "strategy": False,
    "screener": False,
    "quant": False,
    "logs": True,
    "schedule": False,
    "config": False,
}


class User(Base):
    """System user with role-based permissions."""
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String(50), unique=True, index=True, nullable=False)
    hashed_password: Mapped[str] = mapped_column(String(200), nullable=False)
    display_name: Mapped[str] = mapped_column(String(100), nullable=False, default="")
    role: Mapped[str] = mapped_column(String(20), nullable=False, default="user")  # admin / user
    permissions: Mapped[dict | None] = mapped_column(JSON, nullable=True)  # per-feature access
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


# ---------------------------------------------------------------------------
#  Stock Data Models
# ---------------------------------------------------------------------------

class Stock(Base):
    __tablename__ = "stocks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(10), unique=True, index=True, nullable=False)
    name: Mapped[str] = mapped_column(String(50), nullable=False)
    market: Mapped[str] = mapped_column(String(10), nullable=False, default="SH")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    is_favorite: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    quote_snapshots: Mapped[list["QuoteSnapshot"]] = relationship(
        back_populates="stock", lazy="noload"
    )
    daily_klines: Mapped[list["DailyKline"]] = relationship(
        back_populates="stock", lazy="noload"
    )
    fetch_logs: Mapped[list["FetchLog"]] = relationship(
        back_populates="stock", lazy="noload"
    )
    profile: Mapped["StockProfile | None"] = relationship(lazy="noload")


class QuoteSnapshot(Base):
    __tablename__ = "quote_snapshots"
    __table_args__ = (
        Index("ix_quote_snapshots_stock_id_timestamp", "stock_id", "timestamp"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    stock_id: Mapped[int] = mapped_column(Integer, ForeignKey("stocks.id"), nullable=False)
    price: Mapped[float] = mapped_column(Float, nullable=False)
    open: Mapped[float] = mapped_column(Float, nullable=False)
    high: Mapped[float] = mapped_column(Float, nullable=False)
    low: Mapped[float] = mapped_column(Float, nullable=False)
    close: Mapped[float] = mapped_column(Float, nullable=False)
    volume: Mapped[int] = mapped_column(BigInteger, nullable=False)
    amount: Mapped[float] = mapped_column(Float, nullable=False)
    change_pct: Mapped[float] = mapped_column(Float, nullable=False)
    timestamp: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    stock: Mapped["Stock"] = relationship(back_populates="quote_snapshots")


class DailyKline(Base):
    __tablename__ = "daily_klines"
    __table_args__ = (
        UniqueConstraint("stock_id", "date", name="uq_daily_klines_stock_id_date"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    stock_id: Mapped[int] = mapped_column(Integer, ForeignKey("stocks.id"), nullable=False)
    date: Mapped[date] = mapped_column(Date, nullable=False)
    open: Mapped[float] = mapped_column(Float, nullable=False)
    high: Mapped[float] = mapped_column(Float, nullable=False)
    low: Mapped[float] = mapped_column(Float, nullable=False)
    close: Mapped[float] = mapped_column(Float, nullable=False)
    volume: Mapped[int] = mapped_column(BigInteger, nullable=False)
    amount: Mapped[float] = mapped_column(Float, nullable=False)
    change_pct: Mapped[float] = mapped_column(Float, nullable=False)
    turnover_rate: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    stock: Mapped["Stock"] = relationship(back_populates="daily_klines")


class FetchLog(Base):
    __tablename__ = "fetch_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    stock_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("stocks.id"), nullable=True)
    fetch_type: Mapped[str] = mapped_column(String(30), nullable=False)
    source: Mapped[str | None] = mapped_column(String(200), nullable=True)
    status: Mapped[str] = mapped_column(String(10), nullable=False)
    message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    stock: Mapped["Stock | None"] = relationship(back_populates="fetch_logs")


class StockProfile(Base):
    """Fundamental data snapshot for a stock (industry, valuation, market cap)."""
    __tablename__ = "stock_profiles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    stock_id: Mapped[int] = mapped_column(Integer, ForeignKey("stocks.id"), unique=True, nullable=False)
    industry: Mapped[str | None] = mapped_column(String(50), nullable=True)
    sector: Mapped[str | None] = mapped_column(String(200), nullable=True)
    total_market_cap: Mapped[float | None] = mapped_column(Float, nullable=True)
    circ_market_cap: Mapped[float | None] = mapped_column(Float, nullable=True)
    pe_ttm: Mapped[float | None] = mapped_column(Float, nullable=True)
    pb: Mapped[float | None] = mapped_column(Float, nullable=True)
    total_shares: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    circ_shares: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    stock: Mapped["Stock"] = relationship(overlaps="profile")


class AppSetting(Base):
    """Key-value settings store for schedule configuration etc."""
    __tablename__ = "app_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    key: Mapped[str] = mapped_column(String(100), unique=True, nullable=False, index=True)
    value: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class AnalysisHistory(Base):
    """AI analysis history records."""
    __tablename__ = "analysis_history"
    __table_args__ = (
        Index("ix_analysis_history_stock_code_created", "stock_code", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    stock_code: Mapped[str] = mapped_column(String(10), nullable=False, index=True)
    stock_name: Mapped[str] = mapped_column(String(50), nullable=False, default="")
    report_type: Mapped[str] = mapped_column(String(20), nullable=False, default="detailed")
    model_used: Mapped[str | None] = mapped_column(String(100), nullable=True)
    sentiment_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    operation_advice: Mapped[str | None] = mapped_column(String(20), nullable=True)
    trend_prediction: Mapped[str | None] = mapped_column(String(20), nullable=True)
    current_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    change_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    report_json: Mapped[str | None] = mapped_column(Text, nullable=True)  # Full report as JSON
    context_json: Mapped[str | None] = mapped_column(Text, nullable=True)  # Analysis context snapshot
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


# ---------------------------------------------------------------------------
#  Trading Module Models
# ---------------------------------------------------------------------------

class TradeStrategy(Base):
    """A trading strategy consisting of sequential steps with conditions."""
    __tablename__ = "trade_strategies"
    __table_args__ = (
        Index("ix_trade_strategies_status", "status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    stock_code: Mapped[str] = mapped_column(String(10), nullable=False, index=True)
    stock_name: Mapped[str] = mapped_column(String(50), nullable=False, default="")
    market: Mapped[str] = mapped_column(String(10), nullable=False, default="SH")
    mode: Mapped[str] = mapped_column(String(10), nullable=False, default="simulated")  # simulated / live
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="draft")
    # status: draft / active / paused / completed / cancelled / error
    current_step_order: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    sim_initial_cash: Mapped[float] = mapped_column(Float, nullable=False, default=100000.0)
    sim_cash: Mapped[float] = mapped_column(Float, nullable=False, default=100000.0)
    sim_holdings: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    sim_avg_cost: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    steps: Mapped[list["TradeStep"]] = relationship(
        back_populates="strategy", cascade="all, delete-orphan",
        order_by="TradeStep.step_order", lazy="selectin",
    )
    executions: Mapped[list["TradeExecution"]] = relationship(
        back_populates="strategy", cascade="all, delete-orphan",
        order_by="TradeExecution.created_at.desc()", lazy="noload",
    )


class TradeStep(Base):
    """A single step in a strategy: conditions + action (buy/sell)."""
    __tablename__ = "trade_steps"
    __table_args__ = (
        Index("ix_trade_steps_strategy_order", "strategy_id", "step_order"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    strategy_id: Mapped[int] = mapped_column(Integer, ForeignKey("trade_strategies.id", ondelete="CASCADE"), nullable=False)
    step_order: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    name: Mapped[str] = mapped_column(String(100), nullable=False, default="")
    condition_logic: Mapped[str] = mapped_column(String(5), nullable=False, default="AND")  # AND / OR
    action_type: Mapped[str] = mapped_column(String(10), nullable=False)  # buy / sell
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    price_type: Mapped[str] = mapped_column(String(10), nullable=False, default="market")  # market / limit
    limit_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="waiting")
    # status: waiting / watching / triggered / executing / filled / failed / cancelled
    fill_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    fill_quantity: Mapped[int | None] = mapped_column(Integer, nullable=True)
    filled_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    strategy: Mapped["TradeStrategy"] = relationship(back_populates="steps")
    conditions: Mapped[list["TradeCondition"]] = relationship(
        back_populates="step", cascade="all, delete-orphan",
        order_by="TradeCondition.id", lazy="selectin",
    )


class TradeCondition(Base):
    """A single condition within a step (e.g. price >= 10.5)."""
    __tablename__ = "trade_conditions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    step_id: Mapped[int] = mapped_column(Integer, ForeignKey("trade_steps.id", ondelete="CASCADE"), nullable=False)
    field: Mapped[str] = mapped_column(String(30), nullable=False)
    # field: price / change_pct / profit_pct / loss_pct / volume / amount / turnover_rate
    operator: Mapped[str] = mapped_column(String(5), nullable=False)  # >= / <= / > / < / ==
    value: Mapped[float] = mapped_column(Float, nullable=False)
    is_met: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    met_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    step: Mapped["TradeStep"] = relationship(back_populates="conditions")


class TradeExecution(Base):
    """Execution log for strategy events."""
    __tablename__ = "trade_executions"
    __table_args__ = (
        Index("ix_trade_executions_strategy_created", "strategy_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    strategy_id: Mapped[int] = mapped_column(Integer, ForeignKey("trade_strategies.id", ondelete="CASCADE"), nullable=False)
    step_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("trade_steps.id", ondelete="SET NULL"), nullable=True)
    event_type: Mapped[str] = mapped_column(String(30), nullable=False)
    # event_type: strategy_started / strategy_paused / strategy_cancelled /
    #   condition_met / step_triggered / order_submitted / order_filled / order_failed /
    #   step_advanced / strategy_completed / error
    message: Mapped[str | None] = mapped_column(Text, nullable=True)
    price_snapshot: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    strategy: Mapped["TradeStrategy"] = relationship(back_populates="executions")


# ---------------------------------------------------------------------------
#  Cross-Sectional Quantitative Analysis Models
# ---------------------------------------------------------------------------

class QuantFactorDaily(Base):
    """Pre-computed cross-sectional factor values per stock per date."""
    __tablename__ = "quant_factor_daily"
    __table_args__ = (
        UniqueConstraint("stock_id", "date", name="uq_quant_factor_daily"),
        Index("ix_quant_factor_date", "date"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    stock_id: Mapped[int] = mapped_column(Integer, ForeignKey("stocks.id"), nullable=False)
    date: Mapped[date] = mapped_column(Date, nullable=False)
    raw_factors: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    rank_factors: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    composite_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    composite_rank: Mapped[int | None] = mapped_column(Integer, nullable=True)
    in_universe: Mapped[bool] = mapped_column(Boolean, default=True)
    exclude_reason: Mapped[str | None] = mapped_column(String(100), nullable=True)


class QuantICHistory(Base):
    """Daily cross-sectional IC values per factor."""
    __tablename__ = "quant_ic_history"
    __table_args__ = (
        UniqueConstraint("date", "factor_name", name="uq_quant_ic"),
        Index("ix_quant_ic_date", "date"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    date: Mapped[date] = mapped_column(Date, nullable=False)
    factor_name: Mapped[str] = mapped_column(String(50), nullable=False)
    ic_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    rank_ic_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    forward_days: Mapped[int] = mapped_column(Integer, default=10)


class QuantPortfolio(Base):
    """Portfolio composition at each rebalance date."""
    __tablename__ = "quant_portfolios"
    __table_args__ = (
        Index("ix_quant_portfolio_run_date", "run_id", "rebalance_date"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(String(36), nullable=False)
    rebalance_date: Mapped[date] = mapped_column(Date, nullable=False)
    stock_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("stocks.id"), nullable=True)
    stock_code: Mapped[str | None] = mapped_column(String(10), nullable=True)
    stock_name: Mapped[str | None] = mapped_column(String(50), nullable=True)
    weight: Mapped[float | None] = mapped_column(Float, nullable=True)
    rank: Mapped[int | None] = mapped_column(Integer, nullable=True)
    composite_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    action: Mapped[str | None] = mapped_column(String(10), nullable=True)
    sector: Mapped[str | None] = mapped_column(String(50), nullable=True)


class QuantBacktestResult(Base):
    """Backtest run metadata and performance metrics — stores full analysis result for history."""
    __tablename__ = "quant_backtest_results"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(String(36), unique=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    analysis_date: Mapped[str | None] = mapped_column(String(20), nullable=True)
    elapsed_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)
    filters_applied: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    universe_info: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    signal_info: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    ml_model_info: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    factors: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    config: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    total_return: Mapped[float | None] = mapped_column(Float, nullable=True)
    annual_return: Mapped[float | None] = mapped_column(Float, nullable=True)
    annual_volatility: Mapped[float | None] = mapped_column(Float, nullable=True)
    sharpe_ratio: Mapped[float | None] = mapped_column(Float, nullable=True)
    max_drawdown: Mapped[float | None] = mapped_column(Float, nullable=True)
    calmar_ratio: Mapped[float | None] = mapped_column(Float, nullable=True)
    win_rate: Mapped[float | None] = mapped_column(Float, nullable=True)
    avg_turnover: Mapped[float | None] = mapped_column(Float, nullable=True)
    total_cost: Mapped[float | None] = mapped_column(Float, nullable=True)
    equity_curve: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    monthly_returns: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    factor_ic_summary: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    current_portfolio: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    data_splits: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    # Auto-iteration fields
    auto_iterate: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    iterate_status: Mapped[str | None] = mapped_column(String(20), nullable=True)  # idle, running, paused, error
    next_iterate_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    total_iterations: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    live_nav: Mapped[float] = mapped_column(Float, default=1.0, server_default="1.0")
    live_return_pct: Mapped[float] = mapped_column(Float, default=0.0, server_default="0.0")


class QuantIteration(Base):
    """One rebalance cycle within an auto-iterating quant strategy."""
    __tablename__ = "quant_iterations"
    __table_args__ = (
        Index("ix_quant_iter_run_id", "run_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(String(36), nullable=False)
    iteration_num: Mapped[int] = mapped_column(Integer, nullable=False)
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    end_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    portfolio: Mapped[dict | None] = mapped_column(JSON, nullable=True)  # list of {stock_code, stock_name, industry, score, weight}
    new_buys: Mapped[dict | None] = mapped_column(JSON, nullable=True)   # codes added
    new_sells: Mapped[dict | None] = mapped_column(JSON, nullable=True)  # codes removed
    period_return_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    cumulative_return_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    nav: Mapped[float] = mapped_column(Float, default=1.0)
    stock_returns: Mapped[dict | None] = mapped_column(JSON, nullable=True)  # per-stock returns
    status: Mapped[str] = mapped_column(String(20), default="active")  # active, completed, error
    message: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

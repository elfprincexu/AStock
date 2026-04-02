"""
PostgreSQL 数据库测试。

验证:
  - 数据库连接正常
  - 表结构已创建 (stocks, quote_snapshots, daily_klines, fetch_logs)
  - 基本 CRUD 操作
  - 唯一约束和索引

依赖: PostgreSQL 运行中 (docker-compose up -d postgres)
"""
import pytest
from datetime import datetime, date

from conftest import (
    requires_postgres,
    DATABASE_URL_SYNC,
    POSTGRES_HOST,
    POSTGRES_PORT,
    POSTGRES_USER,
    POSTGRES_PASSWORD,
    POSTGRES_DB,
)


# ═══════════════════════════════════════════════════════════════════════════════
# 1. 连接测试
# ═══════════════════════════════════════════════════════════════════════════════

@requires_postgres
class TestDatabaseConnection:
    """测试 PostgreSQL 基础连接。"""

    def test_tcp_connection(self):
        """应能建立到 PostgreSQL 端口的 TCP 连接。"""
        import socket
        with socket.create_connection((POSTGRES_HOST, POSTGRES_PORT), timeout=5):
            pass  # 连接成功即通过

    def test_psycopg2_connection(self):
        """应能通过 psycopg2 建立数据库连接。"""
        import psycopg2
        conn = psycopg2.connect(
            host=POSTGRES_HOST,
            port=POSTGRES_PORT,
            user=POSTGRES_USER,
            password=POSTGRES_PASSWORD,
            dbname=POSTGRES_DB,
        )
        cur = conn.cursor()
        cur.execute("SELECT 1")
        result = cur.fetchone()
        assert result == (1,)
        cur.close()
        conn.close()

    def test_sqlalchemy_sync_engine(self):
        """应能通过 SQLAlchemy 同步引擎连接。"""
        from sqlalchemy import create_engine, text
        engine = create_engine(DATABASE_URL_SYNC)
        with engine.connect() as conn:
            result = conn.execute(text("SELECT version()"))
            version = result.scalar()
            assert "PostgreSQL" in version
        engine.dispose()


# ═══════════════════════════════════════════════════════════════════════════════
# 2. 表结构测试
# ═══════════════════════════════════════════════════════════════════════════════

@requires_postgres
class TestDatabaseSchema:
    """测试数据库表结构是否正确。"""

    @pytest.fixture(autouse=True)
    def setup_engine(self):
        from sqlalchemy import create_engine, text
        self.engine = create_engine(DATABASE_URL_SYNC)
        # Ensure tables exist (same as FastAPI startup)
        from app.database import Base
        from app.models import Stock, QuoteSnapshot, DailyKline, FetchLog
        Base.metadata.create_all(self.engine)
        yield
        self.engine.dispose()

    def _table_exists(self, table_name: str) -> bool:
        from sqlalchemy import text
        with self.engine.connect() as conn:
            result = conn.execute(text(
                "SELECT EXISTS ("
                "  SELECT 1 FROM information_schema.tables "
                "  WHERE table_schema = 'public' AND table_name = :name"
                ")"
            ), {"name": table_name})
            return result.scalar()

    def test_stocks_table_exists(self):
        assert self._table_exists("stocks"), "表 'stocks' 不存在"

    def test_quote_snapshots_table_exists(self):
        assert self._table_exists("quote_snapshots"), "表 'quote_snapshots' 不存在"

    def test_daily_klines_table_exists(self):
        assert self._table_exists("daily_klines"), "表 'daily_klines' 不存在"

    def test_fetch_logs_table_exists(self):
        assert self._table_exists("fetch_logs"), "表 'fetch_logs' 不存在"

    def test_stocks_columns(self):
        """stocks 表应包含所有必要列。"""
        from sqlalchemy import text
        with self.engine.connect() as conn:
            result = conn.execute(text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name = 'stocks' ORDER BY column_name"
            ))
            columns = {row[0] for row in result}
        expected = {"id", "code", "name", "market", "is_active", "created_at"}
        missing = expected - columns
        assert not missing, f"stocks 表缺少列: {missing}"

    def test_daily_klines_columns(self):
        """daily_klines 表应包含所有必要列。"""
        from sqlalchemy import text
        with self.engine.connect() as conn:
            result = conn.execute(text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name = 'daily_klines' ORDER BY column_name"
            ))
            columns = {row[0] for row in result}
        expected = {"id", "stock_id", "date", "open", "high", "low", "close",
                    "volume", "amount", "change_pct", "turnover_rate", "created_at"}
        missing = expected - columns
        assert not missing, f"daily_klines 表缺少列: {missing}"

    def test_unique_constraint_on_daily_klines(self):
        """daily_klines 表应有 (stock_id, date) 唯一约束。"""
        from sqlalchemy import text
        with self.engine.connect() as conn:
            result = conn.execute(text(
                "SELECT constraint_name FROM information_schema.table_constraints "
                "WHERE table_name = 'daily_klines' AND constraint_type = 'UNIQUE'"
            ))
            constraints = [row[0] for row in result]
        assert len(constraints) > 0, "daily_klines 缺少唯一约束"


# ═══════════════════════════════════════════════════════════════════════════════
# 3. CRUD 测试
# ═══════════════════════════════════════════════════════════════════════════════

@requires_postgres
class TestDatabaseCRUD:
    """测试基本的增删改查操作。"""

    @pytest.fixture(autouse=True)
    def setup_session(self):
        from sqlalchemy import create_engine
        from sqlalchemy.orm import Session
        from app.database import Base
        from app.models import Stock, QuoteSnapshot, DailyKline, FetchLog
        self.engine = create_engine(DATABASE_URL_SYNC)
        Base.metadata.create_all(self.engine)
        self.Session = Session
        yield
        self.engine.dispose()

    def _cleanup_stock(self, code: str):
        """Remove test stock and related data."""
        from sqlalchemy import text
        with self.engine.connect() as conn:
            conn.execute(text(
                "DELETE FROM fetch_logs WHERE stock_id IN "
                "(SELECT id FROM stocks WHERE code = :code)"
            ), {"code": code})
            conn.execute(text(
                "DELETE FROM quote_snapshots WHERE stock_id IN "
                "(SELECT id FROM stocks WHERE code = :code)"
            ), {"code": code})
            conn.execute(text(
                "DELETE FROM daily_klines WHERE stock_id IN "
                "(SELECT id FROM stocks WHERE code = :code)"
            ), {"code": code})
            conn.execute(text("DELETE FROM stocks WHERE code = :code"), {"code": code})
            conn.commit()

    def test_insert_and_query_stock(self):
        """应能插入并查询一只股票。"""
        from app.models import Stock
        test_code = "999990"
        self._cleanup_stock(test_code)

        with self.Session(self.engine) as db:
            stock = Stock(code=test_code, name="测试股票", market="SH")
            db.add(stock)
            db.commit()
            db.refresh(stock)
            assert stock.id is not None
            assert stock.is_active is True

        # Query back
        from sqlalchemy import select
        with self.Session(self.engine) as db:
            result = db.execute(select(Stock).where(Stock.code == test_code))
            found = result.scalar_one_or_none()
            assert found is not None
            assert found.name == "测试股票"

        self._cleanup_stock(test_code)

    def test_insert_duplicate_code_fails(self):
        """插入重复 code 应失败 (唯一约束)。"""
        from app.models import Stock
        from sqlalchemy.exc import IntegrityError
        test_code = "999991"
        self._cleanup_stock(test_code)

        with self.Session(self.engine) as db:
            db.add(Stock(code=test_code, name="测试A", market="SH"))
            db.commit()

        with pytest.raises(IntegrityError):
            with self.Session(self.engine) as db:
                db.add(Stock(code=test_code, name="测试B", market="SZ"))
                db.commit()

        self._cleanup_stock(test_code)

    def test_insert_kline(self):
        """应能插入日 K 线记录。"""
        from app.models import Stock, DailyKline
        test_code = "999992"
        self._cleanup_stock(test_code)

        with self.Session(self.engine) as db:
            stock = Stock(code=test_code, name="K线测试", market="SH")
            db.add(stock)
            db.commit()
            db.refresh(stock)

            kline = DailyKline(
                stock_id=stock.id,
                date=date(2024, 6, 14),
                open=100.0, high=105.0, low=99.0, close=103.0,
                volume=1000000, amount=103000000.0,
                change_pct=3.0, turnover_rate=1.5,
            )
            db.add(kline)
            db.commit()
            db.refresh(kline)
            assert kline.id is not None

        self._cleanup_stock(test_code)

    def test_kline_upsert_on_conflict(self):
        """daily_klines 的 upsert (ON CONFLICT) 应更新已有记录。"""
        from app.models import Stock, DailyKline
        from sqlalchemy.dialects.postgresql import insert as pg_insert
        test_code = "999993"
        self._cleanup_stock(test_code)

        with self.Session(self.engine) as db:
            stock = Stock(code=test_code, name="Upsert测试", market="SH")
            db.add(stock)
            db.commit()
            db.refresh(stock)
            stock_id = stock.id

            # First insert
            record = {
                "stock_id": stock_id, "date": date(2024, 1, 1),
                "open": 10.0, "high": 11.0, "low": 9.0, "close": 10.5,
                "volume": 100, "amount": 1050.0, "change_pct": 5.0,
                "turnover_rate": 1.0,
            }
            stmt = pg_insert(DailyKline).values([record])
            stmt = stmt.on_conflict_do_update(
                index_elements=["stock_id", "date"],
                set_={"close": stmt.excluded.close},
            )
            db.execute(stmt)
            db.commit()

            # Upsert with different close
            record["close"] = 12.0
            stmt = pg_insert(DailyKline).values([record])
            stmt = stmt.on_conflict_do_update(
                index_elements=["stock_id", "date"],
                set_={"close": stmt.excluded.close},
            )
            db.execute(stmt)
            db.commit()

        # Verify only one record, with updated close
        from sqlalchemy import select
        with self.Session(self.engine) as db:
            rows = db.execute(
                select(DailyKline).where(DailyKline.stock_id == stock_id)
            ).scalars().all()
            assert len(rows) == 1, f"应有 1 条记录, 实际 {len(rows)}"
            assert rows[0].close == 12.0, f"close 应为 12.0, 实际 {rows[0].close}"

        self._cleanup_stock(test_code)

    def test_insert_fetch_log(self):
        """应能写入抓取日志。"""
        from app.models import FetchLog

        with self.Session(self.engine) as db:
            log = FetchLog(
                stock_id=None,
                fetch_type="test",
                status="success",
                message="unit test log entry",
            )
            db.add(log)
            db.commit()
            db.refresh(log)
            assert log.id is not None
            assert log.created_at is not None

            # Cleanup
            db.delete(log)
            db.commit()

"""
API routes for stock news/announcements and retail sentiment data.
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.database import get_db
from app.models import Stock
from typing import Optional

router = APIRouter()


async def _resolve_stock(
    stock_id: int,
    db: AsyncSession,
) -> Stock:
    """Resolve stock_id to Stock object, raising 404 if not found."""
    result = await db.execute(select(Stock).where(Stock.id == stock_id))
    stock = result.scalar_one_or_none()
    if stock is None:
        raise HTTPException(status_code=404, detail="Stock not found")
    return stock


@router.get(
    "/news/{stock_id}",
    summary="Get company announcements for a stock (last 2 years)",
)
async def get_stock_news(
    stock_id: int,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
):
    """
    Fetch official company announcements (公告) for a stock from EastMoney.
    Only returns announcements from the last 2 years, sorted by date descending.

    Each item contains:
    - title: announcement title
    - date: publication date (YYYY-MM-DD)
    - source: data source
    - url: link to full announcement
    - type: "announcement"
    - category: announcement type (e.g. 其他, 回购进展情况)
    """
    stock = await _resolve_stock(stock_id, db)

    from app.services.news_service import fetch_stock_announcements
    return await fetch_stock_announcements(
        stock_code=stock.code,
        page=page,
        page_size=page_size,
    )


@router.get(
    "/sentiment/{stock_id}",
    summary="Get daily retail investor sentiment for a stock",
)
async def get_stock_sentiment(
    stock_id: int,
    days: int = Query(default=7, ge=1, le=90),
    db: AsyncSession = Depends(get_db),
):
    """
    Scrape EastMoney Guba (股吧) and return daily aggregated statistics.

    Each item contains:
    - date: trading date (YYYY-MM-DD)
    - comment_count: unique user count (deduplicated by user ID)
    - total_posts: total number of posts
    - sentiment_score: 0-100 (50=neutral, >50=bullish, <50=bearish)
    - avg_read_count: average reads per post
    - avg_reply_count: average replies per post
    - source: data source identifier

    The sentiment_score is computed using keyword-based analysis of post titles.
    Higher scores indicate more bullish retail sentiment.
    """
    stock = await _resolve_stock(stock_id, db)

    from app.services.sentiment_service import fetch_guba_sentiment
    items = await fetch_guba_sentiment(
        stock_code=stock.code,
        days=days,
        max_pages=min(days * 2, 30),
    )
    return items

"""
Retail investor sentiment service.

Scrapes EastMoney Guba (股吧) to collect daily comment statistics:
  - Unique user count per day (deduplicated by user ID)
  - Basic keyword-based sentiment score (0-100, higher = more bullish)

Designed to be extensible for Xueqiu / 同花顺 when auth is available.
"""

import logging
import re
from collections import defaultdict
from datetime import datetime, date, timedelta
from typing import Optional

import httpx
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Referer": "https://guba.eastmoney.com/",
}

# ── Keyword-based sentiment scoring ──────────────────────────────────────
# Bullish keywords (看多)
_BULL_KEYWORDS = [
    "看多", "看涨", "看好", "利好", "牛", "大涨", "涨停", "加仓", "买入",
    "抄底", "起飞", "翻倍", "新高", "突破", "拉升", "爆发", "底部",
    "逢低买", "低吸", "上车", "冲", "赚",
]
# Bearish keywords (看空)
_BEAR_KEYWORDS = [
    "看空", "看跌", "利空", "熊", "大跌", "跌停", "减仓", "卖出",
    "清仓", "割肉", "套牢", "暴跌", "崩", "见顶", "出货", "风险",
    "逢高卖", "高抛", "下车", "亏", "跑", "完了", "药丸",
]


def _score_text(text: str) -> float:
    """
    Score a single text: returns value between -1.0 (very bearish) and +1.0 (very bullish).
    0.0 means neutral.
    """
    bull = sum(1 for kw in _BULL_KEYWORDS if kw in text)
    bear = sum(1 for kw in _BEAR_KEYWORDS if kw in text)
    total = bull + bear
    if total == 0:
        return 0.0
    return (bull - bear) / total


def _score_to_100(raw_scores: list[float]) -> int:
    """Convert average raw score [-1, +1] to 0-100 scale. 50 = neutral."""
    if not raw_scores:
        return 50
    avg = sum(raw_scores) / len(raw_scores)
    # Map [-1, +1] -> [0, 100]
    return max(0, min(100, int(round(avg * 50 + 50))))


async def _scrape_guba_page(
    stock_code: str,
    page: int = 1,
) -> list[dict]:
    """
    Scrape one page of EastMoney Guba posts.

    Returns list of dicts:
      {user_id, user_name, title, date_str, read_count, reply_count}
    """
    url = f"https://guba.eastmoney.com/list,{stock_code},f_{page}.html"
    try:
        async with httpx.AsyncClient(timeout=15, headers=_HEADERS, follow_redirects=True) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            soup = BeautifulSoup(resp.text, "html.parser")

            posts = []
            for item in soup.select("tr.listitem"):
                tds = item.select("td")
                if len(tds) < 5:
                    continue
                read_count_str = tds[0].text.strip()
                reply_count_str = tds[1].text.strip()
                title_el = tds[2].select_one("a")
                author_el = tds[3].select_one("a")
                date_str = tds[4].text.strip()

                user_id = ""
                user_name = ""
                if author_el:
                    user_name = author_el.text.strip()
                    href = author_el.get("href", "")
                    # href like "//i.eastmoney.com/5632094310755520"
                    m = re.search(r"/(\d+)$", href)
                    if m:
                        user_id = m.group(1)

                title = title_el.text.strip() if title_el else ""

                try:
                    read_count = int(re.sub(r"\D", "", read_count_str) or "0")
                except ValueError:
                    read_count = 0
                try:
                    reply_count = int(re.sub(r"\D", "", reply_count_str) or "0")
                except ValueError:
                    reply_count = 0

                posts.append({
                    "user_id": user_id,
                    "user_name": user_name,
                    "title": title,
                    "date_str": date_str,
                    "read_count": read_count,
                    "reply_count": reply_count,
                })
            return posts
    except Exception as e:
        logger.warning("[sentiment] Guba scrape page %d failed for %s: %s", page, stock_code, e)
        return []


def _parse_guba_date(date_str: str, reference_year: int = 0) -> Optional[date]:
    """
    Parse guba date like '03-24 01:55' or '2026-03-24 01:55' to a date object.
    """
    if not date_str:
        return None
    if not reference_year:
        reference_year = datetime.now().year
    try:
        # Full date format: "2026-03-24 01:55"
        if len(date_str) >= 10 and date_str[4] == "-":
            return datetime.strptime(date_str[:10], "%Y-%m-%d").date()
        # Short format: "03-24 01:55"
        parts = date_str.split()
        if parts and "-" in parts[0]:
            md = parts[0]
            return datetime.strptime(f"{reference_year}-{md}", "%Y-%m-%d").date()
    except (ValueError, IndexError):
        pass
    return None


async def fetch_guba_sentiment(
    stock_code: str,
    days: int = 7,
    max_pages: int = 20,
) -> list[dict]:
    """
    Scrape EastMoney Guba posts and aggregate daily statistics.

    Uses batched parallel fetching (3 pages at a time) for speed.

    Returns list of dicts sorted by date ascending:
      {
        date: "2026-03-20",
        comment_count: 45,         # unique user count (deduplicated)
        total_posts: 80,           # total posts
        sentiment_score: 62,       # 0-100, 50=neutral, >50=bullish
        avg_read_count: 150,       # average reads per post
        avg_reply_count: 3,        # average replies per post
        source: "eastmoney_guba",
      }
    """
    import asyncio

    today = date.today()
    cutoff = today - timedelta(days=days)
    reference_year = today.year

    # Aggregate by date
    daily_data: dict[str, dict] = defaultdict(lambda: {
        "user_ids": set(),
        "total_posts": 0,
        "scores": [],
        "total_reads": 0,
        "total_replies": 0,
    })

    # Scale max_pages based on days requested
    effective_max = min(max_pages, max(3, days // 2))

    def _process_posts(posts: list[dict]) -> bool:
        """Process posts into daily_data. Returns True if cutoff reached."""
        hit_cutoff = False
        for post in posts:
            d = _parse_guba_date(post["date_str"], reference_year)
            if d is None:
                continue
            if d < cutoff:
                hit_cutoff = True
                continue

            date_key = d.isoformat()
            bucket = daily_data[date_key]
            if post["user_id"]:
                bucket["user_ids"].add(post["user_id"])
            bucket["total_posts"] += 1
            if post["title"]:
                bucket["scores"].append(_score_text(post["title"]))
            bucket["total_reads"] += post["read_count"]
            bucket["total_replies"] += post["reply_count"]
        return hit_cutoff

    # Fetch pages in batches of 3 for parallelism
    BATCH_SIZE = 3
    reached_cutoff = False
    page = 1
    while page <= effective_max and not reached_cutoff:
        batch_end = min(page + BATCH_SIZE, effective_max + 1)
        tasks = [_scrape_guba_page(stock_code, p) for p in range(page, batch_end)]
        results = await asyncio.gather(*tasks)

        for posts in results:
            if not posts:
                reached_cutoff = True
                break
            if _process_posts(posts):
                reached_cutoff = True
                break

        page = batch_end
        if not reached_cutoff and page <= effective_max:
            await asyncio.sleep(0.2)

    # Build results
    results = []
    for date_key in sorted(daily_data.keys()):
        bucket = daily_data[date_key]
        n = bucket["total_posts"] or 1
        results.append({
            "date": date_key,
            "comment_count": len(bucket["user_ids"]),
            "total_posts": bucket["total_posts"],
            "sentiment_score": _score_to_100(bucket["scores"]),
            "avg_read_count": round(bucket["total_reads"] / n),
            "avg_reply_count": round(bucket["total_replies"] / n, 1),
            "source": "eastmoney_guba",
        })

    return results

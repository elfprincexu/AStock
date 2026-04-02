"""
Stock announcement fetching service.

Source: EastMoney announcements API (公告)
"""

import logging
import re
import json
from datetime import datetime, timedelta
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
}


async def fetch_eastmoney_announcements(
    stock_code: str,
    page: int = 1,
    page_size: int = 50,
) -> tuple[list[dict], int]:
    """
    Fetch official company announcements from EastMoney.

    Returns (items, total_hits) where items is list of dicts:
      {title, date, source, url, type, category}
    """
    # Only keep announcements from the last 2 years
    cutoff = (datetime.now() - timedelta(days=730)).strftime("%Y-%m-%d")

    url = "https://np-anotice-stock.eastmoney.com/api/security/ann"
    params = {
        "sr": -1,
        "page_size": page_size,
        "page_index": page,
        "ann_type": "A",
        "client_source": "web",
        "stock_list": stock_code,
        "f_node": "0",
        "s_node": "0",
        "begin_time": cutoff,
    }
    try:
        async with httpx.AsyncClient(timeout=15, headers=_HEADERS) as client:
            resp = await client.get(url, params=params)
            resp.raise_for_status()
            text = resp.text.strip()
            if text.startswith("("):
                text = text[1:-1]
            data = json.loads(text)
            data_obj = data.get("data") or {}
            items = data_obj.get("list") or []
            total_hits = data_obj.get("total_hits") or len(items)

            results = []
            for item in items:
                title = item.get("title", "")
                # Clean HTML tags from title
                title = re.sub(r"<[^>]+>", "", title)
                notice_date = item.get("notice_date", "")
                if notice_date:
                    try:
                        notice_date = notice_date[:10]  # "2026-03-14"
                    except Exception:
                        pass
                # Double-check cutoff (API might not honour begin_time perfectly)
                if notice_date and notice_date < cutoff:
                    continue
                columns = item.get("columns") or []
                ann_type = columns[0].get("column_name", "") if columns else ""
                results.append({
                    "title": title,
                    "date": notice_date,
                    "source": "东方财富",
                    "url": f"https://data.eastmoney.com/notices/detail/{stock_code}/{item.get('art_code','')}.html",
                    "type": "announcement",
                    "category": ann_type,
                })
            return results, int(total_hits)
    except Exception as e:
        logger.warning("[announcements] EastMoney failed for %s: %s", stock_code, e)
        return [], 0


async def fetch_stock_announcements(
    stock_code: str,
    page: int = 1,
    page_size: int = 50,
) -> dict:
    """
    Fetch company announcements for a stock (last 2 years), sorted by date descending.

    Returns dict:
      {total, page, page_size, items: [{title, date, source, url, type, category}]}
    """
    results, total = await fetch_eastmoney_announcements(
        stock_code, page=page, page_size=page_size,
    )
    results.sort(key=lambda x: x.get("date", ""), reverse=True)
    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "items": results,
    }

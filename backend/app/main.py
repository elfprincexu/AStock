from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import select, func

from app.database import init_db, async_session
from app.auth import hash_password
from app.models import User
from app.routers import (
    stocks, quotes,
    screener as screener_router,
    schedule as schedule_router,
    ai as ai_router,
    config as config_router,
    trade as trade_router,
    newssentiment as newssentiment_router,
    quant as quant_router,
    auth as auth_router,
)


async def _seed_default_admin():
    """Create the default admin account if no admin exists yet."""
    async with async_session() as db:
        result = await db.execute(
            select(func.count()).select_from(User).where(User.role == "admin")
        )
        if result.scalar() == 0:
            admin = User(
                username="AStock",
                hashed_password=hash_password("AStock123!"),
                display_name="Administrator",
                role="admin",
                permissions=None,  # admin has all permissions implicitly
                is_active=True,
            )
            db.add(admin)
            await db.commit()


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    await _seed_default_admin()
    yield


app = FastAPI(title="AStock API", version="1.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth_router.router, prefix="/api/auth", tags=["Authentication"])
app.include_router(stocks.router, prefix="/api/stocks", tags=["stocks"])
app.include_router(quotes.router, prefix="/api/quotes", tags=["quotes"])
app.include_router(screener_router.router, prefix="/api/screener", tags=["Screener"])
app.include_router(schedule_router.router, prefix="/api/schedule", tags=["Schedule"])
app.include_router(ai_router.router, prefix="/api/ai", tags=["AI Analysis"])
app.include_router(config_router.router, prefix="/api/config", tags=["Configuration"])
app.include_router(trade_router.router, prefix="/api/trade", tags=["Trading"])
app.include_router(newssentiment_router.router, prefix="/api/newssentiment", tags=["News & Sentiment"])
app.include_router(quant_router.router, prefix="/api/quant", tags=["Quantitative Analysis"])


@app.get("/api/health")
async def health():
    return {"status": "ok"}

"""FastAPI application entry point.

Responsibilities here are deliberately narrow:
  - Construct the FastAPI app
  - Mount auth middleware + login router
  - Include feature routers
  - Manage the auto-predict background loop via lifespan
  - Manage the daily cache-warmer background loop via lifespan
  - Serve top-level static pages (/, /health)

Everything else lives in a feature folder under app/.
"""
import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.core.auth import AuthMiddleware, login_router
from app.core.config import settings
from app.mlb.routes import router as mlb_data_router
from app.predictions.routes import router as predictions_router
from app.salaries.routes import router as salaries_router
from app.scheduler.routes import router as scheduler_router
from app.scheduler.services.auto_predict import scheduler as auto_predict_scheduler
from app.trades.routes import router as trades_router
from app.tuning.routes import router as tuning_router
from app.visitors.routes import admin_pages, router as visitors_router


async def _cache_warmer_loop() -> None:
    """Daily background task: cache yesterday's games so Backtest & Tune is fast."""
    from app.tuning.services.orchestration import warm_cache_for_yesterday
    INTERVAL = 24 * 60 * 60  # 24 hours
    await asyncio.sleep(60)   # short startup delay -- let the server settle
    while True:
        try:
            await asyncio.to_thread(warm_cache_for_yesterday)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            print(f"[cache-warmer] error: {e}")
        try:
            await asyncio.sleep(INTERVAL)
        except asyncio.CancelledError:
            raise


@asynccontextmanager
async def lifespan(app: FastAPI):
    tasks = []
    if settings.auto_predict_enabled:
        tasks.append(asyncio.create_task(auto_predict_scheduler.run_loop()))
        print("[startup] auto-predict scheduler started")
    else:
        print("[startup] auto-predict scheduler disabled via env")
    tasks.append(asyncio.create_task(_cache_warmer_loop()))
    print("[startup] daily cache-warmer started")
    try:
        yield
    finally:
        for t in tasks:
            t.cancel()
            try:
                await t
            except asyncio.CancelledError:
                pass
        print("[shutdown] background tasks stopped")


app = FastAPI(lifespan=lifespan)
app.add_middleware(AuthMiddleware)

# Top-level auth + admin static pages (no /api prefix)
app.include_router(login_router)
app.include_router(admin_pages)

# Feature API routers
app.include_router(mlb_data_router, prefix="/api")
app.include_router(predictions_router, prefix="/api")
app.include_router(tuning_router, prefix="/api")
app.include_router(scheduler_router, prefix="/api")
app.include_router(trades_router, prefix="/api")
app.include_router(salaries_router, prefix="/api")
app.include_router(visitors_router, prefix="/api")


@app.get("/")
def root():
    return FileResponse("static/index.html")


@app.get("/health")
def health():
    return {"status": "ok"}


app.mount("/static", StaticFiles(directory="static"), name="static")
